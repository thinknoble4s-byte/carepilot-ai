"""Voice Triage v1.2, Part 3: the router. The model only tags the voice note from fixed lists (ai.tag_message);
plain rules here decide two things:

1. ROUTE: a health message gets the triage call; a message that is clearly not about health gets one standard reply
   and nothing else. The only way to the standard reply is health_related NO, no symptom and no severe word.
2. QUESTIONS: the danger core (always, first), the bangles question when her record has a left-arm baseline, then the
   modules for what she mentioned. Broken tags, or no symptom she named, ask the fixed 12.

The model never writes a question, never picks a tier, and never gives advice. Her own words can only add questions
or raise a floor ("severe" = at least Amber; a symptom we have no questions for = at least Amber and a nurse calls).
"""
import json
import re

SYMPTOM_TAGS = ("headache", "nausea_vomiting", "sleepiness", "tiredness", "arm_pain", "arm_swelling_or_redness",
                "fever_chills", "chest_or_breathing", "dizziness_or_vision", "other_symptom")
HEALTH = ("YES", "NO", "UNSURE")
CORE = ["Q_SLURRED", "Q_AWAKE", "Q_CHEST", "Q_ARM_RED", "Q_TEMP"]  # always asked, in this order
HISTORY_ARM = ["Q_BANGLES"]  # added when her record has a left-arm baseline
MODULES = {  # tag -> questions added (this order is the order they are asked: danger-type modules first)
    "dizziness_or_vision": ["Q_DIZZY", "Q_VISION"],
    "fever_chills": ["Q_CHILLS", "Q_FEELS_HOT"],
    "headache": ["Q_HEADACHE", "Q_HEAD_SCORE", "Q_HEAD_WORSE"],
    "nausea_vomiting": ["Q_NAUSEA", "Q_VOMIT", "Q_LIQUIDS", "Q_SUPP"],
    "sleepiness": ["Q_DROWSY"],
    "arm_pain": ["Q_ARM_SCORE", "Q_ARM_SUDDEN"],
    "arm_swelling_or_redness": ["Q_SWOLLEN", "Q_SKIN"],
    "tiredness": ["Q_TIRED_DAYS", "Q_TIRED_WORSE"],
    "chest_or_breathing": [],  # already in the core
    "other_symptom": [],  # no question covers it: a floor makes a nurse call
}
MODULE_LABELS = {"dizziness_or_vision": "dizziness and vision", "fever_chills": "chills and feeling hot",
                 "headache": "headache", "nausea_vomiting": "nausea and vomiting", "sleepiness": "sleepiness",
                 "arm_pain": "arm pain", "arm_swelling_or_redness": "arm swelling and skin", "tiredness": "tiredness"}
TAG_LABELS = {"headache": "headache", "nausea_vomiting": "nausea", "sleepiness": "sleepy", "tiredness": "tired",
              "arm_pain": "arm pain", "arm_swelling_or_redness": "arm swelling", "fever_chills": "fever or chills",
              "chest_or_breathing": "chest or breathing", "dizziness_or_vision": "dizzy or vision",
              "other_symptom": "other symptom"}
FALLBACK = ["Q_SLURRED", "Q_AWAKE", "Q_CHEST", "Q_ARM_RED", "Q_TEMP", "Q_VOMIT", "Q_HEADACHE", "Q_HEAD_SCORE",
            "Q_HEAD_WORSE", "Q_BANGLES", "Q_NAUSEA", "Q_SUPP"]  # the fixed 12, when the tags cannot be trusted
FLOORS = {"SEVERE": "AMBER_PATIENT_SAYS_SEVERE", "OTHER": "AMBER_OTHER_SYMPTOM_REPORTED"}

TAG_PROMPT_VERSION = "triage-tags-v1"
TAG_PROMPT = (
    "Read this message from a patient. Return only JSON with three fields:\n"
    "health_related: YES if she mentions how she feels or any symptom, NO if it is clearly about something else "
    "(shopping, travel, greetings), UNSURE if you cannot tell.\n"
    "symptoms: a list using only these words: " + ", ".join(SYMPTOM_TAGS) + ". Use other_symptom for any health "
    "problem not in the list.\n"
    "severity_word: SEVERE only if she herself uses a word like severe, unbearable, very bad. Else NONE.\n"
    "Do not give advice. Do not decide if it is serious. Do not add anything else."
)


def parse_tags(reply: str | None):
    """The model's reply -> the JSON object it holds, or None (then the tags are treated as broken)."""
    match = re.search(r"\{.*\}", reply or "", re.S)
    if not match:
        return None
    try:
        d = json.loads(match.group(0))
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def clean_tags(raw) -> dict:
    """Make the model's tags safe. Anything odd is replaced by the cautious reading."""
    ok = isinstance(raw, dict) and raw.get("health_related") in HEALTH and isinstance(raw.get("symptoms"), list)
    if not ok:
        return {"valid": False, "health_related": "UNSURE", "symptoms": [], "severity_word": "NONE"}
    seen = []
    for s in raw["symptoms"]:
        tag = s if s in SYMPTOM_TAGS else "other_symptom"  # a tag we do not know is a symptom we cannot ask about
        if tag not in seen:
            seen.append(tag)
    return {"valid": True, "health_related": raw["health_related"], "symptoms": seen,
            "severity_word": "SEVERE" if raw.get("severity_word") == "SEVERE" else "NONE"}


def route(raw) -> dict:
    """STANDARD_REPLY only when the message is clearly not about health. Everything else is a triage call."""
    t = clean_tags(raw)
    if t["valid"] and t["health_related"] == "NO" and not t["symptoms"] and t["severity_word"] == "NONE":
        return {"route": "STANDARD_REPLY", "reason": "NOT_HEALTH", "tags": t}
    return {"route": "TRIAGE", "reason": "TAGS_UNUSABLE" if not t["valid"] else "HEALTH_OR_UNSURE", "tags": t}


def plan(h: dict, raw) -> dict:
    """The plan for the call: route, profile, ordered question ids, modules, floors and the cleaned tags."""
    r = route(raw)
    t = r["tags"]
    if r["route"] == "STANDARD_REPLY":
        return {"route": "STANDARD_REPLY", "profile": None, "question_ids": [], "modules": [], "floors": [], "tags": t}
    floors = [FLOORS["SEVERE"]] if t["severity_word"] == "SEVERE" else []
    if not t["valid"] or not t["symptoms"]:  # cannot tell what she said: ask the fixed 12
        ids, profile, modules = list(FALLBACK), "DEMO_LITE", ["fallback"]
    else:
        ids, profile = list(CORE), "ROUTED"
        arm = bool((h or {}).get("arm_baseline"))
        if arm:
            ids += HISTORY_ARM
        modules = ["core"] + (["history_arm"] if arm else [])
        for tag, qs in MODULES.items():  # MODULES order, not the order she said them
            if tag in t["symptoms"]:
                ids += [q for q in qs if q not in ids]
                modules.append(tag)
        if "other_symptom" in t["symptoms"]:
            floors.append(FLOORS["OTHER"])
    return {"route": "TRIAGE", "profile": profile, "question_ids": ids, "modules": modules, "floors": floors,
            "tags": t, "reason": r["reason"]}


def not_asked(p: dict) -> list[str]:
    """The modules this call did not cover, for the summary's "Not asked" line."""
    if "fallback" in p.get("modules", []):
        asked = {"headache", "nausea_vomiting"}
    else:
        asked = set(p.get("modules", []))
    return [label for tag, label in MODULE_LABELS.items() if tag not in asked]
