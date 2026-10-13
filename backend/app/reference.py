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


USERS: list[dict] = _json("users.json")
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
    return _json("message_templates.json")


# How plan items are named to patients (drafts, to be checked by a native speaker).
ITEM_LABELS = {
    "V1": {"English": "clinic review with your doctor", "Hindi": "डॉक्टर से क्लिनिक जाँच", "Kannada": "ವೈದ್ಯರ ತಪಾಸಣೆ"},
    "V2": {"English": "clinic review with your doctor", "Hindi": "डॉक्टर से क्लिनिक जाँच", "Kannada": "ವೈದ್ಯರ ತಪಾಸಣೆ"},
    "T1": {"English": "blood tests", "Hindi": "खून की जाँच", "Kannada": "ರಕ್ತ ಪರೀಕ್ಷೆ"},
    "T2": {"English": "mammogram", "Hindi": "मैमोग्राम", "Kannada": "ಮ್ಯಾಮೊಗ್ರಾಮ್"},
    "T3": {"English": "bone density scan (DEXA)", "Hindi": "हड्डियों की जाँच (DEXA)", "Kannada": "ಮೂಳೆ ಸಾಂದ್ರತೆ ಪರೀಕ್ಷೆ (DEXA)"},
}
USERS_BY_NAME = {u["name"]: u for u in USERS}
