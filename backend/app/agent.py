"""07:30 agent run: the constrained agent acts on gap events within the
autonomy policy. It checks the patient's current state, picks a permitted
action, records the reason code and updates the next planned action. It
cannot write its own messages: it only sends approved templates through the
send-guard. Same gap, different patient state, different action.
"""
from . import audit, messaging, policy, tasks

AGENT = "CarePilot agent"
TASK_FOR_RULE = {"G0": "booking_reminder", "G1": "missed_item_reminder"}


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
    inputs = {"status": patient["status"], "agent_on": patient["agent_on"], "scopes": scopes,
              "handover_open": handover is not None, "policy_mode": mode, "rule": gap["rule"]}

    def decide(action, reason):
        return {"action": action, "reason_code": reason, "task": task, "mode": mode,
                "template": entry.get("template") if entry else None, "policy_version": pol["version"],
                "inputs": inputs}

    if mode == "off":
        return decide("route_to_coordinator", "task_not_in_policy")
    if patient["status"] == "Paused":
        return decide("wait", "patient_paused")
    if patient["status"] in ("Lost to follow-up", "Followed up elsewhere"):
        return decide("close_gap", "patient_closed")
    if handover is not None:
        return decide("wait", "handover_open")
    if "automated_messaging" not in scopes:
        return decide("route_to_coordinator", "no_automated_consent")
    if not patient["agent_on"] or patient["status"] == "Agent off":
        return decide("route_to_coordinator", "agent_off")
    if "whatsapp_patient" not in scopes:
        return decide("route_to_coordinator", "no_whatsapp_consent")
    return decide("send_reminder", f"{gap['rule'].lower()}_{task}")


def run_agent(conn) -> list[dict]:
    results = []
    gaps = conn.execute(
        """
        SELECT g.*, c.item_code, c.name AS card_name FROM gap_event g JOIN order_card c USING (card_id)
        WHERE g.status = 'open' ORDER BY g.gap_id FOR UPDATE OF g
        """
    ).fetchall()
    for gap in gaps:
        d = select_action(conn, gap)
        if d["action"] == "wait" and gap["last_skip_reason"] == d["reason_code"]:
            continue  # already recorded; acts once the patient's state changes
        outcome, messages = d["action"], []
        if d["action"] == "send_reminder":
            messages.append(messaging.send_message(
                conn, mrn=gap["mrn"], template_id=d["template"], actor_type="agent", actor=AGENT,
                reason_code=d["reason_code"], card_id=gap["card_id"], gap_id=gap["gap_id"], mode=d["mode"]))
            scopes = d["inputs"]["scopes"]
            has_caregiver = conn.execute("SELECT caregiver_phone FROM patient WHERE mrn = %s",
                                         (gap["mrn"],)).fetchone()["caregiver_phone"]
            if "whatsapp_caregiver" in scopes and has_caregiver:
                messages.append(messaging.send_message(
                    conn, mrn=gap["mrn"], template_id=d["template"], actor_type="agent", actor=AGENT,
                    reason_code=d["reason_code"], recipient="caregiver", card_id=gap["card_id"],
                    gap_id=gap["gap_id"], mode=d["mode"]))
            outcome = messages[0]["status"]
            due = gap["due_date"].strftime("%d/%m/%Y")
            set_next_action(conn, gap["mrn"], {
                "sent": f"Await patient reply to {d['task'].replace('_', ' ')} ({gap['item_code']} due {due})",
                "pending_approval": f"Coordinator to approve the drafted reminder ({gap['item_code']} due {due})",
            }.get(outcome, f"Reminder blocked: {messages[0]['blocked_reason']}"))
        elif d["action"] == "route_to_coordinator":
            tasks.create_task(conn, mrn=gap["mrn"], task_type="contact_needed", gap_id=gap["gap_id"],
                              card_id=gap["card_id"], actor_type="agent", actor=AGENT,
                              reason=f"{gap['rule']} gap for {gap['card_name']}; agent cannot contact ({d['reason_code']})",
                              dedupe_key=f"contact:{gap['gap_id']}")
            outcome = "routed_to_coordinator"
            set_next_action(conn, gap["mrn"], f"Coordinator to contact the patient ({d['reason_code']})")
        elif d["action"] == "wait":
            outcome = "waiting"
            set_next_action(conn, gap["mrn"], {
                "patient_paused": "Reminders paused at the patient's request",
                "handover_open": "Reminders on hold until the handover is closed",
            }[d["reason_code"]])

        if d["action"] == "wait":
            conn.execute("UPDATE gap_event SET last_skip_reason = %s WHERE gap_id = %s",
                         (d["reason_code"], gap["gap_id"]))
        else:
            conn.execute(
                "UPDATE gap_event SET status = 'acted', outcome = %s, acted_on = (SELECT today FROM demo_clock), "
                "last_skip_reason = NULL WHERE gap_id = %s",
                (outcome, gap["gap_id"]),
            )
        audit.append(
            conn, actor_type="agent", actor_name=AGENT, action="agent_decision", entity_type="gap_event",
            entity_id=str(gap["gap_id"]), mrn=gap["mrn"], reason_code=d["reason_code"], rule=gap["rule"],
            task=d["task"], mode=d["mode"], policy_version=d["policy_version"], outcome=outcome,
            trigger_event=f"gap_event:{gap['gap_id']}", decision_inputs=d["inputs"],
            template_id=d["template"] if messages else None,
            template_version=messages[0]["template_version"] if messages else None,
            details={"action": d["action"], "card": gap["item_code"],
                     "messages": [m["message_id"] for m in messages]},
        )
        results.append({"gap_id": gap["gap_id"], "mrn": gap["mrn"], "rule": gap["rule"], "card": gap["item_code"],
                        "action": d["action"], "reason_code": d["reason_code"], "outcome": outcome})
    return results
