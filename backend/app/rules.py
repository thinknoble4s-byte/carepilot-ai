"""07:00 gap check: deterministic rules, not AI. Running it again adds nothing.

G0  Not booked by T-7: a card still Due with Due - 7 <= today <= Due. Key: G0 + card + due date.
    Still Due at T-3 after the reminder: a booking-help task for the coordinator (if tasks are on).
G1  Missed plan item: a card still Due more than visit_grace_days after its due date.
Which rules run, and which cards they look at, are scenario settings (Use Case 1: G0, plan visit
cards only). Consent and agent state are not checked here; the 07:30 run checks them. Paused and
closed patients are skipped (pause suspends the rules). A gap already on file is not raised again:
the check logs a suppressed duplicate instead.
"""
from datetime import timedelta

from . import audit, clock, policy, tasks

GAP_CHECK = "CarePilot gap check"


def run_gap_check(conn) -> dict:
    token = audit.DEMO_TIME.set("07:00")
    try:
        return _run(conn)
    finally:
        audit.DEMO_TIME.reset(token)


def _run(conn) -> dict:
    today = clock.get_today(conn)
    limits = policy.thresholds(conn)
    cfg = policy.settings(conn)
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

    candidates = conn.execute(
        """
        SELECT CASE WHEN c.due_date >= %(today)s::date THEN 'G0' ELSE 'G1' END AS rule,
               c.card_id, c.mrn, c.due_date, c.item_code
        FROM order_card c JOIN patient p USING (mrn)
        WHERE c.state = 'Due'
          AND p.status NOT IN ('Paused', 'Lost to follow-up', 'Followed up elsewhere')
          AND (%(all_cards)s OR (c.card_type = 'visit' AND c.origin = 'plan_item'))
          AND ((c.due_date BETWEEN %(today)s::date AND %(today)s::date + %(window)s::int)
               OR c.due_date + %(grace)s::int < %(today)s::date)
        ORDER BY c.due_date, c.card_id
        """,
        {"today": today, "window": window, "grace": grace, "all_cards": cfg["reminder_cards"] == "all"},
    ).fetchall()
    candidates = [c for c in candidates if c["rule"] in cfg["gap_rules"]]

    new, suppressed = [], []
    for c in candidates:
        g = conn.execute(
            """
            INSERT INTO gap_event (rule, card_id, mrn, due_date, detected_on) VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (rule, card_id, due_date) DO NOTHING
            RETURNING gap_id, rule, card_id, mrn, due_date, status
            """,
            (c["rule"], c["card_id"], c["mrn"], c["due_date"], today),
        ).fetchone()
        if g:
            new.append(g)
            audit.append(conn, actor_type="system", actor_name=GAP_CHECK, action="gap_detected",
                         entity_type="gap_event", entity_id=str(g["gap_id"]), mrn=g["mrn"], rule=g["rule"],
                         trigger_event="gap_check_0700",
                         details={"card_id": g["card_id"], "card": c["item_code"],
                                  "due_date": g["due_date"].isoformat(), "today": today.isoformat()})
        else:
            existing = conn.execute(
                "SELECT gap_id, status FROM gap_event WHERE rule = %s AND card_id = %s AND due_date = %s",
                (c["rule"], c["card_id"], c["due_date"])).fetchone()
            suppressed.append(existing["gap_id"])
            audit.append(conn, actor_type="system", actor_name=GAP_CHECK, action="duplicate_suppressed",
                         entity_type="gap_event", entity_id=str(existing["gap_id"]), mrn=c["mrn"], rule=c["rule"],
                         outcome="no_new_gap", details={"card": c["item_code"], "gap_status": existing["status"]})

    # G0 at T-3: reminder sent, still not booked -> the coordinator helps book (2 working days).
    t3_tasks = []
    if cfg["people_tasks"]:
        t3 = conn.execute(
            """
            SELECT g.gap_id, g.card_id, g.mrn, c.name FROM gap_event g JOIN order_card c USING (card_id)
            WHERE g.rule = 'G0' AND g.status = 'reminder_sent' AND c.state = 'Due' AND c.due_date - %s::date <= 3
            """,
            (today,),
        ).fetchall()
        t3_tasks = [
            tasks.create_task(conn, mrn=g["mrn"], task_type="booking_help", gap_id=g["gap_id"], card_id=g["card_id"],
                              reason=f"Still not booked at T-3: {g['name']}", actor_type="system", actor=GAP_CHECK,
                              dedupe_key=f"G0-T3:{g['card_id']}")
            for g in t3
        ]
    audit.append(conn, actor_type="system", actor_name=GAP_CHECK, action="gap_check_run", trigger_event="gap_check_0700",
                 outcome=f"{len(new)} new, {len(suppressed)} suppressed",
                 details={"today": today.isoformat(), "rules": cfg["gap_rules"], "cards": cfg["reminder_cards"],
                          "new_gaps": len(new), "suppressed": len(suppressed), "resolved": len(resolved)})
    return {"today": today, "new_gaps": new, "suppressed": len(suppressed), "resolved": len(resolved),
            "t3_tasks": len(t3_tasks), "window_days": window, "grace_days": grace,
            "checked_through": today + timedelta(days=window)}
