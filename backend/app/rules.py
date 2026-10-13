"""07:00 gap check: deterministic rules, not AI. Running it again adds nothing.

G0  Not booked by T-7: a card still Due within g0_booking_days_before_due of its due date.
    Still Due at T-3 after the reminder: a booking-help task for the coordinator.
G1  Missed plan item: a card still Due more than visit_grace_days after its due date.
Paused and closed patients are skipped (pause suspends the rules); "Agent off"
patients still get gap events, which the agent routes to the coordinator.
"""
from datetime import timedelta

from . import audit, clock, policy, tasks

GAP_CHECK = "CarePilot gap check"


def run_gap_check(conn) -> dict:
    today = clock.get_today(conn)
    limits = policy.thresholds(conn)
    window = int(limits["g0_booking_days_before_due"])
    grace = int(limits["visit_grace_days"])

    resolved = conn.execute(
        """
        UPDATE gap_event g SET status = 'resolved'
        FROM order_card c
        WHERE c.card_id = g.card_id AND g.status <> 'resolved' AND c.state <> 'Due'
        RETURNING g.gap_id
        """
    ).fetchall()

    new = conn.execute(
        """
        INSERT INTO gap_event (rule, card_id, mrn, due_date, detected_on)
        SELECT CASE WHEN c.due_date >= %(today)s::date THEN 'G0' ELSE 'G1' END, c.card_id, c.mrn, c.due_date, %(today)s::date
        FROM order_card c JOIN patient p USING (mrn)
        WHERE c.state = 'Due'
          AND p.status NOT IN ('Paused', 'Lost to follow-up', 'Followed up elsewhere')
          AND ((c.due_date BETWEEN %(today)s::date AND %(today)s::date + %(window)s::int)
               OR c.due_date + %(grace)s::int < %(today)s::date)
        ON CONFLICT (rule, card_id) DO NOTHING
        RETURNING gap_id, rule, card_id, mrn, due_date
        """,
        {"today": today, "window": window, "grace": grace},
    ).fetchall()
    for g in new:
        audit.append(conn, actor_type="system", actor_name=GAP_CHECK, action="gap_detected",
                     entity_type="gap_event", entity_id=str(g["gap_id"]), mrn=g["mrn"], rule=g["rule"],
                     trigger_event="gap_check_0700",
                     details={"card_id": g["card_id"], "due_date": g["due_date"].isoformat(),
                              "today": today.isoformat()})

    # G0 at T-3: reminder sent, still not booked -> the coordinator helps book (2 working days).
    t3 = conn.execute(
        """
        SELECT g.gap_id, g.card_id, g.mrn, c.name FROM gap_event g JOIN order_card c USING (card_id)
        WHERE g.rule = 'G0' AND g.status = 'acted' AND g.outcome = 'sent' AND c.state = 'Due'
          AND c.due_date - %s::date <= 3
        """,
        (today,),
    ).fetchall()
    t3_tasks = [
        tasks.create_task(conn, mrn=g["mrn"], task_type="booking_help", gap_id=g["gap_id"], card_id=g["card_id"],
                          reason=f"Still not booked at T-3: {g['name']}", actor_type="system", actor=GAP_CHECK,
                          dedupe_key=f"G0-T3:{g['card_id']}")
        for g in t3
    ]
    return {"today": today, "new_gaps": new, "resolved": len(resolved), "t3_tasks": len(t3_tasks),
            "window_days": window, "grace_days": grace, "checked_through": today + timedelta(days=window)}
