"""The MVP Golden Path, Steps 0-8, through the API only (Increments 1-3, no AI yet).

Step 5 (Sarvam) arrives in Increment 4. Until then a free-text reply has no
classifier, which counts as uncertain, so it takes the safe handover path.
"""
from conftest import ANAND, PRIYA, SURESH, advance, confirm_plan, morning, outbox

SUNITA = "SYN-0007"
WORDS = "मैं शनिवार को आऊँगी। लेकिन पिछले हफ्ते से ऑपरेशन वाली जगह पर दर्द है।"


def test_golden_path(seeded, client):
    # Steps 0-1: the patient exists, consent is checked, the plan is confirmed.
    confirm_plan(client, SUNITA)

    # Step 2: on Mon 9 Nov the 07:00 gap check finds V1 (due 16 Nov) unbooked; again creates nothing.
    advance(client, 7)
    run = morning(client)
    assert ("G0", "2026-11-16") in {(g["rule"], g["due_date"]) for g in run["gap_check"]["new_gaps"]}
    assert client.post("/rules/gap-check", headers=PRIYA).json()["new_gaps"] == []

    # Step 3: the agent checks policy and sends the approved Hindi reminder with three buttons.
    v1 = next(m for m in outbox(SUNITA) if m["recipient"] == "patient" and "16/11/2026" in m["body"])
    assert v1["template_id"] == "booking_reminder" and len(v1["buttons"]) == 3
    assert client.get(f"/patients/{SUNITA}", headers=PRIYA).json()["next_planned_action"].startswith("Await patient reply")

    # Step 4: "Booked" plus a message about pain at the operation site.
    r = client.post(f"/simulator/{SUNITA}/reply", headers=PRIYA,
                    json={"button": "booked", "text": WORDS, "in_reply_to": v1["message_id"]}).json()
    cards = {c["item_code"]: c["state"] for c in client.get(f"/patients/{SUNITA}/cards", headers=PRIYA).json()}
    assert cards["V1"] == "Booked"                                  # button: no AI call
    hid = r["handover_id"]                                          # words: safe path

    # Step 6: the agent stops; the fixed acknowledgement is the only automated message.
    assert outbox(SUNITA)[-1]["template_id"] == "handover_ack"
    sent_before = len(outbox(SUNITA))
    advance(client, 1)
    morning(client)
    assert len(outbox(SUNITA)) == sent_before

    # Step 7: Gate 1 coordinator, Gate 2 doctor's explicit choice, Gate 3 instruction.
    client.post(f"/handovers/{hid}/gate1", headers=PRIYA, json={"choice": "send_to_doctor"})
    assert client.get("/doctor/queue", headers=ANAND).json()[0]["patient_words"] == WORDS
    assert client.post(f"/handovers/{hid}/gate2", headers=ANAND, json={}).status_code == 422
    client.post(f"/handovers/{hid}/gate2", headers=ANAND, json={"decision": "urgent"})
    client.post(f"/handovers/{hid}/call", headers=PRIYA, json={"note": "Called the same day; coming Saturday"})

    # Step 8: the instruction creates a new card and an approved booking message the same day.
    client.post(f"/handovers/{hid}/instruction", headers=ANAND, json={
        "text": "Surgical-site review within 7 days",
        "order": {"name": "Surgical-site review", "department": "Oncology OPD", "within_days": 7}})
    assert outbox(SUNITA)[-1]["template_id"] == "instruction_booking"
    assert client.post(f"/handovers/{hid}/close", headers=PRIYA, json={}).status_code == 422
    client.post(f"/handovers/{hid}/close", headers=PRIYA, json={"reason": "Review booked; doctor's plan in place"})
    assert client.get(f"/patients/{SUNITA}", headers=PRIYA).json()["next_planned_action"] == "Routine follow-up resumed"

    # Step 9 (partly): the audit trail holds the sequence and the chain verifies; the chief sees aggregates.
    entries = client.get("/audit?limit=200").json()
    actions = [e["action"] for e in reversed(entries["entries"])]
    for step in ("plan_confirmed", "gap_detected", "agent_decision", "message_sent", "reply_received",
                 "card_state_changed", "handover_opened", "gate1_decided", "gate2_decided", "patient_called",
                 "instruction_recorded", "handover_closed"):
        assert step in actions, step
    assert actions.index("handover_opened") < actions.index("gate2_decided") < actions.index("handover_closed")
    assert entries["chain"]["valid"]
    chief = client.get("/summary", headers=SURESH)
    assert "Sunita" not in chief.text and "SYN-" not in chief.text and chief.json()["open_handovers"] == 0
