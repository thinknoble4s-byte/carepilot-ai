"""A6: allowed card states and the guards that protect them. Every later step
moves a card through transition_card, never with a plain UPDATE."""
from . import audit
from .errors import DomainError, NotFound

VISIT_FLOW = {"Due": {"Booked"}, "Booked": {"Attended"}, "Attended": set()}

TEST_FLOW = {
    "Due": {"Ordered"},
    "Ordered": {"Scheduled"},
    "Scheduled": {"Completed"},
    "Completed": {"Report received"},
    "Report received": {"Report verified"},
    "Report verified": set(),
}

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
    if move == ("visit", "Due", "Booked") and ctx.get("source") not in ("patient_button", "button_tap", "coordinator"):
        raise IllegalTransition("A booking is recorded from a patient button or by a coordinator")
    if move == ("visit", "Booked", "Attended") and ctx.get("source") not in ("appointment_list", "hospital_feed"):
        raise IllegalTransition("Attendance comes only from the appointment list or a hospital feed, never a reply")
    if move == ("test", "Ordered", "Scheduled") and not ctx.get("appointment_date"):
        raise IllegalTransition("A test cannot be Scheduled without an appointment date")
    if move == ("test", "Completed", "Report received") and not ctx.get("file_ref"):
        raise IllegalTransition("A report needs an attached file (file_ref)")
    if move == ("test", "Report received", "Report verified") and int(ctx.get("matched_identifiers") or 0) < 2:
        raise IllegalTransition("Verification needs two or more matching identifiers")


def transition_card(conn, card_id: int, new_state: str, actor_type: str, actor: str, **ctx) -> dict:
    card = conn.execute(
        "SELECT card_id, mrn, card_type, state FROM order_card WHERE card_id = %s FOR UPDATE", (card_id,)
    ).fetchone()
    if card is None:
        raise NotFound(f"Card {card_id} not found")
    check_transition(card["card_type"], card["state"], new_state, **ctx)
    updated = conn.execute(
        """
        UPDATE order_card
        SET state = %(state)s,
            appointment_date = CASE WHEN %(state)s IN ('Scheduled', 'Booked') AND %(when)s::date IS NOT NULL
                                    THEN %(when)s::date ELSE appointment_date END,
            booking_source = CASE WHEN %(state)s = 'Booked' THEN %(source)s ELSE booking_source END
        WHERE card_id = %(card_id)s
        RETURNING card_id, mrn, item_code, card_type, state, appointment_date
        """,
        {"state": new_state, "when": ctx.get("appointment_date"), "card_id": card_id, "source": ctx.get("source")},
    ).fetchone()
    audit.append(
        conn, actor_type=actor_type, actor_name=actor, action="card_state_changed",
        entity_type="order_card", entity_id=str(card_id), mrn=card["mrn"],
        details={"from": card["state"], "to": new_state, **{k: str(v) for k, v in ctx.items()}},
    )
    return updated
