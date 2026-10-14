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


KINDS = {"agent": ("agent",), "person": ("person",), "system": ("system",), "ai": ("ai", "ai_suggestion")}


def search(conn, *, mrn: str | None = None, kind: str | None = None, days: int | None = None,
           q: str | None = None, limit: int = 400) -> dict:
    """Filter the ledger by patient, actor kind, recency and text; counts per kind for the same filter."""
    where, params = ["action <> 'session.user_selected'"], {}
    if mrn:
        where.append("mrn = %(mrn)s")
        params["mrn"] = mrn
    if days:
        where.append("occurred_at >= now() - make_interval(days => %(days)s)")
        params["days"] = int(days)
    if q:
        where.append("(action || ' ' || actor_name || ' ' || coalesce(entity_type, '') || ' ' || details::text) "
                     "ILIKE %(q)s")
        params["q"] = f"%{q}%"
    counts = {r["actor_type"]: r["n"] for r in conn.execute(
        f"SELECT actor_type, count(*) AS n FROM audit_ledger WHERE {' AND '.join(where)} GROUP BY actor_type",
        params).fetchall()}
    if kind in KINDS:
        where.append("actor_type = ANY(%(kinds)s)")
        params["kinds"] = list(KINDS[kind])
    entries = conn.execute(
        f"""
        SELECT id, occurred_at, actor_type, actor_name, action, entity_type, entity_id, mrn, reason_code, rule,
               outcome, details, hash
        FROM audit_ledger WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT %(limit)s
        """,
        {**params, "limit": limit},
    ).fetchall()
    return {"entries": entries, "chain": verify(conn),
            "counts": {k: sum(counts.get(t, 0) for t in v) for k, v in KINDS.items()}}
