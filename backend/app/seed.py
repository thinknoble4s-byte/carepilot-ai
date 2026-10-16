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

from . import audit, clock, consent, importer, plan, states
from .errors import Invalid
from .reference import (DEMO_START_DATE, STEP_A_USERS, autonomy_policy, deadline_policy, message_templates,
                        plan_template, read_seed, step_a_settings, uc1)

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


SCENARIOS = ("uc1", "step_a")
DEFAULT_SCENARIO = "uc1"


def reset(conn, by: str | None = None, scenario: str = DEFAULT_SCENARIO) -> dict:
    """Rebuild a seed state inside the caller's transaction; returns counts and fingerprint.

    uc1     Use Case 1 (default): TP001-TP003, Anita and Ravi, Sanjeevini Clinic, the narrow switches.
    step_a  Step A / Golden Path: 25 patients from the register, Priya and Meera, everything switched on.
    """
    if scenario not in SCENARIOS:
        raise Invalid(f"Unknown demo data set {scenario!r}; choose one of: {', '.join(SCENARIOS)}")
    conn.execute("SET LOCAL carepilot.demo_reset = 'on'")
    conn.execute(f"TRUNCATE {', '.join(DEMO_TABLES)} RESTART IDENTITY CASCADE")
    conn.execute("SET LOCAL carepilot.demo_reset = 'off'")
    conn.execute("ALTER SEQUENCE audit_ledger_seq RESTART WITH 1")

    audit.append(
        conn,
        actor_type="person" if by else "system",
        actor_name=by or "system",
        action="demo_reset",
        details={"scenario": scenario, "seed_date": DEMO_START_DATE.isoformat(), "synthetic": True},
    )
    if scenario == "uc1":
        _seed_uc1(conn)
    else:
        _seed_step_a(conn)
    return {**counts(conn), "scenario": scenario, "fingerprint": fingerprint(conn)}


def _config(conn, rows) -> None:
    for kind, name, body, signed_off_by in rows:
        conn.execute(
            """
            INSERT INTO config_versions (kind, name, version, body, status, signed_off_by, signed_off_at)
            VALUES (%s, %s, 1, %s, 'active', %s, now())
            """,
            (kind, name, Jsonb(body), signed_off_by),
        )


def _users(conn, users) -> None:
    for u in users:
        conn.execute("INSERT INTO app_user (user_id, name, role) VALUES (%s, %s, %s)",
                     (u["user_id"], u["name"], u["role"]))


def _seed_step_a(conn) -> None:
    # Set-up (not audited): users, demo clock, signed-off config.
    _users(conn, STEP_A_USERS)
    clock.set_today(conn, DEMO_START_DATE)
    template = plan_template()
    _config(conn, (
        ("rule_thresholds", "default", RULE_THRESHOLDS, "Synthetic seed (pending clinical sign-off)"),
        ("plan_template", template["template_id"], template, template["signed_off_by"]),
        ("autonomy_policy", "default", autonomy_policy(), autonomy_policy()["signed_off_by"]),
        ("deadline_policy", "default", deadline_policy(), deadline_policy()["signed_off_by"]),
        ("scenario_settings", "default", {**step_a_settings(), "scenario": "step_a"}, "Demo data set: Step A"),
        *(("message_template", f"{t['template_id']}:{t['language']}", t, t["approved_by"])
          for t in message_templates()),
    ))
    # Audited, as the coordinator: the two imports and every consent record.
    importer.import_register(conn, SEED_USER, read_seed("register.csv").encode(), "register.csv")
    importer.import_appointments(conn, SEED_USER, read_seed("appointments.csv").encode(), "appointments.csv")
    # Each patient has a named coordinator (odd MRNs Priya Nair, even MRNs Meera Joseph).
    conn.execute("""UPDATE patient SET coordinator = CASE WHEN substring(mrn from 5)::int % 2 = 1
                    THEN 'Priya Nair' ELSE 'Meera Joseph' END""")
    for row in csv.DictReader(io.StringIO(read_seed("consent_records.csv"))):
        consent.record_consent(conn, row["recorded_by"], row["mrn"], row["consent_reference"],
                               row["scopes"].split(";"), row["recorded_on"])


def _seed_uc1(conn) -> None:
    """Use Case 1 v1.1 section 4: TP001 (plan confirmed by hand in step 1), TP002 (no automated-messaging
    consent; plan and card seeded) and TP003 (plan seeded, card already Booked). Owner Anita, cover Ravi."""
    uc = uc1()
    coordinator = next(u["user_id"] for u in uc["users"] if u["name"] == uc["coordinator"])
    _users(conn, uc["users"])
    clock.set_today(conn, DEMO_START_DATE)
    template = uc["plan_template"]
    _config(conn, (
        ("rule_thresholds", "default", RULE_THRESHOLDS, "Synthetic seed (pending clinical sign-off)"),
        ("plan_template", template["template_id"], template, template["signed_off_by"]),
        ("autonomy_policy", "default", autonomy_policy(), autonomy_policy()["signed_off_by"]),
        ("deadline_policy", "default", uc["deadline_policy"], uc["deadline_policy"]["signed_off_by"]),
        ("scenario_settings", "default", {**uc["settings"], "scenario": "uc1"}, "Demo data set: Use Case 1"),
        *(("message_template", f"{t['template_id']}:{t['language']}", t, t["approved_by"])
          for t in uc["message_templates"]),
    ))
    batch = conn.execute(
        """
        INSERT INTO import_batch (source_type, file_name, imported_by, rows_read, rows_created,
                                  rows_skipped_identical, rows_exception)
        VALUES ('register', 'uc1 seed (TP001-TP003)', %s, %s, %s, 0, 0) RETURNING batch_id
        """,
        (coordinator, len(uc["patients"]), len(uc["patients"])),
    ).fetchone()["batch_id"]
    for pt in uc["patients"]:
        conn.execute(
            """
            INSERT INTO patient (mrn, full_name, phone, preferred_language, batch_id, coordinator)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (pt["mrn"], pt["full_name"], pt["phone"], pt["preferred_language"], batch, uc["coordinator"]),
        )
    audit.append(conn, actor_type="person", actor_name=coordinator, action="register_imported",
                 entity_type="import_batch", entity_id=str(batch),
                 details={"rows_read": len(uc["patients"]), "rows_created": len(uc["patients"]),
                          "file_name": "uc1 seed (TP001-TP003)"})
    for pt in uc["patients"]:
        c = pt["consent"]
        consent.record_consent(conn, coordinator, pt["mrn"], c["consent_reference"], c["scopes"], c["recorded_on"])
    for pt in uc["patients"]:
        if not pt["plan"]:
            continue
        result = plan.confirm_plan(conn, coordinator, pt["mrn"], template["template_id"],
                                   due_dates=pt["plan"]["due_dates"])
        if booked := pt["plan"].get("booked"):
            for card in result["cards"]:
                states.transition_card(conn, card["card_id"], "Booked", "person", coordinator,
                                       source=booked["source"], appointment_date=booked["appointment_date"])


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
