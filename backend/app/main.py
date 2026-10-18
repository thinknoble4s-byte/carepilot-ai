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
               proposals, rules, seed, states, tasks, test_cycle, timeline, urgent_calls, views, worklist)
from .auth import ANY_ROLE, CLINICAL, COORDINATOR, current_user, require_role
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
                "today": now.date(), "time": now.strftime("%H:%M")}

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
    event_id: str | None = None  # the same event sent twice creates nothing new (REQ-20)


@app.post("/simulator/{mrn}/reply", status_code=201)
def simulate_reply(mrn: str, body: ReplyIn, _user: dict = Depends(require_role(*COORDINATOR))):
    """The WhatsApp simulator: the demo operator plays the patient or caregiver."""
    return in_tx(gateway.receive_reply, mrn, from_role=body.from_role, button=body.button, text=body.text,
                 in_reply_to=body.in_reply_to, clip_id=body.clip_id, event_id=body.event_id)


@app.post("/simulator/{mrn}/voice", status_code=201)
async def simulator_voice(mrn: str, file: UploadFile = File(...), from_role: str = Form("patient"),
                          in_reply_to: int | None = Form(None), event_id: str | None = Form(None),
                          language: str | None = Form(None), _user: dict = Depends(require_role(*COORDINATOR))):
    """A voice note recorded (or an audio file uploaded) in the simulator: stored in the database, then the
    same path as a clip — Sarvam text, translation and the yes/no health flag. language: Hindi, Kannada,
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


@app.post("/simulated-lab/report", status_code=201)
def simulated_lab_report(body: LabReportIn, user: dict = Depends(require_role(*COORDINATOR))):
    """Demo control (SIMULATED): the hospital lab sends a synthetic report with an MRN and a test code."""
    return in_tx(test_cycle.lab_report, mrn=body.mrn, test_code=body.test_code, report_mrn=body.report_mrn,
                 with_file=body.with_file, report_ref=body.report_ref, pressed_by=user["user_id"])


@app.post("/simulator/{mrn}/document", status_code=201)
async def simulator_document(mrn: str, file: UploadFile | None = File(None), from_role: str = Form("patient"),
                             event_id: str | None = Form(None), _user: dict = Depends(require_role(*COORDINATOR))):
    """The patient sends a photo or PDF of an outside report. Without a file: the synthetic outside-lab PDF."""
    data = await file.read() if file else None

    def run(conn):
        if data is None:
            p = conn.execute("SELECT * FROM patient WHERE mrn = %s", (mrn,)).fetchone()
            if p is None:
                raise HTTPException(status_code=404, detail=f"Patient {mrn} not found")
            card = conn.execute("SELECT name FROM order_card WHERE mrn = %s AND card_type = 'test' ORDER BY card_id "
                                "LIMIT 1", (mrn,)).fetchone()
            pdf = test_cycle.outside_report_pdf(p, clock.get_today(conn), card["name"] if card else "Blood test")
            return gateway.receive_document(conn, mrn, data=pdf, mime="application/pdf",
                                            file_name="outside-lab-report.pdf", from_role=from_role, event_id=event_id)
        return gateway.receive_document(conn, mrn, data=data, mime=(file.content_type or "").split(";")[0],
                                        file_name=file.filename or "document", from_role=from_role, event_id=event_id)

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
