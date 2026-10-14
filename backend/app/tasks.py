"""Tasks for people: every one has an owner, a cover and a deadline from the
deadline policy. A missed deadline escalates to the cover (or sends the owner
a reminder, for the doctor's instruction)."""
from . import audit, clock, policy
from .errors import Conflict, NotFound

DEADLINE_CHECK = "CarePilot deadline check"


def create_task(conn, *, mrn: str, task_type: str, reason: str, owner_name: str | None = None,
                actor_type: str, actor: str, handover_id=None, card_id=None, gap_id=None,
                dedupe_key: str | None = None) -> dict:
    rules = policy.deadlines(conn)
    named = conn.execute("SELECT coordinator FROM patient WHERE mrn = %s", (mrn,)).fetchone()
    owner = owner_name or (named and named["coordinator"]) or rules["default_coordinator"]
    days = rules["working_days"].get(task_type)
    due = policy.add_working_days(clock.get_today(conn), days) if days is not None else None
    task = conn.execute(
        """
        INSERT INTO coordinator_task (mrn, task_type, reason, owner_name, cover_name, assigned_name, due_date,
                                      escalation, handover_id, card_id, gap_id, dedupe_key)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (dedupe_key) DO NOTHING
        RETURNING task_id, mrn, task_type, reason, owner_name, cover_name, assigned_name, due_date, status
        """,
        (mrn, task_type, reason, owner, rules["covers"].get(owner), owner, due,
         rules["escalation"].get(task_type), handover_id, card_id, gap_id, dedupe_key),
    ).fetchone()
    if task is None:  # already created for this dedupe key
        return conn.execute("SELECT * FROM coordinator_task WHERE dedupe_key = %s", (dedupe_key,)).fetchone()
    audit.append(conn, actor_type=actor_type, actor_name=actor, action="task_created",
                 entity_type="task", entity_id=str(task["task_id"]), mrn=mrn,
                 details={"task_type": task_type, "owner": owner, "cover": task["cover_name"],
                          "due_date": due.isoformat() if due else None, "reason": reason})
    return task


def complete_task(conn, task_id: int, done_by: str, note: str | None = None) -> dict:
    task = conn.execute("SELECT * FROM coordinator_task WHERE task_id = %s FOR UPDATE", (task_id,)).fetchone()
    if task is None:
        raise NotFound(f"Task {task_id} not found")
    if task["status"] != "open":
        raise Conflict(f"Task {task_id} is already {task['status']}")
    done = conn.execute(
        """
        UPDATE coordinator_task SET status = 'done', done_by = %s, done_note = %s, done_on = %s
        WHERE task_id = %s RETURNING *
        """,
        (done_by, note, clock.get_today(conn), task_id),
    ).fetchone()
    audit.append(conn, actor_type="person", actor_name=done_by, action="task_done", entity_type="task",
                 entity_id=str(task_id), mrn=task["mrn"], details={"task_type": task["task_type"], "note": note})
    return done


def complete_open(conn, handover_id: int, task_type: str, done_by: str, note: str | None = None) -> None:
    for row in conn.execute(
        "SELECT task_id FROM coordinator_task WHERE handover_id = %s AND task_type = %s AND status = 'open'",
        (handover_id, task_type),
    ).fetchall():
        complete_task(conn, row["task_id"], done_by, note)


def run_deadlines(conn) -> list[dict]:
    """Escalate every open task past its deadline, once."""
    today = clock.get_today(conn)
    overdue = conn.execute(
        """
        SELECT * FROM coordinator_task
        WHERE status = 'open' AND due_date < %s AND escalated_on IS NULL AND escalation IS NOT NULL
        ORDER BY task_id FOR UPDATE
        """,
        (today,),
    ).fetchall()
    results = []
    for t in overdue:
        if t["escalation"] == "cover" and t["cover_name"]:
            assigned, action = t["cover_name"], "task_escalated"
        else:
            assigned, action = t["assigned_name"], "task_reminder_sent"
        conn.execute("UPDATE coordinator_task SET assigned_name = %s, escalated_on = %s WHERE task_id = %s",
                     (assigned, today, t["task_id"]))
        audit.append(conn, actor_type="system", actor_name=DEADLINE_CHECK, action=action, entity_type="task",
                     entity_id=str(t["task_id"]), mrn=t["mrn"], rule="deadline",
                     details={"task_type": t["task_type"], "due_date": t["due_date"].isoformat(),
                              "from": t["assigned_name"], "to": assigned})
        results.append({"task_id": t["task_id"], "task_type": t["task_type"], "mrn": t["mrn"],
                        "action": action, "assigned_name": assigned})
    return results
