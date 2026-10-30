"""Voice Triage MVP-lite (CarePilot_Voice_Triage_MVP_Lite_v1.2): the health message, the automated call, the tier
and what each tier sets in motion. It replaces the Use Case 2c handover for health messages in the data sets that
switch it on (settings.voice_triage.enabled).

1. A voice note (or text) the router sends to triage (triage_router: the model's tags, plain rules): the fixed
   acknowledgement goes out and the call is due within 2 minutes. The plan for the call (the danger core, the
   bangles question, the modules she mentioned, the floors) is stored on the episode. Routine messages are held
   while the episode is open. A message that is clearly not about health never gets here (one standard reply).
2. The call (simulated in the phone simulator, Bulbul audio when Sarvam is on): the opening, then the planned
   questions (8 to 16; the fixed 12 when the tags were unusable), one at a time. Each answer becomes one value (word lists, then a checked model answer); one re-ask; stop at the
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
import array
import io
import math
import sys
import textwrap
import time
import wave
from datetime import datetime, timedelta

from psycopg.types.json import Jsonb

from . import ai, audit, clock, messaging, policy, tasks
from . import triage_router as TR
from . import triage_rules as R
from .agent import set_next_action
from .errors import Conflict, Invalid, NotFound
from .reference import USERS_BY_ID

AGENT = "CarePilot triage agent"
RULES = "Triage rules (v1.2, draft)"
TIERS = {"TIER_1_RED": "Red", "TIER_2_AMBER": "Amber", "TIER_3_GREEN": "Green"}
ORDERS = {"physio": ("Physiotherapy referral", "visit", "Physiotherapy", "PHYSIO", 7),
          "bloods": ("Blood tests (brought forward)", "test", "Laboratory", "BLOODFWD", 3),
          "none": None}
ORDER_LABELS = {"physio": "Physiotherapy referral", "bloods": "Blood tests brought forward", "none": "No order needed"}
ED_OUTCOMES = ("REACHED", "NOT_YET", "REFUSED", "NOT_REACHABLE")
FOLLOWUP_ANSWERS = ("BETTER", "SAME", "WORSE")
REASON_LINES = {
    "CONFIRMED": {"English": "{name} Amma gave answers to our call that are warning signs.",
                  "Tamil": "{name_own} அம்மா எங்கள் அழைப்பில் கொடுத்த பதில்கள் எச்சரிக்கை அறிகுறிகளைக் காட்டுகின்றன.",
                  "Hindi": "{name_own} जी ने हमारी कॉल में जो जवाब दिए, उनमें खतरे के संकेत हैं।",
                  "Telugu": "{name_own} గారు మా ఫోన్ కాల్‌లో ఇచ్చిన జవాబుల్లో ప్రమాద సంకేతాలు ఉన్నాయి."},
    "NO_ANSWER": {"English": "{name} Amma sent us a message saying she is unwell. We called twice and could not "
                             "reach her.",
                  "Tamil": "{name_own} அம்மா தனக்கு உடல்நிலை சரியில்லை என்று எங்களுக்குச் செய்தி அனுப்பினார். இரண்டு முறை "
                           "அழைத்தும் அவரைத் தொடர்பு கொள்ள முடியவில்லை.",
                  "Hindi": "{name_own} जी ने हमें संदेश भेजा कि उनकी तबीयत ठीक नहीं है। हमने दो बार कॉल किया, पर उनसे "
                           "बात नहीं हो सकी।",
                  "Telugu": "{name_own} గారు తనకు ఆరోగ్యం బాగోలేదని మాకు సందేశం పంపారు. రెండు సార్లు ఫోన్ చేసినా ఆమెను "
                            "సంప్రదించలేకపోయాము."},
    "UNCONFIRMED": {"English": "{name} Amma's answers could not be confirmed as safe.",
                    "Tamil": "{name_own} அம்மாவின் பதில்கள் அவர் பாதுகாப்பாக இருக்கிறார் என்பதை உறுதிப்படுத்த முடியவில்லை.",
                    "Hindi": "{name_own} जी के जवाबों से यह पक्का नहीं हो सका कि वे सुरक्षित हैं।",
                    "Telugu": "{name_own} గారి జవాబులతో ఆమె సురక్షితంగా ఉన్నారని నిర్ధారించలేకపోయాము."},
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
            "amber_callback_minutes": t.get("amber_callback_minutes", 120), "cover": t.get("cover"),
            "cover_alert_minutes": t.get("cover_alert_minutes", 90), "digest_ack_hour": t.get("digest_ack_hour", 18)}


def enabled(conn) -> bool:
    return config(conn)["enabled"]


def history(conn, mrn: str) -> dict | None:
    """The patient's triage history record, with the call's language (from the patient's preferred language)."""
    row = conn.execute("SELECT h.data, p.preferred_language FROM triage_history h JOIN patient p USING (mrn) "
                       "WHERE h.mrn = %s", (mrn,)).fetchone()
    return {**row["data"], "language": _lang(row)} if row else None


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


def _ticket(conn, at: datetime) -> str:
    """The ticket ID: CP-<date opened>-<number that day>, e.g. CP-20261027-0001."""
    n = conn.execute("SELECT count(*) AS n FROM triage_episode WHERE opened_at::date = %s", (at.date(),)).fetchone()["n"]
    return f"CP-{at:%Y%m%d}-{n + 1:04d}"


def _lang(patient: dict) -> str:
    """The call speaks the patient's language when it is a call language (Tamil, Hindi, Telugu); else English."""
    lang = patient["preferred_language"]
    return lang if lang in R.CALL_LANGUAGES else "English"


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
                 from_role: str = "patient", tags=None, tagged: bool = False) -> tuple[dict, bool] | None:
    """A health message with Voice Triage on: the acknowledgement, then a call due within 2 minutes, with the
    router's plan for it (tags: the model's tags; none, or broken, ask the fixed 12). None when the patient has no
    triage history in the record (then the usual handover path runs)."""
    existing = messaging.open_triage(conn, mrn)
    if existing:
        ep = _update(conn, existing, patient_words="\n".join(x for x in (existing["patient_words"], words) if x),
                     translation="\n".join(x for x in (existing["translation"], translation) if x))
        _audit(conn, ep, "triage_message_added", actor_type="system", actor="WhatsApp gateway",
               details={"inbound_id": inbound["inbound_id"]})
        return ep, False
    h = history(conn, mrn)
    if h is None:
        return None
    plan = TR.plan(h, tags)
    if not plan["question_ids"]:  # sent here although the tags say "not health" (the seed's flag): the fixed 12
        plan = TR.plan(h, None)
    plan["tagged"] = tagged
    cfg, now = config(conn), clock.get_now(conn)
    ack = messaging.send_message(conn, mrn=mrn, template_id="triage_ack", actor_type="agent", actor=AGENT,
                                 reason_code="health_flag_voice_triage", recipient=from_role)
    ep = conn.execute(
        """
        INSERT INTO triage_episode (ticket_id, mrn, inbound_id, patient_words, translation, opened_at, call_due_at,
                                    answers, plan)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *
        """,
        (_ticket(conn, now), mrn, inbound["inbound_id"], words, translation, now,
         now + timedelta(minutes=cfg["call_within_minutes"]), Jsonb(R.blank()), Jsonb(plan)),
    ).fetchone()
    conn.execute("UPDATE outbox_message SET triage_episode_id = %s WHERE message_id = %s",
                 (ep["episode_id"], ack["message_id"]))
    _audit(conn, ep, "triage_opened", actor_type="agent", actor=AGENT, reason_code="health_flag_voice_triage",
           trigger_event=f"inbound_message:{inbound['inbound_id']}", outcome="call_due",
           decision_inputs={"health_flag": "yes", "voice_triage": True, "tags": plan["tags"]},
           details={"call_due_at": _hhmm(ep["call_due_at"]), "routine_messages": "held while the episode is open",
                    "questions": len(plan["question_ids"]), "modules": plan["modules"], "floors": plan["floors"],
                    "profile": plan["profile"]})
    set_next_action(conn, mrn, f"Automated triage call due by {_hhmm(ep['call_due_at'])}; routine messages held")
    return ep, True


def _plan(ep: dict, h: dict) -> dict:
    """The router's plan stored on the episode (the fixed 12 for an episode from before v1.2)."""
    return ep.get("plan") or {**TR.plan(h, None), "tagged": False}


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
    q = R.next_question(h, ep["answers"], None, _plan(ep, h)["question_ids"])
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
    nq = R.next_question(h, answers, q.id, _plan(ep, h)["question_ids"])
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
    """Demo control "Play as": the scripted answers (in the call's language) of the guide's scenario go through the same parsing and
    rules. On a call that has finished, the same voice note is played again as a new episode (demo replay)."""
    if scenario not in R.SCENARIO_LABELS:
        raise Invalid(f"Unknown scenario; choose one of: {', '.join(R.SCENARIO_LABELS)}")
    ep = _load(conn, episode_id)
    if ep["call_status"] in ("done", "no_answer"):
        ep = _replay(conn, ep, user, scenario)
    if ep["call_status"] == "due":
        pickup(conn, ep["episode_id"], user)
    lang = _lang(_patient(conn, ep["mrn"]))
    script = R.scenarios(lang)[scenario]
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
            answer(conn, ep["episode_id"], user, text=said, via=f"Play as {scenario} (scripted {lang} answer)")
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
        INSERT INTO triage_episode (ticket_id, mrn, inbound_id, patient_words, translation, opened_at, call_due_at,
                                    answers, plan)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *
        """,
        (_ticket(conn, now), ep["mrn"], ep["inbound_id"], ep["patient_words"], ep["translation"], now, now,
         Jsonb(R.blank()), Jsonb(ep["plan"]) if ep["plan"] else None),
    ).fetchone()
    _audit(conn, new, "triage_opened", actor_type="person", actor=user["user_id"], reason_code="demo_replay",
           details={"replays_episode": ep["episode_id"], "scenario": scenario})
    return new


# ---------------------------------------------------------------------------------------------------------------
# 3. The result, and 4. what each tier sets in motion
# ---------------------------------------------------------------------------------------------------------------
def _finish(conn, ep: dict, *, nurse=False, fault=False, no_answer=False) -> dict:
    h, now = history(conn, ep["mrn"]), clock.get_now(conn)
    plan = _plan(ep, h)
    answers = dict(ep["answers"])
    if fault:  # fail closed: a danger question she never answered counts as unclear
        for q in R.questions(h, plan["question_ids"]):
            if q.danger and not R.skipped(q, answers) and answers.get(q.field, R.NOT_ASKED) == R.NOT_ASKED \
                    and not (q.kind == "temperature" and answers["thermometer_used"] != R.NOT_ASKED):
                R.store(answers, q, R.UNKNOWN)
    result = R.triage(answers, h, now.date(), nurse=nurse, fault=fault or ep["fault"], no_answer=no_answer,
                      floors=plan["floors"], profile=plan["profile"] or "ROUTED")
    result.update(stopped_after_danger_sign=ep["stopped_early"], why=R.why_lines(result, answers, h))
    turns = list(ep["turns"])
    if not no_answer:
        ta, en = R.closing(result, h)
        _turn(turns, conn, "agent", "closing", ta, en, template_id=result["patient_template_id"])
        _turn(turns, conn, "system", "hangup", en="Call ended")
    ep = _update(conn, ep, call_status="no_answer" if no_answer else "done", current_q=None, turns=turns,
                 answers=answers, result=result, tier=result["tier"], red_kind=result.get("red_kind"), result_at=now)
    _audit(conn, ep, "triage_result", reason_code=result["tier"], outcome=result["action_code"],
           rule=",".join(result["reason_codes"]) or None,
           policy_version=f"{result['protocol_profile']} v1.2 (DRAFT_UNSIGNED)",
           decision_inputs={"answers": answers, "nurse": nurse, "fault": fault, "no_answer": no_answer,
                            "floors": plan["floors"], "questions": plan["question_ids"]},
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
        _update(conn, ep, callback_due_at=due)  # the follow-up call is set 24 hours after the doctor's outcome
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
    reason = {lang: line.format(name=h["name_en"], name_own=R.native(h, "name", lang))
              for lang, line in REASON_LINES[kind].items()}
    msg = messaging.send_message(conn, mrn=ep["mrn"], template_id="triage_red_caregiver", actor_type=actor_type,
                                 actor=actor, reason_code=f"red_{kind.lower()}", recipient="caregiver",
                                 extra={"reason": reason})
    conn.execute("UPDATE outbox_message SET attachment_doc_id = %s, triage_episode_id = %s WHERE message_id = %s",
                 (doc["doc_id"], ep["episode_id"], msg["message_id"]))
    relation = f", her {h['caregiver_relation']}" if h.get("caregiver_relation") else ""
    family = f"the family ({patient['caregiver_name']}{relation})" if patient["caregiver_name"] else "the family"
    why = "; ".join([R.RED_PLAIN[c] for c in ep["result"]["reason_codes"] if c in R.RED_PLAIN] +
                    [f"Raised to Red by {_name(c['by'])}: {c['reason']}" for c in ep["tier_changes"]
                     if c["to"] == "TIER_1_RED"])
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
NO_PLAIN = {"slurred_or_confused": "speech slurred or confusion", "cannot_stay_awake": "very hard to stay awake",
            "chest_pain": "chest pain or trouble breathing", "arm_red_or_hot": "left arm red or hot",
            "bangles_tighter": "bangles tighter than before", "unsteady_or_dizzy_walking": "dizzy or unsteady walking",
            "blurred_vision": "blurred vision", "shaking_chills": "shaking chills", "feels_hot": "feels hot or feverish",
            "arm_swollen": "left arm swollen or heavy", "skin_break_left_arm": "cut, bite or prick on the left arm"}


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


# ---- Ticket states (guide v1.2, section 7.6). The state is read from what people did; a move a role may not make,
# or one the state does not allow, is refused with a message. The agent never acknowledges or closes.
STATES = ("IN_DIGEST", "OPEN", "ACKNOWLEDGED", "OUTCOME_LOGGED", "CLOSURE_PROPOSED", "CLOSED", "REOPENED")
STATE_LABELS = {"IN_DIGEST": "In digest", "OPEN": "Open", "ACKNOWLEDGED": "Acknowledged",
                "OUTCOME_LOGGED": "Outcome logged", "CLOSURE_PROPOSED": "Closure proposed", "CLOSED": "Closed",
                "REOPENED": "Reopened"}
# who may acknowledge, by tier: Green the coordinator (the digest); Amber the doctor, the nurse or the coordinator;
# Red the nurse or the ER doctor (both sign in at the ER Desk)
WHO_MAY_ACK = {"TIER_3_GREEN": ("coordinator",), "TIER_2_AMBER": ("oncologist", "er_desk", "coordinator"),
               "TIER_1_RED": ("er_desk",)}
ACK_FIELD = {"TIER_3_GREEN": "digest_ack_by", "TIER_2_AMBER": "coord_ack_by", "TIER_1_RED": "er_ack_by"}


def _outcome_logged(ep: dict) -> bool:
    if ep["tier"] == "TIER_1_RED":
        return bool(ep["er_log"] or ep["nurse_callback"] or ep["ed_outcome"])
    return bool(ep["doctor_order"] or ep["nurse_callback"])


def ticket_state(ep: dict) -> str:
    if ep["status"] == "closed":
        return "CLOSED"
    tier = ep["tier"]
    if tier is None:
        return "OPEN"
    if not ep[ACK_FIELD[tier]]:
        return "REOPENED" if ep["reopened_at"] else "IN_DIGEST" if tier == "TIER_3_GREEN" else "OPEN"
    if tier == "TIER_2_AMBER" and _outcome_logged(ep) and (ep["followup"] or {}).get("answer") == "BETTER":
        return "CLOSURE_PROPOSED"  # the agent proposes after the 24-hour call; a person confirms
    if tier != "TIER_3_GREEN" and _outcome_logged(ep):
        return "OUTCOME_LOGGED"
    return "ACKNOWLEDGED"


def _ack(conn, ep: dict, user: dict, now: datetime, how: str = "button") -> dict:
    """Record the acknowledgement for the ticket's tier (Amber and Red only; Green is the digest)."""
    tier = ep["tier"]
    field = ACK_FIELD[tier]
    at = {"coord_ack_by": "coord_ack_at", "er_ack_by": "er_ack_at"}[field]
    ep = _update(conn, ep, **{field: user["user_id"], at: now})
    _audit(conn, ep, "triage_acknowledged", actor_type="person", actor=user["user_id"],
           outcome="ACKNOWLEDGED", details={"role": user["role"], "tier": TIERS[tier], "how": how})
    return ep


def _auto_ack(conn, ep: dict, user: dict) -> dict:
    """A person who logs an outcome on an unacknowledged ticket acknowledges it too (when the role may)."""
    tier = ep["tier"]
    if tier in ("TIER_1_RED", "TIER_2_AMBER") and not ep[ACK_FIELD[tier]] and user["role"] in WHO_MAY_ACK[tier]:
        ep = _ack(conn, ep, user, clock.get_now(conn), how="with the outcome")
    return ep


def acknowledge(conn, episode_id: int, user: dict) -> dict:
    """Acknowledge the ticket: Green the coordinator (the digest line), Amber the Doctor on Duty, the nurse or the
    coordinator, Red the nurse or the ER doctor."""
    ep = _load(conn, episode_id)
    _open(ep)
    tier = ep["tier"]
    if tier is None:
        raise Conflict("The call has no result yet")
    if user["role"] not in WHO_MAY_ACK[tier]:
        raise Conflict({"TIER_3_GREEN": "Only the coordinator acknowledges the Green digest",
                        "TIER_2_AMBER": "The Doctor on Duty, the nurse or the coordinator acknowledges an Amber ticket",
                        "TIER_1_RED": "Only the nurse or the ER doctor acknowledges a Red alert"}[tier])
    if tier == "TIER_3_GREEN":
        return digest_ack(conn, episode_id, user)
    if ep[ACK_FIELD[tier]]:
        raise Conflict(f"Already acknowledged by {_name(ep[ACK_FIELD[tier]])}")
    _ack(conn, ep, user, clock.get_now(conn))
    return state(conn, episode_id)


def er_log(conn, episode_id: int, user: dict, note: str | None) -> dict:
    """Red: the nurse or the ER doctor records what was done (the outcome). Only the ER doctor then closes."""
    ep = _load(conn, episode_id)
    _open(ep)
    if ep["tier"] != "TIER_1_RED":
        raise Conflict("'Log what was done' is for a Red ticket")
    if not (note or "").strip():
        raise Invalid("Write what was done")
    ep = _auto_ack(conn, ep, user)
    now = clock.get_now(conn)
    ep = _update(conn, ep, er_log={"by": user["user_id"], "at": now.isoformat(timespec="minutes"),
                                   "note": note.strip()})
    _audit(conn, ep, "red_outcome_logged", actor_type="person", actor=user["user_id"], outcome="OUTCOME_LOGGED",
           details={"note": note.strip()})
    set_next_action(conn, ep["mrn"], f"Red: outcome logged by {_name(user['user_id'])}; the ER doctor closes")
    return state(conn, episode_id)


def _nurse_asked(ep: dict) -> bool:
    """She pressed 0, or Red (unconfirmed): by the rules or raised to Red by hand."""
    return (ep["result"] or {}).get("action_code") == "NURSE_CALLBACK_15_MIN" or \
        (ep["tier"] == "TIER_1_RED" and ep["red_kind"] == "UNCONFIRMED")


def nurse_callback(conn, episode_id: int, user: dict, note: str | None) -> dict:
    ep = _load(conn, episode_id)
    _open(ep)
    if not (note or "").strip():
        raise Invalid("Write what the nurse found on the call")
    if not _nurse_asked(ep):
        raise Conflict("No nurse callback was asked for in this episode")
    ep = _auto_ack(conn, ep, user)
    now = clock.get_now(conn)
    fields = {"nurse_callback": {"by": user["user_id"], "at": now.isoformat(timespec="minutes"), "note": note.strip()}}
    if ep["tier"] == "TIER_2_AMBER" and ep["followup_due_on"] is None:
        fields["followup_due_on"] = now.date() + timedelta(days=1)  # 24 hours after the nurse's outcome
    ep = _update(conn, ep, **fields)
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
    st = ticket_state(ep)
    if st in ("OPEN", "REOPENED"):
        raise Conflict("Acknowledge the alert first")
    if st == "ACKNOWLEDGED":
        raise Conflict("Log what was done first (or the coordinator records the family call)")
    return _close(conn, ep, user, note, "red_closed_by_er_doctor")


def confirm_closure(conn, episode_id: int, user: dict, note: str | None) -> dict:
    """Amber: the Doctor on Duty or the coordinator confirms closure, only after the 24-hour call proposed it
    (outcome logged and the follow-up says better). The agent never closes."""
    ep = _load(conn, episode_id)
    _open(ep)
    if ep["tier"] == "TIER_1_RED":
        raise Conflict("Only the ER doctor closes a Red case")
    if ep["tier"] == "TIER_3_GREEN":
        raise Conflict("A Green ticket closes into the digest: the coordinator acknowledges it")
    if ticket_state(ep) != "CLOSURE_PROPOSED":
        raise Conflict("An Amber ticket is closed only after the 24-hour call proposes closure "
                       f"(now: {STATE_LABELS[ticket_state(ep)]})")
    return _close(conn, ep, user, note or ("Follow-up: " + ep["followup"]["answer"].lower() if ep["followup"] else None),
                  "closure_confirmed_by_doctor" if user["role"] == "oncologist" else "closure_confirmed_by_coordinator")


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
    """Any staff reopens a closed ticket, with a reason: it is Reopened until someone acknowledges it again. A Green
    ticket in the digest is reopened by the coordinator: it becomes an open Amber ticket."""
    ep = _load(conn, episode_id)
    if not (reason or "").strip():
        raise Invalid("Reopening needs a reason")
    if ep["status"] == "open":
        if ep["tier"] != "TIER_3_GREEN":
            raise Conflict("The ticket is open")
        if user["role"] != "coordinator":
            raise Conflict("Only the coordinator reopens a ticket in the Green digest")
        change_tier(conn, episode_id, user, "TIER_2_AMBER", reason)
        ep = _update(conn, _load(conn, episode_id), reopened_at=clock.get_now(conn))
        _audit(conn, ep, "triage_reopened", actor_type="person", actor=user["user_id"], outcome="REOPENED",
               details={"reason": reason.strip(), "from": "Green digest", "now": "Amber, open"})
        return state(conn, episode_id)
    if messaging.open_triage(conn, ep["mrn"]):
        raise Conflict("Another triage episode is open for this patient")
    ep = _update(conn, ep, status="open", closed_by=None, closed_at=None, close_note=None,
                 reopened_at=clock.get_now(conn), coord_ack_by=None, coord_ack_at=None, er_ack_by=None, er_ack_at=None,
                 digest_ack_by=None, digest_ack_at=None)
    _audit(conn, ep, "triage_reopened", actor_type="person", actor=user["user_id"], outcome="REOPENED",
           details={"reason": reason.strip()})
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
    ep = _auto_ack(conn, ep, user)
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
                                         "card_id": card["card_id"] if card else None},
                 followup_due_on=now.date() + timedelta(days=1))  # the AI calls again 24 hours after the outcome
    _audit(conn, ep, "doctor_order_recorded", actor_type="person", actor=user["user_id"], outcome=order,
           details={"note": note, "card_id": card["card_id"] if card else None,
                    "within_callback": bool(ep["callback_due_at"] and now <= ep["callback_due_at"])})
    set_next_action(conn, ep["mrn"], {"physio": "Book the physiotherapy referral", "bloods": "Book the blood tests",
                                      "none": "No order needed"}[order] + "; follow-up call tomorrow")
    return state(conn, episode_id)


RANK = {"TIER_3_GREEN": 0, "TIER_2_AMBER": 1, "TIER_1_RED": 2}
TIER_RULE = ("Coordinators and nurses can only raise the tier. Only a doctor can lower it, and Red only the ER doctor. "
             "A reason is always needed.")


def change_tier(conn, episode_id: int, user: dict, tier: str, reason: str | None) -> dict:
    """The rules set the tier; a person may change it, always with a reason. Anyone on the team may raise it; only a
    doctor lowers it, and Red only the ER doctor. Raised to Red by hand it is Red (unconfirmed): the ER Desk alert
    and a nurse callback within 15 minutes; the family message goes only when a person sends it."""
    ep = _load(conn, episode_id)
    _open(ep)
    if tier not in TIERS:
        raise Invalid("tier must be TIER_1_RED, TIER_2_AMBER or TIER_3_GREEN")
    if ep["tier"] is None:
        raise Conflict("The call has no result yet")
    if tier == ep["tier"]:
        raise Conflict(f"The tier is already {TIERS[tier]}")
    if not (reason or "").strip():
        raise Invalid("Changing the tier needs a reason")
    cfg, now = config(conn), clock.get_now(conn)
    raising = RANK[tier] > RANK[ep["tier"]]
    if not raising and ep["tier"] == "TIER_1_RED" and user["user_id"] != cfg["er_doctor"]:
        raise Conflict("Only the ER doctor lowers a Red case")
    if not raising and ep["tier"] != "TIER_1_RED" and user["role"] != "oncologist":
        raise Conflict("Only a doctor lowers the tier; coordinators and nurses can only raise it")
    changes = list(ep["tier_changes"]) + [{"from": ep["tier"], "to": tier, "by": user["user_id"], "role": user["role"],
                                           "at": now.isoformat(timespec="minutes"), "reason": reason.strip()}]
    fields = {"tier": tier, "tier_changes": changes}
    if tier == "TIER_1_RED":
        fields.update(red_kind="UNCONFIRMED", callback_due_at=now + timedelta(minutes=cfg["nurse_minutes"]),
                      followup_due_on=None)
    elif tier == "TIER_2_AMBER":
        fields["red_kind"] = None
        if not ep["doctor_order"]:
            fields.update(callback_due_at=now + timedelta(minutes=cfg["amber_callback_minutes"]), followup_due_on=None)
    else:
        fields.update(red_kind=None, callback_due_at=None)
        if ep["followup_due_on"] is None and not ep["followup"]:
            fields["followup_due_on"] = now.date() + timedelta(days=1)
    ep = _update(conn, ep, **fields)
    _audit(conn, ep, "tier_raised" if raising else "tier_lowered", actor_type="person", actor=user["user_id"],
           outcome=f"{TIERS[changes[-1]['from']]} -> {TIERS[tier]}",
           details={"reason": reason.strip(), "role": user["role"]})
    who, verb = _name(user["user_id"]), "raised" if raising else "lowered"
    if tier == "TIER_1_RED":
        _audit(conn, ep, "er_desk_alert_created", outcome="UNCONFIRMED",
               details={"to": ["ER Desk", "Doctor on Duty", "Senior Oncologist (banner)"], "raised_by": who})
        set_next_action(conn, ep["mrn"], f"Red (raised by {who}): nurse to call back by "
                                         f"{_hhmm(ep['callback_due_at'])}; no family message sent")
    elif tier == "TIER_2_AMBER" and not ep["doctor_order"]:
        set_next_action(conn, ep["mrn"], f"Amber ({verb} by {who}): {_name(cfg['doctor_on_duty']) or 'Doctor on duty'} "
                                         f"to call back by {_hhmm(ep['callback_due_at'])}")
    else:
        set_next_action(conn, ep["mrn"], f"{TIERS[tier]} ({verb} by {who})")
    return state(conn, episode_id)


def _digest_due(ep: dict, cfg: dict) -> datetime | None:
    """Green: the coordinator acknowledges the digest by the end of the next working day (Monday to Saturday)."""
    if ep["result_at"] is None:
        return None
    d = ep["result_at"].date() + timedelta(days=1)
    while d.weekday() == 6:
        d += timedelta(days=1)
    return datetime.combine(d, datetime.min.time()).replace(hour=cfg["digest_ack_hour"])


def digest_ack(conn, episode_id: int, user: dict) -> dict:
    """Green: the coordinator acknowledges the digest line. The ticket closes into the digest once the follow-up call
    says she is better (to reopen it instead, raise it to Amber with a reason)."""
    ep = _load(conn, episode_id)
    _open(ep)
    if ep["tier"] != "TIER_3_GREEN":
        raise Conflict("Only a Green ticket goes to the digest")
    if ep["digest_ack_by"]:
        raise Conflict(f"Already acknowledged by {_name(ep['digest_ack_by'])}")
    now = clock.get_now(conn)
    due = _digest_due(ep, config(conn))
    ep = _update(conn, ep, digest_ack_by=user["user_id"], digest_ack_at=now)
    _audit(conn, ep, "green_digest_acknowledged", actor_type="person", actor=user["user_id"],
           details={"due": due.isoformat(timespec="minutes") if due else None, "in_time": bool(due and now <= due)})
    _close_green(conn, ep)
    return state(conn, episode_id)


def _close_green(conn, ep: dict) -> None:
    """Green closes into the digest: acknowledged by the coordinator and the follow-up says better."""
    if not (ep["status"] == "open" and ep["tier"] == "TIER_3_GREEN" and ep["digest_ack_by"]
            and (ep["followup"] or {}).get("answer") == "BETTER"):
        return
    note = f"Closed into the Green digest (acknowledged by {_name(ep['digest_ack_by'])}; follow-up: better)"
    ep = _update(conn, ep, status="closed", closed_by=ep["digest_ack_by"], closed_at=clock.get_now(conn),
                 close_note=note)
    _audit(conn, ep, "closed_into_green_digest", outcome="closed_routine_messages_resume", details={"note": note})
    set_next_action(conn, ep["mrn"], "Triage episode closed into the Green digest; routine follow-up resumed")


def _check_cover(conn, ep: dict, now: datetime) -> dict:
    """Amber: if nobody has acted (no doctor outcome, no nurse callback) 90 minutes after the result, the cover
    coordinator is alerted, once, with a task."""
    if ep["status"] != "open" or ep["tier"] != "TIER_2_AMBER" or ep["cover_alerted_at"] or ep["doctor_order"] \
            or ep["nurse_callback"] or ep["result_at"] is None:
        return ep
    cfg = config(conn)
    if now < ep["result_at"] + timedelta(minutes=cfg["cover_alert_minutes"]):
        return ep
    cover = _name(cfg["cover"]) or None
    ep = _update(conn, ep, cover_alerted_at=now)
    task = tasks.create_task(
        conn, mrn=ep["mrn"], task_type="triage_cover_alert", owner_name=cover, actor_type="system", actor="system",
        dedupe_key=f"triage:{ep['episode_id']}:cover_alert",
        reason=(f"Amber ticket {ep['ticket_id']}: nobody has acted {cfg['cover_alert_minutes']} minutes after the call "
                f"({_hhmm(ep['result_at'])}). Reach {_name(cfg['doctor_on_duty']) or 'the Doctor on Duty'} now; the "
                f"2-hour callback is due by {_hhmm(ep['callback_due_at'])}."))
    conn.execute("UPDATE coordinator_task SET due_at_demo = %s, due_date = %s, triage_episode_id = %s "
                 "WHERE task_id = %s", (ep["callback_due_at"], now.date(), ep["episode_id"], task["task_id"]))
    _audit(conn, ep, "amber_cover_alerted", outcome="cover_task_created",
           details={"cover": cover, "minutes": cfg["cover_alert_minutes"], "task_id": task["task_id"]})
    set_next_action(conn, ep["mrn"], f"Amber: no action in {cfg['cover_alert_minutes']} minutes; cover ({cover}) "
                                     "alerted")
    return ep


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
    if value == "BETTER" and ep["digest_ack_by"]:
        _close_green(conn, ep)
        return state(conn, episode_id)
    set_next_action(conn, ep["mrn"], "Follow-up: better; doctor to confirm closure" if value == "BETTER"
                    else f"Follow-up: {value.lower()}; Doctor on Duty to call back by {_hhmm(fields['callback_due_at'])}")
    return state(conn, episode_id)


# ---------------------------------------------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------------------------------------------
# ---- The Triage Room ticket: deadlines, each role's next action, the facts summary ----
def _at(d) -> datetime:
    """A follow-up day as a time: the call goes out at 09:00."""
    return datetime.combine(d, datetime.min.time()).replace(hour=9)


def _deadlines(ep: dict, task: dict | None, cover_task: dict | None, cfg: dict) -> list[dict]:
    """Every open deadline on the ticket: {at, what, role}. The countdown shows the nearest."""
    if ep["status"] != "open":
        return []
    out = []
    add = lambda at, what, role: at and out.append({"at": at, "what": what, "role": role})  # noqa: E731
    if ep["call_status"] == "due":
        add(ep["call_due_at"], "Automated call", "ai")
    tier = ep["tier"]
    if tier == "TIER_1_RED":
        if _nurse_asked(ep) and not ep["nurse_callback"]:
            add(ep["callback_due_at"], "Nurse callback", "nurse")
        if task and task["status"] == "open":
            add(task["due_at_demo"], "Call the family", "coordinator")
    elif tier == "TIER_2_AMBER":
        if _nurse_asked(ep):
            if not ep["nurse_callback"]:
                add(ep["callback_due_at"], "Nurse callback", "nurse")
        elif not ep["doctor_order"]:
            add(ep["callback_due_at"], "Doctor callback", "doctor")
            if not ep["cover_alerted_at"] and ep["result_at"]:
                add(ep["result_at"] + timedelta(minutes=cfg["cover_alert_minutes"]),
                    f"Cover alert ({_name(cfg['cover']) or 'cover'})", "coordinator")
        if cover_task and cover_task["status"] == "open" and not ep["doctor_order"]:
            add(cover_task["due_at_demo"], "Cover: reach the Doctor on Duty", "coordinator")
    elif tier == "TIER_3_GREEN" and not ep["digest_ack_by"]:
        add(_digest_due(ep, cfg), "Acknowledge the digest", "coordinator")
    if ep["followup_due_on"] and not ep["followup"] and tier != "TIER_1_RED":
        add(_at(ep["followup_due_on"]), "Follow-up call", "ai")
    return sorted(out, key=lambda d: d["at"])


def _next_actions(ep: dict, task: dict | None, cfg: dict) -> dict:
    """One card per role (AI, doctor, nurse, coordinator): what is due, or done, for this colour. Facts only."""
    none = {"text": "Nothing", "done": True}
    card = lambda text, done=False, due=None: {"text": text, "done": done, "due_at": due}  # noqa: E731
    tier, doc = ep["tier"], _name(cfg["doctor_on_duty"]) or "The Doctor on Duty"
    out = {"ai": none, "doctor": none, "nurse": none, "coordinator": none}
    if tier is None:
        n = len((ep["plan"] or {}).get("question_ids") or TR.FALLBACK)
        out["ai"] = card(f"Call her by {_hhmm(ep['call_due_at'])} ({n} questions in her language)"
                         if ep["call_status"] == "due" else "On the call now", due=ep["call_due_at"])
        return out
    fu = ep["followup"]
    followup = (f"Follow-up call: {fu['answer'].lower()} ({fu['at'][11:16]})" if fu else
                f"Calls again on {ep['followup_due_on']:%d %b}" if ep["followup_due_on"] else None)
    if tier == "TIER_1_RED":
        out["ai"] = card(f"Sent the family the message and PDF and wrote the doctor note at {_hhmm(ep['package_sent_at'])}",
                         True) if ep["package_sent_at"] else card(
            "No family message: the answers are unconfirmed. A person may send the package")
        er = _name(cfg['er_doctor']).replace(' (ER doctor)', '')
        out["doctor"] = card(f"Closed by {_name(ep['closed_by'])}" if ep["status"] == "closed" else
                             f"Logged by {_name(ep['er_log']['by'])}: “{ep['er_log']['note']}”. The ER doctor ({er}) "
                             "closes the case" if ep["er_log"] else
                             f"Record what was done. Only the ER doctor ({er}) can close a Red case",
                             ep["status"] == "closed")
        nurse = []
        if not ep["er_ack_by"]:
            nurse.append("Acknowledge the alert")
        if _nurse_asked(ep) and not ep["nurse_callback"]:
            nurse.append(f"call her back by {_hhmm(ep['callback_due_at'])} (unconfirmed)")
        out["nurse"] = card("; ".join(nurse).capitalize(), due=ep["callback_due_at"] if _nurse_asked(ep) else None)             if nurse else card(f"Acknowledged by {_name(ep['er_ack_by'])}" +
                               (f"; called back ({_name(ep['nurse_callback']['by'])})" if ep["nurse_callback"] else ""),
                               True)
        if task and task["status"] == "open":
            out["coordinator"] = card(f"Call the family by {_hhmm(task['due_at_demo'])} and record REACHED, NOT YET, "
                                      "REFUSED or NOT REACHABLE", due=task["due_at_demo"])
        elif ep["ed_outcome"]:
            out["coordinator"] = card(f"Family call: {ep['ed_outcome'].replace('_', ' ')}"
                                      f"{' (' + ep['ed_calls'][-1]['hospital'] + ')' if ep['ed_calls'][-1].get('hospital') else ''}",
                                      True)
        elif not ep["package_sent_at"]:
            out["coordinator"] = card("Nothing until the package is sent", True)
    elif tier == "TIER_2_AMBER":
        nurse_case = _nurse_asked(ep)
        other = "AMBER_OTHER_SYMPTOM_REPORTED" in (ep["result"] or {}).get("reason_codes", [])
        out["ai"] = card(("Told her a nurse will call within 15 minutes" if nurse_case else
                          "Told her a doctor will call within 2 hours") + "; calls again 24 hours after the outcome" +
                         (f". {followup}" if followup else ""), bool(fu))
        if nurse_case:
            out["nurse"] = card(f"Called back ({_name(ep['nurse_callback']['by'])}): “{ep['nurse_callback']['note']}”",
                                True) if ep["nurse_callback"] else card(
                f"Call her back by {_hhmm(ep['callback_due_at'])}", due=ep["callback_due_at"])
        o = ep["doctor_order"]
        if o:
            out["doctor"] = card(f"Outcome logged by {_name(o['by'])}: {ORDER_LABELS[o['order']]}. Confirm closure"
                                 + (" (follow-up done)" if fu else " after the follow-up call"))
        elif not nurse_case:
            out["doctor"] = card(f"{doc}: call her by {_hhmm(ep['callback_due_at'])}, log the outcome and pick an "
                                 "order (physio or blood test)", due=ep["callback_due_at"])
        if o and o["order"] != "none":
            out["coordinator"] = card(f"Book the {ORDER_LABELS[o['order']].lower()} (task for {_name(cfg['coordinator'])})")
        elif ep["cover_alerted_at"]:
            out["coordinator"] = card(f"Nobody acted in {cfg['cover_alert_minutes']} minutes: the cover "
                                      f"({_name(cfg['cover'])}) was alerted at {_hhmm(ep['cover_alerted_at'])}")
        elif nurse_case and not ep["nurse_callback"]:
            out["coordinator"] = card("If the nurse cannot reach her in 2 attempts, tell the Doctor on Duty" +
                                      (" (she mentioned a problem we have no questions for)" if other else ""))
        elif not nurse_case and not o:
            out["coordinator"] = card(f"Book the order once the doctor picks one. If nobody acts by "
                                      f"{_hhmm(ep['result_at'] + timedelta(minutes=cfg['cover_alert_minutes']))}, the "
                                      f"cover ({_name(cfg['cover'])}) is alerted")
    else:
        r = ep["result"] or {}
        out["ai"] = card(("Repeated the doctor's signed sentence; " if r.get("repeat_advice") else "") +
                         "added a digest line" + (f"; {followup[0].lower() + followup[1:]}" if followup else ""),
                         bool(fu))
        due = _digest_due(ep, cfg)
        out["coordinator"] = card(f"Acknowledged the digest ({_name(ep['digest_ack_by'])}, {_hhmm(ep['digest_ack_at'])})",
                                  True) if ep["digest_ack_by"] else card(
            f"Acknowledge the digest by {due:%a %d %b %H:%M} (or raise it to Amber to reopen)", due=due)
    return out


def _proposed(ep: dict) -> str | None:
    """The AI only proposes; a named person closes."""
    if ep["status"] != "open" or ep["tier"] is None:
        return None
    fu = (ep["followup"] or {}).get("answer")
    if ep["tier"] == "TIER_1_RED" and ep["ed_outcome"] == "REACHED":
        return "Proposed: she reached an emergency department; the ER doctor may close the case"
    if ep["tier"] == "TIER_2_AMBER" and (ep["doctor_order"] or ep["nurse_callback"]) and fu == "BETTER":
        return "Proposed: close (outcome logged, follow-up better); the Doctor on Duty or the coordinator confirms"
    if ep["tier"] == "TIER_3_GREEN" and fu == "BETTER" and not ep["digest_ack_by"]:
        return "Proposed: close into the digest once the coordinator acknowledges it"
    return None


def _set_by(ep: dict) -> str | None:
    if ep["tier"] is None:
        return None
    if ep["tier_changes"]:
        c = ep["tier_changes"][-1]
        verb = "Raised" if RANK[c["to"]] > RANK[c["from"]] else "Lowered"
        return f"{verb} by {_name(c['by'])} at {c['at'][11:16]}: “{c['reason']}”"
    r = ep["result"] or {}
    return f"Rules ({r.get('protocol_profile', 'ROUTED')}, protocol v1.2, {r.get('protocol_status', 'DRAFT_UNSIGNED')})"


ANSWER_SHOW = {R.YES: "yes", R.NO: "no", R.UNKNOWN: "don't know", R.NO_THERMOMETER: "no thermometer"}


def summary_facts(ep: dict, h: dict) -> list[dict]:
    """The ticket's summary: one plain line per answer (linked to the moment in the recording where she said it),
    then what the record holds. Facts only: no assessment, no advice."""
    said = {}
    for t in ep["turns"]:
        if t["who"] == "patient":
            said[t["id"]] = t["i"]
    a, out = ep["answers"], []
    for q in R.questions(h, _plan(ep, h)["question_ids"]):
        if q.kind == "temperature":
            v = a["temperature_value"] if a["thermometer_used"] == R.YES else \
                R.NO_THERMOMETER if a["thermometer_used"] == R.NO else a["thermometer_used"]
            label = "Temperature today"
        else:
            v, label = a.get(q.field), R.FACT_LABELS.get(q.field, q.field)
        if v in (R.NOT_ASKED, None):
            continue
        out.append({"text": f"{label}: {ANSWER_SHOW.get(v, R._show(v) if not isinstance(v, str) else v)}",
                    "turn": said.get(q.id), "source": "her answer"})
    if ep["followup"]:
        out.append({"text": f"Follow-up call: {ep['followup']['answer'].lower()}", "turn": said.get("followup"),
                    "source": "her answer"})
    arm, head = h.get("arm_baseline"), h.get("last_headache")
    if arm:
        out.append({"text": f"Record: left arm {arm['status']} on {R.day_en(arm['date'])}", "turn": None,
                    "source": "hospital record"})
    if head:
        out.append({"text": f"Record: headache {head['score']} out of 10 on {R.day_en(head['date'])}", "turn": None,
                    "source": "hospital record"})
    return out


def _plan_view(ep: dict) -> dict | None:
    """What the AI heard (tags) and what the router built, for the call pane and the ticket."""
    p = ep["plan"]
    if not p:
        return None
    t = p.get("tags") or {}
    return {"profile": p["profile"], "question_ids": p["question_ids"], "modules": p["modules"],
            "floors": p["floors"], "tagged": p.get("tagged", False), "tags_valid": t.get("valid", False),
            "health_related": t.get("health_related"), "severity_word": t.get("severity_word"),
            "heard": [TR.TAG_LABELS[s] for s in t.get("symptoms", [])] +
                     (["“severe”"] if t.get("severity_word") == "SEVERE" else []),
            "not_asked": TR.not_asked(p)}


def _owner(ep: dict, task: dict | None, cfg: dict) -> str | None:
    """Who the ticket waits on now (the list's Owner column)."""
    st = ticket_state(ep)
    if st == "CLOSED":
        return None
    if ep["tier"] == "TIER_1_RED":
        if not ep["er_ack_by"] or _nurse_asked(ep) and not ep["nurse_callback"]:
            return "Nurse (ER Desk)"
        if task and task["status"] == "open":
            return _name(cfg["coordinator"])
        return _name(cfg["er_doctor"])
    if ep["tier"] == "TIER_2_AMBER":
        if _nurse_asked(ep) and not ep["nurse_callback"]:
            return "Nurse (ER Desk)"
        if not ep["doctor_order"] and not ep["nurse_callback"]:
            return _name(cfg["doctor_on_duty"])
        return _name(cfg["coordinator"]) if st == "CLOSURE_PROPOSED" else _name(cfg["doctor_on_duty"])
    if ep["tier"] == "TIER_3_GREEN":
        return _name(cfg["coordinator"]) if not ep["digest_ack_by"] else "CarePilot (follow-up call)"
    return "CarePilot (the call)"


def _summary(conn, ep: dict, now: datetime) -> dict:
    p = _patient(conn, ep["mrn"])
    task = conn.execute("SELECT task_id, status, due_at_demo, owner_name, cover_name, reason FROM coordinator_task "
                        "WHERE triage_episode_id = %s AND task_type = 'confirm_ed_visit' ORDER BY task_id DESC LIMIT 1",
                        (ep["episode_id"],)).fetchone()
    cover_task = conn.execute("SELECT task_id, status, due_at_demo, owner_name FROM coordinator_task WHERE "
                              "triage_episode_id = %s AND task_type = 'triage_cover_alert' ORDER BY task_id DESC LIMIT 1",
                              (ep["episode_id"],)).fetchone()
    cfg = config(conn)
    deadlines = _deadlines(ep, task, cover_task, cfg)
    doc = conn.execute("SELECT doc_id FROM triage_document WHERE episode_id = %s ORDER BY doc_id DESC LIMIT 1",
                       (ep["episode_id"],)).fetchone()
    return {
        **{k: ep[k] for k in ("episode_id", "ticket_id", "digest_ack_by", "digest_ack_at", "cover_alerted_at", "mrn", "inbound_id", "patient_words", "translation", "opened_at",
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
        "has_call": any(t["who"] == "agent" for t in ep["turns"]),  # a call took place: there is a recording
        "deadlines": deadlines, "due": deadlines[0] if deadlines else None, "next": _next_actions(ep, task, cfg),
        "proposed": _proposed(ep), "tier_set_by": _set_by(ep), "cover_task": cover_task,
        "nurse_asked": _nurse_asked(ep), "state": ticket_state(ep), "state_label": STATE_LABELS[ticket_state(ep)],
        "er_log": ep["er_log"], "reopened_at": ep["reopened_at"], "owner": _owner(ep, task, cfg),
        "plan": _plan_view(ep),
        "now": now,
    }


def episodes(conn, status: str = "open", mrn: str | None = None) -> list[dict]:
    if status not in ("open", "closed", "all"):
        raise Invalid("status must be open, closed or all")
    now = clock.get_now(conn)
    rows = conn.execute(
        "SELECT * FROM triage_episode WHERE (%(s)s = 'all' OR status = %(s)s) AND (%(m)s::text IS NULL OR mrn = %(m)s) "
        "ORDER BY episode_id DESC", {"s": status, "m": mrn}).fetchall()
    return [_summary(conn, _check_cover(conn, ep, now), now) for ep in rows]


def state(conn, episode_id: int) -> dict:
    ep = conn.execute("SELECT * FROM triage_episode WHERE episode_id = %s", (episode_id,)).fetchone()
    if ep is None:
        raise NotFound(f"Triage episode {episode_id} not found")
    h, now = history(conn, ep["mrn"]), clock.get_now(conn)
    ep = _check_cover(conn, ep, now)
    asked = {t["id"] for t in ep["turns"] if t["who"] == "agent"}
    a = ep["answers"]
    plan = _plan(ep, h)
    module_of = {q: m for m, qs in [("core", TR.CORE), ("history_arm", TR.HISTORY_ARM), *TR.MODULES.items()]
                 for q in qs}
    table = []
    for q in R.questions(h, plan["question_ids"]):
        if q.kind == "temperature":
            v = (a["temperature_value"] if a["thermometer_used"] == R.YES else
                 R.NO_THERMOMETER if a["thermometer_used"] == R.NO else a["thermometer_used"])
        else:
            v = a.get(q.field, R.NOT_ASKED)
        table.append({"id": q.id, "field": q.field, "en": q.en, "ta": q.ta, "kind": q.kind, "danger": q.danger,
                      "history": q.history, "value": v, "asked": q.id in asked, "skipped": R.skipped(q, a),
                      "module": module_of.get(q.id)})
    current = R.question(h, ep["current_q"]) if ep["current_q"] else None
    return {**_summary(conn, ep, now), "turns": ep["turns"], "answers": a, "table": table,
            "current": current.__dict__ if current else None,
            "language": _lang(_patient(conn, ep["mrn"])), "protocol": R.PROTOCOL, "scenarios": R.SCENARIO_LABELS,
            "summary": summary_facts(ep, h), "rec_offsets": ep["rec_offsets"], "tier_rule": TIER_RULE,
            "not_asked": TR.not_asked(plan)}


def turn_audio(conn, episode_id: int, index: int, cached_only: bool = False) -> tuple[bytes, str] | None:
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
    return ai.speak(conn, t["ta"] if language in R.CALL_LANGUAGES else t["en"], language, cached_only=cached_only)


# ---- The call recording: the call put back together for a doctor or coordinator to check the transcript ----
REC_RATE = 16_000          # phone quality, mono 16-bit
REC_GAP_S = 0.45           # pause between turns
REC_MAX_S = 130            # longer calls drop to 8 kHz to stay under the API's response size
REC_TTS_BUDGET_S = 10      # Bulbul lines not made yet are made within this time (the API times out at 20 s)


def _pcm(data: bytes) -> array.array | None:
    """WAV bytes -> mono 16-bit samples at REC_RATE (linear resampling); None when the clip cannot be read."""
    try:
        with wave.open(io.BytesIO(data)) as w:
            if w.getsampwidth() != 2:
                return None
            channels, rate, raw = w.getnchannels(), w.getframerate(), w.readframes(w.getnframes())
    except (wave.Error, EOFError):
        return None
    s = array.array("h")
    s.frombytes(raw[: len(raw) // 2 * 2])
    if sys.byteorder == "big":
        s.byteswap()
    if channels > 1:
        s = s[::channels]
    if rate == REC_RATE or not s:
        return s
    step, last = rate / REC_RATE, len(s) - 1
    out = array.array("h", bytes(2 * int(len(s) / step)))
    for k in range(len(out)):
        x = k * step
        j = int(x)
        a, b = s[j], s[min(j + 1, last)]
        out[k] = int(a + (b - a) * (x - j))
    return out


def _silence(seconds: float) -> array.array:
    return array.array("h", bytes(2 * int(REC_RATE * seconds)))


def _tone(seconds: float = 0.3, hz: int = 660) -> array.array:
    """The marker for an answer given on screen (button, typed or scripted words, keypad): no voice was recorded."""
    n = int(REC_RATE * seconds)
    return array.array("h", (int(2500 * math.sin(2 * math.pi * hz * k / REC_RATE)) for k in range(n)))


def recording(conn, episode_id: int, user: dict) -> bytes | None:
    """The whole call as one WAV, in call order: Bulbul's lines and her spoken answers; a short tone where she
    answered on screen and a pause where she was silent. Built from what the call already stored (the Bulbul cache
    and her recorded answers). None when there is no call audio (she did not answer, or simulated mode)."""
    ep = _load(conn, episode_id)
    out, voiced, missing, offsets = array.array("h"), 0, 0, {}
    started = time.monotonic()
    for t in ep["turns"]:
        if t["who"] == "agent":
            late = time.monotonic() - started > REC_TTS_BUDGET_S
            found = turn_audio(conn, episode_id, t["i"], cached_only=late)
            pcm = _pcm(found[0]) if found else None
            if pcm is None:
                missing += 1
                pcm = _silence(0.6)
            else:
                voiced += 1
        elif t["who"] == "patient":
            found = turn_audio(conn, episode_id, t["i"]) if t.get("audio_id") else None
            pcm = _pcm(found[0]) if found else None
            if pcm is not None:
                voiced += 1
            else:
                pcm = _silence(1.5) if t.get("how") == "no speech" else _tone()
        else:
            continue
        offsets[str(t["i"])] = round(len(out) / REC_RATE, 2)  # where this turn starts (seconds)
        out.extend(pcm)
        out.extend(_silence(REC_GAP_S))
    if not voiced:
        return None
    rate = REC_RATE
    if len(out) > REC_MAX_S * REC_RATE:
        out, rate = out[::2], REC_RATE // 2
    if sys.byteorder == "big":
        out.byteswap()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(out.tobytes())
    _update(conn, ep, rec_offsets=offsets)  # the ticket's summary lines jump to these moments
    audit.append(conn, actor_type="person", actor_name=user["user_id"], action="triage_recording_opened",
                 entity_type="triage_episode", entity_id=str(episode_id), mrn=ep["mrn"],
                 details={"seconds": round(len(out) / rate, 1), "lines_with_voice": voiced,
                          "lines_without_audio": missing})
    return buf.getvalue()


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
        src_rec = {"kind": "recording", "label": "Call recording", "episode_id": eid}
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
                          "sources": [src_call, src_rec] if any(t["who"] == "agent" for t in ep["turns"])
                          else [src_call]})
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
