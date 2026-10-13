import os
from unittest.mock import MagicMock, patch

os.environ["AWS_DEFAULT_REGION"] = "eu-north-1"
os.environ["AWS_REGION"] = "eu-north-1"
os.environ["DB_HOST"] = "mock-db.local"
os.environ["DB_NAME"] = "testdb"
os.environ["DB_USER"] = "postgres"
os.environ["DB_PASSWORD"] = "secret123"

from src.db_init import lambda_handler  # noqa: E402


def test_db_init_success():
    with patch("src.db_init.pg8000.native.Connection") as mock_conn_cls:
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn

        result = lambda_handler({}, None)

        assert result == {"status": "SUCCESS"}
        # Doit exécuter 4 requêtes (table, rôle, grant rds_iam, grants sensor_metrics)
        assert mock_conn.run.call_count == 4
        mock_conn.close.assert_called_once()
