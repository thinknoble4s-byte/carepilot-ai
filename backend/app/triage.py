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
4. Triage v3 (2 Nov): two people roles. The coordinator reviews Red and Amber (Confirm, Raise tier, Flag as wrong;
   not a gate); the Doctor on Duty acknowledges and closes them; the Senior Oncologist is the overdue backup.
   Red: the family message with a one-page PDF and a facts-only note for the Doctor on Duty and Senior Oncologist.
   Red (unconfirmed) is not sent by itself: the coordinator calls back within 15 minutes; a person may send it.
   Amber: the SBAR card to the Doctor on Duty and the coordinator, a 2-hour callback, the doctor's order buttons.
   Green: the doctor's own signed sentence (only when she reported nausea), a digest line; the coordinator closes.
5. Closing: a 2-sentence summary drafted from the record and the closer's recommendation, edited and submitted. No
   follow-up call. The Pre-OPD brief and the Patient journey show the episode with sources.

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
          "visit": ("Review visit (advised by the doctor)", "visit", "Oncology OPD", "DRVISIT", 7),
          "medication": None,  # her own words, sent to the patient word for word
          "bloods": ("Blood tests (brought forward)", "test", "Laboratory", "BLOODFWD", 3),
          "none": None}
ORDER_LABELS = {"physio": "Physiotherapy referral", "bloods": "Blood tests brought forward", "visit": "Advise visit",
                "medication": "Medication order", "none": "No order needed"}
DOCTOR_CALL_OUTCOMES = ("PICKED_UP", "NO_ANSWER")
ATTENDER_OUTCOMES = ("REACHED", "NOT_REACHABLE")
REVIEW_ACTIONS = {"CONFIRM": "CONFIRMED", "FLAG": "FLAGGED"}  # the coordinator's review (Raise tier is RAISED)
CONTACT_METHODS = {"PHONE": "phone", "VIDEO": "video", "IN_PERSON": "in person"}
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
    "EMPTY_NO_ANSWER": {"English": "{name} Amma sent us a message with no words in it. We called her twice, and our "
                                   "staff called her twice, and could not reach her.",
                        "Tamil": "{name_own} அம்மா எங்களுக்கு வார்த்தைகள் இல்லாத ஒரு செய்தி அனுப்பினார். நாங்கள் இரண்டு "
                                 "முறையும், எங்கள் ஊழியர்கள் இரண்டு முறையும் அழைத்தும் அவரைத் தொடர்பு கொள்ள முடியவில்லை.",
                        "Hindi": "{name_own} जी ने हमें एक संदेश भेजा जिसमें कोई शब्द नहीं था। हमने दो बार और हमारे "
                                 "कर्मचारियों ने दो बार कॉल किया, पर उनसे बात नहीं हो सकी।",
                        "Telugu": "{name_own} గారు మాకు మాటలు లేని ఒక సందేశం పంపారు. మేము రెండు సార్లు, మా సిబ్బంది రెండు "
                                  "సార్లు ఫోన్ చేసినా ఆమెను సంప్రదించలేకపోయాము."},
    "UNCONFIRMED": {"English": "{name} Amma's answers could not be confirmed as safe.",
                    "Tamil": "{name_own} அம்மாவின் பதில்கள் அவர் பாதுகாப்பாக இருக்கிறார் என்பதை உறுதிப்படுத்த முடியவில்லை.",
                    "Hindi": "{name_own} जी के जवाबों से यह पक्का नहीं हो सका कि वे सुरक्षित हैं।",
                    "Telugu": "{name_own} గారి జవాబులతో ఆమె సురక్షితంగా ఉన్నారని నిర్ధారించలేకపోయాము."},
}
ALERT_TYPE = {"CONFIRMED": "Warning signs in her answers",
              "NO_ANSWER": "Reported symptoms, then could not be reached",
              "UNCONFIRMED": "Answers could not be confirmed as safe",
              "EMPTY_NO_ANSWER": "Sent a message with no words, then could not be reached"}
REACH_OUTCOMES = ("REACHED", "NOT_REACHABLE")
REACH_NEXT = ("CLOSE", "ESCALATE")


# ---------------------------------------------------------------------------------------------------------------
# Settings and loading
# ---------------------------------------------------------------------------------------------------------------
def config(conn) -> dict:
    t = policy.settings(conn).get("voice_triage") or {}
    return {"enabled": bool(t.get("enabled")), "doctor_on_duty": t.get("doctor_on_duty"),
            "senior_oncologist": t.get("senior_oncologist"),
            "coordinator": t.get("coordinator"), "call_within_minutes": t.get("call_within_minutes", 2),
            "retry_minutes": t.get("retry_minutes", 5), "confirm_within_minutes": t.get("confirm_within_minutes", 30),
            "not_yet_again_minutes": t.get("not_yet_again_minutes", 15), "nurse_minutes": t.get("nurse_minutes", 15),
            "amber_callback_minutes": t.get("amber_callback_minutes", 120), "cover": t.get("cover"),
            "doctor_reminder_minutes": t.get("doctor_reminder_minutes", 90),
            "doctor_retry_minutes": t.get("doctor_retry_minutes", 10), "backup_doctor": t.get("backup_doctor"),
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
                 from_role: str = "patient", tags=None, tagged: bool = False,
                 empty: bool = False) -> tuple[dict, bool] | None:
    """A health message with Voice Triage on: the acknowledgement, then a call due within 2 minutes, with the
    router's plan for it (tags: the model's tags; none, or broken, ask the fixed 12). None when the patient has no
    triage history in the record (then the usual handover path runs). empty: her message had no words (Testing round
    1 Nov, Worksheet2): the same call with the fixed 12, but if nobody answers, hospital staff call before the family
    is told."""
    existing = messaging.open_triage(conn, mrn)
    if existing:
        ep = _update(conn, existing, patient_words="\n".join(x for x in (existing["patient_words"], words) if x),
                     translation="\n".join(x for x in (existing["translation"], translation) if x))
        _link_message(conn, ep, inbound)
        _audit(conn, ep, "triage_message_added", actor_type="system", actor="WhatsApp gateway",
               details={"inbound_id": inbound["inbound_id"]})
        return ep, False
    h = history(conn, mrn)
    if h is None:
        return None
    plan = TR.plan(h, tags)
    if not plan["question_ids"] or empty:  # the seed's flag says health, or no words to tag: the fixed 12
        plan = TR.plan(h, None)
    plan["tagged"] = tagged
    if empty:
        plan.update(empty=True, reason="EMPTY_MESSAGE")
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
    _link_message(conn, ep, inbound)
    _audit(conn, ep, "triage_opened", actor_type="agent", actor=AGENT,
           reason_code="empty_message_voice_triage" if empty else "health_flag_voice_triage",
           trigger_event=f"inbound_message:{inbound['inbound_id']}", outcome="call_due",
           decision_inputs={"health_flag": "unknown (empty message)" if empty else "yes", "voice_triage": True,
                            "tags": plan["tags"]},
           details={"call_due_at": _hhmm(ep["call_due_at"]), "routine_messages": "held while the episode is open",
                    "questions": len(plan["question_ids"]), "modules": plan["modules"], "floors": plan["floors"],
                    "profile": plan["profile"]})
    set_next_action(conn, mrn, f"Automated triage call due by {_hhmm(ep['call_due_at'])}; routine messages held")
    return ep, True


def _link_message(conn, ep: dict, inbound: dict) -> None:
    """Her message belongs to this episode: the ticket lists it, and the chat clears it once the episode closes."""
    conn.execute("UPDATE inbound_message SET triage_episode_id = %s WHERE inbound_id = %s",
                 (ep["episode_id"], inbound["inbound_id"]))


def _empty(ep: dict) -> bool:
    """Her message had no words (Testing round 1 Nov, Worksheet2)."""
    return bool((ep["plan"] or {}).get("empty"))


def _reaching(ep: dict) -> bool:
    """Empty message, two unanswered calls: hospital staff are trying to reach her and have not yet."""
    return ep["tier"] == "TIER_2_AMBER" and (ep["result"] or {}).get("action_code") == "STAFF_CALL_15_MIN" and \
        not any(c["outcome"] == "REACHED" for c in ep["reach_calls"])


def _orders(ep: dict) -> list[dict]:
    """The doctor's orders in this Amber round (a list; an episode from before v2 holds one order)."""
    o = ep["doctor_order"]
    return [] if not o else o if isinstance(o, list) else [o]


def _misses(ep: dict) -> int:
    return sum(1 for c in ep["doctor_calls"] if c["outcome"] == "NO_ANSWER")


def _reached(ep: dict) -> bool:
    """The duty doctor reached her, or the coordinator reached her attender."""
    return any(c["outcome"] == "PICKED_UP" for c in ep["doctor_calls"]) or \
        any(c["outcome"] == "REACHED" for c in ep["attender_calls"])


def _sla_start(ep: dict) -> datetime | None:
    return ep["sla_start"] or ep["result_at"]


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
                      floors=plan["floors"], profile=plan["profile"] or "ROUTED", empty=_empty(ep))
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
    _tell_patient(conn, ep, result)
    return state(conn, ep["episode_id"])


# Testing round 31 Oct (defects 10 and 11): after the call the patient gets the outcome on WhatsApp too: who has her
# case, when hospital staff will call, and the guardrail (if nobody calls in time, go to the nearest hospital).
OUTCOME_TEMPLATES = {"TIER_1_RED": "triage_outcome_red", "TIER_2_AMBER": "triage_outcome_amber",
                     "NURSE": "triage_outcome_nurse", "UNREACHED": "triage_outcome_unreached"}


def outcome_template(result: dict) -> str | None:
    """The WhatsApp outcome for this result: none for Green (the follow-up call is tomorrow) or when she never
    answered (the Red family message goes out instead). After an empty message nobody answered: staff will call."""
    if result["action_code"] == "STAFF_CALL_15_MIN":
        return OUTCOME_TEMPLATES["UNREACHED"]
    if result["tier"] == "TIER_3_GREEN" or result.get("red_kind") == "NO_ANSWER":
        return None
    if result.get("red_kind") == "UNCONFIRMED":
        return OUTCOME_TEMPLATES["NURSE"]
    return OUTCOME_TEMPLATES[result["tier"]]


def _tell_patient(conn, ep: dict, result: dict) -> None:
    template_id = outcome_template(result)
    if template_id is None:
        return
    msg = messaging.send_message(conn, mrn=ep["mrn"], template_id=template_id, actor_type="agent", actor=AGENT,
                                 reason_code=f"triage_outcome_{result['tier'].lower()}")
    conn.execute("UPDATE outbox_message SET triage_episode_id = %s WHERE message_id = %s",
                 (ep["episode_id"], msg["message_id"]))


RED_ALERT_TO = ["Doctor on Duty", "Care coordinator", "Senior Oncologist (banner)"]


def _act(conn, ep: dict, result: dict) -> None:
    cfg, now = config(conn), clock.get_now(conn)
    tier = result["tier"]
    if tier == "TIER_1_RED":
        _audit(conn, ep, "red_alert_created", outcome=result["red_kind"], details={"to": RED_ALERT_TO})
        if result["red_kind"] in ("CONFIRMED", "NO_ANSWER"):
            send_package(conn, ep["episode_id"], None)
        else:
            due = now + timedelta(minutes=cfg["nurse_minutes"])
            _update(conn, ep, callback_due_at=due)
            set_next_action(conn, ep["mrn"], f"Red (unconfirmed): coordinator to call back by {_hhmm(due)}; "
                                             "no family message sent")
    elif tier == "TIER_2_AMBER" and result["action_code"] == "STAFF_CALL_15_MIN":
        due = now + timedelta(minutes=cfg["nurse_minutes"])
        _update(conn, ep, callback_due_at=due)
        task = tasks.create_task(
            conn, mrn=ep["mrn"], task_type="reach_patient", owner_name=_name(cfg["coordinator"]) or None,
            actor_type="system", actor="system", dedupe_key=f"triage:{ep['episode_id']}:reach_patient",
            reason=(f"Call her by {_hhmm(due)}: her message had no words and two automated calls were not answered. "
                    "Find out how she is. Reached: close the ticket, or escalate to the Doctor on Duty. Not reachable "
                    "in 2 calls: her family gets the standard message with the PDF."))
        conn.execute("UPDATE coordinator_task SET due_at_demo = %s, due_date = %s, triage_episode_id = %s "
                     "WHERE task_id = %s", (due, now.date(), ep["episode_id"], task["task_id"]))
        _audit(conn, ep, "staff_call_task_created", outcome=result["action_code"],
               details={"to": [_name(cfg["coordinator"])], "call_due": _hhmm(due),
                        "task_id": task["task_id"], "family_message": "only if staff cannot reach her in 2 calls"})
        set_next_action(conn, ep["mrn"], f"Empty message, calls not answered: staff to call her by {_hhmm(due)}")
    elif tier == "TIER_2_AMBER":
        # Amber workflow v2: every Amber goes to the duty doctor, who has 2 hours to call her
        due = now + timedelta(minutes=cfg["amber_callback_minutes"])
        _update(conn, ep, callback_due_at=due, sla_start=now)
        doc = _name(cfg["doctor_on_duty"]) or "Doctor on duty"
        _audit(conn, ep, "sbar_card_created", outcome=result["action_code"],
               details={"to": [doc], "callback_due": _hhmm(due),
                        "reminder_at": _hhmm(now + timedelta(minutes=cfg["doctor_reminder_minutes"]))})
        set_next_action(conn, ep["mrn"], f"Amber: {doc} to call back by {_hhmm(due)}")
    else:  # triage v3: the coordinator reviews and closes it; no follow-up call
        _audit(conn, ep, "green_digest_line", outcome="GREEN_COORDINATOR_REVIEW",
               details={"doctor_sentence_repeated": result["repeat_advice"],
                        "instruction_id": result.get("repeat_instruction_id")})
        set_next_action(conn, ep["mrn"], "Green: the coordinator reviews and closes it")


def send_package(conn, episode_id: int, user: dict | None) -> dict:
    """The Red package (Part 5): family message + PDF, doctor note, audit rows. Automatic for CONFIRMED and NO
    ANSWER; for UNCONFIRMED only when a person presses "Send emergency package". Triage v3: no family-call task."""
    ep = _load(conn, episode_id)
    if ep["tier"] != "TIER_1_RED":
        raise Conflict("The emergency package is only for a Red case")
    if ep["package_sent_at"] is not None:
        raise Conflict("The emergency package has already been sent")
    if user is not None and ep["red_kind"] != "UNCONFIRMED":
        raise Conflict("This package goes out by itself")
    if ep["status"] != "open":
        raise Conflict("The episode is closed")
    _gate(ep, user)
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
    line_kind = "EMPTY_NO_ANSWER" if kind == "NO_ANSWER" and _empty(ep) else kind
    reason = {lang: line.format(name=h["name_en"], name_own=R.native(h, "name", lang))
              for lang, line in REASON_LINES[line_kind].items()}
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
    doc_on_duty = _name(cfg["doctor_on_duty"]) or "the Doctor on Duty"
    note = (f"RED alert: {h.get('title', patient['full_name'])} ({ep['mrn']}). Why: {why}. {told} {doc_on_duty} "
            f"records the outcome and closes the ticket. Alert type: {kind}"
            f"{' (empty message)' if line_kind != kind else ''}. Source: call audio and the PDF are linked on this card.")
    ep = _update(conn, ep, package_sent_at=now, package_sent_by=actor, doctor_note=note)
    _audit(conn, ep, "doctor_note_created", details={"to": ["Doctor on Duty", "Senior Oncologist"], "note": note})
    _audit(conn, ep, "emergency_package_sent", actor_type=actor_type, actor=actor, outcome=msg["status"],
           template_id="TPL_WA_RED_CAREGIVER_V1", reason_code=f"red_{kind.lower()}",
           details={"message_id": msg["message_id"], "pdf_doc_id": doc["doc_id"]})
    set_next_action(conn, ep["mrn"], f"Red: family message and PDF sent; {doc_on_duty} records the outcome and closes")
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
           ("body", f"Alert type: {ALERT_TYPE['EMPTY_NO_ANSWER' if kind == 'NO_ANSWER' and _empty(ep) else kind]}"),
           *(("body", f"- {R.RED_PLAIN[c]}") for c in result["reason_codes"] if c in R.RED_PLAIN),
           ("head", "2. What she reported today")]
    if kind == "NO_ANSWER" and _empty(ep):
        out.append(("body", "Her WhatsApp message had no words in it."))
        out.append(("body", "Two automated calls (5 minutes apart) and two calls by hospital staff were not answered. "
                            "No answers were recorded."))
    elif kind == "NO_ANSWER":
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


def _pdf(lines: list[tuple[str, str]], title: str, band: str = "0.69 0.23 0.18",
         footer: tuple[str, str] = ("Generated by CarePilot's automated assistant from stored hospital records and the "
                                    "patient's own answers.",
                                    "It lists facts and the rules that fired. It does not diagnose or advise.")) -> bytes:
    """A plain A4 PDF from (style, text) lines: title, head, body, small. A new page when one is full."""
    styles = {"title": ("F2", 13, 20), "head": ("F2", 10, 15), "body": ("F1", 9, 11.5), "small": ("F1", 7.5, 9.5)}
    width = {"title": 70, "head": 95, "body": 108, "small": 128}
    esc = lambda s: _latin(s).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")  # noqa: E731
    page_top = ["q 0.92 g BT /F2 60 Tf 0.819 0.574 -0.574 0.819 120 260 Tm (SYNTHETIC DEMO) Tj ET Q"]
    pages, ops, y = [], page_top + [f"{band} rg 50 772 495 30 re f"], 782
    for style, text in lines:
        font, size, lead = styles[style]
        for line in textwrap.wrap(text, width[style]) or [""]:
            if y < 60:
                pages.append(ops)
                ops, y = list(page_top), 800
            colour = "1 g" if style == "title" else ("0.12 0.22 0.39 rg" if style == "head" else "0 g")
            ops.append(f"BT {colour} /{font} {size} Tf 56 {y:.1f} Td ({esc(line)}) Tj ET")
            y -= lead
        if style == "title":
            y -= 8
    pages.append(ops)
    n = len(pages)
    objs = ["<< /Type /Catalog /Pages 2 0 R >>",
            "<< /Type /Pages /Kids [" + " ".join(f"{5 + 2 * i} 0 R" for i in range(n)) + f"] /Count {n} >>",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"]
    for i, page in enumerate(pages):
        page = page + [f"BT 0.33 g /F1 7.5 Tf 50 40 Td ({esc(footer[0])}) Tj ET",
                       f"BT 0.33 g /F1 7.5 Tf 50 30 Td ({esc(footer[1])}) Tj ET"] + (
            [f"BT 0.33 g /F1 7.5 Tf 500 30 Td (Page {i + 1} of {n}) Tj ET"] if n > 1 else [])
        body = "\n".join(page)
        objs += ["<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> "
                 f">> /Contents {6 + 2 * i} 0 R >>",
                 f"<< /Length {len(body.encode('latin-1'))} >>\nstream\n{body}\nendstream"]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info << /Title ({esc(title)}) >> >>\n" \
           f"startxref\n{xref}\n%%EOF\n".encode()
    return out


def summary_pdf(ep: dict, h: dict, patient: dict, sent_at: str) -> bytes:
    return _pdf(summary_lines(ep, h, patient, sent_at), "Emergency summary - SYNTHETIC")


TIER_BANDS = {"TIER_1_RED": "0.69 0.23 0.18", "TIER_2_AMBER": "0.72 0.47 0.05", "TIER_3_GREEN": "0.18 0.49 0.29"}


def outcome_pdf(conn, episode_id: int) -> bytes:
    """Testing round 2 Nov: the triage outcome PDF for any episode (Green, Amber or Red), built from the record each
    time it is opened, so it always shows the latest state. For staff only; it is never sent."""
    ep = _load(conn, episode_id)
    h, patient = history(conn, ep["mrn"]), _patient(conn, ep["mrn"])
    s = episode_summary(ep, h)
    tier = TIERS.get(ep["tier"]) or "not triaged yet"
    lines = [("title", f"Triage outcome - {ep['ticket_id']} - {tier}"),
             ("small", f"SYNTHETIC DEMO. Staff only; not sent to the patient or the family. Printed "
                       f"{_when(clock.get_now(conn))} (demo clock)."),
             ("head", "Patient"),
             ("body", f"{patient['full_name']} - MRN {ep['mrn']} - language {patient['preferred_language']}"
                      + (f" - attender {patient['caregiver_name']}" if patient["caregiver_name"] else "")),
             ("head", "Episode"),
             ("body", f"Opened {_when(ep['opened_at'])}. Tier {tier}"
                      + (f" ({ep['red_kind'].replace('_', ' ').lower()})" if ep["red_kind"] else "")
                      + f". State: {STATE_LABELS[ticket_state(ep)]}. Status: {ep['status']}.")]
    if ep["patient_words"] or ep["translation"]:
        lines += [("head", "Her message (English translation)"), ("body", ep["translation"] or ep["patient_words"])]
    if s["details"]:
        lines += [("head", "What happened")] + [("body", "- " + d) for d in s["details"]]
    if h is not None and ep["answers"]:
        lines += [("head", "Her answers and the record")] + [
            ("body", f"- {f['text']} ({f['source']})") for f in summary_facts(ep, h)]
    if ep["closing"]:
        lines[6:6] = [("head", "Closing summary"), ("body", ep["closing"]["summary"])]
    if ep["status"] == "open":
        lines += [("head", "Still open"), ("body", "This ticket is not closed yet; the PDF shows the state now.")]
    return _pdf(lines, f"Triage outcome {ep['ticket_id']} - SYNTHETIC", TIER_BANDS.get(ep["tier"], "0.35 0.38 0.42"),
                ("Generated by CarePilot from the triage record: facts, the rules that fired and what staff did.",
                 "It does not diagnose or advise. Staff only."))


# ---------------------------------------------------------------------------------------------------------------
# What people do
# ---------------------------------------------------------------------------------------------------------------
def _open(ep: dict) -> None:
    if ep["status"] != "open":
        raise Conflict("The episode is closed")


# ---- Ticket states (guide v1.2, section 7.6). The state is read from what people did; a move a role may not make,
# or one the state does not allow, is refused with a message. The agent never acknowledges or closes.
# Triage v3 (2 Nov): Open -> Acknowledged (the doctor, on Red and Amber) -> Closed; Reopened after a reopen.
STATES = ("OPEN", "ACKNOWLEDGED", "CLOSED", "REOPENED")
STATE_LABELS = {"OPEN": "Open", "ACKNOWLEDGED": "Acknowledged", "CLOSED": "Closed", "REOPENED": "Reopened"}
# who may acknowledge, by tier: Red and Amber the Doctor on Duty (any oncologist); Green is not acknowledged (the
# coordinator reviews and closes it)
WHO_MAY_ACK = {"TIER_2_AMBER": ("oncologist",), "TIER_1_RED": ("oncologist",)}
ACK_FIELD = {"TIER_2_AMBER": "coord_ack_by", "TIER_1_RED": "coord_ack_by"}


def ticket_state(ep: dict) -> str:
    if ep["status"] == "closed":
        return "CLOSED"
    if ep["tier"] in ACK_FIELD and ep[ACK_FIELD[ep["tier"]]]:
        return "ACKNOWLEDGED"
    return "REOPENED" if ep["reopened_at"] else "OPEN"


def _ack(conn, ep: dict, user: dict, now: datetime, how: str = "button") -> dict:
    """Record the doctor's acknowledgement (Red and Amber; a Green ticket is reviewed and closed instead)."""
    tier = ep["tier"]
    ep = _update(conn, ep, coord_ack_by=user["user_id"], coord_ack_at=now)
    _audit(conn, ep, "triage_acknowledged", actor_type="person", actor=user["user_id"],
           outcome="ACKNOWLEDGED", details={"role": user["role"], "tier": TIERS[tier], "how": how})
    return ep


REVIEW_FIRST = "Waiting for the coordinator's review (Confirm, Raise tier or Flag as wrong)"


def _awaits_review(ep: dict) -> bool:
    """Red and Amber reach the doctor only after the coordinator's review (no time limit). A staff call that reached
    her and escalated counts as the review."""
    return (ep["status"] == "open" and ep["tier"] in ("TIER_1_RED", "TIER_2_AMBER") and not ep["review"]
            and not _reaching(ep))


def _gate(ep: dict, user: dict | None) -> None:
    if user is not None and user["role"] == "oncologist" and _awaits_review(ep):
        raise Conflict(REVIEW_FIRST)


def _auto_ack(conn, ep: dict, user: dict) -> dict:
    """A person who logs an outcome on an unacknowledged ticket acknowledges it too (when the role may)."""
    tier = ep["tier"]
    if tier in ACK_FIELD and not ep[ACK_FIELD[tier]] and user["role"] in WHO_MAY_ACK[tier]:
        ep = _ack(conn, ep, user, clock.get_now(conn), how="with the outcome")
    return ep


def acknowledge(conn, episode_id: int, user: dict) -> dict:
    """The Doctor on Duty acknowledges a Red or Amber ticket. Green has no acknowledgement: the coordinator reviews and
    closes it."""
    ep = _load(conn, episode_id)
    _open(ep)
    tier = ep["tier"]
    if tier is None:
        raise Conflict("The call has no result yet")
    if tier not in WHO_MAY_ACK:
        raise Conflict("A Green ticket is not acknowledged: the coordinator reviews and closes it")
    if user["role"] not in WHO_MAY_ACK[tier]:
        raise Conflict("The Doctor on Duty acknowledges a Red or Amber ticket; the coordinator reviews it")
    if ep[ACK_FIELD[tier]]:
        raise Conflict(f"Already acknowledged by {_name(ep[ACK_FIELD[tier]])}")
    _gate(ep, user)
    _ack(conn, ep, user, clock.get_now(conn))
    return state(conn, episode_id)


def _callback_asked(ep: dict) -> bool:
    """Red (unconfirmed), by the rules, raised to Red by hand, or Amber not reached in 2 hours: the coordinator calls
    her back within 15 minutes (triage v3; the nurse before)."""
    return ep["tier"] == "TIER_1_RED" and ep["red_kind"] == "UNCONFIRMED"


def callback(conn, episode_id: int, user: dict, note: str | None) -> dict:
    """Red (unconfirmed): the coordinator's call back within 15 minutes, with what she found (triage v3)."""
    ep = _load(conn, episode_id)
    _open(ep)
    if not (note or "").strip():
        raise Invalid("Write what you found on the call")
    if not _callback_asked(ep):
        raise Conflict("No call back is asked for on this ticket")
    if ep["nurse_callback"]:
        raise Conflict(f"Already called back by {_name(ep['nurse_callback']['by'])}")
    now = clock.get_now(conn)
    ep = _update(conn, ep, nurse_callback={"by": user["user_id"], "at": now.isoformat(timespec="minutes"),
                                           "note": note.strip()})
    _audit(conn, ep, "staff_callback_recorded", actor_type="person", actor=user["user_id"],
           details={"note": note.strip(), "within_minutes": int((now - (ep["result_at"] or now)).total_seconds() // 60),
                    "role": user["role"]})
    set_next_action(conn, ep["mrn"], f"Red (unconfirmed): {_name(user['user_id'])} called her back; the doctor "
                                     "decides and closes")
    return state(conn, episode_id)


def reach_call(conn, episode_id: int, user: dict, outcome: str, next_step: str | None = None,
               note: str | None = None) -> dict:
    """Testing round 1 Nov (Worksheet2): the coordinator calls her after an empty message and two
    unanswered automated calls. Reached: close the ticket with what she said, or escalate to the Doctor on Duty (the
    Amber doctor callback). Not reachable twice: Red (no answer), and the family gets the standard message with the
    PDF."""
    ep = _load(conn, episode_id)
    _open(ep)
    if not _reaching(ep):
        raise Conflict("There is no staff call to log for this ticket")
    if outcome not in REACH_OUTCOMES:
        raise Invalid("outcome must be one of: " + ", ".join(REACH_OUTCOMES))
    note = (note or "").strip()
    if outcome == "REACHED":
        if next_step not in REACH_NEXT:
            raise Invalid("After reaching her, choose CLOSE or ESCALATE")
        if not note:
            raise Invalid("Write what she said on the call")
    elif next_step:
        raise Invalid("Close or escalate only after reaching her")
    cfg, now = config(conn), clock.get_now(conn)
    task = conn.execute("SELECT * FROM coordinator_task WHERE triage_episode_id = %s AND task_type = 'reach_patient' "
                        "AND status = 'open' FOR UPDATE", (episode_id,)).fetchone()
    calls = list(ep["reach_calls"]) + [{"at": now.isoformat(timespec="minutes"), "by": user["user_id"],
                                        "role": user["role"], "outcome": outcome, "next": next_step,
                                        "note": note or None}]
    ep = _auto_ack(conn, _update(conn, ep, reach_calls=calls), user)
    misses = sum(1 for c in calls if c["outcome"] == "NOT_REACHABLE")
    _audit(conn, ep, "staff_call_logged", actor_type="person", actor=user["user_id"], outcome=outcome,
           details={"attempt": len(calls), "next": next_step, "note": note or None, "role": user["role"]})

    def done(line):
        if task:
            tasks.complete_task(conn, task["task_id"], user["user_id"], line)

    if outcome == "REACHED" and next_step == "CLOSE":
        done("Reached her; ticket closed")
        return _close(conn, ep, user, f"Staff call: {note}", "closed_after_staff_call")
    if outcome == "REACHED":
        done("Reached her; escalated to the Doctor on Duty")
        due = now + timedelta(minutes=cfg["amber_callback_minutes"])
        ep = _update(conn, ep, callback_due_at=due, sla_start=now,
                     review={"action": "ESCALATED", "by": user["user_id"], "at": now.isoformat(timespec="minutes"),
                             "reason": note})
        doc = _name(cfg["doctor_on_duty"]) or "Doctor on duty"
        _audit(conn, ep, "sbar_card_created", outcome="ESCALATED_AFTER_STAFF_CALL",
               details={"to": [doc, _name(cfg["coordinator"])], "callback_due": _hhmm(due), "note": note})
        set_next_action(conn, ep["mrn"], f"Amber (escalated after the staff call): {doc} to call back by {_hhmm(due)}")
        return state(conn, episode_id)
    if misses < 2:
        again = now + timedelta(minutes=cfg["not_yet_again_minutes"])
        _update(conn, ep, callback_due_at=again)
        if task:
            conn.execute("UPDATE coordinator_task SET due_at_demo = %s WHERE task_id = %s", (again, task["task_id"]))
        set_next_action(conn, ep["mrn"], f"Staff call: not reachable; call her again by {_hhmm(again)}")
        return state(conn, episode_id)
    done("Not reachable in 2 staff calls; her family was sent the standard message with the PDF")
    h, plan = history(conn, ep["mrn"]), _plan(ep, history(conn, ep["mrn"]))
    result = R.triage(ep["answers"], h, now.date(), no_answer=True, empty=True, staff_unreached=True,
                      floors=plan["floors"], profile=plan["profile"] or "ROUTED")
    result.update(stopped_after_danger_sign=False, why=R.why_lines(result, ep["answers"], h))
    ep = _update(conn, ep, tier=result["tier"], red_kind=result["red_kind"], result=result, result_at=now,
                 callback_due_at=None)
    _audit(conn, ep, "triage_result", reason_code=result["tier"], outcome=result["action_code"],
           rule=",".join(result["reason_codes"]), policy_version=f"{result['protocol_profile']} v1.2 (DRAFT_UNSIGNED)",
           decision_inputs={"empty_message": True, "automated_calls_unanswered": ep["attempts"],
                            "staff_calls": calls},
           details={"why": result["why"], "red_kind": result["red_kind"]})
    _act(conn, ep, result)
    return state(conn, episode_id)


def review(conn, episode_id: int, user: dict, action: str, reason: str | None = None) -> dict:
    """Triage v3: the coordinator reviews the AI's Red or Amber triage: Confirm, or Flag as wrong (with why). It is the
    gate before the doctor (no time limit): the doctor acts only after it, and a flagged ticket still goes to the doctor
    for a clinical decision. Raise tier (change_tier) counts as a review too."""
    ep = _load(conn, episode_id)
    _open(ep)
    if action not in REVIEW_ACTIONS:
        raise Invalid("action must be CONFIRM or FLAG")
    if ep["tier"] not in ("TIER_1_RED", "TIER_2_AMBER"):
        raise Conflict("The coordinator reviews Red and Amber tickets; a Green ticket she closes")
    if ep["review"]:
        raise Conflict(f"Already reviewed by {_name(ep['review']['by'])} ({ep['review']['action'].lower()})")
    reason = (reason or "").strip()
    if action == "FLAG" and not reason:
        raise Invalid("Say why the AI's triage is wrong")
    now = clock.get_now(conn)
    ep = _update(conn, ep, review={"action": REVIEW_ACTIONS[action], "by": user["user_id"],
                                   "at": now.isoformat(timespec="minutes"), "reason": reason or None})
    _audit(conn, ep, "triage_reviewed", actor_type="person", actor=user["user_id"], outcome=REVIEW_ACTIONS[action],
           details={"reason": reason or None, "tier": TIERS[ep["tier"]]})
    set_next_action(conn, ep["mrn"], f"{TIERS[ep['tier']]}: {'confirmed' if action == 'CONFIRM' else 'flagged as wrong'} "
                                     f"by {_name(user['user_id'])}; with the Doctor on Duty")
    return state(conn, episode_id)


def _contact_line(c: dict) -> str:
    return CONTACT_METHODS.get(c.get("contact") or "", "")


def summary_draft(conn, episode_id: int, user: dict, recommendation: str | None = None,
                  contact: str | None = None) -> dict:
    """Triage v3: the 2-sentence closing summary, drafted from the record (what she reported, the questions and her
    answers, the tier and why) and the closer's recommendation word for word. Not written by a model; the person who
    closes edits it and submits it."""
    ep = _load(conn, episode_id)
    _gate(ep, user)
    return {"draft": _draft(conn, ep, user, (recommendation or "").strip(), contact)}


def _draft(conn, ep: dict, user: dict, recommendation: str, contact: str | None) -> str:
    h = history(conn, ep["mrn"])
    first = (h or {}).get("name_en") or _patient(conn, ep["mrn"])["full_name"].split()[0]
    p = ep["plan"] or {}
    heard = [TR.TAG_LABELS.get(t, t) for t in ((p.get("tags") or {}).get("symptoms") or [])]
    tier, result = TIERS.get(ep["tier"]) or "no tier", ep["result"] or {}
    asked = len(p.get("question_ids") or [])
    if ep["red_kind"] == "NO_ANSWER":
        found = f"CarePilot could not reach her on two calls, so the ticket is {tier}"
    else:
        why = [w.rstrip(".") for w in (result.get("why") or [])][:3]
        found = (f"CarePilot asked {asked} questions and found {tier}"
                 + (": " + "; ".join(w[0].lower() + w[1:] for w in why) if why and result.get("reason_codes") else
                    ", with no warning signs" + (f" ({R.facts_text(ep['answers'])})" if ep["answers"] else "")))
    said = f"{first} reported {', '.join(heard)}" if heard else f"{first} sent a message"
    one = f"{said}; {found}."
    by = _name(user["user_id"])
    orders = [ORDER_LABELS[o["order"]].lower() for o in _orders(ep) if o["order"] != "none"]
    how = f" after contacting her by {CONTACT_METHODS[contact]}" if contact in CONTACT_METHODS else ""
    if recommendation:
        two = f"{by} recommended{how}: “{recommendation}”" + (f" (orders: {', '.join(orders)})" if orders else "") + "."
    elif user["role"] == "coordinator":
        two = f"Reviewed by {by}: no doctor's call needed."
    else:
        two = f"Reviewed by {by}{how}" + (f"; orders: {', '.join(orders)}" if orders else "") + "."
    return f"{one} {two}"


def close(conn, episode_id: int, user: dict, *, summary: str | None, recommendation: str | None = None,
          contact: str | None = None) -> dict:
    """Triage v3: close a ticket with the summary for the Patient journey (the draft, edited by the closer). Red and
    Amber: only the Doctor on Duty (an oncologist), with her recommendation and how she contacted the patient; Amber
    once she (or her attender) was reached and the orders are recorded. Green: the coordinator (or the doctor who
    lowered it). There is no follow-up call."""
    ep = _load(conn, episode_id)
    _open(ep)
    tier = ep["tier"]
    if tier is None:
        raise Conflict("The call has no result yet")
    summary, recommendation = (summary or "").strip(), (recommendation or "").strip()
    if tier in ("TIER_1_RED", "TIER_2_AMBER"):
        if user["role"] != "oncologist":
            raise Conflict("Only the Doctor on Duty closes a Red or Amber ticket")
        _gate(ep, user)
        if not recommendation:
            raise Invalid("Write your recommendation")
        if contact not in CONTACT_METHODS:
            raise Invalid("Pick how you contacted her: PHONE, VIDEO or IN_PERSON")
        if tier == "TIER_2_AMBER" and not _reaching(ep):
            if not _reached(ep):
                raise Conflict("Log a call that reached her (or her attender) first")
            if not _orders(ep):
                raise Conflict("Record the orders first (or No order needed)")
    elif user["role"] not in ("coordinator", "oncologist"):
        raise Conflict("The coordinator closes a Green ticket")
    if contact is not None and contact not in CONTACT_METHODS:
        raise Invalid("contact must be PHONE, VIDEO or IN_PERSON")
    if not summary:
        raise Invalid("Submit the summary (edit the draft if needed)")
    ep = _auto_ack(conn, ep, user)
    now = clock.get_now(conn)
    draft = _draft(conn, ep, user, recommendation, contact)
    closing = {"by": user["user_id"], "at": now.isoformat(timespec="minutes"), "recommendation": recommendation or None,
               "contact": contact, "draft": draft, "summary": summary, "edited": summary != draft}
    ep = _update(conn, ep, closing=closing)
    _audit(conn, ep, "closing_summary_submitted", actor_type="person", actor=user["user_id"],
           outcome="EDITED" if closing["edited"] else "AS_DRAFTED",
           details={"draft": draft, "summary": summary, "recommendation": recommendation or None,
                    "contact": CONTACT_METHODS.get(contact or "")})
    return _close(conn, ep, user, summary, {"TIER_1_RED": "red_closed_by_doctor", "TIER_2_AMBER": "amber_closed_by_doctor",
                                            "TIER_3_GREEN": "green_closed_after_review"}[tier])


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
    """Any staff reopens a closed ticket, with a reason: it is Reopened until the doctor acknowledges it again (an
    open Green ticket is raised to Amber instead)."""
    ep = _load(conn, episode_id)
    if not (reason or "").strip():
        raise Invalid("Reopening needs a reason")
    if ep["status"] == "open":
        raise Conflict("The ticket is open (to send a Green ticket to the doctor, raise it to Amber)")
    if messaging.open_triage(conn, ep["mrn"]):
        raise Conflict("Another triage episode is open for this patient")
    ep = _update(conn, ep, status="open", closed_by=None, closed_at=None, close_note=None, closing=None,
                 reopened_at=clock.get_now(conn), coord_ack_by=None, coord_ack_at=None, er_ack_by=None, er_ack_at=None,
                 digest_ack_by=None, digest_ack_at=None)
    _audit(conn, ep, "triage_reopened", actor_type="person", actor=user["user_id"], outcome="REOPENED",
           details={"reason": reason.strip()})
    set_next_action(conn, ep["mrn"], "Triage episode reopened: " + reason.strip())
    if ep["tier"] == "TIER_2_AMBER":  # Amber workflow v2: a new 2-hour round for the duty doctor
        _new_amber_round(conn, ep, "Reopened: " + reason.strip())
    return state(conn, episode_id)


def doctor_call(conn, episode_id: int, user: dict, outcome: str, note: str | None = None) -> dict:
    """Amber workflow v2: the duty doctor (or the backup) logs each call to her. Two attempts, 10 minutes apart; after
    the second unanswered one the coordinator calls her attender."""
    ep = _load(conn, episode_id)
    _open(ep)
    if ep["tier"] != "TIER_2_AMBER" or _reaching(ep):
        raise Conflict("The doctor's call is logged on an Amber ticket")
    if outcome not in DOCTOR_CALL_OUTCOMES:
        raise Invalid("outcome must be one of: " + ", ".join(DOCTOR_CALL_OUTCOMES))
    if _reached(ep):
        raise Conflict("She has been reached: record the orders, then close with your recommendation")
    if outcome == "NO_ANSWER" and _misses(ep) >= 2:
        raise Conflict("Two attempts are logged: the coordinator is calling her attender")
    _gate(ep, user)
    ep = _auto_ack(conn, ep, user)
    cfg, now = config(conn), clock.get_now(conn)
    calls = list(ep["doctor_calls"]) + [{"at": now.isoformat(timespec="minutes"), "by": user["user_id"],
                                         "outcome": outcome, "note": (note or "").strip() or None}]
    ep = _update(conn, ep, doctor_calls=calls)
    _audit(conn, ep, "doctor_call_attempt", actor_type="person", actor=user["user_id"], outcome=outcome,
           details={"attempt": len(calls), "note": (note or "").strip() or None,
                    "within_callback": bool(ep["callback_due_at"] and now <= ep["callback_due_at"])})
    overdue = conn.execute("SELECT task_id FROM coordinator_task WHERE triage_episode_id = %s AND task_type = "
                           "'triage_overdue' AND status = 'open'", (episode_id,)).fetchone()
    if overdue:  # the escalation is answered: a doctor is calling her
        tasks.complete_task(conn, overdue["task_id"], user["user_id"], f"{_name(user['user_id'])} called her")
    who = _name(user["user_id"])
    if outcome == "PICKED_UP":
        set_next_action(conn, ep["mrn"], f"Amber: {who} reached her; orders and the closing summary next")
    elif _misses(ep) == 1:
        again = now + timedelta(minutes=cfg["doctor_retry_minutes"])
        set_next_action(conn, ep["mrn"], f"Amber: no answer; {who} tries again by {_hhmm(again)}")
    else:
        p = _patient(conn, ep["mrn"])
        attender = p["caregiver_name"] or "her attender"
        task = tasks.create_task(
            conn, mrn=ep["mrn"], task_type="call_attender", owner_name=_name(cfg["coordinator"]) or None,
            actor_type="system", actor="system", dedupe_key=f"triage:{ep['episode_id']}:call_attender",
            reason=(f"{who} could not reach her in 2 calls. Call {attender} before {_hhmm(ep['callback_due_at'])} and "
                    "record REACHED (with what they said) or NOT REACHABLE. If she is still not reached by then, the "
                    "ticket goes to Red (unconfirmed)."))
        conn.execute("UPDATE coordinator_task SET due_at_demo = %s, due_date = %s, triage_episode_id = %s "
                     "WHERE task_id = %s", (ep["callback_due_at"], now.date(), ep["episode_id"], task["task_id"]))
        _audit(conn, ep, "attender_call_task_created", outcome="call_attender",
               details={"task_id": task["task_id"], "attender": attender})
        set_next_action(conn, ep["mrn"], f"Amber: doctor's 2 calls not answered; coordinator to call {attender}")
    return state(conn, episode_id)


def attender_call(conn, episode_id: int, user: dict, outcome: str, note: str | None = None) -> dict:
    """Amber workflow v2: the coordinator calls her attender after the doctor's two unanswered calls."""
    ep = _load(conn, episode_id)
    _open(ep)
    task = conn.execute("SELECT * FROM coordinator_task WHERE triage_episode_id = %s AND task_type = 'call_attender' "
                        "AND status = 'open' FOR UPDATE", (episode_id,)).fetchone()
    if task is None:
        raise Conflict("There is no 'call the attender' task for this ticket")
    if outcome not in ATTENDER_OUTCOMES:
        raise Invalid("outcome must be one of: " + ", ".join(ATTENDER_OUTCOMES))
    note = (note or "").strip()
    if outcome == "REACHED" and not note:
        raise Invalid("Write what the attender said")
    now = clock.get_now(conn)
    calls = list(ep["attender_calls"]) + [{"at": now.isoformat(timespec="minutes"), "by": user["user_id"],
                                           "outcome": outcome, "note": note or None}]
    ep = _update(conn, ep, attender_calls=calls)
    line = "Attender reached" if outcome == "REACHED" else "Attender not reachable"
    tasks.complete_task(conn, task["task_id"], user["user_id"], line + (f": {note}" if note else ""))
    _audit(conn, ep, "attender_call_logged", actor_type="person", actor=user["user_id"], outcome=outcome,
           details={"note": note or None})
    doc = _name(config(conn)["doctor_on_duty"]) or "the duty doctor"
    set_next_action(conn, ep["mrn"], f"Amber: attender reached; {doc} to record the orders and close"
                    if outcome == "REACHED" else
                    f"Amber: attender not reachable; Red (unconfirmed) at {_hhmm(ep['callback_due_at'])} if she is "
                    "still not reached")
    return state(conn, episode_id)


def doctor_order(conn, episode_id: int, user: dict, order: str, note: str | None = None) -> dict:
    """The duty doctor, once she (or her attender) is reached: Physiotherapy referral, Bring forward blood tests or
    Advise visit (each an order card, the approved booking message and a booking task for the coordinator), a
    Medication order (the doctor's own words, sent to the patient word for word; the AI writes nothing), or No order
    needed. Several orders may be recorded; the doctor then closes with her recommendation."""
    ep = _load(conn, episode_id)
    _open(ep)
    if order not in ORDERS:
        raise Invalid("order must be one of: " + ", ".join(ORDERS))
    if ep["tier"] != "TIER_2_AMBER":
        raise Conflict("Orders are recorded on an Amber card")
    _gate(ep, user)
    if _reaching(ep):
        raise Conflict("Hospital staff have not reached her yet")
    if not _reached(ep):
        raise Conflict("Log a call that reached her (or her attender) before an order")
    orders, text = _orders(ep), (note or "").strip()
    if any(o["order"] == "none" for o in orders) or (order == "none" and orders):
        raise Conflict("“No order needed” cannot go with other orders")
    if order != "medication" and any(o["order"] == order for o in orders):
        raise Conflict(f"{ORDER_LABELS[order]} is already ordered")
    if order == "medication" and not text:
        raise Invalid("Type the medication order: it is sent to her word for word")
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
                          reason=f"Book the {name} by {card['due_date'].strftime('%d/%m/%Y')} (duty doctor's order)")
        msg = messaging.send_message(conn, mrn=ep["mrn"], template_id="instruction_booking", actor_type="agent",
                                     actor=AGENT, reason_code="triage_doctor_order", card_id=card["card_id"])
        conn.execute("UPDATE outbox_message SET triage_episode_id = %s WHERE message_id = %s",
                     (episode_id, msg["message_id"]))
    elif order == "medication":
        msg = messaging.send_message(conn, mrn=ep["mrn"], template_id="triage_doctor_text", actor_type="person",
                                     actor=user["user_id"], reason_code="triage_medication_order",
                                     extra={"doctor": _name(user["user_id"]), "order_text": text})
        conn.execute("UPDATE outbox_message SET triage_episode_id = %s WHERE message_id = %s",
                     (episode_id, msg["message_id"]))
    orders.append({"order": order, "text": text or None, "by": user["user_id"],
                   "at": now.isoformat(timespec="minutes"), "card_id": card["card_id"] if card else None})
    ep = _update(conn, ep, doctor_order=orders)
    _audit(conn, ep, "doctor_order_recorded", actor_type="person", actor=user["user_id"], outcome=order,
           details={"text": text or None, "card_id": card["card_id"] if card else None,
                    "sent_to_patient": "word for word" if order == "medication" else
                    "booking message" if card else None})
    set_next_action(conn, ep["mrn"], f"Amber: {ORDER_LABELS[order]} ordered; the doctor closes with her "
                                     "recommendation")
    return state(conn, episode_id)


RANK = {"TIER_3_GREEN": 0, "TIER_2_AMBER": 1, "TIER_1_RED": 2}
TIER_RULE = ("The coordinator can only raise the tier (that counts as her review). Only the Doctor on Duty can lower "
             "it, after the review. A reason is always needed.")


def change_tier(conn, episode_id: int, user: dict, tier: str, reason: str | None) -> dict:
    """The rules set the tier; a person may change it, always with a reason. The coordinator may only raise it (her
    review: it then goes to the doctor); only the Doctor on Duty lowers it, after the review. Raised to Red by hand it is Red
    (unconfirmed): the coordinator calls back within 15 minutes; the family message goes only when a person sends it."""
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
    _gate(ep, user)
    cfg, now = config(conn), clock.get_now(conn)
    raising = RANK[tier] > RANK[ep["tier"]]
    if not raising and user["role"] != "oncologist":
        raise Conflict("Only the Doctor on Duty lowers the tier; the coordinator can only raise it")
    changes = list(ep["tier_changes"]) + [{"from": ep["tier"], "to": tier, "by": user["user_id"], "role": user["role"],
                                           "at": now.isoformat(timespec="minutes"), "reason": reason.strip()}]
    fields = {"tier": tier, "tier_changes": changes}
    if raising and user["role"] == "coordinator" and not ep["review"]:  # her review: to the doctor at once
        fields["review"] = {"action": "RAISED", "by": user["user_id"], "at": now.isoformat(timespec="minutes"),
                            "reason": reason.strip()}
    if tier == "TIER_1_RED":
        fields.update(red_kind="UNCONFIRMED", callback_due_at=now + timedelta(minutes=cfg["nurse_minutes"]))
    elif tier == "TIER_2_AMBER":
        fields.update(red_kind=None, callback_due_at=now + timedelta(minutes=cfg["amber_callback_minutes"]),
                      sla_start=now, reminded_at=None, overdue_at=None)
    else:
        fields.update(red_kind=None, callback_due_at=None)
    ep = _update(conn, ep, **fields)
    _audit(conn, ep, "tier_raised" if raising else "tier_lowered", actor_type="person", actor=user["user_id"],
           outcome=f"{TIERS[changes[-1]['from']]} -> {TIERS[tier]}",
           details={"reason": reason.strip(), "role": user["role"]})
    who, verb = _name(user["user_id"]), "raised" if raising else "lowered"
    if tier == "TIER_1_RED":
        _audit(conn, ep, "red_alert_created", outcome="UNCONFIRMED", details={"to": RED_ALERT_TO, "raised_by": who})
        set_next_action(conn, ep["mrn"], f"Red (raised by {who}): coordinator to call back by "
                                         f"{_hhmm(ep['callback_due_at'])}; no family message sent")
    elif tier == "TIER_2_AMBER":
        set_next_action(conn, ep["mrn"], f"Amber ({verb} by {who}): {_name(cfg['doctor_on_duty']) or 'Doctor on duty'} "
                                         f"to call back by {_hhmm(ep['callback_due_at'])}")
    else:
        set_next_action(conn, ep["mrn"], f"{TIERS[tier]} ({verb} by {who})")
    return state(conn, episode_id)


def _digest_due(ep: dict, cfg: dict) -> datetime | None:
    """Green: the coordinator reviews and closes it by the end of the next working day (Monday to Saturday)."""
    if ep["result_at"] is None:
        return None
    d = ep["result_at"].date() + timedelta(days=1)
    while d.weekday() == 6:
        d += timedelta(days=1)
    return datetime.combine(d, datetime.min.time()).replace(hour=cfg["digest_ack_hour"])


def _check_sla(conn, ep: dict, now: datetime) -> dict:
    """Amber workflow v2, read on the demo clock: at 90 minutes with no acknowledgement a reminder to the duty doctor;
    at 120 minutes with no call logged the ticket is Overdue (backup doctor and Senior Oncologist alerted, the
    coordinator gets a task); at 120 minutes with calls logged but nobody reached, Red (unconfirmed)."""
    if ep["status"] != "open" or ep["tier"] != "TIER_2_AMBER" or _reaching(ep) or _sla_start(ep) is None:
        return ep
    cfg, start = config(conn), _sla_start(ep)
    doc = _name(cfg["doctor_on_duty"]) or "the duty doctor"
    if not ep["coord_ack_by"] and not ep["reminded_at"] and now >= start + timedelta(minutes=cfg["doctor_reminder_minutes"]):
        ep = _update(conn, ep, reminded_at=now)
        _audit(conn, ep, "doctor_reminder_sent", outcome="not_acknowledged",
               details={"to": doc, "minutes": cfg["doctor_reminder_minutes"],
                        "callback_due": _hhmm(ep["callback_due_at"])})
        set_next_action(conn, ep["mrn"], f"Amber: not acknowledged in {cfg['doctor_reminder_minutes']} minutes; "
                                         f"{doc} reminded")
    if now < start + timedelta(minutes=cfg["amber_callback_minutes"]) or _reached(ep):
        return ep
    if ep["doctor_calls"]:  # the doctor tried and nobody reached her in the 2 hours: Red (unconfirmed)
        reason = "Not reached by the end of the 2-hour callback (the doctor's calls were not answered)"
        changes = list(ep["tier_changes"]) + [{"from": "TIER_2_AMBER", "to": "TIER_1_RED", "by": "system",
                                               "role": "rules", "at": now.isoformat(timespec="minutes"),
                                               "reason": reason}]
        ep = _update(conn, ep, tier="TIER_1_RED", red_kind="UNCONFIRMED", tier_changes=changes,
                     callback_due_at=now + timedelta(minutes=cfg["nurse_minutes"]))
        _audit(conn, ep, "tier_raised", outcome="Amber -> Red", reason_code="AMBER_PATIENT_UNREACHED",
               details={"reason": reason, "doctor_calls": len(ep["doctor_calls"]),
                        "attender_calls": [c["outcome"] for c in ep["attender_calls"]]})
        _audit(conn, ep, "red_alert_created", outcome="UNCONFIRMED", details={"to": RED_ALERT_TO, "raised_by": "rules"})
        set_next_action(conn, ep["mrn"], "Red (unconfirmed): not reached in 2 hours; the coordinator calls back, and "
                                         "the doctor or the coordinator may send the emergency package")
        return ep
    if not ep["overdue_at"]:
        backup = _name(cfg["backup_doctor"] or cfg["senior_oncologist"]) or "the backup doctor"
        senior = _name(cfg["senior_oncologist"]) or "the Senior Oncologist"
        ep = _update(conn, ep, overdue_at=now)
        task = tasks.create_task(
            conn, mrn=ep["mrn"], task_type="triage_overdue", owner_name=_name(cfg["coordinator"]) or None,
            actor_type="system", actor="system", dedupe_key=f"triage:{ep['episode_id']}:overdue",
            reason=(f"Amber ticket {ep['ticket_id']} is OVERDUE: no doctor's call logged in "
                    f"{cfg['amber_callback_minutes'] // 60} hours. {backup} (backup) and {senior} (Senior Oncologist) "
                    "were alerted; make sure one of them calls her now."))
        conn.execute("UPDATE coordinator_task SET due_at_demo = %s, due_date = %s, triage_episode_id = %s "
                     "WHERE task_id = %s", (now, now.date(), ep["episode_id"], task["task_id"]))
        _audit(conn, ep, "amber_overdue_escalated", outcome="OVERDUE",
               details={"to": [backup + " (backup)", senior + " (Senior Oncologist)", _name(cfg["coordinator"])],
                        "task_id": task["task_id"]})
        set_next_action(conn, ep["mrn"], f"Amber OVERDUE: no doctor's call in 2 hours; {backup} and {senior} alerted")
    return ep


def _new_amber_round(conn, ep: dict, why: str) -> dict:
    """A reopened Amber ticket: a new 2-hour round for the duty doctor. The round that ended is kept on the ticket."""
    cfg, now = config(conn), clock.get_now(conn)
    ended = {"ended_at": now.isoformat(timespec="minutes"), "why": why, "acknowledged_by": ep["coord_ack_by"],
             "doctor_calls": ep["doctor_calls"], "attender_calls": ep["attender_calls"], "orders": _orders(ep)}
    ep = _update(conn, ep, rounds=list(ep["rounds"]) + [ended], doctor_calls=[], attender_calls=[], doctor_order=None,
                 coord_ack_by=None, coord_ack_at=None, reminded_at=None, overdue_at=None, sla_start=now,
                 reopened_at=now, callback_due_at=now + timedelta(minutes=cfg["amber_callback_minutes"]))
    _audit(conn, ep, "amber_round_reopened", outcome="REOPENED",
           details={"why": why, "round": len(ep["rounds"]) + 1, "callback_due": _hhmm(ep["callback_due_at"])})
    set_next_action(conn, ep["mrn"], f"{why}: reopened as Amber; {_name(cfg['doctor_on_duty']) or 'duty doctor'} "
                                     f"to call back by {_hhmm(ep['callback_due_at'])}")
    return ep


# ---------------------------------------------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------------------------------------------
# ---- The Triage Room ticket: deadlines, each role's next action, the facts summary ----
def _at(d) -> datetime:
    """A follow-up day as a time: the call goes out at 09:00."""
    return datetime.combine(d, datetime.min.time()).replace(hour=9)


def _deadlines(ep: dict, task: dict | None, cfg: dict) -> list[dict]:
    """Every open deadline on the ticket: {at, what, role}. The countdown shows the nearest."""
    if ep["status"] != "open":
        return []
    out = []
    add = lambda at, what, role: at and out.append({"at": at, "what": what, "role": role})  # noqa: E731
    if ep["call_status"] == "due":
        add(ep["call_due_at"], "Automated call", "ai")
    tier = ep["tier"]
    if tier == "TIER_1_RED":
        if _callback_asked(ep) and not ep["nurse_callback"]:
            add(ep["callback_due_at"], "Coordinator call back (15 minutes)", "coordinator")
    elif tier == "TIER_2_AMBER" and _reaching(ep):
        add(ep["callback_due_at"], "Staff call: reach her", "coordinator")
    elif tier == "TIER_2_AMBER":
        start = _sla_start(ep)
        if not ep["coord_ack_by"] and start:
            add(start + timedelta(minutes=cfg["doctor_reminder_minutes"]), "Doctor: acknowledge", "doctor")
        if not _reached(ep):
            if _misses(ep) == 1:
                last = datetime.fromisoformat(ep["doctor_calls"][-1]["at"])
                add(last + timedelta(minutes=cfg["doctor_retry_minutes"]), "Doctor: second call attempt", "doctor")
            add(ep["callback_due_at"], "Doctor callback (2 hours)", "doctor")
    elif tier == "TIER_3_GREEN":
        add(_digest_due(ep, cfg), "Review and close", "coordinator")
    return sorted(out, key=lambda d: d["at"])


def _review_text(ep: dict) -> str | None:
    r = ep["review"]
    if not r:
        return None
    return {"CONFIRMED": f"Confirmed by {_name(r['by'])}",
            "FLAGGED": f"Flagged as wrong by {_name(r['by'])}: “{r['reason']}”",
            "RAISED": f"Raised by {_name(r['by'])}: “{r['reason']}”",
            "ESCALATED": f"Escalated by {_name(r['by'])} after her call: “{r['reason']}”"}[r["action"]]


def _next_actions(ep: dict, task: dict | None, cfg: dict, open_types=frozenset()) -> dict:
    """One card per role (AI, doctor, coordinator): what is due, or done, for this colour. Facts only. Triage v3: two
    people roles; the coordinator reviews (not a gate), the Doctor on Duty closes Red and Amber."""
    none = {"text": "Nothing", "done": True}
    card = lambda text, done=False, due=None: {"text": text, "done": done, "due_at": due}  # noqa: E731
    tier, doc = ep["tier"], _name(cfg["doctor_on_duty"]) or "The Doctor on Duty"
    out = {"ai": none, "doctor": none, "coordinator": none}
    if tier is None:
        n = len((ep["plan"] or {}).get("question_ids") or TR.FALLBACK)
        out["ai"] = card(f"Call her by {_hhmm(ep['call_due_at'])} ({n} questions in her language)"
                         if ep["call_status"] == "due" else "On the call now", due=ep["call_due_at"])
        return out
    if ep["status"] == "closed":
        closer = USERS_BY_ID.get(ep["closed_by"] or "", {}).get("role")
        done = card(f"Closed by {_name(ep['closed_by'])}: “{ep['close_note']}”", True)
        out["doctor" if closer == "oncologist" else "coordinator"] = done
        return out
    review = _review_text(ep)
    coord = [] if review or tier == "TIER_3_GREEN" or _reaching(ep) else [
        "Review the AI's triage: Confirm, Raise tier or Flag as wrong (the doctor acts after your review)"]
    coord_due = None
    if tier == "TIER_1_RED":
        out["ai"] = card(f"Sent the family the message and PDF and wrote the doctor note at {_hhmm(ep['package_sent_at'])}",
                         True) if ep["package_sent_at"] else card(
            "No family message: the answers are unconfirmed. The coordinator calls her back; a person may send the "
            "package")
        steps = ([] if ep["coord_ack_by"] else ["acknowledge"]) + ["record your recommendation and close"]
        if _callback_asked(ep) and not ep["package_sent_at"]:
            steps.append("or send the emergency package")
        text = "; ".join(steps)
        out["doctor"] = card(f"{doc}: {text[0].upper()}{text[1:]}")
        if _callback_asked(ep) and not ep["nurse_callback"]:
            coord.insert(0, f"Call her back by {_hhmm(ep['callback_due_at'])} (unconfirmed) and record what you found"
                         + ("; send the emergency package if needed" if not ep["package_sent_at"] else ""))
            coord_due = ep["callback_due_at"]
        elif ep["nurse_callback"]:
            coord.append(f"Called back ({_name(ep['nurse_callback']['by'])}): “{ep['nurse_callback']['note']}”")
    elif tier == "TIER_2_AMBER" and _reaching(ep):
        missed = sum(1 for c in ep["reach_calls"] if c["outcome"] == "NOT_REACHABLE")
        out["ai"] = card("Her message had no words and two automated calls were not answered. Told her on WhatsApp "
                         "that hospital staff will call within 15 minutes; no family message yet", True)
        coord = [f"Call her by {_hhmm(ep['callback_due_at'])}" +
                 (" (second try; the first was not reachable)" if missed else "") +
                 ". Reached: close the ticket, or escalate to the doctor. Not reachable twice: her family gets the "
                 "standard message with the PDF"]
        coord_due = ep["callback_due_at"]
    elif tier == "TIER_2_AMBER":
        orders, misses = _orders(ep), _misses(ep)
        staff = next((c for c in ep["reach_calls"] if c["outcome"] == "REACHED"), None)
        out["ai"] = card(f"Empty message, calls not answered; {_name(staff['by'])} reached her and escalated to the "
                         "doctor" if staff else "Told her a doctor will call within 2 hours, with the emergency line",
                         True)
        late = "OVERDUE. " if ep["overdue_at"] and not ep["doctor_calls"] else ""
        if _reached(ep):
            out["doctor"] = card(late + ("Record the orders (physiotherapy, blood tests, medication, visit or none), "
                                         "then close with your recommendation and the summary" if not orders else
                                         "Ordered: " + ", ".join(ORDER_LABELS[o["order"]] for o in orders) +
                                         ". Close with your recommendation and the summary"))
        else:
            steps = ([] if ep["coord_ack_by"] else ["acknowledge"]) + (
                [f"call her by {_hhmm(ep['callback_due_at'])}"] if misses == 0 else
                [f"call again (the first call was not answered); by {_hhmm(ep['callback_due_at'])}"] if misses == 1 else
                [f"two calls not answered: the coordinator calls her attender. Not reached by "
                 f"{_hhmm(ep['callback_due_at'])}: Red (unconfirmed)"])
            text = "; ".join(steps)
            out["doctor"] = card(late + (f"{doc}: " if not late else "") + text[0].upper() + text[1:],
                                 due=ep["callback_due_at"])
        bookings = [ORDER_LABELS[o["order"]].lower() for o in orders if ORDERS.get(o["order"])]
        if "call_attender" in open_types:
            coord.insert(0, f"Call her attender ({ep.get('caregiver_name') or 'family'}) before "
                            f"{_hhmm(ep['callback_due_at'])}; record reached or not reachable")
            coord_due = ep["callback_due_at"]
        elif ep["overdue_at"] and not ep["doctor_calls"]:
            coord.insert(0, "OVERDUE: no doctor's call in 2 hours. The backup doctor and the Senior Oncologist were "
                            "alerted; make sure one of them calls her")
        if bookings:
            coord.append(f"Book the {', '.join(bookings)} (task for {_name(cfg['coordinator'])})")
    else:
        r = ep["result"] or {}
        out["ai"] = card(("Repeated the doctor's signed sentence; " if r.get("repeat_advice") else "") +
                         "added a digest line for the coordinator", True)
        due = _digest_due(ep, cfg)
        coord = [f"Review and close by {due:%a %d %b %H:%M} (or raise it to Amber for the doctor)"]
        coord_due = due
    if coord:
        out["coordinator"] = card("; ".join(coord), due=coord_due)
    elif review:
        out["coordinator"] = card(review, True)
    if _awaits_review(ep):  # the gate: the doctor's steps wait (her card says so; it does not ask her to act)
        out["doctor"] = {"text": f"{doc}: {REVIEW_FIRST[0].lower()}{REVIEW_FIRST[1:]}", "done": False, "due_at": None,
                         "waiting": True}
    return out


def _proposed(ep: dict) -> str | None:
    """Triage v3: nothing is proposed for closing any more (no follow-up call); a named person closes."""
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


def _pending(ep: dict, task: dict | None, cfg: dict, open_types, nxt: dict) -> list[dict]:
    """Testing round 2 Nov: who the ticket waits on, as persona and name ({persona, user_id, name, what, view}), so a
    tester can switch to that persona. While CarePilot AI waits (the call), the switch goes to the coordinator, who
    runs the phone simulator. Triage v3: two people roles (the Senior Oncologist only as the overdue backup)."""
    if ep["status"] == "closed":
        return []
    coord, doc, senior = cfg["coordinator"], cfg["doctor_on_duty"], cfg["senior_oncologist"]

    def who(persona, uid, view):
        return {"persona": persona, "user_id": uid, "name": _name(uid) if uid else persona, "view": view,
                "what": (nxt.get(view) or {}).get("text")}

    tier = ep["tier"]
    if tier is None:
        return [{"persona": "CarePilot AI", "user_id": coord, "name": "automated assistant", "view": "ai", "ai": True,
                 "what": nxt["ai"]["text"]}]
    coordinator = who("Care coordinator", coord, "coordinator")
    coord_waits = not nxt["coordinator"]["done"]
    if tier == "TIER_3_GREEN":
        return [coordinator]
    if tier == "TIER_2_AMBER" and _reaching(ep):
        return [coordinator]
    doctor = who("Doctor on Duty", doc, "doctor")
    if tier == "TIER_2_AMBER" and ep["overdue_at"] and not ep["doctor_calls"]:
        backup = cfg["backup_doctor"] or senior
        doctor = who("Senior Oncologist (backup)" if backup == senior else "Backup doctor", backup, "doctor")
    if _awaits_review(ep):  # the coordinator's review comes first
        return [coordinator]
    first_coord = coord_waits and (
        open_types & {"call_attender"} or (_callback_asked(ep) and not ep["nurse_callback"]))
    out = [coordinator, doctor] if first_coord else [doctor] + ([coordinator] if coord_waits else [])
    return out


def _summary(conn, ep: dict, now: datetime) -> dict:
    p = _patient(conn, ep["mrn"])
    task = conn.execute("SELECT task_id, status, due_at_demo, owner_name, cover_name, reason FROM coordinator_task "
                        "WHERE triage_episode_id = %s AND task_type = 'confirm_ed_visit' ORDER BY task_id DESC LIMIT 1",
                        (ep["episode_id"],)).fetchone()
    open_types = frozenset(r["task_type"] for r in conn.execute(
        "SELECT task_type FROM coordinator_task WHERE triage_episode_id = %s AND status = 'open'",
        (ep["episode_id"],)).fetchall())
    cfg = config(conn)
    deadlines = _deadlines(ep, task, cfg)
    doc = conn.execute("SELECT doc_id FROM triage_document WHERE episode_id = %s ORDER BY doc_id DESC LIMIT 1",
                       (ep["episode_id"],)).fetchone()
    nxt = _next_actions({**ep, "caregiver_name": p["caregiver_name"]}, task, cfg, open_types)
    pending = _pending(ep, task, cfg, open_types, nxt)
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
                                 and not _reached(ep) and not ep["nurse_callback"]),
        "task": task, "task_overdue": bool(task and task["status"] == "open" and task["due_at_demo"]
                                           and now > task["due_at_demo"]),
        "pdf_doc_id": doc["doc_id"] if doc else None,
        "has_call": any(t["who"] == "agent" for t in ep["turns"]),  # a call took place: there is a recording
        "deadlines": deadlines, "due": deadlines[0] if deadlines else None,
        "next": nxt, "pending": pending, "owner": pending[0]["name"] if pending else None,
        "proposed": None, "tier_set_by": _set_by(ep), "open_tasks": sorted(open_types),
        "review": ep["review"], "review_text": _review_text(ep), "closing": ep["closing"],
        "awaits_review": _awaits_review(ep),
        "orders": _orders(ep), "reached": _reached(ep), "overdue": bool(ep["overdue_at"] and ep["status"] == "open" and not ep["doctor_calls"]),
        "callback_asked": _callback_asked(ep), "state": ticket_state(ep), "state_label": STATE_LABELS[ticket_state(ep)],
        "reopened_at": ep["reopened_at"],
        **{k: ep[k] for k in ("doctor_calls", "attender_calls", "sla_start", "reminded_at", "overdue_at", "rounds")},
        "plan": _plan_view(ep),
        "reach_calls": ep["reach_calls"], "reaching": _reaching(ep), "empty_message": _empty(ep),
        "now": now,
    }


def episodes(conn, status: str = "open", mrn: str | None = None) -> list[dict]:
    if status not in ("open", "closed", "all"):
        raise Invalid("status must be open, closed or all")
    now = clock.get_now(conn)
    rows = conn.execute(
        "SELECT * FROM triage_episode WHERE (%(s)s = 'all' OR status = %(s)s) AND (%(m)s::text IS NULL OR mrn = %(m)s) "
        "ORDER BY episode_id DESC", {"s": status, "m": mrn}).fetchall()
    return [_summary(conn, _check_sla(conn, ep, now), now) for ep in rows]


def state(conn, episode_id: int) -> dict:
    ep = conn.execute("SELECT * FROM triage_episode WHERE episode_id = %s", (episode_id,)).fetchone()
    if ep is None:
        raise NotFound(f"Triage episode {episode_id} not found")
    h, now = history(conn, ep["mrn"]), clock.get_now(conn)
    ep = _check_sla(conn, ep, now)
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
            "summary": summary_facts(ep, h), "rec_offsets": ep["rec_offsets"], "rec_spans": rec_spans(ep),
            "tier_rule": TIER_RULE, "not_asked": TR.not_asked(plan), "patient_notes": patient_notes(conn, ep),
            "previous": previous_episode(conn, ep)}


def rec_spans(ep: dict) -> dict:
    """For each answer in the summary: the part of the recording to play, from the question she was asked to the end
    of her answer (testing round 31 Oct, defect 3: play only that question, then stop). {answer turn: [start, end]}."""
    offs = ep["rec_offsets"] or {}
    ends = offs.get("ends") or {}
    spans, asked = {}, None
    for t in ep["turns"]:
        i = str(t["i"])
        if t["who"] == "agent" and i in offs:
            asked = i
        elif t["who"] == "patient" and i in offs and i in ends:
            spans[i] = [offs[asked] if asked is not None else offs[i], ends[i]]
    return spans


def patient_notes(conn, ep: dict) -> list[dict]:
    """Her messages in this episode (the one that opened it and any she sent while it was open), most recent first,
    with the date and time they arrived (defect 4)."""
    return conn.execute(
        """SELECT inbound_id, kind, text, transcript, translation, received_on, received_time
           FROM inbound_message WHERE triage_episode_id = %s OR inbound_id = %s
           ORDER BY received_on DESC, received_time DESC NULLS LAST, inbound_id DESC""",
        (ep["episode_id"], ep["inbound_id"])).fetchall()


def episode_summary(ep: dict, h: dict | None) -> dict:
    """An episode in a few plain lines, built from its record (not written by a model): what she said, what the AI
    heard, the tier and why, what people did, and how it was closed. The Patient journey shows it (defect 12), and the
    next ticket for her shows the last closed one as context."""
    p = ep["plan"] or {}
    tags = (p.get("tags") or {}).get("symptoms") or []
    tier = TIERS.get(ep["tier"]) or "no tier yet"
    words = (ep["translation"] or ep["patient_words"] or "").strip()
    head = (f"{ep['ticket_id']}: {tier}" + (f" ({ep['red_kind'].replace('_', ' ').lower()})" if ep["red_kind"] else "")
            + (f" · she said: “{words.splitlines()[0][:140]}”" if words else ""))
    details = []
    if tags:
        details.append("AI heard: " + ", ".join(TR.TAG_LABELS.get(t, t) for t in tags)
                       + f"; the rules asked {len(p.get('question_ids') or [])} questions")
    why = (ep["result"] or {}).get("why") or []
    if why:
        details.append(f"Why {tier}: " + "; ".join(w.rstrip(".") for w in why) + ".")
    if h is not None and ep["answers"]:
        facts = [f["text"] for f in summary_facts(ep, h)
                 if f["source"] == "her answer" and not f["text"].startswith("Follow-up call")]
        if facts:
            details.append("Her answers: " + "; ".join(facts))
    if ep["package_sent_at"]:
        details.append(f"Emergency package sent to the family at {_hhmm(ep['package_sent_at'])}")
    if ep["ed_outcome"]:
        details.append(f"Family call: {ep['ed_outcome'].replace('_', ' ').lower()}")
    if ep.get("review"):
        details.append(_review_text(ep))
    if ep["nurse_callback"]:
        details.append(f"{_name(ep['nurse_callback']['by'])} called her back: "
                       f"“{ep['nurse_callback'].get('note') or 'done'}”")
    for r in ep.get("rounds") or []:
        details.append(f"Earlier round reopened ({r['why'].lower()})")
    for c in ep.get("doctor_calls") or []:
        details.append(f"{_name(c['by'])} called her: {'picked up' if c['outcome'] == 'PICKED_UP' else 'no answer'}")
    for c in ep.get("attender_calls") or []:
        details.append(f"Attender call by {_name(c['by'])}: {c['outcome'].replace('_', ' ').lower()}"
                       + (f" (“{c['note']}”)" if c.get("note") else ""))
    for o in _orders(ep):
        text = o.get("text") or o.get("note")
        details.append(f"Doctor's order: {ORDER_LABELS[o['order']]} by {_name(o['by'])}" + (f" (“{text}”)" if text else ""))
    if ep.get("closing") and ep["closing"].get("recommendation"):
        c = ep["closing"]
        details.append(f"Recommendation by {_name(c['by'])}" + (f" (contacted by {_contact_line(c)})" if c.get("contact")
                                                              else "") + f": “{c['recommendation']}”")
    if ep["followup"]:
        details.append(f"Follow-up call: {ep['followup']['answer'].lower()}")
    for c in ep["tier_changes"] or []:
        details.append(f"Tier {TIERS.get(c['from'], c['from'])} → {TIERS.get(c['to'], c['to'])} by {_name(c['by'])}: "
                       f"“{c['reason']}”")
    if ep["status"] == "closed":
        details.append(f"Closed by {_name(ep['closed_by'])} on {_when(ep['closed_at'])}: “{ep['close_note']}”")
    return {"episode_id": ep["episode_id"], "ticket_id": ep["ticket_id"], "tier": ep["tier"], "status": ep["status"],
            "opened_at": ep["opened_at"], "closed_at": ep["closed_at"], "summary": head, "details": details}


def previous_episode(conn, ep: dict) -> dict | None:
    """Her last closed episode before this one: context for whoever handles this ticket."""
    prev = conn.execute("SELECT * FROM triage_episode WHERE mrn = %s AND episode_id < %s AND status = 'closed' "
                        "ORDER BY episode_id DESC LIMIT 1", (ep["mrn"], ep["episode_id"])).fetchone()
    return episode_summary(prev, history(conn, prev["mrn"])) if prev else None


def question_bank() -> dict:
    """The whole question bank for the Triage Room's "Question bank" view (defect 1): every question in bank order,
    with its module, whether it is a danger question, its skip rule and its wording in each call language, and the
    rules the router uses to pick them."""
    groups = [("core", TR.CORE), ("history_arm", TR.HISTORY_ARM), *TR.MODULES.items()]
    module_of = {q: m for m, qs in groups for q in qs}
    labels = {"core": "Danger core (always asked, first)", "history_arm": "Her record: left-arm baseline",
              "chest_or_breathing": "Chest or breathing (in the danger core)",
              "other_symptom": "Other symptom (no questions: the duty doctor calls)",
              **{k: v[:1].upper() + v[1:] for k, v in TR.MODULE_LABELS.items()}}
    skips = {"Q_HEAD_SCORE": "Skipped if she has no headache today",
             "Q_HEAD_WORSE": "Skipped if she has no headache today",
             "Q_SUPP": "Skipped if she has not felt like vomiting since yesterday",
             "Q_LIQUIDS": "Skipped if she has not felt like vomiting and has not vomited"}
    rows = []
    for qid, field, kind, danger, hist, en in R.QUESTION_SPEC:
        rows.append({"id": qid, "field": field, "kind": kind, "danger": danger, "history": hist,
                     "module": module_of.get(qid), "module_label": labels.get(module_of.get(qid), "—"),
                     "fixed_12": qid in TR.FALLBACK, "skip": skips.get(qid),
                     "text": {"English": en.format(day="<day in her record>"),
                              **{lang: R.QUESTION_TEXT[lang][qid].format(day="<…>")
                                 for lang in R.CALL_LANGUAGES if lang in R.QUESTION_TEXT}}})
    return {"questions": rows,
            "modules": [{"tag": t, "label": labels.get(t, t), "questions": qs} for t, qs in groups],
            "rules": ["The five danger questions are always asked first and are never skipped.",
                      "The bangles question is added when her record has a left-arm baseline.",
                      "Each symptom the AI heard adds its module, in the order listed here (not the order she said "
                      "them).",
                      "If the tags are broken, or she named no symptom, the fixed 12 questions are asked.",
                      "A symptom no question covers (other symptom) keeps the call at least Amber and the duty doctor calls; "
                      "her own word “severe” keeps it at least Amber.",
                      "The AI never writes a question, never picks a tier and never gives advice."]}


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
    out, voiced, missing, offsets, ends = array.array("h"), 0, 0, {}, {}
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
        ends[str(t["i"])] = round(len(out) / REC_RATE, 2)  # and where it ends: a summary line plays just its answer
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
    _update(conn, ep, rec_offsets={**offsets, "ends": ends})  # the ticket's summary lines play these moments
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
                          "text": "Emergency package: family message with the summary PDF; Doctor on Duty and "
                                  "Senior Oncologist alerted.",
                          "sources": [{"kind": "pdf", "label": "Emergency summary PDF", "doc_id": doc["doc_id"]},
                                      {"kind": "note", "label": "Doctor note", "episode_id": eid}]})
        for c in ep["ed_calls"]:
            lines.append({"at": c["at"], "kind": "person", "episode_id": eid,
                          "text": f"{_name(c['by'])} called the family: {c['outcome'].replace('_', ' ')}"
                                  + (f", at {c['hospital']}" if c.get("hospital") else ""), "sources": []})
        if ep["nurse_callback"]:
            n = ep["nurse_callback"]
            lines.append({"at": n["at"], "kind": "person", "episode_id": eid,
                          "text": f"Call back by {_name(n['by'])}: {n['note']}", "sources": []})
        for o in _orders(ep):
            text = o.get("text") or o.get("note")
            lines.append({"at": o["at"], "kind": "person", "episode_id": eid,
                          "text": f"{_name(o['by'])}: {ORDER_LABELS[o['order']]}" + (f' — "{text}"' if text else ""),
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
