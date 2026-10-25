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
    "help_booking": "action", "cant_come": "action", "help_request": "action",
    # Use Case 2T: report mismatch, outside report to verify, report reviewed but no next visit card
    "report_mismatch": "action", "outside_report": "action", "no_next_visit": "action",
    # Use Case 2T v1.4: the report check
    "find_hospital_report": "action", "call_about_test": "action", "call_patient_resend": "action",
}
HELP_TAGS = {"help_booking": "Help booking", "cant_come": "Can't come", "help_request": "Help request"}
ESCALATED = {"patient_asked_help_booking": "Escalated: help booking", "patient_cannot_come": "Escalated: can't come",
             "help_keyword": "Escalated: help request"}


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


def build(conn, viewer: str | None = None) -> dict:
    """viewer: the coordinator looking (Use Case 2c REQ-11: an urgent call moved to the cover leaves the
    owner's action count but stays visible to her)."""
    today = clock.get_today(conn)
    rules = policy.deadlines(conn)
    urgent_mode = bool(policy.settings(conn).get("urgent_call"))
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
    handovers = {h["mrn"]: h for h in conn.execute(
        """SELECT h.*, i.kind AS inbound_kind FROM handover h LEFT JOIN inbound_message i ON i.inbound_id = h.inbound_id
           WHERE h.stage <> 'closed'""").fetchall()}
    open_proposals = _group(conn.execute("SELECT * FROM proposal WHERE status = 'drafted' ORDER BY proposal_id").fetchall())
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

        def add(tag, text, action=False, due=None, marks=None):
            reasons.append({"tag": tag, "txt": text, "action": action, "due": due, "marks": marks or []})
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
                "handover_id": None, "unanswered": un, "hold": bool(open_proposals[mrn]),
                "assignees": sorted({t["assigned_name"] for t in tasks[mrn] if t["assigned_name"]}), "urgent": None}
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
                       "health_matter": "Agent stopped: health mention — review the handover"}
                marks = (["health_mention"] if h["trigger"] == "health_matter" else []) + \
                    (["voice"] if h["inbound_kind"] == "voice" else []) + (["ai_failed"] if h["ai_failed"] else [])
                add("action", why.get(h["trigger"], "Review the handover") + escalated("coordinator_review"), True,
                    task_due("coordinator_review"), marks)
            elif h["stage"] == "calling":
                add("action", "Call the patient before deciding on the handover" + escalated("call_first"), True,
                    task_due("call_first"))
            elif h["stage"] == "awaiting_doctor":
                add("verify", "Concern waiting for the doctor's decision" + escalated("doctor_decision"), False,
                    task_due("doctor_decision"))
            elif h["stage"] == "awaiting_instruction":
                add("verify", "Waiting for the doctor's instruction in the hospital record", False,
                    task_due("doctor_instruction"))
            elif h["stage"] == "ready_to_close" and not (urgent_mode and by_type.get("urgent_call")):
                add("action", "Close the handover with a reason — follow-up resumes after closure", True)
            if by_type.get("urgent_call") and urgent_mode:
                # Use Case 2c: the call-today task, above everything else, with a red Urgent tag.
                t = by_type["urgent_call"][0]
                state = "Overdue" if t["overdue_at_demo"] else "Moved to cover" if t["moved_at_demo"] else "Open"
                base["urgent"] = {"task_id": t["task_id"], "assigned": t["assigned_name"], "moved_from": t["moved_from"],
                                  "state": state}
                marks = ["urgent"] + (["overdue"] if state == "Overdue" else [])
                if viewer and t["moved_from"] == viewer and t["assigned_name"] != viewer:
                    add("verify", f"Urgent call moved to {t['assigned_name']} (cover) — you can still open it", False,
                        None, marks)
                else:
                    by = t["cover_deadline_at"] if t["moved_at_demo"] else t["owner_deadline_at"]
                    text = (f"Urgent — call the patient today: {t['assigned_name']}"
                            + (f", moved to cover, from {t['moved_from']}" if t["moved_from"] else f", cover {t['cover_name']}")
                            + (f" — Overdue after {t['cover_deadline_at']:%H:%M}, not reached yet" if state == "Overdue"
                               else f" — by {by:%H:%M}" if by else ""))
                    add("action", text, True, today, marks)
            elif by_type.get("urgent_call"):
                add("action", "Doctor marked the concern urgent — call the patient today" + escalated("urgent_call"),
                    True, task_due("urgent_call"))

        for prop in open_proposals[mrn]:
            add("approval", f"Proposal to approve: {prop['summary'] or 'AI draft not available — read the message'}"
                            f"{' (intent ' + prop['intent'].replace('_', ' ') + ')' if prop['intent'] else ''}"
                            " — routine reminders on hold")
        for m in drafts[mrn]:
            add("approval", f"Agent proposal to approve: {m['template_id'].replace('_', ' ')}"
                            f"{' for ' + m['item_code'] if m['item_code'] else ''} ({m['recipient']})")

        for t in open_tasks:
            tag = PEOPLE_TASKS.get(t["task_type"])
            if tag:
                text = ("Consent does not include automated messaging — the agent stays off"
                        if t["task_type"] == "consent_scope_missing" else t["reason"])
                if t["task_type"] in HELP_TAGS:  # Use Case 2b: type tag, owner and cover, Overdue when late
                    text = f"{HELP_TAGS[t['task_type']]}: {t['reason']} — {t['assigned_name']}" + \
                        (f", cover {t['cover_name']}" if t["cover_name"] else "")
                marks = ([f"task_{t['task_type']}"] if t["task_type"] in HELP_TAGS else []) + \
                    (["overdue"] if t["due_date"] and t["due_date"] < today else [])
                add(tag, text + (f" (escalated to {t['assigned_name']})" if t["escalated_on"] else ""), True,
                    t["due_date"], marks)

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
        reasons.sort(key=lambda r: ("urgent" not in r["marks"], not r["action"], order.get(r["tag"], 9),
                                    r["due"] or date.max))
        dues = sorted(r["due"] for r in reasons if r["due"])
        rows.append({**base, "main": main, "reasons": reasons, "next": dues[0] if dues else None})

    rank = {t: i for i, t in enumerate(PRIORITY + ["ontrack", "paused", "closed"])}
    rows.sort(key=lambda r: (not any("urgent" in x.get("marks", []) and x["action"] for x in r["reasons"]),
                             rank[r["main"]], r["next"] or date.max, r["mrn"]))
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
    stops = conn.execute(
        """
        SELECT a.demo_date AS day, a.occurred_at AS ts, a.mrn, p.full_name, a.reason_code
        FROM audit_ledger a JOIN patient p USING (mrn)
        WHERE a.action = 'agent_decision' AND a.reason_code IN ('health_flag_handover', 'held_open_proposal',
              'patient_asked_help_booking', 'patient_cannot_come', 'help_keyword')
          AND a.demo_date >= %s
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
    rows += [{"day": s["day"], "ts": s["ts"], "mrn": s["mrn"], "full_name": s["full_name"],
              "text": ESCALATED.get(s["reason_code"]) or ("Stopped: health mention" if s["reason_code"] == "health_flag_handover"
                                                         else "Held: open proposal")
              + f" ({s['reason_code']})"} for s in stops]
    return sorted(rows, key=lambda r: r["ts"], reverse=True)


def _glance(conn, today, rows) -> dict:
    feed = agent_feed(conn, today)
    active = [r for r in rows if r["main"] not in ("paused", "closed")]
    need = [r for r in active if r["main"] in ("action", "noresp")]
    handled = [r for r in active if r["main"] == "agent"]
    approvals = conn.execute("SELECT count(*) AS n FROM outbox_message WHERE status = 'pending_approval'").fetchone()["n"] \
        + conn.execute("SELECT count(*) AS n FROM proposal WHERE status = 'drafted'").fetchone()["n"]
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
        WHERE c.state IN ('Due', 'Booked') AND c.due_date BETWEEN %s AND %s
          AND p.status NOT IN ('Paused', 'Lost to follow-up', 'Followed up elsewhere')
        ORDER BY c.department, c.due_date, p.full_name
        """,
        (today, today + timedelta(days=7)),
    ).fetchall()
    out = []
    for dept, items in _group(rows, "department").items():
        out.append({"department": dept, "owner": items[0]["owner_name"], "cover": items[0]["cover_name"], "items": items})
    return out
