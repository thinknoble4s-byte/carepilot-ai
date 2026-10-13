"""A3: consent decides whether the agent is on; the latest record wins."""
import pytest

from conftest import PRIYA, scalar


def test_seeded_consent_state(conn):
    assert conn.execute("SELECT count(*) AS n FROM patient WHERE agent_on").fetchone()["n"] == 23
    off = conn.execute("SELECT mrn FROM patient WHERE NOT agent_on ORDER BY mrn").fetchall()
    assert [r["mrn"] for r in off] == ["SYN-0003", "SYN-0013"]
    tasks = conn.execute("SELECT mrn, task_type, status FROM coordinator_task").fetchall()
    assert tasks == [{"mrn": "SYN-0003", "task_type": "consent_scope_missing", "status": "open"}]
    assert conn.execute("SELECT count(*) AS n FROM consent WHERE mrn = 'SYN-0013'").fetchone()["n"] == 0


def test_has_valid_consent(conn):
    from app.consent import consent_allows, has_valid_consent

    assert has_valid_consent(conn, "SYN-0007")
    assert not has_valid_consent(conn, "SYN-0003")
    assert not has_valid_consent(conn, "SYN-0013")
    assert not consent_allows(conn, "SYN-0021", "whatsapp_caregiver")


def test_latest_consent_wins(seeded, client):
    r = client.post("/patients/SYN-0003/consent", headers=PRIYA, json={
        "consent_reference": "CONS-0003b", "scopes": ["whatsapp_patient", "automated_messaging"],
        "recorded_on": "2026-11-02"})
    assert r.status_code == 201
    assert r.json()["agent_on"] is True
    assert scalar("SELECT agent_on FROM patient WHERE mrn = 'SYN-0003'") is True
    assert scalar("SELECT count(*) FROM coordinator_task WHERE mrn = 'SYN-0003'") == 1  # history kept


@pytest.mark.parametrize("mrn, body, status", [
    ("SYN-0999", {"scopes": ["whatsapp_patient"]}, 404),
    ("SYN-0007", {"scopes": ["telepathy"]}, 422),
    ("SYN-0007", {"scopes": []}, 422),
    ("SYN-0007", {"scopes": ["whatsapp_patient"], "recorded_on": "2026-02-30"}, 422),
])
def test_bad_consent_is_refused_and_not_audited(seeded, client, mrn, body, status):
    audits = scalar("SELECT count(*) FROM audit_ledger")
    payload = {"consent_reference": "CONS-X", "recorded_on": "2026-11-02", **body}
    assert client.post(f"/patients/{mrn}/consent", headers=PRIYA, json=payload).status_code == status
    assert scalar("SELECT count(*) FROM audit_ledger") == audits


def test_each_success_writes_one_audit_row(seeded, client):
    audits = scalar("SELECT count(*) FROM audit_ledger")
    client.post("/patients/SYN-0013/consent", headers=PRIYA, json={
        "consent_reference": "CONS-0013", "scopes": ["whatsapp_patient"], "recorded_on": "2026-11-02"})
    assert scalar("SELECT count(*) FROM audit_ledger") == audits + 1
    assert scalar("SELECT action FROM audit_ledger ORDER BY id DESC LIMIT 1") == "consent_recorded"
