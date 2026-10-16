"""The coordinator's worklist (prototype v7.2 'Workspace').

For each patient: one main status and the reasons behind it, each with a
deadline where there is one. Statuses are operational, never clinical:
action, no response, overdue, awaiting verification, approval, agent handling,
due soon, early warning, on track, paused, closed. Early warnings are listed
as facts and never added up into a score.
"""
from collections import defaultdict
from datetime import date, timedelta

from . import clock, policy
from .agent import HELD_TEXT

PRIORITY = ["action", "noresp", "overdue", "verify", "approval", "agent", "duesoon", "watch"]
CLOSED = ("Lost to follow-up", "Followed up elsewhere")
PEOPLE_TASKS = {
    "contact_needed": "action", "booking_help": "action", "cannot_attend": "action",
    "confirm_test_booking": "action", "consent_scope_missing": "action",
}


def _d(day: date) -> str:
    return day.strftime("%d %b %Y")


def _group(rows, key="mrn"):
    out = defaultdict(list)
    for r in rows:
        out[r[key]].append(r)
    return out


def unanswered_run(outbox: list[dict], inbound: list[dict], calls: list[dict]) -> int:
    """Messages sent to the patient in a row since their last reply or a call that reached them."""
    last_contact = max([i["created_at"] for i in inbound] +
                       [c["created_at"] for c in calls if c["outcome"] not in ("noans", "wrong")], default=None)
    return sum(1 for m in outbox
               if m["recipient"] == "patient" and m["status"] == "sent" and m["template_id"] != "handover_ack"
               and (last_contact is None or m["created_at"] > last_contact))


def build(conn) -> dict:
    today = clock.get_today(conn)
    rules = policy.deadlines(conn)
    patients = conn.execute(
        """
        SELECT p.*, c.scopes,
               (SELECT treatment_end_date FROM care_plan cp WHERE cp.mrn = p.mrn) AS treatment_end,
               (SELECT care_plan_id FROM care_plan cp WHERE cp.mrn = p.mrn) AS plan_id,
               (SELECT therapy FROM care_plan cp WHERE cp.mrn = p.mrn) AS therapy
        FROM patient p
        LEFT JOIN LATERAL (SELECT scopes FROM consent WHERE consent.mrn = p.mrn ORDER BY consent_id DESC LIMIT 1) c
          ON true
        ORDER BY p.mrn
        """
    ).fetchall()
    handovers = {h["mrn"]: h for h in conn.execute("SELECT * FROM handover WHERE stage <> 'closed'").fetchall()}
    tasks = _group(conn.execute("SELECT * FROM coordinator_task WHERE status = 'open' ORDER BY task_id").fetchall())
    drafts = _group(conn.execute(
        """SELECT o.*, c.item_code FROM outbox_message o LEFT JOIN order_card c USING (card_id)
           WHERE o.status = 'pending_approval' ORDER BY message_id""").fetchall())
    cards = _group(conn.execute("SELECT * FROM order_card WHERE state = 'Due' ORDER BY due_date").fetchall())
    reminded = {r["card_id"]: r["acted_on"] for r in conn.execute(
        """SELECT card_id, max(acted_on) AS acted_on FROM gap_event
           WHERE status = 'reminder_sent' AND outcome = 'sent' GROUP BY card_id""").fetchall()}
    blocked = {r["card_id"]: r["blocked_reason"] for r in conn.execute(
        "SELECT card_id, blocked_reason FROM gap_event WHERE status = 'blocked'").fetchall()}
    outbox = _group(conn.execute("SELECT * FROM outbox_message ORDER BY message_id").fetchall())
    inbound = _group(conn.execute("SELECT * FROM inbound_message ORDER BY inbound_id").fetchall())
    calls = _group(conn.execute("SELECT * FROM call_log ORDER BY call_id").fetchall())

    rows = []
    for p in patients:
        mrn, reasons, tags = p["mrn"], [], set()

        def add(tag, text, action=False, due=None):
            reasons.append({"tag": tag, "txt": text, "action": action, "due": due})
            tags.add(tag)
            if action:
                tags.add("action")

        un = unanswered_run(outbox[mrn], inbound[mrn], calls[mrn])
        last_reply = max([i["received_on"] for i in inbound[mrn]], default=None)
        base = {"mrn": mrn, "full_name": p["full_name"], "language": p["preferred_language"],
                "coordinator": p["coordinator"], "status": p["status"], "agent_on": p["agent_on"],
                "therapy": p["therapy"], "treatment_end": p["treatment_end"], "last_reply": last_reply,
                "caregiver": p["caregiver_name"], "caregiver_in_loop": bool(p["caregiver_phone"] and p["scopes"]
                                                                          and "whatsapp_caregiver" in p["scopes"]),
                "handover_id": None, "unanswered": un}
        if p["status"] in CLOSED:
            rows.append({**base, "main": "closed", "reasons": [{"tag": "closed", "txt": f"Closed — {p['status'].lower()}",
                                                                "action": False, "due": None}], "next": None})
            continue
        if p["status"] == "Paused":
            until = f" until {_d(p['pause_until'])}" if p["pause_until"] else ""
            rows.append({**base, "main": "paused", "next": None, "reasons": [
                {"tag": "paused", "txt": f"Follow-up paused{until} — {p['pause_reason'] or 'reason on file'}",
                 "action": False, "due": None}]})
            continue

        open_tasks = tasks[mrn]
        by_type = defaultdict(list)
        for t in open_tasks:
            by_type[t["task_type"]].append(t)

        def task_due(task_type):
            t = by_type.get(task_type)
            return t[0]["due_date"] if t else None

        def escalated(task_type):
            t = by_type.get(task_type)
            return f" (escalated to {t[0]['assigned_name']})" if t and t[0]["escalated_on"] else ""

        h = handovers.get(mrn)
        if h:
            base["handover_id"] = h["handover_id"]
            if h["stage"] == "awaiting_coordinator":
                why = {"help_keyword": "Patient asked for a person (HELP)", "manual": "Handover opened by the team",
                       "unclassified_reply": "Agent stopped: the patient wrote a message — review the handover",
                       "health_matter": "Agent stopped: patient mentioned a health matter — review the handover"}
                add("action", why.get(h["trigger"], "Review the handover") + escalated("coordinator_review"), True,
                    task_due("coordinator_review"))
            elif h["stage"] == "calling":
                add("action", "Call the patient before deciding on the handover" + escalated("call_first"), True,
                    task_due("call_first"))
            elif h["stage"] == "awaiting_doctor":
                add("verify", "Concern waiting for the doctor's decision" + escalated("doctor_decision"), False,
                    task_due("doctor_decision"))
            elif h["stage"] == "awaiting_instruction":
                add("verify", "Waiting for the doctor's instruction in the hospital record", False,
                    task_due("doctor_instruction"))
            elif h["stage"] == "ready_to_close":
                add("action", "Close the handover with a reason — follow-up resumes after closure", True)
            if by_type.get("urgent_call"):
                add("action", "Doctor marked the concern urgent — call the patient today" + escalated("urgent_call"),
                    True, task_due("urgent_call"))

        for m in drafts[mrn]:
            add("approval", f"Agent proposal to approve: {m['template_id'].replace('_', ' ')}"
                            f"{' for ' + m['item_code'] if m['item_code'] else ''} ({m['recipient']})")

        for t in open_tasks:
            tag = PEOPLE_TASKS.get(t["task_type"])
            if tag:
                text = ("Consent does not include automated messaging — the agent stays off"
                        if t["task_type"] == "consent_scope_missing" else t["reason"])
                add(tag, text + (f" (escalated to {t['assigned_name']})" if t["escalated_on"] else ""), True,
                    t["due_date"])

        task_cards = {t["card_id"] for t in open_tasks if t["card_id"]}
        for c in cards[mrn]:
            name = c["name"]
            if c["origin"] == "instruction":
                if by_type.get("booking_from_instruction"):
                    t = next((t for t in by_type["booking_from_instruction"] if t["card_id"] == c["card_id"]), None)
                    if t:
                        add("duesoon", f"{name} — from doctor's instruction; booking with {t['assigned_name']}",
                            False, t["due_date"])
                continue
            if c["card_id"] in task_cards:
                continue  # a person already has a task for this card
            if c["card_id"] in blocked:
                code = blocked[c["card_id"]]
                if code == "held_not_active":
                    continue  # shown as paused or closed
                add("action", f"{name} — reminder blocked: {HELD_TEXT.get(code, code)} ({code})", True)
                continue
            d = (today - c["due_date"]).days
            sent_on = reminded.get(c["card_id"])
            fresh = sent_on is not None and (today - sent_on).days < 2
            if d > 0:
                if fresh:
                    add("agent", f"{name} — {d} day{'s' if d > 1 else ''} overdue; agent sent a reminder on {_d(sent_on)}")
                else:
                    add("overdue", f"{name} — {d} day{'s' if d > 1 else ''} overdue, never booked", d > 14 and un >= 2)
            elif -d <= 7:
                n = -d
                when = "today" if n == 0 else f"in {n} day{'s' if n > 1 else ''}"
                if fresh:
                    add("agent", f"{name} — due {when}; agent sent a booking reminder on {_d(sent_on)}, awaiting reply")
                elif sent_on and n <= 3:
                    add("action", f"{name} — booking reminder unanswered; call to help book", True,
                        policy.add_working_days(c["due_date"] - timedelta(days=3), 2))
                else:
                    add("duesoon", f"{name} — not booked, due {when}" + (" (booking reminder sent)" if sent_on else ""))
            elif -d <= 14:
                add("duesoon", f"{name} due in {-d} days")

        if p["plan_id"] is None:
            add("verify", "Follow-up plan not confirmed yet — confirm it to create the order cards")
        if not p["scopes"]:
            add("action", "No consent on record — record consent before any messaging", True)
        if un >= 2:
            add("noresp", f"{un} messages in a row unanswered", True)
        elif un == 1:
            add("watch", "Early warning: last message unanswered")
        if not p["agent_on"] or p["status"] == "Agent off":
            if p["scopes"] and "automated_messaging" in p["scopes"]:
                add("watch", "Agent switched off for this patient")

        main = next((t for t in PRIORITY if t in tags), "ontrack")
        order = {t: i for i, t in enumerate(PRIORITY)}
        reasons.sort(key=lambda r: (not r["action"], order.get(r["tag"], 9), r["due"] or date.max))
        dues = sorted(r["due"] for r in reasons if r["due"])
        rows.append({**base, "main": main, "reasons": reasons, "next": dues[0] if dues else None})

    rank = {t: i for i, t in enumerate(PRIORITY + ["ontrack", "paused", "closed"])}
    rows.sort(key=lambda r: (rank[r["main"]], r["next"] or date.max, r["mrn"]))
    return {"today": today, "patients": rows, "glance": _glance(conn, today, rows), "feed": agent_feed(conn, today),
            "proposals": _proposals(conn), "appointments": _last_appointments(conn),
            "coordinators": sorted({r["coordinator"] for r in rows if r["coordinator"]}),
            "default_coordinator": rules["default_coordinator"]}


def agent_feed(conn, today: date) -> list[dict]:
    """What the agent did since yesterday evening (demo dates), newest first."""
    since = today - timedelta(days=1)
    sent = conn.execute(
        """
        SELECT o.created_on AS day, o.created_at AS ts, o.mrn, p.full_name, o.template_id, o.recipient, o.status,
               c.item_code, c.due_date
        FROM outbox_message o JOIN patient p USING (mrn) LEFT JOIN order_card c USING (card_id)
        WHERE o.actor_type = 'agent' AND o.created_on >= %s
        """,
        (since,),
    ).fetchall()
    taps = conn.execute(
        """
        SELECT i.received_on AS day, i.created_at AS ts, i.mrn, p.full_name, i.button, i.handling
        FROM inbound_message i JOIN patient p USING (mrn)
        WHERE i.button IS NOT NULL AND i.received_on >= %s
        """,
        (since,),
    ).fetchall()
    verb = {"sent": "Sent", "pending_approval": "Drafted for approval", "blocked": "Blocked by the send-guard:"}
    rows = [{"day": m["day"], "ts": m["ts"], "mrn": m["mrn"], "full_name": m["full_name"],
             "text": f"{verb.get(m['status'], m['status'])} {m['template_id'].replace('_', ' ')}"
                     f"{' for ' + m['item_code'] + ' (due ' + _d(m['due_date']) + ')' if m['item_code'] else ''}"
                     f" to the {m['recipient']}"} for m in sent]
    rows += [{"day": t["day"], "ts": t["ts"], "mrn": t["mrn"], "full_name": t["full_name"],
              "text": f"Logged the \"{t['button'].replace('_', ' ')}\" button reply: {t['handling']}"} for t in taps]
    return sorted(rows, key=lambda r: r["ts"], reverse=True)


def _glance(conn, today, rows) -> dict:
    feed = agent_feed(conn, today)
    active = [r for r in rows if r["main"] not in ("paused", "closed")]
    need = [r for r in active if r["main"] in ("action", "noresp")]
    handled = [r for r in active if r["main"] == "agent"]
    approvals = conn.execute("SELECT count(*) AS n FROM outbox_message WHERE status = 'pending_approval'").fetchone()["n"]
    return {"agent_actions": len(feed), "agent_patients": len({f["mrn"] for f in feed}), "handled": len(handled),
            "approvals": approvals, "need_you": len(need)}


def _proposals(conn) -> list[dict]:
    return conn.execute(
        """
        SELECT o.message_id, o.mrn, p.full_name, o.template_id, o.recipient, o.body, o.body_en, o.language,
               c.item_code, c.due_date
        FROM outbox_message o JOIN patient p USING (mrn) LEFT JOIN order_card c USING (card_id)
        WHERE o.status = 'pending_approval' ORDER BY o.message_id
        """
    ).fetchall()


def _last_appointments(conn) -> dict | None:
    return conn.execute(
        """SELECT batch_id, imported_at, rows_created, rows_exception FROM import_batch
           WHERE source_type = 'appointments' ORDER BY batch_id DESC LIMIT 1"""
    ).fetchone()


def department_lists(conn) -> list[dict]:
    """Morning department lists: every open item due in the next 7 days, by department."""
    today = clock.get_today(conn)
    rows = conn.execute(
        """
        SELECT c.department, c.mrn, p.full_name, c.item_code, c.name, c.due_date, c.state, c.owner_name, c.cover_name
        FROM order_card c JOIN patient p USING (mrn)
        WHERE c.state IN ('Due', 'Booked', 'Ordered', 'Scheduled') AND c.due_date BETWEEN %s AND %s
          AND p.status NOT IN ('Paused', 'Lost to follow-up', 'Followed up elsewhere')
        ORDER BY c.department, c.due_date, p.full_name
        """,
        (today, today + timedelta(days=7)),
    ).fetchall()
    out = []
    for dept, items in _group(rows, "department").items():
        out.append({"department": dept, "owner": items[0]["owner_name"], "cover": items[0]["cover_name"], "items": items})
    return out
