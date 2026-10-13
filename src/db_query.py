import json

from .handler import get_db_connection


def lambda_handler(event, context):
    conn = get_db_connection()
    total = conn.run("SELECT COUNT(*) FROM sensor_metrics")[0][0]
    rows = conn.run(
        """
        SELECT record_id, timestamp_utc, sensor_type, location, value, z_score, is_anomaly
        FROM sensor_metrics
        ORDER BY timestamp_utc DESC
        LIMIT 20
        """
    )
    columns = [c["name"] for c in conn.columns]
    latest = [dict(zip(columns, row, strict=True)) for row in rows]
    return json.loads(json.dumps({"total_rows": total, "latest": latest}, default=str))
