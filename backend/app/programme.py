"""Care Plan (read-only config) and Performance Indicators (aggregates only).

Indicators are calculated from the records; anything that needs data not yet
captured (attendance, a measured baseline) is returned as None with the reason,
never invented. All numbers are synthetic demo data.
"""
from statistics import median

from . import policy, worklist


def care_plan(conn) -> dict:
    rows = conn.execute(
        "SELECT kind, name, version, body, signed_off_by, signed_off_at FROM config_versions WHERE status = 'active'"
    ).fetchall()
    by_kind = {}
    for r in rows:
        by_kind.setdefault(r["kind"], []).append(r)
    return {
        "plan_templates": by_kind.get("plan_template", []),
        "rule_thresholds": by_kind.get("rule_thresholds", []),
        "autonomy_policy": by_kind.get("autonomy_policy", []),
        "deadline_policy": by_kind.get("deadline_policy", []),
        "message_templates": by_kind.get("message_template", []),
        "settings": policy.settings(conn),
    }


def _working_days(start, end) -> int:
    n, d = 0, start
    while d < end:
        d = policy.add_working_days(d, 1)
        n += 1
    return n


def kpis(conn) -> dict:
    actors = conn.execute(
        """
        SELECT actor_type, count(*) AS n FROM audit_ledger
        WHERE action NOT IN ('session.user_selected', 'demo_reset') GROUP BY actor_type
        """
    ).fetchall()
    counts = {r["actor_type"]: r["n"] for r in actors}
    agent_n, human_n = counts.get("agent", 0), counts.get("person", 0)
    g0 = conn.execute(
        """
        SELECT count(*) AS total, count(*) FILTER (WHERE c.state <> 'Due') AS rescued
        FROM gap_event g JOIN order_card c USING (card_id) WHERE g.rule = 'G0'
        """
    ).fetchone()
    responses = [_working_days(r["opened_on"], r["gate1_on"]) for r in conn.execute(
        "SELECT opened_on, gate1_on FROM handover WHERE gate1_on IS NOT NULL").fetchall()]
    minutes = [r["response_minutes"] for r in conn.execute(
        "SELECT response_minutes FROM handover WHERE response_minutes IS NOT NULL").fetchall()]
    decided = conn.execute(
        "SELECT count(*) AS n, count(*) FILTER (WHERE status = 'approved' AND NOT edited) AS clean "
        "FROM proposal WHERE status <> 'drafted'").fetchone()
    calls = [r["minutes_to_call"] for r in conn.execute(
        "SELECT minutes_to_call FROM coordinator_task WHERE task_type = 'urgent_call' AND minutes_to_call IS NOT NULL"
    ).fetchall()]
    rows = worklist.build(conn)["patients"]
    statuses = conn.execute("SELECT status, count(*) AS n FROM patient GROUP BY status").fetchall()
    return {
        "patients": sum(r["n"] for r in statuses),
        "plans_confirmed": conn.execute("SELECT count(*) AS n FROM care_plan").fetchone()["n"],
        "recall_completion": None,
        "recall_completion_note": "Needs attendance from the appointment list against each order card (Increment 6).",
        "baseline": None,
        "baseline_note": "Not measured yet. In the pilot the baseline is calculated inside the clinic before go-live.",
        "agent_share": round(100 * agent_n / (agent_n + human_n)) if agent_n + human_n else 0,
        "agent_actions": agent_n, "human_actions": human_n,
        "g0_gaps": g0["total"], "g0_booked_after_reminder": g0["rescued"],
        "handover_response_median_days": median(responses) if responses else None,
        "handovers_decided": len(responses),
        "handover_response_median_minutes": median(minutes) if minutes else None,
        "proposals_decided": decided["n"], "proposals_approved_unedited": decided["clean"],
        "draft_acceptance": round(100 * decided["clean"] / decided["n"]) if decided["n"] else None,
        "not_responding": sum(1 for r in rows if r["unanswered"] >= 2),
        "lost_to_follow_up": next((s["n"] for s in statuses if s["status"] == "Lost to follow-up"), 0),
        "open_handovers": conn.execute("SELECT count(*) AS n FROM handover WHERE stage <> 'closed'").fetchone()["n"],
        "messages_sent": conn.execute("SELECT count(*) AS n FROM outbox_message WHERE status = 'sent'").fetchone()["n"],
        "sends_blocked": conn.execute("SELECT count(*) AS n FROM outbox_message WHERE status = 'blocked'").fetchone()["n"],
        # Use Case 2c REQ-14: combined numbers only, no names.
        "urgent_calls_reached": len(calls),
        "time_to_call_median_minutes": median(calls) if calls else None,
        "time_to_call_max_minutes": max(calls) if calls else None,
    }


def set_task_mode(conn, task: str, mode: str, user_id: str) -> dict:
    """Promote an agent task to live or move it back to shadow: a new version of the autonomy
    policy (the old one is retired), recorded in the audit trail with who made the change."""
    from psycopg.types.json import Jsonb

    from . import audit
    from .errors import Invalid, NotFound

    if mode not in ("live", "shadow"):
        raise Invalid("mode must be live or shadow")
    current = conn.execute(
        "SELECT * FROM config_versions WHERE kind = 'autonomy_policy' AND name = 'default' AND status = 'active' FOR UPDATE"
    ).fetchone()
    body = current["body"]
    if task not in body["tasks"] or task == "log_button_reply":
        raise NotFound(f"No switchable agent task {task!r}")
    before = body["tasks"][task]["mode"]
    body["tasks"][task]["mode"] = mode
    body["version"] = f"{current['version'] + 1}.0"  # what the agent records as policy_version
    conn.execute("UPDATE config_versions SET status = 'retired' WHERE id = %s", (current["id"],))
    new = conn.execute(
        """
        INSERT INTO config_versions (kind, name, version, body, status, signed_off_by, signed_off_at)
        VALUES ('autonomy_policy', 'default', %s, %s, 'active', %s, now()) RETURNING id, version
        """,
        (current["version"] + 1, Jsonb(body), f"{current['signed_off_by']} (mode change by {user_id}, demo)"),
    ).fetchone()
    audit.append(conn, actor_type="person", actor_name=user_id, action="policy_mode_changed",
                 entity_type="config_versions", entity_id=str(new["id"]), task=task, mode=mode,
                 policy_version=str(new["version"]), details={"from": before, "to": mode})
    return {"task": task, "mode": mode, "version": new["version"]}
