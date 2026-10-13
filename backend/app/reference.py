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
