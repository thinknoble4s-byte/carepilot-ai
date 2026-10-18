"""Patient journey timeline (prototype v7.2 'Patient journey').

Every event carries its source and its origin. An item that nothing on file
explains (for example an appointment with no order card behind it) is shown
as an origin gap, never assumed. Trust tiers: Hospital A (in-house),
patient-reported, CarePilot (raised by the rules or the agent).
"""
from .errors import NotFound
from .reference import USERS_BY_ID


def _who(user_id) -> str:
    return USERS_BY_ID.get(user_id, {}).get("name", user_id)


def _ev(day, label, kind, tier, src, origin, rule=None):
    return {"date": day, "label": label, "kind": kind, "tier": tier, "src": src, "origin": origin, "rule": rule}


def build(conn, mrn: str) -> dict:
    p = conn.execute("SELECT * FROM patient WHERE mrn = %s", (mrn,)).fetchone()
    if p is None:
        raise NotFound(f"Patient {mrn} not found")
    plan = conn.execute("SELECT * FROM care_plan WHERE mrn = %s", (mrn,)).fetchone()
    events = []
    consent = conn.execute("SELECT * FROM consent WHERE mrn = %s ORDER BY consent_id", (mrn,)).fetchall()
    for c in consent:
        events.append(_ev(c["recorded_on"], f"Consent recorded ({c['consent_reference']}): {', '.join(c['scopes'])}",
                          "milestone", "hosp", f"Consent record {c['consent_reference']}", "Coordinator, consent form"))
    if plan:
        if plan["treatment_end_date"]:  # Use Case 1/2a plans have entered due dates and no treatment end date
            events.append(_ev(plan["treatment_end_date"], f"Active treatment completed ({plan['therapy']})", "milestone",
                              "hosp", "Care plan confirmation", f"Confirmed by {_who(plan['confirmed_by'])}"))
        events.append(_ev(plan["confirmed_on"] or plan["treatment_end_date"],
                          f"Follow-up plan {plan['template_id']} v{plan['template_version']} "
                          f"confirmed; identifiers checked: {', '.join(plan['identifiers_checked'])}", "milestone",
                          "hosp", "Care plan confirmation", f"Confirmed by {_who(plan['confirmed_by'])}"))

    cards = conn.execute("SELECT * FROM order_card WHERE mrn = %s ORDER BY due_date", (mrn,)).fetchall()
    for c in cards:
        origin = (f"Doctor's instruction (handover #{c['handover_id']})" if c["origin"] == "instruction"
                  else f"Plan {plan['template_id']} item {c['item_code']}" if plan else None)
        events.append(_ev(c["due_date"], f"{c['name']} due — {c['state']}", "routine", "hosp",
                          f"Order card {c['item_code']}", origin))

    appts = conn.execute("SELECT * FROM appointment WHERE mrn = %s ORDER BY appointment_date", (mrn,)).fetchall()
    for a in appts:
        match = next((c for c in cards if c["department"] == a["department"]
                      and abs((c["due_date"] - a["appointment_date"]).days) <= 30), None)
        events.append(_ev(a["appointment_date"], f"Appointment: {a['department']}", "routine", "hosp",
                          f"Appointment list (batch #{a['batch_id']})",
                          f"Order card {match['item_code']}" if match else None))

    for g in conn.execute(
            """SELECT g.*, c.item_code, c.name FROM gap_event g JOIN order_card c USING (card_id)
               WHERE g.mrn = %s ORDER BY gap_id""", (mrn,)).fetchall():
        what = "not booked 7 days before due" if g["rule"] == "G0" else "missed: not attended by due date + 2 days"
        state = {"open": "open", "reminder_sent": "reminder sent", "resolved": "resolved",
                 "blocked": f"blocked ({g['blocked_reason']})"}[g["status"]]
        events.append(_ev(g["detected_on"], f"{g['name']} — {what} · gap {state}", "gap", "carepilot", "Morning gap check",
                          "Raised automatically by the morning gap check", g["rule"]))

    for m in conn.execute("SELECT * FROM outbox_message WHERE mrn = %s ORDER BY message_id", (mrn,)).fetchall():
        who = "CarePilot agent" if m["actor_type"] == "agent" else "System" if m["actor_type"] == "system" else m["actor"]
        status = {"sent": "sent", "blocked": f"blocked ({m['blocked_reason']})", "pending_approval": "drafted",
                  "rejected": "rejected"}[m["status"]]
        origin = (f"Gap event #{m['gap_id']}" if m["gap_id"] else f"Handover #{m['handover_id']}" if m["handover_id"]
                  else f"Sent by {_who(who)}")
        events.append(_ev(m["created_on"], f"WhatsApp to {m['recipient']}: {m['template_id'].replace('_', ' ')} — {status}",
                          "routine", "carepilot" if m["actor_type"] in ("agent", "system") else "hosp",
                          "WhatsApp (simulated)", origin))
    for i in conn.execute("SELECT * FROM inbound_message WHERE mrn = %s ORDER BY inbound_id", (mrn,)).fetchall():
        if i["kind"] == "voice":
            ai_part = (f" · AI: flag {i['ai_flag']}" + (" (AI could not read it)" if i["ai_failed"] else "")
                       + (f" · \"{i['translation']}\"" if i["translation"] else "")) if i["ai_flag"] else ""
            events.append(_ev(i["received_on"], f"Voice note from {i['from_role']} ({i['clip_id'] or 'recorded'}){ai_part}",
                              "routine", "patient", f"WhatsApp voice note #{i['inbound_id']}",
                              f"Reply to message #{i['in_reply_to']}" if i["in_reply_to"] else "Unprompted message"))
            continue
        said = f"tapped \"{i['button'].replace('_', ' ')}\"" if i["button"] else ""
        if i["text"]:
            said += (" and wrote: " if said else "wrote: ") + f"\"{i['text']}\""
        events.append(_ev(i["received_on"], f"Reply from {i['from_role']}: {said}", "routine", "patient",
                          f"WhatsApp reply #{i['inbound_id']}",
                          f"Reply to message #{i['in_reply_to']}" if i["in_reply_to"] else "Unprompted message"))
    for c in conn.execute("SELECT * FROM call_log WHERE mrn = %s ORDER BY call_id", (mrn,)).fetchall():
        events.append(_ev(c["called_on"], f"Call to {c['who']}: {c['outcome']}" + (f" — {c['note']}" if c["note"] else ""),
                          "routine", "hosp", "Call log", f"Logged by {_who(c['called_by'])}"))
    for h in conn.execute("SELECT * FROM handover WHERE mrn = %s ORDER BY handover_id", (mrn,)).fetchall():
        events.append(_ev(h["opened_on"], f"Handover opened ({h['trigger'].replace('_', ' ')}); automated messages stopped",
                          "routine", "carepilot", h["source"] or "Handover", f"Handover #{h['handover_id']}"))
        if h["gate1_on"]:
            events.append(_ev(h["gate1_on"], f"Gate 1 — coordinator: {h['gate1_choice'].replace('_', ' ')}", "routine",
                              "hosp", f"Handover #{h['handover_id']}", f"Decided by {_who(h['gate1_by'])}"))
        if h["gate2_on"]:
            events.append(_ev(h["gate2_on"], f"Gate 2 — doctor's decision: {h['gate2_decision']}", "milestone", "hosp",
                              f"Handover #{h['handover_id']}", f"Decided by {_who(h['gate2_by'])}"))
        if h["instruction_on"]:
            events.append(_ev(h["instruction_on"], f"Gate 3 — instruction: {h['instruction']}", "milestone", "hosp",
                              "Hospital record (simulated)", f"Recorded by {_who(h['instruction_by'])}"))
        if h["closed_on"]:
            events.append(_ev(h["closed_on"], f"Handover closed: {h['closed_reason']}", "routine", "hosp",
                              f"Handover #{h['handover_id']}", f"Closed by {_who(h['closed_by'])}"))

    labels = {"help_booking": "Help booking", "cant_come": "Can't come", "help_request": "Help request"}
    for t in conn.execute("SELECT * FROM coordinator_task WHERE mrn = %s AND task_type = ANY(%s) ORDER BY task_id",
                          (mrn, list(labels))).fetchall():
        events.append(_ev(t["created_at_demo"].date() if t["created_at_demo"] else t["due_date"],
                          f"Task: {labels[t['task_type']]} for {t['owner_name']} (cover {t['cover_name'] or '—'}), "
                          f"due {t['due_date']:%d %b %Y}", "routine", "carepilot", f"Task #{t['task_id']}",
                          f"Reply #{t['inbound_id']}" if t["inbound_id"] else "Patient request"))
        for a in conn.execute("SELECT * FROM task_call_attempt WHERE task_id = %s ORDER BY attempt_id",
                              (t["task_id"],)).fetchall():
            events.append(_ev(a["called_at_demo"].date(), f"Call attempt by {_who(a['called_by'])}: "
                              f"{'reached' if a['result'] == 'reached' else 'no answer'}", "routine", "hosp",
                              f"Task #{t['task_id']}", "Coordinator's call log"))
        if t["status"] == "done":
            what = (t["result"] or "done").replace("_", " ") + (f" ({t['result_code'].replace('_', ' ')})" if t["result_code"] else "") \
                + (f", appointment {t['appointment_date']:%d %b %Y}" if t["appointment_date"] else "") \
                + (f" — \"{t['done_note']}\"" if t["done_note"] else "")
            events.append(_ev(t["done_on"], f"Task closed by {_who(t['done_by'])}: {what}", "routine", "hosp",
                              f"Task #{t['task_id']}", "Coordinator's result"))
    # Use Case 2c: the call-today task, its attempts, the move to the cover, Overdue and the reached call.
    hh = lambda ts: f"{ts:%H:%M}" if ts else "—"
    for t in conn.execute("SELECT * FROM coordinator_task WHERE mrn = %s AND task_type = 'urgent_call' "
                          "AND created_at_demo IS NOT NULL ORDER BY task_id", (mrn,)).fetchall():
        src = f"Task #{t['task_id']}"
        events.append(_ev(t["created_at_demo"].date(), f"Urgent — call the patient today: {t['moved_from'] or t['owner_name']} "
                          f"by {hh(t['owner_deadline_at'])}, cover {t['cover_name']} by {hh(t['cover_deadline_at'])} "
                          f"(created {hh(t['created_at_demo'])})", "routine", "carepilot", src,
                          f"Doctor's Urgent choice (handover #{t['handover_id']})"))
        for a in conn.execute("SELECT * FROM task_call_attempt WHERE task_id = %s ORDER BY attempt_id",
                              (t["task_id"],)).fetchall():
            events.append(_ev(a["called_at_demo"].date(), f"Call attempt {hh(a['called_at_demo'])} by {_who(a['called_by'])}: "
                              f"{'reached' if a['result'] == 'reached' else 'no answer'}"
                              + (f' — "{a["note"]}"' if a["note"] else ""), "routine", "hosp", src, "Coordinator's call log"))
        if t["moved_at_demo"]:
            events.append(_ev(t["moved_at_demo"].date(), f"Urgent call moved to {t['owner_name']} (cover), from {t['moved_from']} "
                              f"at {hh(t['moved_at_demo'])}: {t['moved_reason']}", "routine",
                              "carepilot" if t["moved_by"] == "CarePilot" else "hosp", src,
                              "System at the owner's deadline" if t["moved_by"] == "CarePilot"
                              else f"Passed by {_who(t['moved_by'])}"))
        if t["overdue_at_demo"]:
            events.append(_ev(t["overdue_at_demo"].date(), f"Urgent call Overdue after {hh(t['cover_deadline_at'])} — "
                              f"not reached yet ({t['owner_name']})", "gap", "carepilot", src,
                              "System at the cover's deadline"))
        if t["status"] == "done":
            booked = (f"; visit booked {t['appointment_date']:%d %b %Y}"
                      + (f" {t['appointment_time']:%H:%M}" if t["appointment_time"] else "")) if t["appointment_date"] \
                else "; no booking recorded"
            events.append(_ev(t["done_on"], f"Patient reached by {_who(t['done_by'])} at {hh(t['done_at_demo'])}: time to call "
                              f"{t['minutes_to_call']} minutes{booked}", "routine", "hosp", src, "Coordinator's call log"))
    # Use Case 2T: the test cycle — reports (hospital or outside) and the doctor's review, never a result value.
    for r in conn.execute("""SELECT r.*, c.name AS card_name FROM test_report r LEFT JOIN order_card c USING (card_id)
                             WHERE r.mrn = %s ORDER BY r.report_id""", (mrn,)).fetchall():
        what = r["card_name"] or r["test_code"] or "report"
        src = f"Report #{r['report_id']}" + (f" ({r['file_name']})" if r["file_name"] else "")
        if r["road"] == "hospital":
            label = {"verified": f"Lab report for {what} linked and verified (MRN and test code match)",
                     "mismatch": f"Lab report for {what} not linked: mismatch — coordinator to check",
                     "exception": f"Lab report ({r['test_code']}) matched no test card — exceptions list"}[r["status"]]
            events.append(_ev(r["received_at_demo"].date(), f"{label} · {hh(r['received_at_demo'])}", "routine",
                              "hosp", src, "Hospital lab (simulated)"))
        else:
            events.append(_ev(r["received_at_demo"].date(), f"Outside report received ({r['file_name']}) — held "
                              f"Unverified · {hh(r['received_at_demo'])}", "routine", "patient", src,
                              r["channel"] or "Outside document"))
            if r["status"] == "verified":
                events.append(_ev(r["verified_at_demo"].date(), f"Outside report verified by {_who(r['verified_by'])} "
                                  f"({', '.join(r['matched'] or [])} match) — {what}", "routine", "hosp", src,
                                  "Coordinator verification"))
            elif r["status"] == "rejected":
                events.append(_ev(r["received_at_demo"].date(), f"Outside report rejected by {_who(r['rejected_by'])}: "
                                  f"{r['reject_reason']}", "routine", "hosp", src, "Coordinator verification"))
    for v in conn.execute("""SELECT v.*, c.name AS card_name FROM report_review v JOIN order_card c USING (card_id)
                             WHERE v.mrn = %s ORDER BY v.review_id""", (mrn,)).fetchall():
        src = f"Report review #{v['review_id']}"
        events.append(_ev(v["created_at_demo"].date(), f"{v['card_name']}: report ready for review ({v['road']}) — "
                          f"{v['moved_from'] or v['doctor']}", "routine", "carepilot", src, f"Report #{v['report_id']}"))
        if v["opened_at_demo"]:
            events.append(_ev(v["opened_at_demo"].date(), f"Source file opened by {_who(v['opened_by'])} at "
                              f"{hh(v['opened_at_demo'])}", "routine", "hosp", src, "Doctor"))
        if v["moved_at_demo"]:
            events.append(_ev(v["moved_at_demo"].date(), f"Report review moved to {v['doctor']} (cover) after 1 working "
                              "day", "routine", "carepilot", src, "Doctor's deadline"))
        if v["decided_at_demo"]:
            events.append(_ev(v["decided_at_demo"].date(), f"Doctor's decision on the {v['card_name']} report: "
                              + ("Continue as planned" if v["decision"] == "continue" else "Needs discussion")
                              + (f" — instruction: {v['instruction']}" if v["instruction"] else ""), "milestone", "hosp",
                              src, f"Decided by {_who(v['decided_by'])}"))
    for pr in conn.execute("SELECT * FROM proposal WHERE mrn = %s ORDER BY proposal_id", (mrn,)).fetchall():
        events.append(_ev(pr["created_at_demo"].date(), f"Proposal drafted by AI: {pr['summary'] or 'no draft'} "
                          f"(intent {pr['intent'] or 'none'}) — {pr['status']}", "routine", "carepilot",
                          f"Proposal #{pr['proposal_id']}", f"Voice/text reply #{pr['inbound_id']}"))
    events.sort(key=lambda e: (e["date"], e["kind"] != "milestone"))
    routine = [e for e in events if e["kind"] == "routine"]
    return {"mrn": mrn, "full_name": p["full_name"], "status": p["status"],
            "therapy": plan["therapy"] if plan else None, "plan": plan["template_id"] if plan else None, "events": events,
            "traced": sum(1 for e in routine if e["origin"]), "need_origin": len(routine),
            "milestones": sum(1 for e in events if e["kind"] == "milestone")}


def recent_decisions(conn) -> list[dict]:
    from . import urgent_calls

    rows = conn.execute(
        """
        SELECT h.handover_id, h.mrn, p.full_name, h.gate2_decision, h.gate2_on, h.gate2_by, h.instruction,
               h.instruction_on, h.stage
        FROM handover h JOIN patient p USING (mrn) WHERE h.gate2_decision IS NOT NULL
        ORDER BY h.gate2_on DESC, h.handover_id DESC LIMIT 20
        """
    ).fetchall()
    return [{**r, "call_line": urgent_calls.call_line(conn, r["handover_id"])} for r in rows]
