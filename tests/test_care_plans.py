"""The three care plans (T1 hormone receptor positive, T2 hormone receptor negative, T3 HER2 positive), the two
test roads and the doctor-only verified status. These run on the real demo data sets (no fixed copies)."""
from datetime import date

import pytest

from conftest import ANAND, PRIYA, morning, outbox, scalar

pytestmark = pytest.mark.demo_template

ANITA, SEKHAR = {"X-User": "u_anita"}, {"X-User": "u_sekhar"}

PLAN_ITEMS = {
    "BC-HRPOS-1": {3: ["BT", "MG", "DX"], 6: ["BT", "MG", "DX", "US"], 9: ["BT", "MG", "DX"],
                   12: ["BT", "MG", "DX", "US", "PET"]},
    "BC-HRNEG-1": {3: ["BT", "MG"], 6: ["BT", "MG", "US"], 9: ["BT", "MG"], 12: ["BT", "MG", "US", "PET"]},
    "BC-HER2-1": {3: ["BT", "MG"], 6: ["BT", "MG", "EC", "US"], 9: ["BT", "MG"], 12: ["BT", "MG", "EC", "US", "PET"]},
}


def test_three_plans_tests_per_visit():
    from app.reference import plan_templates

    plans = plan_templates()
    assert [t["template_id"] for t in plans] == list(PLAN_ITEMS)
    assert [t["short_name"] for t in plans] == ["T1 — Hormone receptor positive", "T2 — Hormone receptor negative",
                                                "T3 — HER2 positive"]
    for t in plans:
        for month, tests in PLAN_ITEMS[t["template_id"]].items():
            items = [i for i in t["items"] if i["months"] == month]
            assert [i["item_code"] for i in items] == [f"{k}{month}" for k in tests] + [f"V{month}"]
            assert items[-1]["type"] == "visit" and items[-1]["days"] == 0
            for i in items[:-1]:
                assert (i["type"], i["days"]) == ("test", -7)
                assert i["specimen"] is (i["item_code"].startswith("BT"))  # only the blood test is specimen-based


def test_due_dates_from_treatment_end():
    """Visits at 3, 6, 9 and 12 months; tests 7 days before; weekends move to Monday."""
    from app.plan import generate_cards
    from app.reference import plan_templates

    cards = {c["item_code"]: c for c in generate_cards(plan_templates()[2], date(2026, 8, 16))}
    assert {k: cards[k]["due_date"] for k in ("V3", "BT3", "MG3", "V6", "EC6", "V9", "V12", "PET12")} == {
        "V3": "2026-11-16", "BT3": "2026-11-09", "MG3": "2026-11-09", "V6": "2027-02-16", "EC6": "2027-02-09",
        "V9": "2027-05-17", "V12": "2027-08-16", "PET12": "2027-08-09"}
    assert (cards["BT3"]["specimen"], cards["MG3"]["specimen"], cards["V3"]["specimen"]) == (True, False, None)
    assert (cards["BT3"]["test_code"], cards["EC6"]["test_code"]) == ("CBC", "ECHO-2D")


@pytest.mark.parametrize("scenario, owner", [("step_a", "Priya Nair"), ("uc1", "Anita"), ("uc2a", "Anita"),
                                             ("uc2b", "Anita"), ("uc2c", "Anita"), ("uc2t", "Anita")])
def test_every_data_set_offers_the_three_plans_and_seeds_none(monkeypatch, client, scenario, owner):
    from conftest import DSN

    if not DSN:
        pytest.skip("TEST_DATABASE_URL not set")
    monkeypatch.setenv("DATABASE_URL", DSN)
    from app import db, seed

    with db.connect() as c:
        result = seed.reset(c, scenario=scenario)
    assert (result["care_plans"], result["order_cards"]) == (0, 0)
    who = PRIYA if scenario == "step_a" else ANITA
    bodies = [t["body"] for t in client.get("/care-plan", headers=who).json()["plan_templates"]]
    assert [b["template_id"] for b in bodies] == list(PLAN_ITEMS)
    assert {b["departments"]["Oncology OPD"]["owner"] for b in bodies} == {owner}


@pytest.fixture()
def her2(seeded, client):
    """Golden Path, SYN-0007 on T3 (HER2 positive): treatment ended Sun 9 Aug 2026, so the 3-month tests are due
    Mon 2 Nov (today) and the visit Mon 9 Nov."""
    r = client.post("/patients/SYN-0007/plan/confirm", headers=PRIYA, json={
        "template_id": "BC-HER2-1", "therapy": "Surgery, chemotherapy and trastuzumab",
        "treatment_end_date": "2026-08-09", "identifiers_checked": ["name", "mrn"]})
    assert r.status_code == 201, r.text
    return {c["item_code"]: c for c in r.json()["cards"]}


def card(client, code):
    return next(c for c in client.get("/patients/SYN-0007/cards", headers=PRIYA).json() if c["item_code"] == code)


def appointment(client, code, day, attended=False):
    r = client.post("/test-cycle/appointment-list", headers=PRIYA,
                    json={"rows": [{"mrn": "SYN-0007", "test_code": code, "date": day, "attended": attended}]})
    assert r.status_code == 200, r.text
    return r.json()


def test_plan_confirmed_with_17_cards(her2):
    assert len(her2) == 17
    assert (her2["V3"]["due_date"], her2["BT3"]["due_date"]) == ("2026-11-09", "2026-11-02")


def test_specimen_test_due_booked_completed_report_received_verified_by_the_system(her2, client):
    """Use Case 2T v1.4: a hospital lab report is linked and checked by MRN and test code: Report received, then
    Report verified by the system. The doctor's Verify is only for a non-specimen test."""
    bt3 = her2["BT3"]["card_id"]
    assert appointment(client, "CBC", "2026-11-04")["moved"] == [{"card_id": bt3, "mrn": "SYN-0007", "to": "Booked"}]
    assert appointment(client, "CBC", "2026-11-04", attended=True)["moved"][0]["to"] == "Completed"
    assert client.post(f"/cards/{bt3}/verify", headers=ANAND).status_code == 409  # reports are not the doctor's Verify
    assert client.get("/doctor/verifications", headers=ANAND).json() == []
    r = client.post("/simulated-lab/report", headers=PRIYA, json={"mrn": "SYN-0007", "test_code": "CBC"})
    assert r.status_code == 201, r.text
    c = card(client, "BT3")
    assert (c["state"], c["verified_by"], c["report"]["file_name"]) == ("Report verified", "CarePilot", r.json()["file_name"])
    assert scalar("""SELECT details->>'matched_identifiers' FROM audit_ledger WHERE action = 'card_state_changed'
                     AND details->>'to' = 'Report verified'""") == "2"


def test_non_specimen_test_due_booked_completed_verified_by_doctor(her2, client):
    mg3 = her2["MG3"]["card_id"]
    appointment(client, "MAMMO", "2026-11-03")
    assert appointment(client, "MAMMO", "2026-11-03", attended=True)["moved"][0]["to"] == "Completed"
    # Imaging has no lab report: Report received is never a step on this road.
    r = client.post("/simulated-lab/report", headers=PRIYA, json={"mrn": "SYN-0007", "test_code": "MAMMO"})
    assert r.status_code == 422 and "not specimen-based" in r.json()["detail"]
    assert client.post(f"/cards/{mg3}/transition", headers=PRIYA,
                       json={"to_state": "Verified"}).status_code == 409
    assert [v["item_code"] for v in client.get("/doctor/verifications", headers=ANAND).json()] == ["MG3"]
    r = client.post(f"/cards/{mg3}/verify", headers=ANAND)
    assert r.status_code == 200, r.text
    assert (r.json()["state"], r.json()["verified_by"]) == ("Verified", "u_anand")
    assert client.post(f"/cards/{mg3}/verify", headers=ANAND).status_code == 409  # once


def test_same_test_code_picks_the_earliest_open_card(her2, client):
    """The blood test is in the plan four times (months 3, 6, 9, 12): rows go to the 3-month card first."""
    appointment(client, "CBC", "2026-11-04")
    assert (card(client, "BT3")["state"], card(client, "BT6")["state"]) == ("Booked", "Due")


def test_booking_a_test_from_a_task_books_it(her2, client):
    """Record booking on a test card: Due -> Booked with the date (confirmed by coordinator)."""
    from app import db, help_tasks

    with db.connect() as c:
        help_tasks._book_card(c, her2["US6"]["card_id"], date(2027, 2, 3), None, "u_priya")
    assert (card(client, "US6")["state"], card(client, "US6")["label"]) == ("Booked", "Booked (confirmed by coordinator)")


def test_booked_tap_on_a_test_moves_it_to_booked_then_the_list_adds_the_date(her2, client):
    """Golden Path (Use Case 2T v1.4 switched on): Booked on a test reminder moves the test Due -> Booked for the day
    of the tap (5 Nov: no "no date" state), with no task; the appointment list replaces the date later."""
    from app import clock, db, gateway

    with db.connect() as c:
        gateway._apply_button(c, "SYN-0007", "booked", {"card_id": her2["MG3"]["card_id"]}, None)
        today = clock.get_today(c).isoformat()
    c = card(client, "MG3")
    assert (c["state"], c["label"], c["booking_source"], c["appointment_date"]) ==         ("Booked", None, "patient_button", today)
    assert not [t for t in client.get("/patients/SYN-0007/tasks", headers=PRIYA).json()["tasks"]
                if t["task_type"] == "confirm_test_booking"]
    appointment(client, "MAMMO", "2026-11-05")
    c = card(client, "MG3")
    assert (c["state"], c["appointment_date"]) == ("Booked", "2026-11-05")


def test_lab_report_sent_again_after_a_mismatch_is_linked(her2, client):
    """A report that arrived before the test was Completed (mismatch) can be sent again once it is."""
    first = client.post("/simulated-lab/report", headers=PRIYA, json={"mrn": "SYN-0007", "test_code": "CBC"}).json()
    assert first["status"] == "mismatch"
    appointment(client, "CBC", "2026-11-02", attended=True)
    again = client.post("/simulated-lab/report", headers=PRIYA, json={"mrn": "SYN-0007", "test_code": "CBC"}).json()
    assert again["status"] == "verified" and not again.get("duplicate")
    assert card(client, "BT3")["state"] == "Report verified"
    dup = client.post("/simulated-lab/report", headers=PRIYA, json={"mrn": "SYN-0007", "test_code": "CBC"}).json()
    assert dup.get("duplicate") is True


def test_golden_path_report_check_in_kannada_for_savitri(seeded, client):
    """Use Case 2T v1.4 in the Golden Path data: Savitri Patil (SYN-0011, Kannada) on T1, treatment ended 26 May, so
    the 3-month blood test (19 Aug) is past with no report. The morning run sends the report check in Kannada with
    the three buttons (REQ-24); Done at an outside lab moves the test to Completed and sends the upload request at
    once (REQ-25, as changed by the owner on 25 Oct). A clear report with her name, MRN and test is then verified
    by the system."""
    r = client.post("/patients/SYN-0011/plan/confirm", headers=PRIYA, json={
        "template_id": "BC-HRPOS-1", "therapy": "Surgery and endocrine therapy",
        "treatment_end_date": "2026-05-26", "identifiers_checked": ["name", "mrn"]})
    assert r.status_code == 201, r.text
    morning(client)
    check = next(m for m in outbox("SYN-0011") if m["template_id"] == "test_report_check")
    assert (check["recipient"], check["language"], check["status"]) == ("patient", "Kannada", "sent")
    assert check["body"].startswith("ನಮಸ್ಕಾರ Savitri ಅವರೇ") and "ರಕ್ತ ಪರೀಕ್ಷೆ" in check["body"]
    assert [(b["id"], b["label"]) for b in check["buttons"]] == [
        ("done_outside", "ಹೊರಗಿನ ಲ್ಯಾಬ್‌ನಲ್ಲಿ ಮಾಡಿಸಿದೆ"), ("done_hospital", "ಆಸ್ಪತ್ರೆಯಲ್ಲಿ ಮಾಡಿಸಿದೆ"),
        ("not_done", "ಇನ್ನೂ ಮಾಡಿಸಿಲ್ಲ")]
    r = client.post("/simulator/SYN-0011/reply", headers=PRIYA,
                    json={"button": "done_outside", "in_reply_to": check["message_id"]})
    assert r.status_code == 201, r.text
    bt3 = next(c for c in client.get("/patients/SYN-0011/cards", headers=PRIYA).json() if c["item_code"] == "BT3")
    assert (bt3["state"], bt3["label"]) == ("Completed", "Patient says done at outside lab (report awaited)")
    upload = outbox("SYN-0011")[-1]
    assert (upload["template_id"], upload["language"]) == ("report_upload_request", "Kannada")
    r = client.post("/simulator/SYN-0011/document", headers=PRIYA, data={"variant": "clear"})
    assert r.status_code == 201, r.text
    assert r.json()["routing_applied"] == "auto_matched" and r.json()["hard_checks"]["G8"] is True
    bt3 = next(c for c in client.get("/patients/SYN-0011/cards", headers=PRIYA).json() if c["item_code"] == "BT3")
    assert bt3["state"] == "Report verified"


def test_scan_done_at_an_outside_centre(her2, client):
    """A mammogram done outside (owner's decision, option 1): the tap on Done at an outside lab moves the scan to
    Completed and asks for the report; a clear report with her name, MRN and test moves it to Report received; the
    doctor opens it and verifies it (Verified). Since 5 Nov the reminder offers Booked elsewhere instead; a reminder
    sent before that still carries Done at an outside lab, and its tap is still honoured."""
    morning(client)
    mg = next(m for m in outbox("SYN-0007") if m["recipient"] == "patient" and m["card_id"] == her2["MG3"]["card_id"])
    assert [b["id"] for b in mg["buttons"]] == ["booked", "help_book", "cant_come", "booked_elsewhere"]
    assert mg["buttons"][0]["label_en"] == "Mammogram booked"
    r = client.post("/simulator/SYN-0007/reply", headers=PRIYA,
                    json={"button": "done_outside", "in_reply_to": mg["message_id"]})
    assert r.status_code == 201, r.text
    c = card(client, "MG3")
    assert (c["state"], c["report_road"]) == ("Completed", "outside")
    assert outbox("SYN-0007")[-1]["template_id"] == "report_upload_request"
    assert all(v["card_id"] != c["card_id"] for v in client.get("/doctor/verifications", headers=ANAND).json())
    r = client.post("/simulator/SYN-0007/document", headers=PRIYA, data={"variant": "clear"})
    assert r.status_code == 201, r.text
    assert r.json()["routing_applied"] == "auto_matched" and all(r.json()["hard_checks"].values())
    c = card(client, "MG3")
    assert c["state"] == "Report received" and c["label"].endswith("doctor to verify")
    item = next(v for v in client.get("/doctor/verifications", headers=ANAND).json() if v["card_id"] == c["card_id"])
    assert item["report"]["status"] == "auto_matched"
    assert client.get(f"/reports/{item['report']['report_id']}/file", headers=ANAND).status_code == 200
    assert client.post(f"/cards/{c['card_id']}/verify", headers=ANAND).status_code == 200
    assert card(client, "MG3")["state"] == "Verified"
