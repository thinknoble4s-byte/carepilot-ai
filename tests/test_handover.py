"""Increment 3: handover, Gates 1-3, deadlines with escalation, closure and resume."""
import psycopg
import pytest

from conftest import ANAND, PRIYA, advance, confirm_plan, morning, outbox, scalar

KAVITA = {"X-User": "u_kavita"}
PAIN = "मैं शनिवार को आऊँगी। लेकिन पिछले हफ्ते से ऑपरेशन वाली जगह पर दर्द है।"


def reply(client, mrn="SYN-0007", **body):
    r = client.post(f"/simulator/{mrn}/reply", headers=PRIYA, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def needs(client):
    return client.get("/needs-you", headers=PRIYA).json()


def task(client, task_type):
    return next(t for t in needs(client)["tasks"] if t["task_type"] == task_type)


def test_free_text_takes_the_safe_path(seeded, client):
    confirm_plan(client, "SYN-0007")
    morning(client)
    r = reply(client, text=PAIN)
    assert r["handling"] == ["ai_flag_yes", "handover_opened:health_flag_handover"]
    h = client.get(f"/handovers/{r['handover_id']}", headers=PRIYA).json()
    assert (h["stage"], h["patient_words"]) == ("awaiting_coordinator", PAIN)
    ack = outbox("SYN-0007")[-1]
    assert (ack["template_id"], ack["status"], ack["language"]) == ("handover_ack", "sent", "Hindi")
    gate1 = task(client, "coordinator_review")
    assert (gate1["assigned_name"], gate1["due_date"]) == ("Priya Nair", "2026-11-03")
    assert "Gate 1" in client.get("/patients/SYN-0007", headers=PRIYA).json()["next_planned_action"]


def test_booked_button_with_a_health_mention_does_both(seeded, client):
    """Golden Path Step 4: the button updates the card; the words go to a person."""
    confirm_plan(client, "SYN-0007")
    advance(client, 7)
    morning(client)
    v1 = next(m for m in outbox("SYN-0007") if m["recipient"] == "patient" and "16/11/2026" in m["body"])
    r = reply(client, button="booked", text=PAIN, in_reply_to=v1["message_id"])
    assert r["handling"] == ["button_booked:card_booked", "ai_flag_yes", "handover_opened:health_flag_handover"]
    assert {c["item_code"]: c["state"] for c in client.get("/patients/SYN-0007/cards", headers=PRIYA).json()}["V1"] == "Booked"


def test_every_automated_send_is_blocked_while_open(seeded, client):
    confirm_plan(client, "SYN-0007")
    reply(client, text=PAIN)                    # handover before any reminder
    before = len(outbox("SYN-0007"))            # just the acknowledgement
    actions = morning(client)["actions"]
    assert {(a["action"], a["reason_code"]) for a in actions} == {("block", "held_handover_open")}
    advance(client, 7)
    morning(client)
    assert len(outbox("SYN-0007")) == before
    assert "Reminder held: handover open" in client.get("/patients/SYN-0007", headers=PRIYA).json()["next_planned_action"]


def test_help_keyword_reaches_a_person(seeded, client):
    r = reply(client, text="मदद")
    assert r["handling"] == ["handover_opened:help_keyword"]


def test_a_second_reply_joins_the_open_handover(seeded, client):
    first = reply(client, text="pain since last week")
    second = reply(client, text="also a fever")
    assert second["handover_id"] == first["handover_id"]
    assert second["handling"] == ["ai_flag_yes", "handover_updated:health_flag_handover"]
    assert scalar("SELECT count(*) FROM handover") == 1
    assert scalar("SELECT count(*) FROM outbox_message WHERE template_id = 'handover_ack'") == 1


def test_manual_handover_needs_a_reason(seeded, client):
    assert client.post("/patients/SYN-0007/handover", headers=PRIYA, json={}).status_code == 422
    r = client.post("/patients/SYN-0007/handover", headers=PRIYA, json={"reason": "Daughter phoned"})
    assert r.status_code == 201 and r.json()["trigger"] == "manual"
    assert outbox("SYN-0007") == []  # no acknowledgement for a handover a person opened


def test_gates_run_in_order(seeded, client):
    hid = reply(client, text="pain")["handover_id"]
    assert client.post(f"/handovers/{hid}/gate1", headers=PRIYA, json={"choice": "maybe"}).status_code == 422
    assert client.post(f"/handovers/{hid}/gate2", headers=ANAND, json={"decision": "routine"}).status_code == 409
    assert client.post(f"/handovers/{hid}/close", headers=PRIYA, json={"reason": "x"}).status_code == 409


def test_nothing_clinical_then_named_closure_resumes_follow_up(seeded, client):
    confirm_plan(client, "SYN-0007")
    hid = client.post("/patients/SYN-0007/handover", headers=PRIYA, json={"reason": "Travel plans by phone"}).json()["handover_id"]
    morning(client)  # the T1 reminder waits
    assert client.post(f"/handovers/{hid}/gate1", headers=PRIYA, json={"choice": "nothing_clinical"}).json()["stage"] \
        == "ready_to_close"
    assert client.post(f"/handovers/{hid}/close", headers=PRIYA, json={"reason": "  "}).status_code == 422
    assert client.post(f"/handovers/{hid}/close", headers=ANAND, json={"reason": "ok"}).status_code == 403
    closed = client.post(f"/handovers/{hid}/close", headers=PRIYA, json={"reason": "Travel plans only; no concern"})
    assert (closed.json()["stage"], closed.json()["closed_by"]) == ("closed", "u_priya")
    assert client.get("/patients/SYN-0007", headers=PRIYA).json()["next_planned_action"] == "Routine follow-up resumed"
    resumed = client.post("/agent/run", headers=PRIYA).json()["actions"]
    assert {(a["card"], a["outcome"]) for a in resumed} == {("T1", "sent")}


def test_call_first_then_decide(seeded, client):
    hid = reply(client, text="worried")["handover_id"]
    assert client.post(f"/handovers/{hid}/gate1", headers=PRIYA, json={"choice": "call_first"}).json()["stage"] == "calling"
    assert client.post(f"/handovers/{hid}/call", headers=PRIYA, json={"note": ""}).status_code == 422
    after = client.post(f"/handovers/{hid}/call", headers=PRIYA, json={"note": "Spoke to patient; mild ache"})
    assert after.json()["stage"] == "awaiting_coordinator"
    assert client.post(f"/handovers/{hid}/gate1", headers=PRIYA, json={"choice": "nothing_clinical"}).status_code == 200


def test_doctor_path_urgent_with_instruction_and_escalation(seeded, client):
    confirm_plan(client, "SYN-0007")
    morning(client)
    hid = reply(client, text=PAIN)["handover_id"]
    client.post(f"/handovers/{hid}/gate1", headers=PRIYA, json={"choice": "send_to_doctor"})

    queue = client.get("/doctor/queue", headers=ANAND).json()
    assert [(q["mrn"], q["stage"], q["patient_words"]) for q in queue] == [("SYN-0007", "awaiting_doctor", PAIN)]
    assert queue[0]["source"].startswith("WhatsApp reply #")

    # Gate 2: explicit choice by a doctor; nothing pre-selected.
    assert client.post(f"/handovers/{hid}/gate2", headers=PRIYA, json={"decision": "urgent"}).status_code == 403
    assert client.post(f"/handovers/{hid}/gate2", headers=ANAND, json={}).status_code == 422
    assert client.post(f"/handovers/{hid}/gate2", headers=ANAND, json={"decision": "soon"}).status_code == 422
    assert client.post(f"/handovers/{hid}/gate2", headers=ANAND, json={"decision": "urgent"}).status_code == 200
    call = task(client, "urgent_call")
    assert (call["due_date"], call["assigned_name"]) == ("2026-11-02", "Priya Nair")  # same day

    # Missed same-day call escalates to the coordinator's cover.
    advance(client, 1)
    escalations = client.post("/deadlines/run", headers=PRIYA).json()["escalations"]
    assert [(e["task_type"], e["assigned_name"]) for e in escalations] == [("urgent_call", "Meera Joseph")]
    assert client.post("/deadlines/run", headers=PRIYA).json()["escalations"] == []  # once

    # Gate 3: the instruction creates a new order card and a booking message, during the open handover.
    assert client.post(f"/handovers/{hid}/instruction", headers=ANAND, json={"text": " "}).status_code == 422
    r = client.post(f"/handovers/{hid}/instruction", headers=ANAND, json={
        "text": "Surgical-site review within 7 days", "days": 7})
    assert r.status_code == 200 and r.json()["stage"] == "ready_to_close"
    cards = {c["item_code"]: c for c in client.get("/patients/SYN-0007/cards", headers=PRIYA).json()}
    x1 = cards["X1"]
    assert (x1["name"], x1["due_date"], x1["owner_name"], x1["cover_name"], x1["state"]) == \
        ("Doctor-requested visit", "2026-11-10", "Priya Nair", "Meera Joseph", "Due")
    booking = task(client, "booking_from_instruction")
    assert booking["due_date"] == "2026-11-05"  # 2 working days from Tue 3 Nov
    msg = outbox("SYN-0007")[-1]
    assert (msg["template_id"], msg["status"], msg["actor"]) == ("instruction_booking", "sent", "CarePilot agent")

    # Closure waits for the urgent call, then needs a named person and a reason.
    assert client.post(f"/handovers/{hid}/close", headers=PRIYA, json={"reason": "done"}).status_code == 409
    client.post(f"/handovers/{hid}/call", headers={"X-User": "u_meera"}, json={"note": "Called; review booked"})
    closed = client.post(f"/handovers/{hid}/close", headers={"X-User": "u_meera"}, json={"reason": "Review arranged"})
    assert closed.json()["closed_by"] == "u_meera"


def test_missed_gates_escalate_to_cover_or_remind(seeded, client):
    hid = reply(client, text="pain")["handover_id"]
    advance(client, 2)  # Wed 4 Nov: Gate 1 was due Tue 3 Nov
    esc = client.post("/deadlines/run", headers=PRIYA).json()["escalations"]
    assert [(e["task_type"], e["action"], e["assigned_name"]) for e in esc] == [
        ("coordinator_review", "task_escalated", "Meera Joseph")]
    client.post(f"/handovers/{hid}/gate1", headers=PRIYA, json={"choice": "send_to_doctor"})
    advance(client, 2)  # Fri 6 Nov: the doctor's decision was due Thu 5 Nov
    esc = client.post("/deadlines/run", headers=PRIYA).json()["escalations"]
    assert [(e["task_type"], e["assigned_name"]) for e in esc] == [("doctor_decision", "Dr. Kavita Menon")]
    client.post(f"/handovers/{hid}/gate2", headers=KAVITA, json={"decision": "routine"})
    advance(client, 4)  # Tue 10 Nov: the instruction was due Mon 9 Nov -> reminder to the same doctor
    esc = client.post("/deadlines/run", headers=PRIYA).json()["escalations"]
    assert [(e["task_type"], e["action"], e["assigned_name"]) for e in esc] == [
        ("doctor_instruction", "task_reminder_sent", "Dr. Kavita Menon")]


def test_database_refuses_a_closure_without_name_and_reason(conn):
    from app import handover

    h, _ = handover.open_handover(conn, "SYN-0007", trigger="manual", actor_type="person", actor="u_priya")
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE handover SET stage = 'closed' WHERE handover_id = %s", (h["handover_id"],))
