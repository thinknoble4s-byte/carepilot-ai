"""AI client layer (Use Case 2a, REQ-03/04): Sarvam for understanding replies, nothing else.

Two steps. Speech (Saaras v3) gives the text in the patient's language and an English translation
(typed text is translated with /translate). A language model then answers ONE fixed question,
"does this mention a health matter?", yes or no. It is biased to yes; unsure, slow, failed or
malformed output counts as yes. A validator rejects any severity, cause or diagnosis in the output.
For the "no" path the model drafts a one-or-two-line summary and an intent from a fixed list.

Each step gets the configured number of tries (default 2: one automatic retry) with a time limit.
Providers: "sarvam" (real API, key in SARVAM_API_KEY) or "fake" (deterministic, for tests and for
machines without a key). The demo switch "Sarvam off" makes every call fail, to test REQ-04.
"""
import base64
import hashlib
import json
import os
import re
import urllib.error
import urllib.request

from . import policy

FLAG_PROMPT_VERSION = "health-flag-v1"
DRAFT_PROMPT_VERSION = "reply-draft-v1"
FLAG_QUESTION = ("Does this message mention the patient's health, such as pain, a symptom, a medicine, feeling "
                 "unwell, a side effect, or a wish to be seen by the doctor? Answer yes or no.")
DRAFT_INSTRUCTION = (
    "Summarise the patient's message in one or two short English lines for a clinic coordinator, and pick the "
    "patient's intent from exactly this list: booked (the patient says she has booked or will come on a date), "
    "help_to_book, cant_come (add a short non-clinical reason such as cost or travel), pause_request, other. "
    "No clinical words, no advice, no judgement. Reply only with JSON: "
    '{"summary": "...", "intent": "...", "intent_reason": null}'
)
INTENTS = ("booked", "help_to_book", "cant_come", "pause_request", "other")
FORBIDDEN = re.compile(r"(severity|severe|urgent|urgency|risk|diagnos\w*|cause|triage|score|emergency)", re.I)
LANG_CODES = {"Hindi": "hi-IN", "Kannada": "kn-IN", "Tamil": "ta-IN", "Telugu": "te-IN", "English": "en-IN"}


class AIFailure(Exception):
    pass


def provider_name() -> str:
    return os.environ.get("AI_PROVIDER") or ("sarvam" if os.environ.get("SARVAM_API_KEY") else "fake")


def sarvam_enabled(conn) -> bool:
    row = conn.execute("SELECT value FROM demo_setting WHERE key = 'sarvam_enabled'").fetchone()
    return True if row is None else bool(row["value"])


def _cfg(conn) -> dict:
    ai = policy.settings(conn).get("ai") or {}
    return {"tries": int(ai.get("tries", 2)), "timeout": float(ai.get("timeout_seconds", 10)),
            "stt_model": ai.get("speech_model", "saaras:v3"), "llm_model": ai.get("language_model", "sarvam-105b")}


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------
class Sarvam:
    def __init__(self, timeout: float, stt_model: str, llm_model: str):
        self.key = os.environ.get("SARVAM_API_KEY", "")
        self.timeout, self.stt_model, self.llm_model = timeout, stt_model, llm_model

    def _json(self, url: str, body: dict, bearer: bool = False) -> dict:
        headers = {"api-subscription-key": self.key, "Content-Type": "application/json"}
        if bearer:
            headers["Authorization"] = f"Bearer {self.key}"
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:  # noqa: S310 (fixed https URL)
                return json.loads(r.read())
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            raise AIFailure(f"{url.rsplit('/', 1)[-1]}: {e}") from None

    def speech(self, audio: bytes, mime: str, language: str, mode: str, clip_id: str | None = None) -> str:
        boundary = "----carepilot"
        fields = {"model": self.stt_model, "mode": mode, "language_code": LANG_CODES.get(language, "unknown")}
        parts = [f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
                 for k, v in fields.items()]
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"note.wav\"\r\n"
                     f"Content-Type: {mime}\r\n\r\n".encode() + audio + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        req = urllib.request.Request("https://api.sarvam.ai/speech-to-text", data=b"".join(parts), method="POST",
                                     headers={"api-subscription-key": self.key,
                                              "Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:  # noqa: S310
                return json.loads(r.read())["transcript"]
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as e:
            raise AIFailure(f"speech-to-text: {e}") from None

    def translate(self, text: str, language: str) -> str:
        if language == "English":
            return text
        return self._json("https://api.sarvam.ai/translate", {
            "input": text, "source_language_code": LANG_CODES.get(language, "auto"), "target_language_code": "en-IN",
            "model": "mayura:v1"})["translated_text"]

    def chat(self, system: str, user: str) -> str:
        body = self._json("https://api.sarvam.ai/v1/chat/completions", {
            "model": self.llm_model, "temperature": 0, "max_tokens": 300, "reasoning_effort": None,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}, bearer=True)
        content = (body.get("choices") or [{}])[0].get("message", {}).get("content")
        if not content:
            raise AIFailure("empty model answer")
        return content

    def tts(self, text: str, language: str, speaker: str, model: str) -> bytes:
        """Bulbul: a fixed call line -> WAV (Voice Triage)."""
        body = self._json("https://api.sarvam.ai/text-to-speech", {
            "text": text, "language_code": LANG_CODES.get(language, "en-IN"), "model": model, "speaker": speaker,
            "pace": 0.95, "speech_sample_rate": 22050, "output_audio_codec": "wav"})
        try:
            return base64.b64decode(body["audios"][0])
        except (KeyError, IndexError, ValueError) as e:
            raise AIFailure(f"text-to-speech: unexpected answer ({e})") from None


# Synthetic clips used by the demo (stand-ins until native recordings arrive).
FAKE_CLIPS = {
    "HI-01": ("पिछले एक हफ्ते से मेरी बांह में दर्द है, मुझे डॉक्टर को दिखाना है।",
              "I have been having pain in my arm for the past week, I need to see a doctor."),
    "HI-02": ("मैं शनिवार को अपने बेटे के साथ आऊँगी, कृपया सुबह का समय दीजिए।",
              "I will come with my son on Saturday, please give me the morning time."),
    "HI-03": ("मैं बिल्कुल ठीक हूँ, मुझे कोई शिकायत नहीं है।",
              "I am perfectly fine, I have no complaints."),
    # Voice Triage: the guide's voice note (section 1.3) in Hindi (Savitri) and Telugu (Nirmala)
    "HI-04": ("कल से मुझे बहुत नींद आ रही है, जी मिचला रहा है, सिरदर्द है, और बाईं बाँह में दर्द है।",
              "Since yesterday I feel very sleepy, I feel nauseous, I have a headache, and pain in my left arm."),
    "TE-01": ("నిన్నటి నుండి నాకు చాలా నిద్రగా ఉంది, వికారంగా ఉంది, తలనొప్పిగా ఉంది, ఎడమ చేతిలో నొప్పిగా ఉంది.",
              "Since yesterday I feel very sleepy, I feel nauseous, I have a headache, and pain in my left arm."),
    # Voice Triage: Lakshmi's voice note (guide v1.1, section 1.3)
    "TA-01": ("நேற்றிலிருந்து எனக்கு ரொம்ப தூக்கமாக இருக்கிறது, குமட்டல் இருக்கிறது, தலைவலி இருக்கிறது, இடது கையில் வலி இருக்கிறது.",
              "Since yesterday I feel very sleepy, I have nausea, a headache, and pain in my left arm."),
    # Voice Triage v1.2: severe arm pain, tired, shopping, in each patient's language
    "HI-05": ("मेरे बाएँ हाथ में बहुत तेज़ दर्द है।", "I have severe pain in my left arm."),
    "HI-06": ("पिछले दस दिनों से मैं बहुत थकी हुई महसूस कर रही हूँ।", "I have been feeling very tired for the last ten days."),
    "HI-07": ("मैं कल शॉपिंग के लिए जाना चाहती हूँ।", "I want to go shopping tomorrow."),
    "TE-02": ("నా ఎడమ చేతిలో తీవ్రమైన నొప్పి ఉంది.", "I have severe pain in my left arm."),
    "TE-03": ("గత పది రోజులుగా నాకు చాలా అలసటగా ఉంది.", "I have been feeling very tired for the last ten days."),
    "TE-04": ("నేను రేపు షాపింగ్‌కి వెళ్లాలనుకుంటున్నాను.", "I want to go shopping tomorrow."),
    "TA-02": ("என் இடது கையில் கடுமையான வலி இருக்கிறது.", "I have severe pain in my left arm."),
    "TA-03": ("கடந்த பத்து நாட்களாக எனக்கு ரொம்ப சோர்வாக இருக்கிறது.", "I have been feeling very tired for the last ten days."),
    "TA-04": ("நான் நாளைக்கு ஷாப்பிங் போக வேண்டும்.", "I want to go shopping tomorrow."),
}
HEALTH_WORDS = re.compile(r"pain|ache|hurt|fever|swell|bleed|breath|sick|unwell|tablet|medicine|side effect|doctor|"
                          r"worried|concern|दर्द|बुखार|डॉक्टर|डाक्टर|ನೋವು|ಜ್ವರ|வலி|காய்ச்சல்|குமட்டல்|வாந்தி|மருத்துவர்|"
                          r"उल्टी|जी मिचला|నొప్పి|జ్వరం|వికారం|వాంతి|డాక్టర్", re.I)

FAKE_TAGS = (  # the simulated tagger: English words in the translation -> a tag from the fixed list
    ("headache", re.compile(r"headache", re.I)),
    ("nausea_vomiting", re.compile(r"nause|vomit", re.I)),
    ("sleepiness", re.compile(r"sleepy|drowsy|groggy", re.I)),
    ("tiredness", re.compile(r"tired|weak", re.I)),
    ("arm_pain", re.compile(r"(pain|hurts?|aches?) in my (left )?arm|arm (pain|hurts)", re.I)),
    ("arm_swelling_or_redness", re.compile(r"arm is (swollen|red)|swelling|swollen", re.I)),
    ("fever_chills", re.compile(r"fever|chills|shiver", re.I)),
    ("chest_or_breathing", re.compile(r"chest|breath", re.I)),
    ("dizziness_or_vision", re.compile(r"dizz|vision|blurr", re.I)),
    ("other_symptom", re.compile(r"rash|itch|cough|bleed", re.I)),
)
SEVERE_WORDS = re.compile(r"severe|unbearable|very bad", re.I)


class Fake:
    """Deterministic stand-in (no network): known clips, simple word rules. Labelled in the audit."""
    stt_model = "fake-saaras"
    llm_model = "fake-language-model"

    def speech(self, audio: bytes, mime: str, language: str, mode: str, clip_id: str | None = None) -> str:
        if clip_id in FAKE_CLIPS:
            hi, en = FAKE_CLIPS[clip_id]
            return hi if mode == "transcribe" else en
        raise AIFailure("fake speech: unknown clip")

    def translate(self, text: str, language: str) -> str:
        for hi, en in FAKE_CLIPS.values():
            if text.strip() == hi:
                return en
        return text

    def chat(self, system: str, user: str) -> str:
        if "Return the JSON now" in user:  # Voice Triage answer value: the simulated model cannot tell
            return '{"value": "UNKNOWN"}'
        message = user.split("Message:", 1)[-1]
        if "Return only JSON with three fields" in user:  # Voice Triage v1.2 tagger: fixed word rules
            symptoms = [tag for tag, words in FAKE_TAGS if words.search(message)]
            other = re.search(r"shopping|travel|wedding|hello|thank|will come|booked|appointment", message, re.I)
            health = "YES" if symptoms or HEALTH_WORDS.search(message) else "NO" if other else "UNSURE"
            return json.dumps({"health_related": health, "symptoms": symptoms,
                               "severity_word": "SEVERE" if SEVERE_WORDS.search(message) else "NONE"})
        if "Answer yes or no" in user:
            return "yes" if HEALTH_WORDS.search(message) else "no"
        intent = "booked" if re.search(r"come|booked|आऊँगी|शनिवार", message, re.I) else "other"
        well = re.search(r"perfectly fine|no complaints|ठीक", message, re.I)
        summary = ("Patient says she will come on Saturday with her son and asks for a morning time." if intent == "booked"
                   else "Patient says she is well and has no complaints." if well else "Patient sent a message.")
        return json.dumps({"summary": summary, "intent": intent, "intent_reason": None})


def _provider(conn):
    c = _cfg(conn)
    return Fake() if provider_name() == "fake" else Sarvam(c["timeout"], c["stt_model"], c["llm_model"])


def _tries(conn, fn, errors: list):
    """Call fn up to the configured number of tries; returns (result or None, attempts)."""
    attempts = 0
    for _ in range(_cfg(conn)["tries"]):
        attempts += 1
        try:
            if not sarvam_enabled(conn):
                raise AIFailure("Sarvam switched off (demo test switch)")
            return fn(), attempts
        except AIFailure as e:
            errors.append(str(e))
    return None, attempts


# ---------------------------------------------------------------------------
# What the rest of CarePilot calls
# ---------------------------------------------------------------------------
def validate_flag(answer: str) -> str | None:
    """'yes' / 'no', or None when the output is not a clean yes/no (then it counts as yes)."""
    word = re.sub(r"[^a-z]", "", (answer or "").strip().lower())
    return word if word in ("yes", "no") else None


def validate_draft(answer: str) -> dict | None:
    text = re.sub(r"^```(?:json)?|```$", "", (answer or "").strip(), flags=re.M).strip()
    try:
        d = json.loads(text)
    except ValueError:
        return None
    if not isinstance(d, dict) or set(d) - {"summary", "intent", "intent_reason"}:
        return None  # any extra field (severity, cause, diagnosis, ...) is rejected
    if d.get("intent") not in INTENTS or not isinstance(d.get("summary"), str) or not d["summary"].strip():
        return None
    if FORBIDDEN.search(d["summary"]) or FORBIDDEN.search(str(d.get("intent_reason") or "")):
        return None
    return {"summary": d["summary"].strip()[:300], "intent": d["intent"],
            "intent_reason": (str(d["intent_reason"]).strip()[:100] if d.get("intent_reason") else None)}


def understand(conn, *, language: str, audio: bytes | None = None, mime: str = "audio/wav",
               text: str | None = None, clip_id: str | None = None, tags: bool = False) -> dict:
    """Text in the patient's language, an English translation and the one yes/no flag.
    Any failure on the way makes the flag 'yes' (the safe path).

    tags=True (Voice Triage v1.2, for patients on the triage path): the model tags the message from fixed lists
    instead of the yes/no question; the router reads the tags, and the flag is "no" only on the router's standard
    reply route. Broken or missing tags count as health (the fixed 12 questions)."""
    if tags:
        return _understand_tags(conn, language=language, audio=audio, mime=mime, text=text, clip_id=clip_id)
    p, errors, attempts = _provider(conn), [], 0
    if audio is not None:
        transcript, n1 = _tries(conn, lambda: p.speech(audio, mime, language, "transcribe", clip_id), errors)
        translation, n2 = _tries(conn, lambda: p.speech(audio, mime, language, "translate", clip_id), errors)
        attempts = max(n1, n2)
    else:
        transcript = text
        translation, attempts = _tries(conn, lambda: p.translate(text, language), errors)
    flag, failed = None, transcript is None or translation is None
    if not failed:
        answer, n = _tries(conn, lambda: p.chat(
            "You answer with exactly one word: yes or no. When in doubt, answer yes.",
            f"{FLAG_QUESTION}\n\nMessage: {transcript}\n(English translation: {translation})"), errors)
        attempts = max(attempts, n)
        flag = validate_flag(answer) if answer is not None else None
        if answer is None:
            failed = True
        elif flag is None:
            errors.append(f"unclear answer {answer[:40]!r}: counted as yes")
    return {"transcript": transcript, "translation": translation, "flag": flag or "yes", "failed": failed,
            "unsure": flag is None, "attempts": attempts, "errors": errors,
            "model": f"{p.stt_model} + {p.llm_model}" + (" (simulated)" if isinstance(p, Fake) else ""),
            "prompt_version": FLAG_PROMPT_VERSION}


def _understand_tags(conn, *, language, audio, mime, text, clip_id) -> dict:
    from . import triage_router

    p, errors, attempts = _provider(conn), [], 0
    if audio is not None:
        transcript, n1 = _tries(conn, lambda: p.speech(audio, mime, language, "transcribe", clip_id), errors)
        translation, n2 = _tries(conn, lambda: p.speech(audio, mime, language, "translate", clip_id), errors)
        attempts = max(n1, n2)
    else:
        transcript = text
        translation, attempts = _tries(conn, lambda: p.translate(text, language), errors)
    raw, failed = None, transcript is None or translation is None
    if not failed:
        answer, n = _tries(conn, lambda: p.chat(
            "You tag a patient's message from fixed lists. You never give advice and never judge how serious it is.",
            f"{triage_router.TAG_PROMPT}\n\nMessage: {transcript}\n(English translation: {translation})"), errors)
        attempts = max(attempts, n)
        raw = triage_router.parse_tags(answer) if answer is not None else None
        if answer is None:
            failed = True
        elif triage_router.clean_tags(raw)["valid"] is False:
            errors.append(f"tags unusable {answer[:60]!r}: the fixed 12 questions are asked")
    r = triage_router.route(raw)
    return {"transcript": transcript, "translation": translation,
            "flag": "no" if r["route"] == "STANDARD_REPLY" else "yes", "failed": failed,
            "unsure": not r["tags"]["valid"] or r["tags"]["health_related"] == "UNSURE", "attempts": attempts,
            "errors": errors, "tags": raw, "clean_tags": r["tags"], "route": r["route"],
            "model": f"{p.stt_model} + {p.llm_model}" + (" (simulated)" if isinstance(p, Fake) else ""),
            "prompt_version": triage_router.TAG_PROMPT_VERSION}


def draft(conn, *, transcript: str, translation: str) -> dict:
    """The proposal draft for the 'no' path: summary + intent, or failed (the coordinator reads the original)."""
    p, errors = _provider(conn), []
    answer, attempts = _tries(conn, lambda: p.chat(
        "You write short neutral summaries for a clinic coordinator. You never give clinical opinions.",
        f"{DRAFT_INSTRUCTION}\n\nMessage: {transcript}\n(English translation: {translation})"), errors)
    d = validate_draft(answer) if answer is not None else None
    return {**(d or {"summary": None, "intent": None, "intent_reason": None}), "failed": d is None,
            "attempts": attempts, "errors": errors, "model": p.llm_model, "prompt_version": DRAFT_PROMPT_VERSION}


# ---------------------------------------------------------------------------
# Voice Triage (MVP-lite): Bulbul speaks the fixed lines, Saaras hears each answer, the model is asked only when
# the word lists cannot read an answer, and its value is checked by triage_rules. Nothing here decides a tier.
# ---------------------------------------------------------------------------
class TriageAIFailure(AIFailure):
    pass


def _triage_cfg(conn) -> dict:
    t = policy.settings(conn).get("voice_triage") or {}
    return {"speaker": t.get("speaker", "kavitha"), "tts_model": t.get("tts_model", "bulbul:v3")}


def speak(conn, text: str, language: str, cached_only: bool = False) -> tuple[bytes, str] | None:
    """Bulbul audio of one fixed line, made once and kept (tts_cache). None in simulated mode or on failure: the
    screen then shows the text only. cached_only: never call Sarvam (a line not made yet gives None)."""
    cfg = _triage_cfg(conn)
    key = hashlib.sha256(f"{cfg['tts_model']}|{cfg['speaker']}|{language}|{text}".encode()).hexdigest()
    row = conn.execute("SELECT data, mime FROM tts_cache WHERE cache_key = %s", (key,)).fetchone()
    if row:
        return bytes(row["data"]), row["mime"]
    if cached_only or provider_name() == "fake" or not sarvam_enabled(conn):
        return None
    p = _provider(conn)
    try:
        audio = p.tts(text, language, cfg["speaker"], cfg["tts_model"])
    except AIFailure:
        return None
    conn.execute("INSERT INTO tts_cache (cache_key, mime, data) VALUES (%s, 'audio/wav', %s) ON CONFLICT DO NOTHING",
                 (key, audio))
    return audio, "audio/wav"


def hear(conn, *, language: str, audio: bytes, mime: str) -> dict:
    """A spoken answer -> text in her language and an English translation. Raises TriageAIFailure."""
    p, errors = _provider(conn), []
    transcript, _ = _tries(conn, lambda: p.speech(audio, mime, language, "transcribe"), errors)
    translation, _ = _tries(conn, lambda: p.speech(audio, mime, language, "translate"), errors)
    if transcript is None or translation is None:
        raise TriageAIFailure("; ".join(errors) or "speech-to-text failed")
    return {"transcript": transcript, "translation": translation, "model": p.stt_model}


def translate(conn, text: str, language: str) -> str:
    p, errors = _provider(conn), []
    out, _ = _tries(conn, lambda: p.translate(text, language), errors)
    if out is None:
        raise TriageAIFailure("; ".join(errors) or "translation failed")
    return out


def answer_value_reply(conn, *, kind: str, question_en: str, transcript: str, translation: str) -> tuple[str, str]:
    """Ask the model for one value (JSON). Returns (raw reply, model); triage_rules.model_value checks it."""
    from .triage_rules import MODEL_SYSTEM

    p, errors = _provider(conn), []
    label = {"yesno": "yes/no", "number": "number", "temperature": "temperature"}[kind]
    user = (f"Question ({label}): {question_en}\nAnswer: {transcript}\nEnglish translation: {translation}\n"
            "Return the JSON now.")
    reply, _ = _tries(conn, lambda: p.chat(MODEL_SYSTEM, user), errors)
    if reply is None:
        raise TriageAIFailure("; ".join(errors) or "model failed")
    return reply, p.llm_model + (" (simulated)" if isinstance(p, Fake) else "")
