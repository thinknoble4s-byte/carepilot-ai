"""Use Case 2b — "Please help me book", "I can't come" and HELP, v1.2 (usecase-docs/): UC2b-T1 to T9.

Seed "after Use Case 1": TP001, TP004, TP005, TP006, TP007, English, "Review visit" Due 16 Nov, T-7 reminder sent
at 07:30 on Mon 9 Nov, demo clock 10:15. No AI on the tap and keyword paths; text and voice use the Use Case 2a
health check (a deterministic fake Sarvam in tests).
"""
import pytest

from conftest import scalar, users_header

ANITA = users_header("u_anita")


def db():
    from app import db as _db

    return _db.connect()


def reminder(mrn, template="booking_reminder"):
    with db() as c:
        return c.execute("SELECT * FROM outbox_message WHERE mrn = %s AND template_id = %s ORDER BY message_id DESC",
                         (mrn, template)).fetchone()


def reply(client, mrn, **body):
    body.setdefault("in_reply_to", reminder(mrn)["message_id"])
    r = client.post(f"/simulator/{mrn}/reply", headers=ANITA, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def tasks(client, mrn):
    r = client.get(f"/patients/{mrn}/tasks", headers=ANITA)
    assert r.status_code == 200, r.text
    return r.json()


def help_tasks(client, mrn, task_type=None):
    return [t for t in tasks(client, mrn)["tasks"] if t["help_task"] and (task_type is None or t["task_type"] == task_type)]


def rv(client, mrn):
    return next(c for c in client.get(f"/patients/{mrn}/cards", headers=ANITA).json() if c["name"] == "Review visit")


def sent(mrn):
    with db() as c:
        return [m["template_id"] for m in c.execute(
            "SELECT template_id FROM outbox_message WHERE mrn = %s AND status = 'sent' ORDER BY message_id", (mrn,)).fetchall()]


def result(client, task_id, **body):
    return client.post(f"/tasks/{task_id}/result", headers=ANITA, json=body)


def set_clock(client, day, at):
    assert client.post("/clock/set", headers=ANITA, json={"date": day, "time": at}).status_code == 200


def ai_entries(mrn):
    return scalar("SELECT count(*) FROM audit_ledger WHERE actor_type = 'ai_suggestion' AND mrn = %s", mrn)


@pytest.mark.parametrize("text,match", [
    ("help", True), (" help ", True), ("HELP!", True), ("ಸಹಾಯ.", True), ("मदद।", True), ("Help?", True),
    ("help me please", False), ("this doesn't help my pain", False), ("", False), (None, False), ("helpful", False)])
def test_keyword_is_an_exact_whole_message(text, match):
    from app.help_tasks import is_keyword

    assert is_keyword(text, ["help", "ಸಹಾಯ", "मदद"]) is match


def test_seed(uc2b, client):
    assert uc2b["scenario"] == "uc2b" and uc2b["patients"] == 5
    for mrn in ("TP001", "TP004", "TP005", "TP006", "TP007"):
        assert reminder(mrn)["status"] == "sent" and rv(client, mrn)["state"] == "Due"
    assert client.get("/demo/settings", headers=ANITA).json()["time"] == "10:15"


def test_uc2b_t1_help_booking(uc2b, client):
    """5 Nov: Please help me book offers four slots; the slot she taps books the visit and she is told; no task."""
    r = reply(client, "TP001", button="help_book")
    assert r["handling"] == ["button_help_book:slots_offered"]
    assert rv(client, "TP001")["state"] == "Due" and ai_entries("TP001") == 0
    assert help_tasks(client, "TP001") == [] and tasks(client, "TP001")["hold"] is False
    assert sent("TP001") == ["booking_reminder", "booking_slots"]
    offer = reminder("TP001", "booking_slots")
    slot = offer["buttons"][0]
    r = reply(client, "TP001", button="slot_1", in_reply_to=offer["message_id"])
    assert r["handling"] == ["button_slot_1:card_booked_slot"]
    card = rv(client, "TP001")
    assert (card["state"], card["booking_source"], card["appointment_date"], card["appointment_time"]) == \
        ("Booked", "patient_slot", slot["date"], f"{slot['time']}:00")
    assert sent("TP001") == ["booking_reminder", "booking_slots", "booking_confirmed"]
    # A later patient tap does not move it again.
    assert reply(client, "TP001", button="booked")["handling"] == ["button_booked:already_progressed"]


def test_uc2b_t2_cant_come(uc2b, client):
    r = reply(client, "TP004", button="cant_come")
    assert r["handling"] == ["button_cant_come:cant_come_created"]
    [t] = help_tasks(client, "TP004", "cant_come")
    assert (t["due_date"], rv(client, "TP004")["state"]) == ("2026-11-11", "Due")
    assert result(client, t["task_id"], result="reason_recorded").status_code == 422
    assert result(client, t["task_id"], result="reason_recorded", reason_code="money").status_code == 422
    ok = result(client, t["task_id"], result="reason_recorded", reason_code="cost", note="Worried about treatment cost")
    assert ok.status_code == 200 and ok.json()["result_code"] == "cost"
    assert rv(client, "TP004")["state"] == "Due" and tasks(client, "TP004")["hold"] is False
    # A new tap later: Rebooked needs a date and makes the card Booked by the coordinator.
    reply(client, "TP004", button="cant_come")
    t2 = next(t for t in help_tasks(client, "TP004", "cant_come") if t["state"] == "Open")
    assert result(client, t2["task_id"], result="rebooked").status_code == 422
    assert result(client, t2["task_id"], result="rebooked", appointment_date="2026-11-20").status_code == 200
    card = rv(client, "TP004")
    assert (card["state"], card["booking_source"], card["appointment_date"]) == ("Booked", "coordinator", "2026-11-20")


def test_uc2b_t3_help_keyword(uc2b, client):
    r = reply(client, "TP005", text=" help ")
    assert r["handling"] == ["help_request_created:help_keyword"] and r["handover_id"] is None
    [t] = help_tasks(client, "TP005", "help_request")
    assert (t["owner_name"], t["cover_name"], t["due_date"], t["card_name"]) == ("Anita", "Ravi", "2026-11-10", "Review visit")
    assert result(client, t["task_id"], result="sorted").status_code == 422  # a note is required
    assert result(client, t["task_id"], result="sorted", note="Asked where to park; answered").status_code == 200
    for word in ("HELP!", "ಸಹಾಯ.", "मदद।"):
        r = reply(client, "TP005", text=word)
        assert r["handling"] == ["help_request_created:help_keyword"], word
        t = next(t for t in help_tasks(client, "TP005", "help_request") if t["state"] == "Open")
        assert result(client, t["task_id"], result="sorted", note=f"Called back after {word}").status_code == 200
    assert len(help_tasks(client, "TP005", "help_request")) == 4
    assert ai_entries("TP005") == 0
    # Agent switched off: the keyword is still processed and nothing is sent (REQ-05).
    client.post("/patients/TP005/status", headers=ANITA, json={"status": "Agent off", "reason": "Test"})
    assert reply(client, "TP005", text="HELP")["handling"] == ["help_request_created:help_keyword"]
    assert sent("TP005") == ["booking_reminder"]
    assert scalar("SELECT count(*) FROM audit_ledger WHERE action = 'keyword_matched' AND mrn = 'TP005'") == 5


def test_uc2b_t4_negative_case(uc2b, client):
    r = reply(client, "TP006", text="this doesn't help my pain")
    assert r["handling"] == ["ai_flag_yes", "handover_opened:health_flag_handover"]
    assert help_tasks(client, "TP006") == []
    assert sent("TP006") == ["booking_reminder", "handover_ack"]
    row = next(p for p in client.get("/worklist", headers=ANITA).json()["patients"] if p["mrn"] == "TP006")
    assert "health_mention" in row["reasons"][0]["marks"]
    r = reply(client, "TP007", text="help me please")
    assert not any("help_request" in h for h in r["handling"]) and help_tasks(client, "TP007") == []


def test_uc2b_t5_hold(uc2b, client):
    from app import agent, messaging

    reply(client, "TP001", button="cant_come")
    with db() as c:
        m = messaging.send_message(c, mrn="TP001", template_id="booking_reminder", actor_type="person",
                                   actor="u_anita", reason_code="test")
        assert (m["status"], m["blocked_reason"]) == ("blocked", "held_open_task")
        assert agent.select_action(c, {"mrn": "TP001", "rule": "G0"})["reason_code"] == "held_open_task"
    assert scalar("SELECT count(*) FROM audit_ledger WHERE action = 'message_blocked' AND reason_code = 'held_open_task'") == 1
    [t] = help_tasks(client, "TP001")
    result(client, t["task_id"], result="reason_recorded", reason_code="transport", note="Needs transport")
    assert rv(client, "TP001")["state"] == "Due"
    with db() as c:
        m = messaging.send_message(c, mrn="TP001", template_id="booking_reminder", actor_type="person",
                                   actor="u_anita", reason_code="test")
    assert m["blocked_reason"] != "held_open_task"


def test_uc2b_t6_no_duplicates(uc2b, client):
    first = reply(client, "TP001", button="cant_come", event_id="wa-1")
    second = reply(client, "TP001", button="cant_come", event_id="wa-2")
    replay = reply(client, "TP001", button="cant_come", event_id="wa-2")
    assert first["handling"] == ["button_cant_come:cant_come_created"]
    assert second["handling"] == ["button_cant_come:cant_come_already_open"]
    assert replay["handling"] == ["duplicate_ignored"]
    assert len(help_tasks(client, "TP001")) == 1
    assert scalar("SELECT count(*) FROM inbound_message WHERE mrn = 'TP001'") == 2
    assert scalar("SELECT count(*) FROM audit_ledger WHERE action = 'duplicate_suppressed' AND entity_type = 'task'") == 1


def test_uc2b_t7_overdue(uc2b, client):
    reply(client, "TP004", button="cant_come")
    set_clock(client, "2026-11-12", "09:00")
    client.post("/deadlines/run", headers=ANITA)  # Use Case 3 moves tasks to the cover; not in this data set
    [t] = help_tasks(client, "TP004")
    assert (t["state"], t["owner_name"], t["cover_name"], t["assigned_name"]) == ("Overdue", "Anita", "Ravi", "Anita")
    assert tasks(client, "TP004")["hold"] is True
    row = next(p for p in client.get("/worklist", headers=ANITA).json()["patients"] if p["mrn"] == "TP004")
    assert any("overdue" in x["marks"] for x in row["reasons"])


def test_uc2b_t8_not_reached(uc2b, client):
    reply(client, "TP007", button="cant_come")
    [t] = help_tasks(client, "TP007")
    tid = t["task_id"]
    assert result(client, tid, result="not_reached", note="No answer").status_code == 422
    set_clock(client, "2026-11-10", "10:30")
    assert client.post(f"/tasks/{tid}/attempts", headers=ANITA, json={"result": "no_answer"}).status_code == 201
    assert result(client, tid, result="not_reached", note="No answer").status_code == 422
    set_clock(client, "2026-11-10", "15:00")
    client.post(f"/tasks/{tid}/attempts", headers=ANITA, json={"result": "no_answer"})
    assert result(client, tid, result="not_reached").status_code == 422  # note required
    assert result(client, tid, result="not_reached", note="Two calls, no answer").status_code == 200
    [t] = help_tasks(client, "TP007")
    assert (t["state"], t["result"], len(t["attempts"])) == ("Done", "not_reached", 2)
    assert [a["called_at_demo"][11:16] for a in t["attempts"]] == ["10:30", "15:00"]
    assert rv(client, "TP007")["state"] == "Due" and tasks(client, "TP007")["hold"] is False
    assert sent("TP007") == ["booking_reminder"]


def test_uc2b_t9_voice_note_is_not_a_keyword(uc2b, client):
    r = reply(client, "TP005", clip_id="HI-01")
    assert help_tasks(client, "TP005") == [] and r["handling"][0].startswith("ai_flag_")


def test_help_task_closes_only_with_a_result(uc2b, client):
    reply(client, "TP001", button="cant_come")
    [t] = help_tasks(client, "TP001")
    assert client.post(f"/tasks/{t['task_id']}/done", headers=ANITA, json={"note": "done"}).status_code == 422


def test_audit_order_help_booking(uc2b, client):
    reply(client, "TP001", button="help_book")
    offer = reminder("TP001", "booking_slots")
    reply(client, "TP001", button="slot_2", in_reply_to=offer["message_id"])
    with db() as c:
        rows = c.execute("""SELECT action, actor_type, reason_code, demo_time FROM audit_ledger WHERE mrn = 'TP001'
                            AND id > (SELECT max(id) FROM audit_ledger WHERE action = 'reminder_run') ORDER BY id""").fetchall()
    assert [(r["action"], r["actor_type"]) for r in rows] == [
        ("reply_received", "system"), ("message_sent", "agent"), ("agent_decision", "agent"),
        ("reply_received", "system"), ("card_state_changed", "system"), ("appointment_booked", "system"),
        ("message_sent", "agent")]
    assert rows[2]["reason_code"] == "patient_asked_help_booking" and all(r["demo_time"] for r in rows)


def test_audit_order_help_keyword_has_no_ai(uc2b, client):
    reply(client, "TP005", text="help")
    with db() as c:
        rows = c.execute("""SELECT action, actor_type, reason_code FROM audit_ledger WHERE mrn = 'TP005'
                            AND id > (SELECT max(id) FROM audit_ledger WHERE action = 'reminder_run') ORDER BY id""").fetchall()
    assert [(r["action"], r["actor_type"]) for r in rows] == [
        ("reply_received", "system"), ("keyword_matched", "system"), ("agent_decision", "agent"), ("task_created", "system")]
    assert {r["reason_code"] for r in rows[1:]} == {"help_keyword"}
