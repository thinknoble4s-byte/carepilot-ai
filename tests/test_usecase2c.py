"""Use Case 2c — the doctor picks Urgent: "Call the patient today", the call log and the cover, v1.2 (usecase-docs/).
UC2c-T1 to T10 and the audit check.

Seed "after Use Case 2a, step 6": TP001, TP004, TP005, TP007, TP006, each with voice note HI-01 (flag yes from the
Use Case 2a run, no AI call), the acknowledgement sent, the send block on, the Review visit Booked (patient-reported)
and the handover sent to Dr. Sekhar on Mon 9 Nov 11:00. The demo clock starts at Tue 10 Nov 09:30.
"""
from conftest import scalar, users_header

ANITA, RAVI, SEKHAR, SURESH = (users_header(u) for u in ("u_anita", "u_ravi", "u_sekhar", "u_suresh"))


def db():
    from app import db as _db

    return _db.connect()


def handover_id(mrn):
    return scalar("SELECT handover_id FROM handover WHERE mrn = %s", mrn)


def decide(client, mrn, decision="urgent"):
    r = client.post(f"/handovers/{handover_id(mrn)}/gate2", headers=SEKHAR, json={"decision": decision})
    assert r.status_code == 200, r.text
    return r.json()


def instruct(client, mrn, text="See within 2 days", days=2):
    r = client.post(f"/handovers/{handover_id(mrn)}/instruction", headers=SEKHAR, json={"text": text, "days": days})
    assert r.status_code == 200, r.text
    return r.json()


def set_clock(client, day, at):
    r = client.post("/clock/set", headers=ANITA, json={"date": day, "time": at})
    assert r.status_code == 200, r.text
    return r.json()


def call_task(client, mrn, who=ANITA):
    r = client.get(f"/patients/{mrn}/tasks", headers=who)
    assert r.status_code == 200, r.text
    calls = [t for t in r.json()["tasks"] if t["task_type"] == "urgent_call"]
    return calls[0] if calls else None


def attempt(client, task_id, result, who=ANITA, **body):
    return client.post(f"/tasks/{task_id}/attempts", headers=who, json={"result": result, **body})


def cards(client, mrn):
    return {c["name"]: c for c in client.get(f"/patients/{mrn}/cards", headers=ANITA).json()}


def sent(mrn):
    with db() as c:
        return [m["template_id"] for m in c.execute(
            "SELECT template_id FROM outbox_message WHERE mrn = %s AND status = 'sent' ORDER BY message_id", (mrn,)).fetchall()]


def worklist(client, who=ANITA):
    return client.get("/worklist", headers=who).json()


def row(client, mrn, who=ANITA):
    return next(p for p in worklist(client, who)["patients"] if p["mrn"] == mrn)


def close(client, mrn, reason="Patient reached, visit booked"):
    return client.post(f"/handovers/{handover_id(mrn)}/close", headers=ANITA, json={"reason": reason})


def queue_line(client, mrn):
    hid = handover_id(mrn)
    items = client.get("/doctor/queue", headers=SEKHAR).json() + client.get("/decisions/recent", headers=SEKHAR).json()
    return next(i["call_line"] for i in items if i["handover_id"] == hid)


def actions(mrn, since=None):
    with db() as c:
        return c.execute("SELECT id, action, actor_type, reason_code, outcome, demo_time, details FROM audit_ledger "
                         "WHERE mrn = %s AND id > %s ORDER BY id", (mrn, since or 0)).fetchall()


def test_seed(uc2c, client):
    assert uc2c["scenario"] == "uc2c" and uc2c["patients"] == 5
    assert client.get("/clock", headers=ANITA).json() == {"today": "2026-11-10", "time": "09:30"}
    queue = client.get("/doctor/queue", headers=SEKHAR).json()
    assert sorted(q["mrn"] for q in queue) == ["TP001", "TP004", "TP005", "TP006", "TP007"]
    assert all(q["stage"] == "awaiting_doctor" and q["call_line"] is None and q["inbound_kind"] == "voice" for q in queue)
    for mrn in ("TP001", "TP004"):
        assert cards(client, mrn)["Review visit"]["booking_source"] == "patient_button"
        assert sent(mrn) == ["booking_reminder", "handover_ack"]
    # Section 6: the AI is not called again — the seed holds the flag and the transcript.
    assert scalar("SELECT count(*) FROM audit_ledger WHERE actor_type = 'ai_suggestion'") == 0
    assert scalar("SELECT count(*) FROM inbound_message WHERE ai_flag = 'yes' AND kind = 'voice'") == 5


def test_t1_urgent_creates_one_task_at_once(uc2c, client):
    decide(client, "TP001")
    t = call_task(client, "TP001")
    assert t["owner_name"] == "Anita" and t["cover_name"] == "Ravi" and t["state"] == "Open"
    assert t["owner_deadline_at"].endswith("16:00:00") and t["cover_deadline_at"].endswith("18:00:00")
    assert t["created_at_demo"] == "2026-11-10T09:30:00" and t["label"] == "Call the patient today"
    # REQ-02: until the instruction is saved the task shows none; the words, translation and audio are there.
    assert t["handover"]["instruction"] is None and t["handover"]["inbound_kind"] == "voice"
    assert "arm" in t["handover"]["translation"] and t["handover"]["gate2_by"] == "u_sekhar"
    assert scalar("SELECT reason_code FROM audit_ledger WHERE action = 'task_created' AND mrn = 'TP001' "
                  "ORDER BY id DESC LIMIT 1") == "urgent_call_today"
    # REQ-04: at the top of Needs you with the Urgent tag, above every other patient.
    wl = worklist(client)
    assert wl["patients"][0]["mrn"] == "TP001"
    top = wl["patients"][0]["reasons"][0]
    assert top["action"] and "urgent" in top["marks"] and "by 16:00" in top["txt"]
    # Nothing sent about the problem; no AI call.
    assert sent("TP001") == ["booking_reminder", "handover_ack"]
    assert scalar("SELECT count(*) FROM audit_ledger WHERE actor_type = 'ai_suggestion'") == 0
    # REQ-05: a second Submit creates nothing new.
    assert client.post(f"/handovers/{handover_id('TP001')}/gate2", headers=SEKHAR, json={"decision": "urgent"}).status_code == 409
    assert scalar("SELECT count(*) FROM coordinator_task WHERE mrn = 'TP001' AND task_type = 'urgent_call'") == 1
    # Decision saved once with name and demo time.
    assert scalar("SELECT gate2_at_demo::text FROM handover WHERE mrn = 'TP001'") == "2026-11-10 09:30:00"
    # The instruction appears on the task once saved; the card and the booking message follow (REQ-15).
    set_clock(client, "2026-11-10", "09:35")
    instruct(client, "TP001")
    t = call_task(client, "TP001")
    assert t["handover"]["instruction"] == "See within 2 days"
    card = cards(client, "TP001")["Doctor-requested visit"]
    assert card["state"] == "Due" and card["due_date"] == "2026-11-12" and card["owner_name"] == "Anita"
    assert t["card_id"] == card["card_id"]
    assert sent("TP001") == ["booking_reminder", "handover_ack", "instruction_booking"]


def test_t2_reached_same_morning_with_booking(uc2c, client):
    decide(client, "TP001")
    set_clock(client, "2026-11-10", "09:35")
    instruct(client, "TP001")
    set_clock(client, "2026-11-10", "10:20")
    t = call_task(client, "TP001")
    # Reached cannot be saved without a note.
    assert attempt(client, t["task_id"], "reached").status_code == 422
    assert scalar("SELECT count(*) FROM task_call_attempt") == 0
    r = attempt(client, t["task_id"], "reached", note="Told patient to come within 2 days; she agreed",
                appointment_date="2026-11-12", appointment_time="09:30")
    assert r.status_code == 201, r.text
    assert r.json()["minutes_to_call"] == 50
    t = call_task(client, "TP001")
    assert t["state"] == "Done" and t["done_by"] == "u_anita" and t["minutes_to_call"] == 50
    c = cards(client, "TP001")
    assert c["Doctor-requested visit"]["state"] == "Booked" and c["Doctor-requested visit"]["booking_source"] == "coordinator"
    assert c["Doctor-requested visit"]["appointment_date"] == "2026-11-12"
    assert c["Doctor-requested visit"]["appointment_time"] == "09:30:00"
    assert c["Review visit"]["booking_source"] == "patient_button"  # the first card is not touched
    # REQ-19: no confirmation message.
    assert sent("TP001") == ["booking_reminder", "handover_ack", "instruction_booking"]
    # REQ-17: the doctor sees the call, read-only.
    assert queue_line(client, "TP001") == "Reached by Anita at 10:20"
    # REQ-16: the hold ends with the task.
    assert client.get("/patients/TP001/tasks", headers=ANITA).json()["hold"] is False
    # REQ-13: close the handover now; send block off.
    assert close(client, "TP001").status_code == 200
    assert scalar("SELECT stage FROM handover WHERE mrn = 'TP001'") == "closed"


def test_t2_reached_without_booking_keeps_card_due(uc2c, client):
    decide(client, "TP001")
    instruct(client, "TP001")
    t = call_task(client, "TP001")
    assert attempt(client, t["task_id"], "reached", note="Passed on the instruction").status_code == 201
    assert cards(client, "TP001")["Doctor-requested visit"]["state"] == "Due"
    assert call_task(client, "TP001")["state"] == "Done"


def test_record_booking_needs_the_instruction_card(uc2c, client):
    decide(client, "TP001")
    t = call_task(client, "TP001")
    r = attempt(client, t["task_id"], "reached", note="Passed on", appointment_date="2026-11-12", appointment_time="09:30")
    assert r.status_code == 422 and "instruction" in r.json()["detail"]
    assert call_task(client, "TP001")["state"] == "Open"


def test_t3_no_answer_then_system_cover_step_in(uc2c, client):
    decide(client, "TP004")
    set_clock(client, "2026-11-10", "09:35")
    instruct(client, "TP004")
    set_clock(client, "2026-11-10", "10:30")
    t = call_task(client, "TP004")
    assert attempt(client, t["task_id"], "no_answer").status_code == 201
    t = call_task(client, "TP004")
    assert t["state"] == "Open" and len(t["attempts"]) == 1
    # There is no "Not reached" result for an urgent task.
    assert client.post(f"/tasks/{t['task_id']}/result", headers=ANITA,
                       json={"result": "not_reached", "note": "x"}).status_code == 422
    assert client.post(f"/tasks/{t['task_id']}/done", headers=ANITA, json={"note": "x"}).status_code == 422
    set_clock(client, "2026-11-10", "15:59")
    assert call_task(client, "TP004")["state"] == "Open"
    before = scalar("SELECT max(id) FROM audit_ledger")
    r = set_clock(client, "2026-11-10", "16:00")
    assert any(m["mrn"] == "TP004" and m["action"] == "moved_to_cover" for m in r["urgent"])
    t = call_task(client, "TP004")
    assert t["state"] == "Moved to cover" and t["owner_name"] == "Ravi" and t["moved_from"] == "Anita"
    assert len(t["attempts"]) == 1 and t["cover_deadline_at"].endswith("18:00:00")
    moved = [a for a in actions("TP004", before) if a["action"] == "task_moved_to_cover"]
    assert len(moved) == 1 and moved[0]["actor_type"] == "system" and moved[0]["reason_code"] == "urgent_moved_to_cover"
    assert moved[0]["details"]["reason"] == "No reached call logged by 16:00"
    assert sent("TP004") == ["booking_reminder", "handover_ack", "instruction_booking"]
    # Once only.
    set_clock(client, "2026-11-10", "16:10")
    assert scalar("SELECT count(*) FROM audit_ledger WHERE action = 'task_moved_to_cover' AND mrn = 'TP004'") == 1
    # REQ-11: Ravi sees it at the top as his action; Anita sees "Moved to Ravi", out of her action count.
    ravi = row(client, "TP004", RAVI)
    assert ravi["reasons"][0]["action"] and "moved to cover, from Anita" in ravi["reasons"][0]["txt"]
    assert worklist(client, RAVI)["patients"][0]["mrn"] in {"TP001", "TP004", "TP005", "TP007"}
    assert "Ravi" in ravi["assignees"]
    anita = row(client, "TP004", ANITA)
    assert "moved to Ravi" in anita["reasons"][0]["txt"] and not anita["reasons"][0]["action"]
    assert anita["main"] != "action"
    # Ravi reaches the patient at 16:20: 410 minutes; card stays Due.
    set_clock(client, "2026-11-10", "16:20")
    r = attempt(client, t["task_id"], "reached", who=RAVI, note="Passed on the doctor's instruction; patient will book herself")
    assert r.status_code == 201 and r.json()["minutes_to_call"] == 410
    t = call_task(client, "TP004")
    assert t["state"] == "Done" and t["done_by"] == "u_ravi"
    assert [a["called_by"] for a in t["attempts"]] == ["u_anita", "u_ravi"]
    assert cards(client, "TP004")["Doctor-requested visit"]["state"] == "Due"
    assert queue_line(client, "TP004") == "Reached by Ravi at 16:20"


def test_t4_handover_cannot_close_early(uc2c, client):
    decide(client, "TP004")
    instruct(client, "TP004")
    r = close(client, "TP004", "Patient reached by cover")
    assert r.status_code == 409 and "Call task still open" in r.json()["detail"]
    assert scalar("SELECT stage FROM handover WHERE mrn = 'TP004'") == "ready_to_close"
    t = call_task(client, "TP004")
    attempt(client, t["task_id"], "reached", note="Passed on")
    assert close(client, "TP004", "Patient reached by cover").status_code == 200
    assert client.get("/patients/TP004", headers=ANITA).json()["send_block"] is False


def test_t5_pass_to_cover_by_hand(uc2c, client):
    decide(client, "TP005")
    instruct(client, "TP005")
    t = call_task(client, "TP005")
    set_clock(client, "2026-11-10", "11:00")
    attempt(client, t["task_id"], "no_answer")
    set_clock(client, "2026-11-10", "14:00")
    attempt(client, t["task_id"], "no_answer")
    assert client.post(f"/tasks/{t['task_id']}/pass-to-cover", headers=ANITA, json={"reason": " "}).status_code == 422
    r = client.post(f"/tasks/{t['task_id']}/pass-to-cover", headers=ANITA, json={"reason": "cannot reach after 2 tries"})
    assert r.status_code == 200, r.text
    t = call_task(client, "TP005")
    assert t["state"] == "Moved to cover" and t["owner_name"] == "Ravi" and len(t["attempts"]) == 2
    e = scalar("SELECT row_to_json(a)::text FROM audit_ledger a WHERE action = 'task_moved_to_cover' AND mrn = 'TP005'")
    assert '"actor_type":"person"' in e and '"actor_name":"u_anita"' in e and "cannot reach after 2 tries" in e
    assert client.post(f"/tasks/{t['task_id']}/pass-to-cover", headers=ANITA, json={"reason": "again"}).status_code == 409
    set_clock(client, "2026-11-10", "16:00")
    assert scalar("SELECT count(*) FROM audit_ledger WHERE action = 'task_moved_to_cover' AND mrn = 'TP005'") == 1


def test_t6_nobody_reaches_the_patient(uc2c, client):
    decide(client, "TP007")
    instruct(client, "TP007")
    set_clock(client, "2026-11-10", "16:00")
    assert call_task(client, "TP007")["state"] == "Moved to cover"
    set_clock(client, "2026-11-10", "18:01")
    t = call_task(client, "TP007")
    assert t["state"] == "Overdue" and t["status"] == "open" and t["owner_name"] == "Ravi"
    assert scalar("SELECT count(*) FROM audit_ledger WHERE action = 'task_moved_to_cover' AND mrn = 'TP007'") == 1
    od = scalar("SELECT details->>'text' FROM audit_ledger WHERE action = 'task_overdue' AND mrn = 'TP007'")
    assert od == "Overdue after 18:00"
    r = row(client, "TP007", RAVI)
    assert "urgent" in r["reasons"][0]["marks"] and "overdue" in r["reasons"][0]["marks"]
    assert close(client, "TP007", "x").status_code == 409
    assert client.get("/patients/TP007", headers=ANITA).json()["send_block"] is True
    # Next day: still Overdue, never closed or moved by itself.
    set_clock(client, "2026-11-11", "09:00")
    assert call_task(client, "TP007")["state"] == "Overdue"
    assert scalar("SELECT count(*) FROM audit_ledger WHERE action = 'task_overdue' AND mrn = 'TP007'") == 1


def test_jump_straight_past_both_deadlines(uc2c, client):
    decide(client, "TP007")
    set_clock(client, "2026-11-10", "18:30")
    t = call_task(client, "TP007")
    assert t["state"] == "Overdue" and t["owner_name"] == "Ravi" and t["moved_from"] == "Anita"


def test_t7_routine_creates_no_call_task(uc2c, client):
    decide(client, "TP006", "routine")
    assert call_task(client, "TP006") is None
    instruct(client, "TP006", "Review visit within 7 days", 7)
    assert cards(client, "TP006")["Doctor-requested visit"]["due_date"] == "2026-11-17"
    assert close(client, "TP006", "Routine visit to be booked").status_code == 200
    assert queue_line(client, "TP006") is None


def test_t8_roles_and_hold(uc2c, client):
    hid = handover_id("TP004")
    for who in (ANITA, RAVI):
        assert client.post(f"/handovers/{hid}/gate2", headers=who, json={"decision": "routine"}).status_code == 403
    decide(client, "TP004")
    assert client.get("/patients/TP004/tasks", headers=ANITA).json()["hold"] is True
    # The doctor cannot log calls or pass the task.
    t = call_task(client, "TP004", SEKHAR)
    assert attempt(client, t["task_id"], "no_answer", who=SEKHAR).status_code == 403
    instruct(client, "TP004")
    # The booking message passed the send block (and the hold).
    assert "instruction_booking" in sent("TP004")
    # A routine reminder is held while the task is open.
    from app import messaging

    with db() as c:
        p = c.execute("SELECT * FROM patient WHERE mrn = 'TP004'").fetchone()
        assert messaging.task_hold(c, "TP004")
        assert messaging.guard(c, p, "booking_reminder", "patient", "agent")[0] in ("held_handover_open", "held_open_task")
    attempt(client, t["task_id"], "reached", note="Passed on")
    assert client.get("/patients/TP004/tasks", headers=ANITA).json()["hold"] is False


def test_t9_booking_message_once_and_no_confirmation(uc2c, client):
    decide(client, "TP001")
    assert "instruction_booking" not in sent("TP001")  # no instruction, no card, no message
    instruct(client, "TP001")
    with db() as c:
        m = c.execute("SELECT * FROM outbox_message WHERE mrn = 'TP001' AND template_id = 'instruction_booking'").fetchall()
    assert len(m) == 1 and m[0]["status"] == "sent" and len(m[0]["buttons"]) == 3
    assert "Dr. Sekhar has asked you to come for a visit by 12-Nov" in m[0]["body"]
    t = call_task(client, "TP001")
    attempt(client, t["task_id"], "reached", note="Booked", appointment_date="2026-11-12", appointment_time="09:30")
    assert sent("TP001").count("instruction_booking") == 1 and len(sent("TP001")) == 3


def test_t10_message_with_no_health_matter(uc2c, client):
    # TP006 after the Routine path and the closed handover: clip HI-02 is flag no — no doctor, no call task.
    decide(client, "TP006", "routine")
    instruct(client, "TP006", "Review visit within 7 days", 7)
    close(client, "TP006", "Routine visit to be booked")
    r = client.post("/simulator/TP006/reply", headers=ANITA, json={"clip_id": "HI-02"})
    assert r.status_code == 201, r.text
    assert r.json()["handover_id"] is None and r.json()["proposal_id"] is not None
    assert scalar("SELECT count(*) FROM handover WHERE mrn = 'TP006' AND stage <> 'closed'") == 0
    assert call_task(client, "TP006") is None


def test_audit_check_positive_case(uc2c, client):
    start = scalar("SELECT max(id) FROM audit_ledger")
    decide(client, "TP001")
    set_clock(client, "2026-11-10", "09:35")
    instruct(client, "TP001")
    set_clock(client, "2026-11-10", "10:20")
    t = call_task(client, "TP001")
    attempt(client, t["task_id"], "reached", note="Told patient", appointment_date="2026-11-12", appointment_time="09:30")
    close(client, "TP001")
    got = [(a["action"], a["actor_type"]) for a in actions("TP001", start)]
    want = [("gate2_decided", "person"), ("task_created", "system"), ("instruction_recorded", "person"),
            ("card_created", "system"), ("message_sent", "agent"), ("call_attempt_logged", "person"),
            ("booking_recorded", "person"), ("card_state_changed", "system"), ("task_done", "system"),
            ("handover_closed", "person")]
    it = iter(got)
    assert all(w in it for w in want), got  # in this order (other entries may sit between)
    done = next(a for a in actions("TP001", start) if a["action"] == "task_done" and a["details"].get("task_type") == "urgent_call")
    assert done["details"]["minutes_to_call"] == 50 and done["details"]["hold"] == "ended"
    assert str(done["demo_time"]).startswith("10:20")


def test_chief_sees_combined_time_to_call(uc2c, client):
    decide(client, "TP001")
    set_clock(client, "2026-11-10", "10:20")
    attempt(client, call_task(client, "TP001")["task_id"], "reached", note="Passed on")
    k = client.get("/kpis", headers=SURESH).json()
    assert k["urgent_calls_reached"] == 1 and k["time_to_call_median_minutes"] == 50


def test_journey_shows_the_urgent_call(uc2c, client):
    decide(client, "TP004")
    set_clock(client, "2026-11-10", "10:30")
    attempt(client, call_task(client, "TP004")["task_id"], "no_answer")
    set_clock(client, "2026-11-10", "16:20")
    attempt(client, call_task(client, "TP004")["task_id"], "reached", who=RAVI, note="Passed on")
    labels = [e["label"] for e in client.get("/patients/TP004/timeline", headers=SEKHAR).json()["events"]]
    assert any(l.startswith("Urgent — call the patient today: Anita by 16:00") for l in labels)
    assert any(l.startswith("Urgent call moved to Ravi (cover), from Anita at 16:00") for l in labels)
    assert any(l.startswith("Patient reached by Ravi at 16:20: time to call 410 minutes") for l in labels)
