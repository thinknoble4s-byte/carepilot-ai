"""Reference data shipped with the app (Step A mock data, all synthetic).

seed_data/ holds the files from the Step A student guide, section 4. Users and
the allowed values come from here; demo reset loads the rest into the database.
"""
import json
import os
from datetime import date
from functools import cache
from pathlib import Path

SEED_DIR = Path(__file__).resolve().parent / "seed_data"


def read_seed(name: str) -> str:
    """A seed file. CAREPILOT_SEED_DIR (tests only) can hold other copies, e.g. the full Step A register."""
    override = os.environ.get("CAREPILOT_SEED_DIR")
    path = Path(override) / name if override and (Path(override) / name).exists() else SEED_DIR / name
    return path.read_text(encoding="utf-8")


@cache
def _json(name: str):
    return json.loads(read_seed(name))


def config() -> dict:
    return _json("config.json")


def plan_templates(departments: dict | None = None) -> list[dict]:
    """The three care plans (T1 hormone receptor positive, T2 hormone receptor negative, T3 HER2 positive), the
    only plan templates in every demo data set. A data set may name its own department owners and covers."""
    return [{**t, "departments": departments or t["departments"]} for t in _json("care_plan_templates.json")]


STEP_A_USERS: list[dict] = _json("users.json")


def uc1() -> dict:
    """Use Case 1 seed: patients, people, plan template, template, settings (seed_data/uc1)."""
    return _json("uc1/scenario.json")


def uc2b() -> dict:
    """Use Case 2b "after Use Case 1" seed (seed_data/uc2b)."""
    return _json("uc2b/scenario.json")


def uc2t() -> dict:
    """Use Case 2T test-cycle seed (seed_data/uc2t)."""
    return _json("uc2t/scenario.json")


def uc2c() -> dict:
    """Use Case 2c "after Use Case 2a, step 6" seed (seed_data/uc2c)."""
    return _json("uc2c/scenario.json")


def uc2a() -> dict:
    """Use Case 2a "after Use Case 1" seed (seed_data/uc2a)."""
    return _json("uc2a/scenario.json")


def vt() -> dict:
    """Voice Triage MVP-lite data set: Mrs. Lakshmi V. and her history (seed_data/vt)."""
    return _json("vt/scenario.json")


def triage_history() -> dict:
    """Golden Path: the synthetic record the triage call and the emergency summary read, by MRN."""
    return _json("triage_history.json")


def step_a_settings() -> dict:
    return _json("settings.json")


# Everyone who can be picked in "Viewing as" across the demo data sets (synthetic users).
USERS: list[dict] = list({u["user_id"]: u for u in STEP_A_USERS + _json("uc1/scenario.json")["users"]
                           + _json("vt/scenario.json")["users"]}.values())
USERS_BY_ID = {u["user_id"]: u for u in USERS}
ROLE_LABELS = {"coordinator": "Care coordinator", "oncologist": "Oncologist", "chief": "Chief of hospital",
               "er_desk": "ER Desk"}
# Voice Triage: the ER Desk (duty nurse and ER doctor) is added to the Golden Path users when triage is on.
ER_DESK_USERS = [u for u in _json("vt/scenario.json")["users"] if u["role"] == "er_desk"]

LANGUAGES = config()["supported_languages"]
DEPARTMENTS = config()["departments"]
CONSENT_SCOPES = config()["consent_scopes"]
IDENTIFIER_OPTIONS = config()["identifier_options"]
DEMO_START_DATE = date.fromisoformat(config()["demo_start_date"])


def autonomy_policy() -> dict:
    return _json("autonomy_policy.json")


def deadline_policy() -> dict:
    return _json("deadline_policy.json")


def report_routing() -> dict:
    """Step A outside-report routing (Use Case 2T v1.4), in Shadow mode."""
    return _json("report_routing.json")


def message_templates() -> list[dict]:
    """Step A templates, one record per template and language (as approved and stored)."""
    rows = []
    for t in _json("message_templates.json"):
        for lang, text in t["text"].items():
            rows.append({
                "template_id": t["template_id"], "language": lang, "version": t["version"],
                "approved_by": t["approved_by"], "effective_from": t["effective_from"], "note": t.get("note"),
                "date_format": "%d/%m/%Y", "text": text, "closing": None,
                "buttons": [{"id": b["id"], "label": b.get(lang, b["English"]), "label_en": b["English"],
                             **({"only": b["only"]} if b.get("only") else {})} for b in t["buttons"]],
            })
    return rows


# How plan items are named to patients (drafts, to be checked by a native speaker).
ITEM_LABELS = {
    "V1": {"English": "clinic review with your doctor", "Hindi": "डॉक्टर से क्लिनिक जाँच", "Kannada": "ವೈದ್ಯರ ತಪಾಸಣೆ", "Tamil": "மருத்துவருடன் கிளினிக் பரிசோதனை"},
    "V2": {"English": "clinic review with your doctor", "Hindi": "डॉक्टर से क्लिनिक जाँच", "Kannada": "ವೈದ್ಯರ ತಪಾಸಣೆ", "Tamil": "மருத்துவருடன் கிளினிக் பரிசோதனை"},
    "T1": {"English": "blood tests", "Hindi": "खून की जाँच", "Kannada": "ರಕ್ತ ಪರೀಕ್ಷೆ", "Tamil": "இரத்தப் பரிசோதனைகள்"},
    "T2": {"English": "mammogram", "Hindi": "मैमोग्राम", "Kannada": "ಮ್ಯಾಮೊಗ್ರಾಮ್", "Tamil": "மேமோகிராம்"},
    "T3": {"English": "bone density scan (DEXA)", "Hindi": "हड्डियों की जाँच (DEXA)", "Kannada": "ಮೂಳೆ ಸಾಂದ್ರತೆ ಪರೀಕ್ಷೆ (DEXA)", "Tamil": "எலும்பு அடர்த்தி ஸ்கேன் (DEXA)"},
    # The three care plans: item codes are a kind plus the month (V3, BT3, MG6, PET12); looked up by the kind.
    # Voice Triage: the Doctor on Duty's orders
    "PHYSIO": {"English": "physiotherapy referral", "Hindi": "फिज़ियोथेरेपी रेफ़रल", "Kannada": "ಫಿಸಿಯೋಥೆರಪಿ ರೆಫರಲ್",
               "Tamil": "பிசியோதெரபி பரிந்துரை"},
    "BLOODFWD": {"English": "blood tests (brought forward)", "Hindi": "खून की जाँच (पहले)",
                 "Kannada": "ರಕ್ತ ಪರೀಕ್ಷೆ (ಮುಂಚಿತವಾಗಿ)", "Tamil": "இரத்தப் பரிசோதனைகள் (முன்னதாக)"},
    "V": {"English": "clinic review with your doctor", "Hindi": "डॉक्टर से क्लिनिक जाँच", "Kannada": "ವೈದ್ಯರ ತಪಾಸಣೆ", "Tamil": "மருத்துவருடன் கிளினிக் பரிசோதனை"},
    "BT": {"English": "blood test", "Hindi": "खून की जाँच", "Kannada": "ರಕ್ತ ಪರೀಕ್ಷೆ", "Tamil": "இரத்தப் பரிசோதனை"},
    "MG": {"English": "mammogram", "Hindi": "मैमोग्राम", "Kannada": "ಮ್ಯಾಮೊಗ್ರಾಮ್", "Tamil": "மேமோகிராம்"},
    "DX": {"English": "bone density scan (DEXA)", "Hindi": "हड्डियों की जाँच (DEXA)", "Kannada": "ಮೂಳೆ ಸಾಂದ್ರತೆ ಪರೀಕ್ಷೆ (DEXA)", "Tamil": "எலும்பு அடர்த்தி ஸ்கேன் (DEXA)"},
    "US": {"English": "ultrasound of the abdomen", "Hindi": "पेट का अल्ट्रासाउंड", "Kannada": "ಹೊಟ್ಟೆಯ ಅಲ್ಟ್ರಾಸೌಂಡ್", "Tamil": "வயிற்று அல்ட்ராசவுண்ட்"},
    "EC": {"English": "2D echo scan of the heart", "Hindi": "दिल की 2D इको जाँच", "Kannada": "ಹೃದಯದ 2D ಎಕೋ ಪರೀಕ್ಷೆ", "Tamil": "இதயத்தின் 2D எக்கோ ஸ்கேன்"},
    "PET": {"English": "PET-CT scan", "Hindi": "पीईटी-सीटी स्कैन", "Kannada": "ಪಿಇಟಿ-ಸಿಟಿ ಸ್ಕ್ಯಾನ್", "Tamil": "PET-CT ஸ்கேன்"},
}


def item_label(item_code: str, language: str) -> str | None:
    """How a plan item is named to the patient: by its code, else by its kind (BT3 -> BT)."""
    labels = ITEM_LABELS.get(item_code) or ITEM_LABELS.get(item_code.rstrip("0123456789")) or {}
    return labels.get(language)
USERS_BY_NAME = {u["name"]: u for u in USERS}
