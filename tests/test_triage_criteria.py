"""The Care Plan triage tabs say what the code does: every "what this answer does" line is run through
triage_rules.triage() on an otherwise calm call, and every reason code the rules can give is explained."""
import re
from datetime import date
from pathlib import Path

import pytest
from conftest import users_header

from app import triage_criteria as C, triage_rules as R, voice_sorter as VS

H = {"arm_baseline": {"status": "normal", "date": "2026-10-01"}, "last_headache": {"score": 3, "date": "2026-10-20"},
     "advice": {"id": "ADV-1", "date": "2026-10-10"}, "events": []}
TIER = {"red": "TIER_1_RED", "amber": "TIER_2_AMBER", "green": "TIER_3_GREEN"}


def calm() -> dict:
    a = R.blank()
    for f in R.FIELDS:
        a[f] = R.NO
    a.update({"thermometer_used": R.YES, "temperature_value": 98.4, "can_keep_liquids_down": R.YES,
              "headache_score": R.NOT_ASKED, "vomiting_count": 0, "arm_pain_score": 0, "tired_days": 0})
    return a


CLAIMS = [(qid, tier, text, probe) for qid, effects in C.EFFECTS.items() for tier, text, probe in effects if probe]


@pytest.mark.parametrize("qid,tier,text,probe", CLAIMS, ids=lambda v: v if isinstance(v, str) else "")
def test_every_shown_effect_is_what_the_rules_do(qid, tier, text, probe):
    a = calm()
    for f, v in probe.items():
        a[f] = H["last_headache"]["score"] + int(v[-1]) if isinstance(v, str) and v.startswith("LAST+") else v
    if "temperature_value" in probe:
        a["thermometer_used"] = R.YES
    assert R.triage(a, H, date(2026, 11, 24))["tier"] == TIER[tier], f"{qid}: {text}"


def test_every_question_is_covered_and_unclear_follows_the_danger_flag():
    assert set(C.EFFECTS) == set(R.QUESTION_IDS)
    for q in C.criteria()["questions"]:
        a = calm()
        field = next(f for qid, f, *_ in R.QUESTION_SPEC if qid == q["id"])
        a["thermometer_used" if field == "temperature" else field] = R.UNKNOWN
        assert R.triage(a, H, date(2026, 11, 24))["tier"] == (TIER["red"] if q["danger"] else TIER["amber"]), q["id"]


def test_every_reason_code_the_rules_can_give_is_explained():
    source = (Path(R.__file__)).read_text(encoding="utf-8")
    amber = set(re.findall(r'"(AMBER_[A-Z0-9_]+)"', source)) | {f"AMBER_RECURRING_3X_14D_{s}" for s in ("HEADACHE", "NAUSEA", "ARM_PAIN")}
    amber -= {"AMBER_RECURRING_3X_14D_"}
    assert amber <= set(C.AMBER_PLAIN), amber - set(C.AMBER_PLAIN)
    red = set(re.findall(r'"(RED_[A-Z0-9_]+)"', source)) - {"RED_PACKAGE_AND_DUTY_DOCTOR", "RED_PACKAGE_NO_ANSWER"}
    assert red <= set(R.RED_PLAIN), red - set(R.RED_PLAIN)


def test_the_english_health_words_shown_are_the_ones_the_sorter_catches():
    for w in C.HEALTH_EN_SHOWN:
        assert VS.HEALTH_EN.search(w), w


def test_the_endpoint_serves_all_tabs(seeded, client):
    body = client.get("/triage/criteria", headers=users_header("u_anita")).json()
    assert set(body["tiers"]) == {"red", "amber", "green"}
    assert all(body["tiers"][t]["questions"] for t in ("red", "amber", "green"))
    assert [lbl[0] for lbl in body["call"]["labels"]] == ["HEALTH_CONCERN", "YES", "NO", "UNCLEAR"]
    assert any(ln["id"] == "L9" for ln in body["call"]["lines"])
