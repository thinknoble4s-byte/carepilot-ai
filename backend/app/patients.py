"""Patient follow-up status. Pause suspends the rules and the agent; "Agent off"
keeps the rules running but routes all contact to the coordinator. A change
away from Active needs a reason."""
from . import audit
from .agent import set_next_action
from .errors import Invalid, NotFound

SETTABLE = ("Active", "Paused", "Agent off")


def set_status(conn, mrn: str, user_id: str, status: str, reason: str | None) -> dict:
    patient = conn.execute("SELECT mrn, status FROM patient WHERE mrn = %s FOR UPDATE", (mrn,)).fetchone()
    if patient is None:
        raise NotFound(f"Patient {mrn} not found")
    if status not in SETTABLE:
        raise Invalid("status must be one of: " + ", ".join(SETTABLE))
    if status != "Active" and (not reason or not reason.strip()):
        raise Invalid("A reason is required")
    updated = conn.execute("UPDATE patient SET status = %s WHERE mrn = %s RETURNING mrn, status",
                           (status, mrn)).fetchone()
    audit.append(conn, actor_type="person", actor_name=user_id, action="patient_status_changed",
                 entity_type="patient", entity_id=mrn, mrn=mrn,
                 details={"from": patient["status"], "to": status, "reason": (reason or "").strip() or None})
    set_next_action(conn, mrn, {"Paused": "Paused: no rules or reminders until resumed",
                                "Agent off": "Agent off: the coordinator handles all contact",
                                "Active": "Routine follow-up"}[status])
    return updated
