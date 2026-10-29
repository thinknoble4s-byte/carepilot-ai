"""Voice Triage MVP-lite (CarePilot_Voice_Triage_MVP_Lite_v1.1): the health message, the automated call, the tier
and what each tier sets in motion. It replaces the Use Case 2c handover for health messages in the data sets that
switch it on (settings.voice_triage.enabled).

1. A voice note (or text) with the health flag YES: the fixed acknowledgement goes out and the call is due within
   2 minutes. Routine messages are held while the episode is open.
2. The call (simulated in the phone simulator, Bulbul audio when Sarvam is on): the opening, then up to 10 questions,
   one at a time. Each answer becomes one value (word lists, then a checked model answer); one re-ask; stop at the
   first danger sign; keypad 0 for a nurse. Two unanswered calls 5 minutes apart give Red (no answer).
3. Plain rules (triage_rules) give Red, Amber or Green, and the "Why this tier" lines. No model decides.
4. Red: the family message with a one-page PDF, a facts-only note for the ER Desk, Doctor on Duty and Senior
   Oncologist, and a coordinator task to confirm the emergency visit within 30 minutes. Only the ER doctor closes.
   Red (unconfirmed) is not sent by itself: a nurse calls back within 15 minutes; a person may send the package.
   Amber: the SBAR card to the Doctor on Duty and the coordinator, a 2-hour callback, the doctor's order buttons.
   Green: the doctor's own signed sentence (only when she reported nausea), a digest line, a follow-up tomorrow.
5. Next day: the follow-up call; the doctor confirms closure; the Pre-OPD brief shows the episode with sources.

Every sentence sent or shown is a template or a stored fact; no model writes one. SYNTHETIC DEMO only.
"""
import textwrap
from datetime import datetime, timedelta

from psycopg.types.json import Jsonb

from . import ai, audit, clock, messaging, policy, tasks
from . import triage_rules as R
from .agent import set_next_action
from .errors import Conflict, Invalid, NotFound
from .reference import USERS_BY_ID

AGENT = "CarePilot triage agent"
RULES = "Triage rules (DEMO_LITE, draft)"
TIERS = {"TIER_1_RED": "Red", "TIER_2_AMBER": "Amber", "TIER_3_GREEN": "Green"}
ORDERS = {"physio": ("Physiotherapy referral", "visit", "Physiotherapy", "PHYSIO", 7),
          "bloods": ("Blood tests (brought forward)", "test", "Laboratory", "BLOODFWD", 3),
          "none": None}
ED_OUTCOMES = ("REACHED", "NOT_YET", "REFUSED", "NOT_REACHABLE")
FOLLOWUP_ANSWERS = ("BETTER", "SAME", "WORSE")
REASON_LINES = {
    "CONFIRMED": {"English": "{name} Amma gave answers to our call that are warning signs.",
                  "Tamil": "{name_ta} அம்மா எங்கள் அழைப்பில் கொடுத்த பதில்கள் எச்சரிக்கை அறிகுறிகளைக் காட்டுகின்றன."},
    "NO_ANSWER": {"English": "{name} Amma sent us a message saying she is unwell. We called twice and could not "
                             "reach her.",
                  "Tamil": "{name_ta} அம்மா தனக்கு உடல்நிலை சரியில்லை என்று எங்களுக்குச் செய்தி அனுப்பினார். இரண்டு முறை "
                           "அழைத்தும் அவரைத் தொடர்பு கொள்ள முடியவில்லை."},
    "UNCONFIRMED": {"English": "{name} Amma's answers could not be confirmed as safe.",
                    "Tamil": "{name_ta} அம்மாவின் பதில்கள் அவர் பாதுகாப்பாக இருக்கிறார் என்பதை உறுதிப்படுத்த முடியவில்லை."},
}
ALERT_TYPE = {"CONFIRMED": "Warning signs in her answers",
              "NO_ANSWER": "Reported symptoms, then could not be reached",
              "UNCONFIRMED": "Answers could not be confirmed as safe"}


# ---------------------------------------------------------------------------------------------------------------
# Settings and loading
# ---------------------------------------------------------------------------------------------------------------
def config(conn) -> dict:
    t = policy.settings(conn).get("voice_triage") or {}
    return {"enabled": bool(t.get("enabled")), "doctor_on_duty": t.get("doctor_on_duty"),
            "senior_oncologist": t.get("senior_oncologist"), "er_doctor": t.get("er_doctor"),
            "coordinator": t.get("coordinator"), "call_within_minutes": t.get("call_within_minutes", 2),
            "retry_minutes": t.get("retry_minutes", 5), "confirm_within_minutes": t.get("confirm_within_minutes", 30),
            "not_yet_again_minutes": t.get("not_yet_again_minutes", 15), "nurse_minutes": t.get("nurse_minutes", 15),
            "amber_callback_minutes": t.get("amber_callback_minutes", 120)}


def enabled(conn) -> bool:
    return config(conn)["enabled"]


def history(conn, mrn: str) -> dict | None:
    row = conn.execute("SELECT data FROM triage_history WHERE mrn = %s", (mrn,)).fetchone()
    return row["data"] if row else None


def _patient(conn, mrn: str) -> dict:
    return conn.execute("SELECT * FROM patient WHERE mrn = %s", (mrn,)).fetchone()


def _load(conn, episode_id: int) -> dict:
    ep = conn.execute("SELECT * FROM triage_episode WHERE episode_id = %s FOR UPDATE", (episode_id,)).fetchone()
    if ep is None:
        raise NotFound(f"Triage episode {episode_id} not found")
    return ep


def _update(conn, ep: dict, **fields) -> dict:
    sets = ", ".join(f"{k} = %({k})s" for k in fields)
    values = {k: Jsonb(v) if isinstance(v, (dict, list)) else v for k, v in fields.items()}
    return conn.execute(f"UPDATE triage_episode SET {sets} WHERE episode_id = %(id)s RETURNING *",
                        {**values, "id": ep["episode_id"]}).fetchone()


def _lang(patient: dict) -> str:
    """The call speaks Tamil (the lite profile); any other language hears the English lines."""
    return "Tamil" if patient["preferred_language"] == "Tamil" else "English"


def _hhmm(at: datetime | None) -> str:
    return at.strftime("%H:%M") if at else ""


def _when(at: datetime) -> str:
    return f"{at.day} {at:%b %Y %H:%M}"


def _name(user_id: str | None) -> str:
    return USERS_BY_ID.get(user_id or "", {}).get("name", user_id or "")


def _audit(conn, ep: dict, action: str, *, actor_type="system", actor=RULES, **kw) -> None:
    audit.append(conn, actor_type=actor_type, actor_name=actor, action=action, entity_type="triage_episode",
                 entity_id=str(ep["episode_id"]), mrn=ep["mrn"], **kw)


# ---------------------------------------------------------------------------------------------------------------
# 1. The health message opens an episode
# ---------------------------------------------------------------------------------------------------------------
def open_episode(conn, mrn: str, *, inbound: dict, words: str | None, translation: str | None,
                 from_role: str = "patient") -> tuple[dict, bool] | None:
    """Flag YES with Voice Triage on: the acknowledgement, then a call due within 2 minutes. None when the patient
    has no triage history in the record (then the usual handover path runs)."""
    existing = messaging.open_triage(conn, mrn)
    if existing:
        ep = _update(conn, existing, patient_words="\n".join(x for x in (existing["patient_words"], words) if x),
                     translation="\n".join(x for x in (existing["translation"], translation) if x))
        _audit(conn, ep, "triage_message_added", actor_type="system", actor="WhatsApp gateway",
               details={"inbound_id": inbound["inbound_id"]})
        return ep, False
    if history(conn, mrn) is None:
        return None
    cfg, now = config(conn), clock.get_now(conn)
    ack = messaging.send_message(conn, mrn=mrn, template_id="triage_ack", actor_type="agent", actor=AGENT,
                                 reason_code="health_flag_voice_triage", recipient=from_role)
    ep = conn.execute(
        """
        INSERT INTO triage_episode (mrn, inbound_id, patient_words, translation, opened_at, call_due_at, answers)
        VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *
        """,
        (mrn, inbound["inbound_id"], words, translation, now, now + timedelta(minutes=cfg["call_within_minutes"]),
         Jsonb(R.blank())),
    ).fetchone()
    conn.execute("UPDATE outbox_message SET triage_episode_id = %s WHERE message_id = %s",
                 (ep["episode_id"], ack["message_id"]))
    _audit(conn, ep, "triage_opened", actor_type="agent", actor=AGENT, reason_code="health_flag_voice_triage",
           trigger_event=f"inbound_message:{inbound['inbound_id']}", outcome="call_due",
           decision_inputs={"health_flag": "yes", "voice_triage": True},
           details={"call_due_at": _hhmm(ep["call_due_at"]), "routine_messages": "held while the episode is open"})
    set_next_action(conn, mrn, f"Automated triage call due by {_hhmm(ep['call_due_at'])}; routine messages held")
    return ep, True


# ---------------------------------------------------------------------------------------------------------------
# 2. The call
# ---------------------------------------------------------------------------------------------------------------
def _turn(turns: list, conn, who: str, id_: str, ta: str = "", en: str = "", **kw) -> None:
    turns.append({"i": len(turns), "at": clock.get_now(conn).strftime("%H:%M"), "who": who, "id": id_, "ta": ta,
                  "en": en, **kw})


def _ask(turns: list, conn, q: R.Question, h: dict, repeat: bool = False) -> None:
    if repeat:
        m = R.messages(h)["repeat"]
        _turn(turns, conn, "agent", q.id, f"{m[0]} {q.ta}", f"{m[1]} {q.en}", repeat=True, history=q.history)
    else:
        _turn(turns, conn, "agent", q.id, q.ta, q.en, history=q.history)


def pickup(conn, episode_id: int, user: dict) -> dict:
    """The patient picks up: the opening line and the first question."""
    ep = _load(conn, episode_id)
    if ep["call_status"] != "due" or ep["status"] != "open":
        raise Conflict(f"No call is ringing for this episode (call {ep['call_status']})")
    h = history(conn, ep["mrn"])
    turns = list(ep["turns"])
    _turn(turns, conn, "system", "pickup", en=f"Call {ep['attempts'] + 1} answered")
    m = R.messages(h)["open"]
    _turn(turns, conn, "agent", "open", m[0], m[1], template_id="TPL_CALL_OPEN_LITE_V1")
    q = R.next_question(h, ep["answers"], None)
    _ask(turns, conn, q, h)
    ep = _update(conn, ep, call_status="in_call", attempts=ep["attempts"] + 1, turns=turns, current_q=q.id,
                 reasked=False)
    _audit(conn, ep, "triage_call_answered", actor_type="agent", actor=AGENT, outcome="in_call",
           details={"attempt": ep["attempts"], "demo_operator": user["user_id"]})
    return state(conn, episode_id)


def _read(conn, ep: dict, q: R.Question, language: str, *, text=None, value=None, audio=None, mime=None,
          silence=False, via: str | None = None) -> dict:
    """One answer -> {value, ta, en, how}. Raises ai.TriageAIFailure (the call then ends as a system fault)."""
    if silence:
        return {"value": R.UNKNOWN, "ta": "", "en": "(silence)", "how": "no speech"}
    if value is not None:
        return {"value": R.check(q.kind, value), "ta": "", "en": str(value), "how": "on-screen answer button"}
    audio_id = None
    if audio is not None:
        heard = ai.hear(conn, language=language, audio=audio, mime=mime or "audio/wav")
        audio_id = conn.execute("INSERT INTO audio_blob (mime, data) VALUES (%s, %s) RETURNING audio_id",
                                ((mime or "audio/wav").split(";")[0], audio)).fetchone()["audio_id"]
        said, english, how = heard["transcript"], heard["translation"], f"spoken; Saaras ({heard['model']})"
    else:
        said, english, how = text.strip(), None, via or "typed"
    out = {"ta": said, "en": english or "", "audio_id": audio_id}
    v = R.parse(q.kind, said)
    if v is not None:
        return {**out, "value": R.check(q.kind, v), "how": f"{how}; word list"}
    if english is None and language != "English":
        english = ai.translate(conn, said, language)
        out["en"] = english
    v = R.parse(q.kind, english or "")
    if v is not None:
        return {**out, "value": R.check(q.kind, v), "how": f"{how}; word list (English)"}
    reply, model = ai.answer_value_reply(conn, kind=q.kind, question_en=q.en, transcript=said,
                                         translation=english or said)
    return {**out, "value": R.model_value(q.kind, reply), "how": f"{how}; model {model}, value checked",
            "model_reply": reply[:200]}


def answer(conn, episode_id: int, user: dict, *, text: str | None = None, value=None, key: str | None = None,
           audio: bytes | None = None, mime: str | None = None, silence: bool = False, via: str | None = None) -> dict:
    ep = _load(conn, episode_id)
    if ep["call_status"] != "in_call" or not ep["current_q"]:
        raise Conflict("The call is not in progress")
    if key is not None:
        if key != "0":
            raise Invalid("Only the key 0 (nurse) does anything on this call")
        turns = list(ep["turns"])
        _turn(turns, conn, "patient", "dtmf", en="(pressed 0 for a nurse)", value="0", how="keypad")
        ep = _update(conn, ep, turns=turns, nurse_pressed=True)
        return _finish(conn, ep, nurse=True)
    if not silence and value is None and audio is None and not (text or "").strip():
        raise Invalid("An answer needs words, a value, a recording or the key 0")
    h, patient = history(conn, ep["mrn"]), _patient(conn, ep["mrn"])
    q = R.question(h, ep["current_q"])
    turns = list(ep["turns"])
    try:
        heard = _read(conn, ep, q, _lang(patient), text=text, value=value, audio=audio, mime=mime, silence=silence,
                      via=via)
    except ai.TriageAIFailure as e:
        _turn(turns, conn, "system", "fault", en=f"Sarvam could not read the answer: {str(e)[:160]}")
        ep = _update(conn, ep, turns=turns, fault=True)
        return _finish(conn, ep, fault=True)
    _turn(turns, conn, "patient", q.id, heard.get("ta", ""), heard.get("en", ""), value=heard["value"],
          how=heard["how"], **({"audio_id": heard["audio_id"]} if heard.get("audio_id") else {}))
    answers = dict(ep["answers"])
    if heard["value"] == R.UNKNOWN and not ep["reasked"]:  # unclear: ask once more
        _ask(turns, conn, q, h, repeat=True)
        _update(conn, ep, turns=turns, reasked=True)
        return state(conn, episode_id)
    R.store(answers, q, heard["value"])
    if R.red_codes(answers):  # stop at the first danger sign
        ep = _update(conn, ep, turns=turns, answers=answers, reasked=False, stopped_early=True)
        return _finish(conn, ep)
    nq = R.next_question(h, answers, q.id)
    if nq is None:
        ep = _update(conn, ep, turns=turns, answers=answers, reasked=False)
        return _finish(conn, ep)
    _ask(turns, conn, nq, h)
    _update(conn, ep, turns=turns, answers=answers, reasked=False, current_q=nq.id)
    return state(conn, episode_id)


def no_answer(conn, episode_id: int, user: dict) -> dict:
    """The call rings out. After the first, the next call goes 5 minutes later (the demo clock moves); after the
    second, Red (no answer)."""
    ep = _load(conn, episode_id)
    if ep["call_status"] != "due" or ep["status"] != "open":
        raise Conflict(f"No call is ringing for this episode (call {ep['call_status']})")
    cfg = config(conn)
    attempts = ep["attempts"] + 1
    turns = list(ep["turns"])
    _turn(turns, conn, "system", "no_answer", en=f"Call {attempts} rang out, not answered")
    ep = _update(conn, ep, attempts=attempts, turns=turns)
    _audit(conn, ep, "triage_call_not_answered", actor_type="agent", actor=AGENT, outcome="not_answered",
           details={"attempt": attempts, "demo_operator": user["user_id"]})
    if attempts >= 2:
        return _finish(conn, ep, no_answer=True)
    later = clock.get_now(conn) + timedelta(minutes=cfg["retry_minutes"])
    clock.set_forward(conn, later.date(), user["user_id"], later.strftime("%H:%M"))
    _update(conn, ep, call_due_at=later)
    set_next_action(conn, ep["mrn"], f"Second automated call at {_hhmm(later)} (first call not answered)")
    return state(conn, episode_id)


def play_as(conn, episode_id: int, user: dict, scenario: str) -> dict:
    """Demo control "Play as": the scripted Tamil answers of the guide's scenario go through the same parsing and
    rules. On a call that has finished, the same voice note is played again as a new episode (demo replay)."""
    if scenario not in R.SCENARIOS:
        raise Invalid(f"Unknown scenario; choose one of: {', '.join(R.SCENARIOS)}")
    ep = _load(conn, episode_id)
    if ep["call_status"] in ("done", "no_answer"):
        ep = _replay(conn, ep, user, scenario)
    if ep["call_status"] == "due":
        pickup(conn, ep["episode_id"], user)
    script = R.SCENARIOS[scenario]
    for _ in range(30):  # every question at most twice
        ep = _load(conn, ep["episode_id"])
        if ep["call_status"] != "in_call":
            break
        said = script.get(ep["current_q"])
        if said == R.PRESS_0:
            answer(conn, ep["episode_id"], user, key="0")
        elif said is None:
            answer(conn, ep["episode_id"], user, silence=True)
        else:
            answer(conn, ep["episode_id"], user, text=said, via=f"Play as {scenario} (scripted Tamil answer)")
    return state(conn, ep["episode_id"])


def _replay(conn, ep: dict, user: dict, scenario: str) -> dict:
    now = clock.get_now(conn)
    other = messaging.open_triage(conn, ep["mrn"])
    if other and other["episode_id"] != ep["episode_id"]:
        raise Conflict(f"Another triage episode (#{other['episode_id']}) is open for this patient")
    if ep["status"] == "open":
        _update(conn, ep, status="closed", closed_by=user["user_id"], closed_at=now,
                close_note=f"Demo replay: the same voice note played again as {scenario}")
        _audit(conn, ep, "triage_closed_for_demo_replay", actor_type="person", actor=user["user_id"],
               details={"scenario": scenario})
    new = conn.execute(
        """
        INSERT INTO triage_episode (mrn, inbound_id, patient_words, translation, opened_at, call_due_at, answers)
        VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *
        """,
        (ep["mrn"], ep["inbound_id"], ep["patient_words"], ep["translation"], now, now, Jsonb(R.blank())),
    ).fetchone()
    _audit(conn, new, "triage_opened", actor_type="person", actor=user["user_id"], reason_code="demo_replay",
           details={"replays_episode": ep["episode_id"], "scenario": scenario})
    return new


# ---------------------------------------------------------------------------------------------------------------
# 3. The result, and 4. what each tier sets in motion
# ---------------------------------------------------------------------------------------------------------------
def _finish(conn, ep: dict, *, nurse=False, fault=False, no_answer=False) -> dict:
    h, now = history(conn, ep["mrn"]), clock.get_now(conn)
    answers = dict(ep["answers"])
    if fault:  # fail closed: a danger question she never answered counts as unclear
        for q in R.questions(h):
            if q.danger and not R.skipped(q, answers) and answers.get(q.field, R.NOT_ASKED) == R.NOT_ASKED \
                    and not (q.kind == "temperature" and answers["thermometer_used"] != R.NOT_ASKED):
                R.store(answers, q, R.UNKNOWN)
    result = R.triage(answers, h, now.date(), nurse=nurse, fault=fault or ep["fault"], no_answer=no_answer)
    result.update(stopped_after_danger_sign=ep["stopped_early"], why=R.why_lines(result, answers, h))
    turns = list(ep["turns"])
    if not no_answer:
        ta, en = R.closing(result, h)
        _turn(turns, conn, "agent", "closing", ta, en, template_id=result["patient_template_id"])
        _turn(turns, conn, "system", "hangup", en="Call ended")
    ep = _update(conn, ep, call_status="no_answer" if no_answer else "done", current_q=None, turns=turns,
                 answers=answers, result=result, tier=result["tier"], red_kind=result.get("red_kind"), result_at=now)
    _audit(conn, ep, "triage_result", reason_code=result["tier"], outcome=result["action_code"],
           rule=",".join(result["reason_codes"]) or None, policy_version="DEMO_LITE (DRAFT_UNSIGNED)",
           decision_inputs={"answers": answers, "nurse": nurse, "fault": fault, "no_answer": no_answer},
           details={"why": result["why"], "red_kind": result.get("red_kind"),
                    "stopped_after_danger_sign": ep["stopped_early"]})
    _act(conn, ep, result)
    return state(conn, ep["episode_id"])


def _act(conn, ep: dict, result: dict) -> None:
    cfg, now = config(conn), clock.get_now(conn)
    tier = result["tier"]
    if tier == "TIER_1_RED":
        _audit(conn, ep, "er_desk_alert_created", outcome=result["red_kind"],
               details={"to": ["ER Desk", "Doctor on Duty", "Senior Oncologist (banner)"]})
        if result["red_kind"] in ("CONFIRMED", "NO_ANSWER"):
            send_package(conn, ep["episode_id"], None)
        else:
            due = now + timedelta(minutes=cfg["nurse_minutes"])
            _update(conn, ep, callback_due_at=due)
            set_next_action(conn, ep["mrn"], f"Red (unconfirmed): nurse to call back by {_hhmm(due)}; "
                                             "no family message sent")
    elif tier == "TIER_2_AMBER":
        minutes = cfg["nurse_minutes"] if result["action_code"] == "NURSE_CALLBACK_15_MIN" else cfg["amber_callback_minutes"]
        due = now + timedelta(minutes=minutes)
        _update(conn, ep, callback_due_at=due, followup_due_on=now.date() + timedelta(days=1))
        _audit(conn, ep, "sbar_card_created", outcome=result["action_code"],
               details={"to": [_name(cfg["doctor_on_duty"]), _name(cfg["coordinator"])],
                        "callback_due": _hhmm(due)})
        who = "Nurse" if minutes == cfg["nurse_minutes"] else f"{_name(cfg['doctor_on_duty']) or 'Doctor on duty'}"
        set_next_action(conn, ep["mrn"], f"Amber: {who} to call back by {_hhmm(due)}")
    else:
        _update(conn, ep, followup_due_on=now.date() + timedelta(days=1))
        _audit(conn, ep, "green_digest_line", outcome="GREEN_COACH_AND_FOLLOWUP_24H",
               details={"doctor_sentence_repeated": result["repeat_advice"],
                        "instruction_id": result.get("repeat_instruction_id")})
        set_next_action(conn, ep["mrn"], "Green: follow-up call tomorrow")


def send_package(conn, episode_id: int, user: dict | None) -> dict:
    """The Red package (Part 5): family message + PDF, doctor note, coordinator task, audit rows. Automatic for
    CONFIRMED and NO ANSWER; for UNCONFIRMED only when a person presses "Send emergency package"."""
    ep = _load(conn, episode_id)
    if ep["tier"] != "TIER_1_RED":
        raise Conflict("The emergency package is only for a Red case")
    if ep["package_sent_at"] is not None:
        raise Conflict("The emergency package has already been sent")
    if user is not None and ep["red_kind"] != "UNCONFIRMED":
        raise Conflict("This package goes out by itself")
    if ep["status"] != "open":
        raise Conflict("The episode is closed")
    cfg, now = config(conn), clock.get_now(conn)
    h, patient = history(conn, ep["mrn"]), _patient(conn, ep["mrn"])
    kind = ep["red_kind"]
    actor_type, actor = ("person", user["user_id"]) if user else ("agent", AGENT)
    sent_at = _when(now)
    pdf = summary_pdf(ep, h, patient, sent_at)
    doc = conn.execute(
        "INSERT INTO triage_document (episode_id, file_name, mime, data, created_at) "
        "VALUES (%s, 'emergency_summary.pdf', 'application/pdf', %s, %s) RETURNING doc_id",
        (ep["episode_id"], pdf, now)).fetchone()
    reason = {lang: line.format(name=h["name_en"], name_ta=h["name_ta"]) for lang, line in REASON_LINES[kind].items()}
    msg = messaging.send_message(conn, mrn=ep["mrn"], template_id="triage_red_caregiver", actor_type=actor_type,
                                 actor=actor, reason_code=f"red_{kind.lower()}", recipient="caregiver",
                                 extra={"reason": reason})
    conn.execute("UPDATE outbox_message SET attachment_doc_id = %s, triage_episode_id = %s WHERE message_id = %s",
                 (doc["doc_id"], ep["episode_id"], msg["message_id"]))
    relation = f", her {h['caregiver_relation']}" if h.get("caregiver_relation") else ""
    family = f"the family ({patient['caregiver_name']}{relation})" if patient["caregiver_name"] else "the family"
    why = "; ".join(R.RED_PLAIN[c] for c in ep["result"]["reason_codes"] if c in R.RED_PLAIN)
    told = (f"At {sent_at} {family} was sent a message telling them to take her to the nearest emergency department, "
            "with an emergency summary PDF for the receiving doctor." if msg["status"] == "sent" else
            f"The family message could not be sent at {sent_at} ({(msg['blocked_reason'] or '').replace('_', ' ')}).")
    mins = cfg["confirm_within_minutes"]
    note = (f"RED alert: {h.get('title', patient['full_name'])} ({ep['mrn']}). Why: {why}. {told} Not yet known: whether "
            "she has reached an emergency department, and which one. The coordinator has been asked to confirm within "
            f"{mins} minutes. Alert type: {kind}. Source: call audio and the PDF are linked on this card.")
    caller = patient["caregiver_name"] or "the family"
    first = h["name_en"]
    task = tasks.create_task(
        conn, mrn=ep["mrn"], task_type="confirm_ed_visit", owner_name=_name(cfg["coordinator"]) or None,
        actor_type="system", actor="system", dedupe_key=f"triage:{ep['episode_id']}:confirm_ed_visit",
        reason=(f"Call {caller} within {mins} minutes. Ask: has {first} reached an emergency department, and which "
                "one? Record the answer: REACHED (with hospital name), NOT YET, REFUSED, or NOT REACHABLE. If the "
                f"caregiver cannot be reached in 2 attempts, tell the Doctor on Duty. Message and PDF were sent at "
                f"{sent_at}."))
    conn.execute("UPDATE coordinator_task SET due_at_demo = %s, due_date = %s, triage_episode_id = %s "
                 "WHERE task_id = %s", (now + timedelta(minutes=mins), now.date(), ep["episode_id"], task["task_id"]))
    ep = _update(conn, ep, package_sent_at=now, package_sent_by=actor, doctor_note=note)
    _audit(conn, ep, "doctor_note_created", details={"to": ["Doctor on Duty", "ER Desk", "Senior Oncologist"],
                                                     "note": note})
    _audit(conn, ep, "emergency_package_sent", actor_type=actor_type, actor=actor, outcome=msg["status"],
           template_id="TPL_WA_RED_CAREGIVER_V1", reason_code=f"red_{kind.lower()}",
           details={"message_id": msg["message_id"], "pdf_doc_id": doc["doc_id"], "task_id": task["task_id"],
                    "due_minutes": mins})
    set_next_action(conn, ep["mrn"], f"Red: coordinator to confirm the emergency visit by "
                                     f"{_hhmm(now + timedelta(minutes=mins))}; ER Desk alerted")
    return state(conn, episode_id)


# ---------------------------------------------------------------------------------------------------------------
# The emergency summary (one page, for a doctor at any hospital). Facts only: no diagnosis of this episode, no
# cause, grade, recommendation, ambulance or map link.
# ---------------------------------------------------------------------------------------------------------------
NO_PLAIN = {"slurred_or_confused": "speech slurred or confusion", "arm_red_or_hot": "left arm red or hot",
            "bangles_tighter": "bangles tighter than before"}


def _latin(s: str) -> str:
    s = str(s).replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').replace("–", "-")
    return s.encode("latin-1", "replace").decode("latin-1")


def summary_lines(ep: dict, h: dict, patient: dict, sent_at: str) -> list[tuple[str, str]]:
    """(style, text) lines: title, note, head, body, small."""
    result, a = ep["result"], ep["answers"]
    kind = ep["red_kind"]
    out = [("title", "EMERGENCY SUMMARY FOR THE TREATING DOCTOR"),
           ("small", f"SYNTHETIC DEMO: not a real patient. Prepared {sent_at} for the emergency doctor who receives "
                     "this patient, possibly at a different hospital. Facts come from the treating hospital's record "
                     "and the patient's own answers."),
           ("head", "1. Why she is being brought in"),
           ("body", f"Alert type: {ALERT_TYPE[kind]}"),
           *(("body", f"- {R.RED_PLAIN[c]}") for c in result["reason_codes"] if c in R.RED_PLAIN),
           ("head", "2. What she reported today")]
    if kind == "NO_ANSWER":
        if ep["translation"]:
            out.append(("body", f'Her voice message (machine translation): "{ep["translation"]}"'))
        out.append(("body", "Two automated calls were not answered (5 minutes apart). No answers were recorded."))
    else:
        facts = R.facts_text(a)
        out.append(("body", facts or "No answers were recorded."))
        said_no = [t for f, t in NO_PLAIN.items() if a.get(f) == R.NO]
        if said_no:
            out.append(("body", "Asked and answered No: " + ", ".join(said_no) + "."))
        if ep["stopped_early"]:
            out.append(("body", "The call stopped at the first warning sign, so later questions were not asked."))
    c = h["clinical"]
    out += [("head", "3. Patient"),
            ("body", f"Name, age, sex: {h.get('title', patient['full_name'])}, {h.get('age', '')}, {h.get('sex', 'female')}"),
            ("body", f"Hospital ID: {ep['mrn']} at {c['treating_hospital']}"),
            ("body", f"Language: {patient['preferred_language']}. The family speaks for her if she cannot."),
            ("body", f"Family contact: {patient['caregiver_name'] or 'not recorded'}"
                     + (f", her {h['caregiver_relation']}" if patient["caregiver_name"] and h.get("caregiver_relation") else "")),
            ("head", "4. Cancer history (from the treating hospital's record)"),
            ("body", f"Diagnosis: {c['diagnosis']}"),
            ("body", f"Treatment done: {'; '.join(c['treatment_done'])}"),
            ("body", f"Active treatment finished: {c['active_treatment_completed']}"),
            ("body", f"Now: {c['current_phase']}"),
            ("head", "5. Current medicines (from her care plan)"),
            *(("body", f"- {m['name']}: {m['schedule']}") for m in h.get("medicines", [])),
            ("body", "- No as-needed (SOS) medicines recorded."),
            ("head", "6. Her last 30 days, from the record")]
    for e in sorted(h.get("events", []), key=lambda e: e["date"]):
        out.append(("body", f"{e['date']}: {e['reported']} Instruction: {e.get('instruction') or 'none recorded.'} "
                            f"{e.get('outcome', '')}".strip()))
    out.append(("body", f"Last recorded left arm status: {h['arm_baseline']['status']} (recorded "
                        f"{h['arm_baseline']['date']}). Last recorded headache score: {h['last_headache']['score']} "
                        f"out of 10 ({h['last_headache']['date']})."))
    if h.get("signed_note"):
        n = h["signed_note"]
        out += [("head", "7. Note from her treating oncologist (word for word)"), ("body", f'"{n["text"]}"'),
                ("small", f"Signed by {n['signed_by']}, {n['date']}.")]
    out += [("head", "8. Not recorded by CarePilot. Please ask the patient or family."),
            ("body", ", ".join(h.get("not_recorded", ["Allergies", "Other conditions", "Other medicines",
                                                      "Blood group"])) + "."),
            ("head", "9. Contact the treating team"),
            ("body", f"{c['treating_hospital']} care team: {c['care_team_phone']}. {c['treating_oncologist']}. "
                     "Please call to share findings or ask for her records.")]
    return out


def summary_pdf(ep: dict, h: dict, patient: dict, sent_at: str) -> bytes:
    styles = {"title": ("F2", 13, 20), "head": ("F2", 10, 15), "body": ("F1", 9, 11.5), "small": ("F1", 7.5, 9.5)}
    width = {"title": 70, "head": 95, "body": 108, "small": 128}
    esc = lambda s: _latin(s).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    ops = ["q 0.92 g BT /F2 60 Tf 0.819 0.574 -0.574 0.819 120 260 Tm (SYNTHETIC DEMO) Tj ET Q",
           "0.69 0.23 0.18 rg 50 772 495 30 re f"]
    y = 782
    for style, text in summary_lines(ep, h, patient, sent_at):
        font, size, lead = styles[style]
        for line in textwrap.wrap(text, width[style]) or [""]:
            colour = "1 g" if style == "title" else ("0.12 0.22 0.39 rg" if style == "head" else "0 g")
            ops.append(f"BT {colour} /{font} {size} Tf 56 {y:.1f} Td ({esc(line)}) Tj ET")
            y -= lead
        if style == "title":
            y -= 8
    ops += ["BT 0.33 g /F1 7.5 Tf 50 40 Td (Generated by CarePilot's automated assistant from stored hospital records "
            "and the patient's own answers.) Tj ET",
            "BT 0.33 g /F1 7.5 Tf 50 30 Td (It lists facts and the rules that fired. It does not diagnose or advise.) "
            "Tj ET"]
    body = "\n".join(ops)
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> "
            "/Contents 6 0 R >>",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
            f"<< /Length {len(body.encode('latin-1'))} >>\nstream\n{body}\nendstream"]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info << /Title (Emergency summary - SYNTHETIC) >> >>\n" \
           f"startxref\n{xref}\n%%EOF\n".encode()
    return out


# ---------------------------------------------------------------------------------------------------------------
# What people do
# ---------------------------------------------------------------------------------------------------------------
def _open(ep: dict) -> None:
    if ep["status"] != "open":
        raise Conflict("The episode is closed")


def acknowledge(conn, episode_id: int, user: dict) -> dict:
    """ER Desk: acknowledge a Red alert. Coordinator: acknowledge an Amber card."""
    ep = _load(conn, episode_id)
    _open(ep)
    now = clock.get_now(conn)
    if user["role"] == "er_desk":
        if ep["tier"] != "TIER_1_RED":
            raise Conflict("The ER Desk sees Red alerts only")
        if ep["er_ack_by"]:
            raise Conflict(f"Already acknowledged by {_name(ep['er_ack_by'])}")
        ep = _update(conn, ep, er_ack_by=user["user_id"], er_ack_at=now)
    else:
        if ep["coord_ack_by"]:
            raise Conflict(f"Already acknowledged by {_name(ep['coord_ack_by'])}")
        ep = _update(conn, ep, coord_ack_by=user["user_id"], coord_ack_at=now)
    _audit(conn, ep, "triage_acknowledged", actor_type="person", actor=user["user_id"], details={"role": user["role"]})
    return state(conn, episode_id)


def nurse_callback(conn, episode_id: int, user: dict, note: str | None) -> dict:
    ep = _load(conn, episode_id)
    _open(ep)
    if not (note or "").strip():
        raise Invalid("Write what the nurse found on the call")
    if ep["result"] is None or ep["result"]["action_code"] != "NURSE_CALLBACK_15_MIN":
        raise Conflict("No nurse callback was asked for in this episode")
    now = clock.get_now(conn)
    ep = _update(conn, ep, nurse_callback={"by": user["user_id"], "at": now.isoformat(timespec="minutes"),
                                           "note": note.strip()})
    _audit(conn, ep, "nurse_callback_recorded", actor_type="person", actor=user["user_id"],
           details={"note": note.strip(), "within_minutes": int((now - ep["result_at"]).total_seconds() // 60)})
    return state(conn, episode_id)


def ed_call(conn, episode_id: int, user: dict, outcome: str, hospital: str | None = None,
            note: str | None = None) -> dict:
    """The coordinator's call to the family after a Red package (Part 5.6). It never closes the case."""
    ep = _load(conn, episode_id)
    _open(ep)
    if outcome not in ED_OUTCOMES:
        raise Invalid("outcome must be one of: " + ", ".join(ED_OUTCOMES))
    if outcome == "REACHED" and not (hospital or "").strip():
        raise Invalid("REACHED needs the hospital name")
    task = conn.execute("SELECT * FROM coordinator_task WHERE triage_episode_id = %s AND task_type = 'confirm_ed_visit' "
                        "AND status = 'open' FOR UPDATE", (episode_id,)).fetchone()
    if task is None:
        raise Conflict("There is no open 'confirm the emergency visit' task for this episode")
    cfg, now = config(conn), clock.get_now(conn)
    calls = list(ep["ed_calls"]) + [{"at": now.isoformat(timespec="minutes"), "by": user["user_id"],
                                     "outcome": outcome, "hospital": (hospital or "").strip() or None,
                                     "note": (note or "").strip() or None}]
    unanswered = sum(1 for c in calls if c["outcome"] in ("NOT_YET", "NOT_REACHABLE"))
    tell_doctor = outcome == "REFUSED" or (outcome in ("NOT_YET", "NOT_REACHABLE") and unanswered >= 2)
    fields = {"ed_calls": calls, "ed_outcome": outcome}
    if tell_doctor and ep["doctor_told_at"] is None:
        fields["doctor_told_at"] = now
    ep = _update(conn, ep, **fields)
    if outcome in ("REACHED", "REFUSED") or tell_doctor:
        line = {"REACHED": f"Reached: at {(hospital or '').strip()}", "REFUSED": "Family refused; Doctor on Duty told",
                "NOT_YET": "Not yet there after 2 calls; Doctor on Duty told",
                "NOT_REACHABLE": "Family not reachable in 2 attempts; Doctor on Duty told"}[outcome]
        tasks.complete_task(conn, task["task_id"], user["user_id"], line)
        set_next_action(conn, ep["mrn"], f"Red: {line}. Only the ER doctor closes the case")
    else:
        again = now + timedelta(minutes=cfg["not_yet_again_minutes"])
        conn.execute("UPDATE coordinator_task SET due_at_demo = %s WHERE task_id = %s", (again, task["task_id"]))
        set_next_action(conn, ep["mrn"], f"Red: call the family again by {_hhmm(again)}")
    _audit(conn, ep, "ed_visit_call_logged", actor_type="person", actor=user["user_id"], outcome=outcome,
           details={"hospital": (hospital or "").strip() or None, "note": note, "attempt": len(calls),
                    "doctor_on_duty_told": tell_doctor,
                    "external_care_episode": bool(hospital) and outcome == "REACHED"})
    return state(conn, episode_id)


def er_close(conn, episode_id: int, user: dict, note: str | None) -> dict:
    """Only the ER doctor closes a Red case."""
    ep = _load(conn, episode_id)
    _open(ep)
    if ep["tier"] != "TIER_1_RED":
        raise Conflict("Only a Red case is closed at the ER Desk; the doctor confirms closure of the others")
    if user["user_id"] != config(conn)["er_doctor"]:
        raise Conflict("Only the ER doctor closes a Red case")
    return _close(conn, ep, user, note, "red_closed_by_er_doctor")


def confirm_closure(conn, episode_id: int, user: dict, note: str | None) -> dict:
    ep = _load(conn, episode_id)
    _open(ep)
    if ep["tier"] == "TIER_1_RED":
        raise Conflict("Only the ER doctor closes a Red case")
    if ep["call_status"] not in ("done", "no_answer"):
        raise Conflict("The call has not finished")
    return _close(conn, ep, user, note or ("Follow-up: " + ep["followup"]["answer"].lower() if ep["followup"] else None),
                  "closure_confirmed_by_doctor")


def _close(conn, ep: dict, user: dict, note: str | None, action: str) -> dict:
    if not (note or "").strip():
        raise Invalid("Closing needs a note")
    open_task = conn.execute("SELECT 1 FROM coordinator_task WHERE triage_episode_id = %s AND status = 'open'",
                             (ep["episode_id"],)).fetchone()
    ep = _update(conn, ep, status="closed", closed_by=user["user_id"], closed_at=clock.get_now(conn),
                 close_note=note.strip())
    _audit(conn, ep, action, actor_type="person", actor=user["user_id"], outcome="closed_routine_messages_resume",
           details={"note": note.strip(), "coordinator_task_still_open": open_task is not None})
    set_next_action(conn, ep["mrn"], "Triage episode closed; routine follow-up resumed")
    return state(conn, ep["episode_id"])


def reopen(conn, episode_id: int, user: dict, reason: str | None) -> dict:
    ep = _load(conn, episode_id)
    if ep["status"] != "closed":
        raise Conflict("The episode is open")
    if not (reason or "").strip():
        raise Invalid("Reopening needs a reason")
    if messaging.open_triage(conn, ep["mrn"]):
        raise Conflict("Another triage episode is open for this patient")
    ep = _update(conn, ep, status="open", closed_by=None, closed_at=None, close_note=None)
    _audit(conn, ep, "triage_reopened", actor_type="person", actor=user["user_id"], details={"reason": reason.strip()})
    set_next_action(conn, ep["mrn"], "Triage episode reopened: " + reason.strip())
    return state(conn, episode_id)


def doctor_order(conn, episode_id: int, user: dict, order: str, note: str | None = None) -> dict:
    """The Doctor on Duty, after the callback: Physiotherapy referral, Bring forward blood tests or No order needed.
    A button creates an order card (owner, due date) and sends the approved booking message."""
    ep = _load(conn, episode_id)
    _open(ep)
    if order not in ORDERS:
        raise Invalid("order must be physio, bloods or none")
    if ep["tier"] != "TIER_2_AMBER":
        raise Conflict("Orders are recorded on an Amber card")
    if ep["doctor_order"]:
        raise Conflict("An order is already recorded for this episode")
    now, card = clock.get_now(conn), None
    if ORDERS[order]:
        name, card_type, dept, code, days = ORDERS[order]
        spec = policy.settings(conn).get("instruction_card") or {}
        plan = conn.execute("SELECT care_plan_id FROM care_plan WHERE mrn = %s", (ep["mrn"],)).fetchone()
        n = conn.execute("SELECT count(*) AS n FROM order_card WHERE mrn = %s AND item_code LIKE %s",
                         (ep["mrn"], code + "%")).fetchone()["n"]
        card = conn.execute(
            """
            INSERT INTO order_card (mrn, care_plan_id, item_code, card_type, name, department, due_date, state,
                                    owner_name, cover_name, origin)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'Due', %s, %s, 'instruction') RETURNING *
            """,
            (ep["mrn"], plan["care_plan_id"] if plan else None, f"{code}{n + 1}", card_type, name, dept,
             now.date() + timedelta(days=days), spec.get("owner", "Anita"), spec.get("cover", "Ravi")),
        ).fetchone()
        audit.append(conn, actor_type="person", actor_name=user["user_id"], action="card_created",
                     entity_type="order_card", entity_id=str(card["card_id"]), mrn=ep["mrn"],
                     details={"card": card["item_code"], "name": name, "due_date": card["due_date"].isoformat(),
                              "owner": card["owner_name"], "cover": card["cover_name"], "origin": "voice_triage_order",
                              "triage_episode_id": episode_id})
        tasks.create_task(conn, mrn=ep["mrn"], task_type="booking_from_instruction", owner_name=card["owner_name"],
                          card_id=card["card_id"], actor_type="system", actor="system",
                          reason=f"Book the {name} by {card['due_date'].strftime('%d/%m/%Y')} (Doctor on Duty's order)")
        messaging.send_message(conn, mrn=ep["mrn"], template_id="instruction_booking", actor_type="agent", actor=AGENT,
                               reason_code="triage_doctor_order", card_id=card["card_id"])
    ep = _update(conn, ep, doctor_order={"order": order, "note": (note or "").strip() or None, "by": user["user_id"],
                                         "at": now.isoformat(timespec="minutes"),
                                         "card_id": card["card_id"] if card else None})
    _audit(conn, ep, "doctor_order_recorded", actor_type="person", actor=user["user_id"], outcome=order,
           details={"note": note, "card_id": card["card_id"] if card else None,
                    "within_callback": bool(ep["callback_due_at"] and now <= ep["callback_due_at"])})
    set_next_action(conn, ep["mrn"], {"physio": "Book the physiotherapy referral", "bloods": "Book the blood tests",
                                      "none": "No order needed"}[order] + "; follow-up call tomorrow")
    return state(conn, episode_id)


def change_tier(conn, episode_id: int, user: dict, tier: str, reason: str | None) -> dict:
    """The doctor raises or lowers Amber/Green, with a reason. Red is the ER Desk's (only the ER doctor closes)."""
    ep = _load(conn, episode_id)
    _open(ep)
    if tier not in ("TIER_2_AMBER", "TIER_3_GREEN"):
        raise Invalid("A doctor can set Amber or Green here")
    if ep["tier"] not in ("TIER_2_AMBER", "TIER_3_GREEN"):
        raise Conflict("A Red case is not raised or lowered here")
    if tier == ep["tier"]:
        raise Conflict(f"The tier is already {TIERS[tier]}")
    if not (reason or "").strip():
        raise Invalid("Raising or lowering the tier needs a reason")
    now = clock.get_now(conn)
    changes = list(ep["tier_changes"]) + [{"from": ep["tier"], "to": tier, "by": user["user_id"],
                                           "at": now.isoformat(timespec="minutes"), "reason": reason.strip()}]
    fields = {"tier": tier, "tier_changes": changes}
    if tier == "TIER_2_AMBER" and ep["callback_due_at"] is None:
        fields["callback_due_at"] = now + timedelta(minutes=config(conn)["amber_callback_minutes"])
    ep = _update(conn, ep, **fields)
    _audit(conn, ep, "tier_changed_by_doctor", actor_type="person", actor=user["user_id"],
           outcome=f"{TIERS[changes[-1]['from']]} -> {TIERS[tier]}", details={"reason": reason.strip()})
    return state(conn, episode_id)


def followup(conn, episode_id: int, user: dict, *, answer: str | None = None, text: str | None = None) -> dict:
    """The next-day follow-up call (one question: better, the same or worse)."""
    ep = _load(conn, episode_id)
    _open(ep)
    if ep["tier"] == "TIER_1_RED" or ep["followup_due_on"] is None:
        raise Conflict("No follow-up call is planned for this episode")
    if ep["followup"]:
        raise Conflict("The follow-up call has already been made")
    now = clock.get_now(conn)
    if now.date() < ep["followup_due_on"]:
        raise Conflict(f"The follow-up call is due on {ep['followup_due_on']:%d/%m/%Y}; advance the demo clock")
    value = answer or R.parse_followup(text or "")
    if value not in FOLLOWUP_ANSWERS:
        raise Invalid("The answer must be better, same or worse")
    h = history(conn, ep["mrn"])
    m = R.messages(h)
    turns = list(ep["turns"])
    _turn(turns, conn, "system", "followup", en="Follow-up call answered")
    _turn(turns, conn, "agent", "followup", *m["followup"])
    _turn(turns, conn, "patient", "followup", (text or "").strip(), value.lower(), value=value,
          how="on-screen answer button" if answer else "typed; word list")
    _turn(turns, conn, "agent", "followup_closing", *m["followup_better" if value == "BETTER" else "followup_not_better"])
    fields = {"turns": turns, "followup": {"answer": value, "at": now.isoformat(timespec="minutes"),
                                           "how": "button" if answer else "words"}}
    if value != "BETTER":
        fields["callback_due_at"] = now + timedelta(minutes=config(conn)["amber_callback_minutes"])
    ep = _update(conn, ep, **fields)
    _audit(conn, ep, "followup_call", actor_type="agent", actor=AGENT, outcome=value)
    set_next_action(conn, ep["mrn"], "Follow-up: better; doctor to confirm closure" if value == "BETTER"
                    else f"Follow-up: {value.lower()}; Doctor on Duty to call back by {_hhmm(fields['callback_due_at'])}")
    return state(conn, episode_id)


# ---------------------------------------------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------------------------------------------
def _summary(conn, ep: dict, now: datetime) -> dict:
    p = _patient(conn, ep["mrn"])
    task = conn.execute("SELECT task_id, status, due_at_demo, owner_name, cover_name, reason FROM coordinator_task "
                        "WHERE triage_episode_id = %s AND task_type = 'confirm_ed_visit' ORDER BY task_id DESC LIMIT 1",
                        (ep["episode_id"],)).fetchone()
    doc = conn.execute("SELECT doc_id FROM triage_document WHERE episode_id = %s ORDER BY doc_id DESC LIMIT 1",
                       (ep["episode_id"],)).fetchone()
    return {
        **{k: ep[k] for k in ("episode_id", "mrn", "inbound_id", "patient_words", "translation", "opened_at",
                              "call_status", "call_due_at", "attempts", "tier", "red_kind", "result_at",
                              "callback_due_at", "package_sent_at", "package_sent_by", "doctor_note", "er_ack_by",
                              "er_ack_at", "nurse_callback", "ed_calls", "ed_outcome", "doctor_told_at", "coord_ack_by",
                              "coord_ack_at", "doctor_order", "tier_changes", "followup_due_on", "followup", "status",
                              "closed_by", "closed_at", "close_note", "stopped_early")},
        "full_name": p["full_name"], "preferred_language": p["preferred_language"], "caregiver_name": p["caregiver_name"],
        "tier_label": TIERS.get(ep["tier"]), "result": ep["result"], "facts": R.facts_text(ep["answers"]),
        "history": history(conn, ep["mrn"]),
        "callback_overdue": bool(ep["callback_due_at"] and now > ep["callback_due_at"] and ep["status"] == "open"
                                 and not ep["doctor_order"] and not ep["nurse_callback"]),
        "followup_due": bool(ep["followup_due_on"] and not ep["followup"] and now.date() >= ep["followup_due_on"]
                             and ep["status"] == "open"),
        "task": task, "task_overdue": bool(task and task["status"] == "open" and task["due_at_demo"]
                                           and now > task["due_at_demo"]),
        "pdf_doc_id": doc["doc_id"] if doc else None,
        "now": now,
    }


def episodes(conn, status: str = "open", mrn: str | None = None) -> list[dict]:
    if status not in ("open", "closed", "all"):
        raise Invalid("status must be open, closed or all")
    now = clock.get_now(conn)
    rows = conn.execute(
        "SELECT * FROM triage_episode WHERE (%(s)s = 'all' OR status = %(s)s) AND (%(m)s::text IS NULL OR mrn = %(m)s) "
        "ORDER BY episode_id DESC", {"s": status, "m": mrn}).fetchall()
    return [_summary(conn, ep, now) for ep in rows]


def state(conn, episode_id: int) -> dict:
    ep = conn.execute("SELECT * FROM triage_episode WHERE episode_id = %s", (episode_id,)).fetchone()
    if ep is None:
        raise NotFound(f"Triage episode {episode_id} not found")
    h, now = history(conn, ep["mrn"]), clock.get_now(conn)
    asked = {t["id"] for t in ep["turns"] if t["who"] == "agent"}
    a = ep["answers"]
    table = []
    for q in R.questions(h):
        if q.kind == "temperature":
            v = (a["temperature_value"] if a["thermometer_used"] == R.YES else
                 R.NO_THERMOMETER if a["thermometer_used"] == R.NO else a["thermometer_used"])
        else:
            v = a.get(q.field)
        table.append({"id": q.id, "field": q.field, "en": q.en, "ta": q.ta, "kind": q.kind, "danger": q.danger,
                      "history": q.history, "value": v, "asked": q.id in asked, "skipped": R.skipped(q, a)})
    current = R.question(h, ep["current_q"]) if ep["current_q"] else None
    return {**_summary(conn, ep, now), "turns": ep["turns"], "answers": a, "table": table,
            "current": current.__dict__ if current else None,
            "language": _lang(_patient(conn, ep["mrn"])), "protocol": R.PROTOCOL, "scenarios": R.SCENARIO_LABELS}


def turn_audio(conn, episode_id: int, index: int) -> tuple[bytes, str] | None:
    ep = conn.execute("SELECT * FROM triage_episode WHERE episode_id = %s", (episode_id,)).fetchone()
    if ep is None or not 0 <= index < len(ep["turns"]):
        raise NotFound("No such turn")
    t = ep["turns"][index]
    if t["who"] == "patient" and t.get("audio_id"):
        row = conn.execute("SELECT data, mime FROM audio_blob WHERE audio_id = %s", (t["audio_id"],)).fetchone()
        return bytes(row["data"]), row["mime"]
    if t["who"] != "agent":
        return None
    language = _lang(_patient(conn, ep["mrn"]))
    return ai.speak(conn, t["ta"] if language == "Tamil" else t["en"], language)


def document(conn, doc_id: int) -> dict:
    row = conn.execute("SELECT * FROM triage_document WHERE doc_id = %s", (doc_id,)).fetchone()
    if row is None:
        raise NotFound(f"Document {doc_id} not found")
    return row


def brief(conn, mrn: str, days: int = 90) -> dict:
    """The Pre-OPD brief: the last 90 days from the record and the triage episodes, every line with its source."""
    p = _patient(conn, mrn)
    if p is None:
        raise NotFound(f"Patient {mrn} not found")
    h, now = history(conn, mrn) or {}, clock.get_now(conn)
    since = now.date() - timedelta(days=days)
    lines = []
    for e in h.get("events", []):
        if e["date"] >= since.isoformat():
            lines.append({"at": e["date"], "kind": "record", "text": f"{e['reported']} Instruction: "
                          f"{e.get('instruction') or 'none recorded.'} {e.get('outcome', '')}".strip(),
                          "sources": [{"kind": "record", "label": "Hospital record (synthetic)"}]})
    for ep in conn.execute("SELECT * FROM triage_episode WHERE mrn = %s AND opened_at >= %s ORDER BY episode_id",
                           (mrn, since)).fetchall():
        eid = ep["episode_id"]
        src_call = {"kind": "transcript", "label": "Call transcript", "episode_id": eid}
        if ep["inbound_id"]:
            words = ep["translation"] or ep["patient_words"] or ""
            lines.append({"at": ep["opened_at"].isoformat(timespec="minutes"), "kind": "message", "episode_id": eid,
                          "text": f'She sent a message: "{words}" (health flag YES)',
                          "sources": [{"kind": "audio", "label": "Voice note", "inbound_id": ep["inbound_id"]}]})
        if ep["result"]:
            r = ep["result"]
            lines.append({"at": ep["result_at"].isoformat(timespec="minutes"), "kind": "result", "episode_id": eid,
                          "tier": ep["result"]["tier"],
                          "text": f"Call result {TIERS[r['tier']]}" + (f" ({ep['red_kind']})" if ep["red_kind"] else "")
                                  + ": " + " ".join(r["why"]),
                          "sources": [src_call]})
        if ep["package_sent_at"]:
            doc = conn.execute("SELECT doc_id FROM triage_document WHERE episode_id = %s", (eid,)).fetchone()
            lines.append({"at": ep["package_sent_at"].isoformat(timespec="minutes"), "kind": "red", "episode_id": eid,
                          "text": "Emergency package: family message with the summary PDF; ER Desk, Doctor on Duty "
                                  "and Senior Oncologist alerted; coordinator asked to confirm the emergency visit.",
                          "sources": [{"kind": "pdf", "label": "Emergency summary PDF", "doc_id": doc["doc_id"]},
                                      {"kind": "note", "label": "Doctor note", "episode_id": eid}]})
        for c in ep["ed_calls"]:
            lines.append({"at": c["at"], "kind": "person", "episode_id": eid,
                          "text": f"{_name(c['by'])} called the family: {c['outcome'].replace('_', ' ')}"
                                  + (f", at {c['hospital']}" if c.get("hospital") else ""), "sources": []})
        if ep["nurse_callback"]:
            n = ep["nurse_callback"]
            lines.append({"at": n["at"], "kind": "person", "episode_id": eid,
                          "text": f"Nurse callback by {_name(n['by'])}: {n['note']}", "sources": []})
        if ep["doctor_order"]:
            o = ep["doctor_order"]
            label = {"physio": "Physiotherapy referral", "bloods": "Bring forward blood tests",
                     "none": "No order needed"}[o["order"]]
            lines.append({"at": o["at"], "kind": "person", "episode_id": eid,
                          "text": f"{_name(o['by'])}: {label}" + (f' — "{o["note"]}"' if o.get("note") else ""),
                          "sources": [{"kind": "card", "label": "Order card", "card_id": o["card_id"]}]
                          if o.get("card_id") else []})
        for c in ep["tier_changes"]:
            lines.append({"at": c["at"], "kind": "person", "episode_id": eid,
                          "text": f"{_name(c['by'])} changed the tier {TIERS[c['from']]} to {TIERS[c['to']]}: "
                                  f"{c['reason']}", "sources": []})
        if ep["followup"]:
            lines.append({"at": ep["followup"]["at"], "kind": "result", "episode_id": eid,
                          "text": f"Follow-up call: {ep['followup']['answer'].lower()}", "sources": [src_call]})
        if ep["status"] == "closed":
            lines.append({"at": ep["closed_at"].isoformat(timespec="minutes"), "kind": "person", "episode_id": eid,
                          "text": f"Closed by {_name(ep['closed_by'])}: {ep['close_note']}", "sources": []})
    lines.sort(key=lambda x: x["at"])
    open_red = conn.execute("SELECT 1 FROM triage_episode WHERE mrn = %s AND status = 'open' AND tier = 'TIER_1_RED'",
                            (mrn,)).fetchone() is not None
    return {"mrn": mrn, "full_name": p["full_name"], "title": h.get("title", p["full_name"]), "days": days,
            "since": since, "open_red": open_red, "clinical": h.get("clinical"), "medicines": h.get("medicines", []),
            "signed_note": h.get("signed_note"), "advice": h.get("advice"), "lines": lines}
