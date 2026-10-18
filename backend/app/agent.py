"""07:30 reminder run: the constrained agent acts on gap events within the autonomy policy.

For each gap that is open or blocked it re-reads the card (no longer Due: stop) and checks, in
order: consent in scope, patient Active, agent on, no open handover, the task allowed by the
policy, and an approved template for the patient's language. All pass: send, the gap becomes
reminder_sent (gap_actionable). Any fails: the gap becomes blocked with the reason, nothing is
sent, and the next run checks it again. It cannot write its own messages: it sends approved
templates through the send-guard. Same gap, different patient state, different action.
"""
from . import audit, messaging, policy, tasks

AGENT = "CarePilot agent"
TASK_FOR_RULE = {"G0": "booking_reminder", "G1": "missed_item_reminder"}
CANNOT_CONTACT = ("held_no_consent", "held_agent_off", "held_template_missing", "held_policy_off")
HELD_TEXT = {
    "held_no_consent": "no consent for automated messaging", "held_not_active": "patient not active (paused or closed)",
    "held_agent_off": "agent switched off", "held_handover_open": "handover open",
    "held_policy_off": "task not allowed by the autonomy policy",
    "held_template_missing": "no approved template in the patient's language",
    "held_open_proposal": "a proposal is waiting for the coordinator",
    "held_open_task": "a person is already helping the patient (open task)",
}


def set_next_action(conn, mrn: str, text: str) -> None:
    conn.execute("UPDATE patient SET next_planned_action = %s WHERE mrn = %s", (text, mrn))


def select_action(conn, gap: dict) -> dict:
    """Decide what to do for one gap. Pure reading: no writes."""
    patient = conn.execute("SELECT * FROM patient WHERE mrn = %s", (gap["mrn"],)).fetchone()
    scopes = messaging.latest_scopes(conn, gap["mrn"])
    handover = messaging.open_handover(conn, gap["mrn"])
    pol = policy.autonomy(conn)
    task = TASK_FOR_RULE[gap["rule"]]
    entry = pol["tasks"].get(task)
    mode = entry["mode"] if entry else "off"
    template_id = entry.get("template") if entry else None
    test_cycle = policy.settings(conn).get("test_cycle")
    if template_id and gap.get("card_type") == "test" and test_cycle and gap["rule"] == "G0":
        template_id = test_cycle.get("reminder_template", template_id)  # Use Case 2T: the test reminder
    has_template = bool(template_id) and policy.template(conn, template_id, patient["preferred_language"]) is not None
    inputs = {"status": patient["status"], "agent_on": patient["agent_on"], "scopes": scopes,
              "handover_open": handover is not None, "policy_mode": mode, "rule": gap["rule"],
              "language": patient["preferred_language"], "template_approved": has_template}

    def decide(reason):
        return {"action": "send_reminder" if reason == "gap_actionable" else "block", "reason_code": reason,
                "task": task, "mode": mode, "template": template_id, "policy_version": pol["version"],
                "inputs": inputs}

    if "automated_messaging" not in scopes or "whatsapp_patient" not in scopes:
        return decide("held_no_consent")
    if patient["status"] in ("Paused", "Lost to follow-up", "Followed up elsewhere"):
        return decide("held_not_active")
    if not patient["agent_on"] or patient["status"] == "Agent off":
        return decide("held_agent_off")
    if handover is not None:
        return decide("held_handover_open")
    if policy.settings(conn).get("hold_on_proposal", True) and conn.execute(
            "SELECT 1 FROM proposal WHERE mrn = %s AND status = 'drafted'", (gap["mrn"],)).fetchone():
        return decide("held_open_proposal")
    if messaging.task_hold(conn, gap["mrn"]):
        return decide("held_open_task")
    if mode == "off":
        return decide("held_policy_off")
    if not has_template:
        return decide("held_template_missing")
    return decide("gap_actionable")


def run_agent(conn) -> list[dict]:
    token = audit.DEMO_TIME.set("07:30")
    try:
        return _run(conn)
    finally:
        audit.DEMO_TIME.reset(token)


def _run(conn) -> list[dict]:
    cfg = policy.settings(conn)
    results = []
    gaps = conn.execute(
        """
        SELECT g.*, c.item_code, c.name AS card_name, c.state AS card_state, c.card_type
        FROM gap_event g JOIN order_card c USING (card_id)
        WHERE g.status IN ('open', 'blocked') ORDER BY g.gap_id FOR UPDATE OF g
        """
    ).fetchall()
    for gap in gaps:
        if gap["card_state"] != "Due":  # re-read: booked since 07:00, so stop
            conn.execute("UPDATE gap_event SET status = 'resolved' WHERE gap_id = %s", (gap["gap_id"],))
            results.append({"gap_id": gap["gap_id"], "mrn": gap["mrn"], "rule": gap["rule"], "card": gap["item_code"],
                            "action": "stop", "reason_code": "card_no_longer_due", "outcome": "resolved"})
            continue
        d = select_action(conn, gap)
        if d["action"] == "block" and gap["status"] == "blocked" and gap["blocked_reason"] == d["reason_code"]:
            continue  # already recorded with this reason; checked again next run
        messages, due = [], gap["due_date"].strftime("%d/%m/%Y")

        def record(reason, outcome, sent=()):
            audit.append(
                conn, actor_type="agent", actor_name=AGENT, action="agent_decision", entity_type="gap_event",
                entity_id=str(gap["gap_id"]), mrn=gap["mrn"], reason_code=reason, rule=gap["rule"],
                task=d["task"], mode=d["mode"], policy_version=d["policy_version"], outcome=outcome,
                trigger_event=f"gap_event:{gap['gap_id']}", decision_inputs=d["inputs"],
                template_id=d["template"] if d["action"] == "send_reminder" else None,
                template_version=(tv["version"] if d["action"] == "send_reminder" and (tv := policy.template(
                    conn, d["template"], d["inputs"]["language"])) else None),
                details={"action": d["action"], "card": gap["item_code"], "messages": [m["message_id"] for m in sent]},
            )

        if d["action"] == "send_reminder":
            record(d["reason_code"], "send")  # the decision is recorded before the message it leads to
            messages.append(messaging.send_message(
                conn, mrn=gap["mrn"], template_id=d["template"], actor_type="agent", actor=AGENT,
                reason_code=d["reason_code"], card_id=gap["card_id"], gap_id=gap["gap_id"], mode=d["mode"]))
            caregiver_phone = conn.execute("SELECT caregiver_phone FROM patient WHERE mrn = %s",
                                           (gap["mrn"],)).fetchone()["caregiver_phone"]
            if cfg["caregiver_copy"] and "whatsapp_caregiver" in d["inputs"]["scopes"] and caregiver_phone:
                messages.append(messaging.send_message(
                    conn, mrn=gap["mrn"], template_id=d["template"], actor_type="agent", actor=AGENT,
                    reason_code=d["reason_code"], recipient="caregiver", card_id=gap["card_id"],
                    gap_id=gap["gap_id"], mode=d["mode"]))
            outcome = messages[0]["status"]
            if outcome == "blocked":  # the send-guard refused at send time
                d["reason_code"] = messages[0]["blocked_reason"]
                conn.execute("UPDATE gap_event SET status = 'blocked', blocked_reason = %s, outcome = 'blocked' "
                             "WHERE gap_id = %s", (d["reason_code"], gap["gap_id"]))
            else:
                conn.execute(
                    "UPDATE gap_event SET status = 'reminder_sent', outcome = %s, blocked_reason = NULL, "
                    "acted_on = (SELECT today FROM demo_clock) WHERE gap_id = %s", (outcome, gap["gap_id"]))
            set_next_action(conn, gap["mrn"], {
                "sent": f"Await patient reply to {d['task'].replace('_', ' ')} ({gap['item_code']} due {due})",
                "pending_approval": f"Coordinator to approve the drafted reminder ({gap['item_code']} due {due})",
            }.get(outcome, f"Reminder blocked: {messages[0]['blocked_reason']}"))
        else:
            outcome = "blocked"
            conn.execute("UPDATE gap_event SET status = 'blocked', blocked_reason = %s, outcome = 'blocked' "
                         "WHERE gap_id = %s", (d["reason_code"], gap["gap_id"]))
            set_next_action(conn, gap["mrn"], f"Reminder held: {HELD_TEXT[d['reason_code']]} ({gap['item_code']} due {due})")
            if cfg["people_tasks"] and d["reason_code"] in CANNOT_CONTACT:
                tasks.create_task(conn, mrn=gap["mrn"], task_type="contact_needed", gap_id=gap["gap_id"],
                                  card_id=gap["card_id"], actor_type="agent", actor=AGENT,
                                  reason=f"{gap['rule']} gap for {gap['card_name']}; agent cannot contact "
                                         f"({HELD_TEXT[d['reason_code']]})",
                                  dedupe_key=f"contact:{gap['gap_id']}")
        if d["action"] != "send_reminder" or outcome == "blocked":
            record(d["reason_code"], "blocked", messages)
        results.append({"gap_id": gap["gap_id"], "mrn": gap["mrn"], "rule": gap["rule"], "card": gap["item_code"],
                        "action": d["action"], "reason_code": d["reason_code"], "outcome": outcome})
    audit.append(conn, actor_type="agent", actor_name=AGENT, action="reminder_run", trigger_event="reminder_run_0730",
                 outcome=f"{sum(1 for r in results if r['outcome'] == 'sent')} sent, "
                         f"{sum(1 for r in results if r['outcome'] == 'blocked')} blocked",
                 details={"gaps_checked": len(gaps), "decisions": len(results)})
    return results
