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
                        plan_templates, read_seed, step_a_settings, uc1, uc2a, uc2b, uc2c, uc2t)

SEED_USER = "u_priya"

DEMO_TABLES = (
    "demo_setting", "task_call_attempt", "proposal", "call_log", "inbound_message", "audio_blob", "outbox_message", "gap_event", "handover",
    "order_card", "care_plan", "coordinator_task", "consent", "appointment", "import_exception",
    "patient", "import_batch", "app_user", "demo_clock", "config_versions", "audit_ledger",
)

# Same values as the Increment 0 seed (001_init.sql), kept identical on every reset.
RULE_THRESHOLDS = {
    "g0_booking_days_before_due": 7, "g0_booking_help_days_before_due": 3, "visit_grace_days": 2,
    "refill_grace_days": 3, "report_wait_days": 7, "return_window_days": 21,
    "g4_unanswered_messages": 2, "g4_wait_hours": 24,
}


SCENARIOS = ("uc1", "uc2a", "uc2b", "uc2c", "uc2t", "step_a")
DEFAULT_SCENARIO = "uc1"


def reset(conn, by: str | None = None, scenario: str = DEFAULT_SCENARIO) -> dict:
    """Rebuild a seed state inside the caller's transaction; returns counts and fingerprint.

    uc1     Use Case 1 (default): TP001-TP003, Anita and Ravi, Sanjeevini Clinic, the narrow switches.
    uc2a    Use Case 2a: TP001 and TP004, clock Mon 9 Nov 10:15, replies on.
    uc2b    Use Case 2b: TP001, TP004-TP007, same start; taps and HELP become tasks.
    uc2c    Use Case 2c: TP001, TP004-TP007, Urgent - call the patient today; clock Tue 10 Nov 09:30.
    uc2t    Use Case 2T test cycle: TP001, TP004-TP007; D0 Mon 2 Nov.
    step_a  Step A / Golden Path: 25 patients from the register, Priya and Meera, everything switched on.

    Every data set offers the same three care plans (T1 hormone receptor positive, T2 hormone receptor negative,
    T3 HER2 positive) and seeds no plan: the coordinator chooses one for each patient. A scenario file may still
    carry plans (the tests' fixed copies); then the scripted start state is rebuilt from them.
    """
    if scenario not in SCENARIOS:
        raise Invalid(f"Unknown demo data set {scenario!r}; choose one of: {', '.join(SCENARIOS)}")
    conn.execute("SET LOCAL carepilot.demo_reset = 'on'")
    conn.execute(f"TRUNCATE {', '.join(DEMO_TABLES)} RESTART IDENTITY CASCADE")
    conn.execute("SET LOCAL carepilot.demo_reset = 'off'")
    conn.execute("ALTER SEQUENCE audit_ledger_seq RESTART WITH 1")
    clock.set_today(conn, DEMO_START_DATE)  # before the first entry, so the reset entry has a demo date too
    conn.execute("UPDATE demo_clock SET now_time = '09:00' WHERE id = 1")

    audit.append(
        conn,
        actor_type="person" if by else "system",
        actor_name=by or "system",
        action="demo_reset",
        details={"scenario": scenario, "seed_date": DEMO_START_DATE.isoformat(), "synthetic": True},
    )
    if scenario == "uc1":
        _seed_uc1(conn)
    elif scenario in ("uc2a", "uc2b"):
        _seed_after_uc1(conn, uc2a() if scenario == "uc2a" else uc2b())
    elif scenario == "uc2c":
        _seed_uc2c(conn, uc2c())
    elif scenario == "uc2t":
        _seed_uc1(conn, uc2t())
    else:
        _seed_step_a(conn)
    return {**counts(conn), "scenario": scenario, "fingerprint": fingerprint(conn)}


def _config(conn, rows) -> None:
    for kind, name, body, signed_off_by in rows:
        draft = bool(body.get("draft")) if isinstance(body, dict) else False  # unapproved: stored, never sent
        conn.execute(
            """
            INSERT INTO config_versions (kind, name, version, body, status, signed_off_by, signed_off_at)
            VALUES (%s, %s, 1, %s, %s, %s, CASE WHEN %s THEN NULL ELSE now() END)
            """,
            (kind, name, Jsonb(body), "draft" if draft else "active", None if draft else signed_off_by, draft),
        )


def _users(conn, users) -> None:
    for u in users:
        conn.execute("INSERT INTO app_user (user_id, name, role) VALUES (%s, %s, %s)",
                     (u["user_id"], u["name"], u["role"]))


def _seed_step_a(conn) -> None:
    # Set-up (not audited): users, demo clock, signed-off config.
    _users(conn, STEP_A_USERS)
    clock.set_today(conn, DEMO_START_DATE)
    _config(conn, (
        ("rule_thresholds", "default", RULE_THRESHOLDS, "Synthetic seed (pending clinical sign-off)"),
        *(("plan_template", t["template_id"], t, t["signed_off_by"]) for t in plan_templates()),
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


def _seed_after_uc1(conn, uc: dict) -> None:
    """Use Case 2a / 2b "after Use Case 1": demo clock Mon 9 Nov 10:15. With seeded plans (the tests' copies)
    the T-7 reminder has already gone at 07:30."""
    from datetime import date, time

    from . import agent, rules

    _seed_uc1(conn, uc)
    clock.set_today(conn, date.fromisoformat(uc["seed_date"]))
    rules.run_gap_check(conn)
    agent.run_agent(conn)
    clock.set_time(conn, time.fromisoformat(uc["seed_time"]))


def _seed_uc2c(conn, uc: dict) -> None:
    """Use Case 2c section 6, "after Use Case 2a, step 6", driven through the real code paths: each patient taps
    Booked on the T-7 reminder (Review visit Booked, patient-reported) and sends voice note HI-01 at 10:15. The
    flag and transcript are the Use Case 2a result (no AI call); the agent stops, sends the acknowledgement and the
    handover opens with the send block on; Anita sends it to the doctor at 11:00. The clock ends at Tue 10 Nov 09:30."""
    from datetime import date, time

    from . import gateway, handover
    from .reference import USERS_BY_ID

    _seed_after_uc1(conn, uc)
    hs = uc["handover_seed"]
    if not any(pt["plan"] for pt in uc["patients"]):  # no plans seeded: nothing was sent, so no replies yet
        clock.set_today(conn, date.fromisoformat(hs["start_date"]))
        clock.set_time(conn, time.fromisoformat(hs["start_time"]))
        return
    coordinator = USERS_BY_ID[next(u["user_id"] for u in uc["users"] if u["name"] == uc["coordinator"])]
    clock.set_time(conn, time.fromisoformat(hs["reply_time"]))
    for pt in uc["patients"]:
        reminder = conn.execute("SELECT message_id FROM outbox_message WHERE mrn = %s AND template_id = 'booking_reminder' "
                                "AND status = 'sent'", (pt["mrn"],)).fetchone()["message_id"]
        gateway.receive_reply(conn, pt["mrn"], button="booked", in_reply_to=reminder)
        gateway.receive_reply(conn, pt["mrn"], clip_id=hs["clip"], in_reply_to=reminder, understood=hs["understood"])
    clock.set_time(conn, time.fromisoformat(hs["sent_to_doctor_time"]))
    for h in conn.execute("SELECT handover_id FROM handover ORDER BY handover_id").fetchall():
        handover.gate1(conn, h["handover_id"], coordinator, "send_to_doctor")
    clock.set_today(conn, date.fromisoformat(hs["start_date"]))
    clock.set_time(conn, time.fromisoformat(hs["start_time"]))


def _seed_uc1(conn, uc: dict | None = None) -> None:
    """Use Case 1 v1.1 section 4: TP001 (plan confirmed by hand in step 1), TP002 (no automated-messaging
    consent; plan and card seeded) and TP003 (plan seeded, card already Booked). Owner Anita, cover Ravi."""
    uc = uc or uc1()
    coordinator = next(u["user_id"] for u in uc["users"] if u["name"] == uc["coordinator"])
    _users(conn, uc["users"])
    clock.set_today(conn, DEMO_START_DATE)
    seeded = [t for t in [uc.get("plan_template"), *uc.get("more_plan_templates", [])] if t]
    templates = {t["template_id"]: t for t in seeded or plan_templates(uc.get("plan_departments"))}
    _config(conn, (
        ("rule_thresholds", "default", RULE_THRESHOLDS, "Synthetic seed (pending clinical sign-off)"),
        *(("plan_template", t["template_id"], t, t["signed_off_by"]) for t in templates.values()),
        ("autonomy_policy", "default", autonomy_policy(), autonomy_policy()["signed_off_by"]),
        ("deadline_policy", "default", uc["deadline_policy"], uc["deadline_policy"]["signed_off_by"]),
        ("scenario_settings", "default", {**uc["settings"], "scenario": uc["scenario"]}, f"Demo data set: {uc['title']}"),
        *(("message_template", f"{t['template_id']}:{t['language']}", t, t["approved_by"])
          for t in uc["message_templates"]),
        *((("report_routing", "default", uc["report_routing"], uc["report_routing"]["signed_off_by"]),)
          if uc.get("report_routing") else ()),
    ))
    batch = conn.execute(
        """
        INSERT INTO import_batch (source_type, file_name, imported_by, rows_read, rows_created,
                                  rows_skipped_identical, rows_exception)
        VALUES ('register', %s, %s, %s, %s, 0, 0) RETURNING batch_id
        """,
        (f"{uc['scenario']} seed", coordinator, len(uc["patients"]), len(uc["patients"])),
    ).fetchone()["batch_id"]
    for pt in uc["patients"]:
        conn.execute(
            """
            INSERT INTO patient (mrn, full_name, phone, preferred_language, batch_id, coordinator, age, sex,
                                 caregiver_name, caregiver_phone)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (pt["mrn"], pt["full_name"], pt["phone"], pt["preferred_language"], batch, uc["coordinator"],
             pt.get("age"), pt.get("sex"), pt.get("caregiver_name"), pt.get("caregiver_phone")),
        )
    audit.append(conn, actor_type="person", actor_name=coordinator, action="register_imported",
                 entity_type="import_batch", entity_id=str(batch),
                 details={"rows_read": len(uc["patients"]), "rows_created": len(uc["patients"]),
                          "file_name": f"{uc['scenario']} seed"})
    for pt in uc["patients"]:
        c = pt["consent"]
        consent.record_consent(conn, coordinator, pt["mrn"], c["consent_reference"], c["scopes"], c["recorded_on"])
    for pt in uc["patients"]:
        if not pt["plan"]:
            continue
        result = plan.confirm_plan(conn, coordinator, pt["mrn"], pt["plan"].get("template_id", seeded[0]["template_id"]),
                                   due_dates=pt["plan"]["due_dates"])
        if booked := pt["plan"].get("booked"):
            for card in result["cards"]:
                states.transition_card(conn, card["card_id"], "Booked", "person", coordinator,
                                       source=booked["source"], appointment_date=booked["appointment_date"])
        for code, b in (pt["plan"].get("booked_items") or {}).items():  # Use Case 2T: one item already booked
            card = next(c for c in result["cards"] if c["item_code"] == code)
            states.transition_card(conn, card["card_id"], "Booked", "person", coordinator, source=b["source"],
                                   appointment_date=b["appointment_date"])
            conn.execute("UPDATE order_card SET appointment_time = %s WHERE card_id = %s",
                         (b.get("appointment_time"), card["card_id"]))


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
