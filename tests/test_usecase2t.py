"""Use Case 2T — the test cycle, v1.4 (usecase-docs/): UC2T-T1 to T14 and the audit check.

Seed: TP001, TP004, TP005, TP006, TP008, TP009, TP010 with "Blood test (CBC)" due Mon 23 Nov and a Review visit due
14 Dec (TP001's already Booked for Mon 14 Dec 10:30); TP007 with the blood test only. Plans confirmed on D0, Mon 2 Nov.
The routing setting is Live with the threshold 95 (the demo; a pilot starts in Shadow).
"""
import re

import pytest

from conftest import scalar, users_header

ANITA, RAVI, SEKHAR, ANAND = (users_header(u) for u in ("u_anita", "u_ravi", "u_sekhar", "u_anand"))


@pytest.fixture()
def uc2t(monkeypatch):
    from conftest import DSN

    if not DSN:
        pytest.skip("TEST_DATABASE_URL not set")
    monkeypatch.setenv("DATABASE_URL", DSN)
    from app import db, seed

    with db.connect() as c:
        return seed.reset(c, scenario="uc2t")


def db():
    from app import db as _db

    return _db.connect()


def ok(r, code=200):
    assert r.status_code == code, r.text
    return r.json()


def set_clock(client, day, at="09:00"):
    ok(client.post("/clock/set", headers=ANITA, json={"date": day, "time": at}))


def gap_check(client):
    return ok(client.post("/rules/gap-check", headers=ANITA))


def reminders(client):
    return ok(client.post("/agent/run", headers=ANITA))


def cbc(client, mrn):
    return next(c for c in ok(client.get(f"/patients/{mrn}/cards", headers=ANITA)) if c["test_code"] == "CBC")


def sent(mrn, template=None):
    with db() as c:
        rows = c.execute("SELECT * FROM outbox_message WHERE mrn = %s AND status = 'sent' ORDER BY message_id",
                         (mrn,)).fetchall()
    return [m for m in rows if template is None or m["template_id"] == template]


def tap(client, mrn, button, template="test_booking_reminder"):
    message = sent(mrn, template)[-1]
    return ok(client.post(f"/simulator/{mrn}/reply", headers=ANITA,
                          json={"button": button, "in_reply_to": message["message_id"]}), 201)


def task(client, mrn, task_type):
    return next((t for t in ok(client.get(f"/patients/{mrn}/tasks", headers=ANITA))["tasks"]
                 if t["task_type"] == task_type and t["status"] == "open"), None)


def to_reminder(client):
    set_clock(client, "2026-11-16", "07:00")
    gap_check(client)
    reminders(client)


def morning(client, day):
    set_clock(client, day, "07:00")
    gap_check(client)
    set_clock(client, day, "07:30")
    reminders(client)


def to_check(client):
    """Tue 24 Nov, the first working day after the due date: the report check at 07:00, sent at 07:30."""
    morning(client, "2026-11-24")


def completed(client, mrn, attended_on="2026-11-20"):
    return ok(client.post("/test-cycle/appointment-list", headers=ANITA, json={
        "rows": [{"mrn": mrn, "test_code": "CBC", "date": attended_on, "attended": True}]}))


def lab(client, mrn, **body):
    return client.post("/simulated-lab/report", headers=ANITA, json={"mrn": mrn, "test_code": "CBC", **body})


def document(client, mrn, variant="clear", **form):
    return ok(client.post(f"/simulator/{mrn}/document", headers=ANITA, data={"variant": variant, **form}), 201)


def review(client, mrn):
    items = ok(client.get("/doctor/reviews", headers=SEKHAR))
    return next((i for i in items["open"] if i["mrn"] == mrn), None)


def copy(client, mrn, kind="auto_matched"):
    return next((k for k in ok(client.get("/copies", headers=ANITA)) if k["mrn"] == mrn and k["kind"] == kind), None)


def report(rid):
    with db() as c:
        return c.execute("SELECT * FROM test_report WHERE report_id = %s", (rid,)).fetchone()


def hospital_cycle(client, mrn="TP001"):
    """P steps 1 to 11: up to Report verified and the doctor's item."""
    to_reminder(client)
    set_clock(client, "2026-11-16", "10:15")
    tap(client, mrn, "help_book")
    set_clock(client, "2026-11-17", "10:00")
    t = task(client, mrn, "help_booking")
    ok(client.post(f"/tasks/{t['task_id']}/result", headers=ANITA, json={
        "result": "booked", "appointment_date": "2026-11-20", "appointment_time": "08:00", "note": "Booked the lab slot"}))
    set_clock(client, "2026-11-20", "18:00")
    completed(client, mrn)
    set_clock(client, "2026-11-23", "11:00")
    return ok(lab(client, mrn), 201)


def outside_road(client, mrn="TP004", variant="clear"):
    """N steps 1 to 6: the Booked tap, the report check, Done at an outside lab, then the photo at 10:00."""
    to_reminder(client)
    set_clock(client, "2026-11-16", "10:15")
    tap(client, mrn, "booked")
    to_check(client)
    set_clock(client, "2026-11-24", "08:20")
    tap(client, mrn, "done_outside", "test_report_check")
    set_clock(client, "2026-11-24", "10:00")
    return document(client, mrn, variant)


def test_seed(uc2t, client):
    assert uc2t["scenario"] == "uc2t" and uc2t["patients"] == 8
    c = cbc(client, "TP001")
    assert c["card_type"] == "test" and c["state"] == "Due" and c["due_date"] == "2026-11-23"
    assert c["owner_name"] == "Anita" and c["cover_name"] == "Ravi" and c["origin"] == "plan_item"
    rv = next(x for x in ok(client.get("/patients/TP001/cards", headers=ANITA)) if x["name"] == "Review visit")
    assert rv["state"] == "Booked" and rv["appointment_date"] == "2026-12-14" and rv["appointment_time"] == "10:30:00"
    assert [x["name"] for x in ok(client.get("/patients/TP007/cards", headers=ANITA))] == ["Blood test (CBC)"]
    for mrn in ("TP008", "TP009", "TP010"):
        assert cbc(client, mrn)["state"] == "Due"
    # REQ-02: no order step, no task, no message at plan confirmation; confirming again adds nothing.
    assert scalar("SELECT count(*) FROM coordinator_task") == 0 and scalar("SELECT count(*) FROM outbox_message") == 0
    r = client.post("/patients/TP001/plan/confirm", headers=ANITA,
                    json={"template_id": "UC2T-PLAN", "due_dates": {"CBC": "2026-11-23", "RV": "2026-12-14"}})
    assert r.status_code == 409 and scalar("SELECT count(*) FROM order_card WHERE mrn = 'TP001'") == 2
    routing = ok(client.get("/report-routing", headers=ANITA))
    assert (routing["mode"], routing["threshold"], sum(routing["weights"].values())) == ("live", 95, 100)


def test_t1_full_hospital_cycle(uc2t, client):
    set_clock(client, "2026-11-15", "07:00")
    gap_check(client)
    assert scalar("SELECT count(*) FROM gap_event") == 0  # T-8: nothing
    to_reminder(client)
    assert scalar("SELECT count(*) FROM gap_event WHERE rule = 'G0'") == 8
    m = sent("TP001", "test_booking_reminder")
    assert len(m) == 1 and "not yet booked your Blood test (CBC), advised by Dr. Sekhar, due on 23-Nov" in m[0]["body"]
    assert [b["id"] for b in m[0]["buttons"]] == ["booked", "help_book", "cant_come", "done_outside"]
    assert m[0]["buttons"][0]["label"] == "Blood test booked (I have already booked)"
    assert scalar("SELECT reason_code FROM audit_ledger WHERE action = 'agent_decision' AND mrn = 'TP001'") == "gap_actionable"
    set_clock(client, "2026-11-16", "10:15")
    tap(client, "TP001", "help_book")
    assert cbc(client, "TP001")["state"] == "Due"  # the tap alone leaves the card Due
    t = task(client, "TP001", "help_booking")
    assert t["due_date"] == "2026-11-18" and t["card_id"] == cbc(client, "TP001")["card_id"]
    assert ok(client.get("/patients/TP001/tasks", headers=ANITA))["hold"] is True
    set_clock(client, "2026-11-17", "10:00")
    # Record booking needs a date and a time.
    assert client.post(f"/tasks/{t['task_id']}/result", headers=ANITA,
                       json={"result": "booked", "appointment_date": "2026-11-20"}).status_code == 422
    ok(client.post(f"/tasks/{t['task_id']}/result", headers=ANITA, json={
        "result": "booked", "appointment_date": "2026-11-20", "appointment_time": "08:00", "note": "Lab slot booked"}))
    c = cbc(client, "TP001")
    assert (c["state"], c["booking_source"], c["appointment_date"], c["appointment_time"], c["label"]) == \
        ("Booked", "coordinator", "2026-11-20", "08:00:00", "Booked (confirmed by coordinator)")
    set_clock(client, "2026-11-20", "18:00")
    completed(client, "TP001")
    c = cbc(client, "TP001")
    assert c["state"] == "Completed" and c["completed_on"] == "2026-11-20"
    set_clock(client, "2026-11-23", "11:00")
    rep = ok(lab(client, "TP001"), 201)
    assert rep["status"] == "verified"
    c = cbc(client, "TP001")
    # Linked and checked by MRN and test code in the same run: Report verified, actor System, no AI.
    assert (c["state"], c["verified_by"], c["report_road"]) == ("Report verified", "CarePilot", "hospital")
    assert c["report"]["file_name"]
    item = review(client, "TP001")
    assert item["test_name"] == "Blood test (CBC)" and item["road"] == "hospital" and item["doctor"] == "Dr. Sekhar"
    assert item["checked"] == "by the system (MRN and test code match)"
    assert not any(k in item for k in ("value", "result", "summary", "flag"))
    # No message on arrival; the doctor's click comes first.
    assert not any(m["template_id"].startswith("report_continue") for m in sent("TP001"))
    # REQ-24: no report check on Tue 24 Nov, the card is Completed (here: verified).
    morning(client, "2026-11-24")
    assert sent("TP001", "test_report_check") == []
    assert scalar("SELECT count(*) FROM report_check WHERE mrn = 'TP001'") == 0
    set_clock(client, "2026-11-24", "09:00")
    f = client.get(f"/reports/{rep['report_id']}/file", headers=SEKHAR)
    assert f.status_code == 200 and f.headers["content-type"] == "application/pdf" and f.content.startswith(b"%PDF")
    assert scalar("SELECT count(*) FROM audit_ledger WHERE action = 'report_file_opened' AND actor_name = 'u_sekhar'") == 1
    assert client.post(f"/reviews/{item['review_id']}/decision", headers=SEKHAR, json={}).status_code == 422
    assert client.post(f"/reviews/{item['review_id']}/decision", headers=ANITA,
                       json={"decision": "continue"}).status_code == 403  # the coordinator has neither button
    ok(client.post(f"/reviews/{item['review_id']}/decision", headers=SEKHAR, json={"decision": "continue"}))
    c = cbc(client, "TP001")
    with db() as conn:
        moves = [(a["details"]["from"], a["details"]["to"]) for a in conn.execute(
            "SELECT details FROM audit_ledger WHERE action = 'card_state_changed' AND entity_id = %s ORDER BY id",
            (str(c["card_id"]),)).fetchall()]
    assert moves == [("Due", "Booked"), ("Booked", "Completed"), ("Completed", "Report received"),
                     ("Report received", "Report verified")]
    msgs = sent("TP001", "report_continue_booked")
    assert len(msgs) == 1
    assert "Dr. Sekhar has reviewed your Blood test (CBC) report. No change to your plan is needed. Please continue " \
           "with your next planned visit on Mon 14 Dec at 10:30." in msgs[0]["body"]
    assert review(client, "TP001") is None
    assert client.post(f"/reviews/{item['review_id']}/decision", headers=SEKHAR,
                       json={"decision": "continue"}).status_code == 409
    assert scalar("SELECT count(*) FROM audit_ledger WHERE actor_type = 'ai_suggestion'") == 0


def test_t2_outside_lab_road(uc2t, client):
    to_reminder(client)
    set_clock(client, "2026-11-16", "10:15")
    tap(client, "TP004", "booked")
    c = cbc(client, "TP004")
    assert (c["state"], c["label"], c["booking_source"], c["appointment_date"]) == \
        ("Booked", "Patient says booked (no date)", "patient_button", None)
    assert task(client, "TP004", "help_booking") is None
    # The report check: raised Tue 24 Nov at 07:00, sent at 07:30, once.
    morning(client, "2026-11-23")
    assert scalar("SELECT count(*) FROM report_check WHERE mrn = 'TP004'") == 0
    to_check(client)
    checks = sent("TP004", "test_report_check")
    assert len(checks) == 1 and [b["id"] for b in checks[0]["buttons"]] == ["done_outside", "done_hospital", "not_done"]
    assert "If you did the test at another lab, please send a clear photo or PDF" in checks[0]["body"]
    assert scalar("SELECT reason_code FROM outbox_message WHERE template_id = 'test_report_check' AND mrn = 'TP004'") \
        == "report_check_due"
    set_clock(client, "2026-11-24", "08:20")
    before = len(sent("TP004"))
    tap(client, "TP004", "done_outside", "test_report_check")
    assert cbc(client, "TP004")["label"] == "Patient says done at outside lab (report awaited)"
    assert [m["template_id"] for m in sent("TP004")[before:]] == ["report_upload_request"]  # and nothing else
    assert "Tap the attach (paper-clip) icon" in sent("TP004")[-1]["body"]
    c = cbc(client, "TP004")
    assert (c["state"], c["report_road"]) == ("Completed", "outside")  # owner's decision: the tap completes it
    set_clock(client, "2026-11-24", "10:00")
    before = len(sent("TP004"))
    r = document(client, "TP004")
    assert (r["identifiers"]["name"], r["identifiers"]["mrn"], r["identifiers"]["age"]) == ("Kavita", "TP004", "54")
    assert not set(r["identifiers"]) - {"name", "mrn", "age", "sex", "lab_id", "facility", "report_date",
                                        "document_type"}
    assert r["score"] == 97 and all(r["hard_checks"].values()) and r["routing_applied"] == "auto_matched"
    assert scalar("SELECT timer_from FROM report_check WHERE mrn = 'TP004'") is None  # the timer stopped
    # One save: name, MRN and test match -> Report received -> Report verified by the system, the doctor's item;
    # no person confirms it, no copy and no Verify task.
    c = cbc(client, "TP004")
    assert (c["state"], c["report_road"]) == ("Report verified", "outside")
    assert c["label"] == "Outside report (checked by AI: name, MRN and test match, score 97)"
    item = review(client, "TP004")
    assert item["road"] == "outside" and item["checked"] == "by the system (AI read: name, MRN and test match, score 97)"
    assert item["unverified_label"] is None
    assert {"name: match", "MRN: match"} <= set(item["match_facts"]) and item["report_date"] == "2026-11-24"
    assert item["days_since_due"] == 1
    assert copy(client, "TP004") is None and task(client, "TP004", "outside_report") is None
    assert len(sent("TP004")) == before  # the patient receives no message
    assert scalar("SELECT count(*) FROM audit_ledger WHERE actor_type = 'ai_suggestion'") == 1  # the only AI entry
    set_clock(client, "2026-11-25", "09:00")
    ok(client.post(f"/reviews/{item['review_id']}/decision", headers=SEKHAR, json={"decision": "continue"}))
    assert sent("TP004")[-1]["template_id"] == "report_continue_due"
    assert "next visit, due by Mon 14 Dec" in sent("TP004")[-1]["body"]


def test_t2_reminder_done_outside_button(uc2t, client):
    """The test reminder's fourth button: Done at an outside lab moves the card to Completed and asks for the file;
    the upload timer runs (one nudge, then a call task) as after the report check."""
    to_reminder(client)
    set_clock(client, "2026-11-16", "10:15")
    tap(client, "TP008", "done_outside")
    c = cbc(client, "TP008")
    assert (c["state"], c["label"]) == ("Completed", "Patient says done at outside lab (report awaited)")
    assert sent("TP008")[-1]["template_id"] == "report_upload_request"
    assert scalar("SELECT count(*) FROM gap_event WHERE mrn = 'TP008' AND status <> 'resolved'") == 0
    morning(client, "2026-11-18")
    assert len(sent("TP008", "report_upload_nudge")) == 1
    morning(client, "2026-11-20")
    assert task(client, "TP008", "call_about_test") is not None
    to_check(client)
    assert sent("TP008", "test_report_check") == []  # Completed: no report check


def test_t2_report_without_mrn_goes_to_the_coordinator(uc2t, client):
    set_clock(client, "2026-11-24", "10:00")
    r = document(client, "TP004", "no_mrn")
    assert r["hard_checks"]["G8"] is False and r["routing_applied"] == "verify"
    assert "MRN: not on the report" in task(client, "TP004", "outside_report")["reason"]
    assert cbc(client, "TP004")["state"] == "Due" and review(client, "TP004") is None


def test_t2_reject_keeps_card_and_file(uc2t, client):
    set_clock(client, "2026-11-24", "10:00")
    r = document(client, "TP010", "blurred")
    assert r["routing_applied"] == "verify"
    rid = r["report_id"]
    assert client.post(f"/reports/{rid}/reject", headers=ANITA, json={"reason": " "}).status_code == 422
    ok(client.post(f"/reports/{rid}/reject", headers=ANITA, json={"reason": "Belongs to another patient"}))
    assert cbc(client, "TP010")["state"] == "Due"
    assert (report(rid)["status"], report(rid)["blob_id"] is not None) == ("rejected", True)
    assert review(client, "TP010") is None
    t = task(client, "TP010", "call_patient_resend")
    assert t["owner_name"] == "Anita" and t["due_date"] == "2026-11-26"
    assert scalar("SELECT count(*) FROM audit_ledger WHERE reason_code = 'outside_report_rejected'") == 1


def test_t3_illegal_moves_rejected_and_audited(uc2t, client):
    card = cbc(client, "TP005")["card_id"]

    def refused(to_state, **body):
        before = scalar("SELECT count(*) FROM audit_ledger WHERE action = 'transition_refused'")
        r = client.post(f"/cards/{card}/transition", headers=ANITA, json={"to_state": to_state, **body})
        assert r.status_code == 409, (to_state, body, r.text)
        assert scalar("SELECT count(*) FROM audit_ledger WHERE action = 'transition_refused'") == before + 1

    refused("Report received", file_ref="x.pdf")                       # Due -> Report received for a hospital report
    refused("Completed", source="patient_reply")                       # Due -> Completed from a patient message
    refused("Scheduled", appointment_date="2026-11-20")                # these states do not exist
    refused("Ordered")
    assert cbc(client, "TP005")["state"] == "Due"
    ok(client.post(f"/cards/{card}/transition", headers=ANITA, json={"to_state": "Booked", "source": "coordinator"}))
    refused("Report received", source="outside_report_verified")       # Booked -> Report received with no file
    refused("Scheduled", appointment_date="2026-11-20")
    completed(client, "TP005")
    ok(client.post(f"/cards/{card}/transition", headers=ANITA, json={"to_state": "Report received", "file_ref": "r.pdf"}))
    refused("Report verified", matched_identifiers=1)                  # one identifier
    assert cbc(client, "TP005")["state"] == "Report received"


def test_t4_booked_tap(uc2t, client):
    to_reminder(client)
    set_clock(client, "2026-11-16", "10:15")
    tap(client, "TP005", "booked")
    c = cbc(client, "TP005")
    assert (c["state"], c["label"], c["patient_says_booked"]) == ("Booked", "Patient says booked (no date)", True)
    assert scalar("SELECT count(*) FROM coordinator_task WHERE mrn = 'TP005'") == 0
    assert scalar("SELECT status FROM gap_event WHERE card_id = %s", c["card_id"]) == "resolved"
    set_clock(client, "2026-11-17", "07:00")
    gap_check(client)
    reminders(client)
    assert len(sent("TP005", "test_booking_reminder")) == 1
    # A date later (the appointment list) keeps the card Booked and replaces the label.
    ok(client.post("/test-cycle/appointment-list", headers=ANITA, json={
        "rows": [{"mrn": "TP005", "test_code": "CBC", "date": "2026-11-20", "time": "09:00"}]}))
    c = cbc(client, "TP005")
    assert (c["state"], c["patient_says_booked"], c["booking_source"], c["appointment_date"], c["label"]) == \
        ("Booked", False, "appointment_list", "2026-11-20", None)


def test_t4_record_booking_replaces_label(uc2t, client):
    to_reminder(client)
    tap(client, "TP005", "booked")
    tap(client, "TP005", "help_book")
    t = task(client, "TP005", "help_booking")
    ok(client.post(f"/tasks/{t['task_id']}/result", headers=ANITA, json={
        "result": "booked", "appointment_date": "2026-11-20", "appointment_time": "08:00"}))
    c = cbc(client, "TP005")
    assert (c["state"], c["booking_source"], c["patient_says_booked"], c["label"]) == \
        ("Booked", "coordinator", False, "Booked (confirmed by coordinator)")


def test_t5_patient_words_do_not_move_the_card(uc2t, client):
    to_reminder(client)
    tap(client, "TP001", "help_book")
    t = task(client, "TP001", "help_booking")
    ok(client.post(f"/tasks/{t['task_id']}/result", headers=ANITA, json={
        "result": "booked", "appointment_date": "2026-11-20", "appointment_time": "08:00"}))
    r = ok(client.post("/simulator/TP001/reply", headers=ANITA, json={"text": "I have done my test"}), 201)
    assert r["handover_id"] or r["proposal_id"]  # the Use Case 2a path: a person sees it
    assert cbc(client, "TP001")["state"] == "Booked"
    completed(client, "TP001")
    assert cbc(client, "TP001")["state"] == "Completed"


def test_t6_report_cannot_be_linked(uc2t, client):
    card = cbc(client, "TP006")
    ok(client.post(f"/cards/{card['card_id']}/transition", headers=ANITA,
                   json={"to_state": "Booked", "source": "coordinator", "appointment_date": "2026-11-20"}))
    completed(client, "TP006")
    # Wrong test code: not attached, exceptions list.
    r = ok(lab(client, "TP006", test_code="LFT"), 201)
    assert r["status"] == "exception"
    assert scalar("SELECT count(*) FROM import_exception WHERE reason_code = 'report_no_matching_card'") == 1
    assert cbc(client, "TP006")["state"] == "Completed"
    # Wrong MRN on the report: a Report mismatch task, never verified.
    r = ok(lab(client, "TP006", report_mrn="TP009"), 201)
    assert r["status"] == "mismatch" and cbc(client, "TP006")["state"] == "Completed"
    t = task(client, "TP006", "report_mismatch")
    assert t["owner_name"] == "Anita" and "TP009" in t["reason"]
    assert review(client, "TP006") is None
    # No file: refused.
    assert lab(client, "TP006", with_file=False).status_code == 422
    # The correct report sent twice is attached once.
    first = ok(lab(client, "TP006"), 201)
    again = ok(lab(client, "TP006"), 201)
    assert again["report_id"] == first["report_id"] and again["duplicate"] is True
    assert scalar("SELECT count(*) FROM test_report WHERE mrn = 'TP006' AND status = 'verified'") == 1
    assert cbc(client, "TP006")["state"] == "Report verified"


def test_t7_no_next_visit_card(uc2t, client):
    hospital_cycle(client, "TP007")
    item = review(client, "TP007")
    ok(client.post(f"/reviews/{item['review_id']}/decision", headers=SEKHAR, json={"decision": "continue"}))
    assert not any(m["template_id"].startswith("report_continue") for m in sent("TP007"))
    assert task(client, "TP007", "no_next_visit") is not None


def test_t7_needs_discussion(uc2t, client):
    hospital_cycle(client)
    set_clock(client, "2026-11-24", "09:00")
    item = review(client, "TP001")
    ok(client.post(f"/reviews/{item['review_id']}/decision", headers=SEKHAR, json={"decision": "discussion"}))
    assert review(client, "TP001")["status"] == "awaiting_instruction"
    ok(client.post(f"/reviews/{item['review_id']}/instruction", headers=SEKHAR,
                   json={"text": "Review visit within 7 days", "days": 7}))
    cards = ok(client.get("/patients/TP001/cards", headers=ANITA))
    x = next(c for c in cards if c["name"] == "Doctor-requested visit")
    assert x["state"] == "Due" and x["due_date"] == "2026-12-01" and x["owner_name"] == "Anita" and x["origin"] == "instruction"
    assert task(client, "TP001", "booking_from_instruction")["due_date"] == "2026-11-26"
    assert cbc(client, "TP001")["state"] == "Report verified"
    templates = [m["template_id"] for m in sent("TP001")]
    assert "instruction_booking" in templates and not any(t.startswith("report_continue") for t in templates)
    assert scalar("SELECT count(*) FROM gap_event WHERE card_id = %s", x["card_id"]) == 0


def test_t8_doctor_does_not_decide(uc2t, client):
    hospital_cycle(client)
    set_clock(client, "2026-11-24", "10:59")
    assert review(client, "TP001")["doctor"] == "Dr. Sekhar"
    set_clock(client, "2026-11-25", "09:01")
    item = review(client, "TP001")
    assert item["doctor"] == "Dr. Anand Rao" and item["moved_from"] == "Dr. Sekhar" and item["status"] == "open"
    assert scalar("SELECT count(*) FROM audit_ledger WHERE action = 'review_moved_to_cover'") == 1
    assert not any(m["template_id"].startswith("report_continue") for m in sent("TP001"))
    # The cover decides.
    ok(client.post(f"/reviews/{item['review_id']}/decision", headers=ANAND, json={"decision": "continue"}))


def test_t9_repeat_runs(uc2t, client):
    to_reminder(client)
    gap_check(client)
    reminders(client)
    assert scalar("SELECT count(*) FROM gap_event WHERE mrn = 'TP001'") == 1
    assert len(sent("TP001", "test_booking_reminder")) == 1
    reminder = sent("TP001", "test_booking_reminder")[0]
    for _ in range(2):
        client.post("/simulator/TP001/reply", headers=ANITA,
                    json={"button": "help_book", "in_reply_to": reminder["message_id"], "event_id": "tap-1"})
    assert scalar("SELECT count(*) FROM coordinator_task WHERE mrn = 'TP001' AND task_type = 'help_booking'") == 1


def test_t10_report_check_rules(uc2t, client):
    morning(client, "2026-11-23")  # the due date: nothing is raised
    assert scalar("SELECT count(*) FROM report_check") == 0
    to_check(client)
    assert scalar("SELECT count(*) FROM report_check WHERE mrn = 'TP004'") == 1
    assert len(sent("TP004", "test_report_check")) == 1
    gap_check(client)  # the 07:00 and 07:30 jobs twice: still one
    reminders(client)
    assert scalar("SELECT count(*) FROM report_check WHERE mrn = 'TP004'") == 1
    assert len(sent("TP004", "test_report_check")) == 1


def test_t10_hospital_data_first(uc2t, client):
    set_clock(client, "2026-11-23", "11:00")
    held = ok(lab(client, "TP004", deliver=False), 201)  # in the hospital data only
    assert held["status"] == "held" and cbc(client, "TP004")["state"] == "Due"
    # TP001 is Completed (the list showed she came): no report check for her.
    completed(client, "TP001", "2026-11-20")
    to_check(client)
    c = cbc(client, "TP004")
    assert (c["state"], c["report_road"]) == ("Report verified", "hospital")
    assert sent("TP004", "test_report_check") == []
    assert scalar("SELECT status FROM report_check WHERE mrn = 'TP004'") == "skipped"
    assert scalar("SELECT count(*) FROM audit_ledger WHERE reason_code = 'report_check_skipped' AND mrn = 'TP004'") >= 1
    assert scalar("SELECT count(*) FROM report_check WHERE mrn = 'TP001'") == 0
    assert review(client, "TP004")["road"] == "hospital"
    labels = [e["label"] for e in ok(client.get("/patients/TP004/timeline", headers=ANITA))["events"]]
    assert any("found in the hospital data and linked" in l for l in labels)


def test_t11_done_outside(uc2t, client):
    to_check(client)
    before = len(sent("TP008"))
    tap(client, "TP008", "done_outside", "test_report_check")
    assert cbc(client, "TP008")["label"] == "Patient says done at outside lab (report awaited)"
    assert [m["template_id"] for m in sent("TP008")[before:]] == ["report_upload_request"]


def test_t11_done_at_hospital(uc2t, client):
    to_check(client)
    before = len(sent("TP008"))
    tap(client, "TP008", "done_hospital", "test_report_check")
    c = cbc(client, "TP008")
    assert (c["label"], c["state"]) == ("Patient says done at hospital", "Due")
    t = task(client, "TP008", "find_hospital_report")
    assert t["due_date"] == "2026-11-26" and t["owner_name"] == "Anita" and t["cover_name"] == "Ravi"
    assert len(sent("TP008")) == before


def test_t11_not_done_yet_repeats_twice_then_a_call(uc2t, client):
    to_check(client)
    tap(client, "TP008", "not_done", "test_report_check")
    assert cbc(client, "TP008")["label"] == "Patient says not done yet"
    morning(client, "2026-11-26")
    assert len(sent("TP008", "test_report_check")) == 1  # not yet: 3 working days
    morning(client, "2026-11-27")
    assert len(sent("TP008", "test_report_check")) == 2  # repeat 1
    tap(client, "TP008", "not_done", "test_report_check")
    morning(client, "2026-12-02")
    assert len(sent("TP008", "test_report_check")) == 3  # repeat 2, the last
    tap(client, "TP008", "not_done", "test_report_check")
    morning(client, "2026-12-07")
    assert len(sent("TP008", "test_report_check")) == 3
    assert task(client, "TP008", "call_about_test") is not None
    templates = {m["template_id"] for m in sent("TP008")}
    assert templates == {"test_report_check"}  # no other message


def test_t11_no_answer(uc2t, client):
    to_check(client)
    morning(client, "2026-11-25")
    assert task(client, "TP008", "call_about_test") is None
    morning(client, "2026-11-26")
    t = task(client, "TP008", "call_about_test")
    assert t is not None and "no answer" in t["reason"]


def test_t11_no_upload_one_nudge_then_a_call(uc2t, client):
    to_check(client)
    tap(client, "TP008", "done_outside", "test_report_check")
    morning(client, "2026-11-25")
    assert sent("TP008", "report_upload_nudge") == []
    morning(client, "2026-11-26")
    nudge = sent("TP008", "report_upload_nudge")
    assert len(nudge) == 1 and "still waiting for the report of your Blood test (CBC)" in nudge[0]["body"]
    morning(client, "2026-11-27")
    assert task(client, "TP008", "call_about_test") is None
    morning(client, "2026-11-30")
    assert task(client, "TP008", "call_about_test") is not None
    morning(client, "2026-12-01")
    assert len(sent("TP008", "report_upload_nudge")) == 1  # never a second nudge


def test_t11_a_file_stops_the_timers(uc2t, client):
    to_check(client)
    tap(client, "TP008", "done_outside", "test_report_check")
    set_clock(client, "2026-11-25", "10:00")
    document(client, "TP008")
    morning(client, "2026-11-26")
    morning(client, "2026-11-30")
    assert sent("TP008", "report_upload_nudge") == [] and task(client, "TP008", "call_about_test") is None


def test_t12_wrong_or_unclear_report(uc2t, client):
    to_check(client)
    tap(client, "TP009", "done_outside", "test_report_check")
    set_clock(client, "2026-11-24", "10:00")
    before = len(sent("TP009"))
    r = document(client, "TP009", "ct_scan")
    assert r["routing_applied"] == "wrong_report" and r["hard_checks"]["G1"] is False
    rep = report(r["report_id"])
    assert (rep["status"], rep["wrong_report"]) == ("unverified", True)
    msgs = sent("TP009")[before:]
    assert [m["template_id"] for m in msgs] == ["report_wrong_report"]
    assert "may not be the report of your Blood test (CBC)" in msgs[0]["body"]
    assert not re.search(r"\b(ct|scan|radiology|chest)\b", msgs[0]["body"], re.I)  # never what the file seems to be
    assert scalar("SELECT reason_code FROM outbox_message WHERE template_id = 'report_wrong_report'") == "wrong_report_asked"
    assert cbc(client, "TP009")["state"] == "Completed" and review(client, "TP009") is None
    assert copy(client, "TP009", "wrong_report")["owner_name"] == "Anita"
    assert scalar("SELECT timer_from FROM report_check WHERE mrn = 'TP009'") is not None  # the timer restarted
    # A second wrong or unclear file: a "Call about test" task, no second message.
    set_clock(client, "2026-11-24", "11:00")
    r2 = document(client, "TP009", "unreadable")
    assert r2["routing_applied"] == "wrong_report" and r2["hard_checks"]["G5"] is False
    assert len(sent("TP009", "report_wrong_report")) == 1
    assert task(client, "TP009", "call_about_test") is not None
    # A clearly different name goes to the coordinator, not to the patient; Verify needs two identifiers.
    set_clock(client, "2026-11-24", "12:00")
    before = len(sent("TP009"))
    r3 = document(client, "TP009", "other_name")
    assert r3["routing_applied"] == "verify" and r3["hard_checks"]["G3"] is False and r3["hard_checks"]["G8"] is False
    assert len(sent("TP009")) == before
    t = task(client, "TP009", "outside_report")
    assert "name: no match" in t["reason"]
    assert client.post(f"/reports/{r3['report_id']}/verify", headers=ANITA, json={"matched": ["phone"]}).status_code == 422


def test_t12_unreadable_photo_gets_the_message(uc2t, client):
    set_clock(client, "2026-11-24", "10:00")
    r = document(client, "TP010", "unreadable")
    assert r["identifiers"] == {} and r["routing_applied"] == "wrong_report"
    assert len(sent("TP010", "report_wrong_report")) == 1


def test_t13_below_threshold_goes_to_the_coordinator(uc2t, client):
    set_clock(client, "2026-11-24", "10:00")
    r = document(client, "TP010", "blurred")
    assert r["score"] == 82 and r["routing_applied"] == "verify"
    assert review(client, "TP010") is None
    t = task(client, "TP010", "outside_report")
    assert "score 82" in t["reason"] and "name: partly read" in t["reason"]
    ok(client.post(f"/reports/{r['report_id']}/verify", headers=ANITA, json={"matched": ["age", "sex"]}))
    c = cbc(client, "TP010")
    assert (c["state"], c["label"]) == ("Report verified", "Outside report (verified by coordinator)")
    item = review(client, "TP010")
    assert item["checked"].startswith("by the coordinator, Anita")


@pytest.mark.parametrize("form, failed", [
    ({"from_phone": "+919999999999"}, "G7"),                 # an unknown number never passes
    ({"variant": "future_date"}, "G4"),                       # a report dated in the future
])
def test_t13_hard_checks_stop_auto_routing(uc2t, client, form, failed):
    set_clock(client, "2026-11-24", "10:00")
    variant = form.pop("variant", "clear")
    r = document(client, "TP004", variant, **form)
    assert r["hard_checks"][failed] is False and r["routing_applied"] == "verify"
    assert review(client, "TP004") is None and task(client, "TP004", "outside_report") is not None


def test_t13_duplicate_file_of_another_patient(uc2t, client):
    set_clock(client, "2026-11-24", "10:00")
    first = document(client, "TP004")
    data = client.get(f"/reports/{first['report_id']}/file", headers=ANITA).content
    r = ok(client.post("/simulator/TP001/document", headers=ANITA,
                       files={"file": ("report.pdf", data, "application/pdf")}), 201)
    assert r["hard_checks"]["G6"] is False and r["routing_applied"] == "verify"


def test_t14_shadow_mode_and_the_threshold(uc2t, client):
    assert client.put("/report-routing", headers=ANITA, json={"mode": "shadow"}).status_code == 403
    assert client.put("/report-routing", headers=ANAND, json={"mode": "shadow"}).status_code == 403
    assert client.put("/report-routing", headers=SEKHAR, json={"threshold": 101}).status_code == 422
    ok(client.put("/report-routing", headers=SEKHAR, json={"mode": "shadow"}))
    set_clock(client, "2026-11-24", "10:00")
    r = document(client, "TP004")
    assert r["score"] == 97 and report(r["report_id"])["routing"] == "auto_matched"  # shown, not applied
    assert r["routing_applied"] == "verify" and review(client, "TP004") is None
    assert "Shadow mode" in task(client, "TP004", "outside_report")["reason"]
    ok(client.put("/report-routing", headers=SEKHAR, json={"mode": "live", "threshold": 96}))
    assert ok(client.get("/report-routing", headers=ANITA))["threshold"] == 96
    assert scalar("SELECT count(*) FROM audit_ledger WHERE action = 'report_routing_changed'") == 2


def test_t14_document_test_set(uc2t, client):
    set_clock(client, "2026-11-24", "10:00")
    e = ok(client.get("/report-routing/evaluation", headers=SEKHAR))
    assert e["threshold"] == 95 and e["should_not_pass"] > 0
    assert e["reached_threshold"] == 0 and e["would_auto_route"] == 0  # the target is zero
    assert e["right_reports_auto_routed"] == e["right_reports"] > 0
    assert {r["case"] for r in e["rows"]} >= {"clear", "wrong_patient", "ct_scan", "blurred", "unknown_sender"}


def test_audit_thread_positive_case(uc2t, client):
    hospital_cycle(client)
    set_clock(client, "2026-11-24", "09:00")
    item = review(client, "TP001")
    client.get(f"/reports/{item['report_id']}/file", headers=SEKHAR)
    ok(client.post(f"/reviews/{item['review_id']}/decision", headers=SEKHAR, json={"decision": "continue"}))
    with db() as c:
        got = [(a["action"], a["actor_type"]) for a in c.execute(
            "SELECT action, actor_type FROM audit_ledger WHERE mrn = 'TP001' ORDER BY id").fetchall()]
    want = [("plan_confirmed", "person"), ("card_created", "system"), ("gap_detected", "system"),
            ("agent_decision", "agent"), ("message_sent", "agent"), ("reply_received", "system"),
            ("task_created", "system"), ("task_result_recorded", "person"), ("card_state_changed", "system"),
            ("test_appointment_list_loaded", "person"), ("card_state_changed", "system"),
            ("lab_report_received", "system"), ("card_state_changed", "system"), ("report_linked", "system"),
            ("card_state_changed", "system"), ("review_item_created", "system"), ("report_file_opened", "person"),
            ("report_review_decided", "person"), ("message_sent", "agent")]
    it = iter(got)
    missing = [w for w in want if w not in it]
    assert not missing, (missing, got)
    labels = [e["label"] for e in ok(client.get("/patients/TP001/timeline", headers=SEKHAR))["events"]]
    assert any("Lab report for Blood test (CBC) linked and checked" in l for l in labels)
    assert any("Doctor's decision on the Blood test (CBC) report: Continue as planned" in l for l in labels)


def test_audit_thread_negative_case(uc2t, client):
    outside_road(client)
    with db() as c:
        got = [(a["action"], a["actor_type"]) for a in c.execute(
            "SELECT action, actor_type FROM audit_ledger WHERE mrn = 'TP004' ORDER BY id").fetchall()]
    want = [("reply_received", "system"), ("card_state_changed", "system"), ("report_check_raised", "system"),
            ("agent_decision", "agent"), ("message_sent", "agent"), ("reply_received", "system"),
            ("card_state_changed", "system"), ("message_sent", "agent"), ("outside_report_received", "system"),
            ("identifiers_read", "ai_suggestion"), ("report_scored", "system"), ("card_state_changed", "system"),
            ("card_state_changed", "system"), ("outside_report_auto_matched", "system"),
            ("review_item_created", "system")]
    it = iter(got)
    missing = [w for w in want if w not in it]
    assert not missing, (missing, got)
    assert sum(1 for a in got if a[1] == "ai_suggestion") == 1
    labels = [e["label"] for e in ok(client.get("/patients/TP004/timeline", headers=SEKHAR))["events"]]
    assert any("Report check sent" in l for l in labels) and any("Patient tapped: Done at an outside lab" in l for l in labels)
    assert any("Outside report scored 97 by plain rules; all hard checks pass" in l for l in labels)
    assert not [a for a in got if a[0] in ("coordinator_copy_created", "auto_matched_report_confirmed")]
