import io
import json
import logging
import os
import urllib.parse
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

os.environ["AWS_DEFAULT_REGION"] = "eu-north-1"
os.environ["AWS_REGION"] = "eu-north-1"
os.environ["DB_HOST"] = "mock-db.local"
os.environ["DB_NAME"] = "testdb"
os.environ["DB_USER"] = "lambda_user"

from src.handler import lambda_handler, logger  # noqa: E402
from src.logger import JsonFormatter  # noqa: E402

CSV_HEADER = "record_id,timestamp_str,sensor_type,raw_value,location\n"


@pytest.fixture
def json_logs():
    """Capture les logs du handler et les renvoie sous forme de dictionnaires JSON."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)

    def read():
        return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]

    yield read
    logger.removeHandler(handler)


@pytest.fixture
def mock_db():
    with patch("src.handler.get_db_connection") as mock_conn_fn:
        mock_conn = MagicMock()
        mock_conn.row_count = 1
        mock_conn_fn.return_value = mock_conn
        yield mock_conn


@pytest.fixture
def aws_env():
    with mock_aws():
        s3 = boto3.client("s3", region_name="eu-north-1")
        s3.create_bucket(
            Bucket="test-bucket",
            CreateBucketConfiguration={"LocationConstraint": "eu-north-1"},
        )
        yield s3


def build_sqs_event(bucket, key, message_id="msg-123"):
    s3_notification = {
        "Records": [
            {
                "s3": {
                    "bucket": {"name": bucket},
                    "object": {"key": urllib.parse.quote_plus(key)},
                }
            }
        ]
    }
    return {
        "Records": [
            {
                "messageId": message_id,
                "body": json.dumps(s3_notification),
            }
        ]
    }


def upload_csv(s3, key, body):
    s3.put_object(Bucket="test-bucket", Key=key, Body=body.encode("utf-8"))


def sql_statements(conn):
    """Liste des requêtes SQL envoyées à la connexion mockée, dans l'ordre."""
    return [call.args[0].strip() for call in conn.run.call_args_list if call.args]


def test_nominal_flow(aws_env, mock_db, json_logs):
    upload_csv(
        aws_env,
        "data/nominal.csv",
        CSV_HEADER
        + "r1,2026-10-10T10:00:00Z,temperature,22.0,zone-1\n"
        + "r2,2026-10-10T10:00:05Z,pressure,1015.0,zone-1\n",
    )

    result = lambda_handler(build_sqs_event("test-bucket", "data/nominal.csv"), None)

    assert result == {"batchItemFailures": []}

    sql = sql_statements(mock_db)
    assert sql[0] == "START TRANSACTION;"
    assert sql[-1] == "COMMIT;"
    assert len([s for s in sql if s.startswith("INSERT INTO sensor_metrics")]) == 2

    processed = [log for log in json_logs() if log["message"] == "FILE_PROCESSED"]
    assert len(processed) == 1
    assert processed[0]["rows_read"] == 2
    assert processed[0]["rows_inserted"] == 2


def test_anomaly_detection(aws_env, mock_db, json_logs):
    upload_csv(
        aws_env,
        "data/anomaly.csv",
        CSV_HEADER + "r-anom,2026-10-10T10:00:00Z,temperature,45.0,furnace-1\n",
    )

    result = lambda_handler(build_sqs_event("test-bucket", "data/anomaly.csv"), None)

    assert result == {"batchItemFailures": []}

    anomaly_logs = [log for log in json_logs() if log["message"] == "ANOMALY_DETECTED"]
    assert len(anomaly_logs) == 1
    assert anomaly_logs[0]["severity"] == "CRITICAL"
    assert anomaly_logs[0]["sensor"] == "temperature"
    assert anomaly_logs[0]["record_id"] == "r-anom"
    assert anomaly_logs[0]["z_score"] == 11.75


def test_duplicate_row_does_not_raise_anomaly_alert(aws_env, mock_db, json_logs):
    # row_count == 0 : la ligne existait déjà, ON CONFLICT DO NOTHING l'a ignorée
    mock_db.row_count = 0
    upload_csv(
        aws_env,
        "data/duplicate.csv",
        CSV_HEADER + "r-anom,2026-10-10T10:00:00Z,temperature,45.0,furnace-1\n",
    )

    result = lambda_handler(build_sqs_event("test-bucket", "data/duplicate.csv"), None)

    assert result == {"batchItemFailures": []}

    logs = json_logs()
    assert not [log for log in logs if log["message"] == "ANOMALY_DETECTED"]
    processed = [log for log in logs if log["message"] == "FILE_PROCESSED"]
    assert processed[0]["rows_inserted"] == 0


def test_sql_query_contains_on_conflict(aws_env, mock_db):
    upload_csv(
        aws_env,
        "data/idempotent.csv",
        CSV_HEADER + "r1,2026-10-10T10:00:00Z,temperature,20.0,zone-1\n",
    )

    lambda_handler(build_sqs_event("test-bucket", "data/idempotent.csv"), None)

    insert_sql = next(s for s in sql_statements(mock_db) if s.startswith("INSERT INTO"))
    assert "ON CONFLICT (record_id) DO NOTHING" in insert_sql


def test_corrupted_header_returns_failure(aws_env, mock_db):
    upload_csv(aws_env, "data/corrupted.csv", "bad_col_1,bad_col_2\nval1,val2\n")

    event = build_sqs_event("test-bucket", "data/corrupted.csv", message_id="failed-msg-99")
    result = lambda_handler(event, None)

    assert result == {"batchItemFailures": [{"itemIdentifier": "failed-msg-99"}]}
    # L'en-tête est rejeté avant toute connexion à la base
    mock_db.run.assert_not_called()


def test_error_mid_file_rolls_back_and_raises_no_alert(aws_env, mock_db, json_logs):
    # La 1re ligne est une anomalie valide, la 2e a un capteur inconnu : tout est annulé
    upload_csv(
        aws_env,
        "data/partial.csv",
        CSV_HEADER
        + "r1,2026-10-10T10:00:00Z,temperature,45.0,furnace-1\n"
        + "r2,2026-10-10T10:00:05Z,humidity,50.0,furnace-1\n",
    )

    event = build_sqs_event("test-bucket", "data/partial.csv", message_id="msg-partial")
    result = lambda_handler(event, None)

    assert result == {"batchItemFailures": [{"itemIdentifier": "msg-partial"}]}

    sql = sql_statements(mock_db)
    assert "ROLLBACK;" in sql
    assert "COMMIT;" not in sql

    logs = json_logs()
    # Aucune alerte d'anomalie pour des lignes qui n'ont jamais été validées
    assert not [log for log in logs if log["message"] == "ANOMALY_DETECTED"]
    errors = [log for log in logs if log["message"] == "BATCH_ITEM_FAILURE"]
    assert errors[0]["severity"] == "ERROR"
    assert "exc_info" in errors[0]


def test_stale_connection_triggers_reconnect(aws_env):
    stale = MagicMock()
    stale.run.side_effect = OSError("connection reset")
    fresh = MagicMock()
    fresh.row_count = 1

    upload_csv(
        aws_env,
        "data/reconnect.csv",
        CSV_HEADER + "r1,2026-10-10T10:00:00Z,temperature,20.0,zone-1\n",
    )

    with patch("src.handler.get_db_connection", side_effect=[stale, fresh]) as get_conn:
        result = lambda_handler(build_sqs_event("test-bucket", "data/reconnect.csv"), None)

    assert result == {"batchItemFailures": []}
    assert get_conn.call_args_list[1].kwargs == {"force_refresh": True}
    fresh.run.assert_any_call("COMMIT;")


def test_ignore_s3_test_event(mock_db):
    event = {
        "Records": [
            {
                "messageId": "test-init-msg",
                "body": json.dumps({"Event": "s3:TestEvent"}),
            }
        ]
    }

    result = lambda_handler(event, None)

    assert result == {"batchItemFailures": []}
    mock_db.run.assert_not_called()


def test_url_encoded_key_handling(aws_env, mock_db):
    special_key = "folder/sensor=temperature file.csv"
    upload_csv(
        aws_env,
        special_key,
        CSV_HEADER + "r3,2026-10-10T10:00:00Z,vibration,0.05,motor-1\n",
    )

    result = lambda_handler(build_sqs_event("test-bucket", special_key), None)

    assert result == {"batchItemFailures": []}
