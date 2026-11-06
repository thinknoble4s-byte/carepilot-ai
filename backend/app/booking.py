"""Patient self-booking from a WhatsApp reminder (5 Nov). No AI on this path: every tap is a fact.

- "{item} booked": the card moves Due -> Booked, dated the day of the tap (source patient_button).
- "Booked elsewhere" (a test): the card moves Due -> Booked, labelled "Booked outside", dated the day of the tap
  (source patient_elsewhere).
- "Please help me book": CarePilot offers four appointment slots as buttons. The slot she taps books the card with
  that date and time (source patient_slot) and she gets a confirmation message. If the offer cannot be sent (no
  consent for automated messages, a ticket open), a coordinator gets the help-booking task instead.

Every booking is written to the appointment list (Intake & verification) and closes the card's reminder gap.
"""
import random
from datetime import date, time, timedelta

from . import audit, clock, help_tasks, messaging, states
from .agent import AGENT, set_next_action

SLOT_BUTTONS = ("slot_1", "slot_2", "slot_3", "slot_4")
SLOT_TIMES = ("09:00", "09:30", "10:00", "10:30", "11:00", "11:30", "14:00", "14:30", "15:00", "15:30", "16:00")
SLOT_DAYS = 10  # the slots fall on working days (Mon to Fri) within the next two weeks
BOOKED_OUTSIDE = "Booked outside"


def _fmt(day: date, at: time | None) -> str:
    return f"{day:%d/%m/%Y}" + (f" {at:%H:%M}" if at else "")


def book(conn, card: dict, *, source: str, day: date, at: time | None, inbound_id: int, actor: str) -> dict:
    """Due -> Booked with the date (and time), the appointment-list row, the reminder gap closed."""
    states.transition_card(conn, card["card_id"], "Booked", "system", actor, source=source,
                           appointment_date=day.isoformat(), inbound_id=inbound_id)
    label = BOOKED_OUTSIDE if source == "patient_elsewhere" else None
    conn.execute("UPDATE order_card SET appointment_time = %s, label = %s, patient_says_booked = false "
                 "WHERE card_id = %s", (at, label, card["card_id"]))
    appt = conn.execute(
        """INSERT INTO appointment (mrn, appointment_date, appointment_time, department, card_id, item, source)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (card_id) WHERE card_id IS NOT NULL DO UPDATE
             SET appointment_date = EXCLUDED.appointment_date, appointment_time = EXCLUDED.appointment_time,
                 source = EXCLUDED.source
           RETURNING appointment_id""",
        (card["mrn"], day, at, card["department"], card["card_id"], card["name"], source)).fetchone()
    closed = conn.execute("UPDATE gap_event SET status = 'resolved', outcome = 'patient_booked' "
                          "WHERE card_id = %s AND status <> 'resolved' RETURNING gap_id", (card["card_id"],)).fetchall()
    audit.append(conn, actor_type="system", actor_name=actor, action="appointment_booked", entity_type="order_card",
                 entity_id=str(card["card_id"]), mrn=card["mrn"], reason_code=source,
                 details={"appointment_id": appt["appointment_id"], "date": day.isoformat(),
                          "time": at.strftime("%H:%M") if at else None, "label": label, "inbound_id": inbound_id,
                          "gaps_closed": [g["gap_id"] for g in closed]})
    where = " (outside)" if label else ""
    set_next_action(conn, card["mrn"], f"{card['name']} booked{where} for {_fmt(day, at)}")
    return appt


def tap_booked(conn, card: dict, inbound_id: int, actor: str, *, elsewhere: bool = False) -> str:
    """"Booked" or "Booked elsewhere": booked for the day of the tap."""
    book(conn, card, source="patient_elsewhere" if elsewhere else "patient_button", day=clock.get_today(conn),
         at=None, inbound_id=inbound_id, actor=actor)
    return "card_booked_elsewhere" if elsewhere else "card_booked"


def slots(conn, card: dict) -> list[dict]:
    """Four slots on different working days in the next two weeks, each at a clinic time. Random, but the same
    card on the same day gets the same four (a repeated tap offers the same times)."""
    today = clock.get_today(conn)
    days, d = [], today
    while len(days) < SLOT_DAYS:
        d += timedelta(days=1)
        if d.weekday() < 5:
            days.append(d)
    pick = random.Random(f"{card['card_id']}:{today.isoformat()}")
    out = []
    for n, day in enumerate(sorted(pick.sample(days, len(SLOT_BUTTONS)))):
        at = time.fromisoformat(pick.choice(SLOT_TIMES))
        label = f"{day:%a} {_fmt(day, at)}"
        out.append({"id": SLOT_BUTTONS[n], "label": label, "label_en": label, "date": day.isoformat(),
                    "time": at.strftime("%H:%M")})
    return out


def offer_slots(conn, mrn: str, card: dict, inbound_id: int, actor: str) -> str:
    """"Please help me book": the slot message with four buttons. Not sent -> a person books her."""
    offered = slots(conn, card)
    msg = messaging.send_message(conn, mrn=mrn, template_id="booking_slots", actor_type="agent", actor=AGENT,
                                 reason_code="patient_asked_help_booking", card_id=card["card_id"], buttons=offered)
    audit.append(conn, actor_type="agent", actor_name=AGENT, action="agent_decision", entity_type="inbound_message",
                 entity_id=str(inbound_id), mrn=mrn, reason_code="patient_asked_help_booking", task="patient_help",
                 outcome="slots_offered" if msg["status"] == "sent" else "escalate_to_coordinator",
                 trigger_event=f"inbound_message:{inbound_id}",
                 decision_inputs={"card": card["item_code"], "card_state": card["state"], "ai_used": False},
                 details={"message_id": msg["message_id"], "status": msg["status"],
                          "blocked_reason": msg["blocked_reason"], "slots": [s["label_en"] for s in offered]})
    if msg["status"] == "sent":
        set_next_action(conn, mrn, f"{card['name']}: four slots offered on WhatsApp; waiting for her choice")
        return "slots_offered"
    r = help_tasks.escalate(conn, mrn=mrn, task_type="help_booking", card=card, inbound_id=inbound_id, actor=actor)
    return f"slots_not_sent_help_booking_{'created' if r['created'] else 'already_open'}"


def tap_slot(conn, mrn: str, button: str, target: dict | None, card: dict | None, inbound_id: int,
             actor: str) -> str:
    """The slot she tapped books the card with its date and time; she is told it is booked."""
    slot = next((b for b in (target or {}).get("buttons") or [] if b.get("id") == button and b.get("date")), None)
    if slot is None or target["template_id"] != "booking_slots":
        return "no_slot_offer"
    if card is None:
        return "no_card"
    if card["state"] != "Due":
        return "already_progressed"
    day, at = date.fromisoformat(slot["date"]), time.fromisoformat(slot["time"])
    book(conn, card, source="patient_slot", day=day, at=at, inbound_id=inbound_id, actor=actor)
    messaging.send_message(conn, mrn=mrn, template_id="booking_confirmed", actor_type="agent", actor=AGENT,
                           reason_code="appointment_booked", card_id=card["card_id"],
                           extra={"appointment": f"{slot['label']}"})
    return "card_booked_slot"
