"""Demo clock: the stored "today". Business logic never reads the real date;
only audit timestamps use real time."""
from datetime import date, timedelta

from . import audit
from .errors import Invalid


def get_today(conn) -> date:
    return conn.execute("SELECT today FROM demo_clock WHERE id = 1").fetchone()["today"]


def set_today(conn, today: date) -> None:
    conn.execute(
        "INSERT INTO demo_clock (id, today) VALUES (1, %s) ON CONFLICT (id) DO UPDATE SET today = EXCLUDED.today",
        (today,),
    )


def advance(conn, days: int, actor: str | None = None) -> date:
    if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 60:
        raise Invalid("days must be a whole number from 1 to 60")
    before = get_today(conn)
    after = before + timedelta(days=days)
    set_today(conn, after)
    audit.append(
        conn,
        actor_type="person" if actor else "system",
        actor_name=actor or "system",
        action="clock_advanced",
        entity_type="demo_clock",
        details={"from": before.isoformat(), "to": after.isoformat(), "days": days},
    )
    return after
