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


def template(conn, template_id: str) -> dict:
    return active(conn, "message_template", template_id)


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
