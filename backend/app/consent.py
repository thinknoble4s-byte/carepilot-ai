"""A3: record consent. The agent is on only when the latest consent includes
automated_messaging; otherwise a coordinator task is created."""
from datetime import date

from . import audit, policy
from .errors import Invalid, NotFound
from .reference import CONSENT_SCOPES

AUTOMATED = "automated_messaging"


def record_consent(conn, user_id: str, mrn: str, consent_reference: str, scopes, recorded_on) -> dict:
    if conn.execute("SELECT 1 FROM patient WHERE mrn = %s FOR UPDATE", (mrn,)).fetchone() is None:
        raise NotFound(f"Patient {mrn} not found")
    if not isinstance(scopes, list) or not scopes:
        raise Invalid("scopes must be a non-empty list")
    bad = [s for s in scopes if s not in CONSENT_SCOPES]
    if bad:
        raise Invalid(f"Unknown scope(s): {', '.join(map(str, bad))}")
    if not consent_reference or not str(consent_reference).strip():
        raise Invalid("consent_reference is required")
    try:
        when = recorded_on if isinstance(recorded_on, date) else date.fromisoformat(str(recorded_on))
    except ValueError:
        raise Invalid("recorded_on must be a real date (YYYY-MM-DD)")
    scopes = list(dict.fromkeys(scopes))  # keep order, drop repeats

    consent = conn.execute(
        """
        INSERT INTO consent (mrn, consent_reference, scopes, recorded_by, recorded_on)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING consent_id, mrn, consent_reference, scopes, recorded_by, recorded_on
        """,
        (mrn, str(consent_reference).strip(), scopes, user_id, when),
    ).fetchone()
    agent_on = AUTOMATED in scopes
    conn.execute("UPDATE patient SET agent_on = %s WHERE mrn = %s", (agent_on, mrn))
    task_id = None
    if not agent_on and policy.settings(conn)["people_tasks"]:
        task_id = conn.execute(
            """
            INSERT INTO coordinator_task (mrn, task_type, reason)
            VALUES (%s, 'consent_scope_missing', 'Consent does not include automated_messaging; agent stays off')
            RETURNING task_id
            """,
            (mrn,),
        ).fetchone()["task_id"]
    audit.append(
        conn,
        actor_type="person",
        actor_name=user_id,
        action="consent_recorded",
        entity_type="consent",
        entity_id=str(consent["consent_id"]),
        mrn=mrn,
        details={"consent_reference": consent["consent_reference"], "scopes": scopes, "agent_on": agent_on,
                 "task_id": task_id},
    )
    return {**consent, "agent_on": agent_on, "task_id": task_id}


def latest_scopes(conn, mrn: str) -> list[str] | None:
    row = conn.execute(
        "SELECT scopes FROM consent WHERE mrn = %s ORDER BY consent_id DESC LIMIT 1", (mrn,)
    ).fetchone()
    return None if row is None else list(row["scopes"])


def has_valid_consent(conn, mrn: str) -> bool:
    """True only if the latest consent record includes automated_messaging."""
    return consent_allows(conn, mrn, AUTOMATED)


def consent_allows(conn, mrn: str, scope: str) -> bool:
    scopes = latest_scopes(conn, mrn)
    return scopes is not None and scope in scopes
