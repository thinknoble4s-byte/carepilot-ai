"""Read side for the Workspace, Patient journey and Summary screens."""
from .errors import NotFound


def list_patients(conn) -> list[dict]:
    return conn.execute(
        """
        SELECT p.mrn, p.full_name, p.preferred_language, p.status, p.agent_on,
               CASE WHEN c.scopes IS NULL THEN 'none'
                    WHEN 'automated_messaging' = ANY (c.scopes) THEN 'automated_ok'
                    ELSE 'no_automated_messaging' END AS consent_status,
               EXISTS (SELECT 1 FROM care_plan cp WHERE cp.mrn = p.mrn) AS has_plan,
               (SELECT min(oc.due_date) FROM order_card oc WHERE oc.mrn = p.mrn AND oc.state = 'Due') AS next_due
        FROM patient p
        LEFT JOIN LATERAL (
            SELECT scopes FROM consent WHERE consent.mrn = p.mrn ORDER BY consent_id DESC LIMIT 1
        ) c ON true
        ORDER BY p.mrn
        """
    ).fetchall()


def patient_detail(conn, mrn: str) -> dict:
    patient = conn.execute(
        """
        SELECT mrn, full_name, phone, caregiver_name, caregiver_phone, preferred_language, status, agent_on,
               batch_id, created_at
        FROM patient WHERE mrn = %s
        """,
        (mrn,),
    ).fetchone()
    if patient is None:
        raise NotFound(f"Patient {mrn} not found")
    consent = conn.execute(
        """
        SELECT consent_id, consent_reference, scopes, recorded_by, recorded_on
        FROM consent WHERE mrn = %s ORDER BY consent_id DESC LIMIT 1
        """,
        (mrn,),
    ).fetchone()
    plan = conn.execute(
        """
        SELECT care_plan_id, template_id, template_version, therapy, treatment_end_date, identifiers_checked,
               confirmed_by, confirmed_at
        FROM care_plan WHERE mrn = %s
        """,
        (mrn,),
    ).fetchone()
    return {
        **patient,
        "consent": consent,
        "plan": plan,
        "cards": conn.execute("SELECT count(*) AS n FROM order_card WHERE mrn = %s", (mrn,)).fetchone()["n"],
        "open_tasks": conn.execute(
            """
            SELECT task_id, task_type, reason, status, created_at
            FROM coordinator_task WHERE mrn = %s AND status = 'open' ORDER BY task_id
            """,
            (mrn,),
        ).fetchall(),
        "appointments": conn.execute(
            "SELECT appointment_date, department FROM appointment WHERE mrn = %s ORDER BY appointment_date",
            (mrn,),
        ).fetchall(),
    }


def patient_cards(conn, mrn: str) -> list[dict]:
    if conn.execute("SELECT 1 FROM patient WHERE mrn = %s", (mrn,)).fetchone() is None:
        raise NotFound(f"Patient {mrn} not found")
    return conn.execute(
        """
        SELECT card_id, item_code, card_type, name, department, due_date, state, appointment_date,
               owner_name, cover_name
        FROM order_card WHERE mrn = %s ORDER BY card_id
        """,
        (mrn,),
    ).fetchall()


def open_tasks(conn) -> list[dict]:
    return conn.execute(
        """
        SELECT t.task_id, t.mrn, p.full_name, t.task_type, t.reason, t.status, t.created_at
        FROM coordinator_task t JOIN patient p USING (mrn)
        WHERE t.status = 'open' ORDER BY t.task_id
        """
    ).fetchall()


def summary(conn) -> dict:
    """Aggregates only: never a name, phone number or MRN (what the chief sees)."""
    row = conn.execute(
        """
        SELECT (SELECT count(*) FROM patient)                                   AS patients,
               (SELECT count(*) FROM patient WHERE agent_on)                    AS agent_on,
               (SELECT count(*) FROM import_exception WHERE status = 'open')    AS open_exceptions,
               (SELECT count(*) FROM care_plan)                                 AS plans_confirmed,
               (SELECT count(*) FROM coordinator_task WHERE status = 'open')    AS open_tasks,
               (SELECT today FROM demo_clock WHERE id = 1)                      AS today
        """
    ).fetchone()
    by_state = conn.execute("SELECT state, count(*) AS n FROM order_card GROUP BY state ORDER BY state").fetchall()
    return {**row, "cards_by_state": {r["state"]: r["n"] for r in by_state}}
