"""Use Case 2b: a patient asks a person for help — "Please help me book", "I can't come" or typed HELP.

No AI on this path. The agent chooses "escalate to coordinator" (a reason code, no message to anyone),
the system creates one task with an owner, a cover and a due date, and routine reminders to the patient
are held while it is open. CarePilot sends the patient nothing (REQ-14): the coordinator phones.
The coordinator logs call attempts and closes the task with a result; she closes the task, never the
order card. The card changes only when she records a booking with a date (REQ-18)."""
import unicodedata
from datetime import date, datetime, time

from . import audit, clock, messaging, policy, states
from .agent import AGENT, set_next_action
from .errors import Conflict, Invalid, NotFound

SYSTEM = "CarePilot"
TYPES = ("help_booking", "cant_come", "help_request")
REASON = {"help_booking": "patient_asked_help_booking", "cant_come": "patient_cannot_come", "help_request": "help_keyword"}
LABEL = {"help_booking": "Help booking", "cant_come": "Can't come", "help_request": "Help request"}
RESULTS = {
    "help_booking": ("booked", "could_not_book", "not_reached"),
    "cant_come": ("rebooked", "reason_recorded", "not_reached"),
    "help_request": ("sorted", "needs_follow_up", "not_reached"),
}
DEFAULT_REASONS = ["cost", "pre_authorisation", "transport", "work_family", "other"]
TRAILING = " \t.,!?;:।॥。！？"


def is_keyword(text: str | None, keywords) -> bool:
    """REQ-04: the whole message, trimmed, any case, Unicode NFC, trailing punctuation removed."""
    if not text:
        return False
    norm = lambda s: unicodedata.normalize("NFC", s).strip().casefold().rstrip(TRAILING).strip()
    return norm(text) in {norm(k) for k in keywords}


def task_hold_on(conn, mrn: str) -> bool:
    """REQ-08: routine reminders are held while a help task is open (a setting, decision 10)."""
    return messaging.task_hold(conn, mrn)


def escalate(conn, *, mrn: str, task_type: str, card: dict | None, inbound_id: int, actor: str) -> dict:
    """Agent decision + one task (or a suppressed duplicate). Returns {"task": ..., "created": bool}."""
    rules = policy.deadlines(conn)
    patient = conn.execute("SELECT status, agent_on FROM patient WHERE mrn = %s", (mrn,)).fetchone()
    open_task = conn.execute(
        "SELECT * FROM coordinator_task WHERE mrn = %s AND task_type = %s AND coalesce(card_id, 0) = %s "
        "AND status = 'open'", (mrn, task_type, card["card_id"] if card else 0)).fetchone()
    reason = REASON[task_type]
    if open_task:  # REQ-13: a repeat adds a line to the audit, never a second task
        audit.append(conn, actor_type="system", actor_name=actor, action="duplicate_suppressed", entity_type="task",
                     entity_id=str(open_task["task_id"]), mrn=mrn, reason_code=reason, outcome="task_already_open",
                     details={"task_type": task_type, "inbound_id": inbound_id})
        return {"task": open_task, "created": False}

    facts = {"card_state": card["state"] if card else None, "card": card["item_code"] if card else None,
             "open_task": False, "status": patient["status"], "agent_on": patient["agent_on"], "ai_used": False}
    audit.append(conn, actor_type="agent", actor_name=AGENT, action="agent_decision", entity_type="inbound_message",
                 entity_id=str(inbound_id), mrn=mrn, reason_code=reason, task="patient_help",
                 outcome="escalate_to_coordinator", trigger_event=f"inbound_message:{inbound_id}",
                 decision_inputs=facts, details={"action": "escalate to coordinator", "message_to_patient": None})

    # REQ-07: the card owner, then the cover; neither -> Unassigned (shown for the lead coordinator).
    owner = (card or {}).get("owner_name") or (card or {}).get("cover_name") or "Unassigned"
    cover = (card or {}).get("cover_name") if (card or {}).get("owner_name") else None
    days = rules["working_days"].get(task_type)
    today = clock.get_today(conn)
    due = policy.add_working_days(today, days) if days is not None else None
    what = card["name"] if card else "their follow-up"
    text = {"help_booking": f"Patient asked for help booking {what}",
            "cant_come": f"Patient says they cannot come for {what}",
            "help_request": "Patient sent HELP: call the patient"}[task_type]
    task = conn.execute(
        """
        INSERT INTO coordinator_task (mrn, task_type, reason, owner_name, cover_name, assigned_name, due_date,
                                      escalation, card_id, inbound_id, created_at_demo)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *
        """,
        (mrn, task_type, text, owner, cover, owner, due, rules["escalation"].get(task_type),
         card["card_id"] if card else None, inbound_id, clock.get_now(conn)),
    ).fetchone()
    hold = task_hold_on(conn, mrn)
    audit.append(conn, actor_type="system", actor_name=SYSTEM, action="task_created", entity_type="task",
                 entity_id=str(task["task_id"]), mrn=mrn, reason_code=reason,
                 outcome="hold_on" if hold else None,
                 details={"task_type": task_type, "owner": owner, "cover": cover, "due_date": due.isoformat() if due else None,
                          "card_id": task["card_id"], "reason": text, "hold": hold})
    set_next_action(conn, mrn, f"{LABEL[task_type]}: {owner} to call the patient by {due:%d %b %Y}" if due
                    else f"{LABEL[task_type]}: {owner} to call the patient")
    return {"task": task, "created": True}


def _load(conn, task_id: int) -> dict:
    t = conn.execute("SELECT * FROM coordinator_task WHERE task_id = %s FOR UPDATE", (task_id,)).fetchone()
    if t is None:
        raise NotFound(f"Task {task_id} not found")
    if t["task_type"] not in TYPES:
        raise Invalid("Call attempts and results are recorded on help-booking, can't-come and help-request tasks")
    return t


def attempts(conn, task_id: int) -> list[dict]:
    return conn.execute("SELECT * FROM task_call_attempt WHERE task_id = %s ORDER BY attempt_id", (task_id,)).fetchall()


def log_attempt(conn, task_id: int, user_id: str, result: str, note: str | None = None) -> dict:
    """REQ-17: one phone call — date, time, who, reached or no answer."""
    t = _load(conn, task_id)
    if t["status"] != "open":
        raise Conflict(f"Task {task_id} is already {t['status']}")
    if result not in ("reached", "no_answer"):
        raise Invalid("result must be reached or no_answer")
    row = conn.execute(
        "INSERT INTO task_call_attempt (task_id, called_at_demo, called_by, result, note) VALUES (%s, %s, %s, %s, %s) "
        "RETURNING *", (task_id, clock.get_now(conn), user_id, result, (note or "").strip() or None)).fetchone()
    n = len(attempts(conn, task_id))
    audit.append(conn, actor_type="person", actor_name=user_id, action="call_attempt_logged", entity_type="task",
                 entity_id=str(task_id), mrn=t["mrn"], outcome=result,
                 details={"task_type": t["task_type"], "attempt": n, "note": row["note"]})
    return row


def _parse(day, at):
    try:
        d = day if isinstance(day, date) else date.fromisoformat(str(day))
        tm = None if at in (None, "") else (at if isinstance(at, time) else time.fromisoformat(str(at)))
    except ValueError:
        raise Invalid("appointment_date must be YYYY-MM-DD and appointment_time HH:MM")
    return d, tm


def record_result(conn, task_id: int, user_id: str, *, result: str, note: str | None = None,
                  appointment_date=None, appointment_time=None, reason_code: str | None = None) -> dict:
    """Close a help task with a result (REQ-09 to REQ-11, REQ-17). Nothing closes without a result."""
    t = _load(conn, task_id)
    if t["status"] != "open":
        raise Conflict(f"Task {task_id} is already {t['status']}")
    if result not in RESULTS[t["task_type"]]:
        raise Invalid(f"A {LABEL[t['task_type']].lower()} task closes with: " + ", ".join(RESULTS[t["task_type"]]))
    rules = policy.deadlines(conn)
    note = (note or "").strip() or None
    today = clock.get_today(conn)
    appt_d = appt_t = None
    if result in ("booked", "rebooked"):
        if not appointment_date or (result == "booked" and not appointment_time):
            raise Invalid("Enter the appointment date" + (" and time" if result == "booked" else ""))
        appt_d, appt_t = _parse(appointment_date, appointment_time)
        if appt_d < today:
            raise Invalid(f"The appointment date is before today ({today})")
        if t["card_id"] is None:
            raise Invalid("This task has no visit card to book")
    if result == "could_not_book" and not note:
        raise Invalid("Write why the visit could not be booked")
    if result == "reason_recorded":
        allowed = rules.get("cant_come_reasons") or DEFAULT_REASONS
        if reason_code not in allowed:
            raise Invalid("Choose a reason: " + ", ".join(allowed))
    if t["task_type"] == "help_request" and not note:
        raise Invalid("Write a short note about the call")
    if result == "not_reached":
        need = int(rules.get("not_reached_after_attempts", 2))
        misses = sum(1 for a in attempts(conn, task_id) if a["result"] == "no_answer")
        if misses < need:
            raise Invalid(f"Not reached needs {need} call attempts with no answer ({misses} logged)")
        if not note:
            raise Invalid("Write a note for Not reached")

    audit.append(conn, actor_type="person", actor_name=user_id, action="task_result_recorded", entity_type="task",
                 entity_id=str(task_id), mrn=t["mrn"], outcome=result, reason_code=reason_code,
                 details={"task_type": t["task_type"], "note": note,
                          "appointment": f"{appt_d} {appt_t:%H:%M}" if appt_d and appt_t else (str(appt_d) if appt_d else None),
                          "attempts": len(attempts(conn, task_id))})
    if appt_d:
        _book_card(conn, t["card_id"], appt_d, appt_t, user_id)
    now = clock.get_now(conn)
    done = conn.execute(
        """
        UPDATE coordinator_task SET status = 'done', done_by = %s, done_note = %s, done_on = %s, done_at_demo = %s,
               result = %s, result_code = %s, appointment_date = %s, appointment_time = %s
        WHERE task_id = %s RETURNING *
        """,
        (user_id, note, today, now, result, reason_code if result == "reason_recorded" else None, appt_d, appt_t, task_id),
    ).fetchone()
    hold_was_on = policy.settings(conn).get("hold_on_task", False)
    still_held = task_hold_on(conn, t["mrn"])
    audit.append(conn, actor_type="system", actor_name=SYSTEM, action="task_done", entity_type="task",
                 entity_id=str(task_id), mrn=t["mrn"], outcome=result,
                 details={"task_type": t["task_type"], "closed_by": user_id, "result": result, "note": note,
                          "hold": "ended" if hold_was_on and not still_held else ("still on" if still_held else None)})
    set_next_action(conn, t["mrn"], {"booked": "Visit booked by the coordinator", "rebooked": "Visit rebooked by the coordinator",
                                     "not_reached": "Not reached after call attempts; visit still due"}.get(
                                         result, "Routine follow-up"))
    return {**done, "attempts": attempts(conn, task_id)}


def _book_card(conn, card_id: int, day: date, at: time | None, user_id: str) -> None:
    """REQ-09/10/12: the card becomes Booked (confirmed by coordinator) with the appointment date. A
    patient-reported booking is upgraded; a coordinator booking is never overwritten by a patient tap."""
    card = conn.execute("SELECT * FROM order_card WHERE card_id = %s FOR UPDATE", (card_id,)).fetchone()
    if card["state"] == "Due":
        states.transition_card(conn, card_id, "Booked", "system", SYSTEM, source="coordinator",
                               appointment_date=day.isoformat(), recorded_by=user_id)
    elif card["state"] == "Booked":
        conn.execute("UPDATE order_card SET booking_source = 'coordinator', appointment_date = %s WHERE card_id = %s",
                     (day, card_id))
        audit.append(conn, actor_type="system", actor_name=SYSTEM, action="card_booking_confirmed",
                     entity_type="order_card", entity_id=str(card_id), mrn=card["mrn"],
                     details={"from_source": card["booking_source"], "to_source": "coordinator",
                              "appointment_date": day.isoformat(), "recorded_by": user_id})
    else:
        raise Conflict(f"The card is already {card['state']}")
    conn.execute("UPDATE order_card SET appointment_time = %s WHERE card_id = %s", (at, card_id))


def tasks_for(conn, mrn: str) -> list[dict]:
    """Patient page Tasks section: every task with owner, cover, due, state (Open / Overdue / Done) and attempts."""
    today = clock.get_today(conn)
    rows = conn.execute(
        """SELECT t.*, c.name AS card_name, c.item_code FROM coordinator_task t
           LEFT JOIN order_card c USING (card_id) WHERE t.mrn = %s
           ORDER BY (t.status = 'open') DESC, t.task_id DESC""", (mrn,)).fetchall()
    out = []
    for t in rows:
        state = "Done" if t["status"] == "done" else ("Cancelled" if t["status"] == "cancelled"
                                                      else "Overdue" if t["due_date"] and t["due_date"] < today else "Open")
        out.append({**t, "state": state, "label": LABEL.get(t["task_type"]), "help_task": t["task_type"] in TYPES,
                    "attempts": attempts(conn, t["task_id"]) if t["task_type"] in TYPES else []})
    return out
