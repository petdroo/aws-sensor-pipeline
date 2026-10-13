import codecs
import csv
import json
import os
import ssl
import time
import urllib.parse
from datetime import UTC, datetime

import boto3
import pg8000.native

from .logger import get_logger

logger = get_logger("sensor-handler")

REFERENCE_STATS = {
    "temperature": {"mean": 21.5, "std": 2.0},
    "pressure": {"mean": 1013.25, "std": 10.0},
    "vibration": {"mean": 0.05, "std": 0.01},
}

REQUIRED_COLUMNS = {"record_id", "timestamp_str", "sensor_type", "raw_value", "location"}

s3_client = None
rds_client = None
db_conn = None
db_conn_created_at = 0.0


def get_s3_client():
    global s3_client
    if s3_client is None:
        s3_client = boto3.client("s3")
    return s3_client


def get_rds_client():
    global rds_client
    if rds_client is None:
        region = os.environ.get("AWS_REGION", "eu-north-1")
        rds_client = boto3.client("rds", region_name=region)
    return rds_client


def get_db_connection(force_refresh: bool = False):
    global db_conn, db_conn_created_at
    now = time.time()

    if not force_refresh and db_conn is not None and (now - db_conn_created_at) < 840:
        return db_conn

    if db_conn is not None:
        try:
            db_conn.close()
        except Exception:
            pass
        db_conn = None

    db_host = os.environ["DB_HOST"]
    db_name = os.environ.get("DB_NAME", "postgres")
    db_user = os.environ.get("DB_USER", "lambda_user")
    db_port = int(os.environ.get("DB_PORT", 5432))
    region = os.environ.get("AWS_REGION", "eu-north-1")

    rds = get_rds_client()
    auth_token = rds.generate_db_auth_token(
        DBHostname=db_host,
        Port=db_port,
        DBUsername=db_user,
        Region=region,
    )

    base_root = os.environ.get("LAMBDA_TASK_ROOT", ".")
    ca_path = os.path.join(base_root, "certs", "global-bundle.pem")
    ssl_context = ssl.create_default_context(cafile=ca_path)

    db_conn = pg8000.native.Connection(
        user=db_user,
        host=db_host,
        port=db_port,
        database=db_name,
        password=auth_token,
        ssl_context=ssl_context,
        timeout=10,
    )
    db_conn_created_at = now
    return db_conn


def lambda_handler(event, context):
    batch_failures = []

    for record in event.get("Records", []):
        message_id = record.get("messageId")
        try:
            body = json.loads(record.get("body", "{}"))

            if "Event" in body and body["Event"] == "s3:TestEvent":
                logger.info("Ignored s3:TestEvent message")
                continue

            if "Records" not in body:
                logger.warning(
                    "Unrecognized message format, skipping",
                    extra={"extra_data": {"body": body}},
                )
                continue

            for s3_record in body["Records"]:
                bucket_name = s3_record["s3"]["bucket"]["name"]
                raw_key = s3_record["s3"]["object"]["key"]
                object_key = urllib.parse.unquote_plus(raw_key)

                process_s3_file(bucket_name, object_key)

        except Exception as exc:
            logger.error(
                "BATCH_ITEM_FAILURE",
                extra={
                    "extra_data": {
                        "severity": "ERROR",
                        "message_id": message_id,
                        "error": str(exc),
                    }
                },
                exc_info=True,
            )
            if message_id:
                batch_failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": batch_failures}


def process_s3_file(bucket: str, key: str):
    global db_conn

    s3 = get_s3_client()
    response = s3.get_object(Bucket=bucket, Key=key)

    lines = codecs.iterdecode(response["Body"].iter_lines(), "utf-8")
    reader = csv.DictReader(lines)

    if not reader.fieldnames or not REQUIRED_COLUMNS.issubset(set(reader.fieldnames)):
        raise ValueError(f"Invalid CSV structure. Expected headers: {REQUIRED_COLUMNS}")

    # 1. Gestion d'une connexion morte en cache (stale socket)
    try:
        conn = get_db_connection()
        conn.run("START TRANSACTION;")
    except Exception:
        conn = get_db_connection(force_refresh=True)
        conn.run("START TRANSACTION;")

    anomalies_to_report = []
    rows_read = 0
    rows_inserted = 0

    insert_query = """
        INSERT INTO sensor_metrics (
            record_id, timestamp_utc, sensor_type, location, value, z_score, is_anomaly
        )
        VALUES (:rid, :ts, :stype, :loc, :val, :z, :anomaly)
        ON CONFLICT (record_id) DO NOTHING;
    """

    try:
        for row in reader:
            rows_read += 1
            record_id = row["record_id"].strip()
            sensor = row["sensor_type"].strip()
            raw_val_str = row["raw_value"].strip()
            location = row["location"].strip()
            ts_raw = row["timestamp_str"].strip()

            if sensor not in REFERENCE_STATS:
                raise ValueError(f"Unknown sensor type: {sensor}")

            val = float(raw_val_str)
            stats = REFERENCE_STATS[sensor]
            z = (val - stats["mean"]) / stats["std"]
            is_anomaly = abs(z) >= 3.0

            dt = datetime.fromisoformat(ts_raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            else:
                dt = dt.astimezone(UTC)

            conn.run(
                insert_query,
                rid=record_id,
                ts=dt,
                stype=sensor,
                loc=location,
                val=val,
                z=z,
                anomaly=is_anomaly,
            )
            inserted = conn.row_count
            rows_inserted += inserted

            # Une ligne ignorée par ON CONFLICT (doublon) ne redéclenche pas d'alerte
            if is_anomaly and inserted == 1:
                anomalies_to_report.append({"sensor": sensor, "z_score": z, "record_id": record_id})

        conn.run("COMMIT;")
    except Exception:
        # 2. Invalidation propre du cache si la socket réseau a sauté
        try:
            conn.run("ROLLBACK;")
        except Exception:
            db_conn = None
        raise

    for anom in anomalies_to_report:
        logger.critical(
            "ANOMALY_DETECTED",
            extra={
                "extra_data": {
                    "severity": "CRITICAL",
                    "sensor": anom["sensor"],
                    "z_score": anom["z_score"],
                    "record_id": anom["record_id"],
                }
            },
        )

    logger.info(
        "FILE_PROCESSED",
        extra={
            "extra_data": {
                "bucket": bucket,
                "key": key,
                "rows_read": rows_read,
                "rows_inserted": rows_inserted,
            }
        },
    )
