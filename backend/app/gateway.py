"""Inbound WhatsApp replies (simulated channel gateway).

Buttons are structured, so they update the workflow directly with no AI call.
Free text is never acted on: it goes to a person. Until the Sarvam health-matter
flag arrives (Increment 4), there is no classifier, and "no classifier" counts
as uncertain, so free text takes the safe path: handover plus the fixed
acknowledgement. HELP (or ಸಹಾಯ / मदद) always reaches a person directly.
"""
from . import audit, clock, handover, messaging, policy, states, tasks
from .agent import AGENT, set_next_action
from .errors import Invalid, NotFound

GATEWAY = "WhatsApp gateway (simulated)"
BUTTONS = ("booked", "help_book", "cant_come")


def receive_reply(conn, mrn: str, *, from_role: str = "patient", button: str | None = None,
                  text: str | None = None, in_reply_to: int | None = None) -> dict:
    if conn.execute("SELECT 1 FROM patient WHERE mrn = %s", (mrn,)).fetchone() is None:
        raise NotFound(f"Patient {mrn} not found")
    if from_role not in ("patient", "caregiver"):
        raise Invalid("from_role must be patient or caregiver")
    if button is not None and button not in BUTTONS:
        raise Invalid("button must be one of: " + ", ".join(BUTTONS))
    text = (text or "").strip() or None
    if button is None and text is None:
        raise Invalid("A reply needs a button or some text")
    if in_reply_to is not None:
        target = conn.execute("SELECT * FROM outbox_message WHERE message_id = %s AND mrn = %s AND status = 'sent'",
                              (in_reply_to, mrn)).fetchone()
        if target is None:
            raise NotFound(f"Message {in_reply_to} was not sent to {mrn}")
    else:
        target = conn.execute(
            "SELECT * FROM outbox_message WHERE mrn = %s AND status = 'sent' AND card_id IS NOT NULL "
            "ORDER BY message_id DESC LIMIT 1", (mrn,)).fetchone()

    today = clock.get_today(conn)
    inbound = conn.execute(
        """
        INSERT INTO inbound_message (mrn, from_role, in_reply_to, button, text, handling, received_on)
        VALUES (%s, %s, %s, %s, %s, 'received', %s) RETURNING *
        """,
        (mrn, from_role, target["message_id"] if target else None, button, text, today),
    ).fetchone()
    source = f"WhatsApp reply #{inbound['inbound_id']} from {from_role}, {today.isoformat()}"
    audit.append(conn, actor_type="system", actor_name=GATEWAY, action="reply_received", entity_type="inbound_message",
                 entity_id=str(inbound["inbound_id"]), mrn=mrn,
                 details={"button": button, "text": text, "from_role": from_role,
                          "in_reply_to": target["message_id"] if target else None})

    handling, ho = [], None
    if not policy.settings(conn)["act_on_replies"]:
        # Use Case 1: the reply is kept in the message history and CarePilot takes no action.
        conn.execute("UPDATE inbound_message SET handling = 'saved_no_action' WHERE inbound_id = %s",
                     (inbound["inbound_id"],))
        return {"inbound_id": inbound["inbound_id"], "handling": ["saved_no_action"], "handover_id": None}
    if button:
        handling.append(_apply_button(conn, mrn, button, target, inbound["inbound_id"]))
    if text:
        keywords = [k.lower() for k in policy.deadlines(conn)["help_keywords"]]
        trigger = "help_keyword" if text.lower() in keywords else "unclassified_reply"
        ho, created = handover.open_handover(conn, mrn, trigger=trigger, actor_type="system", actor=GATEWAY,
                                             patient_words=text, source=source)
        handling.append(f"handover_{'opened' if created else 'updated'}:{trigger}")
        if created:
            messaging.send_message(conn, mrn=mrn, template_id="handover_ack", actor_type="system", actor=GATEWAY,
                                   reason_code=f"ack_{trigger}", recipient=from_role,
                                   handover_id=ho["handover_id"])
    conn.execute("UPDATE inbound_message SET handling = %s, handover_id = %s WHERE inbound_id = %s",
                 ("; ".join(handling), ho["handover_id"] if ho else None, inbound["inbound_id"]))
    return {"inbound_id": inbound["inbound_id"], "handling": handling,
            "handover_id": ho["handover_id"] if ho else None}


def _apply_button(conn, mrn: str, button: str, target: dict | None, inbound_id: int) -> str:
    """Deterministic: no LLM call. Logged as the agent's live 'log_button_reply' task."""
    card = conn.execute("SELECT * FROM order_card WHERE card_id = %s",
                        (target["card_id"],)).fetchone() if target and target["card_id"] else None
    label = card["item_code"] if card else "no card"
    if button == "booked" and card and card["card_type"] == "visit" and card["state"] == "Due":
        states.transition_card(conn, card["card_id"], "Booked", "agent", AGENT, source="button_tap")
        outcome = "card_booked"
        set_next_action(conn, mrn, f"{card['item_code']} booked; attendance comes from the appointment list")
    elif button == "booked" and card and card["state"] == "Due":
        tasks.create_task(conn, mrn=mrn, task_type="confirm_test_booking", card_id=card["card_id"],
                          reason=f"Patient says {card['name']} is booked: add the appointment date",
                          actor_type="agent", actor=AGENT, dedupe_key=f"testbooked:{card['card_id']}")
        outcome = "test_booking_to_confirm"
        set_next_action(conn, mrn, f"Coordinator to confirm the {card['item_code']} appointment date")
    elif button == "booked":
        outcome = "already_progressed" if card else "no_card"
    else:
        task_type = "booking_help" if button == "help_book" else "cannot_attend"
        tasks.create_task(conn, mrn=mrn, task_type=task_type, card_id=card["card_id"] if card else None,
                          reason=("Patient asked for help booking " if button == "help_book"
                                  else "Patient says they cannot come for ") + (card["name"] if card else "a visit"),
                          actor_type="agent", actor=AGENT, dedupe_key=f"{button}:{inbound_id}")
        outcome = f"task_{task_type}"
        set_next_action(conn, mrn, "Coordinator to help book" if button == "help_book"
                        else "Coordinator to follow up: patient cannot come")
    audit.append(conn, actor_type="agent", actor_name=AGENT, action="agent_decision", entity_type="inbound_message",
                 entity_id=str(inbound_id), mrn=mrn, reason_code=f"button_{button}", task="log_button_reply",
                 mode="live", outcome=outcome, trigger_event=f"inbound_message:{inbound_id}",
                 details={"card": label})
    return f"button_{button}:{outcome}"
