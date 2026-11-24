"""UC1-V sections 7 and 10.1: the line library and the synthetic patient's clips (seed_data/uc1v/library.json).

R1: a spoken sentence is a library line with its curly-bracket values filled in, and nothing else. No other module
holds words for the agent, and the AI has no way to add one.
"""
import hashlib
import re
from datetime import date, time

from . import reference
from .errors import Conflict

VARIABLE = re.compile(r"\{([^{}]+)\}")


def library() -> dict:
    return reference.uc1v_library()


def pack(language: str) -> dict | None:
    return library()["languages"].get(language)


def named(language: str, full_name: str) -> dict | None:
    """The checked spelling of a patient's name. A name is never transliterated on the fly."""
    return ((pack(language) or {}).get("names") or {}).get(full_name)


def script_gap(language: str, patient: dict, card: dict, plan: dict | None) -> tuple[str, str] | None:
    """Why the fixed lines would not be true for this patient and this card; None when they are (scope A: Hindi,
    DEXA, Letrozole). The therapy must be plainly Letrozole: any word not on the short list ("stopped") is a no."""
    p = pack(language)
    if not p:
        return "no_lines_in_preferred_language", f"No fixed script in {language}"
    want = p["written_for"]
    if card.get("test_code") != want["test_code"]:
        test = re.sub(r" \(.*\)$", "", card["name"])
        return "no_script_for_this_test", f"No fixed script for {test}: only for the DEXA scan"
    plan = plan or {}
    if plan.get("template_id") not in want["plans"]:
        therapy = (plan.get("therapy") or "").strip()
        words = re.findall(r"[^\W\d_]+|\d+", therapy.casefold()) if therapy.isascii() else therapy.split()
        medicine = [w.casefold() for w in want["medicine"]]
        if not any(w in medicine for w in words) or any(w not in medicine + want["therapy_words"] for w in words):
            return "therapy_not_letrozole", f'The script is only for Letrozole; the therapy on the plan is "{therapy}"'
    if not named(language, patient["full_name"]):
        return "no_name_in_library", f"No checked {language} spelling of {patient['full_name']}"
    return None


def slot_values(language: str, day: date, at: time) -> tuple[dict, dict]:
    p, hhmm = pack(language), f"{at:%H:%M}"
    if hhmm not in p["times"]:
        raise Conflict(f"The library has no words for the time {hhmm}")
    words = {"दिन": p["days"][day.weekday()], "तारीख़": f"{day.day} {p['months'][day.month - 1]}",
             "समय": p["times"][hhmm]}
    english = {"day": f"{day:%A}", "date": f"{day.day} {day:%B}", "time": library()["times_en"][hhmm]}
    return words, english


def values(language: str, patient: dict | None = None, slot: dict | None = None) -> tuple[dict, dict]:
    words, english = dict(pack(language)["values"]), dict(library()["values_en"])
    full = patient["full_name"] if patient else None
    if full and (name := named(language, full)):
        words.update({k: v for k, v in name.items() if k != "clip"})
        english.update(full_name=full, first_name=full.split()[0], surname=full.split()[-1])
    if slot:
        w, e = slot_values(language, slot["slot_date"], slot["slot_time"])
        words.update(w)
        english.update(e)
    return words, english


def fill(text: str, known: dict) -> str:
    """Variables only. One without a value stops the line: a placeholder is never spoken."""
    missing = [v for v in VARIABLE.findall(text) if v not in known]
    if missing:
        raise Conflict("No value for " + ", ".join(missing))
    return VARIABLE.sub(lambda m: known[m.group(1)], text)


def line(conn, line_id: str, language: str, patient: dict, slot: dict | None = None) -> dict:
    row = conn.execute("SELECT * FROM voice_line WHERE line_id = %s AND language = %s", (line_id, language)).fetchone()
    if row is None:
        raise Conflict(f"No library line {line_id} in {language}")
    words, english = values(language, patient, slot)
    return {**row, "spoken": fill(row["text"], words), "spoken_en": fill(row["text_en"], english)}


def agent_audio_path(language: str, text: str, speaker: str, model: str):
    """A recorded line is found by the exact words, voice and model: changed wording never plays an old recording."""
    key = hashlib.sha256(f"{model}|{speaker}|{language}|{text}".encode()).hexdigest()[:24]
    return reference.SEED_DIR / "uc1v" / "audio" / "agent" / f"{key}.mp3"


def agent_audio(language: str, text: str, speaker: str, model: str) -> tuple[bytes, str] | None:
    path = agent_audio_path(language, text, speaker, model)
    return (path.read_bytes(), "audio/mpeg") if path.exists() else None


def clip(language: str, clip_id: str | None, patient: dict, script: str | None = None) -> dict | None:
    """A patient clip. One that says her name has its own recording for each name, and a script with a patient_voice
    (S2 and S3) has its own takes: `take` is that recording's id."""
    p = pack(language)
    found = p and p["clips"].get(clip_id or "")
    if not found:
        return None
    take = clip_id
    if VARIABLE.search(found["text"]):
        if not (name := named(language, patient["full_name"])):
            return None
        words, english = values(language, patient)
        found = {"text": fill(found["text"], words), "text_en": fill(found["text_en"], english)}
        take += name["clip"]
    if script and reference.uc1v()["scripts"].get(script, {}).get("patient_voice"):
        take += f"-{script}"
    path = reference.SEED_DIR / "uc1v" / "audio" / language.lower() / f"{take}.wav"
    return {**found, "clip_id": clip_id, "take": take, "audio": path.read_bytes() if path.exists() else None}
