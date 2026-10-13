"""Shared fixtures.

Tests marked with the `seeded` fixture need TEST_DATABASE_URL (a migrated
PostgreSQL 16 database) and start from a fresh demo reset each time. They
wipe that database's demo data, so never point it at a database you care about.
"""
import csv
import io
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

DSN = os.environ.get("TEST_DATABASE_URL")


def users_header(user_id: str) -> dict:
    return {"X-User": user_id}


PRIYA = users_header("u_priya")
MEERA = users_header("u_meera")
ANAND = users_header("u_anand")
SURESH = users_header("u_suresh")


@pytest.fixture()
def offline(monkeypatch):
    """Point the API at a closed port so the database is 'unreachable' quickly."""
    for key, value in {"DB_HOST": "127.0.0.1", "DB_PORT": "1", "DB_SSLMODE": "disable",
                       "DB_CONNECT_TIMEOUT": "1"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DB_INSTANCE_ID", raising=False)


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


@pytest.fixture()
def seeded(monkeypatch):
    if not DSN:
        pytest.skip("TEST_DATABASE_URL not set")
    monkeypatch.setenv("DATABASE_URL", DSN)
    from app import db, seed

    with db.connect() as c:
        result = seed.reset(c)
    return result


@pytest.fixture()
def conn(seeded):
    """A connection on the freshly seeded database; rolled back at the end."""
    from app import db

    with db.connect() as c:
        yield c
        c.rollback()


def scalar(sql: str, *params):
    from app import db

    with db.connect() as c:
        row = c.execute(sql, params).fetchone()
    return next(iter(row.values()))


def plan_inputs() -> dict[str, dict]:
    from app.reference import read_seed

    return {r["mrn"]: r for r in csv.DictReader(io.StringIO(read_seed("plan_inputs.csv")))}


def plan_body(mrn: str, **overrides) -> dict:
    row = plan_inputs()[mrn]
    body = {
        "template_id": row["template_id"],
        "therapy": row["therapy"],
        "treatment_end_date": row["treatment_end_date"],
        "identifiers_checked": row["identifiers_checked"].split(";"),
    }
    return {**body, **overrides}


def seed_file(name: str) -> bytes:
    from app.reference import read_seed

    return read_seed(name).encode()
