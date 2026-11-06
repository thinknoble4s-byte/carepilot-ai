"""Read side for the Workspace, Patient journey and Summary screens."""
from . import messaging, policy, urgent_calls
from .errors import NotFound
from .states import DONE_STATES


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
            """SELECT h.*, i.kind AS inbound_kind, i.transcript, i.translation AS ai_translation, i.audio_id
               FROM handover h LEFT JOIN inbound_message i ON i.inbound_id = h.inbound_id
               WHERE h.mrn = %s ORDER BY (h.stage = 'closed'), h.handover_id DESC LIMIT 1""", (mrn,)
        ).fetchone(),
        "proposals": conn.execute(
            """SELECT p.*, i.transcript, i.translation, i.audio_id, i.kind, i.text
               FROM proposal p JOIN inbound_message i USING (inbound_id) WHERE p.mrn = %s
               ORDER BY p.proposal_id DESC""", (mrn,)).fetchall(),
        "send_block": conn.execute("SELECT 1 FROM handover WHERE mrn = %s AND stage <> 'closed'",
                                   (mrn,)).fetchone() is not None,
        "hold": conn.execute("SELECT 1 FROM proposal WHERE mrn = %s AND status = 'drafted'", (mrn,)).fetchone() is not None,
        "task_hold": messaging.task_hold(conn, mrn),
        "urgent_mode": bool(urgent_calls.config(conn)),
        "test_mode": bool(policy.settings(conn).get("test_cycle")),
        "appointments": conn.execute(
            "SELECT appointment_date, department FROM appointment WHERE mrn = %s ORDER BY appointment_date",
            (mrn,),
        ).fetchall(),
    }


def patient_card(conn, mrn: str) -> dict:
    """The pop-up behind a patient's name (testing round 31 Oct, defect 2): who she is, how to reach her and her
    family, her plan and next due items, open tasks, and her triage tickets. Read only; staff in Voice Triage
    see it."""
    p = conn.execute(
        """SELECT mrn, full_name, phone, caregiver_name, caregiver_phone, preferred_language, status, agent_on,
                  next_planned_action, coordinator, pause_until, pause_reason
           FROM patient WHERE mrn = %s""", (mrn,)).fetchone()
    if p is None:
        raise NotFound(f"Patient {mrn} not found")
    history = conn.execute("SELECT data FROM triage_history WHERE mrn = %s", (mrn,)).fetchone()
    h = history["data"] if history else {}
    clinical = h.get("clinical") or {}
    return {
        **p,
        "consent": conn.execute("SELECT consent_reference, scopes, recorded_on FROM consent WHERE mrn = %s "
                                "ORDER BY consent_id DESC LIMIT 1", (mrn,)).fetchone(),
        "plan": conn.execute("SELECT template_id, therapy, treatment_end_date, confirmed_by FROM care_plan "
                             "WHERE mrn = %s", (mrn,)).fetchone(),
        "record": {"title": h.get("title"), "age": h.get("age"), "sex": h.get("sex"),
                   "caregiver_relation": h.get("caregiver_relation"), "diagnosis": clinical.get("diagnosis"),
                   "treatment": clinical.get("active_treatment_completed"), "phase": clinical.get("current_phase"),
                   "medicines": [m["name"] for m in h.get("medicines") or []],
                   "arm_baseline": h.get("arm_baseline"), "last_headache": h.get("last_headache")} if h else None,
        "due": conn.execute(
            """SELECT item_code, name, due_date, state FROM order_card WHERE mrn = %s AND state <> ALL(%s)
               ORDER BY due_date LIMIT 5""", (mrn, list(DONE_STATES))).fetchall(),
        "appointments": conn.execute(
            "SELECT appointment_date, department FROM appointment WHERE mrn = %s ORDER BY appointment_date", (mrn,)
        ).fetchall(),
        "open_tasks": conn.execute("SELECT count(*) AS n FROM coordinator_task WHERE mrn = %s AND status = 'open'",
                                   (mrn,)).fetchone()["n"],
        "triage": conn.execute(
            """SELECT episode_id, ticket_id, tier, status, opened_at, closed_at FROM triage_episode WHERE mrn = %s
               ORDER BY episode_id DESC LIMIT 5""", (mrn,)).fetchall(),
    }


def patient_cards(conn, mrn: str) -> list[dict]:
    if conn.execute("SELECT 1 FROM patient WHERE mrn = %s", (mrn,)).fetchone() is None:
        raise NotFound(f"Patient {mrn} not found")
    return conn.execute(
        """
        SELECT c.card_id, c.item_code, c.card_type, c.name, c.department, c.due_date, c.state, c.appointment_date,
               c.appointment_time, c.booking_source, c.owner_name, c.cover_name, c.origin, c.care_plan_id,
               c.test_code, c.patient_says_booked, c.completed_on, c.report_road, c.specimen, c.verified_by, c.label,
               c.verified_at_demo,
               (SELECT row_to_json(r) FROM (SELECT report_id, file_name, received_at_demo, status FROM test_report
                  WHERE card_id = c.card_id AND status IN ('verified', 'auto_matched') ORDER BY report_id DESC LIMIT 1) r) AS report,
               g.rule AS gap_rule, g.status AS gap_status, g.blocked_reason AS gap_reason, g.detected_on AS gap_detected_on,
               g.acted_on AS gap_acted_on
        FROM order_card c
        LEFT JOIN LATERAL (SELECT * FROM gap_event WHERE card_id = c.card_id ORDER BY gap_id DESC LIMIT 1) g ON true
        WHERE c.mrn = %s ORDER BY c.card_id
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
    """The WhatsApp thread for one patient: outbox (sent, blocked, drafts), replies (taps, text, voice notes with
    their transcript, translation and flag) and calls, in order. A message and the replies it triggers are written in
    one transaction (same created_at), so the order within it is the order the audit ledger logged them (her message,
    then what was sent back). Each row carries the order
    it is about (a tap is about the message it answers) and its triage episode (ticket, tier, open or closed)."""
    if conn.execute("SELECT 1 FROM patient WHERE mrn = %s", (mrn,)).fetchone() is None:
        raise NotFound(f"Patient {mrn} not found")
    return conn.execute(
        """
        SELECT t.*, e.ticket_id AS ep_ticket, e.status AS ep_status, e.tier AS ep_tier, e.red_kind AS ep_red_kind,
               e.result->>'action_code' AS ep_action, e.closed_at AS ep_closed_at
        FROM (
        SELECT 'out' AS direction, message_id AS id, created_at, created_on AS day,
               (SELECT a.demo_time::time FROM audit_ledger a WHERE a.entity_type = 'outbox_message'
                  AND a.entity_id = message_id::text AND a.demo_time IS NOT NULL ORDER BY a.id LIMIT 1) AS at,
               recipient AS party,
               body, body_en, buttons, status, blocked_reason, template_id, actor, NULL::text AS button,
               NULL::text AS handling, language, NULL::text AS kind, NULL::text AS transcript, NULL::text AS translation,
               NULL::text AS ai_flag, NULL::boolean AS ai_failed, NULL::bigint AS audio_id, NULL::text AS clip_id,
               NULL::bigint AS reply_to, attachment_doc_id, triage_episode_id,
               (SELECT c.name FROM order_card c WHERE c.card_id = o.card_id) AS order_name,
               (SELECT min(a.id) FROM audit_ledger a WHERE a.mrn = %(mrn)s AND a.entity_type = 'outbox_message'
                  AND a.entity_id = message_id::text) AS seq
        FROM outbox_message o WHERE mrn = %(mrn)s
        UNION ALL
        SELECT 'in', inbound_id, i.created_at, received_on, received_time, from_role, text, NULL, NULL, NULL, NULL, NULL,
               NULL, button, handling, NULL, kind, transcript, translation, ai_flag, ai_failed, audio_id, clip_id,
               in_reply_to, NULL, coalesce(i.triage_episode_id, o.triage_episode_id),
               (SELECT c.name FROM order_card c WHERE c.card_id = o.card_id),
               (SELECT min(a.id) FROM audit_ledger a WHERE a.mrn = %(mrn)s AND a.entity_type = 'inbound_message'
                  AND a.entity_id = inbound_id::text)
        FROM inbound_message i LEFT JOIN outbox_message o ON o.message_id = i.in_reply_to WHERE i.mrn = %(mrn)s
        UNION ALL
        SELECT 'call', call_id, created_at, called_on, NULL, who, note, NULL, NULL, outcome, NULL, NULL, called_by,
               NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
        FROM call_log WHERE mrn = %(mrn)s
        ) t LEFT JOIN triage_episode e ON e.episode_id = t.triage_episode_id
        ORDER BY t.created_at, t.seq NULLS LAST, t.direction <> 'in', t.id
        """,
        {"mrn": mrn},
    ).fetchall()


def needs_you(conn) -> dict:
    """The coordinator's queue: open handovers, people tasks with deadlines, drafts awaiting approval."""
    return {
        "handovers": conn.execute(
            """
            SELECT h.handover_id, h.mrn, p.full_name, h.trigger, h.stage, h.patient_words, h.opened_on,
                   h.gate2_decision, h.opened_at_demo, i.kind AS inbound_kind
            FROM handover h JOIN patient p USING (mrn) LEFT JOIN inbound_message i ON i.inbound_id = h.inbound_id
            WHERE h.stage <> 'closed' ORDER BY h.handover_id
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
    """Needs my decision: handovers waiting for Gate 2 or Gate 3, with the patient's words and source, and
    (Use Case 2c REQ-17) a read-only line on the urgent call."""
    rows = conn.execute(
        """
        SELECT h.handover_id, h.mrn, p.full_name, p.preferred_language, h.stage, h.trigger, h.patient_words,
               h.translation, h.source, h.call_note, h.gate1_by, h.gate2_decision, h.doctor, h.ai_failed,
               i.kind AS inbound_kind, i.audio_id, i.transcript, h.inbound_id,
               (SELECT due_date FROM coordinator_task t WHERE t.handover_id = h.handover_id AND t.status = 'open'
                  AND t.task_type IN ('doctor_decision', 'doctor_instruction') ORDER BY task_id DESC LIMIT 1) AS due_date
        FROM handover h JOIN patient p USING (mrn) LEFT JOIN inbound_message i ON i.inbound_id = h.inbound_id
        WHERE h.stage IN ('awaiting_doctor', 'awaiting_instruction') ORDER BY h.handover_id
        """
    ).fetchall()
    return [{**r, "call_line": urgent_calls.call_line(conn, r["handover_id"])} for r in rows]


def handover_detail(conn, handover_id: int) -> dict:
    h = conn.execute(
        """SELECT h.*, i.kind AS inbound_kind, i.transcript, i.translation AS ai_translation, i.audio_id,
                  i.ai_attempts, i.ai_model
           FROM handover h LEFT JOIN inbound_message i ON i.inbound_id = h.inbound_id WHERE h.handover_id = %s""",
        (handover_id,)).fetchone()
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


def appointments(conn) -> list[dict]:
    """The appointment list on Intake & verification: rows imported from the hospital's list and the bookings
    patients made on WhatsApp (Booked, Booked elsewhere, or a slot they chose), with the order card each books."""
    return conn.execute(
        """SELECT a.appointment_id, a.mrn, p.full_name, a.appointment_date, a.appointment_time, a.department,
                  coalesce(a.item, c.name) AS item, a.source, a.batch_id, c.item_code, c.state AS card_state
           FROM appointment a JOIN patient p USING (mrn) LEFT JOIN order_card c USING (card_id)
           ORDER BY a.appointment_date, a.appointment_time NULLS FIRST, p.full_name""").fetchall()


def all_cards(conn) -> list[dict]:
    """Every order card across patients, with its latest gap (state and reason): the Workspace card list."""
    return conn.execute(
        """
        SELECT c.card_id, c.mrn, p.full_name, c.item_code, c.card_type, c.name, c.due_date, c.state, c.booking_source,
               c.appointment_date, c.appointment_time, c.owner_name, c.cover_name, c.origin, g.rule AS gap_rule, g.status AS gap_status,
               g.blocked_reason AS gap_reason
        FROM order_card c JOIN patient p USING (mrn)
        LEFT JOIN LATERAL (SELECT * FROM gap_event WHERE card_id = c.card_id ORDER BY gap_id DESC LIMIT 1) g ON true
        ORDER BY c.due_date, p.full_name, c.card_id
        """
    ).fetchall()
