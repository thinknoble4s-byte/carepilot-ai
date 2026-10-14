"""Read side for the Workspace, Patient journey and Summary screens."""
from .errors import NotFound


def list_patients(conn) -> list[dict]:
    return conn.execute(
        """
        SELECT p.mrn, p.full_name, p.preferred_language, p.status, p.agent_on, p.coordinator,
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
               next_planned_action, coordinator, pause_until, pause_reason, batch_id, created_at
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
            SELECT task_id, task_type, reason, status, owner_name, cover_name, assigned_name, due_date, escalated_on,
                   handover_id, created_at
            FROM coordinator_task WHERE mrn = %s AND status = 'open' ORDER BY task_id
            """,
            (mrn,),
        ).fetchall(),
        "handover": conn.execute(
            "SELECT * FROM handover WHERE mrn = %s ORDER BY (stage = 'closed'), handover_id DESC LIMIT 1", (mrn,)
        ).fetchone(),
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
               (SELECT count(*) FROM handover WHERE stage <> 'closed')          AS open_handovers,
               (SELECT count(*) FROM outbox_message WHERE status = 'sent')      AS messages_sent,
               (SELECT count(*) FROM outbox_message WHERE status = 'blocked')   AS messages_blocked,
               (SELECT today FROM demo_clock WHERE id = 1)                      AS today
        """
    ).fetchone()
    by_state = conn.execute("SELECT state, count(*) AS n FROM order_card GROUP BY state ORDER BY state").fetchall()
    return {**row, "cards_by_state": {r["state"]: r["n"] for r in by_state}}


def messages(conn, mrn: str) -> list[dict]:
    """The WhatsApp thread for one patient: outbox (sent, blocked, drafts) and replies, in order."""
    if conn.execute("SELECT 1 FROM patient WHERE mrn = %s", (mrn,)).fetchone() is None:
        raise NotFound(f"Patient {mrn} not found")
    return conn.execute(
        """
        SELECT 'out' AS direction, message_id AS id, created_at, created_on AS day, recipient AS party, body,
               body_en, buttons, status, blocked_reason, template_id, actor, NULL::text AS button, NULL::text AS handling
        FROM outbox_message WHERE mrn = %(mrn)s
        UNION ALL
        SELECT 'in', inbound_id, created_at, received_on, from_role, text, NULL, NULL, NULL, NULL, NULL, NULL,
               button, handling
        FROM inbound_message WHERE mrn = %(mrn)s
        UNION ALL
        SELECT 'call', call_id, created_at, called_on, who, note, NULL, NULL, outcome, NULL, NULL, called_by,
               NULL, NULL
        FROM call_log WHERE mrn = %(mrn)s
        ORDER BY created_at
        """,
        {"mrn": mrn},
    ).fetchall()


def needs_you(conn) -> dict:
    """The coordinator's queue: open handovers, people tasks with deadlines, drafts awaiting approval."""
    return {
        "handovers": conn.execute(
            """
            SELECT h.handover_id, h.mrn, p.full_name, h.trigger, h.stage, h.patient_words, h.opened_on,
                   h.gate2_decision
            FROM handover h JOIN patient p USING (mrn) WHERE h.stage <> 'closed' ORDER BY h.handover_id
            """
        ).fetchall(),
        "tasks": conn.execute(
            """
            SELECT t.task_id, t.mrn, p.full_name, t.task_type, t.reason, t.owner_name, t.cover_name, t.assigned_name,
                   t.due_date, t.escalated_on, t.handover_id
            FROM coordinator_task t JOIN patient p USING (mrn)
            WHERE t.status = 'open' ORDER BY t.due_date NULLS LAST, t.task_id
            """
        ).fetchall(),
        "approvals": conn.execute(
            """
            SELECT o.message_id, o.mrn, p.full_name, o.template_id, o.body_en, o.reason_code
            FROM outbox_message o JOIN patient p USING (mrn) WHERE o.status = 'pending_approval' ORDER BY o.message_id
            """
        ).fetchall(),
    }


def doctor_queue(conn) -> list[dict]:
    """Needs my decision: handovers waiting for Gate 2 or Gate 3, with the patient's words and source."""
    return conn.execute(
        """
        SELECT h.handover_id, h.mrn, p.full_name, p.preferred_language, h.stage, h.trigger, h.patient_words,
               h.translation, h.source, h.call_note, h.gate1_by, h.gate2_decision, h.doctor,
               (SELECT due_date FROM coordinator_task t WHERE t.handover_id = h.handover_id AND t.status = 'open'
                  AND t.task_type IN ('doctor_decision', 'doctor_instruction') ORDER BY task_id DESC LIMIT 1) AS due_date
        FROM handover h JOIN patient p USING (mrn)
        WHERE h.stage IN ('awaiting_doctor', 'awaiting_instruction') ORDER BY h.handover_id
        """
    ).fetchall()


def handover_detail(conn, handover_id: int) -> dict:
    h = conn.execute("SELECT * FROM handover WHERE handover_id = %s", (handover_id,)).fetchone()
    if h is None:
        raise NotFound(f"Handover {handover_id} not found")
    return {**h, "tasks": conn.execute(
        """
        SELECT task_id, task_type, reason, status, owner_name, cover_name, assigned_name, due_date, escalated_on,
               done_by, done_note, done_on
        FROM coordinator_task WHERE handover_id = %s ORDER BY task_id
        """,
        (handover_id,),
    ).fetchall()}
