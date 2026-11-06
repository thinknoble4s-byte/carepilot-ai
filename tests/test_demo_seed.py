"""The Golden Path demo data as shipped: Savitri Patil (Hindi), Nirmala Reddy (Telugu) and Lakshmi V. (Tamil, Voice
Triage), all consented."""
import pytest

from conftest import PRIYA, morning, outbox


@pytest.fixture()
def demo_seed(monkeypatch, seeded):
    monkeypatch.delenv("CAREPILOT_SEED_DIR")
    from app import db, seed

    with db.connect() as c:
        return seed.reset(c, scenario="step_a")


@pytest.mark.demo_template
def test_three_patients_in_their_languages_with_consent(demo_seed, client):
    assert (demo_seed["patients"], demo_seed["consents"], demo_seed["agent_on"], demo_seed["open_exceptions"]) == \
        (3, 3, 3, 0)
    patients = {p["mrn"]: p for p in client.get("/patients", headers=PRIYA).json()}
    assert set(patients) == {"SYN-0011", "SYN-0013", "SYN-0142"}
    assert {m: p["preferred_language"] for m, p in patients.items()} == \
        {"SYN-0011": "Hindi", "SYN-0013": "Telugu", "SYN-0142": "Tamil"}
    assert {p["full_name"] for p in patients.values()} == {"Savitri Patil", "Nirmala Reddy", "Lakshmi V."}


@pytest.mark.demo_template
def test_telugu_test_reminder_with_four_buttons(demo_seed, client):
    r = client.post("/patients/SYN-0013/plan/confirm", headers=PRIYA, json={
        "template_id": "BC-HRPOS-1", "therapy": "Surgery and endocrine therapy",
        "treatment_end_date": "2026-08-10", "identifiers_checked": ["name", "mrn"]})
    assert r.status_code == 201, r.text
    morning(client)
    sent = [m for m in outbox("SYN-0013") if m["status"] == "sent"]
    bt = next(m for m in sent if m["template_id"] == "test_booking_reminder" and "రక్త పరీక్ష" in m["body"])
    assert bt["language"] == "Telugu" and bt["body"].startswith("నమస్తే Nirmala గారు")
    assert [(b["id"], b["label"]) for b in bt["buttons"]] == [
        ("booked", "రక్త పరీక్ష బుక్ చేశాను"), ("help_book", "బుక్ చేయడానికి సహాయం చేయండి"),
        ("cant_come", "రాలేను"), ("booked_elsewhere", "వేరే చోట బుక్ చేశాను")]
    assert bt["buttons"][0]["label_en"] == "Blood test booked"
