"""CarePilot API (Increment 0: walking skeleton).

Endpoints prove the path browser -> Lambda URL -> Postgres -> audit ledger,
and let a visitor wake the database, which is stopped when unused.
"""
import os

import psycopg
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from . import audit, db
from .roles import ROLES, ROLES_BY_ID

app = FastAPI(title="CarePilot API", version="0.1.0")

if not os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
    # Local dev only: on AWS the Function URL adds CORS headers (adding them twice breaks browsers).
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class SessionIn(BaseModel):
    role: str


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


@app.post("/db/start")
def start_database():
    """Wake the stopped database (takes about 5 minutes). Anyone may call this in the demo profile."""
    return {"db": db.start_instance()}


@app.get("/roles")
def roles():
    return ROLES


@app.post("/session")
def switch_role(body: SessionIn):
    role = ROLES_BY_ID.get(body.role)
    if role is None:
        raise HTTPException(status_code=422, detail=f"Unknown role {body.role!r}")
    try:
        with db.connect() as conn:
            entry = audit.append(
                conn,
                actor_type="person",
                actor_name=role["person"],
                action="session.role_selected",
                entity_type="role",
                entity_id=role["id"],
                details={"label": role["label"], "synthetic": True},
            )
    except psycopg.OperationalError:
        raise HTTPException(status_code=503, detail="Database is not available; start it first")
    return {"role": role, "audit": entry}


@app.get("/audit")
def audit_trail(limit: int = 20):
    try:
        with db.connect() as conn:
            return {"entries": audit.recent(conn, min(max(limit, 1), 200)), "chain": audit.verify(conn)}
    except psycopg.OperationalError:
        raise HTTPException(status_code=503, detail="Database is not available; start it first")
