"""Demo reset: wipe the demo data and rebuild the same starting state every time.

Runs only while the database is on (owner approval). Clearing the audit ledger
is its one allowed exception to the no-delete rule; the new chain starts with a
demo_reset entry. Decisions: documents/build-guide-comparison.md.
"""
import csv
import hashlib
import io
import json

from psycopg.types.json import Jsonb

from . import audit, clock, consent, importer
from .reference import (DEMO_START_DATE, USERS, autonomy_policy, deadline_policy, message_templates,
                        plan_template, read_seed)

SEED_USER = "u_priya"

DEMO_TABLES = (
    "call_log", "inbound_message", "outbox_message", "gap_event", "handover",
    "order_card", "care_plan", "coordinator_task", "consent", "appointment", "import_exception",
    "patient", "import_batch", "app_user", "demo_clock", "config_versions", "audit_ledger",
)

# Same values as the Increment 0 seed (001_init.sql), kept identical on every reset.
RULE_THRESHOLDS = {
    "g0_booking_days_before_due": 7, "g0_booking_help_days_before_due": 3, "visit_grace_days": 2,
    "refill_grace_days": 3, "report_wait_days": 7, "return_window_days": 21,
    "g4_unanswered_messages": 2, "g4_wait_hours": 24,
}


def reset(conn, by: str | None = None) -> dict:
    """Rebuild the seed state inside the caller's transaction; returns counts and fingerprint."""
    conn.execute("SET LOCAL carepilot.demo_reset = 'on'")
    conn.execute(f"TRUNCATE {', '.join(DEMO_TABLES)} RESTART IDENTITY CASCADE")
    conn.execute("SET LOCAL carepilot.demo_reset = 'off'")
    conn.execute("ALTER SEQUENCE audit_ledger_seq RESTART WITH 1")

    audit.append(
        conn,
        actor_type="person" if by else "system",
        actor_name=by or "system",
        action="demo_reset",
        details={"seed_date": DEMO_START_DATE.isoformat(), "synthetic": True},
    )

    # Set-up (not audited): users, demo clock, signed-off config.
    for u in USERS:
        conn.execute("INSERT INTO app_user (user_id, name, role) VALUES (%s, %s, %s)",
                     (u["user_id"], u["name"], u["role"]))
    clock.set_today(conn, DEMO_START_DATE)
    template = plan_template()
    for kind, name, body, signed_off_by in (
        ("rule_thresholds", "default", RULE_THRESHOLDS, "Synthetic seed (pending clinical sign-off)"),
        ("plan_template", template["template_id"], template, template["signed_off_by"]),
        ("autonomy_policy", "default", autonomy_policy(), autonomy_policy()["signed_off_by"]),
        ("deadline_policy", "default", deadline_policy(), deadline_policy()["signed_off_by"]),
        *(("message_template", t["template_id"], t, t["approved_by"]) for t in message_templates()),
    ):
        conn.execute(
            """
            INSERT INTO config_versions (kind, name, version, body, status, signed_off_by, signed_off_at)
            VALUES (%s, %s, 1, %s, 'active', %s, now())
            """,
            (kind, name, Jsonb(body), signed_off_by),
        )

    # Audited, as the coordinator: the two imports and every consent record.
    importer.import_register(conn, SEED_USER, read_seed("register.csv").encode(), "register.csv")
    importer.import_appointments(conn, SEED_USER, read_seed("appointments.csv").encode(), "appointments.csv")
    # Each patient has a named coordinator (odd MRNs Priya Nair, even MRNs Meera Joseph).
    conn.execute("""UPDATE patient SET coordinator = CASE WHEN substring(mrn from 5)::int % 2 = 1
                    THEN 'Priya Nair' ELSE 'Meera Joseph' END""")
    for row in csv.DictReader(io.StringIO(read_seed("consent_records.csv"))):
        consent.record_consent(conn, row["recorded_by"], row["mrn"], row["consent_reference"],
                               row["scopes"].split(";"), row["recorded_on"])
    return {**counts(conn), "fingerprint": fingerprint(conn)}


def counts(conn) -> dict:
    return conn.execute(
        """
        SELECT (SELECT today FROM demo_clock WHERE id = 1)                          AS clock,
               (SELECT count(*) FROM app_user)                                      AS users,
               (SELECT count(*) FROM config_versions)                               AS config_versions,
               (SELECT count(*) FROM patient)                                       AS patients,
               (SELECT count(*) FROM appointment)                                   AS appointments,
               (SELECT count(*) FROM import_exception WHERE status = 'open')        AS open_exceptions,
               (SELECT count(*) FROM import_batch)                                  AS import_batches,
               (SELECT count(*) FROM consent)                                       AS consents,
               (SELECT count(*) FROM patient WHERE agent_on)                        AS agent_on,
               (SELECT count(*) FROM coordinator_task)                              AS tasks,
               (SELECT count(*) FROM care_plan)                                     AS care_plans,
               (SELECT count(*) FROM order_card)                                    AS order_cards,
               (SELECT count(*) FROM audit_ledger)                                  AS audit_rows
        """
    ).fetchone()


FINGERPRINT_QUERIES = (
    """SELECT mrn, full_name, phone, caregiver_name, caregiver_phone, preferred_language, status, agent_on
       FROM patient ORDER BY mrn""",
    "SELECT mrn, appointment_date, department FROM appointment ORDER BY mrn, appointment_date, department",
    """SELECT source_type, row_number, reason_code, status FROM import_exception
       ORDER BY source_type, row_number, reason_code""",
    "SELECT mrn, consent_reference, scopes, recorded_by, recorded_on FROM consent ORDER BY mrn, consent_reference",
    "SELECT mrn, task_type, status FROM coordinator_task ORDER BY mrn, task_type",
    "SELECT kind, name, version, body::text AS body FROM config_versions ORDER BY kind, name, version",
    "SELECT user_id, name, role FROM app_user ORDER BY user_id",
    "SELECT today FROM demo_clock",
    "SELECT (SELECT count(*) FROM care_plan) AS plans, (SELECT count(*) FROM order_card) AS cards",
)


def fingerprint(conn) -> str:
    """SHA-256 of the seed-relevant data; ids and timestamps are left out on purpose."""
    parts = [json.dumps(conn.execute(q).fetchall(), default=str, sort_keys=True) for q in FINGERPRINT_QUERIES]
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()
