"""Proposals (Use Case 2a, REQ-16 to REQ-19): the AI's draft of a reply that did not mention a
health matter. Approve means "I read the message and the draft is a fair description". It changes no
card and sends nothing. While a proposal is open, routine reminders to the patient are on hold
(decision 10, a setting); the hold ends when the coordinator approves or rejects."""
from . import audit, clock, policy, tasks
from .agent import set_next_action
from .errors import Conflict, Invalid, NotFound


def _load(conn, proposal_id: int) -> dict:
    p = conn.execute("SELECT * FROM proposal WHERE proposal_id = %s FOR UPDATE", (proposal_id,)).fetchone()
    if p is None:
        raise NotFound(f"Proposal {proposal_id} not found")
    if p["status"] != "drafted":
        raise Conflict(f"Proposal {proposal_id} is already {p['status']}")
    return p


def _decide(conn, p: dict, user_id: str, status: str, **fields) -> dict:
    now = clock.get_now(conn)
    minutes = int((now - p["created_at_demo"]).total_seconds() // 60)
    sets = ", ".join(f"{k} = %({k})s" for k in fields)
    row = conn.execute(
        f"""UPDATE proposal SET status = %(status)s, decided_by = %(user)s, decided_at_demo = %(now)s,
                minutes_taken = %(minutes)s{', ' + sets if sets else ''}
            WHERE proposal_id = %(id)s RETURNING *""",
        {"status": status, "user": user_id, "now": now, "minutes": minutes, "id": p["proposal_id"], **fields},
    ).fetchone()
    for t in conn.execute("SELECT task_id FROM coordinator_task WHERE proposal_id = %s AND status = 'open'",
                          (p["proposal_id"],)).fetchall():
        tasks.complete_task(conn, t["task_id"], user_id, f"Proposal {status}")
    still_open = conn.execute("SELECT 1 FROM proposal WHERE mrn = %s AND status = 'drafted'", (p["mrn"],)).fetchone()
    if not still_open and policy.settings(conn).get("hold_on_proposal", True):
        audit.append(conn, actor_type="system", actor_name="CarePilot", action="hold_lifted", entity_type="proposal",
                     entity_id=str(p["proposal_id"]), mrn=p["mrn"], outcome="hold_off")
        set_next_action(conn, p["mrn"], "Routine follow-up (hold lifted)")
    return row


def approve(conn, proposal_id: int, user_id: str, summary: str | None = None) -> dict:
    p = _load(conn, proposal_id)
    final = (summary or "").strip() or p["summary"]
    edited = bool((summary or "").strip()) and (summary or "").strip() != (p["summary"] or "")
    row = _decide(conn, p, user_id, "approved", final_summary=final, edited=edited)
    audit.append(conn, actor_type="person", actor_name=user_id, action="proposal_approved", entity_type="proposal",
                 entity_id=str(proposal_id), mrn=p["mrn"], outcome="approved",
                 details={"edited": edited, "minutes_taken": row["minutes_taken"], "intent": p["intent"]})
    return row


def reject(conn, proposal_id: int, user_id: str, reason: str | None) -> dict:
    if not reason or not reason.strip():
        raise Invalid("A reason is required to reject a proposal")
    p = _load(conn, proposal_id)
    row = _decide(conn, p, user_id, "rejected", decision_reason=reason.strip(), edited=False)
    audit.append(conn, actor_type="person", actor_name=user_id, action="proposal_rejected", entity_type="proposal",
                 entity_id=str(proposal_id), mrn=p["mrn"], outcome="rejected",
                 details={"reason": reason.strip(), "minutes_taken": row["minutes_taken"]})
    return row


def open_for(conn, mrn: str) -> list[dict]:
    return conn.execute(
        """SELECT p.*, i.transcript, i.translation, i.audio_id, i.kind, i.text, i.ai_failed
           FROM proposal p JOIN inbound_message i USING (inbound_id)
           WHERE p.mrn = %s ORDER BY p.proposal_id DESC""", (mrn,)).fetchall()


def waiting(conn) -> list[dict]:
    return conn.execute(
        """SELECT p.proposal_id, p.mrn, pt.full_name, p.summary, p.intent, p.intent_reason, p.draft_failed,
                  p.created_at_demo, i.transcript, i.translation, i.audio_id, i.kind, i.text
           FROM proposal p JOIN inbound_message i USING (inbound_id) JOIN patient pt ON pt.mrn = p.mrn
           WHERE p.status = 'drafted' ORDER BY p.proposal_id""").fetchall()
