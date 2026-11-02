"""CarePilot API (Increments 1-3: records and plan, the deterministic loop, the human safety path).

Each request runs in one database transaction: an action and its audit row
commit together, and any error rolls both back. The database is off by
default; only the owner starts it (scripts/db.sh start). Callers are picked by
the X-User header (demo profile, no login).
"""
import os
from datetime import date
from typing import Literal

import psycopg
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from . import (agent, ai, audit, clock, consent, db, gateway, handover, help_tasks, importer, messaging, patients, plan,
               programme,
               proposals, rules, seed, states, tasks, test_cycle, timeline, triage, urgent_calls, views, worklist)
from .auth import ANY_ROLE, CLINICAL, COORDINATOR, TRIAGE_VIEW, current_user, require_role
from .errors import DomainError
from . import policy
from .reference import ROLE_LABELS, USERS

app = FastAPI(title="CarePilot API", version="0.3.0")

if not os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
    # Local dev only: on AWS the Function URL adds CORS headers (adding them twice breaks browsers).
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(DomainError)
def domain_error(_request, exc: DomainError):
    return JSONResponse(status_code=exc.status, content={"detail": exc.detail})


def in_tx(fn, *args, **kwargs):
    """Run fn(conn, ...) in one transaction; 503 when the database is off."""
    try:
        with db.connect() as conn:
            return fn(conn, *args, **kwargs)
    except psycopg.OperationalError:
        raise HTTPException(status_code=503, detail="Database is not available; start it first")


# ---------------------------------------------------------------------------
# No database needed
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    """API is up; report whether the database is reachable, and if not, why."""
    try:
        with db.connect() as conn:
            conn.execute("SELECT 1")
        return {"api": "ok", "db": "available", "env": os.environ.get("APP_ENV", "local")}
    except psycopg.OperationalError:
        try:
            status = db.instance_status()
        except Exception:  # describe call failed; still answer
            status = None
        return {"api": "ok", "db": status or "unreachable", "env": os.environ.get("APP_ENV", "local")}


@app.post("/demo/start-db")
def start_db():
    """Anyone with the demo link may switch the stopped demo database on (no user: the database holds the users).
    It stops again by itself after 60 idle minutes and every night."""
    try:
        status = db.instance_status()
    except Exception:
        raise HTTPException(503, "The database status could not be read; try again in a minute") from None
    if status is None:
        raise HTTPException(409, "Not running on AWS: there is no demo database to start")
    if status != "stopped":
        return {"db": status}
    try:
        return {"db": db.start_instance()}
    except Exception:
        raise HTTPException(503, "AWS did not start the database; try again in a minute") from None


@app.get("/users")
def users():
    """Synthetic users for the "Viewing as" switcher: the people in the loaded demo data set
    (every known synthetic user while the database is off)."""
    rows = USERS
    try:
        with db.connect() as conn:
            loaded = conn.execute("SELECT user_id, name, role FROM app_user ORDER BY role, user_id").fetchall()
        if loaded:
            order = {"coordinator": 0, "oncologist": 1, "chief": 2}
            rows = sorted(loaded, key=lambda u: (order[u["role"]], u["user_id"]))
    except psycopg.Error:
        pass
    return [{"id": u["user_id"], "person": u["name"], "role": u["role"], "label": ROLE_LABELS[u["role"]]}
            for u in rows]


# ---------------------------------------------------------------------------
# Session, audit trail, demo clock and reset
# ---------------------------------------------------------------------------
@app.post("/session")
def select_user(user: dict = Depends(current_user)):
    def run(conn):
        return audit.append(conn, actor_type="person", actor_name=user["user_id"], action="session.user_selected",
                            entity_type="app_user", entity_id=user["user_id"],
                            details={"role": user["role"], "synthetic": True})

    return {"user": user, "audit": in_tx(run)}


@app.get("/audit")
def audit_trail(limit: int = 20, mrn: str | None = None, kind: str | None = None, days: int | None = None,
                q: str | None = None, date_from: date | None = Query(None, alias="from"),
                date_to: date | None = Query(None, alias="to")):
    """Audit search: patient, actor kind, text, and an inclusive From/To range on each entry's demo date."""
    limit = min(max(limit, 1), 400)
    if not (mrn or kind or days or q or date_from or date_to):
        return in_tx(lambda conn: {"entries": audit.recent(conn, limit), "chain": audit.verify(conn)})
    return in_tx(lambda conn: audit.search(conn, mrn=mrn, kind=kind, days=days, q=q, date_from=date_from,
                                           date_to=date_to, limit=limit))


@app.get("/clock")
def get_clock(_user: dict = Depends(current_user)):
    now = in_tx(clock.get_now)
    return {"today": now.date(), "time": now.strftime("%H:%M")}


class AdvanceIn(BaseModel):
    days: int


@app.post("/clock/advance")
def advance_clock(body: AdvanceIn, user: dict = Depends(require_role(*COORDINATOR))):
    def run(conn):
        today = clock.advance(conn, body.days, user["user_id"])
        urgent_calls.sweep(conn)
        test_cycle.sweep(conn)
        return today

    return {"today": in_tx(run)}


class ResetIn(BaseModel):
    scenario: str = seed.DEFAULT_SCENARIO


@app.post("/demo/reset")
def demo_reset(body: ResetIn | None = None, user: dict = Depends(require_role(*COORDINATOR))):
    return in_tx(seed.reset, user["user_id"], (body or ResetIn()).scenario)


class SetDateIn(BaseModel):
    date: str
    time: str | None = None


@app.post("/clock/set")
def set_clock(body: SetDateIn, user: dict = Depends(require_role(*COORDINATOR))):
    def run(conn):
        clock.set_forward(conn, body.date, user["user_id"], body.time)
        moved = urgent_calls.sweep(conn)  # Use Case 2c: cover step-in and Overdue at the times of day
        test_cycle.sweep(conn)  # Use Case 2T: report review to the doctor's cover after 1 working day
        now = clock.get_now(conn)
        return {"today": now.date(), "time": now.strftime("%H:%M"), "urgent": moved}

    return in_tx(run)


@app.get("/demo/settings")
def demo_settings(_user: dict = Depends(current_user)):
    def run(conn):
        s = policy.settings(conn)
        now = clock.get_now(conn)
        return {"scenario": s.get("scenario"), "clinic_name": s.get("clinic_name"), "act_on_replies": s["act_on_replies"],
                "sarvam_enabled": ai.sarvam_enabled(conn), "ai_provider": ai.provider_name(),
                "today": now.date(), "time": now.strftime("%H:%M"), "voice_triage": triage.config(conn)}

    return in_tx(run)


class SwitchIn(BaseModel):
    enabled: bool


@app.post("/demo/sarvam")
def sarvam_switch(body: SwitchIn, user: dict = Depends(require_role(*COORDINATOR))):
    """Test switch (UC2a-T4): with Sarvam off every AI call fails, so replies take the safe path."""
    def run(conn):
        from psycopg.types.json import Jsonb

        conn.execute("INSERT INTO demo_setting (key, value) VALUES ('sarvam_enabled', %s) "
                     "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (Jsonb(body.enabled),))
        audit.append(conn, actor_type="person", actor_name=user["user_id"], action="demo_switch_changed",
                     entity_type="demo_setting", entity_id="sarvam_enabled", outcome=str(body.enabled).lower())
        return {"sarvam_enabled": body.enabled}

    return in_tx(run)


@app.get("/proposals")
def list_proposals(_user: dict = Depends(require_role(*COORDINATOR))):
    return in_tx(proposals.waiting)


class ApproveIn(BaseModel):
    summary: str | None = None


@app.post("/proposals/{proposal_id}/approve")
def approve_proposal(proposal_id: int, body: ApproveIn, user: dict = Depends(require_role(*COORDINATOR))):
    return in_tx(proposals.approve, proposal_id, user["user_id"], body.summary)


class RejectIn(BaseModel):
    reason: str | None = None


@app.post("/proposals/{proposal_id}/reject")
def reject_proposal(proposal_id: int, body: RejectIn, user: dict = Depends(require_role(*COORDINATOR))):
    return in_tx(proposals.reject, proposal_id, user["user_id"], body.reason)


@app.get("/cards")
def all_cards(_user: dict = Depends(require_role(*CLINICAL))):
    return in_tx(views.all_cards)


# ---------------------------------------------------------------------------
# A2 imports and exceptions
# ---------------------------------------------------------------------------
@app.post("/imports", status_code=201)
async def upload(
    file: UploadFile = File(...),
    source_type: Literal["register", "appointments"] = Form(...),
    preview: bool = False,
    user: dict = Depends(require_role(*COORDINATOR)),
):
    data = await file.read()
    run = importer.import_register if source_type == "register" else importer.import_appointments

    def go(conn):
        if not preview:
            return run(conn, user["user_id"], data, file.filename)
        with conn.transaction(force_rollback=True):  # preview: show what would happen, keep nothing
            return {**run(conn, user["user_id"], data, file.filename), "preview": True}

    return in_tx(go)


@app.get("/exceptions")
def exceptions(status: Literal["open", "dismissed", "all"] = "open", batch_id: int | None = None,
               _user: dict = Depends(require_role(*COORDINATOR))):
    return in_tx(importer.list_exceptions, None if status == "all" else status, batch_id)


class DismissIn(BaseModel):
    reason: str | None = None


@app.post("/exceptions/{exception_id}/dismiss")
def dismiss(exception_id: int, body: DismissIn, user: dict = Depends(require_role(*COORDINATOR))):
    return in_tx(importer.dismiss_exception, user["user_id"], exception_id, body.reason)


# ---------------------------------------------------------------------------
# A3 consent, A4/A5 plan and cards
# ---------------------------------------------------------------------------
class ConsentIn(BaseModel):
    consent_reference: str
    scopes: list[str]
    recorded_on: str


@app.post("/patients/{mrn}/consent", status_code=201)
def record_consent(mrn: str, body: ConsentIn, user: dict = Depends(require_role(*COORDINATOR))):
    return in_tx(consent.record_consent, user["user_id"], mrn, body.consent_reference, body.scopes, body.recorded_on)


@app.get("/templates/{template_id}")
def template(template_id: str, _user: dict = Depends(require_role(*CLINICAL))):
    body = in_tx(plan.get_template, template_id)
    if body is None:
        raise HTTPException(status_code=404, detail=f"Template {template_id} not found")
    return body


class PlanIn(BaseModel):
    template_id: str
    therapy: str | None = None
    treatment_end_date: str | None = None
    identifiers_checked: list[str] = []
    due_dates: dict[str, str] | None = None  # for templates whose due dates are entered


@app.post("/patients/{mrn}/plan/confirm", status_code=201)
def confirm_plan(mrn: str, body: PlanIn, user: dict = Depends(require_role(*COORDINATOR))):
    return in_tx(plan.confirm_plan, user["user_id"], mrn, body.template_id, body.therapy,
                 body.treatment_end_date, body.identifiers_checked, body.due_dates)


# ---------------------------------------------------------------------------
# Read side: Workspace, Patient journey, Summary
# ---------------------------------------------------------------------------
@app.get("/patients")
def list_patients(_user: dict = Depends(require_role(*CLINICAL))):
    return in_tx(views.list_patients)


@app.get("/patients/{mrn}")
def patient(mrn: str, _user: dict = Depends(require_role(*CLINICAL))):
    return in_tx(views.patient_detail, mrn)


@app.get("/patients/{mrn}/cards")
def cards(mrn: str, _user: dict = Depends(require_role(*CLINICAL))):
    return in_tx(views.patient_cards, mrn)


@app.get("/tasks")
def list_tasks(_user: dict = Depends(require_role(*COORDINATOR))):
    return in_tx(views.open_tasks)


@app.get("/summary")
def summary(_user: dict = Depends(require_role(*ANY_ROLE))):
    return in_tx(views.summary)


# ---------------------------------------------------------------------------
# Increment 2: the morning run (07:00 gap check, 07:30 agent), send-guard, simulator
# The database is off by default, so these run on demand instead of on a schedule.
# ---------------------------------------------------------------------------
@app.post("/rules/gap-check")
def gap_check(_user: dict = Depends(require_role(*COORDINATOR))):
    return in_tx(rules.run_gap_check)


@app.post("/agent/run")
def agent_run(_user: dict = Depends(require_role(*COORDINATOR))):
    return {"actions": in_tx(agent.run_agent)}


@app.post("/deadlines/run")
def deadlines_run(_user: dict = Depends(require_role(*COORDINATOR))):
    return {"escalations": in_tx(tasks.run_deadlines)}


@app.post("/demo/morning-run")
def morning_run(_user: dict = Depends(require_role(*COORDINATOR))):
    def run(conn):
        return {"pauses_ended": patients.resume_expired(conn), "escalations": tasks.run_deadlines(conn),
                "gap_check": rules.run_gap_check(conn), "actions": agent.run_agent(conn)}

    return in_tx(run)


@app.get("/needs-you")
def needs_you(_user: dict = Depends(require_role(*COORDINATOR))):
    return in_tx(views.needs_you)


@app.get("/patients/{mrn}/messages")
def patient_messages(mrn: str, _user: dict = Depends(require_role(*CLINICAL))):
    return in_tx(views.messages, mrn)


class ReplyIn(BaseModel):
    button: str | None = None
    text: str | None = None
    from_role: str = "patient"
    in_reply_to: int | None = None
    clip_id: str | None = None   # "Record voice note": attaches a pre-recorded clip (UC2a)
    empty: bool = False          # Testing round 1 Nov (Worksheet2): a message with nothing in it
    event_id: str | None = None  # the same event sent twice creates nothing new (REQ-20)


@app.post("/simulator/{mrn}/reply", status_code=201)
def simulate_reply(mrn: str, body: ReplyIn, _user: dict = Depends(require_role(*COORDINATOR))):
    """The WhatsApp simulator: the demo operator plays the patient or caregiver."""
    return in_tx(gateway.receive_reply, mrn, from_role=body.from_role, button=body.button, text=body.text,
                 in_reply_to=body.in_reply_to, clip_id=body.clip_id, event_id=body.event_id, empty=body.empty)


@app.post("/simulator/{mrn}/voice", status_code=201)
async def simulator_voice(mrn: str, file: UploadFile = File(...), from_role: str = Form("patient"),
                          in_reply_to: int | None = Form(None), event_id: str | None = Form(None),
                          language: str | None = Form(None), _user: dict = Depends(require_role(*COORDINATOR))):
    """A voice note recorded (or an audio file uploaded) in the simulator: stored in the database, then the
    same path as a clip — Sarvam text, translation and the yes/no health flag. language: Hindi, Kannada, Telugu,
    English or auto (omit for the patient's language)."""
    audio = await file.read()
    return in_tx(gateway.receive_reply, mrn, from_role=from_role, in_reply_to=in_reply_to, event_id=event_id or None,
                 audio=audio, mime=file.content_type or "", spoken=language or None)


@app.get("/simulator/clips")
def simulator_clips(_user: dict = Depends(require_role(*COORDINATOR))):
    return in_tx(gateway.clips)


@app.get("/inbound/{inbound_id}/audio")
def inbound_audio(inbound_id: int, _user: dict = Depends(require_role(*CLINICAL))):
    def run(conn):
        row = conn.execute(
            """SELECT a.data, a.mime FROM inbound_message i JOIN audio_blob a USING (audio_id)
               WHERE i.inbound_id = %s""", (inbound_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="No audio for this message")
        return row

    row = in_tx(run)
    return Response(content=bytes(row["data"]), media_type=row["mime"])


@app.post("/outbox/{message_id}/approve")
def approve_message(message_id: int, user: dict = Depends(require_role(*COORDINATOR))):
    return in_tx(messaging.approve, message_id, user["user_id"])


@app.post("/outbox/{message_id}/reject")
def reject_message(message_id: int, user: dict = Depends(require_role(*COORDINATOR))):
    return in_tx(messaging.reject, message_id, user["user_id"])


class StatusIn(BaseModel):
    status: str
    reason: str | None = None


@app.post("/patients/{mrn}/status")
def patient_status(mrn: str, body: StatusIn, user: dict = Depends(require_role(*COORDINATOR))):
    return in_tx(patients.set_status, mrn, user["user_id"], body.status, body.reason)


class NoteIn(BaseModel):
    note: str | None = None


@app.post("/tasks/{task_id}/done")
def task_done(task_id: int, body: NoteIn, user: dict = Depends(require_role(*COORDINATOR))):
    def run(conn):
        t = conn.execute("SELECT task_type FROM coordinator_task WHERE task_id = %s", (task_id,)).fetchone()
        if t and t["task_type"] in help_tasks.TYPES:
            raise HTTPException(status_code=422, detail="Close this task with a result (POST /tasks/{id}/result)")
        if t and t["task_type"] == urgent_calls.TYPE and urgent_calls.config(conn):
            raise HTTPException(status_code=422, detail="An urgent call closes only with a reached call attempt and a note")
        return tasks.complete_task(conn, task_id, user["user_id"], body.note)

    return in_tx(run)


# ---------------------------------------------------------------------------
# Use Case 2b: help-booking, can't-come and help-request tasks
# ---------------------------------------------------------------------------
@app.get("/patients/{mrn}/tasks")
def patient_tasks(mrn: str, _user: dict = Depends(require_role(*CLINICAL))):
    def run(conn):
        rules = policy.deadlines(conn)
        return {"tasks": help_tasks.tasks_for(conn, mrn), "hold": help_tasks.task_hold_on(conn, mrn),
                "reasons": rules.get("cant_come_reasons") or help_tasks.DEFAULT_REASONS,
                "attempts_needed": int(rules.get("not_reached_after_attempts", 2))}

    return in_tx(run)


class AttemptIn(BaseModel):
    result: str  # reached | no_answer
    note: str | None = None
    appointment_date: str | None = None  # Use Case 2c: optional Record booking on a reached urgent call
    appointment_time: str | None = None


@app.post("/tasks/{task_id}/attempts", status_code=201)
def task_attempt(task_id: int, body: AttemptIn, user: dict = Depends(require_role(*COORDINATOR))):
    def run(conn):
        t = conn.execute("SELECT task_type FROM coordinator_task WHERE task_id = %s", (task_id,)).fetchone()
        if t and t["task_type"] == urgent_calls.TYPE:
            return urgent_calls.log_attempt(conn, task_id, user["user_id"], body.result, body.note,
                                            body.appointment_date, body.appointment_time)
        return help_tasks.log_attempt(conn, task_id, user["user_id"], body.result, body.note)

    return in_tx(run)


class PassIn(BaseModel):
    reason: str | None = None


@app.post("/tasks/{task_id}/pass-to-cover")
def task_pass_to_cover(task_id: int, body: PassIn, user: dict = Depends(require_role(*COORDINATOR))):
    """Use Case 2c REQ-09: the same urgent task moves to the cover at once, with a reason."""
    return in_tx(urgent_calls.pass_to_cover, task_id, user["user_id"], body.reason)


class ResultIn(BaseModel):
    result: str
    note: str | None = None
    appointment_date: str | None = None
    appointment_time: str | None = None
    reason_code: str | None = None


@app.post("/tasks/{task_id}/result")
def task_result(task_id: int, body: ResultIn, user: dict = Depends(require_role(*COORDINATOR))):
    return in_tx(help_tasks.record_result, task_id, user["user_id"], result=body.result, note=body.note,
                 appointment_date=body.appointment_date, appointment_time=body.appointment_time,
                 reason_code=body.reason_code)


# ---------------------------------------------------------------------------
# Use Case 2T: the test cycle (appointment list rows, simulated lab, outside reports, the doctor's review)
# ---------------------------------------------------------------------------
class AppointmentRow(BaseModel):
    mrn: str
    test_code: str
    date: str
    time: str | None = None
    attended: bool = False


class AppointmentListIn(BaseModel):
    rows: list[AppointmentRow]
    file_name: str | None = None


@app.post("/test-cycle/appointment-list")
def test_appointment_list(body: AppointmentListIn, user: dict = Depends(require_role(*COORDINATOR))):
    return in_tx(test_cycle.load_appointment_rows, user["user_id"], [r.model_dump() for r in body.rows], body.file_name)


class LabReportIn(BaseModel):
    mrn: str
    test_code: str
    report_mrn: str | None = None  # the MRN printed on the report (default: the patient's)
    with_file: bool = True
    report_ref: str | None = None
    deliver: bool = True  # False: the report sits in the hospital data only (the report check finds it)


@app.post("/simulated-lab/report", status_code=201)
def simulated_lab_report(body: LabReportIn, user: dict = Depends(require_role(*COORDINATOR))):
    """Demo control (SIMULATED): the hospital lab sends a synthetic report with an MRN and a test code."""
    return in_tx(test_cycle.lab_report, mrn=body.mrn, test_code=body.test_code, report_mrn=body.report_mrn,
                 with_file=body.with_file, report_ref=body.report_ref, pressed_by=user["user_id"],
                 deliver=body.deliver)


@app.post("/simulator/{mrn}/document", status_code=201)
async def simulator_document(mrn: str, file: UploadFile | None = File(None), from_role: str = Form("patient"),
                             event_id: str | None = Form(None), variant: str = Form("clear"),
                             from_phone: str | None = Form(None), note: str | None = Form(None),
                             card_id: int | None = Form(None), _user: dict = Depends(require_role(*COORDINATOR))):
    """The patient sends a photo or PDF of an outside report. Without a file: a synthetic outside-lab PDF (variant:
    clear, blurred, ct_scan, other_name, no_mrn, unreadable, future_date) for card_id, by default the test the
    patient last said was done outside. from_phone: another sender number (demo)."""
    data = await file.read() if file else None
    extra = {"from_phone": from_phone, "note": note}

    def run(conn):
        if data is None:
            p = conn.execute("SELECT * FROM patient WHERE mrn = %s", (mrn,)).fetchone()
            if p is None:
                raise HTTPException(status_code=404, detail=f"Patient {mrn} not found")
            card = conn.execute(
                """SELECT name, specimen FROM order_card c WHERE mrn = %s AND card_type = 'test'
                   ORDER BY card_id = %s DESC, (SELECT max(check_id) FROM report_check k WHERE k.card_id = c.card_id
                     AND k.status = 'sent' AND k.answer = 'done_outside') DESC NULLS LAST, card_id LIMIT 1""",
                (mrn, card_id or 0)).fetchone()
            pdf = test_cycle.outside_report_pdf(p, clock.get_today(conn), card["name"] if card else "Blood test",
                                                variant, imaging=bool(card) and card["specimen"] is False)
            return gateway.receive_document(conn, mrn, data=pdf, mime="application/pdf",
                                            file_name=f"outside-lab-report-{variant}.pdf", from_role=from_role,
                                            event_id=event_id, **extra)
        return gateway.receive_document(conn, mrn, data=data, mime=(file.content_type or "").split(";")[0],
                                        file_name=file.filename or "document", from_role=from_role, event_id=event_id,
                                        **extra)

    return in_tx(run)


@app.get("/patients/{mrn}/reports")
def patient_reports(mrn: str, _user: dict = Depends(require_role(*CLINICAL))):
    return in_tx(test_cycle.reports_for, mrn)


@app.get("/reports/{report_id}/file")
def report_file(report_id: int, user: dict = Depends(require_role(*CLINICAL))):
    """Open source file: audited with the reader's name and demo time (REQ-14)."""
    f = in_tx(test_cycle.open_file, report_id, user)
    return Response(content=f["data"], media_type=f["mime"],
                    headers={"Content-Disposition": f'inline; filename="{f["file_name"]}"'})


class VerifyIn(BaseModel):
    matched: list[str] = []
    card_id: int | None = None


@app.post("/reports/{report_id}/verify")
def verify_report(report_id: int, body: VerifyIn, user: dict = Depends(require_role(*COORDINATOR))):
    return in_tx(test_cycle.verify_outside, report_id, user["user_id"], body.matched, body.card_id)


class RejectReportIn(BaseModel):
    reason: str | None = None


@app.post("/reports/{report_id}/reject")
def reject_report(report_id: int, body: RejectReportIn, user: dict = Depends(require_role(*COORDINATOR))):
    return in_tx(test_cycle.reject_outside, report_id, user["user_id"], body.reason)


class CopyFlagIn(BaseModel):
    reason: str | None = None


@app.get("/copies")
def info_copies(owner: str | None = None, _user: dict = Depends(require_role(*CLINICAL))):
    """Copies for your information: auto-matched outside reports and wrong-report notes (no due date)."""
    return in_tx(test_cycle.copies, owner)


@app.post("/copies/{copy_id}/confirm")
def confirm_copy(copy_id: int, user: dict = Depends(require_role(*COORDINATOR))):
    return in_tx(test_cycle.confirm_copy, copy_id, user["user_id"])


@app.post("/copies/{copy_id}/flag")
def flag_copy(copy_id: int, body: CopyFlagIn, user: dict = Depends(require_role(*COORDINATOR))):
    return in_tx(test_cycle.flag_copy, copy_id, user["user_id"], body.reason)


@app.post("/copies/{copy_id}/read")
def read_copy(copy_id: int, user: dict = Depends(require_role(*COORDINATOR))):
    return in_tx(test_cycle.read_copy, copy_id, user["user_id"])


@app.get("/report-routing")
def report_routing(_user: dict = Depends(current_user)):
    return in_tx(lambda conn: {**test_cycle.routing(conn), "use_case_2t": bool(test_cycle.config(conn))})


class RoutingIn(BaseModel):
    mode: str | None = None
    threshold: int | None = None
    weights: dict[str, int] | None = None


@app.put("/report-routing")
def set_report_routing(body: RoutingIn, user: dict = Depends(current_user)):
    """REQ-29: only the clinical lead changes the threshold, the weights or Shadow / Live; each change audited."""
    return in_tx(test_cycle.set_routing, user, mode=body.mode, threshold=body.threshold, weights=body.weights)


@app.get("/report-routing/evaluation")
def report_routing_evaluation(_user: dict = Depends(require_role(*CLINICAL))):
    """REQ-29: the synthetic document set: how many reports that should not pass reached the threshold."""
    return in_tx(test_cycle.evaluate)


@app.post("/reviews/{review_id}/noted")
def review_noted(review_id: int, user: dict = Depends(require_role("oncologist"))):
    return in_tx(test_cycle.noted, review_id, user)


@app.get("/doctor/verifications")
def doctor_verifications(_user: dict = Depends(require_role(*CLINICAL))):
    """Tests waiting for the doctor's Verify: a non-specimen test (imaging) completed."""
    return in_tx(test_cycle.to_verify)


@app.post("/cards/{card_id}/verify")
def verify_card(card_id: int, user: dict = Depends(require_role("oncologist"))):
    """Only the doctor sets a non-specimen test (imaging) to Verified; reports are verified by their identifiers."""
    return in_tx(test_cycle.doctor_verify, card_id, user)


@app.get("/doctor/reviews")
def doctor_reviews(_user: dict = Depends(require_role(*CLINICAL))):
    return in_tx(lambda conn: {"open": test_cycle.doctor_items(conn), "recent": test_cycle.recent_decisions(conn)})


class ReviewDecisionIn(BaseModel):
    decision: str | None = None  # continue | discussion; no default: the doctor must choose


@app.post("/reviews/{review_id}/decision")
def review_decision(review_id: int, body: ReviewDecisionIn, user: dict = Depends(require_role("oncologist"))):
    return in_tx(test_cycle.decide, review_id, user, body.decision)


class ReviewInstructionIn(BaseModel):
    text: str | None = None
    days: int | None = None


@app.post("/reviews/{review_id}/instruction")
def review_instruction(review_id: int, body: ReviewInstructionIn, user: dict = Depends(require_role("oncologist"))):
    return in_tx(test_cycle.instruction, review_id, user, body.text, body.days)


class TransitionIn(BaseModel):
    to_state: str
    source: str | None = None
    appointment_date: str | None = None
    file_ref: str | None = None
    matched_identifiers: int | None = None


@app.post("/cards/{card_id}/transition")
def card_transition(card_id: int, body: TransitionIn, user: dict = Depends(require_role(*COORDINATOR))):
    """Demo control for illegal-move checks (UC2T-T3): a refused move changes nothing and is audited."""
    ctx = {k: v for k, v in body.model_dump().items() if k != "to_state" and v is not None}
    try:
        return in_tx(states.transition_card, card_id, body.to_state, "person", user["user_id"], **ctx)
    except states.IllegalTransition as e:
        def refused(conn):
            row = conn.execute("SELECT mrn, state FROM order_card WHERE card_id = %s", (card_id,)).fetchone()
            audit.append(conn, actor_type="person", actor_name=user["user_id"], action="transition_refused",
                         entity_type="order_card", entity_id=str(card_id), mrn=row["mrn"] if row else None,
                         outcome="refused", details={"from": row["state"] if row else None, "to": body.to_state,
                                                     "reason": str(e), **{k: str(v) for k, v in ctx.items()}})

        in_tx(refused)
        raise


# ---------------------------------------------------------------------------
# Increment 3: handover, Gates 1-3, closure
# ---------------------------------------------------------------------------
class ManualHandoverIn(BaseModel):
    reason: str | None = None


@app.post("/patients/{mrn}/handover", status_code=201)
def manual_handover(mrn: str, body: ManualHandoverIn, user: dict = Depends(require_role(*COORDINATOR))):
    def run(conn):
        if not body.reason or not body.reason.strip():
            raise HTTPException(status_code=422, detail="A reason is required")
        h, created = handover.open_handover(conn, mrn, trigger="manual", actor_type="person", actor=user["user_id"],
                                            patient_words=body.reason.strip(), source=f"Opened by {user['name']}")
        return {**h, "created": created}

    return in_tx(run)


@app.get("/doctor/queue")
def doctor_queue(_user: dict = Depends(require_role(*CLINICAL))):
    return in_tx(views.doctor_queue)


@app.get("/handovers/{handover_id}")
def get_handover(handover_id: int, _user: dict = Depends(require_role(*CLINICAL))):
    return in_tx(views.handover_detail, handover_id)


class Gate1In(BaseModel):
    choice: str | None = None


@app.post("/handovers/{handover_id}/gate1")
def gate1(handover_id: int, body: Gate1In, user: dict = Depends(require_role(*COORDINATOR))):
    return in_tx(handover.gate1, handover_id, user, body.choice)


@app.post("/handovers/{handover_id}/call")
def record_call(handover_id: int, body: NoteIn, user: dict = Depends(require_role(*COORDINATOR))):
    return in_tx(handover.record_call, handover_id, user, body.note)


class Gate2In(BaseModel):
    decision: str | None = None  # no default: the doctor must choose


@app.post("/handovers/{handover_id}/gate2")
def gate2(handover_id: int, body: Gate2In, user: dict = Depends(require_role("oncologist"))):
    return in_tx(handover.gate2, handover_id, user, body.decision)


class InstructionIn(BaseModel):
    text: str | None = None
    days: int | None = None  # a visit within N days; omit for an instruction without a new card


@app.post("/handovers/{handover_id}/instruction")
def instruction(handover_id: int, body: InstructionIn, user: dict = Depends(require_role("oncologist"))):
    return in_tx(handover.record_instruction, handover_id, user, body.text, body.days)


class CloseIn(BaseModel):
    reason: str | None = None


@app.post("/handovers/{handover_id}/close")
def close_handover(handover_id: int, body: CloseIn, user: dict = Depends(require_role(*COORDINATOR))):
    return in_tx(handover.close, handover_id, user, body.reason)


# ---------------------------------------------------------------------------
# Screens matching prototype v7.2: worklist, timeline, calls, pause, Care Plan, indicators
# ---------------------------------------------------------------------------
@app.get("/worklist")
def get_worklist(user: dict = Depends(require_role(*COORDINATOR))):
    return in_tx(worklist.build, user["name"])


@app.get("/department-lists")
def department_lists(_user: dict = Depends(require_role(*COORDINATOR))):
    return in_tx(worklist.department_lists)


@app.get("/patients/{mrn}/timeline")
def patient_timeline(mrn: str, _user: dict = Depends(require_role(*CLINICAL))):
    return in_tx(timeline.build, mrn)


class CallIn(BaseModel):
    who: str = "patient"
    outcome: str
    note: str | None = None


@app.post("/patients/{mrn}/calls", status_code=201)
def log_call(mrn: str, body: CallIn, user: dict = Depends(require_role(*COORDINATOR))):
    return in_tx(patients.log_call, mrn, user["user_id"], body.who, body.outcome, body.note)


class PauseIn(BaseModel):
    until: str
    reason: str | None = None


@app.post("/patients/{mrn}/pause")
def pause_patient(mrn: str, body: PauseIn, user: dict = Depends(require_role(*COORDINATOR))):
    return in_tx(patients.pause, mrn, user["user_id"], body.until, body.reason)


@app.post("/patients/{mrn}/remind", status_code=201)
def remind_patient(mrn: str, user: dict = Depends(require_role(*COORDINATOR))):
    return in_tx(patients.send_reminder, mrn, user["user_id"])


@app.get("/decisions/recent")
def recent_decisions(_user: dict = Depends(require_role(*CLINICAL))):
    return in_tx(timeline.recent_decisions)


@app.get("/care-plan")
def care_plan(_user: dict = Depends(require_role(*CLINICAL))):
    return in_tx(programme.care_plan)


@app.get("/kpis")
def kpis(_user: dict = Depends(require_role(*ANY_ROLE))):
    return in_tx(programme.kpis)


@app.get("/patients/{mrn}/assessment")
def patient_assessment(mrn: str, _user: dict = Depends(require_role(*CLINICAL))):
    """The patient's worklist status and reasons (also for the oncologist's read-only view)."""
    def run(conn):
        data = worklist.build(conn)
        row = next((r for r in data["patients"] if r["mrn"] == mrn), None)
        if row is None:
            raise HTTPException(status_code=404, detail=f"Patient {mrn} not found")
        return {**row, "today": data["today"]}

    return in_tx(run)


class ModeIn(BaseModel):
    mode: str


@app.post("/care-plan/autonomy/{task}")
def set_task_mode(task: str, body: ModeIn, user: dict = Depends(require_role(*COORDINATOR))):
    return in_tx(programme.set_task_mode, task, body.mode, user["user_id"])


# ---------------------------------------------------------------------------
# Voice Triage MVP-lite (guide v1.1): the call (driven from the phone simulator), the tier, and what people do.
# ---------------------------------------------------------------------------
@app.get("/triage/episodes")
def triage_episodes(status: str = "open", mrn: str | None = None, _user: dict = Depends(require_role(*TRIAGE_VIEW))):
    return in_tx(triage.episodes, status, mrn)


@app.get("/triage/episodes/{episode_id}")
def triage_episode(episode_id: int, _user: dict = Depends(require_role(*TRIAGE_VIEW))):
    return in_tx(triage.state, episode_id)


@app.get("/triage/questions")
def triage_questions(_user: dict = Depends(require_role(*TRIAGE_VIEW))):
    """The question bank and how the router picks from it (no database needed)."""
    return triage.question_bank()


@app.get("/patients/{mrn}/card")
def patient_card(mrn: str, _user: dict = Depends(require_role(*TRIAGE_VIEW))):
    return in_tx(views.patient_card, mrn)


@app.post("/triage/episodes/{episode_id}/pickup")
def triage_pickup(episode_id: int, user: dict = Depends(require_role(*COORDINATOR))):
    """Demo: the patient picks up the automated call."""
    return in_tx(triage.pickup, episode_id, user)


class TriageAnswerIn(BaseModel):
    text: str | None = None
    value: float | str | None = None  # an on-screen answer button: YES, NO, UNKNOWN, NO_THERMOMETER or a number
    key: str | None = None            # keypad: "0" for a nurse
    silence: bool = False


@app.post("/triage/episodes/{episode_id}/answer")
def triage_answer(episode_id: int, body: TriageAnswerIn, user: dict = Depends(require_role(*COORDINATOR))):
    """Demo: the patient's answer to the current question (words, an on-screen button, the key 0, or silence)."""
    return in_tx(triage.answer, episode_id, user, text=body.text, value=body.value, key=body.key, silence=body.silence)


@app.post("/triage/episodes/{episode_id}/answer-voice")
async def triage_answer_voice(episode_id: int, file: UploadFile = File(...),
                              user: dict = Depends(require_role(*COORDINATOR))):
    """Demo: a spoken answer recorded in the browser; Saaras hears it (stored with the call)."""
    audio = await file.read()
    if not audio or len(audio) > 2_000_000:
        raise HTTPException(status_code=422, detail="Keep the spoken answer short (under 30 seconds)")
    return in_tx(triage.answer, episode_id, user, audio=audio, mime=file.content_type or "audio/wav")


@app.post("/triage/episodes/{episode_id}/no-answer")
def triage_no_answer(episode_id: int, user: dict = Depends(require_role(*COORDINATOR))):
    """Demo: "She does not answer": the call rings out (the second call is 5 minutes later)."""
    return in_tx(triage.no_answer, episode_id, user)


class PlayIn(BaseModel):
    scenario: str


@app.post("/triage/episodes/{episode_id}/play")
def triage_play(episode_id: int, body: PlayIn, user: dict = Depends(require_role(*COORDINATOR))):
    """Demo: "Play as" a scenario of the guide (the scripted Tamil answers go through the same parsing and rules)."""
    return in_tx(triage.play_as, episode_id, user, body.scenario)


@app.get("/triage/episodes/{episode_id}/turns/{index}/audio")
def triage_turn_audio(episode_id: int, index: int, _user: dict = Depends(require_role(*TRIAGE_VIEW))):
    """Bulbul audio of a call line (made once, then cached), or the patient's recorded answer."""
    found = in_tx(triage.turn_audio, episode_id, index)
    if found is None:
        raise HTTPException(status_code=404, detail="No audio for this line (simulated mode shows the text only)")
    return Response(content=found[0], media_type=found[1])


@app.get("/triage/episodes/{episode_id}/recording")
def triage_recording(episode_id: int, user: dict = Depends(require_role(*TRIAGE_VIEW))):
    """The whole call as one WAV (Bulbul's lines and her spoken answers in call order), to check the transcript."""
    found = in_tx(triage.recording, episode_id, user)
    if found is None:
        raise HTTPException(status_code=404, detail="No call audio: she did not answer, or simulated mode (text only)")
    return Response(content=found, media_type="audio/wav")


@app.get("/triage/documents/{doc_id}")
def triage_document(doc_id: int, _user: dict = Depends(require_role(*TRIAGE_VIEW))):
    doc = in_tx(triage.document, doc_id)
    return Response(content=bytes(doc["data"]), media_type=doc["mime"],
                    headers={"Content-Disposition": f'inline; filename="{doc["file_name"]}"'})


@app.get("/triage/episodes/{episode_id}/outcome.pdf")
def triage_outcome_pdf(episode_id: int, _user: dict = Depends(require_role(*TRIAGE_VIEW))):
    """Testing round 2 Nov: the triage outcome PDF for any episode (staff only; built from the record when opened)."""
    pdf = in_tx(triage.outcome_pdf, episode_id)
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="triage-outcome-{episode_id}.pdf"'})


class ReachCallIn(BaseModel):
    outcome: str
    next: str | None = None
    note: str | None = None


@app.post("/triage/episodes/{episode_id}/reach-call")
def triage_reach_call(episode_id: int, body: ReachCallIn,
                      user: dict = Depends(require_role(*COORDINATOR))):
    """Testing round 1 Nov (Worksheet2): the coordinator calls her after an empty message and two unanswered calls."""
    return in_tx(triage.reach_call, episode_id, user, body.outcome, body.next, body.note)


@app.post("/triage/episodes/{episode_id}/acknowledge")
def triage_acknowledge(episode_id: int, user: dict = Depends(require_role(*CLINICAL))):
    """Triage v3: the Doctor on Duty acknowledges Red and Amber. A role that may not is refused with the reason."""
    return in_tx(triage.acknowledge, episode_id, user)


class TriageNoteIn(BaseModel):
    note: str | None = None


@app.post("/triage/episodes/{episode_id}/reopen")
def triage_reopen(episode_id: int, body: TriageNoteIn,
                  user: dict = Depends(require_role(*CLINICAL))):
    """Any staff reopens a closed ticket, with a reason."""
    return in_tx(triage.reopen, episode_id, user, body.note)


@app.post("/triage/episodes/{episode_id}/send-package")
def triage_send_package(episode_id: int,
                        user: dict = Depends(require_role(*CLINICAL))):
    """Red (unconfirmed) only: the coordinator or the Doctor on Duty sends the emergency package."""
    return in_tx(triage.send_package, episode_id, user)


@app.post("/triage/episodes/{episode_id}/callback")
def triage_callback(episode_id: int, body: TriageNoteIn, user: dict = Depends(require_role(*COORDINATOR))):
    """Triage v3: Red (unconfirmed): the coordinator calls her back within 15 minutes and records what she found."""
    return in_tx(triage.callback, episode_id, user, body.note)


class ReviewIn(BaseModel):
    action: str
    reason: str | None = None


@app.post("/triage/episodes/{episode_id}/review")
def triage_review(episode_id: int, body: ReviewIn, user: dict = Depends(require_role(*COORDINATOR))):
    """Triage v3: the coordinator's review of a Red or Amber ticket: CONFIRM, or FLAG as wrong (with why). Not a
    gate; Raise tier is POST .../tier."""
    return in_tx(triage.review, episode_id, user, body.action, body.reason)


class CloseIn(BaseModel):
    summary: str | None = None
    recommendation: str | None = None
    contact: str | None = None


@app.post("/triage/episodes/{episode_id}/summary-draft")
def triage_summary_draft(episode_id: int, body: CloseIn, user: dict = Depends(require_role(*CLINICAL))):
    """Triage v3: the 2-sentence closing summary drafted from the record and the recommendation (to edit)."""
    return in_tx(triage.summary_draft, episode_id, user, body.recommendation, body.contact)


@app.post("/triage/episodes/{episode_id}/close")
def triage_close(episode_id: int, body: CloseIn, user: dict = Depends(require_role(*CLINICAL))):
    """Triage v3: Red and Amber the Doctor on Duty closes (recommendation, how she contacted her, the summary);
    Green the coordinator. The summary goes to the Patient journey."""
    return in_tx(triage.close, episode_id, user, summary=body.summary, recommendation=body.recommendation,
                 contact=body.contact)


class OrderIn(BaseModel):
    order: str
    note: str | None = None


@app.post("/triage/episodes/{episode_id}/order")
def triage_order(episode_id: int, body: OrderIn, user: dict = Depends(require_role("oncologist"))):
    """Physiotherapy referral, Bring forward blood tests, Advise visit, Medication order (the note is sent to her
    word for word) or No order needed. Several may be recorded (the doctor decides the care)."""
    return in_tx(triage.doctor_order, episode_id, user, body.order, body.note)


class CallLogIn(BaseModel):
    outcome: str
    note: str | None = None


@app.post("/triage/episodes/{episode_id}/doctor-call")
def triage_doctor_call(episode_id: int, body: CallLogIn, user: dict = Depends(require_role("oncologist"))):
    """Amber workflow v2: the duty doctor logs each call to her, PICKED_UP or NO_ANSWER."""
    return in_tx(triage.doctor_call, episode_id, user, body.outcome, body.note)


@app.post("/triage/episodes/{episode_id}/attender-call")
def triage_attender_call(episode_id: int, body: CallLogIn, user: dict = Depends(require_role("coordinator"))):
    """Amber workflow v2: after the doctor's two unanswered calls the coordinator calls her attender."""
    return in_tx(triage.attender_call, episode_id, user, body.outcome, body.note)


class TierIn(BaseModel):
    tier: str
    reason: str | None = None


@app.post("/triage/episodes/{episode_id}/tier")
def triage_tier(episode_id: int, body: TierIn, user: dict = Depends(require_role(*CLINICAL))):
    """Anyone on the team may raise the tier; only a doctor lowers it, and Red only the ER doctor. Always a reason."""
    return in_tx(triage.change_tier, episode_id, user, body.tier, body.reason)


@app.get("/triage/brief/{mrn}")
def triage_brief(mrn: str, _user: dict = Depends(require_role(*CLINICAL))):
    """The Pre-OPD brief: 90 days, every line with its source."""
    return in_tx(triage.brief, mrn)
