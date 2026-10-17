"""Demo clock: the stored "today". Business logic never reads the real date;
only audit timestamps use real time."""
from datetime import date, datetime, time, timedelta

from . import audit
from .errors import Invalid


def get_today(conn) -> date:
    return conn.execute("SELECT today FROM demo_clock WHERE id = 1").fetchone()["today"]


def get_now(conn) -> datetime:
    row = conn.execute("SELECT today, now_time FROM demo_clock WHERE id = 1").fetchone()
    return datetime.combine(row["today"], row["now_time"])


def set_time(conn, at: time) -> None:
    conn.execute("UPDATE demo_clock SET now_time = %s WHERE id = 1", (at,))


def set_today(conn, today: date) -> None:
    conn.execute(
        "INSERT INTO demo_clock (id, today) VALUES (1, %s) ON CONFLICT (id) DO UPDATE SET today = EXCLUDED.today",
        (today,),
    )


def set_forward(conn, target, actor: str, at: str | None = None) -> date:
    """Set the demo date, and optionally the time (forward only; a demo reset goes back to the start)."""
    try:
        new = target if isinstance(target, date) else date.fromisoformat(str(target))
        new_time = time.fromisoformat(at) if at else None
    except ValueError:
        raise Invalid("date must be YYYY-MM-DD and time HH:MM")
    before = get_now(conn)
    after = datetime.combine(new, new_time or (before.time() if new == before.date() else time(9, 0)))
    if after < before:
        raise Invalid(f"The demo clock only moves forward (now {before:%Y-%m-%d %H:%M}); use Reset demo to start again")
    set_today(conn, after.date())
    set_time(conn, after.time())
    audit.append(conn, actor_type="person", actor_name=actor, action="clock_set", entity_type="demo_clock",
                 details={"from": f"{before:%Y-%m-%d %H:%M}", "to": f"{after:%Y-%m-%d %H:%M}"})
    return after.date()


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
