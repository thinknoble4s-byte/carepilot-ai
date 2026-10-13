"""CarePilot API (Increment 1: records and plan, Golden Path Steps 0-1).

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

from . import audit, clock, consent, db, importer, plan, seed, views
from .auth import ANY_ROLE, CLINICAL, COORDINATOR, current_user, require_role
from .errors import DomainError
from .reference import ROLE_LABELS, USERS

app = FastAPI(title="CarePilot API", version="0.2.0")

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
    """Synthetic users for the "Viewing as" switcher."""
    return [{"id": u["user_id"], "person": u["name"], "role": u["role"], "label": ROLE_LABELS[u["role"]]}
            for u in USERS]


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
def audit_trail(limit: int = 20):
    return in_tx(lambda conn: {"entries": audit.recent(conn, min(max(limit, 1), 200)), "chain": audit.verify(conn)})


@app.get("/clock")
def get_clock(_user: dict = Depends(current_user)):
    return {"today": in_tx(clock.get_today)}


class AdvanceIn(BaseModel):
    days: int


@app.post("/clock/advance")
def advance_clock(body: AdvanceIn, user: dict = Depends(require_role(*COORDINATOR))):
    return {"today": in_tx(clock.advance, body.days, user["user_id"])}


@app.post("/demo/reset")
def demo_reset(user: dict = Depends(require_role(*COORDINATOR))):
    return in_tx(seed.reset, user["user_id"])


# ---------------------------------------------------------------------------
# A2 imports and exceptions
# ---------------------------------------------------------------------------
@app.post("/imports", status_code=201)
async def upload(
    file: UploadFile = File(...),
    source_type: Literal["register", "appointments"] = Form(...),
    user: dict = Depends(require_role(*COORDINATOR)),
):
    data = await file.read()
    run = importer.import_register if source_type == "register" else importer.import_appointments
    return in_tx(run, user["user_id"], data, file.filename)


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
    therapy: str
    treatment_end_date: str
    identifiers_checked: list[str]


@app.post("/patients/{mrn}/plan/confirm", status_code=201)
def confirm_plan(mrn: str, body: PlanIn, user: dict = Depends(require_role(*COORDINATOR))):
    return in_tx(plan.confirm_plan, user["user_id"], mrn, body.template_id, body.therapy,
                 body.treatment_end_date, body.identifiers_checked)


# ---------------------------------------------------------------------------
# Read side: Workspace, Patient journey, Summary
# ---------------------------------------------------------------------------
@app.get("/patients")
def patients(_user: dict = Depends(require_role(*CLINICAL))):
    return in_tx(views.list_patients)


@app.get("/patients/{mrn}")
def patient(mrn: str, _user: dict = Depends(require_role(*CLINICAL))):
    return in_tx(views.patient_detail, mrn)


@app.get("/patients/{mrn}/cards")
def cards(mrn: str, _user: dict = Depends(require_role(*CLINICAL))):
    return in_tx(views.patient_cards, mrn)


@app.get("/tasks")
def tasks(_user: dict = Depends(require_role(*COORDINATOR))):
    return in_tx(views.open_tasks)


@app.get("/summary")
def summary(_user: dict = Depends(require_role(*ANY_ROLE))):
    return in_tx(views.summary)
