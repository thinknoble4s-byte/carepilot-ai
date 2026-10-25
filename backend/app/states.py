"""A6: allowed card states and the guards that protect them. Every later step
moves a card through transition_card, never with a plain UPDATE."""
from . import audit, clock
from .errors import DomainError, NotFound

VISIT_FLOW = {"Due": {"Booked"}, "Booked": {"Attended"}, "Attended": set()}

# Test cards follow one of two roads, set by the plan item (order_card.specimen) — Use Case 2T v1.4:
#   specimen-based (blood test):  Due -> Booked -> Completed -> Report received -> Report verified
#   non-specimen (imaging, echo): Due -> Booked -> Completed -> Verified (the doctor)
# Booked needs a source (the patient's tap, a person, or the appointment list) but not a date. Completed comes only
# from the appointment list (Due -> Completed is a walk-in). An outside document that is auto-matched or verified by
# a coordinator moves Due or Booked -> Report received (nobody told CarePilot the test was done). Report verified
# needs two matching identifiers: the system for a hospital report, a coordinator for an outside one. A coordinator's
# "Flag as wrong" returns an auto-matched card to its earlier state.
TEST_FLOW = {
    "Due": {"Booked", "Completed", "Report received"},
    "Booked": {"Completed", "Report received"},
    "Completed": {"Report received", "Verified"},
    "Report received": {"Report verified", "Due", "Booked"},
    "Report verified": set(),
    "Verified": set(),
}
VERIFIED = ("Report verified", "Verified")
DONE_STATES = ("Attended", *VERIFIED)
OUTSIDE_SOURCES = ("outside_report_auto_matched", "outside_report_verified")

FLOWS = {"visit": VISIT_FLOW, "test": TEST_FLOW}


class IllegalTransition(DomainError):
    status = 409


def check_transition(card_type: str, from_state: str, to_state: str, **ctx) -> None:
    flow = FLOWS.get(card_type)
    if flow is None:
        raise IllegalTransition(f"Unknown card type {card_type!r}")
    if from_state not in flow or to_state not in flow:
        raise IllegalTransition(f"Unknown state for a {card_type} card: {from_state!r} -> {to_state!r}")
    if to_state not in flow[from_state]:
        raise IllegalTransition(f"A {card_type} card cannot move from {from_state} to {to_state}")
    move = (card_type, from_state, to_state)
    source = ctx.get("source")
    if move == ("visit", "Due", "Booked") and source not in ("patient_button", "button_tap", "coordinator"):
        raise IllegalTransition("A booking is recorded from a patient button or by a coordinator")
    if move == ("visit", "Booked", "Attended") and source not in ("appointment_list", "hospital_feed"):
        raise IllegalTransition("Attendance comes only from the appointment list or a hospital feed, never a reply")
    if move == ("test", "Due", "Booked") and source not in ("patient_button", "coordinator", "appointment_list"):
        raise IllegalTransition("A test is Booked from the patient's tap, by a person, or from the appointment list")
    if card_type == "test" and to_state == "Completed" and source not in ("appointment_list", "hospital_feed"):
        raise IllegalTransition("A test becomes Completed only from the appointment list, never a patient message")
    specimen = ctx.get("specimen", True) is not False
    if card_type == "test" and to_state == "Report received":
        if from_state in ("Due", "Booked") and source not in OUTSIDE_SOURCES:
            raise IllegalTransition("Only an outside document that is auto-matched or verified by a coordinator "
                                    f"moves a test from {from_state} to Report received")
        if not specimen:
            raise IllegalTransition("A non-specimen test has no lab report: the doctor verifies it once Completed")
        if not ctx.get("file_ref"):
            raise IllegalTransition("A report needs an attached file (file_ref)")
    if from_state == "Report received" and to_state in ("Due", "Booked") and source != "flagged_wrong":
        raise IllegalTransition("A report is taken off a card only when a coordinator flags it as wrong")
    if move == ("test", "Completed", "Verified"):
        if specimen:
            raise IllegalTransition("A specimen-based test needs its report received before it is verified")
        if ctx.get("role") != "oncologist":
            raise IllegalTransition("Only the doctor sets a non-specimen test to Verified")
    if move == ("test", "Report received", "Report verified") and int(ctx.get("matched_identifiers") or 0) < 2:
        raise IllegalTransition("Verification needs two or more matching identifiers")


def transition_card(conn, card_id: int, new_state: str, actor_type: str, actor: str, **ctx) -> dict:
    card = conn.execute(
        "SELECT card_id, mrn, card_type, state, specimen FROM order_card WHERE card_id = %s FOR UPDATE", (card_id,)
    ).fetchone()
    if card is None:
        raise NotFound(f"Card {card_id} not found")
    check_transition(card["card_type"], card["state"], new_state, specimen=card["specimen"], **ctx)
    updated = conn.execute(
        """
        UPDATE order_card
        SET state = %(state)s,
            appointment_date = CASE WHEN %(state)s = 'Booked' AND %(when)s::date IS NOT NULL
                                    THEN %(when)s::date ELSE appointment_date END,
            booking_source = CASE WHEN %(state)s = 'Booked' THEN %(source)s ELSE booking_source END,
            verified_by = CASE WHEN %(state)s IN ('Report verified', 'Verified') THEN %(actor)s ELSE verified_by END,
            verified_at_demo = CASE WHEN %(state)s IN ('Report verified', 'Verified') THEN %(now)s::timestamp
                                    ELSE verified_at_demo END
        WHERE card_id = %(card_id)s
        RETURNING card_id, mrn, item_code, card_type, state, appointment_date
        """,
        {"state": new_state, "when": ctx.get("appointment_date"), "card_id": card_id, "source": ctx.get("source"),
         "actor": actor, "now": clock.get_now(conn)},
    ).fetchone()
    audit.append(
        conn, actor_type=actor_type, actor_name=actor, action="card_state_changed",
        entity_type="order_card", entity_id=str(card_id), mrn=card["mrn"],
        details={"from": card["state"], "to": new_state, **{k: str(v) for k, v in ctx.items()}},
    )
    return updated
