"""Reference data shipped with the app (Step A mock data, all synthetic).

seed_data/ holds the files from the Step A student guide, section 4. Users and
the allowed values come from here; demo reset loads the rest into the database.
"""
import json
from datetime import date
from functools import cache
from pathlib import Path

SEED_DIR = Path(__file__).resolve().parent / "seed_data"


def read_seed(name: str) -> str:
    return (SEED_DIR / name).read_text(encoding="utf-8")


@cache
def _json(name: str):
    return json.loads(read_seed(name))


def config() -> dict:
    return _json("config.json")


def plan_template() -> dict:
    return _json("care_plan_template.json")


STEP_A_USERS: list[dict] = _json("users.json")


def uc1() -> dict:
    """Use Case 1 seed: patients, people, plan template, template, settings (seed_data/uc1)."""
    return _json("uc1/scenario.json")


def uc2a() -> dict:
    """Use Case 2a "after Use Case 1" seed (seed_data/uc2a)."""
    return _json("uc2a/scenario.json")


def step_a_settings() -> dict:
    return _json("settings.json")


# Everyone who can be picked in "Viewing as" across the demo data sets (synthetic users).
USERS: list[dict] = STEP_A_USERS + [u for u in _json("uc1/scenario.json")["users"]
                                    if u["user_id"] not in {s["user_id"] for s in STEP_A_USERS}]
USERS_BY_ID = {u["user_id"]: u for u in USERS}
ROLE_LABELS = {"coordinator": "Care coordinator", "oncologist": "Oncologist", "chief": "Chief of hospital"}

LANGUAGES = config()["supported_languages"]
DEPARTMENTS = config()["departments"]
CONSENT_SCOPES = config()["consent_scopes"]
IDENTIFIER_OPTIONS = config()["identifier_options"]
DEMO_START_DATE = date.fromisoformat(config()["demo_start_date"])


def autonomy_policy() -> dict:
    return _json("autonomy_policy.json")


def deadline_policy() -> dict:
    return _json("deadline_policy.json")


def message_templates() -> list[dict]:
    """Step A templates, one record per template and language (as approved and stored)."""
    rows = []
    for t in _json("message_templates.json"):
        for lang, text in t["text"].items():
            rows.append({
                "template_id": t["template_id"], "language": lang, "version": t["version"],
                "approved_by": t["approved_by"], "effective_from": t["effective_from"], "note": t.get("note"),
                "date_format": "%d/%m/%Y", "text": text, "closing": None,
                "buttons": [{"id": b["id"], "label": b.get(lang, b["English"]), "label_en": b["English"]}
                            for b in t["buttons"]],
            })
    return rows


# How plan items are named to patients (drafts, to be checked by a native speaker).
ITEM_LABELS = {
    "V1": {"English": "clinic review with your doctor", "Hindi": "डॉक्टर से क्लिनिक जाँच", "Kannada": "ವೈದ್ಯರ ತಪಾಸಣೆ"},
    "V2": {"English": "clinic review with your doctor", "Hindi": "डॉक्टर से क्लिनिक जाँच", "Kannada": "ವೈದ್ಯರ ತಪಾಸಣೆ"},
    "T1": {"English": "blood tests", "Hindi": "खून की जाँच", "Kannada": "ರಕ್ತ ಪರೀಕ್ಷೆ"},
    "T2": {"English": "mammogram", "Hindi": "मैमोग्राम", "Kannada": "ಮ್ಯಾಮೊಗ್ರಾಮ್"},
    "T3": {"English": "bone density scan (DEXA)", "Hindi": "हड्डियों की जाँच (DEXA)", "Kannada": "ಮೂಳೆ ಸಾಂದ್ರತೆ ಪರೀಕ್ಷೆ (DEXA)"},
}
USERS_BY_NAME = {u["name"]: u for u in USERS}
