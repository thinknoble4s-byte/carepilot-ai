"""Append-only audit ledger. The hash chain is computed by a database trigger
(db/migrations/001_init.sql), so every writer gets it for free."""
from typing import Literal

from psycopg.types.json import Jsonb

ActorType = Literal["person", "system", "agent", "ai_suggestion"]


def append(
    conn,
    *,
    actor_type: ActorType,
    actor_name: str,
    action: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    details: dict | None = None,
) -> dict:
    return conn.execute(
        """
        INSERT INTO audit_ledger (actor_type, actor_name, action, entity_type, entity_id, details)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id, occurred_at, actor_type, actor_name, action, hash
        """,
        (actor_type, actor_name, action, entity_type, entity_id, Jsonb(details or {})),
    ).fetchone()


def recent(conn, limit: int = 20) -> list[dict]:
    return conn.execute(
        """
        SELECT id, occurred_at, actor_type, actor_name, action, entity_type, entity_id, details, hash
        FROM audit_ledger ORDER BY id DESC LIMIT %s
        """,
        (limit,),
    ).fetchall()


def verify(conn) -> dict:
    row = conn.execute("SELECT * FROM audit_ledger_verify()").fetchone()
    return {"valid": row["first_bad_id"] is None, "checked": row["checked"], "firstBadId": row["first_bad_id"]}
