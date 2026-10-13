"""Append-only audit ledger. The hash chain is computed by a database trigger
(db/migrations/001_init.sql, 002_records_and_plan.sql), so every writer gets it
for free. Write the entry on the same connection as the change it records, so
both commit or roll back together."""
from typing import Literal

from psycopg.types.json import Jsonb

ActorType = Literal["person", "system", "agent", "ai", "ai_suggestion"]

# Optional fields for later agent and AI steps (Step A audit_event columns).
EXTRA_FIELDS = (
    "mrn", "reason_code", "decision_inputs", "source_link", "trigger_event", "rule", "task", "mode",
    "policy_version", "template_id", "template_version", "model", "prompt_version", "outcome",
)


def append(
    conn,
    *,
    actor_type: ActorType,
    actor_name: str,
    action: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    details: dict | None = None,
    **extra,
) -> dict:
    unknown = set(extra) - set(EXTRA_FIELDS)
    if unknown:
        raise TypeError(f"Unknown audit fields: {sorted(unknown)}")
    if extra.get("decision_inputs") is not None:
        extra["decision_inputs"] = Jsonb(extra["decision_inputs"])
    cols = ["actor_type", "actor_name", "action", "entity_type", "entity_id", "details", *extra]
    values = [actor_type, actor_name, action, entity_type, entity_id, Jsonb(details or {}), *extra.values()]
    return conn.execute(
        f"""
        INSERT INTO audit_ledger ({", ".join(cols)})
        VALUES ({", ".join(["%s"] * len(cols))})
        RETURNING id, occurred_at, actor_type, actor_name, action, hash
        """,
        values,
    ).fetchone()


def recent(conn, limit: int = 20) -> list[dict]:
    return conn.execute(
        """
        SELECT id, occurred_at, actor_type, actor_name, action, entity_type, entity_id, mrn, details, hash
        FROM audit_ledger ORDER BY id DESC LIMIT %s
        """,
        (limit,),
    ).fetchall()


def verify(conn) -> dict:
    row = conn.execute("SELECT * FROM audit_ledger_verify()").fetchone()
    return {"valid": row["first_bad_id"] is None, "checked": row["checked"], "firstBadId": row["first_bad_id"]}
