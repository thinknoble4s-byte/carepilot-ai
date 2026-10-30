"""The Golden Path demo data as shipped: Savitri Patil, Nirmala Reddy and Lakshmi V. (Voice Triage), all Tamil,
all consented."""
import pytest

from conftest import PRIYA, morning, outbox


@pytest.fixture()
def demo_seed(monkeypatch, seeded):
    monkeypatch.delenv("CAREPILOT_SEED_DIR")
    from app import db, seed

    with db.connect() as c:
        return seed.reset(c, scenario="step_a")


@pytest.mark.demo_template
def test_three_tamil_patients_with_consent(demo_seed, client):
    assert (demo_seed["patients"], demo_seed["consents"], demo_seed["agent_on"], demo_seed["open_exceptions"]) == \
        (3, 3, 3, 0)
    patients = {p["mrn"]: p for p in client.get("/patients", headers=PRIYA).json()}
    assert set(patients) == {"SYN-0011", "SYN-0013", "SYN-0142"}
    assert {p["preferred_language"] for p in patients.values()} == {"Tamil"}
    assert {p["full_name"] for p in patients.values()} == {"Savitri Patil", "Nirmala Reddy", "Lakshmi V."}


@pytest.mark.demo_template
def test_tamil_test_reminder_with_four_buttons(demo_seed, client):
    r = client.post("/patients/SYN-0013/plan/confirm", headers=PRIYA, json={
        "template_id": "BC-HRPOS-1", "therapy": "Surgery and endocrine therapy",
        "treatment_end_date": "2026-08-10", "identifiers_checked": ["name", "mrn"]})
    assert r.status_code == 201, r.text
    morning(client)
    sent = [m for m in outbox("SYN-0013") if m["status"] == "sent"]
    bt = next(m for m in sent if m["template_id"] == "test_booking_reminder" and "இரத்தப் பரிசோதனை" in m["body"])
    assert bt["language"] == "Tamil" and bt["body"].startswith("வணக்கம் Nirmala")
    assert [(b["id"], b["label"]) for b in bt["buttons"]] == [
        ("booked", "இரத்தப் பரிசோதனை முன்பதிவு செய்துவிட்டேன்"), ("help_book", "முன்பதிவு செய்ய உதவுங்கள்"),
        ("cant_come", "வர முடியாது"), ("done_outside", "வெளி ஆய்வகத்தில் செய்தேன்")]
    assert bt["buttons"][0]["label_en"] == "Blood test booked"
