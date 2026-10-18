"""Patient follow-up status. Pause suspends the rules and the agent; "Agent off"
keeps the rules running but routes all contact to the coordinator. A change
away from Active needs a reason."""
from . import audit
from .agent import set_next_action
from .errors import Invalid, NotFound

SETTABLE = ("Active", "Paused", "Agent off")


def set_status(conn, mrn: str, user_id: str, status: str, reason: str | None) -> dict:
    patient = conn.execute("SELECT mrn, status FROM patient WHERE mrn = %s FOR UPDATE", (mrn,)).fetchone()
    if patient is None:
        raise NotFound(f"Patient {mrn} not found")
    if status not in SETTABLE:
        raise Invalid("status must be one of: " + ", ".join(SETTABLE))
    if status != "Active" and (not reason or not reason.strip()):
        raise Invalid("A reason is required")
    updated = conn.execute(
        "UPDATE patient SET status = %s, pause_until = NULL, pause_reason = CASE WHEN %s = 'Paused' THEN %s END "
        "WHERE mrn = %s RETURNING mrn, status",
        (status, status, (reason or "").strip() or None, mrn)).fetchone()
    audit.append(conn, actor_type="person", actor_name=user_id, action="patient_status_changed",
                 entity_type="patient", entity_id=mrn, mrn=mrn,
                 details={"from": patient["status"], "to": status, "reason": (reason or "").strip() or None})
    set_next_action(conn, mrn, {"Paused": "Paused: no rules or reminders until resumed",
                                "Agent off": "Agent off: the coordinator handles all contact",
                                "Active": "Routine follow-up"}[status])
    return updated


CALL_OUTCOMES = {
    "noans": "No answer", "book": "Spoke — will come; rebook the missed or due items",
    "concern": "Spoke — reported a concern", "away": "Spoke — away or unavailable until a date",
    "ok": "Spoke — all well, nothing to do", "wrong": "Wrong or unreachable number",
}


def log_call(conn, mrn: str, user_id: str, who: str, outcome: str, note: str | None) -> dict:
    """A coordinator logs a phone call. A reported concern opens a handover; a call that a handover
    is waiting for (call first, or the same-day urgent call) is recorded on it."""
    from . import clock, handover, messaging
    from .reference import USERS_BY_ID

    if conn.execute("SELECT 1 FROM patient WHERE mrn = %s", (mrn,)).fetchone() is None:
        raise NotFound(f"Patient {mrn} not found")
    if who not in ("patient", "caregiver"):
        raise Invalid("who must be patient or caregiver")
    if outcome not in CALL_OUTCOMES:
        raise Invalid("Unknown call outcome")
    note = (note or "").strip() or None
    if outcome == "concern" and not note:
        raise Invalid("Write down what the patient said")
    open_h = messaging.open_handover(conn, mrn)
    call = conn.execute(
        """
        INSERT INTO call_log (mrn, who, outcome, note, called_by, called_on, handover_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *
        """,
        (mrn, who, outcome, note, user_id, clock.get_today(conn), open_h["handover_id"] if open_h else None),
    ).fetchone()
    audit.append(conn, actor_type="person", actor_name=user_id, action="call_logged", entity_type="call_log",
                 entity_id=str(call["call_id"]), mrn=mrn, outcome=outcome, details={"who": who, "note": note})
    result = dict(call)
    user = USERS_BY_ID[user_id]
    if outcome == "concern":
        h, _ = handover.open_handover(conn, mrn, trigger="manual", actor_type="person", actor=user_id,
                                      patient_words=note, source=f"Phone call by {user['name']}")
        result["handover_id"] = h["handover_id"]
    elif open_h and outcome not in ("noans", "wrong"):
        from . import urgent_calls

        # Use Case 2c: the urgent call is logged on its own task (Log call attempt), not from a general call.
        waiting = open_h["stage"] == "calling" or (not urgent_calls.config(conn) and conn.execute(
            "SELECT 1 FROM coordinator_task WHERE handover_id = %s AND task_type = 'urgent_call' AND status = 'open'",
            (open_h["handover_id"],)).fetchone())
        if waiting:
            handover.record_call(conn, open_h["handover_id"], user,
                                 CALL_OUTCOMES[outcome] + (f": {note}" if note else ""))
    return result


def pause(conn, mrn: str, user_id: str, until, reason: str | None) -> dict:
    """Pause with an end date: stops reminders and suspends gap rules until then."""
    from datetime import date

    from . import clock

    try:
        end = until if isinstance(until, date) else date.fromisoformat(str(until))
    except ValueError:
        raise Invalid("Pause until must be a real date")
    if end <= clock.get_today(conn):
        raise Invalid("Pause until must be after today")
    updated = set_status(conn, mrn, user_id, "Paused", reason)
    conn.execute("UPDATE patient SET pause_until = %s WHERE mrn = %s", (end, mrn))
    set_next_action(conn, mrn, f"Paused until {end.strftime('%d/%m/%Y')}: no rules or reminders")
    return {**updated, "pause_until": end}


def resume_expired(conn) -> list[str]:
    """Morning run: a pause ends on its date; follow-up resumes automatically and is logged."""
    from . import clock

    today = clock.get_today(conn)
    rows = conn.execute("SELECT mrn FROM patient WHERE status = 'Paused' AND pause_until IS NOT NULL "
                        "AND pause_until <= %s", (today,)).fetchall()
    for r in rows:
        conn.execute("UPDATE patient SET status = 'Active', pause_until = NULL, pause_reason = NULL WHERE mrn = %s",
                     (r["mrn"],))
        audit.append(conn, actor_type="system", actor_name="CarePilot deadline check", action="pause_ended",
                     entity_type="patient", entity_id=r["mrn"], mrn=r["mrn"], details={"date": today.isoformat()})
        set_next_action(conn, r["mrn"], "Pause ended: routine follow-up resumed")
    return [r["mrn"] for r in rows]


def send_reminder(conn, mrn: str, user_id: str) -> dict:
    """'Send reminder': a person sends the approved template for the most pressing open card."""
    from . import clock, messaging
    from .errors import Conflict

    if conn.execute("SELECT 1 FROM patient WHERE mrn = %s", (mrn,)).fetchone() is None:
        raise NotFound(f"Patient {mrn} not found")
    card = conn.execute(
        "SELECT * FROM order_card WHERE mrn = %s AND state = 'Due' ORDER BY due_date LIMIT 1", (mrn,)).fetchone()
    if card is None:
        raise Conflict("Nothing to remind about: no open order card")
    overdue = card["due_date"] < clock.get_today(conn)
    return messaging.send_message(conn, mrn=mrn, template_id="missed_item_reminder" if overdue else "booking_reminder",
                                  actor_type="person", actor=user_id, reason_code="coordinator_reminder",
                                  card_id=card["card_id"])
