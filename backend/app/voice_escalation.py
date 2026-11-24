"""UC1-V voice escalation for unbooked follow-ups (spec v0.1): a simulated outbound call from the hospital.

V1 raises the event, V2 is the only door to a call, the fixed script runs the call, V6 (voice_sorter) puts each
reply into one of four labels and the rules here choose the next library line. Health beats everything: a concern
or an AI fault stops the script, books nothing, sets the send block and hands over to a person within two hours.
Every step writes its reason code in the transaction that changes the state (R8).
"""
import io
import re
import wave
from datetime import date, datetime, time, timedelta

from psycopg.types.json import Jsonb

from . import ai, audit, clock, handover, messaging, policy, states, tasks, voice_script, voice_sorter
from .agent import AGENT, set_next_action
from .errors import Conflict, Invalid, NotFound
from .voice_sorter import HEALTH, NO, UNCLEAR, YES

RULE, SYSTEM = "V1", "CarePilot rules"
CONSENT = "automated_voice_call"
SOURCE = "voice_agent"
REJECTED = "patient_rejected"  # a reason of the no_answer outcome: the call never connected
TEMPLATE = {"W1": "uc1v_w1_booked", "W2": "uc1v_w2_safety"}
REMINDERS = ("booking_reminder", "test_booking_reminder")

# The task each safe exit leaves with the coordinator (section 6.2).
TASKS = {
    "voice_not_connected": "Call did not connect",
    "voice_not_reached": "Patient not reached",
    "voice_declined": "Declined after the explanation: no more calls for this scan until a person decides",
    "voice_book_by_phone": "Book the scan by phone",
    "voice_unclear": "Reply unclear twice: call the patient",
    "voice_rebook": "The agent's booking was undone: book the scan by phone",
}


def config(conn) -> dict | None:
    cfg = policy.settings(conn).get("voice_escalation")
    return cfg if cfg and cfg.get("enabled") else None


def _config(conn) -> dict:
    cfg = config(conn)
    if cfg is None:
        raise Conflict("Voice escalation is not switched on in this data set")
    return cfg


def _log(conn, action: str, mrn: str, reason: str, *, actor_type: str = "system", actor: str = SYSTEM,
         entity: tuple | None = None, details: dict | None = None, **extra) -> None:
    audit.append(conn, actor_type=actor_type, actor_name=actor, action=action, mrn=mrn, reason_code=reason,
                 entity_type=entity[0] if entity else None, entity_id=str(entity[1]) if entity else None,
                 details=details, **extra)


def _card(conn, card_id: int) -> dict:
    return conn.execute("SELECT * FROM order_card WHERE card_id = %s", (card_id,)).fetchone()


def _patient(conn, mrn: str) -> dict:
    return conn.execute("SELECT * FROM patient WHERE mrn = %s", (mrn,)).fetchone()


def _event(conn, escalation_id: int, lock: bool = False) -> dict:
    e = conn.execute("SELECT * FROM voice_escalation WHERE escalation_id = %s" + (" FOR UPDATE" if lock else ""),
                     (escalation_id,)).fetchone()
    if e is None:
        raise NotFound(f"Voice escalation {escalation_id} not found")
    return e


def _call(conn, call_id: int, lock: bool = False) -> dict:
    c = conn.execute("SELECT * FROM voice_call WHERE call_id = %s" + (" FOR UPDATE" if lock else ""),
                     (call_id,)).fetchone()
    if c is None:
        raise NotFound(f"Voice call {call_id} not found")
    return c


# ---------------------------------------------------------------------------
# V1: the rule
# ---------------------------------------------------------------------------
def reminders(conn) -> list[dict]:
    """Unbooked cards with a G0 reminder sent, and the demo time of the first one."""
    rows = conn.execute(
        """
        SELECT c.card_id, c.mrn, c.due_date, c.name,
               (SELECT min(a.demo_date + a.demo_time::time) FROM outbox_message o
                  JOIN audit_ledger a ON a.entity_type = 'outbox_message' AND a.entity_id = o.message_id::text
                                     AND a.action IN ('message_sent', 'message_approved')
                 WHERE o.gap_id = g.gap_id AND o.card_id = c.card_id AND o.status = 'sent'
                   AND o.recipient = 'patient' AND o.template_id = ANY(%s)) AS reminder_at
        FROM gap_event g JOIN order_card c USING (card_id)
        WHERE g.rule = 'G0' AND g.status = 'reminder_sent' AND g.due_date = c.due_date AND c.state = 'Due'
        ORDER BY c.card_id
        """, (list(REMINDERS),)).fetchall()
    return rows


def script_gap(conn, p: dict, card_id: int) -> tuple[str, str] | None:
    card = _card(conn, card_id)
    plan = conn.execute("SELECT template_id, therapy FROM care_plan WHERE care_plan_id = %s",
                        (card["care_plan_id"],)).fetchone() if card["care_plan_id"] else None
    return voice_script.script_gap(p["preferred_language"], p, card, plan)


def catch_up(conn, since: date) -> None:
    """Golden Path: the date was set forward onto (after 07:30) or over a card's T-7 day. That day's morning job runs
    as on the day itself, for every patient, so each reminder really went then. It never goes back before `since`, the date the
    clock stood at: a plan confirmed later gets no reminder from the past."""
    if not (config(conn) or {}).get("catch_up_reminder"):
        return
    from . import agent, rules

    now = clock.get_now(conn)
    ahead = int(policy.thresholds(conn)["g0_booking_days_before_due"])
    last = now.date()
    if since == last or now.time() < time(7, 30):
        last -= timedelta(days=1)  # that morning has not come, or the clock was already standing on that day
    days = [r["day"] for r in conn.execute(
        """SELECT DISTINCT due_date - %(ahead)s::int AS day FROM order_card
           WHERE state = 'Due' AND due_date - %(ahead)s::int >= %(since)s AND due_date - %(ahead)s::int <= %(last)s
           ORDER BY day""", {"ahead": ahead, "since": since, "last": last}).fetchall()]
    for day in days:
        clock.set_today(conn, day)
        clock.set_time(conn, time(7, 30))
        rules.run_gap_check(conn)
        agent.run_agent(conn)
    clock.set_today(conn, now.date())
    clock.set_time(conn, now.time())


def run_due(conn) -> list[dict]:
    """Raise voice_escalation_due. Safe to run any number of times (key: card + due date + V1)."""
    cfg = config(conn)
    if not cfg:
        return []
    token = audit.DEMO_TIME.set(None)  # the event is logged at the real demo time, also inside the 07:00 job
    try:
        now, raised = clock.get_now(conn), []
        for r in reminders(conn):
            if r["reminder_at"] is None or now < r["reminder_at"] + timedelta(hours=cfg["delay_hours"]):
                continue
            if cfg.get("scripted_only") and script_gap(conn, _patient(conn, r["mrn"]), r["card_id"]):
                continue  # Golden Path: no event without true lines; the card says why
            e = conn.execute(
                """INSERT INTO voice_escalation (rule, card_id, mrn, due_date, reminder_at, raised_at)
                   VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (card_id, due_date, rule) DO NOTHING RETURNING *""",
                (RULE, r["card_id"], r["mrn"], r["due_date"], r["reminder_at"], now)).fetchone()
            if e:
                raised.append(e)
                _log(conn, "voice_escalation_due", e["mrn"], "not_booked_48h_after_reminder", rule=RULE,
                     entity=("voice_escalation", e["escalation_id"]),
                     details={"card_id": e["card_id"], "due_date": e["due_date"].isoformat(),
                              "reminder_at": str(e["reminder_at"]), "delay_hours": cfg["delay_hours"]})
                set_next_action(conn, e["mrn"], f"Automated call due: {r['name']} still not booked")
        return raised
    finally:
        audit.DEMO_TIME.reset(token)


def ready(conn) -> list[int]:
    """Events whose call can start now: the call screen opens for these."""
    if not config(conn):
        return []
    rows = conn.execute("SELECT * FROM voice_escalation WHERE status IN ('due', 'held', 'retry') "
                        "ORDER BY escalation_id").fetchall()
    return [e["escalation_id"] for e in rows if all(t["ok"] for t in gate(conn, e))]


def first_call_time(cfg: dict, at: datetime) -> datetime:
    """The first moment from `at` on that the gate lets a call out: inside the hours, never on a no-call day."""
    opens, closes = time.fromisoformat(cfg["window_start"]), time.fromisoformat(cfg["window_end"])
    if at.time() < opens:
        at = datetime.combine(at.date(), opens)
    elif at.time() >= closes:
        at = datetime.combine(at.date() + timedelta(days=1), opens)
    while at.isoweekday() in cfg.get("no_call_weekdays", []):
        at = datetime.combine(at.date() + timedelta(days=1), opens)
    return at


def advance_to_call(conn, user: dict) -> dict:
    """Demo: move the clock 48 hours past the reminder, to the next moment inside the call window."""
    cfg = _config(conn)
    waiting = [e for e in conn.execute("SELECT * FROM voice_escalation WHERE status <> 'done'").fetchall()]
    now = clock.get_now(conn)
    if waiting:
        target = max(now, waiting[0]["retry_at"] or now)
    else:
        sent = [r["reminder_at"] for r in reminders(conn) if r["reminder_at"] and not conn.execute(
            "SELECT 1 FROM voice_escalation WHERE card_id = %s AND due_date = %s AND rule = %s",
            (r["card_id"], r["due_date"], RULE)).fetchone()]
        if not sent:
            raise Conflict("No unbooked scan with an unanswered reminder is waiting for a call")
        target = max(now, min(sent) + timedelta(hours=cfg["delay_hours"]))
    target = first_call_time(cfg, target)
    if target > now:
        clock.set_forward(conn, target.date(), user["user_id"], target.strftime("%H:%M"))
    run_due(conn)
    event = conn.execute("SELECT escalation_id FROM voice_escalation WHERE status <> 'done' "
                         "ORDER BY escalation_id LIMIT 1").fetchone()
    return {"escalation_id": event and event["escalation_id"], "today": target.date(), "time": f"{target:%H:%M}"}


# ---------------------------------------------------------------------------
# V2: the gate
# ---------------------------------------------------------------------------
def _permitted(conn, e: dict) -> list[dict]:
    """The checks that also hold for a booking during the call."""
    p, card = _patient(conn, e["mrn"]), _card(conn, e["card_id"])
    scopes = messaging.latest_scopes(conn, e["mrn"])
    proposal = conn.execute("SELECT 1 FROM proposal WHERE mrn = %s AND status = 'drafted'", (e["mrn"],)).fetchone()
    return [
        {"check": "consent", "label": "Automated voice call consent on record", "ok": CONSENT in scopes,
         "reason": "no_voice_call_consent"},
        {"check": "not_paused", "label": "Not paused, closed or agent off",
         "ok": p["status"] == "Active" and bool(p["agent_on"]), "reason": "patient_paused_or_agent_off"},
        {"check": "no_handover", "label": "No handover open", "ok": messaging.open_handover(conn, e["mrn"]) is None,
         "reason": "handover_open"},
        {"check": "no_proposal", "label": "No open proposal", "ok": proposal is None, "reason": "open_proposal"},
        {"check": "still_unbooked", "label": "Scan still not booked",
         "ok": card["state"] == "Due" and card["due_date"] == e["due_date"], "reason": "card_no_longer_unbooked"},
    ]


def ignored(conn, mrn: str, since: datetime) -> bool:
    """The patient has sent nothing back since the reminder: no tap, text or voice note."""
    return conn.execute(
        """SELECT 1 FROM inbound_message WHERE mrn = %s AND from_role = 'patient'
             AND received_on + coalesce(received_time, time '23:59') >= %s LIMIT 1""", (mrn, since)).fetchone() is None


def gate(conn, e: dict) -> list[dict]:
    cfg, now = _config(conn), clock.get_now(conn)
    p = _patient(conn, e["mrn"])
    checks = _permitted(conn, e)
    checks.insert(4, {
        "check": "window", "label": f"Inside the call window ({cfg['window_start']} to {cfg['window_end']})",
        "ok": time.fromisoformat(cfg["window_start"]) <= now.time() < time.fromisoformat(cfg["window_end"]),
        "reason": "outside_call_window"})
    checks.insert(5, {"check": "weekday", "label": "No calls on Sunday",
                      "ok": now.isoweekday() not in cfg.get("no_call_weekdays", []), "reason": "no_call_day"})
    checks.append({"check": "no_reply", "label": "No reply to the reminder",
                   "ok": ignored(conn, e["mrn"], e["reminder_at"]), "reason": "patient_replied"})
    language = p["preferred_language"]
    gap = script_gap(conn, p, e["card_id"])
    if not gap and conn.execute("SELECT count(*) AS n FROM voice_line WHERE language = %s",
                                (language,)).fetchone()["n"] != 12:
        gap = "no_lines_in_preferred_language", f"No fixed script in {language}"
    checks.append({"check": "library", "label": gap[1] if gap else f"Fixed script fits this patient ({language})",
                   "ok": not gap, "reason": gap[0] if gap else "no_lines_in_preferred_language"})
    if e["retry_at"]:
        checks.append({"check": "retry_gap", "label": f"{cfg['retry_hours']} hours since the unanswered call",
                       "ok": now >= e["retry_at"], "reason": "retry_not_due"})
    return checks


def dial(conn, escalation_id: int, script: str = "s2") -> dict:
    """The only way a call starts."""
    e = _event(conn, escalation_id, lock=True)
    if script not in scripts():
        raise Invalid("script must be one of: " + ", ".join(scripts()))
    if e["status"] in ("in_call", "done"):
        raise Conflict("This escalation already has its call")
    checks = gate(conn, e)
    failed = [c for c in checks if not c["ok"]]
    ref = ("voice_escalation", escalation_id)
    if failed:
        conn.execute("UPDATE voice_escalation SET status = 'held', held_reason = %s, gate = %s WHERE escalation_id = %s",
                     (failed[0]["reason"], Jsonb(checks), escalation_id))
        _log(conn, "gate_blocked", e["mrn"], failed[0]["reason"], rule=RULE, entity=ref, outcome="no_call",
             details={"failed": [c["reason"] for c in failed], "checks": checks})
        return {"call": None, "gate": checks, "held_reason": failed[0]["reason"]}

    p, settings, cfg = _patient(conn, e["mrn"]), policy.settings(conn), _config(conn)
    provider = ai.provider_for(settings.get("ai") or {}, cfg["sorter_try_seconds"])
    simulated_ai = isinstance(provider, ai.Fake)
    models = {"speech": provider.stt_model, "sorter": provider.llm_model,
              "translation": "fake" if simulated_ai else "mayura:v1",
              "voice": "none (simulated AI)" if simulated_ai else f"{cfg['tts_model']} ({cfg['agent_speaker']})"}
    call = conn.execute(
        """INSERT INTO voice_call (escalation_id, mrn, card_id, language, script, started_at, gate, models)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
        (escalation_id, e["mrn"], e["card_id"], p["preferred_language"], script, clock.get_now(conn), Jsonb(checks),
         Jsonb(models))).fetchone()
    conn.execute("UPDATE voice_escalation SET status = 'in_call', held_reason = NULL, gate = %s WHERE escalation_id = %s",
                 (Jsonb(checks), escalation_id))
    _log(conn, "gate_passed", e["mrn"], "all_checks_passed", rule=RULE, entity=ref,
         details={"checks": [c["check"] for c in checks]})
    _log(conn, "call_started", e["mrn"], "simulated", actor_type="agent", actor=AGENT,
         entity=("voice_call", call["call_id"]), outcome="simulated",
         details={"language": call["language"], "models": models, "patient_voice": "synthetic"})
    set_next_action(conn, e["mrn"], "Automated call in progress (simulated)")
    return {"call": record(conn, call["call_id"]), "gate": checks, "held_reason": None}


def answer(conn, call_id: int) -> dict:
    """She picks up: the call opens with L1."""
    c = _call(conn, call_id, lock=True)
    if c["status"] != "ringing":
        raise Conflict("The call is not ringing")
    conn.execute("UPDATE voice_call SET status = 'live' WHERE call_id = %s", (call_id,))
    _say(conn, c, "L1")
    return record(conn, call_id)


def no_answer(conn, call_id: int) -> dict:
    c = _call(conn, call_id, lock=True)
    if c["status"] != "ringing":
        raise Conflict("Only a ringing call can go unanswered")
    cfg = _config(conn)
    attempts = conn.execute("SELECT count(*) AS n FROM voice_call WHERE escalation_id = %s",
                            (c["escalation_id"],)).fetchone()["n"]
    if attempts < cfg["call_attempts"]:
        retry = clock.get_now(conn) + timedelta(hours=cfg["retry_hours"])
        _end(conn, c, "no_answer", "no_answer_retry", event="retry")
        conn.execute("UPDATE voice_escalation SET retry_at = %s WHERE escalation_id = %s",
                     (retry, c["escalation_id"]))
        set_next_action(conn, c["mrn"], f"No answer: one retry from {retry:%d %b %H:%M}")
    else:
        _task(conn, c, "voice_not_connected")
        _end(conn, c, "no_answer", "no_answer_second_miss")
    return record(conn, call_id)


def reject(conn, call_id: int) -> dict:
    """She presses Reject: nothing was said, and no machine calls her again for this scan (R4). A person follows up."""
    c = _call(conn, call_id, lock=True)
    if c["status"] != "ringing":
        raise Conflict("Only a ringing call can be rejected")
    _task(conn, c, "voice_not_connected", "Patient rejected the call: no automatic retry")
    _end(conn, c, "no_answer", REJECTED)
    return record(conn, call_id)


def _info(p: dict, now: datetime, reminder_at: datetime, **more) -> dict:
    return {"escalation_id": None, "reminder_at": reminder_at, "retry_at": None, "call_id": None, "blocked": [],
            "no_script": None, "opens_at": None, "hours_since": int((now - reminder_at).total_seconds() // 3600),
            "patient": {k: p[k] for k in ("mrn", "full_name", "preferred_language")}, **more}


def for_cards(conn, cards: list[dict]) -> list[dict]:
    """Each order card's `voice`: what its Simulate call button needs. It is there only for a card still not booked
    whose T-7 reminder had no reply for the delay (48 hours), or while its call is open. In the Golden Path an
    overdue card without true lines has `voice.no_script` in place of the button, and `voice_note` says why a card
    that can get a call has no button yet."""
    for card in cards:
        card["voice"] = card["voice_note"] = None
    cfg = config(conn)
    if not cfg:
        return cards
    now, delay = clock.get_now(conn), timedelta(hours=cfg["delay_hours"])
    events = {(e["card_id"], e["due_date"]): e for e in conn.execute(
        "SELECT * FROM voice_escalation ORDER BY escalation_id").fetchall()}
    explain = cfg.get("scripted_only")
    if explain:
        told = {r["card_id"]: r["reminder_at"] for r in reminders(conn)}
        ahead = timedelta(days=int(policy.thresholds(conn)["g0_booking_days_before_due"]))
    for card in cards:
        if card["state"] != "Due":
            continue
        e = events.get((card["card_id"], card["due_date"]))
        if e and e["status"] == "done":
            continue
        if not e:
            if not explain:
                continue
            p, at = _patient(conn, card["mrn"]), told.get(card["card_id"])
            if at and now >= at + delay:
                if ignored(conn, p["mrn"], at) and (gap := script_gap(conn, p, card["card_id"])):
                    card["voice"] = _info(p, now, at, no_script=gap[1])
            elif card["due_date"] - ahead <= now.date() <= card["due_date"] and not script_gap(conn, p, card["card_id"]):
                card["voice_note"] = (f"Simulate call opens on {first_call_time(cfg, at + delay):%a %d %b %H:%M} "
                                      "if there is no reply." if at
                                      else "No reminder has been sent for this test. Simulate call opens 2 days after "
                                           "the reminder goes out.")
            continue
        p = _patient(conn, e["mrn"])
        info = _info(p, now, e["reminder_at"], escalation_id=e["escalation_id"], retry_at=e["retry_at"])
        if e["status"] == "in_call":
            open_call = conn.execute("SELECT call_id FROM voice_call WHERE escalation_id = %s AND status <> 'ended'",
                                     (e["escalation_id"],)).fetchone()
            if not open_call:
                continue
            info["call_id"] = open_call["call_id"]
        else:
            if not ignored(conn, e["mrn"], e["reminder_at"]):
                if explain:
                    card["voice_note"] = "No simulated call: the patient replied after the reminder."
                continue
            failed = [t for t in gate(conn, e) if not t["ok"]]
            info["blocked"] = [t["label"] for t in failed]
            if failed and {t["reason"] for t in failed} <= {"outside_call_window", "no_call_day", "retry_not_due"}:
                info["opens_at"] = first_call_time(cfg, max(now, e["retry_at"] or now))  # only waiting blocks it
        card["voice"] = info
    return cards


# ---------------------------------------------------------------------------
# The fixed script
# ---------------------------------------------------------------------------
def _turn(conn, c: dict, who: str, **f) -> int:
    seq = conn.execute("SELECT coalesce(max(seq), 0) + 1 AS n FROM voice_turn WHERE call_id = %s",
                       (c["call_id"],)).fetchone()["n"]
    cols = ["call_id", "seq", "who", "at_demo", *f]
    conn.execute(f"INSERT INTO voice_turn ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                 (c["call_id"], seq, who, clock.get_now(conn), *f.values()))
    return seq


def _slot(conn, slot_id: int | None) -> dict | None:
    return conn.execute("SELECT * FROM voice_slot WHERE slot_id = %s", (slot_id,)).fetchone() if slot_id else None


def _say(conn, c: dict, line_id: str) -> None:
    """The one place the agent speaks: a library line, saved with its approval status."""
    ln = voice_script.line(conn, line_id, c["language"], _patient(conn, c["mrn"]), _slot(conn, c.get("slot_id")))
    seq = _turn(conn, c, "agent", line_id=line_id, approval_status=ln["status"], text=ln["spoken"],
                translation=ln["spoken_en"])
    _log(conn, "line_spoken", c["mrn"], line_id, actor_type="agent", actor=AGENT, entity=("voice_call", c["call_id"]),
         template_id=line_id, template_version=ln["version"],
         details={"line": line_id, "turn": seq, "approval_status": ln["status"]})


def _set(conn, c: dict, **fields) -> None:
    conn.execute(f"UPDATE voice_call SET {', '.join(f'{k} = %s' for k in fields)} WHERE call_id = %s",
                 (*fields.values(), c["call_id"]))
    c.update(fields)


def _end(conn, c: dict, outcome: str, reason: str, event: str = "done") -> None:
    conn.execute("UPDATE voice_call SET status = 'ended', outcome = %s, reason_code = %s, ended_at = %s, "
                 "ended_tx_at = now() WHERE call_id = %s", (outcome, reason, clock.get_now(conn), c["call_id"]))
    conn.execute("UPDATE voice_escalation SET status = %s WHERE escalation_id = %s", (event, c["escalation_id"]))
    _log(conn, "call_ended", c["mrn"], reason, actor_type="agent", actor=AGENT, entity=("voice_call", c["call_id"]),
         outcome=outcome)


def _task(conn, c: dict, task_type: str, what: str | None = None) -> None:
    card = _card(conn, c["card_id"])
    tasks.create_task(conn, mrn=c["mrn"], task_type=task_type, card_id=c["card_id"], owner_name=card["owner_name"],
                      reason=f"{what or TASKS[task_type]} ({card['name']})", actor_type="system", actor=SYSTEM,
                      dedupe_key=f"{task_type}:{c['call_id']}")
    set_next_action(conn, c["mrn"], f"{what or TASKS[task_type]}: {card['owner_name']}")


def reply(conn, call_id: int, after_seq: int, *, text: str | None = None, clip_id: str | None = None,
          silence: bool = False) -> dict:
    """One patient reply, sorted, then the rules' next step. after_seq: the agent turn it answers."""
    c = _call(conn, call_id, lock=True)
    if c["status"] != "live":
        raise Conflict("The call is not live")
    last = conn.execute("SELECT * FROM voice_turn WHERE call_id = %s ORDER BY seq DESC LIMIT 1", (call_id,)).fetchone()
    if last["seq"] != after_seq:
        raise Conflict("That question has already been answered")

    audio, audio_id, take, p = None, None, None, _patient(conn, c["mrn"])
    if silence:
        text = ""
    elif text is None:  # the simulated patient
        clip_id = clip_id or _next_clip(c, last["line_id"])
        clip = voice_script.clip(c["language"], clip_id, p, c["script"])
        if clip is None:
            raise Invalid("No recorded patient clip for this question")
        take = clip["take"]
        if clip["audio"]:
            audio = clip["audio"]
            audio_id = conn.execute("INSERT INTO audio_blob (mime, data) VALUES ('audio/wav', %s) RETURNING audio_id",
                                    (audio,)).fetchone()["audio_id"]
        else:
            text = clip["text"]  # no recording on file: the screen says so and shows the words
    else:
        clip_id = None

    r = voice_sorter.sort_reply(conn, step=c["stage"], language=c["language"], audio=audio, text=text, clip_id=take,
                                name=voice_script.named(c["language"], p["full_name"]),
                                other_names=tuple(n["नाम"] for full, n in voice_script.pack(c["language"])["names"].items()
                                                  if full != p["full_name"]))
    seq = _turn(conn, c, "patient", text=r["transcript"], translation=r["translation"], label=r["label"],
                reason_code=r["reason"], ai_failed=r["failed"], model=r["model"], clip_id=clip_id, audio_id=audio_id)
    said = {**r, "seq": seq}
    identity = c["stage"] == "identity"
    _log(conn, "identity_reply" if identity else "reply_label", c["mrn"], r["reason"],
         actor_type="ai", actor="Sarvam reply sorter", entity=("voice_call", call_id), outcome=r["label"],
         model=r["model"], prompt_version=r["prompt_version"],
         details={"turn": seq, "label": r["label"], "errors": r["errors"]})

    # R3 and R4: health first, whatever the stage and whatever else she said.
    if r["label"] == HEALTH:
        _safety_stop(conn, c, said)
    else:
        {"identity": _after_identity, "booking": _after_booking, "slot": _after_slot}[c["stage"]](conn, c, r["label"])
    return record(conn, call_id)


def _after_identity(conn, c: dict, label: str) -> None:
    if label == YES:
        _set(conn, c, stage="booking", identified=True)
        _log(conn, "identity_confirmed", c["mrn"], "patient_confirmed", actor_type="agent", actor=AGENT,
             entity=("voice_call", c["call_id"]))
        _say(conn, c, "L2")
    elif label == UNCLEAR and c["identity_repeats"] < _config(conn)["identity_repeats"]:
        _set(conn, c, identity_repeats=c["identity_repeats"] + 1)
        _say(conn, c, "L1")
    else:
        _say(conn, c, "L1b")
        _task(conn, c, "voice_not_reached")
        _end(conn, c, "wrong_person", "not_the_patient" if label == NO else "identity_not_confirmed")


def _unclear(conn, c: dict, again: str) -> None:
    if c["reasks"] < _config(conn)["reasks"]:
        _set(conn, c, reasks=c["reasks"] + 1)
        _say(conn, c, again)
    else:
        _say(conn, c, "L7")  # the only polite close in the library that promises a person's call
        _task(conn, c, "voice_unclear")
        _end(conn, c, "unclear", "reply_unclear_twice")


def _after_booking(conn, c: dict, label: str) -> None:
    if label == UNCLEAR:
        _unclear(conn, c, "L3")
    elif label == YES:
        _offer(conn, c)
    elif c["explanations"] < _config(conn)["explanations"]:
        _set(conn, c, explanations=c["explanations"] + 1)
        _say(conn, c, "L4")
    else:  # R6: the second no is accepted
        _say(conn, c, "L8")
        _task(conn, c, "voice_declined")
        _log(conn, "doctor_informed", c["mrn"], "declined_after_explanation", entity=("voice_call", c["call_id"]))
        _end(conn, c, "declined", "declined_after_explanation")


def _by_phone(conn, c: dict, reason: str) -> None:
    _say(conn, c, "L7")
    _task(conn, c, "voice_book_by_phone")
    _end(conn, c, "slot_unsuitable", reason)


def _blocker(conn, c: dict) -> str | None:
    return next((t["reason"] for t in _permitted(conn, _event(conn, c["escalation_id"])) if not t["ok"]), None)


def _offer(conn, c: dict) -> None:
    """V8: rules pick one free slot, at least two working days ahead."""
    cfg = _config(conn)
    if blocked := _blocker(conn, c):
        return _by_phone(conn, c, blocked)
    earliest = policy.add_working_days(clock.get_today(conn), cfg["slot_working_days_ahead"])
    last, day = _card(conn, c["card_id"])["due_date"], earliest
    while cfg.get("slot_times") and day <= last:  # Golden Path: the simulated list follows the card's due date
        if day.weekday() < 5:
            for at in cfg["slot_times"]:
                conn.execute("INSERT INTO voice_slot (slot_date, slot_time, status) VALUES (%s, %s, 'free') "
                             "ON CONFLICT (slot_date, slot_time) DO NOTHING", (day, at))
        day += timedelta(days=1)
    slot = conn.execute("SELECT * FROM voice_slot WHERE status = 'free' AND slot_date BETWEEN %s AND %s "
                        "AND extract(isodow FROM slot_date) < 6 ORDER BY slot_date, slot_time LIMIT 1",
                        (earliest, last)).fetchone()
    if slot is None:
        return _by_phone(conn, c, "no_free_slot")
    _set(conn, c, stage="slot", slot_id=slot["slot_id"])
    _log(conn, "slot_offered", c["mrn"], "first_free_slot", rule="V8", entity=("voice_call", c["call_id"]),
         details={"slot_id": slot["slot_id"], "date": slot["slot_date"].isoformat(),
                  "time": f"{slot['slot_time']:%H:%M}"})
    _say(conn, c, "L5")


def _after_slot(conn, c: dict, label: str) -> None:
    if label == UNCLEAR:
        _unclear(conn, c, "L5")  # the same slot again, so that a yes is a yes to a slot
    elif label == NO:
        _by_phone(conn, c, "slot_unsuitable")
    else:
        _book(conn, c)


def _send(conn, c: dict, line_id: str, handover_id: int | None = None) -> None:
    words, english = voice_script.values(c["language"], _patient(conn, c["mrn"]), _slot(conn, c.get("slot_id")))
    msg = messaging.send_message(conn, mrn=c["mrn"], template_id=TEMPLATE[line_id], actor_type="agent", actor=AGENT,
                                 reason_code=line_id, card_id=c["card_id"], handover_id=handover_id,
                                 extra={**english, **words})
    _log(conn, "whatsapp_sent" if msg["status"] == "sent" else "whatsapp_blocked", c["mrn"],
         line_id if msg["status"] == "sent" else msg["blocked_reason"], actor_type="agent", actor=AGENT,
         entity=("outbox_message", msg["message_id"]), details={"message": line_id, "status": msg["status"]})


def _book(conn, c: dict) -> None:
    """V9: card, slot, appointment, L6 and one W1, in one transaction, with the agent as the actor."""
    if blocked := _blocker(conn, c):
        return _by_phone(conn, c, blocked)
    card = _card(conn, c["card_id"])
    slot = conn.execute("UPDATE voice_slot SET status = 'taken', taken_by_card = %s "
                        "WHERE slot_id = %s AND status = 'free' RETURNING *",
                        (card["card_id"], c["slot_id"])).fetchone()
    if slot is None:
        return _by_phone(conn, c, "slot_no_longer_free")
    day, at = slot["slot_date"], slot["slot_time"]
    states.transition_card(conn, card["card_id"], "Booked", "agent", AGENT, source=SOURCE,
                           appointment_date=day.isoformat(), appointment_time=f"{at:%H:%M}", call_id=c["call_id"])
    conn.execute("UPDATE order_card SET appointment_time = %s, label = NULL, patient_says_booked = false "
                 "WHERE card_id = %s", (at, card["card_id"]))
    conn.execute(
        """INSERT INTO appointment (mrn, appointment_date, appointment_time, department, card_id, item, source)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (card_id) WHERE card_id IS NOT NULL DO UPDATE
             SET appointment_date = EXCLUDED.appointment_date, appointment_time = EXCLUDED.appointment_time,
                 source = EXCLUDED.source""",
        (c["mrn"], day, at, card["department"], card["card_id"], card["name"], SOURCE))
    conn.execute("UPDATE gap_event SET status = 'resolved', outcome = 'booked_by_agent' "
                 "WHERE card_id = %s AND status <> 'resolved'", (card["card_id"],))
    _log(conn, "booked_by_agent", c["mrn"], "yes_to_offered_slot", actor_type="agent", actor=AGENT,
         entity=("order_card", card["card_id"]), outcome="Booked",
         details={"date": day.isoformat(), "time": f"{at:%H:%M}", "slot_id": slot["slot_id"], "call_id": c["call_id"]})
    for t in conn.execute("SELECT task_id FROM coordinator_task WHERE card_id = %s AND task_type = 'booking_help' "
                          "AND status = 'open'", (card["card_id"],)).fetchall():
        tasks.complete_task(conn, t["task_id"], AGENT, "Booked by the agent on the call", actor_type="agent",
                            reason_code="booked_by_agent")
    set_next_action(conn, c["mrn"], f"{card['name']} booked by the agent for {day:%d/%m/%Y} {at:%H:%M}")
    _say(conn, c, "L6")
    _send(conn, c, "W1")
    _end(conn, c, "booked", "yes_to_offered_slot")


def _safety_stop(conn, c: dict, said: dict) -> None:
    """V10. Before she has confirmed who she is, L9 and W2 would disclose too much (R5): L1b, and still a handover."""
    h, _ = handover.open_handover(
        conn, c["mrn"], trigger="health_matter", actor_type="agent", actor=AGENT,
        patient_words=said["transcript"], translation=said["translation"], ai_failed=said["failed"],
        source=f"Simulated voice call #{c['call_id']}, {clock.get_now(conn):%d %b %Y %H:%M}"
               + (f" · AI fault: {voice_sorter.FAULT_TEXT.get(said['reason'], said['reason'])}" if said["failed"] else ""))
    reason = "ai_could_not_sort" if said["failed"] else "health_concern"
    ref = ("handover", h["handover_id"])
    _log(conn, "send_block_on", c["mrn"], reason, entity=ref)
    due = h["callback_due_at"] or clock.get_now(conn) + timedelta(hours=_config(conn)["safety_callback_hours"])
    conn.execute("UPDATE handover SET voice_call_id = %s, voice_turn = %s, callback_due_at = %s WHERE handover_id = %s",
                 (c["call_id"], said["seq"], due, h["handover_id"]))
    _say(conn, c, "L9" if c["identified"] else "L1b")
    if c["identified"]:
        _send(conn, c, "W2", h["handover_id"])
    _log(conn, "handover_created", c["mrn"], reason, actor_type="agent", actor=AGENT, entity=ref,
         outcome="due +2h", details={"due_at": f"{due:%Y-%m-%d %H:%M}", "call_id": c["call_id"],
                                     "identity_confirmed": c["identified"]})
    set_next_action(conn, c["mrn"], f"On hold: handover open; a person calls the patient by {due:%H:%M}")
    _end(conn, c, "safety_stop", reason)


def undo(conn, call_id: int, user: dict, reason: str | None) -> dict:
    """V9: the coordinator can undo the agent's booking. No new automated call follows: a person takes it."""
    c = _call(conn, call_id, lock=True)
    reason = (reason or "").strip()
    if not reason:
        raise Invalid("Undo needs a reason")
    card = _card(conn, c["card_id"])
    if c["outcome"] != "booked" or c["undone_at"] or card["state"] != "Booked" or card["booking_source"] != SOURCE:
        raise Conflict("There is no agent booking to undo on this call")
    states.transition_card(conn, card["card_id"], "Due", "person", user["user_id"], source="agent_booking_undone",
                           role=user["role"], call_id=call_id)
    conn.execute("UPDATE order_card SET appointment_date = NULL, appointment_time = NULL, booking_source = NULL "
                 "WHERE card_id = %s", (card["card_id"],))
    conn.execute("DELETE FROM appointment WHERE card_id = %s AND source = %s", (card["card_id"], SOURCE))
    conn.execute("UPDATE voice_slot SET status = 'free', taken_by_card = NULL WHERE slot_id = %s", (c["slot_id"],))
    conn.execute("UPDATE voice_call SET undone_by = %s, undone_at = %s, undo_reason = %s WHERE call_id = %s",
                 (user["user_id"], clock.get_now(conn), reason, call_id))
    _log(conn, "agent_booking_undone", c["mrn"], "undone_by_person", actor_type="person", actor=user["user_id"],
         entity=("order_card", card["card_id"]), details={"reason": reason, "call_id": call_id})
    _task(conn, c, "voice_rebook")
    return record(conn, call_id)


# ---------------------------------------------------------------------------
# V12: the call record, its audio, and what the screens read
# ---------------------------------------------------------------------------
def scripts() -> dict:
    from . import reference

    return reference.uc1v()["scripts"]


def _next_clip(c: dict, line_id: str) -> str | None:
    return scripts().get(c["script"] or "", {}).get("answers", {}).get(line_id)


def record(conn, call_id: int) -> dict:
    c = _call(conn, call_id)
    turns = conn.execute(
        """SELECT seq, who, line_id, approval_status, text, translation, label, reason_code, ai_failed, model, clip_id,
                  audio_id, at_demo
           FROM voice_turn WHERE call_id = %s ORDER BY seq""", (call_id,)).fetchall()
    p, card = _patient(conn, c["mrn"]), _card(conn, c["card_id"])
    waiting = c["status"] == "live" and turns and turns[-1]["who"] == "agent"
    clip_id = _next_clip(c, turns[-1]["line_id"]) if waiting else None
    clip = voice_script.clip(c["language"], clip_id, p, c["script"])
    h = conn.execute("SELECT handover_id, stage, callback_due_at FROM handover WHERE voice_call_id = %s",
                     (call_id,)).fetchone()
    return {**c, "caller": _config(conn).get("caller_name"), "patient": {k: p[k] for k in ("mrn", "full_name", "preferred_language")},
            "card": {k: card[k] for k in ("card_id", "item_code", "name", "due_date", "state", "appointment_date",
                                          "appointment_time", "booking_source")},
            "slot": _slot(conn, c["slot_id"]), "turns": turns, "handover": h,
            "patient_next": {"clip_id": clip_id, "has_audio": bool(clip["audio"]),
                             "chars": len(clip["text"])} if clip else None}


def _speak_live(conn, text: str, language: str, cfg: dict, voice: dict) -> tuple[bytes, str] | None:
    """A line with no recording (e.g. wording changed and not yet recorded) is still spoken, never shown as text
    only: each sentence is its own short Bulbul request (a long request drops clauses, R1) and the parts are joined
    with a short pause. None only if the voice service fails for a sentence."""
    if len(text) <= cfg["tts_live_max_chars"]:
        return ai.speak(conn, text, language, timeout=cfg["tts_timeout_seconds"], **voice)
    parts = []
    for sentence in [x for x in re.split(r"(?<=[।?.!])\s+", text.strip()) if x]:
        part = ai.speak(conn, sentence, language, timeout=cfg["tts_timeout_seconds"], **voice)
        if not part:
            return None
        parts.append(part[0])
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        for i, part in enumerate(parts):
            with wave.open(io.BytesIO(part)) as r:
                if i == 0:
                    w.setparams(r.getparams())
                else:
                    w.writeframes(bytes(1) * int(r.getframerate() * 0.25) * r.getsampwidth() * r.getnchannels())
                w.writeframes(r.readframes(r.getnframes()))
    return out.getvalue(), "audio/wav"


def turn_audio(conn, call_id: int, seq: int) -> tuple[bytes, str] | None:
    """The stored audio of a turn. An agent line is Bulbul reading the saved library text: recorded beforehand
    and listened back (scripts/voice_escalation_kit.py lines). Without a recording it is made now, sentence by
    sentence (_speak_live), so the agent is always heard."""
    c = _call(conn, call_id)
    t = conn.execute("SELECT * FROM voice_turn WHERE call_id = %s AND seq = %s", (call_id, seq)).fetchone()
    if t is None:
        raise NotFound("No such turn")
    if t["audio_id"]:
        row = conn.execute("SELECT data, mime FROM audio_blob WHERE audio_id = %s", (t["audio_id"],)).fetchone()
        return bytes(row["data"]), row["mime"]
    if t["who"] != "agent":
        return None
    cfg = _config(conn)
    voice = {"speaker": cfg["agent_speaker"], "model": cfg["tts_model"]}
    made = voice_script.agent_audio(c["language"], t["text"], **voice)
    if not made:
        made = _speak_live(conn, t["text"], c["language"], cfg, voice)
    if made:
        audio_id = conn.execute("INSERT INTO audio_blob (mime, data) VALUES (%s, %s) RETURNING audio_id",
                                (made[1], made[0])).fetchone()["audio_id"]
        conn.execute("UPDATE voice_turn SET audio_id = %s WHERE call_id = %s AND seq = %s AND audio_id IS NULL",
                     (audio_id, call_id, seq))
    return made


def clip_audio(conn, call_id: int, clip_id: str) -> tuple[bytes, str] | None:
    c = _call(conn, call_id)
    clip = voice_script.clip(c["language"], clip_id, _patient(conn, c["mrn"]), c["script"])
    return (clip["audio"], "audio/wav") if clip and clip["audio"] else None


def callback_state(conn, h: dict, now: datetime | None = None) -> dict | None:
    """The 2-hour target: met when a person has spoken to her, or a person closed the handover."""
    if not h.get("callback_due_at"):
        return None
    done = h["stage"] == "closed" or bool(h.get("called_on") or h.get("call_note")) or conn.execute(
        "SELECT 1 FROM call_log WHERE handover_id = %s AND who = 'patient' AND outcome NOT IN ('noans', 'wrong')",
        (h["handover_id"],)).fetchone() is not None
    minutes = int((h["callback_due_at"] - (now or clock.get_now(conn))).total_seconds() // 60)
    return {"due_at": h["callback_due_at"], "done": done, "minutes_left": minutes,
            "overdue": not done and minutes < 0}


def safety_handover(conn, h: dict | None) -> dict | None:
    """A handover row with what the screens need when it came from a voice call."""
    if not h or not h.get("voice_call_id"):
        return h
    t = conn.execute("SELECT audio_id FROM voice_turn WHERE call_id = %s AND seq = %s",
                     (h["voice_call_id"], h["voice_turn"])).fetchone()
    return {**h, "voice_audio": bool(t and t["audio_id"]), "callback": callback_state(conn, h)}


def overview(conn) -> dict:
    cfg = config(conn)
    if not cfg:
        return {"enabled": False}
    raised = {e["card_id"]: e for e in conn.execute("SELECT * FROM voice_escalation ORDER BY escalation_id").fetchall()}
    open_cards = {r["card_id"]: r for r in reminders(conn)
                  if not (cfg.get("scripted_only") and script_gap(conn, _patient(conn, r["mrn"]), r["card_id"]))}
    items = []
    for card_id in sorted({*raised, *open_cards}):
        card, e = _card(conn, card_id), raised.get(card_id)
        calls = conn.execute(
            """SELECT call_id, status, outcome, reason_code, started_at, ended_at, undone_at FROM voice_call
               WHERE card_id = %s ORDER BY call_id DESC""", (card_id,)).fetchall()
        items.append({
            "mrn": card["mrn"], "full_name": _patient(conn, card["mrn"])["full_name"], "calls": calls, "event": e,
            "card": {k: card[k] for k in ("card_id", "name", "due_date", "state", "appointment_date",
                                          "appointment_time", "booking_source")},
            "reminder_at": e["reminder_at"] if e else open_cards[card_id]["reminder_at"],
            "gate": gate(conn, e) if e and e["status"] in ("due", "held", "retry") else None,
            "on_hold": card["state"] == "Due" and messaging.open_handover(conn, card["mrn"]) is not None})
    declined = conn.execute(
        """SELECT v.call_id, v.mrn, p.full_name, c.name AS card_name, v.ended_at FROM voice_call v
           JOIN patient p USING (mrn) JOIN order_card c ON c.card_id = v.card_id
           WHERE v.outcome = 'declined' AND c.state = 'Due' ORDER BY v.call_id DESC""").fetchall()
    return {"enabled": True, "resettable": policy.settings(conn).get("scenario") == "uc1v", "settings": cfg, "now": clock.get_now(conn), "items": items, "declined": declined,
            "scripts": {k: v["title"] for k, v in scripts().items()},
            "slots": conn.execute("SELECT * FROM voice_slot ORDER BY slot_date, slot_time").fetchall()}
