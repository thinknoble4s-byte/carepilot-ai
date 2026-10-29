"""Voice Triage MVP-lite (CarePilot_Voice_Triage_MVP_Lite_v1.1, Parts 3 and 4): what the call says, how one spoken
answer becomes one value, and the lite-profile rules that sort the answers into Red, Amber or Green. Plain code,
no database and no model: the model is only asked (in triage.py) when the word lists here cannot read an answer,
and its value is checked here. Thresholds and every Tamil line are drafts until Dr. Sekhar and a native speaker
sign them (protocol_status DRAFT_UNSIGNED)."""
import re
from dataclasses import dataclass
from datetime import date, timedelta

PROTOCOL = {"protocol_profile": "DEMO_LITE", "protocol_status": "DRAFT_UNSIGNED"}

# ---------------------------------------------------------------------------------------------------------------
# One answer -> one value
# ---------------------------------------------------------------------------------------------------------------
YES, NO, UNKNOWN, NOT_ASKED, NO_THERMOMETER = "YES", "NO", "UNKNOWN", "NOT_ASKED", "NO_THERMOMETER"

YES_WORDS = {"ஆம்", "ஆமா", "ஆமாம்", "ஆம", "yes", "yeah", "yep", "haan"}
NO_WORDS = {"இல்லை", "இல்ல", "இல்லே", "இல்லீங்க", "இல்லைங்க", "no", "nope", "illa", "illai"}
DONT_KNOW = ("தெரியவில்லை", "தெரியல", "தெரியாது", "don't know", "dont know", "not sure", "do not know")
NO_THERMOMETER_WORDS = ("வெப்பமானி இல்லை", "தெர்மாமீட்டர் இல்லை", "no thermometer", "don't have a thermometer",
                        "do not have a thermometer")
NUMBER_WORDS = {
    "பூஜ்யம்": 0, "பூஜ்ஜியம்": 0, "ஒன்று": 1, "ஒண்ணு": 1, "இரண்டு": 2, "ரெண்டு": 2, "மூன்று": 3, "மூணு": 3,
    "நான்கு": 4, "நாலு": 4, "ஐந்து": 5, "அஞ்சு": 5, "ஆறு": 6, "ஏழு": 7, "எட்டு": 8, "ஒன்பது": 9, "பத்து": 10,
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "once": 1, "twice": 2, "none": 0,
}


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[\s,.!?;:'\"()\-]+", text.lower()) if w]


def parse(kind: str, text: str):
    """A value, or None when the word lists cannot tell (then the model is asked, and its answer checked)."""
    low = (text or "").lower().strip()
    if not low:
        return None
    if any(p in low for p in DONT_KNOW):
        return UNKNOWN
    words = _words(low)
    if kind == "yesno":
        yes, no = any(w in YES_WORDS for w in words), any(w in NO_WORDS for w in words)
        return YES if yes and not no else NO if no and not yes else None
    if kind == "temperature" and any(p in low for p in NO_THERMOMETER_WORDS):
        return NO_THERMOMETER
    digits = re.findall(r"\d+(?:\.\d+)?", low)
    if len(digits) == 1:
        return float(digits[0])
    if kind == "number":
        found = [NUMBER_WORDS[w] for w in words if w in NUMBER_WORDS]
        if len(found) == 1:
            return float(found[0])
    return None


def check(kind: str, value):
    """A value is used only if it has the right shape and is in range; anything else is UNKNOWN."""
    if value in (UNKNOWN, None):
        return UNKNOWN
    if kind == "yesno":
        return value if value in (YES, NO) else UNKNOWN
    if kind == "temperature" and value == NO_THERMOMETER:
        return value
    if isinstance(value, bool):
        return UNKNOWN
    try:
        number = float(value)
    except (TypeError, ValueError):
        return UNKNOWN
    if kind == "temperature":
        return number if 90 <= number <= 110 or 32 <= number <= 43 else UNKNOWN  # F, or C; else unreadable
    if kind == "number":
        return number if 0 <= number <= 50 and number == int(number) else UNKNOWN
    return UNKNOWN


MODEL_SYSTEM = (
    "You convert one answer from a phone call into one value. You never judge health, never advise, never explain. "
    "Reply with JSON only: {\"value\": ...}. "
    "For a yes/no question the value is \"YES\", \"NO\" or \"UNKNOWN\". "
    "For a number question the value is a number (digits) or \"UNKNOWN\". "
    "For the temperature question the value is the number she said (for example 98.4), \"NO_THERMOMETER\", or "
    "\"UNKNOWN\". If the answer is unclear, does not answer the question, or mixes yes and no, the value is \"UNKNOWN\"."
)
MODEL_PROMPT_VERSION = "triage-answer-value-v1"


def model_value(kind: str, reply: str | None):
    """The model's JSON reply -> a checked value (UNKNOWN when it is anything else)."""
    match = re.search(r"\{.*?\}", reply or "", re.S)
    if not match:
        return UNKNOWN
    try:
        import json
        value = json.loads(match.group(0)).get("value")
    except (ValueError, AttributeError):
        return UNKNOWN
    if isinstance(value, str) and value.upper() in (YES, NO, UNKNOWN, NO_THERMOMETER):
        value = value.upper()
    return check(kind, value)


FOLLOWUP = {"BETTER": ("நன்றாக", "பரவாயில்லை", "குணம", "better", "good", "fine"),
            "SAME": ("அதே", "same"), "WORSE": ("மோசம", "worse")}


def parse_followup(text: str) -> str | None:
    low = (text or "").lower()
    found = [k for k, words in FOLLOWUP.items() if any(w in low for w in words)]
    return found[0] if len(found) == 1 else None


# ---------------------------------------------------------------------------------------------------------------
# What the call says (Part 3). Tamil lines are drafts.
# ---------------------------------------------------------------------------------------------------------------
TA_MONTHS = ("ஜனவரி", "பிப்ரவரி", "மார்ச்", "ஏப்ரல்", "மே", "ஜூன்", "ஜூலை", "ஆகஸ்ட்", "செப்டம்பர்", "அக்டோபர்",
             "நவம்பர்", "டிசம்பர்")


def day_ta(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{TA_MONTHS[d.month - 1]} {d.day}"


def day_en(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {d:%B}"


@dataclass(frozen=True)
class Question:
    id: str
    field: str
    kind: str        # yesno | number | temperature
    danger: bool     # a Red question: an unclear answer makes the call Red (unconfirmed)
    ta: str
    en: str
    history: bool = False


def questions(h: dict) -> list[Question]:
    last = h["last_headache"]["date"]
    return [
        Question("q1", "slurred_or_confused", "yesno", True,
                 "இப்போது உங்கள் பேச்சு குழறுகிறதா, அல்லது நீங்கள் குழப்பமாக இருக்கிறீர்களா? ஆம் அல்லது இல்லை என்று சொல்லுங்கள்.",
                 "Right now, is your speech slurred, or are you confused?"),
        Question("q2", "arm_red_or_hot", "yesno", True,
                 "தயவுசெய்து இப்போது இடது கையைப் பாருங்கள். அது சிவந்தோ, தொட்டால் சூடாகவோ இருக்கிறதா?",
                 "Please look at your left arm now. Is it red, or hot to touch?"),
        Question("q3", "temperature", "temperature", True,
                 "இன்று உங்கள் உடல் வெப்பநிலை எவ்வளவு? எண்ணைச் சொல்லுங்கள். வெப்பமானி இல்லையென்றால் ‘வெப்பமானி இல்லை’ என்று சொல்லுங்கள்.",
                 "What was your temperature today? Say the number, or say 'no thermometer'."),
        Question("q4", "vomiting_count", "number", True,
                 "நேற்று முதல் எத்தனை முறை வாந்தி எடுத்தீர்கள்? இல்லையென்றால் பூஜ்யம் என்று சொல்லுங்கள்.",
                 "How many times have you vomited since yesterday? Say zero if none."),
        Question("q5", "headache_present", "yesno", False, "இன்று தலைவலி இருக்கிறதா?", "Do you have a headache today?"),
        Question("q5a", "headache_score", "number", True,
                 "தலைவலி எவ்வளவு கடுமையாக இருக்கிறது? ஒன்று முதல் பத்து வரை சொல்லுங்கள். பத்து என்றால் மிகக் கடுமையான வலி.",
                 "On a scale of 1 to 10, how strong is the headache? Ten is the worst pain."),
        Question("q5b", "headache_worse_than_last_time", "yesno", False,
                 f"{day_ta(last)} அன்று சொன்னதை விட இந்த தலைவலி அதிகமாக இருக்கிறதா?",
                 f"Is it worse than when you told us about it on {day_en(last)}?", history=True),
        Question("q6", "bangles_tighter", "yesno", False,
                 "இடது கையில் வளையல்கள் அல்லது ரவிக்கைக் கை முன்பை விட இறுக்கமாக இருக்கிறதா?",
                 "Do your bangles or sleeve feel tighter on the left arm than before?", history=True),
        Question("q7", "nausea_since_yesterday", "yesno", False, "நேற்று முதல் எப்போதாவது குமட்டல் இருந்ததா?",
                 "Have you felt like vomiting at any time since yesterday?"),
        Question("q7a", "took_supplements_after_full_meals", "yesno", False,
                 "இன்று ஷெல்கால் மற்றும் நியூரோபியான் மாத்திரைகளை முழு உணவுக்குப் பிறகு மட்டுமே எடுத்தீர்களா?",
                 "Did you take your Shelcal-HD and Neurobion Forte only after a full meal today?", history=True),
    ]


def question(h: dict, qid: str) -> Question:
    return next(q for q in questions(h) if q.id == qid)


def skipped(q: Question, answers: dict) -> bool:
    """Part 3.2: no headache skips 5a and 5b; no nausea skips 7a."""
    if q.id in ("q5a", "q5b"):
        return answers.get("headache_present") != YES
    if q.id == "q7a":
        return answers.get("nausea_since_yesterday") != YES
    return False


def next_question(h: dict, answers: dict, after: str | None) -> Question | None:
    qs = questions(h)
    start = 0 if after is None else [q.id for q in qs].index(after) + 1
    return next((q for q in qs[start:] if not skipped(q, answers)), None)


def messages(h: dict) -> dict[str, tuple[str, str]]:
    """id -> (Tamil, English). TPL_CALL_*_V1 from the guide; the Tamil closing lines are drafted from the English."""
    name_ta, name_en, team = h["name_ta"], h["name_en"], h["team"]
    advice = h["advice"]
    return {
        "open": (f"வணக்கம். இது கேர்பைலட்டின் தானியங்கி உதவியாளர். {team['ta']} சார்பில் {name_ta} அம்மாவுக்காக "
                 "அழைக்கிறேன். சில குறுகிய கேள்விகள் கேட்பேன். ஆம் அல்லது இல்லை என்று, அல்லது ஒரு எண்ணைச் சொல்லுங்கள். எந்த நேரத்திலும் "
                 "செவிலியரிடம் பேச 0 அழுத்தவும்.",
                 f"Hello. This is CarePilot's automated assistant, calling for {name_en} Amma from {team['en']}. "
                 "I will ask a few short questions. Please answer yes or no, or say a number. To talk to a nurse at any "
                 "time, press 0."),
        "repeat": ("மன்னிக்கவும், எனக்குச் சரியாகக் கேட்கவில்லை. மீண்டும் சொல்லுங்கள்.",
                   "Sorry, I did not hear that clearly. Please say it again."),
        "red": ("நன்றி. உங்கள் பதில்களில் எச்சரிக்கை அறிகுறி உள்ளது. உடனே அருகிலுள்ள அவசர சிகிச்சைப் பிரிவுக்குச் செல்லுங்கள். "
                "காத்திருக்க வேண்டாம். உங்கள் குடும்பத்தினருக்கும் எங்கள் பராமரிப்புக் குழுவுக்கும் தெரிவிக்கிறோம்.",
                "Thank you. Your answers include a warning sign. Please go to the nearest emergency department now. "
                "Do not wait. We are telling your family and our care team."),
        "red_unconfirmed": ("நன்றி. உங்கள் பதில்களை உறுதிப்படுத்த முடியவில்லை. ஒரு செவிலியர் 15 நிமிடங்களுக்குள் உங்களை அழைப்பார். "
                            "உடல்நிலை மோசமானால் அருகிலுள்ள அவசர சிகிச்சைப் பிரிவுக்குச் செல்லுங்கள்.",
                            "Thank you. We could not confirm your answers. A nurse will call you within 15 minutes. "
                            "If you feel worse, go to the nearest emergency department."),
        "nurse": ("சரி. ஒரு செவிலியர் 15 நிமிடங்களுக்குள் உங்களை அழைப்பார். உடல்நிலை மோசமானால் அருகிலுள்ள அவசர சிகிச்சைப் "
                  "பிரிவுக்குச் செல்லுங்கள்.",
                  "All right. A nurse will call you within 15 minutes. If you feel worse, go to the nearest emergency "
                  "department."),
        "amber": ("நன்றி. உங்கள் பதில்களை ஒரு மருத்துவர் பார்க்க வேண்டும். இரண்டு மணி நேரத்திற்குள் மருத்துவர் உங்களை அழைப்பார். "
                  "எந்த மாத்திரையையும் நீங்களாக நிறுத்தவோ மாற்றவோ வேண்டாம். உடல்நிலை மோசமானால் 0 அழுத்தவும் அல்லது அருகிலுள்ள "
                  "அவசர சிகிச்சைப் பிரிவுக்குச் செல்லுங்கள்.",
                  "Thank you. A doctor needs to look at your answers. A doctor will call you within 2 hours. Please do "
                  "not stop or change any tablet on your own. If you feel worse, press 0 or go to the nearest emergency "
                  "department."),
        "green": ("நன்றி. இன்றைய உங்கள் பதில்களுக்கு மருத்துவர் அழைப்பு தேவையில்லை.",
                  "Thank you. Your answers today do not need a doctor's call."),
        "green_advice": (advice["ta"], advice["en"]),
        "green_end": ("நாளை காலை மீண்டும் அழைப்போம். எப்போதாவது உடல்நிலை மோசமானால் 0 அழுத்தவும் அல்லது HELP என்று பதில் அனுப்பவும்.",
                      "We will call you again tomorrow morning. If you feel worse at any time, press 0 or reply HELP."),
        "followup": ("வணக்கம். இது கேர்பைலட்டின் தானியங்கி உதவியாளர். நேற்று உங்களிடம் பேசினோம். இன்று எப்படி இருக்கிறீர்கள்: "
                     "நேற்றை விட நன்றாக, அதே போல, அல்லது மோசமாக?",
                     "Hello. This is CarePilot's automated assistant. We spoke yesterday. How are you today: better than "
                     "yesterday, the same, or worse?"),
        "followup_better": ("நன்றி. உங்கள் பதிலை உங்கள் மருத்துவருக்குத் தெரிவிக்கிறோம்.",
                            "Thank you. We will tell your doctor."),
        "followup_not_better": ("நன்றி. ஒரு மருத்துவர் இன்று உங்களை அழைப்பார். உடல்நிலை மோசமானால் அருகிலுள்ள அவசர சிகிச்சைப் "
                                "பிரிவுக்குச் செல்லுங்கள்.",
                                "Thank you. A doctor will call you today. If you feel worse, go to the nearest emergency "
                                "department."),
    }


TEMPLATE_IDS = {"red": "TPL_CALL_RED_V1", "red_unconfirmed": "TPL_CALL_RED_UNCONFIRMED_V1",
                "nurse": "TPL_CALL_NURSE_V1", "amber": "TPL_CALL_AMBER_V1", "green": "TPL_CALL_GREEN_V1",
                "no_answer": "TPL_CALL_CAREGIVER_V1"}


def closing(result: dict, h: dict) -> tuple[str, str]:
    """The fixed closing line for the tier. The only medical sentence is the doctor's own signed advice."""
    m = messages(h)
    if result["message"] != "green":
        return m[result["message"]]
    parts = [m["green"]] + ([m["green_advice"]] if result.get("repeat_advice") else []) + [m["green_end"]]
    return " ".join(p[0] for p in parts), " ".join(p[1] for p in parts)


# ---------------------------------------------------------------------------------------------------------------
# The rules (Part 4)
# ---------------------------------------------------------------------------------------------------------------
FIELDS = ("slurred_or_confused", "arm_red_or_hot", "thermometer_used", "temperature_value", "vomiting_count",
          "headache_present", "headache_score", "headache_worse_than_last_time", "bangles_tighter",
          "nausea_since_yesterday", "took_supplements_after_full_meals")
DANGER_FIELDS = ("slurred_or_confused", "arm_red_or_hot", "thermometer_used", "vomiting_count", "headache_score")

RED_PLAIN = {
    "RED_SLURRED_OR_CONFUSED": "Speech reported as slurred, or confusion",
    "RED_ARM_RED_OR_HOT": "Left arm reported as red or hot to touch",
    "RED_FEVER_100_4F_PLUS": "Temperature reported as 100.4 F (38.0 C) or more",
    "RED_VOMITING_6_PLUS": "Vomiting reported 6 or more times since yesterday",
    "RED_HEADACHE_SEVERE_7_PLUS": "Headache reported as 7 out of 10 or more",
    "RED_CANNOT_CONFIRM_SAFE": "Some answers could not be confirmed as safe",
    "RED_NO_ANSWER_2X": "She reported feeling unwell, then two calls were not answered",
    "RED_SYSTEM_FAULT": "The system could not read the answers",
}


def blank() -> dict:
    a = {f: NOT_ASKED for f in FIELDS}
    a["temperature_value"] = None
    return a


def store(answers: dict, q: Question, value) -> None:
    if q.kind != "temperature":
        answers[q.field] = value
    elif value == NO_THERMOMETER:
        answers["thermometer_used"] = NO
    elif value == UNKNOWN:
        answers["thermometer_used"] = UNKNOWN
    else:
        answers["thermometer_used"], answers["temperature_value"] = YES, value


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def temperature_f(a: dict):
    v = a.get("temperature_value")
    if a.get("thermometer_used") != YES or not _num(v):
        return None
    return float(v) if v >= 90 else round(v * 9 / 5 + 32, 1)


def red_codes(a: dict) -> list[str]:
    """Confirmed danger signs only: these stop the call at once."""
    codes = []
    if a["slurred_or_confused"] == YES:
        codes.append("RED_SLURRED_OR_CONFUSED")
    if a["arm_red_or_hot"] == YES:
        codes.append("RED_ARM_RED_OR_HOT")
    f = temperature_f(a)
    if f is not None and f >= 100.4:
        codes.append("RED_FEVER_100_4F_PLUS")
    if _num(a["vomiting_count"]) and a["vomiting_count"] >= 6:
        codes.append("RED_VOMITING_6_PLUS")
    if _num(a["headache_score"]) and a["headache_score"] >= 7:
        codes.append("RED_HEADACHE_SEVERE_7_PLUS")
    return codes


def _recurring(h: dict, today: date, symptom: str) -> bool:
    """Third report in 14 days, counting today's."""
    earlier = [e for e in h.get("events", []) if symptom in e.get("symptoms", [])
               and timedelta(0) <= today - date.fromisoformat(e["date"]) <= timedelta(days=14)]
    return len(earlier) + 1 >= 3


def triage(a: dict, h: dict, today: date, *, nurse: bool = False, fault: bool = False,
           no_answer: bool = False) -> dict:
    base = {**PROTOCOL, "repeat_advice": False, "repeat_instruction_id": None}
    if no_answer:
        return {**base, "tier": "TIER_1_RED", "reason_codes": ["RED_NO_ANSWER_2X"], "confirmed_danger": False,
                "red_kind": "NO_ANSWER", "action_code": "ALERT_NURSE_AND_CALL_CAREGIVER", "message": None,
                "patient_template_id": TEMPLATE_IDS["no_answer"], "response_minutes": 0}
    reds = red_codes(a)
    confirmed = bool(reds)
    if fault:
        reds.append("RED_SYSTEM_FAULT")
    if any(a[f] == UNKNOWN for f in DANGER_FIELDS):
        reds.append("RED_CANNOT_CONFIRM_SAFE")
    if reds:
        message = "red" if confirmed else "red_unconfirmed"
        return {**base, "tier": "TIER_1_RED", "reason_codes": reds, "confirmed_danger": confirmed,
                "red_kind": "CONFIRMED" if confirmed else "UNCONFIRMED",
                "action_code": "ER_DESK_HANDOFF_SIMULATED" if confirmed else "NURSE_CALLBACK_15_MIN",
                "message": message, "patient_template_id": TEMPLATE_IDS[message],
                "response_minutes": 0 if confirmed else 15}

    ambers = []
    if a["bangles_tighter"] == YES:
        ambers.append("AMBER_BANGLES_TIGHTER_THAN_BASELINE")
    if _num(a["headache_score"]) and a["headache_score"] >= h["last_headache"]["score"] + 2:
        ambers.append("AMBER_HEADACHE_RISE_2_PLUS")
    if a["headache_worse_than_last_time"] == YES:
        ambers.append("AMBER_HEADACHE_WORSE_THAN_LAST_TIME")
    if a["nausea_since_yesterday"] == YES and a["took_supplements_after_full_meals"] == YES:
        ambers.append("AMBER_NAUSEA_DESPITE_MEALS")
    if _num(a["vomiting_count"]) and 2 <= a["vomiting_count"] <= 5:
        ambers.append("AMBER_VOMITING_2_TO_5")
    symptoms = a["headache_present"] == YES or a["nausea_since_yesterday"] == YES or \
        (_num(a["vomiting_count"]) and a["vomiting_count"] > 0)
    if a["thermometer_used"] == NO and symptoms:
        ambers.append("AMBER_NO_THERMOMETER_WITH_SYMPTOMS")
    if a["headache_present"] == YES and _recurring(h, today, "headache"):
        ambers.append("AMBER_RECURRING_3X_14D_HEADACHE")
    if a["nausea_since_yesterday"] == YES and _recurring(h, today, "nausea"):
        ambers.append("AMBER_RECURRING_3X_14D_NAUSEA")
    if any(a[f] == UNKNOWN for f in FIELDS if f not in DANGER_FIELDS):
        ambers.append("AMBER_CANNOT_CONFIRM")
    if nurse:
        ambers.append("AMBER_NURSE_REQUESTED")
    if ambers:
        message = "nurse" if nurse else "amber"
        return {**base, "tier": "TIER_2_AMBER", "reason_codes": ambers, "confirmed_danger": False,
                "action_code": "NURSE_CALLBACK_15_MIN" if nurse else "DOCTOR_CALLBACK_2H", "message": message,
                "patient_template_id": TEMPLATE_IDS[message], "response_minutes": 15 if nurse else 120}
    repeat = a["nausea_since_yesterday"] == YES
    return {**base, "tier": "TIER_3_GREEN", "reason_codes": [], "confirmed_danger": False,
            "action_code": "GREEN_COACH_AND_FOLLOWUP_24H", "message": "green",
            "patient_template_id": TEMPLATE_IDS["green"], "response_minutes": 1440, "repeat_advice": repeat,
            "repeat_instruction_id": h["advice"].get("id") if repeat else None}


FACT_LABELS = {"slurred_or_confused": "Speech slurred or confused", "arm_red_or_hot": "Left arm red or hot",
               "vomiting_count": "Vomited (times since yesterday)", "headache_present": "Headache today",
               "headache_score": "Headache (out of 10)", "headache_worse_than_last_time": "Headache worse than last time",
               "bangles_tighter": "Bangles tighter", "nausea_since_yesterday": "Nausea since yesterday",
               "took_supplements_after_full_meals": "Supplements only after full meals"}


def _show(v) -> str:
    return str(int(v)) if _num(v) and v == int(v) else str(v)


def facts_text(a: dict) -> str:
    out = []
    if a["thermometer_used"] == YES:
        out.append(f"Temperature {_show(a['temperature_value'])}")
    elif a["thermometer_used"] == NO:
        out.append("No thermometer")
    out += [f"{label}: {_show(a[f])}" for f, label in FACT_LABELS.items() if a.get(f) not in (NOT_ASKED, None)]
    return "; ".join(out)


def why_lines(result: dict, a: dict, h: dict) -> list[str]:
    """The "Why this tier" panel (Part 4.3): fixed sentences with a date or number filled in. No interpretation."""
    arm, head, advice = h["arm_baseline"], h["last_headache"], h["advice"]
    out = []
    for code in result["reason_codes"]:
        if code in RED_PLAIN:
            out.append(RED_PLAIN[code])
        elif code == "AMBER_BANGLES_TIGHTER_THAN_BASELINE":
            out.append(f"Left arm: {arm['status']} on {day_en(arm['date'])}. Today: bangles tighter.")
        elif code == "AMBER_HEADACHE_RISE_2_PLUS":
            out.append(f"Headache: {head['score']} out of 10 on {day_en(head['date'])}. "
                       f"Today: {_show(a['headache_score'])} out of 10.")
        elif code == "AMBER_HEADACHE_WORSE_THAN_LAST_TIME":
            out.append(f"She says the headache is worse than on {day_en(head['date'])}.")
        elif code == "AMBER_NAUSEA_DESPITE_MEALS":
            out.append("She still feels sick although she says she took her supplements only after full meals, "
                       f"as the doctor advised on {day_en(advice['date'])}.")
        elif code == "AMBER_VOMITING_2_TO_5":
            out.append(f"Vomited {_show(a['vomiting_count'])} times since yesterday.")
        elif code == "AMBER_NO_THERMOMETER_WITH_SYMPTOMS":
            out.append("No temperature reading, and she has symptoms.")
        elif code.startswith("AMBER_RECURRING_3X_14D_"):
            out.append(f"This is the third report of {code.rsplit('_', 1)[1].lower()} in 14 days.")
        elif code == "AMBER_CANNOT_CONFIRM":
            out.append("Some answers could not be confirmed.")
        elif code == "AMBER_NURSE_REQUESTED":
            out.append("She pressed 0 to speak to a nurse.")
        else:
            out.append(code)
    if result["tier"] == "TIER_3_GREEN":
        out = ["No rule fired. " + facts_text(a)]
    return out


# ---------------------------------------------------------------------------------------------------------------
# Scripted answers for "Play as" (the guide's scenarios 3.4 to 3.8, in Tamil): they go through the same parsing
# ---------------------------------------------------------------------------------------------------------------
PRESS_0 = "<press 0>"
_AMBER = {"q1": "இல்லை", "q2": "இல்லை, சிவக்கவில்லை", "q3": "98.4", "q4": "பூஜ்யம்", "q5": "ஆம்", "q5a": "ஐந்து",
          "q5b": "ஆம், அதிகமாக இருக்கிறது", "q6": "ஆம், ரொம்ப இறுக்கமாக இருக்கிறது", "q7": "ஆம், நேற்றிலிருந்து",
          "q7a": "ஆம், சாப்பிட்ட பிறகுதான் எடுக்கிறேன்"}
SCENARIOS = {
    "amber": _AMBER,
    "green": {**_AMBER, "q5": "ஆம், கொஞ்சம்", "q5a": "இரண்டு", "q5b": "இல்லை, முன்பு போலவே",
              "q6": "இல்லை, முன்பு போல் தளர்வாக இருக்கிறது", "q7": "ஆம், இன்று காலை",
              "q7a": "இல்லை, இன்று காலை அவசரத்தில் வெறும் வயிற்றில் சாப்பிட்டேன்"},
    "red": {"q1": "இல்லை", "q2": "ஆம், சிவந்து எரிகிறது"},
    "fever": {**_AMBER, "q3": "101.2"},
    "unclear_arm": {**_AMBER, "q2": "தெரியவில்லை"},  # EC-A: twice "I don't know" on the arm -> Red, unconfirmed
    "no_thermometer": {**_AMBER, "q3": "வெப்பமானி இல்லை", "q5a": "மூன்று", "q5b": "இல்லை", "q6": "இல்லை",
                       "q7": "இல்லை"},  # EC-C: Amber
    "nurse": {"q1": "இல்லை", "q2": PRESS_0},
}
SCENARIO_LABELS = {"amber": "Amber (the hero scene)", "green": "Green", "red": "Red (arm red and burning)",
                   "fever": "Red (temperature 101.2)", "unclear_arm": "Red, unconfirmed (\"I don't know\" twice)",
                   "no_thermometer": "Amber (no thermometer, with a headache)", "nurse": "She presses 0 (nurse)"}
