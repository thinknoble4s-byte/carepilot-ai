"""Use Case 2a — Patient replies "Booked" and sends a Hindi voice note, v1.2 (usecase-docs/): UC2a-T1 to T6.

Seed "after Use Case 1": TP001 (positive case, clip HI-01) and TP004 (negative case, clip HI-02), both English,
"Review visit" Due 16 Nov, T-7 reminder sent at 07:30 on Mon 9 Nov; demo clock 10:15. Sarvam is replaced by a
deterministic fake in tests (app/ai.py), so tests check the flag, transcript, translation and audio, not words.
"""
from conftest import scalar, users_header

ANITA, SEKHAR = users_header("u_anita"), users_header("u_sekhar")


def db():
    from app import db as _db

    return _db.connect()


def reminder(mrn):
    with db() as c:
        return c.execute("SELECT * FROM outbox_message WHERE mrn = %s AND template_id = 'booking_reminder'",
                         (mrn,)).fetchone()


def reply(client, mrn, **body):
    r = client.post(f"/simulator/{mrn}/reply", headers=ANITA, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def cards(client, mrn):
    return {c["name"]: c for c in client.get(f"/patients/{mrn}/cards", headers=ANITA).json()}


def outbox(mrn):
    with db() as c:
        return c.execute("SELECT * FROM outbox_message WHERE mrn = %s ORDER BY message_id", (mrn,)).fetchall()


def set_clock(client, day, at):
    r = client.post("/clock/set", headers=ANITA, json={"date": day, "time": at})
    assert r.status_code == 200, r.text


def tap_and_voice(client, mrn, clip, event_id=None):
    reply(client, mrn, button="booked", in_reply_to=reminder(mrn)["message_id"])
    return reply(client, mrn, clip_id=clip, event_id=event_id)


def test_seed_after_use_case_1(uc2a, client):
    assert uc2a["scenario"] == "uc2a" and uc2a["patients"] == 2
    assert client.get("/demo/settings", headers=ANITA).json()["time"] == "10:15"
    for mrn in ("TP001", "TP004"):
        assert reminder(mrn)["status"] == "sent"
        assert cards(client, mrn)["Review visit"]["state"] == "Due"
    assert [c["id"] for c in client.get("/simulator/clips", headers=ANITA).json()] == ["HI-01", "HI-02"]


def test_uc2a_t1_positive_case(uc2a, client):
    # Step 1: the Booked tap moves the card, source patient button, no appointment date, no AI.
    reply(client, "TP001", button="booked", in_reply_to=reminder("TP001")["message_id"])
    rv = cards(client, "TP001")["Review visit"]
    assert (rv["state"], rv["booking_source"], rv["appointment_date"]) == ("Booked", "patient_button", None)
    assert scalar("SELECT count(*) FROM audit_ledger WHERE actor_type = 'ai_suggestion'") == 0

    # Steps 2-5: voice note -> text, translation, flag yes -> acknowledgement, handover, send block.
    r = reply(client, "TP001", clip_id="HI-01")
    assert r["handling"] == ["ai_flag_yes", "handover_opened:health_flag_handover"]
    with db() as c:
        v = c.execute("SELECT * FROM inbound_message WHERE kind = 'voice' AND mrn = 'TP001'").fetchone()
    assert v["transcript"] and v["translation"] and v["ai_flag"] == "yes" and not v["ai_failed"]
    audio = client.get(f"/inbound/{v['inbound_id']}/audio", headers=SEKHAR)
    assert audio.status_code == 200 and audio.headers["content-type"] == "audio/wav" and len(audio.content) > 10_000
    acks = [m for m in outbox("TP001") if m["template_id"] == "handover_ack"]
    assert len(acks) == 1 and acks[0]["status"] == "sent" and acks[0]["language"] == "English"
    assert acks[0]["body"].startswith("Dear Meena,\nGreetings from Sanjeevini Clinic. We have received your message.")
    row = next(p for p in client.get("/worklist", headers=ANITA).json()["patients"] if p["mrn"] == "TP001")
    assert row["main"] == "action" and {"health_mention", "voice"} <= set(row["reasons"][0]["marks"])
    detail = client.get("/patients/TP001", headers=ANITA).json()
    assert detail["send_block"] and detail["handover"]["stage"] == "awaiting_coordinator"
    messages = client.get("/patients/TP001/messages", headers=ANITA).json()
    assert any(m["kind"] == "voice" and m["transcript"] and m["translation"] and m["audio_id"] for m in messages)

    # Step 6: Anita reviews at 11:00 and sends it to the doctor: 45 minutes.
    hid = r["handover_id"]
    set_clock(client, "2026-11-09", "11:00")
    assert client.post(f"/handovers/{hid}/gate1", headers=ANITA, json={"choice": "send_to_doctor"}).status_code == 200
    h = client.get(f"/handovers/{hid}", headers=SEKHAR).json()
    assert (h["response_minutes"], h["reviewed_by"], h["stage"]) == (45, "u_anita", "awaiting_doctor")
    queue = client.get("/doctor/queue", headers=SEKHAR).json()
    assert queue[0]["audio_id"] and queue[0]["transcript"] and queue[0]["translation"]

    # Steps 7-10: Tue 10 Nov 09:30, the doctor must choose; Routine; instruction with days = 7.
    set_clock(client, "2026-11-10", "09:30")
    assert client.post(f"/handovers/{hid}/gate2", headers=SEKHAR, json={}).status_code == 422
    assert client.post(f"/handovers/{hid}/gate2", headers=ANITA, json={"decision": "routine"}).status_code == 403
    assert client.post(f"/handovers/{hid}/gate2", headers=SEKHAR, json={"decision": "routine"}).status_code == 200
    r = client.post(f"/handovers/{hid}/instruction", headers=SEKHAR, json={"text": "Review visit within 7 days", "days": 7})
    assert r.status_code == 200, r.text
    c = cards(client, "TP001")
    new = c["Doctor-requested visit"]
    assert (new["state"], new["due_date"], new["owner_name"], new["cover_name"], new["origin"]) == \
        ("Due", "2026-11-17", "Anita", "Ravi", "instruction")
    assert c["Review visit"]["state"] == "Booked"
    deadline = next(t for t in client.get("/patients/TP001", headers=ANITA).json()["open_tasks"]
                    if t["task_type"] == "booking_from_instruction")
    assert deadline["due_date"] == "2026-11-12"
    booking = outbox("TP001")[-1]
    assert (booking["template_id"], booking["status"], booking["actor_type"]) == ("instruction_booking", "sent", "agent")
    assert "17-Nov" in booking["body"] and len(booking["buttons"]) == 3
    # REQ-13: no G0 on the doctor-requested card.
    client.post("/rules/gap-check", headers=ANITA)
    assert scalar("SELECT count(*) FROM gap_event WHERE card_id = %s", new["card_id"]) == 0

    # Step 11: closing needs a reason; then the block is off.
    assert client.post(f"/handovers/{hid}/close", headers=ANITA, json={"reason": " "}).status_code == 422
    closed = client.post(f"/handovers/{hid}/close", headers=ANITA, json={"reason": "Doctor instruction done, patient messaged"})
    assert closed.json()["stage"] == "closed" and not client.get("/patients/TP001", headers=ANITA).json()["send_block"]


def test_uc2a_t2_send_block(uc2a, client):
    from app import messaging

    tap_and_voice(client, "TP001", "HI-01")
    with db() as c:
        for actor_type, actor in (("agent", "CarePilot agent"), ("person", "u_anita")):
            m = messaging.send_message(c, mrn="TP001", template_id="booking_reminder", actor_type=actor_type,
                                       actor=actor, reason_code="test")
            assert (m["status"], m["blocked_reason"]) == ("blocked", "held_handover_open")
    assert scalar("SELECT count(*) FROM audit_ledger WHERE action = 'message_blocked' AND reason_code = 'held_handover_open'") == 2
    hid = scalar("SELECT handover_id FROM handover WHERE mrn = 'TP001'")
    client.post(f"/handovers/{hid}/gate1", headers=ANITA, json={"choice": "nothing_clinical"})
    client.post(f"/handovers/{hid}/close", headers=ANITA, json={"reason": "Nothing clinical"})
    with db() as c:
        after = messaging.send_message(c, mrn="TP001", template_id="booking_reminder", actor_type="person",
                                       actor="u_anita", reason_code="test")
    assert after["status"] == "sent"


def test_uc2a_t3_negative_case(uc2a, client):
    from app import agent

    r = tap_and_voice(client, "TP004", "HI-02")
    assert r["handling"] == ["ai_flag_no", "proposal_created:held_open_proposal"] and r["handover_id"] is None
    assert [m["template_id"] for m in outbox("TP004")] == ["booking_reminder"]  # nothing sent after the tap
    assert scalar("SELECT count(*) FROM handover") == 0
    [prop] = client.get("/proposals", headers=ANITA).json()
    assert prop["summary"] and prop["intent"] in ("booked", "help_to_book", "cant_come", "pause_request", "other")
    assert scalar("SELECT reason_code FROM audit_ledger WHERE action = 'agent_decision' AND mrn = 'TP004' "
                  "AND task = 'reply_understanding'") == "held_open_proposal"
    gap = {"mrn": "TP004", "rule": "G0"}
    with db() as c:
        assert agent.select_action(c, gap)["reason_code"] == "held_open_proposal"   # the hold is on
    set_clock(client, "2026-11-09", "11:20")
    approved = client.post(f"/proposals/{prop['proposal_id']}/approve", headers=ANITA, json={}).json()
    assert (approved["status"], approved["decided_by"], approved["edited"], approved["minutes_taken"]) == \
        ("approved", "u_anita", False, 65)
    with db() as c:
        assert agent.select_action(c, gap)["reason_code"] == "gap_actionable"      # the hold is lifted
    assert client.get("/proposals", headers=ANITA).json() == []
    assert cards(client, "TP004")["Review visit"]["state"] == "Booked"
    assert scalar("SELECT count(*) FROM order_card WHERE mrn = 'TP004'") == 1
    assert client.post(f"/proposals/{prop['proposal_id']}/approve", headers=ANITA, json={}).status_code == 409


def test_uc2a_t4_ai_failure_counts_as_yes(uc2a, client):
    assert client.post("/demo/sarvam", headers=ANITA, json={"enabled": False}).json() == {"sarvam_enabled": False}
    r = tap_and_voice(client, "TP004", "HI-02")
    assert r["handling"] == ["ai_flag_yes_failed", "handover_opened:health_flag_handover"]
    with db() as c:
        v = c.execute("SELECT ai_attempts, ai_failed, ai_flag FROM inbound_message WHERE kind = 'voice'").fetchone()
    assert (v["ai_attempts"], v["ai_failed"], v["ai_flag"]) == (2, True, "yes")
    assert scalar("SELECT reason_code FROM audit_ledger WHERE action = 'ai_understanding'") == "ai_call_failed"
    h = client.get(f"/handovers/{r['handover_id']}", headers=ANITA).json()
    assert h["ai_failed"] is True
    assert [m["template_id"] for m in outbox("TP004") if m["status"] == "sent"] == ["booking_reminder", "handover_ack"]


def test_uc2a_t5_acknowledgement_falls_back_to_english(uc2a, client):
    with db() as c:
        c.execute("UPDATE patient SET preferred_language = 'Hindi' WHERE mrn = 'TP001'")
    tap_and_voice(client, "TP001", "HI-01")
    ack = next(m for m in outbox("TP001") if m["template_id"] == "handover_ack")
    assert (ack["status"], ack["language"]) == ("sent", "English")
    with db() as c:
        row = c.execute("SELECT details FROM audit_ledger WHERE action = 'message_sent' AND template_id = 'handover_ack'").fetchone()
    assert "Hindi template not approved" in row["details"]["language_fallback"]


def test_uc2a_t6_replay_creates_nothing_new(uc2a, client):
    first = tap_and_voice(client, "TP001", "HI-01", event_id="wa-evt-0001")
    again = reply(client, "TP001", clip_id="HI-01", event_id="wa-evt-0001")
    assert again["handling"] == ["duplicate_ignored"] and again["handover_id"] == first["handover_id"]
    assert scalar("SELECT count(*) FROM handover WHERE mrn = 'TP001'") == 1
    assert scalar("SELECT count(*) FROM outbox_message WHERE template_id = 'handover_ack'") == 1
    assert scalar("SELECT count(*) FROM proposal") == 0
    assert scalar("SELECT count(*) FROM inbound_message WHERE kind = 'voice'") == 1


def test_audit_order_positive_case(uc2a, client):
    r = tap_and_voice(client, "TP001", "HI-01")
    hid = r["handover_id"]
    client.post(f"/handovers/{hid}/gate1", headers=ANITA, json={"choice": "send_to_doctor"})
    client.post(f"/handovers/{hid}/gate2", headers=SEKHAR, json={"decision": "routine"})
    client.post(f"/handovers/{hid}/instruction", headers=SEKHAR, json={"text": "Review visit within 7 days", "days": 7})
    client.post(f"/handovers/{hid}/close", headers=ANITA, json={"reason": "Doctor instruction done, patient messaged"})
    with db() as c:
        rows = c.execute("""SELECT action, actor_type, template_id, reason_code, demo_time FROM audit_ledger
                            WHERE mrn = 'TP001' AND id > (SELECT max(id) FROM audit_ledger WHERE action = 'reminder_run')
                              AND action NOT IN ('task_created', 'task_done') ORDER BY id""").fetchall()
    seq = [(r["action"], r["actor_type"]) for r in rows]
    expected = [("reply_received", "system"), ("card_state_changed", "system"), ("voice_note_received", "system"), ("ai_understanding", "ai_suggestion"),
                ("agent_decision", "agent"), ("message_sent", "agent"), ("handover_opened", "system"),
                ("gate1_decided", "person"), ("gate2_decided", "person"), ("instruction_recorded", "person"),
                ("card_created", "system"), ("message_sent", "agent"), ("handover_closed", "person")]
    assert seq == expected, seq
    assert all(r["demo_time"] for r in rows)
    assert scalar("SELECT reason_code FROM audit_ledger WHERE action = 'agent_decision' AND mrn = 'TP001' "
                  "AND task = 'reply_understanding'") == "health_flag_handover"
