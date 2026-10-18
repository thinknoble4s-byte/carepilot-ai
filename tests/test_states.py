"""A6: cards move only along the allowed path; guards are enforced."""
import pytest

from app.states import TEST_FLOW, VISIT_FLOW, IllegalTransition, check_transition
from conftest import PRIYA, plan_body

GUARDS = {
    ("visit", "Due", "Booked"): {"source": "button_tap"},
    ("visit", "Booked", "Attended"): {"source": "appointment_list"},
    ("test", "Ordered", "Scheduled"): {"appointment_date": "2026-11-12"},
    ("test", "Due", "Scheduled"): {"appointment_date": "2026-11-12"},
    ("test", "Scheduled", "Completed"): {"source": "appointment_list"},
    ("test", "Due", "Completed"): {"source": "appointment_list"},
    ("test", "Due", "Report received"): {"source": "outside_report_verified", "file_ref": "photo.pdf"},
    ("test", "Scheduled", "Report received"): {"source": "outside_report_verified", "file_ref": "photo.pdf"},
    ("test", "Completed", "Report received"): {"file_ref": "report.pdf"},
    ("test", "Report received", "Report verified"): {"matched_identifiers": 2},
}


def all_moves():
    for card_type, flow in (("visit", VISIT_FLOW), ("test", TEST_FLOW)):
        for a in flow:
            for b in flow:
                yield card_type, a, b, b in flow[a]


@pytest.mark.parametrize("card_type, a, b, legal", list(all_moves()))
def test_only_legal_moves_pass(card_type, a, b, legal):
    ctx = GUARDS.get((card_type, a, b), {})
    if legal:
        check_transition(card_type, a, b, **ctx)
    else:
        with pytest.raises(IllegalTransition):
            check_transition(card_type, a, b, **ctx)


@pytest.mark.parametrize("card_type, a, b, ctx", [
    ("visit", "Due", "Ordered", {}),                                  # visit card into a test state
    ("visit", "Due", "Teleported", {}),                               # unknown state
    ("visit", "Due", "Booked", {"source": "patient_reply"}),
    ("visit", "Booked", "Attended", {"source": "patient_reply"}),     # attendance never from a reply
    ("test", "Ordered", "Scheduled", {}),
    ("test", "Completed", "Report received", {}),
    # Use Case 2T-T3: illegal moves
    ("test", "Due", "Scheduled", {}),                                          # no date
    ("test", "Due", "Report received", {"file_ref": "report.pdf"}),            # hospital report, not Completed
    ("test", "Due", "Report received", {"source": "outside_report_verified"}),  # no file
    ("test", "Scheduled", "Completed", {"source": "patient_reply"}),           # never from a patient message
    ("test", "Due", "Completed", {}),
    ("test", "Report received", "Report verified", {"matched_identifiers": 1}),
])
def test_guards(card_type, a, b, ctx):
    with pytest.raises(IllegalTransition):
        check_transition(card_type, a, b, **ctx)


def test_transition_card_writes_audit_and_refusals_change_nothing(seeded, client):
    from app import db
    from app.states import transition_card

    client.post("/patients/SYN-0007/plan/confirm", headers=PRIYA, json=plan_body("SYN-0007"))
    with db.connect() as c:
        t1 = c.execute("SELECT card_id FROM order_card WHERE mrn = 'SYN-0007' AND item_code = 'T1'").fetchone()["card_id"]
        before = c.execute("SELECT count(*) AS n FROM audit_ledger").fetchone()["n"]
        transition_card(c, t1, "Ordered", "person", "u_priya")
        moved = transition_card(c, t1, "Scheduled", "person", "u_priya", appointment_date="2026-11-05")
        assert str(moved["appointment_date"]) == "2026-11-05"
        assert c.execute("SELECT count(*) AS n FROM audit_ledger").fetchone()["n"] == before + 2
    with db.connect() as c:
        with pytest.raises(IllegalTransition):
            transition_card(c, t1, "Report verified", "person", "u_priya")
        c.rollback()
        row = c.execute("SELECT state FROM order_card WHERE card_id = %s", (t1,)).fetchone()
        assert row["state"] == "Scheduled"
        assert c.execute("SELECT count(*) AS n FROM audit_ledger").fetchone()["n"] == before + 2
