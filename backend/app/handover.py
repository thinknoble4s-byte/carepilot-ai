"""The human safety path (Golden Path Steps 6-8).

A handover stops all automated messages to the patient until a named person
closes it with a reason. Gate 1: the coordinator chooses send to doctor, call
first, or nothing clinical. Gate 2: the doctor explicitly chooses Urgent or
Routine (nothing pre-selected, no AI suggestion). Gate 3: the doctor records
the instruction, which can create a new order card and a booking message.
Each gate has a deadline that escalates to the named cover.
"""
from datetime import timedelta

from . import audit, clock, messaging, policy, tasks, urgent_calls
from .agent import set_next_action
from .errors import Conflict, Invalid, NotFound
from .reference import USERS_BY_ID

GATE1_CHOICES = ("send_to_doctor", "call_first", "nothing_clinical")
GATE2_CHOICES = ("urgent", "routine")


def _load(conn, handover_id: int) -> dict:
    h = conn.execute("SELECT * FROM handover WHERE handover_id = %s FOR UPDATE", (handover_id,)).fetchone()
    if h is None:
        raise NotFound(f"Handover {handover_id} not found")
    return h


def _stage(conn, h: dict, stage: str, **fields) -> dict:
    sets = ", ".join(f"{k} = %({k})s" for k in fields)
    return conn.execute(
        f"UPDATE handover SET stage = %(stage)s{', ' + sets if sets else ''} WHERE handover_id = %(id)s RETURNING *",
        {"stage": stage, "id": h["handover_id"], **fields},
    ).fetchone()


def _due(task: dict) -> str:
    return task["due_date"].strftime("%d/%m/%Y") if task.get("due_date") else "today"


def _record_review(conn, h: dict, user: dict) -> int | None:
    """REQ-10: minutes from the agent stopping to the coordinator reviewing (first decision), demo clock."""
    if h["reviewed_at_demo"] is not None or h["opened_at_demo"] is None:
        return h["response_minutes"]
    now = clock.get_now(conn)
    minutes = int((now - h["opened_at_demo"]).total_seconds() // 60)
    conn.execute("UPDATE handover SET reviewed_at_demo = %s, reviewed_by = %s, response_minutes = %s "
                 "WHERE handover_id = %s", (now, user["user_id"], minutes, h["handover_id"]))
    return minutes


def open_handover(conn, mrn: str, *, trigger: str, actor_type: str, actor: str, patient_words: str | None = None,
                  source: str | None = None, translation: str | None = None, inbound_id: int | None = None,
                  ai_failed: bool = False) -> tuple[dict, bool]:
    """Open a handover, or return the one already open (no duplicates). Returns (handover, created)."""
    if conn.execute("SELECT 1 FROM patient WHERE mrn = %s FOR UPDATE", (mrn,)).fetchone() is None:
        raise NotFound(f"Patient {mrn} not found")
    existing = messaging.open_handover(conn, mrn)
    if existing:
        if patient_words:
            conn.execute(
                "UPDATE handover SET patient_words = concat_ws(E'\\n', patient_words, %s::text) WHERE handover_id = %s",
                (patient_words, existing["handover_id"]))
            audit.append(conn, actor_type=actor_type, actor_name=actor, action="handover_reply_added",
                         entity_type="handover", entity_id=str(existing["handover_id"]), mrn=mrn,
                         details={"source": source})
        return existing, False
    now = clock.get_now(conn)
    h = conn.execute(
        """
        INSERT INTO handover (mrn, trigger, patient_words, source, opened_by, opened_on, translation, inbound_id,
                              ai_failed, opened_at_demo)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *
        """,
        (mrn, trigger, patient_words, source, actor, now.date(), translation, inbound_id, ai_failed, now),
    ).fetchone()
    task = tasks.create_task(conn, mrn=mrn, task_type="coordinator_review", handover_id=h["handover_id"],
                             reason=f"Gate 1: review the handover ({trigger.replace('_', ' ')})",
                             actor_type=actor_type, actor=actor)
    audit.append(conn, actor_type=actor_type, actor_name=actor, action="handover_opened", entity_type="handover",
                 entity_id=str(h["handover_id"]), mrn=mrn, reason_code=trigger, trigger_event=source,
                 outcome="send_block_on", details={"patient_words": patient_words, "ai_failed": ai_failed})
    set_next_action(conn, mrn, f"Coordinator review (Gate 1) by {_due(task)}; automated messages stopped")
    return h, True


def gate1(conn, handover_id: int, user: dict, choice: str | None) -> dict:
    h = _load(conn, handover_id)
    if h["stage"] != "awaiting_coordinator":
        raise Conflict(f"Handover {handover_id} is not waiting for the coordinator (stage: {h['stage']})")
    if choice not in GATE1_CHOICES:
        raise Invalid("Choose one: " + ", ".join(GATE1_CHOICES))
    tasks.complete_open(conn, handover_id, "coordinator_review", user["user_id"], f"Gate 1: {choice}")
    minutes = _record_review(conn, h, user)
    if choice == "send_to_doctor":
        rules = policy.deadlines(conn)
        doctor = USERS_BY_ID[rules["default_doctor"]]
        task = tasks.create_task(conn, mrn=h["mrn"], task_type="doctor_decision", owner_name=doctor["name"],
                                 handover_id=handover_id, reason="Gate 2: decide Urgent or Routine",
                                 actor_type="person", actor=user["user_id"])
        h = _stage(conn, h, "awaiting_doctor", gate1_choice=choice, gate1_by=user["user_id"], doctor=doctor["user_id"],
                   gate1_on=clock.get_today(conn))
        next_action = f"Doctor decision (Gate 2) by {_due(task)}, {doctor['name']}"
    elif choice == "call_first":
        task = tasks.create_task(conn, mrn=h["mrn"], task_type="call_first", handover_id=handover_id,
                                 reason="Call the patient before deciding", actor_type="person",
                                 actor=user["user_id"])
        h = _stage(conn, h, "calling", gate1_choice=choice, gate1_by=user["user_id"], gate1_on=clock.get_today(conn))
        next_action = f"Coordinator to call the patient by {_due(task)}"
    else:
        h = _stage(conn, h, "ready_to_close", gate1_choice=choice, gate1_by=user["user_id"],
                   gate1_on=clock.get_today(conn))
        next_action = "Coordinator to close the handover with a reason"
    audit.append(conn, actor_type="person", actor_name=user["user_id"], action="gate1_decided", entity_type="handover",
                 entity_id=str(handover_id), mrn=h["mrn"], outcome=choice,
                 details={"handover_response_minutes": minutes})
    set_next_action(conn, h["mrn"], next_action)
    return h


def record_call(conn, handover_id: int, user: dict, note: str | None) -> dict:
    """Record a phone call: either the 'call first' call or the same-day urgent call."""
    h = _load(conn, handover_id)
    if not note or not note.strip():
        raise Invalid("Describe the call outcome")
    urgent = conn.execute(
        "SELECT task_id FROM coordinator_task WHERE handover_id = %s AND task_type = 'urgent_call' AND status = 'open'",
        (handover_id,)).fetchone()
    if urgent and urgent_calls.config(conn):
        raise Conflict("Log the urgent call on its task: Log call attempt, then reached with a note")
    if urgent:
        tasks.complete_task(conn, urgent["task_id"], user["user_id"], note.strip())
        conn.execute("UPDATE handover SET called_on = %s WHERE handover_id = %s", (clock.get_today(conn), handover_id))
        kind, next_action = "urgent_call", None
    elif h["stage"] == "calling":
        tasks.complete_open(conn, handover_id, "call_first", user["user_id"], note.strip())
        task = tasks.create_task(conn, mrn=h["mrn"], task_type="coordinator_review", handover_id=handover_id,
                                 reason="Gate 1 after the call", actor_type="person", actor=user["user_id"])
        h = _stage(conn, h, "awaiting_coordinator", call_note=note.strip())
        kind, next_action = "call_first", f"Coordinator decision after the call (Gate 1) by {_due(task)}"
    else:
        raise Conflict("No call is due for this handover")
    audit.append(conn, actor_type="person", actor_name=user["user_id"], action="patient_called", entity_type="handover",
                 entity_id=str(handover_id), mrn=h["mrn"], task=kind, details={"note": note.strip()})
    if next_action:
        set_next_action(conn, h["mrn"], next_action)
    elif h["stage"] == "ready_to_close":
        set_next_action(conn, h["mrn"], "Coordinator to close the handover with a reason")
    return h


def gate2(conn, handover_id: int, user: dict, decision: str | None) -> dict:
    h = _load(conn, handover_id)
    if h["stage"] != "awaiting_doctor":
        raise Conflict(f"Handover {handover_id} is not waiting for a doctor's decision (stage: {h['stage']})")
    if decision not in GATE2_CHOICES:
        raise Invalid("The doctor must explicitly choose Urgent or Routine")
    tasks.complete_open(conn, handover_id, "doctor_decision", user["user_id"], f"Gate 2: {decision}")
    task = tasks.create_task(conn, mrn=h["mrn"], task_type="doctor_instruction", owner_name=user["name"],
                             handover_id=handover_id, reason="Gate 3: record the instruction in the hospital record",
                             actor_type="person", actor=user["user_id"])
    next_action = f"Doctor's instruction (Gate 3) by {_due(task)}"
    h = _stage(conn, h, "awaiting_instruction", gate2_decision=decision, gate2_by=user["user_id"],
               gate2_on=clock.get_today(conn), gate2_at_demo=clock.get_now(conn))
    audit.append(conn, actor_type="person", actor_name=user["user_id"], action="gate2_decided", entity_type="handover",
                 entity_id=str(handover_id), mrn=h["mrn"], outcome=decision)
    if decision == "urgent" and urgent_calls.config(conn):
        # Use Case 2c REQ-01: the call-today task is created at once, before the instruction is typed.
        call = urgent_calls.create(conn, h, user)
        next_action = f"Urgent: {call['assigned_name']} to call the patient today by " \
                      f"{call['owner_deadline_at']:%H:%M}; {next_action}"
    elif decision == "urgent":
        call = tasks.create_task(conn, mrn=h["mrn"], task_type="urgent_call", handover_id=handover_id,
                                 reason="Urgent: call the patient today", actor_type="person", actor=user["user_id"])
        next_action = f"Urgent call to the patient today ({call['assigned_name']}); {next_action}"
    set_next_action(conn, h["mrn"], next_action)
    return h


def record_instruction(conn, handover_id: int, user: dict, text: str | None, days=None) -> dict:
    """Gate 3 (REQ-12): the doctor's instruction in the SIMULATED hospital record: text + number of days.
    Rules create one neutral "Doctor-requested visit" card (no clinical words in card lists): due = decision
    day + days, booking deadline = decision day + 2 working days, owner and cover from the settings. No G0
    runs on it (REQ-13); its own booking message goes out the same day (REQ-14)."""
    h = _load(conn, handover_id)
    if h["stage"] != "awaiting_instruction":
        raise Conflict(f"Handover {handover_id} is not waiting for an instruction (stage: {h['stage']})")
    if not text or not text.strip():
        raise Invalid("The instruction text is required")
    if days is not None and (not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 365):
        raise Invalid("days must be a whole number from 1 to 365")
    audit.append(conn, actor_type="person", actor_name=user["user_id"], action="instruction_recorded",
                 entity_type="handover", entity_id=str(handover_id), mrn=h["mrn"],
                 details={"instruction": text.strip(), "days": days, "source": "SIMULATED hospital record"})
    card = None
    if days is not None:
        card = create_instruction_card(conn, h["mrn"], days, handover_id=handover_id)
        # Use Case 2c: the call task follows the new card, so Record booking books the doctor-requested visit.
        conn.execute("UPDATE coordinator_task SET card_id = %s WHERE handover_id = %s AND task_type = 'urgent_call' "
                     "AND status = 'open'", (card["card_id"], handover_id))
    tasks.complete_open(conn, handover_id, "doctor_instruction", user["user_id"], text.strip())
    h = _stage(conn, h, "ready_to_close", instruction=text.strip(), instruction_by=user["user_id"],
               instruction_card=card["card_id"] if card else None, instruction_on=clock.get_today(conn))
    pending_call = conn.execute(
        "SELECT 1 FROM coordinator_task WHERE handover_id = %s AND task_type = 'urgent_call' AND status = 'open'",
        (handover_id,)).fetchone()
    set_next_action(conn, h["mrn"], "Urgent call still to be recorded, then close the handover" if pending_call
                    else "Coordinator to close the handover with a reason")
    return h


def create_instruction_card(conn, mrn: str, days: int, *, handover_id: int | None = None,
                            review_id: int | None = None) -> dict:
    """One neutral "Doctor-requested visit" card from a doctor's instruction (UC2a REQ-12/13, UC2T REQ-17): Due,
    due = decision day + days, booking task due in 2 working days, owner and cover from the settings, no G0.
    The approved booking message goes out the same day, allowed through an open handover (REQ-06/14)."""
    today = clock.get_today(conn)
    spec = policy.settings(conn).get("instruction_card") or {}
    name = spec.get("name", "Doctor-requested visit")
    plan = conn.execute("SELECT care_plan_id FROM care_plan WHERE mrn = %s", (mrn,)).fetchone()
    n = conn.execute("SELECT count(*) AS n FROM order_card WHERE mrn = %s AND origin = 'instruction'",
                     (mrn,)).fetchone()["n"]
    card = conn.execute(
        """
        INSERT INTO order_card (mrn, care_plan_id, item_code, card_type, name, department, due_date, state,
                                owner_name, cover_name, origin, handover_id)
        VALUES (%s, %s, %s, 'visit', %s, %s, %s, 'Due', %s, %s, 'instruction', %s) RETURNING *
        """,
        (mrn, plan["care_plan_id"] if plan else None, f"X{n + 1}", name, spec.get("department", "Oncology OPD"),
         today + timedelta(days=days), spec.get("owner", "Priya Nair"), spec.get("cover", "Meera Joseph"),
         handover_id),
    ).fetchone()
    audit.append(conn, actor_type="system", actor_name="system", action="card_created", entity_type="order_card",
                 entity_id=str(card["card_id"]), mrn=mrn,
                 details={"card": card["item_code"], "name": name, "due_date": card["due_date"].isoformat(),
                          "owner": card["owner_name"], "cover": card["cover_name"], "origin": "instruction",
                          "handover_id": handover_id, "report_review_id": review_id})
    tasks.create_task(conn, mrn=mrn, task_type="booking_from_instruction", owner_name=card["owner_name"],
                      card_id=card["card_id"], handover_id=handover_id,
                      reason=f"Book the {name} by {card['due_date'].strftime('%d/%m/%Y')} (doctor's instruction)",
                      actor_type="system", actor="system")
    messaging.send_message(conn, mrn=mrn, template_id="instruction_booking", actor_type="agent",
                           actor="CarePilot agent", reason_code="instruction_booking", card_id=card["card_id"],
                           handover_id=handover_id)
    return card


def close(conn, handover_id: int, user: dict, reason: str | None) -> dict:
    """Only a named person closes, with a reason; routine follow-up resumes after this."""
    h = _load(conn, handover_id)
    if h["stage"] == "closed":
        raise Conflict(f"Handover {handover_id} is already closed")
    if not reason or not reason.strip():
        raise Invalid("Closing needs a recorded reason")
    if h["stage"] != "ready_to_close":
        raise Conflict(f"Not ready to close: the handover is at stage {h['stage']}")
    if conn.execute("SELECT 1 FROM coordinator_task WHERE handover_id = %s AND task_type = 'urgent_call' "
                    "AND status = 'open'", (handover_id,)).fetchone():
        raise Conflict("Call task still open: the urgent call has not reached the patient yet")
    h = _stage(conn, h, "closed", closed_by=user["user_id"], closed_reason=reason.strip(),
               closed_on=clock.get_today(conn))
    audit.append(conn, actor_type="person", actor_name=user["user_id"], action="handover_closed",
                 entity_type="handover", entity_id=str(handover_id), mrn=h["mrn"], outcome="closed_send_block_off",
                 details={"reason": reason.strip()})
    set_next_action(conn, h["mrn"], "Routine follow-up resumed")
    return h
