"""The Golden Path demo data as shipped: seven patients, all Hindi speakers (user, 22 Nov), all consented; and
Hindi in the other data sets whose Hindi is cleared for the demo."""
import pytest

from conftest import PRIYA, morning, outbox


@pytest.fixture()
def demo_seed(monkeypatch, seeded):
    monkeypatch.delenv("CAREPILOT_SEED_DIR")
    from app import db, seed

    with db.connect() as c:
        return seed.reset(c, scenario="step_a")


@pytest.mark.demo_template
def test_seven_patients_all_hindi_with_consent(demo_seed, client):
    assert (demo_seed["patients"], demo_seed["consents"], demo_seed["agent_on"], demo_seed["open_exceptions"]) == \
        (7, 7, 7, 0)
    patients = {p["mrn"]: p for p in client.get("/patients", headers=PRIYA).json()}
    assert set(patients) == {"SYN-0011", "SYN-0013", "SYN-0142", "SYN-0202", "SYN-0204", "SYN-0206", "SYN-0209"}
    assert {p["preferred_language"] for p in patients.values()} == {"Hindi"}
    assert {p["full_name"] for p in patients.values()} == {"Savitri Patil", "Nirmala Reddy", "Lakshmi V.", "Radha Sharma",
                                                          "Anjana Gupta", "Kamla Joshi", "Sushila Kulkarni"}


@pytest.mark.demo_template
def test_hindi_test_reminder_with_four_buttons_for_nirmala(demo_seed, client):
    r = client.post("/patients/SYN-0013/plan/confirm", headers=PRIYA, json={
        "template_id": "BC-HRPOS-1", "therapy": "Surgery and endocrine therapy",
        "treatment_end_date": "2026-08-10", "identifiers_checked": ["name", "mrn"]})
    assert r.status_code == 201, r.text
    morning(client)
    sent = [m for m in outbox("SYN-0013") if m["status"] == "sent"]
    bt = next(m for m in sent if m["template_id"] == "test_booking_reminder" and "खून की जाँच" in m["body"])
    assert bt["language"] == "Hindi" and bt["body"].startswith("नमस्ते Nirmala जी")
    assert [(b["id"], b["label"]) for b in bt["buttons"]] == [
        ("booked", "खून की जाँच बुक कर लिया"), ("help_book", "बुक करने में मदद करें"),
        ("cant_come", "नहीं आ पाऊँगी"), ("booked_elsewhere", "कहीं और बुक कर लिया")]
    assert bt["buttons"][0]["label_en"] == "Blood test booked"


@pytest.mark.demo_template
@pytest.mark.parametrize("name", ["uc1v", "vt"])
def test_every_data_set_with_cleared_hindi_can_send_everything_in_hindi(seeded, monkeypatch, name):
    import re
    from datetime import date

    monkeypatch.delenv("CAREPILOT_SEED_DIR")
    from app import db, messaging, seed

    with db.connect() as c:
        seed.reset(c, scenario=name)
        assert {r["preferred_language"] for r in c.execute("SELECT preferred_language FROM patient").fetchall()} == {"Hindi"}
        rows = [r["body"] for r in c.execute(
            "SELECT body FROM config_versions WHERE kind = 'message_template' AND status = 'active'").fetchall()]
        english = {t["template_id"] for t in rows if t["language"] == "English"}
        hindi = {t["template_id"]: t for t in rows if t["language"] == "Hindi"}
        assert english and english <= set(hindi), english - set(hindi)
        patient = {"full_name": "Meena Rao", "mrn": "X"}
        card = {"item_code": "DX3", "name": "DEXA scan (3rd month)", "card_type": "test", "due_date": date(2026, 12, 2)}
        for tid, t in hindi.items():
            extra = {k: "x" for k in re.findall(r"\{([^{}]+)\}", t["text"])
                     if k not in ("patient_name", "first_name", "item", "item_name", "due", "due_date")}
            body = messaging.render(t, patient, card, extra)
            assert re.search("[\u0900-\u097f]", body) and not re.search("[\u0b80-\u0bff\u0c00-\u0c7f]", body), tid
            for b in messaging.render_buttons(c, t, card):
                if b["id"].startswith("slot"):
                    continue
                assert re.search("[\u0900-\u097f]", b["label"]) and b["label_en"], (tid, b)
        settings = c.execute("SELECT body FROM config_versions WHERE kind = 'scenario_settings' AND status = 'active'"
                             ).fetchone()["body"]
        clips = settings.get("voice_clips") or []
        assert {x["language"] for x in clips} <= {"Hindi"} and (clips or name == "uc1v")


@pytest.mark.demo_template
@pytest.mark.parametrize("name", ["uc2a", "uc2b", "uc2c", "uc2t"])
def test_unapproved_hindi_is_still_never_sent(seeded, name):
    from app import db, policy, seed

    with db.connect() as c:
        seed.reset(c, scenario=name)
        for tid in ("handover_ack", "instruction_booking"):
            assert policy.template(c, tid, "Hindi") is None, tid
        assert policy.template(c, "handover_ack", "English")
        assert {r["preferred_language"] for r in c.execute("SELECT preferred_language FROM patient").fetchall()} == {"English"}
