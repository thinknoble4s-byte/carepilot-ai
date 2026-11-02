"""Voice Triage MVP-lite (CarePilot_Voice_Triage_MVP_Lite_v1.2, Parts 4 and 5): the question bank, what the call
says, how one spoken answer becomes one value, and the rules that sort the answers into Red, Amber or Green. Plain
code, no database and no model: the model is only asked (in triage.py) when the word lists here cannot read an
answer, and its value is checked here. Which questions a call asks is decided by triage_router from the tags the
model gave the voice note. Thresholds, the new v1.2 rules and every Tamil, Hindi and Telugu line are drafts until
Dr. Sekhar and a native speaker sign them (protocol_status DRAFT_UNSIGNED)."""
import re
from dataclasses import dataclass
from datetime import date, timedelta

PROTOCOL = {"protocol_profile": "ROUTED", "protocol_status": "DRAFT_UNSIGNED"}

# ---------------------------------------------------------------------------------------------------------------
# One answer -> one value
# ---------------------------------------------------------------------------------------------------------------
YES, NO, UNKNOWN, NOT_ASKED, NO_THERMOMETER = "YES", "NO", "UNKNOWN", "NOT_ASKED", "NO_THERMOMETER"

# Tamil, Hindi, Telugu, English
YES_WORDS = {"ஆம்", "ஆமா", "ஆமாம்", "ஆம", "हाँ", "हां", "हाँजी", "అవును", "అవునండి", "ఔను", "yes", "yeah", "yep",
             "haan"}
NO_WORDS = {"இல்லை", "இல்ல", "இல்லே", "இல்லீங்க", "இல்லைங்க", "नहीं", "नही", "ना", "లేదు", "లేదండి", "కాదు",
            "కాదండి", "no", "nope", "illa", "illai", "nahin", "ledu"}
DONT_KNOW = ("தெரியவில்லை", "தெரியல", "தெரியாது", "पता नहीं", "नहीं पता", "मालूम नहीं", "नहीं मालूम", "తెలియదు",
             "తెలీదు", "don't know", "dont know", "not sure", "do not know")
NO_THERMOMETER_WORDS = ("வெப்பமானி இல்லை", "தெர்மாமீட்டர் இல்லை", "थर्मामीटर नहीं", "థర్మామీటర్ లేదు", "no thermometer",
                        "don't have a thermometer", "do not have a thermometer")
NUMBER_WORDS = {
    "பூஜ்யம்": 0, "பூஜ்ஜியம்": 0, "ஒன்று": 1, "ஒண்ணு": 1, "இரண்டு": 2, "ரெண்டு": 2, "மூன்று": 3, "மூணு": 3,
    "நான்கு": 4, "நாலு": 4, "ஐந்து": 5, "அஞ்சு": 5, "ஆறு": 6, "ஏழு": 7, "எட்டு": 8, "ஒன்பது": 9, "பத்து": 10,
    "शून्य": 0, "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पाँच": 5, "पांच": 5, "छह": 6, "छः": 6, "सात": 7, "आठ": 8,
    "नौ": 9, "दस": 10,
    "సున్నా": 0, "ఒకటి": 1, "రెండు": 2, "మూడు": 3, "నాలుగు": 4, "ఐదు": 5, "ఆరు": 6, "ఏడు": 7, "ఎనిమిది": 8,
    "తొమ్మిది": 9, "పది": 10,
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "once": 1, "twice": 2, "none": 0,
}


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[\s,.!?;:'\"()\-।]+", text.lower()) if w]


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


FOLLOWUP = {"BETTER": ("நன்றாக", "பரவாயில்லை", "குணம", "बेहतर", "अच्छ", "బాగా", "బాగుంది", "మెరుగ", "better", "good",
                       "fine"),
            "SAME": ("அதே", "वैसी ही", "वैसा ही", "అలాగే", "అదే", "same"),
            "WORSE": ("மோசம", "खराब", "ख़राब", "बदतर", "బాగోలేదు", "ఎక్కువైంది", "worse")}


def parse_followup(text: str) -> str | None:
    low = (text or "").lower()
    found = [k for k, words in FOLLOWUP.items() if any(w in low for w in words)]
    return found[0] if len(found) == 1 else None


# ---------------------------------------------------------------------------------------------------------------
# What the call says (Part 4). The call speaks the patient's language (Tamil, Hindi or Telugu; any other hears the
# English lines). Every non-English line is a draft for a native speaker to check.
# ---------------------------------------------------------------------------------------------------------------
CALL_LANGUAGES = {"Tamil": "ta", "Hindi": "hi", "Telugu": "te"}
MONTHS = {
    "Tamil": ("ஜனவரி", "பிப்ரவரி", "மார்ச்", "ஏப்ரல்", "மே", "ஜூன்", "ஜூலை", "ஆகஸ்ட்", "செப்டம்பர்", "அக்டோபர்",
              "நவம்பர்", "டிசம்பர்"),
    "Hindi": ("जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर",
              "दिसंबर"),
    "Telugu": ("జనవరి", "ఫిబ్రవరి", "మార్చి", "ఏప్రిల్", "మే", "జూన్", "జూలై", "ఆగస్టు", "సెప్టెంబర్", "అక్టోబర్",
               "నవంబర్", "డిసెంబర్"),
}
TA_MONTHS = MONTHS["Tamil"]


def language(h: dict) -> str:
    """The call's language, set on the history when it is loaded (triage.history); Tamil for a bare record."""
    lang = h.get("language", "Tamil")
    return lang if lang in CALL_LANGUAGES else "English"


def day(iso: str, lang: str) -> str:
    d = date.fromisoformat(iso)
    if lang == "Hindi":
        return f"{d.day} {MONTHS['Hindi'][d.month - 1]}"
    if lang in MONTHS:
        return f"{MONTHS[lang][d.month - 1]} {d.day}"
    return day_en(iso)


def day_ta(iso: str) -> str:
    return day(iso, "Tamil")


def day_en(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {d:%B}"


def native(h: dict, key: str, lang: str | None = None):
    """The patient's own-language name (key "name"), team or advice text from the history record."""
    lang = lang or language(h)
    code = CALL_LANGUAGES.get(lang)
    if key == "name":
        return h.get(f"name_{code}") or h["name_en"]
    return h[key].get(code) or h[key]["en"]


@dataclass(frozen=True)
class Question:
    id: str
    field: str
    kind: str        # yesno | number | temperature
    danger: bool     # a Red question: an unclear answer makes the call Red (unconfirmed)
    ta: str          # the line in the patient's language (Tamil, Hindi or Telugu; English for anyone else)
    en: str
    history: bool = False


# The question bank (Part 4.2), in bank order: id -> (field, kind, danger, history, English)
QUESTION_SPEC = (
    ("Q_SLURRED", "slurred_or_confused", "yesno", True, False, "Right now, is your speech slurred, or are you confused?"),
    ("Q_AWAKE", "cannot_stay_awake", "yesno", True, False,
     "Is it so hard to stay awake that you cannot keep your eyes open?"),
    ("Q_CHEST", "chest_pain", "yesno", True, False, "Do you have chest pain, or is it hard to breathe, right now?"),
    ("Q_ARM_RED", "arm_red_or_hot", "yesno", True, False, "Please look at your left arm now. Is it red, or hot to touch?"),
    ("Q_TEMP", "temperature", "temperature", True, False,
     "What was your temperature today? Say the number, or say 'no thermometer'."),
    ("Q_BANGLES", "bangles_tighter", "yesno", False, True,
     "Do your bangles or sleeve feel tighter on the left arm than before?"),
    ("Q_DIZZY", "unsteady_or_dizzy_walking", "yesno", True, False, "When you walk, do you feel dizzy or unsteady?"),
    ("Q_VISION", "blurred_vision", "yesno", True, False, "Right now, is your vision blurred?"),
    ("Q_CHILLS", "shaking_chills", "yesno", True, False, "Have you had shaking chills?"),
    ("Q_FEELS_HOT", "feels_hot", "yesno", False, False, "Do you feel hot or feverish?"),
    ("Q_HEADACHE", "headache_present", "yesno", False, False, "Do you have a headache today?"),
    ("Q_HEAD_SCORE", "headache_score", "number", True, True,
     "On a scale of 1 to 10, how strong is the headache? Ten is the worst pain."),
    ("Q_HEAD_WORSE", "headache_worse_than_last_time", "yesno", False, True,
     "Is it worse than when you told us about it on {day}?"),
    ("Q_NAUSEA", "nausea_since_yesterday", "yesno", False, False,
     "Have you felt like vomiting at any time since yesterday?"),
    ("Q_VOMIT", "vomiting_count", "number", True, False,
     "How many times have you vomited since yesterday? Say zero if none."),
    ("Q_LIQUIDS", "can_keep_liquids_down", "yesno", True, False, "Can you keep water down?"),
    ("Q_SUPP", "took_supplements_after_full_meals", "yesno", False, True,
     "Did you take your Shelcal-HD and Neurobion Forte only after a full meal today?"),
    ("Q_DROWSY", "drowsy_most_of_today", "yesno", False, False, "Have you felt sleepy or groggy for most of today?"),
    ("Q_ARM_SCORE", "arm_pain_score", "number", False, False,
     "On a scale of 1 to 10, how strong is the pain in your left arm? Ten is the worst pain."),
    ("Q_ARM_SUDDEN", "arm_pain_sudden", "yesno", False, False, "Did the arm pain start suddenly?"),
    ("Q_SWOLLEN", "arm_swollen", "yesno", False, False, "Does the left arm look swollen, or feel heavy?"),
    ("Q_SKIN", "skin_break_left_arm", "yesno", False, False, "Is there a cut, a bite or a prick on your left arm?"),
    ("Q_TIRED_DAYS", "tired_days", "number", False, False,
     "For how many days have you been feeling tired? Say a number."),
    ("Q_TIRED_WORSE", "tired_getting_worse", "yesno", False, False, "Is the tiredness worse than last week?"),
)
QUESTION_IDS = tuple(q[0] for q in QUESTION_SPEC)
QUESTION_TEXT = {
    "Tamil": {  # the guide's drafts (Part 4.4)
        "Q_SLURRED": "இப்போது உங்கள் பேச்சு குழறுகிறதா, அல்லது நீங்கள் குழப்பமாக இருக்கிறீர்களா?",
        "Q_AWAKE": "கண்களைத் திறந்து வைக்க முடியாத அளவுக்கு தூக்கம் வருகிறதா?",
        "Q_CHEST": "இப்போது நெஞ்சுவலி இருக்கிறதா, அல்லது மூச்சு விடுவது கடினமாக இருக்கிறதா?",
        "Q_ARM_RED": "தயவுசெய்து இப்போது இடது கையைப் பாருங்கள். அது சிவந்தோ, தொட்டால் சூடாகவோ இருக்கிறதா?",
        "Q_TEMP": "இன்று உங்கள் உடல் வெப்பநிலை எவ்வளவு? எண்ணைச் சொல்லுங்கள். வெப்பமானி இல்லையென்றால் ‘வெப்பமானி இல்லை’ என்று சொல்லுங்கள்.",
        "Q_BANGLES": "இடது கையில் வளையல்கள் அல்லது ரவிக்கைக் கை முன்பை விட இறுக்கமாக இருக்கிறதா?",
        "Q_DIZZY": "நடக்கும்போது தலைசுற்றல் அல்லது தடுமாற்றம் இருக்கிறதா?",
        "Q_VISION": "இப்போது உங்கள் பார்வை மங்கலாக இருக்கிறதா?",
        "Q_CHILLS": "நடுக்கத்துடன் குளிர் இருந்ததா?",
        "Q_FEELS_HOT": "உடல் சூடாக அல்லது காய்ச்சல் போல உணர்கிறீர்களா?",
        "Q_HEADACHE": "இன்று தலைவலி இருக்கிறதா?",
        "Q_HEAD_SCORE": "தலைவலி எவ்வளவு கடுமையாக இருக்கிறது? ஒன்று முதல் பத்து வரை சொல்லுங்கள். பத்து என்றால் மிகக் கடுமையான வலி.",
        "Q_HEAD_WORSE": "{day} அன்று சொன்னதை விட இந்த தலைவலி அதிகமாக இருக்கிறதா?",
        "Q_NAUSEA": "நேற்று முதல் எப்போதாவது குமட்டல் இருந்ததா?",
        "Q_VOMIT": "நேற்று முதல் எத்தனை முறை வாந்தி எடுத்தீர்கள்? இல்லையென்றால் பூஜ்யம் என்று சொல்லுங்கள்.",
        "Q_LIQUIDS": "தண்ணீர் குடித்தால் வயிற்றில் தங்குகிறதா?",
        "Q_SUPP": "இன்று ஷெல்கால் மற்றும் நியூரோபியான் மாத்திரைகளை முழு உணவுக்குப் பிறகு மட்டுமே எடுத்தீர்களா?",
        "Q_DROWSY": "இன்று பெரும்பாலான நேரம் தூக்கக் கலக்கமாக அல்லது சோர்வாக இருந்ததா?",
        "Q_ARM_SCORE": "இடது கை வலி எவ்வளவு கடுமையாக இருக்கிறது? ஒன்று முதல் பத்து வரை சொல்லுங்கள். பத்து என்றால் மிகக் கடுமையான வலி.",
        "Q_ARM_SUDDEN": "இந்த கை வலி திடீரென்று தொடங்கியதா?",
        "Q_SWOLLEN": "இடது கை வீங்கியிருக்கிறதா, அல்லது கனமாக இருக்கிறதா?",
        "Q_SKIN": "இடது கையில் பூச்சிக்கடி, வெட்டு அல்லது முள் குத்திய காயம் ஏதாவது இருக்கிறதா?",
        "Q_TIRED_DAYS": "எத்தனை நாட்களாக இந்த சோர்வு இருக்கிறது? எண்ணைச் சொல்லுங்கள்.",
        "Q_TIRED_WORSE": "சென்ற வாரத்தை விட சோர்வு அதிகமாக இருக்கிறதா?",
    },
    "Hindi": {
        "Q_SLURRED": "अभी, क्या आपकी बोली लड़खड़ा रही है, या आप उलझन महसूस कर रही हैं? हाँ या नहीं में बताइए।",
        "Q_AWAKE": "क्या इतनी नींद आ रही है कि आप आँखें खुली नहीं रख पा रहीं?",
        "Q_CHEST": "क्या अभी आपके सीने में दर्द है, या साँस लेने में तकलीफ़ है?",
        "Q_ARM_RED": "कृपया अभी अपनी बाईं बाँह देखिए। क्या वह लाल है, या छूने पर गरम है?",
        "Q_TEMP": "आज आपका तापमान कितना था? नंबर बताइए। अगर थर्मामीटर नहीं है, तो ‘थर्मामीटर नहीं है’ कहिए।",
        "Q_BANGLES": "क्या बाईं बाँह में चूड़ियाँ या ब्लाउज़ की बाँह पहले से ज़्यादा कसी लग रही हैं?",
        "Q_DIZZY": "चलते समय क्या आपको चक्कर आता है, या आप लड़खड़ाती हैं?",
        "Q_VISION": "क्या अभी आपको धुंधला दिख रहा है?",
        "Q_CHILLS": "क्या आपको कँपकँपी के साथ ठंड लगी है?",
        "Q_FEELS_HOT": "क्या आपको शरीर गरम या बुखार जैसा लग रहा है?",
        "Q_HEADACHE": "क्या आज आपको सिरदर्द है?",
        "Q_HEAD_SCORE": "सिरदर्द कितना तेज़ है? एक से दस तक बताइए। दस का मतलब सबसे तेज़ दर्द।",
        "Q_HEAD_WORSE": "{day} को आपने जो बताया था, क्या यह सिरदर्द उससे ज़्यादा है?",
        "Q_NAUSEA": "क्या कल से किसी भी समय आपका जी मिचलाया है?",
        "Q_VOMIT": "कल से अब तक आपको कितनी बार उल्टी हुई है? अगर नहीं हुई, तो शून्य कहिए।",
        "Q_LIQUIDS": "क्या पानी पीने के बाद पेट में टिकता है?",
        "Q_SUPP": "क्या आज आपने शेलकाल और न्यूरोबियान की गोलियाँ पूरा खाना खाने के बाद ही लीं?",
        "Q_DROWSY": "क्या आज ज़्यादातर समय आपको नींद या सुस्ती महसूस हुई?",
        "Q_ARM_SCORE": "बाईं बाँह का दर्द कितना तेज़ है? एक से दस तक बताइए। दस का मतलब सबसे तेज़ दर्द।",
        "Q_ARM_SUDDEN": "क्या बाँह का यह दर्द अचानक शुरू हुआ?",
        "Q_SWOLLEN": "क्या बाईं बाँह सूजी हुई दिखती है, या भारी लगती है?",
        "Q_SKIN": "क्या बाईं बाँह पर कोई कट, कीड़े का काटा या चुभन का घाव है?",
        "Q_TIRED_DAYS": "आप कितने दिनों से थकान महसूस कर रही हैं? एक नंबर बताइए।",
        "Q_TIRED_WORSE": "क्या थकान पिछले हफ़्ते से ज़्यादा है?",
    },
    "Telugu": {
        "Q_SLURRED": "ఇప్పుడు మీ మాట తడబడుతోందా, లేదా మీకు అయోమయంగా ఉందా? అవును లేదా కాదు అని చెప్పండి.",
        "Q_AWAKE": "కళ్ళు తెరిచి ఉంచలేనంతగా నిద్ర వస్తోందా?",
        "Q_CHEST": "ఇప్పుడు మీకు ఛాతీ నొప్పి ఉందా, లేదా ఊపిరి తీసుకోవడం కష్టంగా ఉందా?",
        "Q_ARM_RED": "దయచేసి ఇప్పుడు మీ ఎడమ చేతిని చూడండి. అది ఎర్రగా ఉందా, లేదా తాకితే వేడిగా ఉందా?",
        "Q_TEMP": "ఈ రోజు మీ శరీర ఉష్ణోగ్రత ఎంత? సంఖ్య చెప్పండి. థర్మామీటర్ లేకపోతే ‘థర్మామీటర్ లేదు’ అని చెప్పండి.",
        "Q_BANGLES": "ఎడమ చేతికి గాజులు లేదా జాకెట్ చేయి ముందు కంటే బిగుతుగా అనిపిస్తున్నాయా?",
        "Q_DIZZY": "నడిచేటప్పుడు తల తిరుగుతోందా, లేదా తడబడుతున్నారా?",
        "Q_VISION": "ఇప్పుడు మీ చూపు మసకగా ఉందా?",
        "Q_CHILLS": "వణుకుతో చలి వచ్చిందా?",
        "Q_FEELS_HOT": "ఒంట్లో వేడిగా లేదా జ్వరంగా అనిపిస్తోందా?",
        "Q_HEADACHE": "ఈ రోజు మీకు తలనొప్పి ఉందా?",
        "Q_HEAD_SCORE": "తలనొప్పి ఎంత తీవ్రంగా ఉంది? ఒకటి నుండి పది వరకు చెప్పండి. పది అంటే చాలా తీవ్రమైన నొప్పి.",
        "Q_HEAD_WORSE": "{day} న మీరు చెప్పిన దానికంటే ఈ తలనొప్పి ఎక్కువగా ఉందా?",
        "Q_NAUSEA": "నిన్నటి నుండి ఎప్పుడైనా వికారంగా అనిపించిందా?",
        "Q_VOMIT": "నిన్నటి నుండి ఎన్ని సార్లు వాంతి అయ్యింది? కాకపోతే సున్నా అని చెప్పండి.",
        "Q_LIQUIDS": "నీళ్ళు తాగితే కడుపులో నిలుస్తున్నాయా?",
        "Q_SUPP": "ఈ రోజు షెల్‌కాల్ మరియు న్యూరోబియాన్ మాత్రలను పూర్తి భోజనం తర్వాతే వేసుకున్నారా?",
        "Q_DROWSY": "ఈ రోజు ఎక్కువ సమయం నిద్రమత్తుగా లేదా మగతగా అనిపించిందా?",
        "Q_ARM_SCORE": "ఎడమ చేతి నొప్పి ఎంత తీవ్రంగా ఉంది? ఒకటి నుండి పది వరకు చెప్పండి. పది అంటే చాలా తీవ్రమైన నొప్పి.",
        "Q_ARM_SUDDEN": "ఈ చేతి నొప్పి అకస్మాత్తుగా మొదలైందా?",
        "Q_SWOLLEN": "ఎడమ చేయి వాచినట్లు కనిపిస్తోందా, లేదా బరువుగా అనిపిస్తోందా?",
        "Q_SKIN": "ఎడమ చేతిపై ఏదైనా గాటు, పురుగు కాటు లేదా గుచ్చుకున్న గాయం ఉందా?",
        "Q_TIRED_DAYS": "ఎన్ని రోజులుగా అలసటగా అనిపిస్తోంది? ఒక సంఖ్య చెప్పండి.",
        "Q_TIRED_WORSE": "గత వారం కంటే అలసట ఎక్కువగా ఉందా?",
    },
}


def bank(h: dict) -> list[Question]:
    """All the questions, in the patient's language, in bank order."""
    lang, last = language(h), h["last_headache"]["date"]
    own = QUESTION_TEXT.get(lang)
    out = []
    for qid, field, kind, danger, hist, en in QUESTION_SPEC:
        en = en.format(day=day_en(last))
        out.append(Question(qid, field, kind, danger, own[qid].format(day=day(last, lang)) if own else en, en,
                            history=hist))
    return out


def questions(h: dict, ids=None) -> list[Question]:
    """The call's questions in the order the router chose (the whole bank when no list is given)."""
    by_id = {q.id: q for q in bank(h)}
    return [by_id[i] for i in (ids or QUESTION_IDS)]


def question(h: dict, qid: str) -> Question:
    return next(q for q in bank(h) if q.id == qid)


def skipped(q: Question, answers: dict) -> bool:
    """Part 4.1: "headache today" NO skips the score and "worse than last time"; "felt sick since yesterday" NO
    skips the supplements, and with no vomiting "keeps water down" too. Skipped questions stay NOT_ASKED."""
    if q.id in ("Q_HEAD_SCORE", "Q_HEAD_WORSE"):
        return answers.get("headache_present") == NO
    if q.id == "Q_SUPP":
        return answers.get("nausea_since_yesterday") == NO
    if q.id == "Q_LIQUIDS":
        return answers.get("nausea_since_yesterday") == NO and answers.get("vomiting_count") == 0
    return False


def next_question(h: dict, answers: dict, after: str | None, ids=None) -> Question | None:
    qs = questions(h, ids)
    start = 0 if after is None else [q.id for q in qs].index(after) + 1
    return next((q for q in qs[start:] if not skipped(q, answers)), None)


# TPL_CALL_*_V1 from the guide; the closing lines are drafted from the English. {name} and {team} come from the
# history record ("green_advice" is the doctor's own signed advice, also from the record).
MESSAGE_TEXT = {
    "English": {
        "open": "Hello. This is CarePilot's automated assistant, calling for {name} Amma from {team}. I will ask a few "
                "short questions. Please answer yes or no, or say a number. To ask for a doctor to call you back at any time, press 0.",
        "repeat": "Sorry, I did not hear that clearly. Please say it again.",
        "red": "Thank you. Your answers include a warning sign. Please go to the nearest emergency department now. "
               "Do not wait. We are telling your family and our care team.",
        "red_unconfirmed": "Thank you. We could not confirm your answers. A member of hospital staff will call you within 15 minutes. "
                           "If you feel worse, go to the nearest emergency department.",
        "nurse": "All right. A nurse will call you within 15 minutes. If you feel worse, go to the nearest emergency "
                 "department.",
        "nurse_other": "Thank you. A nurse will call you within 15 minutes. If you feel worse, go to the nearest "
                       "emergency department.",
        "amber": "Thank you. A doctor needs to look at your answers. A doctor will call you within 2 hours. Please do "
                 "not stop or change any tablet on your own. If you feel worse, press 0 or go to the nearest emergency "
                 "department.",
        "green": "Thank you. Your answers today do not need a doctor's call.",
        "green_end": "We will call you again tomorrow morning. If you feel worse at any time, press 0 or reply HELP.",
        "followup": "Hello. This is CarePilot's automated assistant. We spoke yesterday. How are you today: better than "
                    "yesterday, the same, or worse?",
        "followup_better": "Thank you. We will tell your doctor.",
        "followup_not_better": "Thank you. A doctor will call you today. If you feel worse, go to the nearest emergency "
                               "department.",
    },
    "Tamil": {
        "open": "வணக்கம். இது கேர்பைலட்டின் தானியங்கி உதவியாளர். {team} சார்பில் {name} அம்மாவுக்காக அழைக்கிறேன். சில "
                "குறுகிய கேள்விகள் கேட்பேன். ஆம் அல்லது இல்லை என்று, அல்லது ஒரு எண்ணைச் சொல்லுங்கள். எந்த நேரத்திலும் "
                "மருத்துவர் உங்களைத் திரும்ப அழைக்க 0 அழுத்தவும்.",
        "repeat": "மன்னிக்கவும், எனக்குச் சரியாகக் கேட்கவில்லை. மீண்டும் சொல்லுங்கள்.",
        "red": "நன்றி. உங்கள் பதில்களில் எச்சரிக்கை அறிகுறி உள்ளது. உடனே அருகிலுள்ள அவசர சிகிச்சைப் பிரிவுக்குச் செல்லுங்கள். "
               "காத்திருக்க வேண்டாம். உங்கள் குடும்பத்தினருக்கும் எங்கள் பராமரிப்புக் குழுவுக்கும் தெரிவிக்கிறோம்.",
        "red_unconfirmed": "நன்றி. உங்கள் பதில்களை உறுதிப்படுத்த முடியவில்லை. மருத்துவமனை ஊழியர் ஒருவர் 15 நிமிடங்களுக்குள் "
                           "உங்களை அழைப்பார். உடல்நிலை மோசமானால் அருகிலுள்ள அவசர சிகிச்சைப் பிரிவுக்குச் செல்லுங்கள்.",
        "nurse": "சரி. ஒரு செவிலியர் 15 நிமிடங்களுக்குள் உங்களை அழைப்பார். உடல்நிலை மோசமானால் அருகிலுள்ள அவசர சிகிச்சைப் "
                 "பிரிவுக்குச் செல்லுங்கள்.",
        "nurse_other": "நன்றி. ஒரு செவிலியர் 15 நிமிடங்களுக்குள் உங்களை அழைப்பார். உடல்நிலை மோசமானால் அருகிலுள்ள அவசர "
                       "சிகிச்சைப் பிரிவுக்குச் செல்லுங்கள்.",
        "amber": "நன்றி. உங்கள் பதில்களை ஒரு மருத்துவர் பார்க்க வேண்டும். இரண்டு மணி நேரத்திற்குள் மருத்துவர் உங்களை அழைப்பார். "
                 "எந்த மாத்திரையையும் நீங்களாக நிறுத்தவோ மாற்றவோ வேண்டாம். உடல்நிலை மோசமானால் 0 அழுத்தவும் அல்லது அருகிலுள்ள "
                 "அவசர சிகிச்சைப் பிரிவுக்குச் செல்லுங்கள்.",
        "green": "நன்றி. இன்றைய உங்கள் பதில்களுக்கு மருத்துவர் அழைப்பு தேவையில்லை.",
        "green_end": "நாளை காலை மீண்டும் அழைப்போம். எப்போதாவது உடல்நிலை மோசமானால் 0 அழுத்தவும் அல்லது HELP என்று பதில் "
                     "அனுப்பவும்.",
        "followup": "வணக்கம். இது கேர்பைலட்டின் தானியங்கி உதவியாளர். நேற்று உங்களிடம் பேசினோம். இன்று எப்படி இருக்கிறீர்கள்: "
                    "நேற்றை விட நன்றாக, அதே போல, அல்லது மோசமாக?",
        "followup_better": "நன்றி. உங்கள் பதிலை உங்கள் மருத்துவருக்குத் தெரிவிக்கிறோம்.",
        "followup_not_better": "நன்றி. ஒரு மருத்துவர் இன்று உங்களை அழைப்பார். உடல்நிலை மோசமானால் அருகிலுள்ள அவசர சிகிச்சைப் "
                               "பிரிவுக்குச் செல்லுங்கள்.",
    },
    "Hindi": {
        "open": "नमस्ते। यह केयरपायलट का स्वचालित सहायक है। मैं {team} की ओर से {name} जी के लिए कॉल कर रही हूँ। मैं कुछ "
                "छोटे सवाल पूछूँगी। कृपया हाँ या नहीं में, या एक नंबर बोलकर जवाब दीजिए। किसी भी समय डॉक्टर से वापस कॉल "
                "करवाने के लिए 0 दबाइए।",
        "repeat": "माफ़ कीजिए, मुझे ठीक से सुनाई नहीं दिया। कृपया फिर से बताइए।",
        "red": "धन्यवाद। आपके जवाबों में एक खतरे का संकेत है। कृपया अभी नज़दीकी आपातकालीन विभाग में जाइए। इंतज़ार मत "
               "कीजिए। हम आपके परिवार और हमारी देखभाल टीम को बता रहे हैं।",
        "red_unconfirmed": "धन्यवाद। हम आपके जवाबों की पुष्टि नहीं कर सके। अस्पताल का एक कर्मचारी 15 मिनट के अंदर आपको कॉल करेगा। अगर "
                           "तबीयत ज़्यादा खराब लगे, तो नज़दीकी आपातकालीन विभाग में जाइए।",
        "nurse": "ठीक है। एक नर्स 15 मिनट के अंदर आपको कॉल करेगी। अगर तबीयत ज़्यादा खराब लगे, तो नज़दीकी आपातकालीन विभाग "
                 "में जाइए।",
        "nurse_other": "धन्यवाद। एक नर्स 15 मिनट के अंदर आपको कॉल करेगी। अगर तबीयत ज़्यादा खराब लगे, तो नज़दीकी "
                       "आपातकालीन विभाग में जाइए।",
        "amber": "धन्यवाद। आपके जवाब एक डॉक्टर को देखने होंगे। डॉक्टर 2 घंटे के अंदर आपको कॉल करेंगे। कृपया कोई भी गोली "
                 "अपने आप बंद या बदलिए मत। अगर तबीयत ज़्यादा खराब लगे, तो 0 दबाइए या नज़दीकी आपातकालीन विभाग में जाइए।",
        "green": "धन्यवाद। आज आपके जवाबों के लिए डॉक्टर की कॉल की ज़रूरत नहीं है।",
        "green_end": "हम कल सुबह फिर कॉल करेंगे। अगर किसी भी समय तबीयत ज़्यादा खराब लगे, तो 0 दबाइए या मदद लिखकर भेजिए।",
        "followup": "नमस्ते। यह केयरपायलट का स्वचालित सहायक है। कल हमने आपसे बात की थी। आज आप कैसी हैं: कल से बेहतर, "
                    "वैसी ही, या ज़्यादा खराब?",
        "followup_better": "धन्यवाद। हम आपके डॉक्टर को बता देंगे।",
        "followup_not_better": "धन्यवाद। एक डॉक्टर आज आपको कॉल करेंगे। अगर तबीयत ज़्यादा खराब लगे, तो नज़दीकी आपातकालीन "
                               "विभाग में जाइए।",
    },
    "Telugu": {
        "open": "నమస్కారం. ఇది కేర్‌పైలట్ ఆటోమేటెడ్ సహాయకుడు. {team} తరఫున {name} గారి కోసం ఫోన్ చేస్తున్నాను. కొన్ని "
                "చిన్న ప్రశ్నలు అడుగుతాను. అవును లేదా కాదు అని, లేదా ఒక సంఖ్య చెప్పండి. ఎప్పుడైనా డాక్టర్ మీకు తిరిగి ఫోన్ "
                "చేయాలంటే 0 నొక్కండి.",
        "repeat": "క్షమించండి, నాకు సరిగ్గా వినిపించలేదు. దయచేసి మళ్ళీ చెప్పండి.",
        "red": "ధన్యవాదాలు. మీ జవాబుల్లో ఒక ప్రమాద సంకేతం ఉంది. దయచేసి ఇప్పుడే దగ్గరలోని అత్యవసర విభాగానికి వెళ్లండి. "
               "ఆలస్యం చేయవద్దు. మీ కుటుంబానికి మరియు మా సంరక్షణ బృందానికి తెలియజేస్తున్నాము.",
        "red_unconfirmed": "ధన్యవాదాలు. మీ జవాబులను నిర్ధారించలేకపోయాము. ఆసుపత్రి సిబ్బందిలో ఒకరు 15 నిమిషాల్లో మీకు ఫోన్ చేస్తారు. "
                           "ఆరోగ్యం మరింత బాగోలేకపోతే, దగ్గరలోని అత్యవసర విభాగానికి వెళ్లండి.",
        "nurse": "సరే. ఒక నర్స్ 15 నిమిషాల్లో మీకు ఫోన్ చేస్తారు. ఆరోగ్యం మరింత బాగోలేకపోతే, దగ్గరలోని అత్యవసర విభాగానికి "
                 "వెళ్లండి.",
        "nurse_other": "ధన్యవాదాలు. ఒక నర్స్ 15 నిమిషాల్లో మీకు ఫోన్ చేస్తారు. ఆరోగ్యం మరింత బాగోలేకపోతే, దగ్గరలోని "
                       "అత్యవసర విభాగానికి వెళ్లండి.",
        "amber": "ధన్యవాదాలు. మీ జవాబులను ఒక డాక్టర్ చూడాలి. డాక్టర్ 2 గంటల్లో మీకు ఫోన్ చేస్తారు. దయచేసి ఏ మాత్రనూ మీ "
                 "అంతట మీరే ఆపవద్దు లేదా మార్చవద్దు. ఆరోగ్యం మరింత బాగోలేకపోతే, 0 నొక్కండి లేదా దగ్గరలోని అత్యవసర "
                 "విభాగానికి వెళ్లండి.",
        "green": "ధన్యవాదాలు. ఈ రోజు మీ జవాబులకు డాక్టర్ ఫోన్ అవసరం లేదు.",
        "green_end": "రేపు ఉదయం మళ్ళీ ఫోన్ చేస్తాము. ఎప్పుడైనా ఆరోగ్యం మరింత బాగోలేకపోతే, 0 నొక్కండి లేదా సహాయం అని "
                     "జవాబు పంపండి.",
        "followup": "నమస్కారం. ఇది కేర్‌పైలట్ ఆటోమేటెడ్ సహాయకుడు. నిన్న మీతో మాట్లాడాము. ఈ రోజు మీరు ఎలా ఉన్నారు: "
                    "నిన్నటి కంటే బాగా, అలాగే, లేదా ఇంకా బాగోలేదా?",
        "followup_better": "ధన్యవాదాలు. మీ డాక్టర్‌కు తెలియజేస్తాము.",
        "followup_not_better": "ధన్యవాదాలు. ఈ రోజు ఒక డాక్టర్ మీకు ఫోన్ చేస్తారు. ఆరోగ్యం మరింత బాగోలేకపోతే, దగ్గరలోని "
                               "అత్యవసర విభాగానికి వెళ్లండి.",
    },
}


def messages(h: dict) -> dict[str, tuple[str, str]]:
    """id -> (the patient's language, English)."""
    lang = language(h)
    own, en = MESSAGE_TEXT.get(lang, MESSAGE_TEXT["English"]), MESSAGE_TEXT["English"]
    fill_own = {"name": native(h, "name", lang), "team": native(h, "team", lang)}
    fill_en = {"name": h["name_en"], "team": h["team"]["en"]}
    out = {k: (own[k].format(**fill_own), en[k].format(**fill_en)) for k in en}
    out["green_advice"] = (native(h, "advice", lang), h["advice"]["en"])
    return out


TEMPLATE_IDS = {"red": "TPL_CALL_RED_V1", "red_unconfirmed": "TPL_CALL_RED_UNCONFIRMED_V1",
                "nurse": "TPL_CALL_NURSE_V1", "nurse_other": "TPL_CALL_NURSE_V1", "amber": "TPL_CALL_AMBER_V1",
                "green": "TPL_CALL_GREEN_V1", "no_answer": "TPL_CALL_CAREGIVER_V1",
                "unreached": "TPL_WA_UNREACHED_V1"}


def closing(result: dict, h: dict) -> tuple[str, str]:
    """The fixed closing line for the tier. The only medical sentence is the doctor's own signed advice."""
    m = messages(h)
    if result["message"] != "green":
        return m[result["message"]]
    parts = [m["green"]] + ([m["green_advice"]] if result.get("repeat_advice") else []) + [m["green_end"]]
    return " ".join(p[0] for p in parts), " ".join(p[1] for p in parts)


# ---------------------------------------------------------------------------------------------------------------
# The rules (Part 5). Only fields that were asked can fire a rule: anything not asked stays NOT_ASKED.
# ---------------------------------------------------------------------------------------------------------------
FIELDS = ("slurred_or_confused", "cannot_stay_awake", "chest_pain", "arm_red_or_hot", "thermometer_used",
          "temperature_value", "bangles_tighter", "unsteady_or_dizzy_walking", "blurred_vision", "shaking_chills",
          "feels_hot", "headache_present", "headache_score", "headache_worse_than_last_time", "nausea_since_yesterday",
          "vomiting_count", "can_keep_liquids_down", "took_supplements_after_full_meals", "drowsy_most_of_today",
          "arm_pain_score", "arm_pain_sudden", "arm_swollen", "skin_break_left_arm", "tired_days",
          "tired_getting_worse")
DANGER_FIELDS = ("slurred_or_confused", "cannot_stay_awake", "chest_pain", "arm_red_or_hot", "thermometer_used",
                 "unsteady_or_dizzy_walking", "blurred_vision", "shaking_chills", "headache_score", "vomiting_count",
                 "can_keep_liquids_down")
RED_YES = {"slurred_or_confused": "RED_SLURRED_OR_CONFUSED", "cannot_stay_awake": "RED_CANNOT_STAY_AWAKE",
           "chest_pain": "RED_CHEST_PAIN", "arm_red_or_hot": "RED_ARM_RED_OR_HOT",
           "unsteady_or_dizzy_walking": "RED_UNSTEADY_OR_DIZZY", "blurred_vision": "RED_BLURRED_VISION",
           "shaking_chills": "RED_SHAKING_CHILLS"}
FLOORS = ("AMBER_PATIENT_SAYS_SEVERE", "AMBER_OTHER_SYMPTOM_REPORTED")

RED_PLAIN = {
    "RED_SLURRED_OR_CONFUSED": "Speech reported as slurred, or confusion",
    "RED_CANNOT_STAY_AWAKE": "Reported as so sleepy she cannot keep her eyes open",
    "RED_CHEST_PAIN": "Chest pain or hard to breathe reported",
    "RED_ARM_RED_OR_HOT": "Left arm reported as red or hot to touch",
    "RED_FEVER_100_4F_PLUS": "Temperature reported as 100.4 F (38.0 C) or more",
    "RED_UNSTEADY_OR_DIZZY": "Dizzy or unsteady when walking",
    "RED_BLURRED_VISION": "Blurred vision reported",
    "RED_SHAKING_CHILLS": "Shaking chills reported",
    "RED_VOMITING_6_PLUS": "Vomiting reported 6 or more times since yesterday",
    "RED_CANNOT_KEEP_LIQUIDS": "Reported as not able to keep water down",
    "RED_HEADACHE_SEVERE_7_PLUS": "Headache reported as 7 out of 10 or more",
    "RED_CANNOT_CONFIRM_SAFE": "Some answers could not be confirmed as safe",
    "RED_NO_ANSWER_2X": "She reported feeling unwell, then two calls were not answered",
    "RED_NOT_REACHED_AFTER_EMPTY_MESSAGE": "She sent an empty message, then two automated calls and two staff calls "
                                           "were not answered",
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


def _get(a: dict, f: str):
    """An answer; NOT_ASKED for a field an older episode never had."""
    return a.get(f, NOT_ASKED)


def temperature_f(a: dict):
    v = a.get("temperature_value")
    if a.get("thermometer_used") != YES or not _num(v):
        return None
    return float(v) if v >= 90 else round(v * 9 / 5 + 32, 1)


def red_codes(a: dict) -> list[str]:
    """Confirmed danger signs only: these stop the call at once."""
    codes = [code for f, code in RED_YES.items() if _get(a, f) == YES]
    f = temperature_f(a)
    if f is not None and f >= 100.4:
        codes.append("RED_FEVER_100_4F_PLUS")
    if _num(_get(a, "vomiting_count")) and a["vomiting_count"] >= 6:
        codes.append("RED_VOMITING_6_PLUS")
    if _get(a, "can_keep_liquids_down") == NO:
        codes.append("RED_CANNOT_KEEP_LIQUIDS")
    if _num(_get(a, "headache_score")) and a["headache_score"] >= 7:
        codes.append("RED_HEADACHE_SEVERE_7_PLUS")
    return codes


def _recurring(h: dict, today: date, symptom: str) -> bool:
    """Third report in 14 days, counting today's."""
    earlier = [e for e in h.get("events", []) if symptom in e.get("symptoms", [])
               and timedelta(0) <= today - date.fromisoformat(e["date"]) <= timedelta(days=14)]
    return len(earlier) + 1 >= 3


def triage(a: dict, h: dict, today: date, *, nurse: bool = False, fault: bool = False,
           no_answer: bool = False, floors=(), profile: str = "ROUTED", empty: bool = False,
           staff_unreached: bool = False) -> dict:
    """floors: reason codes the router set from her own message ("severe", a symptom with no questions). They can
    only raise the tier, never lower it.

    empty (Testing round 1 Nov, Worksheet2): her message had no words. Two unanswered automated calls then make a
    staff call within 15 minutes (Amber), not Red at once: she never said she was unwell. staff_unreached: the staff
    could not reach her either in two calls, so it is Red (no answer) and the family gets the standard message."""
    base = {**PROTOCOL, "protocol_profile": profile, "repeat_advice": False, "repeat_instruction_id": None,
            "floors": list(floors)}
    if no_answer and empty and not staff_unreached:
        return {**base, "tier": "TIER_2_AMBER", "reason_codes": ["AMBER_EMPTY_MESSAGE_NOT_REACHED"],
                "confirmed_danger": False, "red_kind": None, "action_code": "STAFF_CALL_15_MIN",
                "message": None, "patient_template_id": TEMPLATE_IDS["unreached"], "response_minutes": 15}
    if no_answer and staff_unreached:
        return {**base, "tier": "TIER_1_RED", "reason_codes": ["RED_NOT_REACHED_AFTER_EMPTY_MESSAGE"],
                "confirmed_danger": False, "red_kind": "NO_ANSWER", "action_code": "RED_PACKAGE_NO_ANSWER",
                "message": None, "patient_template_id": TEMPLATE_IDS["no_answer"], "response_minutes": 0}
    if no_answer:
        return {**base, "tier": "TIER_1_RED", "reason_codes": ["RED_NO_ANSWER_2X"], "confirmed_danger": False,
                "red_kind": "NO_ANSWER", "action_code": "RED_PACKAGE_NO_ANSWER", "message": None,
                "patient_template_id": TEMPLATE_IDS["no_answer"], "response_minutes": 0}
    reds = red_codes(a)
    confirmed = bool(reds)
    if fault:
        reds.append("RED_SYSTEM_FAULT")
    if any(_get(a, f) == UNKNOWN for f in DANGER_FIELDS):
        reds.append("RED_CANNOT_CONFIRM_SAFE")
    if reds:
        message = "red" if confirmed else "red_unconfirmed"
        return {**base, "tier": "TIER_1_RED", "reason_codes": reds, "confirmed_danger": confirmed,
                "red_kind": "CONFIRMED" if confirmed else "UNCONFIRMED",
                "action_code": "RED_PACKAGE_AND_DUTY_DOCTOR" if confirmed else "STAFF_CALLBACK_15_MIN",
                "message": message, "patient_template_id": TEMPLATE_IDS[message],
                "response_minutes": 0 if confirmed else 15}

    g = lambda f: _get(a, f)  # noqa: E731
    vomits = g("vomiting_count") if _num(g("vomiting_count")) else None
    ambers = []
    if g("bangles_tighter") == YES:
        ambers.append("AMBER_BANGLES_TIGHTER_THAN_BASELINE")
    if _num(g("headache_score")) and a["headache_score"] >= h["last_headache"]["score"] + 2:
        ambers.append("AMBER_HEADACHE_RISE_2_PLUS")
    if g("headache_worse_than_last_time") == YES:
        ambers.append("AMBER_HEADACHE_WORSE_THAN_LAST_TIME")
    if g("nausea_since_yesterday") == YES and g("took_supplements_after_full_meals") == YES:
        ambers.append("AMBER_NAUSEA_DESPITE_MEALS")
    if g("drowsy_most_of_today") == YES:
        ambers.append("AMBER_DROWSY_MOST_OF_TODAY")
    if vomits is not None and 2 <= vomits <= 5:
        ambers.append("AMBER_VOMITING_2_TO_5")
    if g("arm_swollen") == YES:
        ambers.append("AMBER_ARM_SWOLLEN")
    if g("skin_break_left_arm") == YES:
        ambers.append("AMBER_SKIN_BREAK_LEFT_ARM")
    if g("thermometer_used") == NO:
        if g("feels_hot") == YES:
            ambers.append("AMBER_FEELS_HOT_NO_THERMOMETER")
        elif g("headache_present") == YES or g("nausea_since_yesterday") == YES or \
                g("drowsy_most_of_today") == YES or (vomits or 0) > 0:
            ambers.append("AMBER_NO_THERMOMETER_WITH_SYMPTOMS")
    if _num(g("arm_pain_score")) and a["arm_pain_score"] >= 7:
        ambers.append("AMBER_ARM_PAIN_7_PLUS")
    if g("arm_pain_sudden") == YES:
        ambers.append("AMBER_ARM_PAIN_SUDDEN_ONSET")
    if _num(g("tired_days")) and a["tired_days"] >= 7:
        ambers.append("AMBER_TIREDNESS_7_DAYS_PLUS")
    if g("tired_getting_worse") == YES:
        ambers.append("AMBER_TIREDNESS_GETTING_WORSE")
    # Recurrence (third report in 14 days) for headache, nausea and arm pain; not for sleepiness in a routed call
    if g("headache_present") == YES and _recurring(h, today, "headache"):
        ambers.append("AMBER_RECURRING_3X_14D_HEADACHE")
    if g("nausea_since_yesterday") == YES and _recurring(h, today, "nausea"):
        ambers.append("AMBER_RECURRING_3X_14D_NAUSEA")
    if _num(g("arm_pain_score")) and _recurring(h, today, "arm_pain"):
        ambers.append("AMBER_RECURRING_3X_14D_ARM_PAIN")
    if any(g(f) == UNKNOWN for f in FIELDS if f not in DANGER_FIELDS):
        ambers.append("AMBER_CANNOT_CONFIRM")
    if nurse:
        ambers.append("AMBER_NURSE_REQUESTED")
    ambers += [f for f in FLOORS if f in floors]
    if ambers:
        # Amber workflow v2 (1 Nov): every Amber reason, pressing 0 and a symptom we have no questions for included,
        # goes to the duty doctor with a 2-hour callback. The nurse is not in the Amber path.
        return {**base, "tier": "TIER_2_AMBER", "reason_codes": ambers, "confirmed_danger": False,
                "action_code": "DOCTOR_CALLBACK_2H", "message": "amber",
                "patient_template_id": TEMPLATE_IDS["amber"], "response_minutes": 120}
    repeat = g("nausea_since_yesterday") == YES
    return {**base, "tier": "TIER_3_GREEN", "reason_codes": [], "confirmed_danger": False,
            "action_code": "GREEN_COACH_AND_FOLLOWUP_24H", "message": "green",
            "patient_template_id": TEMPLATE_IDS["green"], "response_minutes": 1440, "repeat_advice": repeat,
            "repeat_instruction_id": h["advice"].get("id") if repeat else None}


FACT_LABELS = {"slurred_or_confused": "Speech slurred or confused", "cannot_stay_awake": "Very hard to stay awake",
               "chest_pain": "Chest pain or hard to breathe", "arm_red_or_hot": "Left arm red or hot",
               "bangles_tighter": "Bangles tighter", "unsteady_or_dizzy_walking": "Dizzy or unsteady walking",
               "blurred_vision": "Blurred vision", "shaking_chills": "Shaking chills",
               "feels_hot": "Feels hot or feverish", "headache_present": "Headache today",
               "headache_score": "Headache (out of 10)", "headache_worse_than_last_time": "Headache worse than last time",
               "nausea_since_yesterday": "Nausea since yesterday", "vomiting_count": "Vomited (times since yesterday)",
               "can_keep_liquids_down": "Keeps water down",
               "took_supplements_after_full_meals": "Supplements only after full meals",
               "drowsy_most_of_today": "Sleepy most of today", "arm_pain_score": "Left arm pain (out of 10)",
               "arm_pain_sudden": "Arm pain started suddenly", "arm_swollen": "Left arm swollen or heavy",
               "skin_break_left_arm": "Cut, bite or prick on the left arm", "tired_days": "Tired for (days)",
               "tired_getting_worse": "Tiredness worse than last week"}


def _show(v) -> str:
    return str(int(v)) if _num(v) and v == int(v) else str(v)


def facts_text(a: dict) -> str:
    out = []
    if a.get("thermometer_used") == YES:
        out.append(f"Temperature {_show(a['temperature_value'])}")
    elif a.get("thermometer_used") == NO:
        out.append("No thermometer")
    out += [f"{label}: {_show(a[f])}" for f, label in FACT_LABELS.items() if a.get(f) not in (NOT_ASKED, None)]
    return "; ".join(out)


def why_lines(result: dict, a: dict, h: dict) -> list[str]:
    """The "Why this tier" panel (Part 5.4): fixed sentences with a date or number filled in. No interpretation."""
    arm, head, advice = h["arm_baseline"], h["last_headache"], h["advice"]
    fixed = {
        "AMBER_HEADACHE_WORSE_THAN_LAST_TIME": f"She says the headache is worse than on {day_en(head['date'])}.",
        "AMBER_NAUSEA_DESPITE_MEALS": "She still feels sick although she says she took her supplements only after "
                                      f"full meals, as the doctor advised on {day_en(advice['date'])}.",
        "AMBER_DROWSY_MOST_OF_TODAY": "She says she felt sleepy or groggy for most of today.",
        "AMBER_ARM_SWOLLEN": "She says the left arm looks swollen or feels heavy.",
        "AMBER_SKIN_BREAK_LEFT_ARM": "She reports a cut, a bite or a prick on the left arm.",
        "AMBER_FEELS_HOT_NO_THERMOMETER": "No temperature reading, and she feels hot or feverish.",
        "AMBER_NO_THERMOMETER_WITH_SYMPTOMS": "No temperature reading, and she has symptoms.",
        "AMBER_ARM_PAIN_SUDDEN_ONSET": "She says the arm pain started suddenly.",
        "AMBER_TIREDNESS_GETTING_WORSE": "She says the tiredness is worse than last week.",
        "AMBER_PATIENT_SAYS_SEVERE": "She described her problem as severe in her own message.",
        "AMBER_OTHER_SYMPTOM_REPORTED": "She mentioned a problem we have no questions for, so the duty doctor "
                                        "calls her.",
        "AMBER_CANNOT_CONFIRM": "Some answers could not be confirmed.",
        "AMBER_NURSE_REQUESTED": "She pressed 0 to ask for a doctor to call her back.",
        "AMBER_EMPTY_MESSAGE_NOT_REACHED": "Her message had no words, and two automated calls were not answered, "
                                           "so hospital staff call her.",
    }
    out = []
    for code in result["reason_codes"]:
        if code in RED_PLAIN:
            out.append(RED_PLAIN[code])
        elif code in fixed:
            out.append(fixed[code])
        elif code == "AMBER_BANGLES_TIGHTER_THAN_BASELINE":
            out.append(f"Left arm: {arm['status']} on {day_en(arm['date'])}. Today: bangles tighter.")
        elif code == "AMBER_HEADACHE_RISE_2_PLUS":
            out.append(f"Headache: {head['score']} out of 10 on {day_en(head['date'])}. "
                       f"Today: {_show(a['headache_score'])} out of 10.")
        elif code == "AMBER_VOMITING_2_TO_5":
            out.append(f"Vomited {_show(a['vomiting_count'])} times since yesterday.")
        elif code == "AMBER_ARM_PAIN_7_PLUS":
            out.append(f"Left arm pain reported as {_show(a['arm_pain_score'])} out of 10.")
        elif code == "AMBER_TIREDNESS_7_DAYS_PLUS":
            out.append(f"Tired for {_show(a['tired_days'])} days.")
        elif code.startswith("AMBER_RECURRING_3X_14D_"):
            out.append(f"This is the third report of {code[len('AMBER_RECURRING_3X_14D_'):].lower().replace('_', ' ')} "
                       "in 14 days.")
        else:
            out.append(code)
    if result["tier"] == "TIER_3_GREEN":
        out = ["No rule fired. " + facts_text(a)]
    return out


# ---------------------------------------------------------------------------------------------------------------
# Scripted answers for "Play as" (the guide's walk-throughs, Part 6, in each call language). One answer for every
# question in the bank, so a scenario plays on any call the router built; they go through the same parsing.
# ---------------------------------------------------------------------------------------------------------------
PRESS_0 = "<press 0>"
SAY = {  # the words, per language (drafts)
    "Tamil": {"no": "இல்லை", "yes": "ஆம்", "no_red": "இல்லை, சிவக்கவில்லை", "temp": "98.4", "zero": "பூஜ்யம்",
              "two": "இரண்டு", "three": "மூன்று", "five": "ஐந்து", "worse": "ஆம், அதிகமாக இருக்கிறது",
              "tight": "ஆம், ரொம்ப இறுக்கமாக இருக்கிறது", "nausea": "ஆம், நேற்றிலிருந்து",
              "meals": "ஆம், சாப்பிட்ட பிறகுதான் எடுக்கிறேன்", "a_little": "ஆம், கொஞ்சம்", "same": "இல்லை, முன்பு போலவே",
              "loose": "இல்லை, முன்பு போல் தளர்வாக இருக்கிறது", "this_morning": "ஆம், இன்று காலை",
              "empty_stomach": "இல்லை, இன்று காலை அவசரத்தில் வெறும் வயிற்றில் சாப்பிட்டேன்",
              "red_arm": "ஆம், சிவந்து எரிகிறது", "dont_know": "தெரியவில்லை", "no_thermometer": "வெப்பமானி இல்லை"},
    "Hindi": {"no": "नहीं", "yes": "हाँ", "no_red": "नहीं, लाल नहीं है", "temp": "98.4", "zero": "शून्य", "two": "दो",
              "three": "तीन", "five": "पाँच", "worse": "हाँ, ज़्यादा है", "tight": "हाँ, बहुत कसी लग रही हैं",
              "nausea": "हाँ, कल से", "meals": "हाँ, खाना खाने के बाद ही लेती हूँ", "a_little": "हाँ, थोड़ा सा",
              "same": "नहीं, पहले जैसा ही", "loose": "नहीं, पहले जैसी ढीली हैं", "this_morning": "हाँ, आज सुबह",
              "empty_stomach": "नहीं, आज सुबह जल्दी में खाली पेट ले ली", "red_arm": "हाँ, लाल है और जल रही है",
              "dont_know": "पता नहीं", "no_thermometer": "थर्मामीटर नहीं है"},
    "Telugu": {"no": "లేదు", "yes": "అవును", "no_red": "లేదు, ఎర్రగా లేదు", "temp": "98.4", "zero": "సున్నా",
               "two": "రెండు", "three": "మూడు", "five": "ఐదు", "worse": "అవును, ఎక్కువగా ఉంది",
               "tight": "అవును, చాలా బిగుతుగా ఉన్నాయి", "nausea": "అవును, నిన్నటి నుండి",
               "meals": "అవును, భోజనం తర్వాతే వేసుకుంటున్నాను", "a_little": "అవును, కొంచెం", "same": "లేదు, ముందులాగే ఉంది",
               "loose": "లేదు, ముందులాగే వదులుగా ఉన్నాయి", "this_morning": "అవును, ఈ రోజు ఉదయం",
               "empty_stomach": "లేదు, ఈ రోజు ఉదయం తొందరలో ఖాళీ కడుపుతో వేసుకున్నాను",
               "red_arm": "అవును, ఎర్రగా ఉంది, మండుతోంది", "dont_know": "తెలియదు", "no_thermometer": "థర్మామీటర్ లేదు"},
}
_CALM = {  # Scenario B (Green): mild answers to every question
    "Q_SLURRED": "no", "Q_AWAKE": "no", "Q_CHEST": "no", "Q_ARM_RED": "no_red", "Q_TEMP": "temp", "Q_BANGLES": "loose",
    "Q_DIZZY": "no", "Q_VISION": "no", "Q_CHILLS": "no", "Q_FEELS_HOT": "no", "Q_HEADACHE": "a_little",
    "Q_HEAD_SCORE": "two", "Q_HEAD_WORSE": "same", "Q_NAUSEA": "this_morning", "Q_VOMIT": "zero", "Q_LIQUIDS": "yes",
    "Q_SUPP": "empty_stomach", "Q_DROWSY": "no", "Q_ARM_SCORE": "two", "Q_ARM_SUDDEN": "no", "Q_SWOLLEN": "no",
    "Q_SKIN": "no", "Q_TIRED_DAYS": "two", "Q_TIRED_WORSE": "no",
}
_AMBER = {**_CALM, "Q_BANGLES": "tight", "Q_HEADACHE": "yes", "Q_HEAD_SCORE": "five", "Q_HEAD_WORSE": "worse",
          "Q_NAUSEA": "nausea", "Q_SUPP": "meals", "Q_DROWSY": "yes", "Q_ARM_SCORE": "five"}  # Scenario A (the hero)
_SCRIPTS = {
    "amber": _AMBER,
    "green": _CALM,
    "red": {**_CALM, "Q_ARM_RED": "red_arm"},                           # Scenario C: the call stops at the arm
    "mild_arm": {**_CALM, "Q_ARM_SCORE": "three"},                      # Scenario E: Amber only by her word "severe"
    "fever": {**_AMBER, "Q_TEMP": "101.2"},
    "unclear_chest": {**_AMBER, "Q_CHEST": "dont_know"},                # EC-A: twice "I don't know" -> Red, unconfirmed
    "no_thermometer": {**_CALM, "Q_TEMP": "no_thermometer", "Q_HEAD_SCORE": "three", "Q_NAUSEA": "no"},  # EC-C
    "nurse": {"Q_SLURRED": "no", "Q_AWAKE": PRESS_0},
}


def scenarios(lang: str = "Tamil") -> dict[str, dict]:
    w = SAY.get(lang, SAY["Tamil"])
    return {name: {q: w.get(k, k) for q, k in script.items()} for name, script in _SCRIPTS.items()}


SCENARIOS = scenarios("Tamil")
SCENARIO_LABELS = {"amber": "Amber (the hero scene)", "green": "Green (calm answers)",
                   "red": "Red (arm red and burning)", "mild_arm": "Mild answers, arm pain 3",
                   "fever": "Red (temperature 101.2)", "unclear_chest": "Red, unconfirmed (\"I don't know\" twice)",
                   "no_thermometer": "Amber (no thermometer, with a headache)", "nurse": "She presses 0 (doctor call-back)"}
