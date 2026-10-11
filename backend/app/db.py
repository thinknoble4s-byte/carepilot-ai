"""Database access. One short-lived connection per request.

Per-request connections keep the idle-stop check honest (no warm Lambda
holds a connection open) and are cheap at demo traffic.
"""
import os
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row


def conninfo() -> str:
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    return psycopg.conninfo.make_conninfo(
        host=os.environ.get("DB_HOST", "localhost"),
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ.get("DB_NAME", "carepilot"),
        user=os.environ.get("DB_USER", "carepilot"),
        password=os.environ.get("DB_PASSWORD", ""),
        sslmode=os.environ.get("DB_SSLMODE", "require"),
        connect_timeout=int(os.environ.get("DB_CONNECT_TIMEOUT", "5")),
    )


@contextmanager
def connect():
    """Yield a connection inside a transaction; commit on success, roll back on error."""
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        yield conn


def instance_status() -> str | None:
    """RDS instance status (available, stopped, starting, ...) or None when not on AWS."""
    instance_id = os.environ.get("DB_INSTANCE_ID")
    if not instance_id:
        return None
    import boto3  # provided by the Lambda runtime

    rds = boto3.client("rds")
    return rds.describe_db_instances(DBInstanceIdentifier=instance_id)["DBInstances"][0]["DBInstanceStatus"]
