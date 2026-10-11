"""Audit ledger integration tests. Run against a migrated database:
TEST_DATABASE_URL=postgresql://... pytest tests/test_audit_ledger.py
Skipped when TEST_DATABASE_URL is not set."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")


@pytest.fixture()
def conn():
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(DSN, row_factory=dict_row) as c:
        yield c
        c.rollback()


def test_append_chains_to_previous_entry(conn):
    from app import audit

    first = audit.append(conn, actor_type="system", actor_name="test", action="test.first")
    second = audit.append(conn, actor_type="person", actor_name="Priya Nair", action="test.second")
    prev = conn.execute("SELECT prev_hash FROM audit_ledger WHERE id = %s", (second["id"],)).fetchone()["prev_hash"]
    assert second["id"] > first["id"]
    assert prev == first["hash"]
    assert audit.verify(conn)["valid"]


def test_ledger_rejects_update_and_delete(conn):
    import psycopg
    from app import audit

    entry = audit.append(conn, actor_type="system", actor_name="test", action="test.immutable")
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE audit_ledger SET action = 'tampered' WHERE id = %s", (entry["id"],))


def test_unknown_actor_type_rejected(conn):
    import psycopg
    from app import audit

    with pytest.raises(psycopg.errors.CheckViolation):
        audit.append(conn, actor_type="robot", actor_name="x", action="test.bad")
