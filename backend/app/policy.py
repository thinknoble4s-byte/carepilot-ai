"""Signed-off configuration read from config_versions (rules, autonomy policy,
deadlines, message templates), and working-day arithmetic for deadlines."""
from datetime import date, timedelta

from .errors import NotFound


def active(conn, kind: str, name: str) -> dict:
    row = conn.execute(
        "SELECT body, version FROM config_versions WHERE kind = %s AND name = %s AND status = 'active'",
        (kind, name),
    ).fetchone()
    if row is None:
        raise NotFound(f"No active {kind} {name!r}")
    return row["body"]


def thresholds(conn) -> dict:
    return active(conn, "rule_thresholds", "default")


def autonomy(conn) -> dict:
    return active(conn, "autonomy_policy", "default")


def deadlines(conn) -> dict:
    return active(conn, "deadline_policy", "default")


def template(conn, template_id: str, language: str) -> dict | None:
    """The approved, effective template for this language, or None (the sender then refuses)."""
    row = conn.execute(
        """
        SELECT body FROM config_versions
        WHERE kind = 'message_template' AND name = %s AND status = 'active' AND signed_off_by IS NOT NULL
        """,
        (f"{template_id}:{language}",),
    ).fetchone()
    if row is None:
        return None
    body = row["body"]
    today = conn.execute("SELECT today FROM demo_clock WHERE id = 1").fetchone()
    if today and body.get("effective_from") and date.fromisoformat(body["effective_from"]) > today["today"]:
        return None
    return body


DEFAULT_SETTINGS = {"gap_rules": ["G0", "G1"], "reminder_cards": "all", "caregiver_copy": True,
                    "act_on_replies": True, "people_tasks": True, "clinic_name": "Hospital A"}


def settings(conn) -> dict:
    """Per-scenario switches (Use Case 1 narrows them). Defaults: everything built so far is on."""
    row = conn.execute(
        "SELECT body FROM config_versions WHERE kind = 'scenario_settings' AND name = 'default' AND status = 'active'"
    ).fetchone()
    return {**DEFAULT_SETTINGS, **(row["body"] if row else {})}


def add_working_days(start: date, days: int) -> date:
    """Monday to Friday. 0 = the same day (or the next Monday if it is a weekend)."""
    d = start
    while d.weekday() >= 5:
        d += timedelta(days=1)
    added = 0
    while added < days:
        d += timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d
