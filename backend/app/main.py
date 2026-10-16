"""CarePilot API (Increments 1-3: records and plan, the deterministic loop, the human safety path).

Each request runs in one database transaction: an action and its audit row
commit together, and any error rolls both back. The database is off by
default; only the owner starts it (scripts/db.sh start). Callers are picked by
the X-User header (demo profile, no login).
"""
import os
from typing import Literal

import psycopg
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import (agent, audit, clock, consent, db, gateway, handover, importer, messaging, patients, plan, programme,
               rules, seed, tasks, timeline, views, worklist)
from .auth import ANY_ROLE, CLINICAL, COORDINATOR, current_user, require_role
from .errors import DomainError
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
                q: str | None = None):
    limit = min(max(limit, 1), 400)
    if not (mrn or kind or days or q):
        return in_tx(lambda conn: {"entries": audit.recent(conn, limit), "chain": audit.verify(conn)})
    return in_tx(lambda conn: audit.search(conn, mrn=mrn, kind=kind, days=days, q=q, limit=limit))


@app.get("/clock")
def get_clock(_user: dict = Depends(current_user)):
    return {"today": in_tx(clock.get_today)}


class AdvanceIn(BaseModel):
    days: int


@app.post("/clock/advance")
def advance_clock(body: AdvanceIn, user: dict = Depends(require_role(*COORDINATOR))):
    return {"today": in_tx(clock.advance, body.days, user["user_id"])}


class ResetIn(BaseModel):
    scenario: str = seed.DEFAULT_SCENARIO


@app.post("/demo/reset")
def demo_reset(body: ResetIn | None = None, user: dict = Depends(require_role(*COORDINATOR))):
    return in_tx(seed.reset, user["user_id"], (body or ResetIn()).scenario)


class SetDateIn(BaseModel):
    date: str


@app.post("/clock/set")
def set_clock(body: SetDateIn, user: dict = Depends(require_role(*COORDINATOR))):
    return {"today": in_tx(clock.set_forward, body.date, user["user_id"])}


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


@app.post("/simulator/{mrn}/reply", status_code=201)
def simulate_reply(mrn: str, body: ReplyIn, _user: dict = Depends(require_role(*COORDINATOR))):
    """The WhatsApp simulator: the demo operator plays the patient or caregiver."""
    return in_tx(gateway.receive_reply, mrn, from_role=body.from_role, button=body.button, text=body.text,
                 in_reply_to=body.in_reply_to)


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
    return in_tx(tasks.complete_task, task_id, user["user_id"], body.note)


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


class OrderIn(BaseModel):
    name: str
    department: str
    card_type: str = "visit"
    within_days: int


class InstructionIn(BaseModel):
    text: str | None = None
    order: OrderIn | None = None


@app.post("/handovers/{handover_id}/instruction")
def instruction(handover_id: int, body: InstructionIn, user: dict = Depends(require_role("oncologist"))):
    return in_tx(handover.record_instruction, handover_id, user, body.text,
                 body.order.model_dump() if body.order else None)


class CloseIn(BaseModel):
    reason: str | None = None


@app.post("/handovers/{handover_id}/close")
def close_handover(handover_id: int, body: CloseIn, user: dict = Depends(require_role(*COORDINATOR))):
    return in_tx(handover.close, handover_id, user, body.reason)


# ---------------------------------------------------------------------------
# Screens matching prototype v7.2: worklist, timeline, calls, pause, Care Plan, indicators
# ---------------------------------------------------------------------------
@app.get("/worklist")
def get_worklist(_user: dict = Depends(require_role(*COORDINATOR))):
    return in_tx(worklist.build)


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
