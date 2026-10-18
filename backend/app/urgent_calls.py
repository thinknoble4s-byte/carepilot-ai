"""Use Case 2c: the doctor picks Urgent — "Call the patient today", the call log and the cover.

Plain rules, no AI. The doctor's Urgent choice creates one call-today task at once (before the instruction
is typed), owner = the card owner, cover = the named cover, with two times of day from the settings: the
owner's deadline (16:00) and the cover's deadline (18:00). The coordinator phones outside CarePilot, logs
each call attempt and closes the task only with a reached call and a note; there is no "not reached" for
an urgent matter. At the owner's deadline the system moves the same task to the cover, once; at the cover's
deadline it shows Overdue and stays at the top. CarePilot never phones or messages the patient about it.
Active only in data sets whose settings carry "urgent_call" (Use Case 2c); elsewhere the Golden Path
same-day call task is unchanged."""
from datetime import datetime, time, timedelta

from . import audit, clock, help_tasks, policy
from .agent import set_next_action
from .errors import Conflict, Invalid, NotFound

SYSTEM = "CarePilot"
TYPE = "urgent_call"
LABEL = "Call the patient today"


def config(conn) -> dict | None:
    return policy.settings(conn).get("urgent_call")


def _at(day, hhmm: str) -> datetime:
    return datetime.combine(day, time.fromisoformat(hhmm))


def deadlines(cfg: dict, now: datetime) -> tuple[datetime, datetime]:
    """REQ-03: 16:00 and 18:00 on the day of the Urgent choice. A choice made after the owner's deadline
    (open point, section 15) gets two hours, capped at the cover's deadline; the cover always has at least
    that long."""
    owner, cover = _at(now.date(), cfg.get("owner_deadline", "16:00")), _at(now.date(), cfg.get("cover_deadline", "18:00"))
    if now >= owner:
        owner = now + timedelta(hours=2)
        if now < cover:
            owner = min(owner, cover)
        cover = max(cover, owner)
    return owner, cover


def _card_for(conn, h: dict) -> dict | None:
    """The patient's card behind the message that started the handover (the reminder's card)."""
    row = conn.execute(
        """SELECT c.* FROM inbound_message i JOIN outbox_message o ON o.message_id = i.in_reply_to
           JOIN order_card c ON c.card_id = o.card_id WHERE i.inbound_id = %s""", (h["inbound_id"],)).fetchone() \
        if h.get("inbound_id") else None
    return row or conn.execute("SELECT * FROM order_card WHERE mrn = %s ORDER BY card_id LIMIT 1",
                               (h["mrn"],)).fetchone()


def create(conn, h: dict, user: dict) -> dict:
    """REQ-01/05: exactly one call-today task per handover, created the moment Urgent is saved."""
    existing = conn.execute("SELECT * FROM coordinator_task WHERE handover_id = %s AND task_type = %s AND status = 'open'",
                            (h["handover_id"], TYPE)).fetchone()
    if existing:
        audit.append(conn, actor_type="system", actor_name=SYSTEM, action="duplicate_suppressed", entity_type="task",
                     entity_id=str(existing["task_id"]), mrn=h["mrn"], reason_code="urgent_call_today",
                     outcome="task_already_open", details={"handover_id": h["handover_id"]})
        return existing
    cfg, rules, now = config(conn), policy.deadlines(conn), clock.get_now(conn)
    card = _card_for(conn, h)
    named = conn.execute("SELECT coordinator FROM patient WHERE mrn = %s", (h["mrn"],)).fetchone()
    owner = (card or {}).get("owner_name") or named["coordinator"] or rules["default_coordinator"]
    cover = (card or {}).get("cover_name") if (card or {}).get("owner_name") else rules["covers"].get(owner)
    owner_dl, cover_dl = deadlines(cfg, now)
    task = conn.execute(
        """
        INSERT INTO coordinator_task (mrn, task_type, reason, owner_name, cover_name, assigned_name, due_date,
                                      escalation, handover_id, card_id, inbound_id, created_at_demo,
                                      owner_deadline_at, cover_deadline_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, %s, %s, %s, %s, %s, %s) RETURNING *
        """,
        (h["mrn"], TYPE, f"Urgent: {user['name']} asked for the patient to be called today", owner, cover, owner,
         owner_dl.date(), h["handover_id"], card["card_id"] if card else None, h.get("inbound_id"), now,
         owner_dl, cover_dl),
    ).fetchone()
    audit.append(conn, actor_type="system", actor_name=SYSTEM, action="task_created", entity_type="task",
                 entity_id=str(task["task_id"]), mrn=h["mrn"], reason_code="urgent_call_today",
                 outcome="hold_on" if help_tasks.task_hold_on(conn, h["mrn"]) else None,
                 details={"task_type": TYPE, "owner": owner, "cover": cover, "handover_id": h["handover_id"],
                          "owner_deadline": f"{owner_dl:%Y-%m-%d %H:%M}", "cover_deadline": f"{cover_dl:%Y-%m-%d %H:%M}",
                          "decided_by": user["user_id"], "ai_used": False, "message_to_patient": None})
    return task


def _load(conn, task_id: int) -> dict:
    t = conn.execute("SELECT * FROM coordinator_task WHERE task_id = %s FOR UPDATE", (task_id,)).fetchone()
    if t is None:
        raise NotFound(f"Task {task_id} not found")
    if t["task_type"] != TYPE:
        raise Invalid("This is not a call-today task")
    if t["status"] != "open":
        raise Conflict(f"Task {task_id} is already {t['status']}")
    return t


def attempts(conn, task_id: int) -> list[dict]:
    return help_tasks.attempts(conn, task_id)


def log_attempt(conn, task_id: int, user_id: str, result: str, note: str | None = None,
                appointment_date=None, appointment_time=None) -> dict:
    """REQ-06 to REQ-08. No answer: the attempt is saved and the task stays open (no limit, no "not reached").
    Reached: a note is required (what was passed on, what the patient said); an optional booking makes the
    doctor-requested visit card Booked (confirmed by coordinator). Saving closes the task as Done."""
    t = _load(conn, task_id)
    if result not in ("reached", "no_answer"):
        raise Invalid("result must be reached or no_answer")
    note = (note or "").strip() or None
    now, today = clock.get_now(conn), clock.get_today(conn)
    appt_d = appt_t = None
    if result == "reached":
        if not note:
            raise Invalid("Write what you passed on and what the patient said")
        if appointment_time and not appointment_date:
            raise Invalid("Enter the appointment date with the time")
        if appointment_date:
            appt_d, appt_t = help_tasks._parse(appointment_date, appointment_time)
            if appt_d < today:
                raise Invalid(f"The appointment date is before today ({today})")
            h = conn.execute("SELECT instruction_card FROM handover WHERE handover_id = %s", (t["handover_id"],)).fetchone()
            if not h or not h["instruction_card"]:
                raise Invalid("Record booking needs the doctor's instruction first: the visit card does not exist yet")
    row = conn.execute(
        "INSERT INTO task_call_attempt (task_id, called_at_demo, called_by, result, note) VALUES (%s, %s, %s, %s, %s) "
        "RETURNING *", (task_id, now, user_id, result, note)).fetchone()
    n = len(attempts(conn, task_id))
    audit.append(conn, actor_type="person", actor_name=user_id, action="call_attempt_logged", entity_type="task",
                 entity_id=str(task_id), mrn=t["mrn"], outcome=result,
                 details={"task_type": TYPE, "attempt": n, "note": note})
    if result == "no_answer":
        return {**row, "task_status": "open"}

    if appt_d:
        audit.append(conn, actor_type="person", actor_name=user_id, action="booking_recorded", entity_type="task",
                     entity_id=str(task_id), mrn=t["mrn"],
                     details={"appointment": f"{appt_d} {appt_t:%H:%M}" if appt_t else str(appt_d),
                              "card_id": h["instruction_card"], "message_to_patient": None})
        help_tasks._book_card(conn, h["instruction_card"], appt_d, appt_t, user_id)
        for b in conn.execute("SELECT task_id FROM coordinator_task WHERE card_id = %s AND status = 'open' "
                              "AND task_type = 'booking_from_instruction'", (h["instruction_card"],)).fetchall():
            conn.execute("UPDATE coordinator_task SET status = 'done', done_by = %s, done_note = %s, done_on = %s, "
                         "done_at_demo = %s WHERE task_id = %s",
                         (user_id, "Booked on the urgent call", today, now, b["task_id"]))
    minutes = int((now - t["created_at_demo"]).total_seconds() // 60) if t["created_at_demo"] else None
    done = conn.execute(
        """
        UPDATE coordinator_task SET status = 'done', done_by = %s, done_note = %s, done_on = %s, done_at_demo = %s,
               result = 'reached', appointment_date = %s, appointment_time = %s, minutes_to_call = %s
        WHERE task_id = %s RETURNING *
        """, (user_id, note, today, now, appt_d, appt_t, minutes, task_id)).fetchone()
    still_held = help_tasks.task_hold_on(conn, t["mrn"])
    audit.append(conn, actor_type="system", actor_name=SYSTEM, action="task_done", entity_type="task",
                 entity_id=str(task_id), mrn=t["mrn"], outcome="reached",
                 details={"task_type": TYPE, "reached_by": user_id, "minutes_to_call": minutes, "note": note,
                          "booked": appt_d is not None,
                          "hold": "still on" if still_held else ("ended" if policy.settings(conn).get("hold_on_task") else None)})
    set_next_action(conn, t["mrn"], f"Patient reached ({minutes} minutes after the Urgent choice); "
                                    "close the handover with a reason")
    return {**row, "task_status": "done", "task": done, "minutes_to_call": minutes}


def pass_to_cover(conn, task_id: int, user_id: str, reason: str | None) -> dict:
    """REQ-09: by hand, with a short reason; the same task moves at once, attempts kept."""
    t = _load(conn, task_id)
    if not reason or not reason.strip():
        raise Invalid("Write why you are passing the call to the cover")
    if t["moved_at_demo"] is not None:
        raise Conflict(f"Already moved to {t['assigned_name']}")
    if not t["cover_name"] or t["cover_name"] == t["assigned_name"]:
        raise Conflict("There is no cover to pass this call to")
    return _move(conn, t, actor_type="person", actor=user_id, reason=reason.strip(), at=clock.get_now(conn))


def _move(conn, t: dict, *, actor_type: str, actor: str, reason: str, at) -> dict:
    """at: when it moved — now for Pass to cover, the owner's deadline for the system step-in."""
    now, cover = at, t["cover_name"]
    moved = conn.execute(
        """UPDATE coordinator_task SET owner_name = %s, assigned_name = %s, moved_from = %s, moved_at_demo = %s,
                  moved_by = %s, moved_reason = %s, escalated_on = %s, due_date = %s WHERE task_id = %s RETURNING *""",
        (cover, cover, t["assigned_name"], now, actor, reason, now.date(),
         (t["cover_deadline_at"] or now).date(), t["task_id"])).fetchone()
    audit.append(conn, actor_type=actor_type, actor_name=actor, action="task_moved_to_cover", entity_type="task",
                 entity_id=str(t["task_id"]), mrn=t["mrn"], reason_code="urgent_moved_to_cover",
                 details={"task_type": TYPE, "from": t["assigned_name"], "to": cover, "reason": reason,
                          "cover_deadline": f"{t['cover_deadline_at']:%Y-%m-%d %H:%M}" if t["cover_deadline_at"] else None,
                          "attempts_kept": len(attempts(conn, t["task_id"])), "message_to_patient": None})
    set_next_action(conn, t["mrn"], f"Urgent call moved to {cover} (cover), from {t['assigned_name']}"
                    + (f"; by {t['cover_deadline_at']:%H:%M}" if t["cover_deadline_at"] else ""))
    return moved


def sweep(conn) -> list[dict]:
    """REQ-10/12, run whenever the demo clock moves: at the owner's deadline the same task moves to the cover
    (once); at the cover's deadline it shows Overdue. Never closed by itself, never moved again."""
    if not config(conn):
        return []
    now, out = clock.get_now(conn), []
    for t in conn.execute(
            """SELECT * FROM coordinator_task WHERE task_type = %s AND status = 'open' AND moved_at_demo IS NULL
               AND owner_deadline_at <= %s ORDER BY task_id FOR UPDATE""", (TYPE, now)).fetchall():
        if t["cover_name"] and t["cover_name"] != t["assigned_name"]:
            _move(conn, t, actor_type="system", actor=SYSTEM,
                  reason=f"No reached call logged by {t['owner_deadline_at']:%H:%M}", at=t["owner_deadline_at"])
            out.append({"task_id": t["task_id"], "mrn": t["mrn"], "action": "moved_to_cover", "to": t["cover_name"]})
    for t in conn.execute(
            """SELECT * FROM coordinator_task WHERE task_type = %s AND status = 'open' AND overdue_at_demo IS NULL
               AND cover_deadline_at <= %s ORDER BY task_id FOR UPDATE""", (TYPE, now)).fetchall():
        conn.execute("UPDATE coordinator_task SET overdue_at_demo = %s WHERE task_id = %s",
                     (t["cover_deadline_at"], t["task_id"]))
        audit.append(conn, actor_type="system", actor_name=SYSTEM, action="task_overdue", entity_type="task",
                     entity_id=str(t["task_id"]), mrn=t["mrn"], reason_code="urgent_call_today", outcome="overdue",
                     details={"task_type": TYPE, "text": f"Overdue after {t['cover_deadline_at']:%H:%M}",
                              "owner": t["assigned_name"], "attempts": len(attempts(conn, t["task_id"]))})
        set_next_action(conn, t["mrn"], f"Urgent call overdue: {t['assigned_name']} has not reached the patient")
        out.append({"task_id": t["task_id"], "mrn": t["mrn"], "action": "overdue"})
    return out


def state(t: dict) -> str:
    if t["status"] == "done":
        return "Done"
    if t["status"] == "cancelled":
        return "Cancelled"
    if t.get("overdue_at_demo"):
        return "Overdue"
    if t.get("moved_at_demo"):
        return "Moved to cover"
    return "Open"


def call_line(conn, handover_id: int) -> str | None:
    """REQ-17: the doctor's read-only line on the call."""
    t = conn.execute("SELECT * FROM coordinator_task WHERE handover_id = %s AND task_type = %s "
                     "ORDER BY task_id DESC LIMIT 1", (handover_id, TYPE)).fetchone()
    if t is None:
        return None
    if t["status"] == "done":
        from .reference import USERS_BY_ID

        who = USERS_BY_ID.get(t["done_by"], {}).get("name", t["done_by"])
        return f"Reached by {who} at {t['done_at_demo']:%H:%M}" if t["done_at_demo"] else f"Reached by {who}"
    if t["moved_at_demo"]:
        return "Moved to cover"
    return "Call pending"
