import os
import ssl

import pg8000.native

from .logger import get_logger

logger = get_logger("db-init")


def lambda_handler(event, context):
    db_host = os.environ["DB_HOST"]
    db_name = os.environ.get("DB_NAME", "postgres")
    db_user = os.environ.get("DB_USER", "postgres")
    db_password = os.environ["DB_PASSWORD"]
    db_port = int(os.environ.get("DB_PORT", 5432))

    base_root = os.environ.get("LAMBDA_TASK_ROOT", ".")
    ca_path = os.path.join(base_root, "certs", "global-bundle.pem")
    ssl_context = ssl.create_default_context(cafile=ca_path)

    logger.info("Connecting to PostgreSQL as master user...")
    conn = pg8000.native.Connection(
        user=db_user,
        host=db_host,
        port=db_port,
        database=db_name,
        password=db_password,
        ssl_context=ssl_context,
        timeout=15,
    )

    statements = [
        """
        CREATE TABLE IF NOT EXISTS sensor_metrics (
            record_id VARCHAR(64) PRIMARY KEY,
            timestamp_utc TIMESTAMP WITH TIME ZONE NOT NULL,
            sensor_type VARCHAR(32) NOT NULL,
            location VARCHAR(64),
            value DOUBLE PRECISION NOT NULL,
            z_score DOUBLE PRECISION NOT NULL,
            is_anomaly BOOLEAN NOT NULL
        );
        """,
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'lambda_user') THEN
                CREATE USER lambda_user WITH LOGIN;
            END IF;
        END $$;
        """,
        "GRANT rds_iam TO lambda_user;",
        "GRANT SELECT, INSERT ON sensor_metrics TO lambda_user;",
    ]

    for stmt in statements:
        conn.run(stmt.strip())

    conn.close()
    logger.info("Database schema initialized successfully.")
    return {"status": "SUCCESS"}
