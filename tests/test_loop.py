"""Increment 2: G0/G1 gap rules, the constrained agent, send-guard, outbox, buttons (no AI)."""
from datetime import date

import pytest

from conftest import ANAND, PRIYA, advance, confirm_plan, last_audit, morning, outbox, scalar


def gaps(client):
    return client.post("/rules/gap-check", headers=PRIYA).json()


def test_no_plan_no_gaps(seeded, client):
    r = morning(client)
    assert r["gap_check"]["new_gaps"] == [] and r["actions"] == []


def test_g0_detects_the_unbooked_item_once(seeded, client):
    confirm_plan(client, "SYN-0007")  # T1 due Mon 9 Nov: inside the 7-day window on 2 Nov
    first = gaps(client)
    assert [(g["rule"], g["due_date"]) for g in first["new_gaps"]] == [("G0", "2026-11-09")]
    assert gaps(client)["new_gaps"] == []  # running again creates no duplicate
    assert scalar("SELECT count(*) FROM gap_event") == 1
    assert scalar("SELECT count(*) FROM audit_ledger WHERE action = 'gap_detected'") == 1


def test_agent_sends_an_approved_reminder_and_records_why(seeded, client):
    confirm_plan(client, "SYN-0007")
    actions = morning(client)["actions"]
    assert actions == [{"gap_id": 1, "mrn": "SYN-0007", "rule": "G0", "card": "T1", "action": "send_reminder",
                        "reason_code": "gap_actionable", "outcome": "sent"}]
    sent = outbox("SYN-0007")
    assert [(m["recipient"], m["status"], m["language"]) for m in sent] == [
        ("patient", "sent", "Hindi"), ("caregiver", "sent", "Hindi")]
    assert sent[0]["body"].startswith("नमस्ते Sunita जी") and "09/11/2026" in sent[0]["body"]
    assert [b["id"] for b in sent[0]["buttons"]] == ["booked", "help_book", "cant_come", "booked_elsewhere"]
    assert [b["label_en"] for b in sent[0]["buttons"]][0] == "Blood tests booked"
    decision = last_audit("agent_decision")
    assert (decision["actor_type"], decision["rule"], decision["task"], decision["mode"], decision["policy_version"],
            decision["template_id"], decision["template_version"], decision["outcome"], decision["trigger_event"]) == \
        ("agent", "G0", "booking_reminder", "live", "1.0", "test_booking_reminder", "1.2", "send", "gap_event:1")
    assert decision["reason_code"] == "gap_actionable"
    assert decision["decision_inputs"]["handover_open"] is False
    assert client.get("/patients/SYN-0007", headers=PRIYA).json()["next_planned_action"].startswith("Await patient reply")
    assert morning(client)["actions"] == []  # nothing is sent twice
    assert len(outbox("SYN-0007")) == 2


def test_same_gap_different_patient_state(seeded, client):
    """Golden Path section 3: patient A gets the reminder, B is paused, C has an open handover."""
    for mrn in ("SYN-0007", "SYN-0004", "SYN-0005"):
        confirm_plan(client, mrn)
    gaps(client)
    client.post("/patients/SYN-0004/status", headers=PRIYA, json={"status": "Paused", "reason": "Patient asked"})
    client.post("/patients/SYN-0005/handover", headers=PRIYA, json={"reason": "Caregiver phoned with a concern"})
    by_mrn = {}
    for a in client.post("/agent/run", headers=PRIYA).json()["actions"]:
        by_mrn.setdefault(a["mrn"], set()).add((a["action"], a["reason_code"]))
    assert by_mrn["SYN-0007"] == {("send_reminder", "gap_actionable")}
    assert by_mrn["SYN-0004"] == {("block", "held_not_active")}
    assert by_mrn["SYN-0005"] == {("block", "held_handover_open")}
    assert outbox("SYN-0004") == [] and outbox("SYN-0005") == []
    assert client.post("/agent/run", headers=PRIYA).json()["actions"] == []  # a blocked gap is not re-logged

    client.post("/patients/SYN-0004/status", headers=PRIYA, json={"status": "Active"})
    resumed = client.post("/agent/run", headers=PRIYA).json()["actions"]
    assert {(a["mrn"], a["outcome"]) for a in resumed} == {("SYN-0004", "sent")}


def test_send_layer_blocks_even_if_a_workflow_tries(seeded, client):
    from app import db, messaging

    client.post("/patients/SYN-0007/handover", headers=PRIYA, json={"reason": "Concern by phone"})
    with db.connect() as c:
        agent_try = messaging.send_message(c, mrn="SYN-0007", template_id="booking_reminder", actor_type="agent",
                                           actor="CarePilot agent", reason_code="test")
        ack = messaging.send_message(c, mrn="SYN-0007", template_id="handover_ack", actor_type="system",
                                     actor="test", reason_code="test")
        person = messaging.send_message(c, mrn="SYN-0007", template_id="booking_reminder", actor_type="person",
                                        actor="u_priya", reason_code="test")
        caregiver = messaging.send_message(c, mrn="SYN-0021", template_id="booking_reminder", actor_type="person",
                                           actor="u_priya", reason_code="test", recipient="caregiver")
        no_consent = messaging.send_message(c, mrn="SYN-0013", template_id="booking_reminder", actor_type="person",
                                            actor="u_priya", reason_code="test")
    assert (agent_try["status"], agent_try["blocked_reason"]) == ("blocked", "held_handover_open")
    assert ack["status"] == "sent"       # the fixed acknowledgement is the one exception
    assert (person["status"], person["blocked_reason"]) == ("blocked", "held_handover_open")  # only 2 templates pass
    assert caregiver["blocked_reason"] == "no_whatsapp_consent"
    assert no_consent["blocked_reason"] == "no_whatsapp_consent"
    assert scalar("SELECT count(*) FROM audit_ledger WHERE action = 'message_blocked'") == 4


def test_agent_off_and_missing_consent_route_to_the_coordinator(seeded, client):
    confirm_plan(client, "SYN-0003")  # consent without automated_messaging
    confirm_plan(client, "SYN-0007")
    client.post("/patients/SYN-0007/status", headers=PRIYA, json={"status": "Agent off", "reason": "Prefers calls"})
    actions = {(a["mrn"], a["reason_code"]) for a in morning(client)["actions"]}
    assert actions == {("SYN-0003", "held_no_consent"), ("SYN-0007", "held_agent_off")}
    assert outbox("SYN-0003") == [] and outbox("SYN-0007") == []
    tasks = client.get("/needs-you", headers=PRIYA).json()["tasks"]
    contact = [t for t in tasks if t["task_type"] == "contact_needed"]
    assert {t["mrn"] for t in contact} == {"SYN-0003", "SYN-0007"}
    assert all(t["assigned_name"] == "Priya Nair" and t["cover_name"] == "Meera Joseph" for t in contact)


def test_g1_missed_item_reminder_in_kannada(seeded, client):
    confirm_plan(client, "SYN-0001")  # V1 due 20 Oct, T1 13 Oct: both missed by 2 Nov
    actions = morning(client)["actions"]
    # Use Case 2T v1.4: T1 is past due with no report, so its report check is also raised and sent.
    assert {(a["rule"], a["card"], a["reason_code"]) for a in actions} == {
        ("G1", "V1", "gap_actionable"), ("G1", "T1", "gap_actionable"), ("report_check", None, "report_check_due")}
    assert outbox("SYN-0001")[0]["body"].startswith("ನಮಸ್ಕಾರ Meena ಅವರೇ")


def test_booked_button_updates_the_visit_card_without_ai(seeded, client):
    confirm_plan(client, "SYN-0007")
    advance(client, 7)  # Mon 9 Nov: V1 (16 Nov) enters the window
    morning(client)
    v1_msg = next(m for m in outbox("SYN-0007") if m["recipient"] == "patient" and "16/11/2026" in m["body"])
    r = client.post("/simulator/SYN-0007/reply", headers=PRIYA, json={"button": "booked", "in_reply_to": v1_msg["message_id"]})
    assert r.json()["handling"] == ["button_booked:card_booked"] and r.json()["handover_id"] is None
    cards = {c["item_code"]: c["state"] for c in client.get("/patients/SYN-0007/cards", headers=PRIYA).json()}
    assert cards["V1"] == "Booked"
    change = last_audit("card_state_changed")
    assert (change["actor_type"], change["details"]["source"]) == ("system", "patient_button")
    assert scalar("SELECT count(*) FROM audit_ledger WHERE actor_type = 'ai'") == 0  # no model involved
    # 5 Nov: booked for the day of the tap, on the appointment list, and the reminder gap closed at once
    v1 = next(c for c in client.get("/patients/SYN-0007/cards", headers=PRIYA).json() if c["item_code"] == "V1")
    assert v1["appointment_date"] == "2026-11-09"
    assert [(a["item_code"], a["source"]) for a in client.get("/appointments", headers=PRIYA).json()
            if a["mrn"] == "SYN-0007"] == [("V1", "patient_button")]
    assert scalar("SELECT count(*) FROM gap_event WHERE card_id = %s AND status <> 'resolved'", v1["card_id"]) == 0


def test_other_buttons_create_people_tasks(seeded, client):
    confirm_plan(client, "SYN-0007")
    morning(client)  # T1 reminder (a test card)
    client.post("/simulator/SYN-0007/reply", headers=PRIYA, json={"button": "cant_come"})
    client.post("/simulator/SYN-0007/reply", headers=PRIYA, json={"button": "booked"})
    r = client.post("/simulator/SYN-0007/reply", headers=PRIYA, json={"button": "help_book"})
    tasks = {t["task_type"]: t for t in client.get("/needs-you", headers=PRIYA).json()["tasks"]}
    # Use Case 2b: a can't-come task, owned by the card's owner (T1: Laboratory) and cover.
    # 5 Nov: Booked on a test books it for the day of the tap, with no task; once booked, Please help me book
    # changes nothing (no slots, no task).
    assert "cant_come" in tasks and not {"help_booking", "confirm_test_booking"} & set(tasks)
    assert r.json()["handling"] == ["button_help_book:already_progressed"]
    t1 = next(c for c in client.get("/patients/SYN-0007/cards", headers=PRIYA).json() if c["item_code"] == "T1")
    assert (t1["state"], t1["label"], t1["appointment_date"]) == ("Booked", None, "2026-11-02")
    assert tasks["cant_come"]["due_date"] == "2026-11-04"  # 2 working days
    assert (tasks["cant_come"]["owner_name"], tasks["cant_come"]["cover_name"]) == ("Sunil Kumar", "Priya Nair")


def test_still_unbooked_at_t3_goes_to_the_coordinator_once(seeded, client):
    confirm_plan(client, "SYN-0007")
    morning(client)          # T1 reminder sent on 2 Nov, due 9 Nov
    advance(client, 4)       # Fri 6 Nov = T-3
    assert morning(client)["gap_check"]["t3_tasks"] == 1
    morning(client)
    assert scalar("SELECT count(*) FROM coordinator_task WHERE task_type = 'booking_help'") == 1


def test_shadow_mode_drafts_need_approval(seeded, client):
    from app import db

    with db.connect() as c:
        c.execute("""UPDATE config_versions SET body = jsonb_set(body, '{tasks,booking_reminder,mode}', '"shadow"')
                     WHERE kind = 'autonomy_policy'""")
    confirm_plan(client, "SYN-0007")
    assert {a["outcome"] for a in morning(client)["actions"]} == {"pending_approval"}
    drafts = client.get("/needs-you", headers=PRIYA).json()["approvals"]
    assert len(drafts) == 2
    approved = client.post(f"/outbox/{drafts[0]['message_id']}/approve", headers=PRIYA).json()
    assert (approved["status"], approved["approved_by"]) == ("sent", "u_priya")
    assert client.post(f"/outbox/{drafts[1]['message_id']}/reject", headers=PRIYA).json()["status"] == "rejected"
    assert client.post(f"/outbox/{drafts[1]['message_id']}/approve", headers=PRIYA).status_code == 409


@pytest.mark.parametrize("path", ["/demo/morning-run", "/rules/gap-check", "/agent/run", "/deadlines/run",
                                  "/simulator/SYN-0007/reply"])
def test_only_the_coordinator_runs_the_loop(seeded, client, path):
    assert client.post(path, headers=ANAND, json={"button": "booked"}).status_code == 403


def test_status_change_needs_a_reason(seeded, client):
    r = client.post("/patients/SYN-0007/status", headers=PRIYA, json={"status": "Paused"})
    assert r.status_code == 422
    r = client.post("/patients/SYN-0007/status", headers=PRIYA, json={"status": "Lost to follow-up", "reason": "x"})
    assert r.status_code == 422  # closing statuses are not settable here
    assert client.get("/clock", headers=PRIYA).json()["today"] == date(2026, 11, 2).isoformat()


def test_help_booking_on_a_test_card_books_the_test(seeded, client):
    """Golden Path (5 Nov): "Please help me book" on the T1 blood-test reminder offers four slots in Hindi; the slot
    she taps books the test for that date and time, she is told so, and it is on the appointment list."""
    confirm_plan(client, "SYN-0007")
    morning(client)  # T1 reminder (a test card)
    r = client.post("/simulator/SYN-0007/reply", headers=PRIYA, json={"button": "help_book"})
    assert r.json()["handling"] == ["button_help_book:slots_offered"]
    assert not [t for t in client.get("/patients/SYN-0007/tasks", headers=PRIYA).json()["tasks"]
                if t["task_type"] == "help_booking"]
    offer = next(m for m in outbox("SYN-0007") if m["template_id"] == "booking_slots")
    assert (offer["status"], offer["language"], len(offer["buttons"])) == ("sent", "Hindi", 4)
    assert offer["body"].startswith("नमस्ते Sunita जी। कृपया अपनी")
    days = [b["date"] for b in offer["buttons"]]
    assert days == sorted(set(days)) and all("2026-11-03" <= d <= "2026-11-16" for d in days)
    slot = offer["buttons"][2]
    r = client.post("/simulator/SYN-0007/reply", headers=PRIYA,
                    json={"button": "slot_3", "in_reply_to": offer["message_id"]})
    assert r.json()["handling"] == ["button_slot_3:card_booked_slot"]
    t1 = next(c for c in client.get("/patients/SYN-0007/cards", headers=PRIYA).json() if c["item_code"] == "T1")
    assert (t1["state"], t1["appointment_date"], t1["booking_source"], t1["appointment_time"]) == \
        ("Booked", slot["date"], "patient_slot", f"{slot['time']}:00")
    done = outbox("SYN-0007")[-1]
    assert done["template_id"] == "booking_confirmed" and "सफलतापूर्वक बुक हो गया है" in done["body"]
    assert slot["label"] in done["body"]
    # a second tap on another slot changes nothing
    r = client.post("/simulator/SYN-0007/reply", headers=PRIYA,
                    json={"button": "slot_1", "in_reply_to": offer["message_id"]})
    assert r.json()["handling"] == ["button_slot_1:already_progressed"]


def test_help_booking_goes_to_a_person_when_the_slots_cannot_be_sent(seeded, client):
    """No consent for automated messages: the slot offer is blocked, so a coordinator gets the help-booking task."""
    from app import db

    confirm_plan(client, "SYN-0007")
    morning(client)
    reminder = next(m for m in outbox("SYN-0007") if m["recipient"] == "patient")
    with db.connect() as c:
        c.execute("UPDATE consent SET scopes = array_remove(scopes, 'automated_messaging') WHERE mrn = 'SYN-0007'")
    r = client.post("/simulator/SYN-0007/reply", headers=PRIYA,
                    json={"button": "help_book", "in_reply_to": reminder["message_id"]})
    assert r.json()["handling"] == ["button_help_book:slots_not_sent_help_booking_created"]
    assert next(m for m in outbox("SYN-0007") if m["template_id"] == "booking_slots")["status"] == "blocked"
