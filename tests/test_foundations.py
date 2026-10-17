"""A1: transactions, append-only audit (with the demo-reset exception), demo clock."""
from datetime import date

import psycopg
import pytest

from conftest import ANAND, PRIYA, scalar


def test_changes_inside_a_transaction_are_saved(seeded):
    from app import db

    with db.connect() as c:
        c.execute("INSERT INTO app_user (user_id, name, role) VALUES ('u_test', 'Test', 'coordinator')")
    assert scalar("SELECT count(*) FROM app_user WHERE user_id = 'u_test'") == 1


def test_an_error_inside_a_transaction_leaves_nothing(seeded):
    from app import db

    with pytest.raises(RuntimeError):
        with db.connect() as c:
            c.execute("INSERT INTO app_user (user_id, name, role) VALUES ('u_test', 'Test', 'coordinator')")
            raise RuntimeError("boom")
    assert scalar("SELECT count(*) FROM app_user WHERE user_id = 'u_test'") == 0


@pytest.mark.parametrize("sql", ["UPDATE audit_ledger SET action = 'x'", "DELETE FROM audit_ledger",
                                 "TRUNCATE audit_ledger"])
def test_audit_ledger_rejects_changes(conn, sql):
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute(sql)


def test_reset_flag_allows_truncate_only(conn):
    conn.execute("SET LOCAL carepilot.demo_reset = 'on'")
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("DELETE FROM audit_ledger")


def test_reset_starts_a_valid_chain_with_a_reset_entry(conn):
    from app import audit

    first = conn.execute("SELECT id, action, prev_hash FROM audit_ledger ORDER BY id LIMIT 1").fetchone()
    assert first == {"id": 1, "action": "demo_reset", "prev_hash": "0" * 64}
    assert audit.verify(conn) == {"valid": True, "checked": 27, "firstBadId": None}


def test_extra_audit_fields_are_in_the_hash(conn):
    from app import audit

    audit.append(conn, actor_type="ai", actor_name="test", action="test.extra", mrn="SYN-0007",
                 model="m", decision_inputs={"a": 1})
    assert audit.verify(conn)["valid"]
    conn.execute("ALTER TABLE audit_ledger DISABLE TRIGGER audit_ledger_no_update")
    conn.execute("UPDATE audit_ledger SET model = 'tampered' WHERE action = 'test.extra'")
    assert not audit.verify(conn)["valid"]


def test_advance_clock(conn):
    from app import clock

    assert clock.get_today(conn) == date(2026, 11, 2)
    assert clock.advance(conn, 7, "u_priya") == date(2026, 11, 9)
    last = conn.execute("SELECT action, details FROM audit_ledger ORDER BY id DESC LIMIT 1").fetchone()
    assert last["action"] == "clock_advanced"
    assert last["details"] == {"from": "2026-11-02", "to": "2026-11-09", "days": 7}


def test_clock_endpoints(seeded, client):
    assert client.get("/clock", headers=ANAND).json() == {"today": "2026-11-02", "time": "09:00"}
    assert client.post("/clock/advance", json={"days": 7}, headers=PRIYA).json()["today"] == "2026-11-09"
    for days in (0, 61):
        assert client.post("/clock/advance", json={"days": days}, headers=PRIYA).status_code == 422
    assert client.post("/clock/advance", json={"days": 7}, headers=ANAND).status_code == 403
    assert client.get("/clock", headers=ANAND).json()["today"] == "2026-11-09"
