"""Use Case 2T — the test cycle, v1.2 (usecase-docs/): UC2T-T1 to T9 and the audit check.

Seed: TP001, TP004, TP005, TP006 with "Blood test (CBC)" due Mon 23 Nov and a Review visit due 14 Dec (TP001's
already Booked for Mon 14 Dec 10:30); TP007 with the blood test only. Plans confirmed on D0, Mon 2 Nov.
"""
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


def sent(mrn):
    with db() as c:
        return c.execute("SELECT * FROM outbox_message WHERE mrn = %s AND status = 'sent' ORDER BY message_id",
                         (mrn,)).fetchall()


def tap(client, mrn, button):
    reminder = next(m for m in sent(mrn) if m["template_id"] == "test_booking_reminder")
    return ok(client.post(f"/simulator/{mrn}/reply", headers=ANITA,
                          json={"button": button, "in_reply_to": reminder["message_id"]}), 201)


def task(client, mrn, task_type):
    return next((t for t in ok(client.get(f"/patients/{mrn}/tasks", headers=ANITA))["tasks"]
                 if t["task_type"] == task_type), None)


def to_reminder(client):
    set_clock(client, "2026-11-16", "07:00")
    gap_check(client)
    reminders(client)


def completed(client, mrn, attended_on="2026-11-20"):
    return ok(client.post("/test-cycle/appointment-list", headers=ANITA, json={
        "rows": [{"mrn": mrn, "test_code": "CBC", "date": attended_on, "attended": True}]}))


def lab(client, mrn, **body):
    return client.post("/simulated-lab/report", headers=ANITA, json={"mrn": mrn, "test_code": "CBC", **body})


def review(client, mrn):
    items = ok(client.get("/doctor/reviews", headers=SEKHAR))
    return next((i for i in items["open"] if i["mrn"] == mrn), None)


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


def test_seed(uc2t, client):
    assert uc2t["scenario"] == "uc2t" and uc2t["patients"] == 5
    c = cbc(client, "TP001")
    assert c["card_type"] == "test" and c["state"] == "Due" and c["due_date"] == "2026-11-23"
    assert c["owner_name"] == "Anita" and c["cover_name"] == "Ravi" and c["origin"] == "plan_item"
    rv = next(x for x in ok(client.get("/patients/TP001/cards", headers=ANITA)) if x["name"] == "Review visit")
    assert rv["state"] == "Booked" and rv["appointment_date"] == "2026-12-14" and rv["appointment_time"] == "10:30:00"
    assert [x["name"] for x in ok(client.get("/patients/TP007/cards", headers=ANITA))] == ["Blood test (CBC)"]
    # REQ-02: no order step, no task, no message at plan confirmation; confirming again adds nothing.
    assert scalar("SELECT count(*) FROM coordinator_task") == 0 and scalar("SELECT count(*) FROM outbox_message") == 0
    r = client.post("/patients/TP001/plan/confirm", headers=ANITA,
                    json={"template_id": "UC2T-PLAN", "due_dates": {"CBC": "2026-11-23", "RV": "2026-12-14"}})
    assert r.status_code == 409 and scalar("SELECT count(*) FROM order_card WHERE mrn = 'TP001'") == 2


def test_t1_full_hospital_cycle(uc2t, client):
    set_clock(client, "2026-11-15", "07:00")
    gap_check(client)
    assert scalar("SELECT count(*) FROM gap_event") == 0  # T-8: nothing
    to_reminder(client)
    assert scalar("SELECT count(*) FROM gap_event WHERE rule = 'G0'") == 5
    m = [x for x in sent("TP001") if x["template_id"] == "test_booking_reminder"]
    assert len(m) == 1 and "not yet booked your Blood test (CBC), advised by Dr. Sekhar, due on 23-Nov" in m[0]["body"]
    assert [b["id"] for b in m[0]["buttons"]] == ["booked", "help_book", "cant_come"]
    assert scalar("SELECT reason_code FROM audit_ledger WHERE action = 'agent_decision' AND mrn = 'TP001'") == "gap_actionable"
    set_clock(client, "2026-11-16", "10:15")
    tap(client, "TP001", "help_book")
    assert cbc(client, "TP001")["state"] == "Due"
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
    assert c["state"] == "Scheduled" and c["booking_source"] == "coordinator" and c["appointment_date"] == "2026-11-20"
    assert c["appointment_time"] == "08:00:00"
    set_clock(client, "2026-11-20", "18:00")
    completed(client, "TP001")
    c = cbc(client, "TP001")
    assert c["state"] == "Completed" and c["completed_on"] == "2026-11-20"
    set_clock(client, "2026-11-23", "11:00")
    rep = ok(lab(client, "TP001"), 201)
    assert rep["status"] == "verified"
    c = cbc(client, "TP001")
    # Linked and checked by MRN and test code: Report received. Only the doctor sets Report verified.
    assert c["state"] == "Report received" and c["report_road"] == "hospital" and c["report"]["file_name"]
    item = review(client, "TP001")
    assert item["test_name"] == "Blood test (CBC)" and item["road"] == "hospital" and item["doctor"] == "Dr. Sekhar"
    assert not any(k in item for k in ("value", "result", "summary", "flag"))
    # No message on arrival; the doctor's click comes first.
    assert not any(m["template_id"].startswith("report_continue") for m in sent("TP001"))
    set_clock(client, "2026-11-24", "09:00")
    f = client.get(f"/reports/{rep['report_id']}/file", headers=SEKHAR)
    assert f.status_code == 200 and f.headers["content-type"] == "application/pdf" and f.content.startswith(b"%PDF")
    assert scalar("SELECT count(*) FROM audit_ledger WHERE action = 'report_file_opened' AND actor_name = 'u_sekhar'") == 1
    assert client.post(f"/reviews/{item['review_id']}/decision", headers=SEKHAR, json={}).status_code == 422
    assert client.post(f"/reviews/{item['review_id']}/decision", headers=ANITA,
                       json={"decision": "continue"}).status_code == 403  # the coordinator has neither button
    ok(client.post(f"/reviews/{item['review_id']}/decision", headers=SEKHAR, json={"decision": "continue"}))
    c = cbc(client, "TP001")
    assert (c["state"], c["verified_by"]) == ("Report verified", "u_sekhar")  # the decision verifies the test
    with db() as conn:
        moves = [(a["details"]["from"], a["details"]["to"]) for a in conn.execute(
            "SELECT details FROM audit_ledger WHERE action = 'card_state_changed' AND entity_id = %s ORDER BY id",
            (str(c["card_id"]),)).fetchall()]
    assert moves == [("Due", "Scheduled"), ("Scheduled", "Completed"), ("Completed", "Report received"),
                     ("Report received", "Report verified")]
    msgs = [m for m in sent("TP001") if m["template_id"] == "report_continue_booked"]
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
    assert c["state"] == "Due" and c["patient_says_booked"] is True
    assert task(client, "TP004", "help_booking") is None
    set_clock(client, "2026-11-23", "10:00")
    r = ok(client.post("/simulator/TP004/document", headers=ANITA), 201)
    assert r["status"] == "unverified" and r["identifiers"]["name"] == "Kavita" and r["identifiers"]["age"] == "54"
    assert not set(r["identifiers"]) - {"name", "age", "sex", "lab_id", "facility", "report_date", "document_type"}
    assert cbc(client, "TP004")["state"] == "Due"  # no card moves yet
    assert task(client, "TP004", "outside_report") is not None
    rid = r["report_id"]
    # The doctor cannot see an unverified report.
    assert client.get(f"/reports/{rid}/file", headers=SEKHAR).status_code == 409
    set_clock(client, "2026-11-24", "09:30")
    assert client.post(f"/reports/{rid}/verify", headers=ANITA, json={"matched": ["name"]}).status_code == 422
    ok(client.post(f"/reports/{rid}/verify", headers=ANITA, json={"matched": ["name", "age"]}))
    c = cbc(client, "TP004")
    # Done outside: Due -> Completed -> Report received; the doctor verifies.
    assert c["state"] == "Report received" and c["report_road"] == "outside" and c["patient_says_booked"] is False
    item = review(client, "TP004")
    assert item["road"] == "outside"
    assert scalar("SELECT count(*) FROM audit_ledger WHERE actor_type = 'ai_suggestion'") == 1
    set_clock(client, "2026-11-25", "09:00")
    ok(client.post(f"/reviews/{item['review_id']}/decision", headers=SEKHAR, json={"decision": "continue"}))
    assert cbc(client, "TP004")["state"] == "Report verified"
    assert [m["template_id"] for m in sent("TP004")][-1] == "report_continue_due"
    assert "next visit, due by Mon 14 Dec" in sent("TP004")[-1]["body"]


def test_t2_reject_keeps_card_and_file(uc2t, client):
    r = ok(client.post("/simulator/TP004/document", headers=ANITA), 201)
    assert client.post(f"/reports/{r['report_id']}/reject", headers=ANITA, json={"reason": " "}).status_code == 422
    ok(client.post(f"/reports/{r['report_id']}/reject", headers=ANITA, json={"reason": "Belongs to another patient"}))
    assert cbc(client, "TP004")["state"] == "Due"
    assert scalar("SELECT status FROM test_report WHERE report_id = %s", r["report_id"]) == "rejected"
    assert scalar("SELECT blob_id IS NOT NULL FROM test_report WHERE report_id = %s", r["report_id"]) is True
    assert review(client, "TP004") is None


@pytest.mark.parametrize("to_state, body", [
    ("Report received", {"file_ref": "x.pdf"}),          # hospital report on a Due card
    ("Scheduled", {}),                                    # no date
    ("Completed", {"source": "patient_reply"}),           # a patient message never moves a test
])
def test_t3_illegal_moves_rejected_and_audited(uc2t, client, to_state, body):
    card = cbc(client, "TP005")
    r = client.post(f"/cards/{card['card_id']}/transition", headers=ANITA, json={"to_state": to_state, **body})
    assert r.status_code == 409
    assert cbc(client, "TP005")["state"] == "Due"
    assert scalar("SELECT count(*) FROM audit_ledger WHERE action = 'transition_refused'") == 1


def test_t3_report_needs_file_and_verified_needs_two_identifiers(uc2t, client):
    card = cbc(client, "TP005")
    ok(client.post(f"/cards/{card['card_id']}/transition", headers=ANITA,
                   json={"to_state": "Scheduled", "appointment_date": "2026-11-20"}))
    assert client.post(f"/cards/{card['card_id']}/transition", headers=ANITA,
                       json={"to_state": "Report received"}).status_code == 409
    completed(client, "TP005")
    ok(client.post(f"/cards/{card['card_id']}/transition", headers=ANITA,
                   json={"to_state": "Report received", "file_ref": "r.pdf"}))
    assert client.post(f"/cards/{card['card_id']}/transition", headers=ANITA,
                       json={"to_state": "Report verified", "matched_identifiers": 1}).status_code == 409


def test_t4_booked_tap(uc2t, client):
    to_reminder(client)
    set_clock(client, "2026-11-16", "10:15")
    tap(client, "TP005", "booked")
    c = cbc(client, "TP005")
    assert c["state"] == "Due" and c["patient_says_booked"] is True
    assert scalar("SELECT count(*) FROM coordinator_task WHERE mrn = 'TP005'") == 0
    assert scalar("SELECT status FROM gap_event WHERE card_id = %s", c["card_id"]) == "resolved"
    set_clock(client, "2026-11-17", "07:00")
    gap_check(client)
    reminders(client)
    assert sum(1 for m in sent("TP005") if m["template_id"] == "test_booking_reminder") == 1
    # A date later (the appointment list) replaces the label with Scheduled.
    ok(client.post("/test-cycle/appointment-list", headers=ANITA, json={
        "rows": [{"mrn": "TP005", "test_code": "CBC", "date": "2026-11-20", "time": "09:00"}]}))
    c = cbc(client, "TP005")
    assert c["state"] == "Scheduled" and c["patient_says_booked"] is False and c["booking_source"] == "appointment_list"


def test_t4_record_booking_replaces_label(uc2t, client):
    to_reminder(client)
    tap(client, "TP005", "booked")
    tap(client, "TP005", "help_book")
    t = task(client, "TP005", "help_booking")
    ok(client.post(f"/tasks/{t['task_id']}/result", headers=ANITA, json={
        "result": "booked", "appointment_date": "2026-11-20", "appointment_time": "08:00"}))
    c = cbc(client, "TP005")
    assert c["state"] == "Scheduled" and c["booking_source"] == "coordinator" and c["patient_says_booked"] is False


def test_t5_patient_words_do_not_move_the_card(uc2t, client):
    to_reminder(client)
    tap(client, "TP001", "help_book")
    t = task(client, "TP001", "help_booking")
    ok(client.post(f"/tasks/{t['task_id']}/result", headers=ANITA, json={
        "result": "booked", "appointment_date": "2026-11-20", "appointment_time": "08:00"}))
    r = ok(client.post("/simulator/TP001/reply", headers=ANITA, json={"text": "I have done my test"}), 201)
    assert r["handover_id"] or r["proposal_id"]  # the Use Case 2a path: a person sees it
    assert cbc(client, "TP001")["state"] == "Scheduled"
    completed(client, "TP001")
    assert cbc(client, "TP001")["state"] == "Completed"


def test_t6_report_cannot_be_linked(uc2t, client):
    card = cbc(client, "TP006")
    ok(client.post(f"/cards/{card['card_id']}/transition", headers=ANITA,
                   json={"to_state": "Scheduled", "appointment_date": "2026-11-20"}))
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
    assert cbc(client, "TP006")["state"] == "Report received"


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
    assert sum(1 for m in sent("TP001") if m["template_id"] == "test_booking_reminder") == 1
    reminder = next(m for m in sent("TP001") if m["template_id"] == "test_booking_reminder")
    for _ in range(2):
        client.post("/simulator/TP001/reply", headers=ANITA,
                    json={"button": "help_book", "in_reply_to": reminder["message_id"], "event_id": "tap-1"})
    assert scalar("SELECT count(*) FROM coordinator_task WHERE mrn = 'TP001' AND task_type = 'help_booking'") == 1


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
            ("review_item_created", "system"), ("report_file_opened", "person"), ("card_state_changed", "person"),
            ("report_review_decided", "person"), ("message_sent", "agent")]
    it = iter(got)
    missing = [w for w in want if w not in it]
    assert not missing, (missing, got)
    labels = [e["label"] for e in ok(client.get("/patients/TP001/timeline", headers=SEKHAR))["events"]]
    assert any("Lab report for Blood test (CBC) linked and checked" in l for l in labels)
    assert any("Blood test (CBC): Report verified by Dr. Sekhar" in l for l in labels)
    assert any("Doctor's decision on the Blood test (CBC) report: Continue as planned" in l for l in labels)
