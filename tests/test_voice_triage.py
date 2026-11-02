"""Voice Triage MVP-lite (CarePilot_Voice_Triage_MVP_Lite_v1.2): the router, the rules, the call, the Red package and
PDF, the Amber order, the Green coaching, ticket states, the coordinator's review, the closing summary and the Pre-OPD
brief. Triage v3 (2 Nov): two people roles, the coordinator and the Doctor on Duty; no ER Desk, no follow-up call.
Synthetic data only."""
import json
from datetime import date
from pathlib import Path

import pytest

from conftest import users_header

from app import triage_router as TR
from app import triage_rules as R

ANITA, RAVI = users_header("u_anita"), users_header("u_ravi")
ANANYA, SEKHAR = users_header("u_ananya"), users_header("u_sekhar")
MRN = "SYN-0142"
TODAY = date(2026, 10, 27)
LAKSHMI = json.loads((Path(__file__).resolve().parents[1] / "backend" / "app" / "seed_data" / "vt" /
                      "scenario.json").read_text(encoding="utf-8"))["triage_history"][MRN]


# ---------------------------------------------------------------------------------------------------------------
# The rules (no database)
# ---------------------------------------------------------------------------------------------------------------
def answers(**kw) -> dict:
    a = {**R.blank(), "slurred_or_confused": R.NO, "arm_red_or_hot": R.NO, "thermometer_used": R.YES,
         "temperature_value": 98.4, "vomiting_count": 0.0, "headache_present": R.NO, "bangles_tighter": R.NO,
         "nausea_since_yesterday": R.NO}
    a.update(kw)
    return a


HERO = {"health_related": "YES", "symptoms": ["sleepiness", "nausea_vomiting", "headache", "arm_pain"],
        "severity_word": "NONE"}  # the tags of the hero voice note (guide 6.1)
SEVERE_ARM = {"health_related": "YES", "symptoms": ["arm_pain"], "severity_word": "SEVERE"}
TIRED = {"health_related": "YES", "symptoms": ["tiredness"], "severity_word": "NONE"}
SHOPPING = {"health_related": "NO", "symptoms": [], "severity_word": "NONE"}
RASH = {"health_related": "YES", "symptoms": ["rash on the back"], "severity_word": "NONE"}


def run_script(scenario: str, lang: str = "Tamil", tags=HERO) -> tuple[dict, dict]:
    """The router's plan for the tags, then the scripted answers (Tamil unless said) through the parser, as the
    call does."""
    plan = TR.plan(LAKSHMI, tags)
    ids = plan["question_ids"]
    a, script = R.blank(), R.scenarios(lang)[scenario]
    q = R.next_question(LAKSHMI, a, None, ids)
    while q is not None:
        said = script.get(q.id)
        value = R.check(q.kind, R.parse(q.kind, said))
        R.store(a, q, value)
        if R.red_codes(a):
            break
        q = R.next_question(LAKSHMI, a, q.id, ids)
    return a, R.triage(a, LAKSHMI, TODAY, floors=plan["floors"], profile=plan["profile"])


def test_amber_hero_scene_gives_the_guides_five_reasons():
    a, r = run_script("amber")
    assert r["tier"] == "TIER_2_AMBER" and r["action_code"] == "DOCTOR_CALLBACK_2H" and r["response_minutes"] == 120
    assert sorted(r["reason_codes"]) == ["AMBER_BANGLES_TIGHTER_THAN_BASELINE", "AMBER_DROWSY_MOST_OF_TODAY",
                                         "AMBER_HEADACHE_RISE_2_PLUS", "AMBER_HEADACHE_WORSE_THAN_LAST_TIME",
                                         "AMBER_NAUSEA_DESPITE_MEALS"]
    assert (r["protocol_profile"], r["protocol_status"]) == ("ROUTED", "DRAFT_UNSIGNED")
    why = R.why_lines(r, a, LAKSHMI)
    assert why[0] == "Left arm: no swelling and loose bangles on 10 October. Today: bangles tighter."
    assert why[1] == "Headache: 2 out of 10 on 19 October. Today: 5 out of 10."


def test_green_repeats_only_the_doctors_signed_sentence():
    a, r = run_script("green")
    assert (r["tier"], r["reason_codes"], r["repeat_advice"]) == ("TIER_3_GREEN", [], True)
    assert r["repeat_instruction_id"] == "INST-2026-10-19-01"
    ta, en = R.closing(r, LAKSHMI)
    assert LAKSHMI["advice"]["en"] in en and LAKSHMI["advice"]["ta"] in ta
    assert R.why_lines(r, a, LAKSHMI)[0].startswith("No rule fired.")
    a2 = {**a, "nausea_since_yesterday": R.NO, "took_supplements_after_full_meals": R.NOT_ASKED}
    assert LAKSHMI["advice"]["en"] not in R.closing(R.triage(a2, LAKSHMI, TODAY), LAKSHMI)[1]


@pytest.mark.parametrize("lang", ["Hindi", "Telugu"])
def test_hindi_and_telugu_answers_give_the_same_tiers_as_tamil(lang):
    for scenario in R.SCENARIO_LABELS:
        tamil, own = run_script(scenario), run_script(scenario, lang)
        assert own[0] == tamil[0], (lang, scenario)
        assert (own[1]["tier"], own[1]["reason_codes"]) == (tamil[1]["tier"], tamil[1]["reason_codes"]), scenario


def test_the_call_speaks_the_patients_language():
    for lang, word, month in (("Hindi", "नमस्ते", "26 अक्टूबर"), ("Telugu", "నమస్కారం", "అక్టోబర్ 26"),
                              ("Tamil", "வணக்கம்", "அக்டோபர் 26"), ("English", "Hello", "26 October")):
        h = {**LAKSHMI, "language": lang, "last_headache": {"date": "2026-10-26", "score": 3}}
        assert R.messages(h)["open"][0].startswith(word) and month in R.question(h, "Q_HEAD_WORSE").ta, lang
        assert all(q.ta for q in R.bank(h)), lang  # every question of the bank in her language
    assert R.parse("yesno", "हाँ, थोड़ा सा") == R.YES and R.parse("yesno", "లేదు") == R.NO
    assert R.parse("yesno", "पता नहीं") == R.UNKNOWN and R.parse("number", "ఐదు") == 5.0
    assert R.parse("temperature", "थर्मामीटर नहीं है") == R.NO_THERMOMETER
    assert R.parse_followup("కొంచెం బాగుంది") == "BETTER" and R.parse_followup("ज़्यादा खराब") == "WORSE"


def test_red_stops_at_the_arm_question():
    a, r = run_script("red")
    assert (r["tier"], r["red_kind"], r["reason_codes"]) == ("TIER_1_RED", "CONFIRMED", ["RED_ARM_RED_OR_HOT"])
    assert a["chest_pain"] == R.NO and a["thermometer_used"] == R.NOT_ASKED and a["headache_present"] == R.NOT_ASKED


# ---- The router (guide Part 3 and the walk-throughs of Part 6)
@pytest.mark.parametrize("tags,n,modules,floors", [
    (HERO, 16, ["core", "history_arm", "headache", "nausea_vomiting", "sleepiness", "arm_pain"], []),
    (SEVERE_ARM, 8, ["core", "history_arm", "arm_pain"], ["AMBER_PATIENT_SAYS_SEVERE"]),
    (TIRED, 8, ["core", "history_arm", "tiredness"], []),
    (RASH, 6, ["core", "history_arm", "other_symptom"], ["AMBER_OTHER_SYMPTOM_REPORTED"]),
    ("garbage", 12, ["fallback"], []),
    ({"health_related": "UNSURE", "symptoms": [], "severity_word": "NONE"}, 12, ["fallback"], []),
])
def test_the_router_builds_the_guides_calls(tags, n, modules, floors):
    p = TR.plan(LAKSHMI, tags)
    assert p["route"] == "TRIAGE" and len(p["question_ids"]) == n and p["modules"] == modules
    assert p["floors"] == floors
    assert p["question_ids"][:5] == TR.CORE  # the danger core is never skipped, and comes first


def test_only_a_clearly_not_health_message_gets_the_standard_reply():
    assert TR.route(SHOPPING)["route"] == "STANDARD_REPLY" and TR.plan(LAKSHMI, SHOPPING)["question_ids"] == []
    # a symptom always wins; a severe word too; broken tags are treated as health
    assert TR.route({**SHOPPING, "symptoms": ["arm_swelling_or_redness"]})["route"] == "TRIAGE"
    assert TR.route({**SHOPPING, "severity_word": "SEVERE"})["route"] == "TRIAGE"
    assert TR.route(None)["route"] == "TRIAGE" and TR.route({"health_related": "maybe"})["route"] == "TRIAGE"
    assert TR.parse_tags('noise {"health_related": "NO", "symptoms": []} noise')["health_related"] == "NO"


def test_every_mix_of_up_to_three_tags_keeps_the_core():
    from itertools import combinations

    for k in (1, 2, 3):
        for mix in combinations(TR.SYMPTOM_TAGS, k):
            p = TR.plan(LAKSHMI, {"health_related": "YES", "symptoms": list(mix), "severity_word": "NONE"})
            assert p["question_ids"][:5] == TR.CORE and len(set(p["question_ids"])) == len(p["question_ids"]), mix


def test_scenario_e_her_word_severe_makes_it_amber_and_f_tired_stays_green():
    a, r = run_script("mild_arm", tags=SEVERE_ARM)
    assert (r["tier"], r["reason_codes"]) == ("TIER_2_AMBER", ["AMBER_PATIENT_SAYS_SEVERE"])
    assert a["arm_pain_score"] == 3 and a["headache_present"] == R.NOT_ASKED
    assert R.why_lines(r, a, LAKSHMI) == ["She described her problem as severe in her own message."]
    a, r = run_script("green", tags=TIRED)
    assert (r["tier"], a["tired_days"], a["tired_getting_worse"]) == ("TIER_3_GREEN", 2, R.NO)


def test_a_symptom_with_no_questions_goes_to_the_duty_doctor_in_2_hours():
    a, r = run_script("green", tags=RASH)
    assert (r["tier"], r["reason_codes"], r["action_code"]) == ("TIER_2_AMBER", ["AMBER_OTHER_SYMPTOM_REPORTED"],
                                                                "DOCTOR_CALLBACK_2H")
    assert r["response_minutes"] == 120 and R.closing(r, LAKSHMI)[1].startswith("Thank you. A doctor needs to look")


@pytest.mark.parametrize("field,value,code", [
    ("cannot_stay_awake", R.YES, "RED_CANNOT_STAY_AWAKE"), ("chest_pain", R.YES, "RED_CHEST_PAIN"),
    ("can_keep_liquids_down", R.NO, "RED_CANNOT_KEEP_LIQUIDS"), ("blurred_vision", R.YES, "RED_BLURRED_VISION"),
    ("unsteady_or_dizzy_walking", R.YES, "RED_UNSTEADY_OR_DIZZY"), ("shaking_chills", R.YES, "RED_SHAKING_CHILLS"),
])
def test_the_new_red_rules(field, value, code):
    r = R.triage(answers(**{field: value}), LAKSHMI, TODAY)
    assert (r["tier"], r["reason_codes"], r["red_kind"]) == ("TIER_1_RED", [code], "CONFIRMED")


@pytest.mark.parametrize("kw,code", [
    ({"arm_pain_score": 7.0}, "AMBER_ARM_PAIN_7_PLUS"), ({"arm_pain_sudden": R.YES}, "AMBER_ARM_PAIN_SUDDEN_ONSET"),
    ({"tired_days": 7.0}, "AMBER_TIREDNESS_7_DAYS_PLUS"), ({"tired_getting_worse": R.YES}, "AMBER_TIREDNESS_GETTING_WORSE"),
    ({"drowsy_most_of_today": R.YES}, "AMBER_DROWSY_MOST_OF_TODAY"), ({"arm_swollen": R.YES}, "AMBER_ARM_SWOLLEN"),
    ({"skin_break_left_arm": R.YES}, "AMBER_SKIN_BREAK_LEFT_ARM"),
    ({"thermometer_used": R.NO, "temperature_value": None, "feels_hot": R.YES}, "AMBER_FEELS_HOT_NO_THERMOMETER"),
])
def test_the_new_amber_rules(kw, code):
    r = R.triage(answers(**kw), LAKSHMI, TODAY)
    assert (r["tier"], r["reason_codes"]) == ("TIER_2_AMBER", [code])
    assert not R.why_lines(r, R.blank() | answers(**kw), LAKSHMI)[0].startswith("AMBER_")  # a plain sentence


def test_not_asked_is_not_no_and_fires_nothing():
    r = R.triage({**R.blank(), "slurred_or_confused": R.NO, "cannot_stay_awake": R.NO, "chest_pain": R.NO,
                  "arm_red_or_hot": R.NO, "thermometer_used": R.YES, "temperature_value": 98.4}, LAKSHMI, TODAY)
    assert (r["tier"], r["reason_codes"]) == ("TIER_3_GREEN", [])
    assert "Vomited" not in r.get("why", "") and "Vomited" not in R.facts_text(R.blank())


def test_skips_follow_the_guide():
    h, ids = LAKSHMI, TR.plan(LAKSHMI, HERO)["question_ids"]
    a = {**R.blank(), "headache_present": R.NO}
    assert R.skipped(R.question(h, "Q_HEAD_SCORE"), a) and R.skipped(R.question(h, "Q_HEAD_WORSE"), a)
    a = {**R.blank(), "nausea_since_yesterday": R.NO, "vomiting_count": 0.0}
    assert R.skipped(R.question(h, "Q_SUPP"), a) and R.skipped(R.question(h, "Q_LIQUIDS"), a)
    a["vomiting_count"] = 1.0  # she vomited once: "keeps water down" is asked
    assert not R.skipped(R.question(h, "Q_LIQUIDS"), a) and "Q_VOMIT" in ids


@pytest.mark.parametrize("temp,tier", [(100.3, "TIER_3_GREEN"), (100.4, "TIER_1_RED"), (38.0, "TIER_1_RED"),
                                       (37.9, "TIER_3_GREEN")])
def test_ec_d_fever_threshold(temp, tier):
    assert R.triage(answers(temperature_value=temp), LAKSHMI, TODAY)["tier"] == tier


def test_unknown_on_a_danger_question_is_red_unconfirmed_and_elsewhere_amber():
    r = R.triage(answers(arm_red_or_hot=R.UNKNOWN), LAKSHMI, TODAY)
    assert (r["tier"], r["red_kind"], r["action_code"]) == ("TIER_1_RED", "UNCONFIRMED", "STAFF_CALLBACK_15_MIN")
    r = R.triage(answers(bangles_tighter=R.UNKNOWN), LAKSHMI, TODAY)
    assert (r["tier"], r["reason_codes"]) == ("TIER_2_AMBER", ["AMBER_CANNOT_CONFIRM"])


def test_ec_b_third_headache_in_14_days_and_ec_c_no_thermometer():
    h = {**LAKSHMI, "events": LAKSHMI["events"] + [{"date": "2026-10-22", "symptoms": ["headache"]}]}
    r = R.triage(answers(headache_present=R.YES, headache_score=2.0, headache_worse_than_last_time=R.NO), h, TODAY)
    assert r["reason_codes"] == ["AMBER_RECURRING_3X_14D_HEADACHE"]
    a, r = run_script("no_thermometer")
    assert r["tier"] == "TIER_2_AMBER" and "AMBER_NO_THERMOMETER_WITH_SYMPTOMS" in r["reason_codes"]


def test_ec_e_broken_data_is_red_never_green():
    r = R.triage(answers(), LAKSHMI, TODAY, fault=True)
    assert r["tier"] == "TIER_1_RED" and "RED_SYSTEM_FAULT" in r["reason_codes"]
    assert R.check("number", "seven") == R.UNKNOWN and R.check("yesno", "maybe") == R.UNKNOWN
    assert R.model_value("yesno", "I think so") == R.UNKNOWN and R.model_value("number", '{"value": 99}') == R.UNKNOWN


def test_parse_reads_tamil_and_english():
    assert R.parse("yesno", "இல்லை, சிவக்கவில்லை") == R.NO and R.parse("yesno", "yes it is") == R.YES
    assert R.parse("yesno", "ஆம் இல்லை") is None and R.parse("yesno", "தெரியவில்லை") == R.UNKNOWN
    assert R.parse("number", "ஐந்து") == 5 and R.parse("temperature", "வெப்பமானி இல்லை") == R.NO_THERMOMETER
    assert R.parse_followup("இன்று நன்றாக இருக்கிறேன்") == "BETTER" and R.parse_followup("worse") == "WORSE"


# ---------------------------------------------------------------------------------------------------------------
# The data set and the call
# ---------------------------------------------------------------------------------------------------------------
def voice_note(client, mrn=MRN, headers=ANITA, clip="TA-01") -> dict:
    r = client.post(f"/simulator/{mrn}/reply", headers=headers, json={"clip_id": clip})
    assert r.status_code == 201, r.text
    return r.json()


def episode(client, mrn=MRN, headers=ANITA) -> dict:
    eps = client.get("/triage/episodes", headers=headers, params={"mrn": mrn, "status": "all"}).json()
    return client.get(f"/triage/episodes/{eps[0]['episode_id']}", headers=headers).json()


def play(client, scenario: str, eid: int | None = None) -> dict:
    eid = eid or episode(client)["episode_id"]
    r = client.post(f"/triage/episodes/{eid}/play", headers=ANITA, json={"scenario": scenario})
    assert r.status_code == 200, r.text
    return r.json()


def confirm(client, eid: int, who=ANITA) -> dict:
    """The coordinator's review: Confirm. On Red and Amber the doctor acts only after it (triage v3.1)."""
    r = client.post(f"/triage/episodes/{eid}/review", headers=who, json={"action": "CONFIRM"})
    assert r.status_code == 200, r.text
    return r.json()


def amber(client) -> int:
    """The Amber call, confirmed by the coordinator: with the duty doctor."""
    eid = play(client, "amber")["episode_id"]
    confirm(client, eid)
    return eid


def sent(mrn=MRN) -> list[dict]:
    from conftest import outbox

    return [m for m in outbox(mrn) if m["status"] == "sent"]


def test_vt_reset_is_lakshmi_with_her_history(vt, client):
    assert (vt["patients"], vt["consents"], vt["clock"].isoformat()) == (1, 1, "2026-10-27")
    assert client.get("/clock", headers=ANITA).json()["time"] == "10:12"
    users = {u["id"]: u["role"] for u in client.get("/users").json()}
    assert users == {"u_anita": "coordinator", "u_ravi": "coordinator", "u_ananya": "oncologist",
                     "u_sekhar": "oncologist", "u_suresh": "chief"}  # triage v3: no ER Desk
    vt_cfg = client.get("/demo/settings", headers=RAVI).json()["voice_triage"]
    assert vt_cfg["enabled"] is True and "er_doctor" not in vt_cfg


def test_voice_note_brings_the_ack_and_a_call_due_in_two_minutes_not_a_handover(vt, client):
    r = voice_note(client)
    assert r["handover_id"] is None and r["triage_episode_id"]
    assert any(h.startswith("triage_call_due") for h in r["handling"])
    ep = episode(client)
    assert (ep["call_status"], ep["call_due_at"][11:16]) == ("due", "10:14")
    assert ep["translation"].startswith("Since yesterday I feel very sleepy")
    ack = [m for m in sent() if m["template_id"] == "triage_ack"]
    assert len(ack) == 1 and ack[0]["language"] == "Tamil" and ack[0]["recipient"] == "patient"
    from conftest import scalar
    assert scalar("SELECT count(*) FROM handover") == 0
    assert scalar("SELECT count(*) FROM coordinator_task") == 0
    # A second message joins the open episode: no second acknowledgement, no second call.
    r2 = client.post(f"/simulator/{MRN}/reply", headers=ANITA, json={"text": "தலைவலி அதிகமாக இருக்கிறது"}).json()
    assert any(h.startswith("triage_episode_updated") for h in r2["handling"])
    assert len([m for m in sent() if m["template_id"] == "triage_ack"]) == 1


def test_amber_call_question_by_question_then_the_doctors_physio_order(vt, client):
    voice_note(client)
    eid = episode(client)["episode_id"]
    s = client.post(f"/triage/episodes/{eid}/pickup", headers=ANITA).json()
    assert s["current"]["id"] == "Q_SLURRED" and s["turns"][1]["template_id"] == "TPL_CALL_OPEN_LITE_V1"
    assert "லட்சுமி அம்மாவுக்காக" in s["turns"][1]["ta"]
    # What the AI heard and the 16 questions the router built (guide 6.1)
    assert s["plan"]["heard"] == ["headache", "nausea", "sleepy", "arm pain"] and len(s["table"]) == 16
    assert s["plan"]["tagged"] is True and s["plan"]["profile"] == "ROUTED"
    assert s["not_asked"] == ["dizziness and vision", "chills and feeling hot", "arm swelling and skin", "tiredness"]
    # An unclear answer is asked once more; then the on-screen buttons and Tamil words.
    s = client.post(f"/triage/episodes/{eid}/answer", headers=ANITA, json={"text": "ம்ம்"}).json()
    assert s["current"]["id"] == "Q_SLURRED" and s["turns"][-1].get("repeat") is True
    s = client.post(f"/triage/episodes/{eid}/answer", headers=ANITA, json={"value": "NO"}).json()
    assert s["current"]["id"] == "Q_AWAKE"
    s = play(client, "amber", eid)
    assert (s["tier"], s["call_status"]) == ("TIER_2_AMBER", "done")
    worse = next(q for q in s["table"] if q["id"] == "Q_HEAD_WORSE")
    assert worse["history"] and "அக்டோபர் 19" in worse["ta"] and worse["value"] == "YES"
    assert all(q["asked"] for q in s["table"])  # all 16 asked
    assert s["turns"][-2]["template_id"] == "TPL_CALL_AMBER_V1"
    # The follow-up call waits for the doctor's outcome (24 hours after it).
    assert s["callback_due_at"][11:16] == "12:12" and s["followup_due_on"] is None
    assert len(s["result"]["why"]) == 5 and s["state"] == "OPEN"
    # Only the doctor orders, and only after a call reached her; a button creates the card and sends the approved
    # booking message. The medication order goes to her word for word; the AI writes nothing.
    assert client.post(f"/triage/episodes/{eid}/order", headers=ANITA, json={"order": "physio"}).status_code == 403
    r = client.post(f"/triage/episodes/{eid}/order", headers=ANANYA, json={"order": "physio"})
    assert r.status_code == 409 and r.json()["detail"] == "Waiting for the coordinator's review (Confirm, Raise tier or Flag as wrong)"  # the coordinator's review comes first
    confirm(client, eid)
    r = client.post(f"/triage/episodes/{eid}/order", headers=ANANYA, json={"order": "physio"})
    assert r.status_code == 409 and "before an order" in r.json()["detail"]
    s = doctor_call(client, eid, "PICKED_UP").json()
    assert (s["state"], s["coord_ack_by"], s["reached"]) == ("ACKNOWLEDGED", "u_ananya", True)  # acknowledged with it
    r = client.post(f"/triage/episodes/{eid}/order", headers=ANANYA, json={"order": "physio"})
    assert r.status_code == 200, r.text
    r = client.post(f"/triage/episodes/{eid}/order", headers=ANANYA,
                    json={"order": "medication", "note": "Pause Neurobion Forte for 5 days."})
    assert r.status_code == 200, r.text
    assert [o["order"] for o in r.json()["orders"]] == ["physio", "medication"]
    assert (r.json()["state"], r.json()["followup_due_on"]) == ("ACKNOWLEDGED", None)
    med = [m for m in sent() if m["template_id"] == "triage_doctor_text"]
    assert len(med) == 1 and med[0]["body"].endswith("Pause Neurobion Forte for 5 days.") and "Dr. Ananya" in med[0]["body"]
    card = client.get(f"/patients/{MRN}/cards", headers=ANITA).json()
    card = card["cards"] if isinstance(card, dict) else card
    physio = [c for c in card if c["name"] == "Physiotherapy referral"]
    assert len(physio) == 1 and physio[0]["state"] == "Due" and physio[0]["department"] == "Physiotherapy"
    booking = [m for m in sent() if m["template_id"] == "instruction_booking"]
    assert len(booking) == 1 and "பிசியோதெரபி பரிந்துரை" in booking[0]["body"]
    assert client.post(f"/triage/episodes/{eid}/order", headers=ANANYA, json={"order": "none"}).status_code == 409


def test_red_package_message_pdf_note_and_no_family_call_task(vt, client):
    from conftest import scalar

    voice_note(client)
    s = play(client, "red")
    assert (s["tier"], s["red_kind"], s["stopped_early"]) == ("TIER_1_RED", "CONFIRMED", True)
    assert s["result"]["action_code"] == "RED_PACKAGE_AND_DUTY_DOCTOR"
    assert [q["id"] for q in s["table"] if q["asked"]] == ["Q_SLURRED", "Q_AWAKE", "Q_CHEST", "Q_ARM_RED"]
    msg = [m for m in sent() if m["template_id"] == "triage_red_caregiver"]
    assert len(msg) == 1 and msg[0]["recipient"] == "caregiver" and msg[0]["to_phone"] == "+919000100142"
    assert msg[0]["body_en"].startswith("This is CarePilot's automated assistant. Lakshmi Amma gave answers to our "
                                        "call that are warning signs. Please take her to the nearest emergency")
    assert "லட்சுமி அம்மா எங்கள் அழைப்பில்" in msg[0]["body"]
    pdf = client.get(f"/triage/documents/{msg[0]['attachment_doc_id']}", headers=ANANYA)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    text = pdf.content.decode("latin-1")
    for must in ("EMERGENCY SUMMARY FOR THE TREATING DOCTOR", "SYNTHETIC DEMO", "Left arm reported as red or hot",
                 "Asked and answered No: speech slurred or confusion, very hard to stay awake, chest pain or",
                 "Shelcal-HD", "Note from her treating oncologist",
                 "It does not diagnose or advise"):
        assert must in text, must
    for never in ("ambulance", "maps.", "risk score", "grade", "recommend"):
        assert never not in text.lower(), never
    assert s["doctor_note"].startswith("RED alert: Mrs. Lakshmi V. (SYN-0142). Why: Left arm reported as red or "
                                       "hot to touch. At 27 Oct 2026 10:12 the family (Karthik, her son) was sent")
    assert "Dr. Ananya records the outcome and closes the ticket" in s["doctor_note"]
    # Triage v3: no family "confirm the emergency visit" call; the Doctor on Duty has the ticket at once.
    assert s["task"] is None and scalar("SELECT count(*) FROM coordinator_task") == 0
    assert [p["persona"] for p in s["pending"]] == ["Care coordinator"] and s["awaits_review"]  # the review first
    assert s["next"]["doctor"]["waiting"] is True
    eid = s["episode_id"]
    r = client.post(f"/triage/episodes/{eid}/acknowledge", headers=ANANYA)
    assert r.status_code == 409 and r.json()["detail"] == "Waiting for the coordinator's review (Confirm, Raise tier or Flag as wrong)"
    # The coordinator reviews but cannot close a Red; the doctor closes with her recommendation.
    s = confirm(client, eid)
    assert [p["persona"] for p in s["pending"]] == ["Doctor on Duty"] and not s["awaits_review"]
    r = client.post(f"/triage/episodes/{eid}/close", headers=ANITA, json={"summary": "x"})
    assert r.status_code == 409 and r.json()["detail"] == "Only the Doctor on Duty closes a Red or Amber ticket"
    r = client.post(f"/triage/episodes/{eid}/close", headers=ANANYA, json={"summary": "x", "contact": "PHONE"})
    assert r.status_code == 422 and r.json()["detail"] == "Write your recommendation"
    r = client.post(f"/triage/episodes/{eid}/close", headers=ANANYA,
                    json={"summary": "Seen in ED.", "recommendation": "Admitted at City General ED", "contact": "PHONE"})
    assert r.status_code == 200 and r.json()["status"] == "closed" and r.json()["coord_ack_by"] == "u_ananya"


def test_no_answer_twice_is_red_with_her_voice_note_in_the_pdf(vt, client):
    voice_note(client)
    eid = episode(client)["episode_id"]
    s = client.post(f"/triage/episodes/{eid}/no-answer", headers=ANITA).json()
    assert (s["call_status"], s["attempts"]) == ("due", 1)
    assert client.get("/clock", headers=ANITA).json()["time"] == "10:17"
    s = client.post(f"/triage/episodes/{eid}/no-answer", headers=ANITA).json()
    assert (s["tier"], s["red_kind"], s["call_status"]) == ("TIER_1_RED", "NO_ANSWER", "no_answer")
    msg = [m for m in sent() if m["template_id"] == "triage_red_caregiver"][0]
    assert "We called twice and could not reach her" in msg["body_en"]
    text = client.get(f"/triage/documents/{msg['attachment_doc_id']}", headers=ANITA).content.decode("latin-1")
    assert r"Her voice message \(machine translation\)" in text and "Two automated calls were not answered" in text


def test_unconfirmed_red_the_coordinator_calls_back_then_a_person_sends_the_package(vt, client):
    voice_note(client)
    s = play(client, "unclear_chest")
    eid = s["episode_id"]
    assert (s["tier"], s["red_kind"], s["callback_asked"]) == ("TIER_1_RED", "UNCONFIRMED", True)
    assert s["result"]["reason_codes"] == ["RED_CANNOT_CONFIRM_SAFE"] and s["package_sent_at"] is None
    assert s["result"]["action_code"] == "STAFF_CALLBACK_15_MIN"
    assert not [m for m in sent() if m["template_id"] == "triage_red_caregiver"]
    assert s["callback_due_at"][11:16] == "10:27" and s["due"]["what"] == "Coordinator call back (15 minutes)"
    assert s["next"]["coordinator"]["text"].startswith("Call her back by 10:27 (unconfirmed)")
    assert [p["persona"] for p in s["pending"]] == ["Care coordinator"]  # the call back and the review first
    assert client.post(f"/triage/episodes/{eid}/send-package", headers=ANANYA).status_code == 409  # after the review
    assert client.post(f"/triage/episodes/{eid}/callback", headers=ANANYA, json={"note": "x"}).status_code == 403
    assert client.post(f"/triage/episodes/{eid}/callback", headers=ANITA, json={"note": ""}).status_code == 422
    s = client.post(f"/triage/episodes/{eid}/callback", headers=ANITA,
                    json={"note": "Chest tight since noon; son taking her to the ED"}).json()
    assert s["nurse_callback"]["by"] == "u_anita" and s["next"]["coordinator"]["text"].startswith(
        "Review the AI's triage") and "Called back (Anita)" in s["next"]["coordinator"]["text"]
    assert client.post(f"/triage/episodes/{eid}/callback", headers=RAVI, json={"note": "x"}).status_code == 409
    r = client.post(f"/triage/episodes/{eid}/send-package", headers=ANITA)
    assert r.status_code == 200 and r.json()["package_sent_by"] == "u_anita"
    msg = [m for m in sent() if m["template_id"] == "triage_red_caregiver"]
    assert len(msg) == 1 and "could not be confirmed as safe" in msg[0]["body_en"]
    assert client.post(f"/triage/episodes/{eid}/send-package", headers=ANANYA).status_code == 409


def test_keypad_zero_asks_for_the_duty_doctor_to_call_back(vt, client):
    voice_note(client)
    s = play(client, "nurse")
    assert (s["tier"], s["result"]["action_code"]) == ("TIER_2_AMBER", "DOCTOR_CALLBACK_2H")
    assert s["result"]["reason_codes"] == ["AMBER_NURSE_REQUESTED"] and not s["callback_asked"]
    assert s["result"]["why"] == ["She pressed 0 to ask for a doctor to call her back."]


def test_sarvam_off_mid_call_fails_closed_to_red(vt, client):
    voice_note(client)
    eid = episode(client)["episode_id"]
    client.post(f"/triage/episodes/{eid}/pickup", headers=ANITA)
    client.post("/demo/sarvam", headers=ANITA, json={"enabled": False})
    s = client.post(f"/triage/episodes/{eid}/answer", headers=ANITA, json={"text": "ம்ம் சரி"}).json()
    assert s["tier"] == "TIER_1_RED" and "RED_SYSTEM_FAULT" in s["result"]["reason_codes"]
    assert s["red_kind"] == "UNCONFIRMED" and s["package_sent_at"] is None


def test_green_the_coordinator_closes_with_the_summary_and_the_pre_opd_brief(vt, client):
    voice_note(client)
    s = play(client, "green")
    eid = s["episode_id"]
    assert s["tier"] == "TIER_3_GREEN" and LAKSHMI["advice"]["ta"] in s["turns"][-2]["ta"]
    assert s["followup_due_on"] is None and s["state"] == "OPEN"  # triage v3: no follow-up call
    assert [m["template_id"] for m in sent()].count("triage_outcome_green") == 0
    assert client.post(f"/triage/episodes/{eid}/followup", headers=ANITA, json={"answer": "BETTER"}).status_code == 404
    # Green is not acknowledged: the coordinator reviews it and closes it with the summary.
    r = client.post(f"/triage/episodes/{eid}/acknowledge", headers=ANITA)
    assert r.status_code == 409 and "reviews and closes" in r.json()["detail"]
    d = client.post(f"/triage/episodes/{eid}/summary-draft", headers=ANITA, json={}).json()["draft"]
    assert d.startswith("Lakshmi reported headache, nausea, sleepy, arm pain; CarePilot asked 16 questions and found "
                        "Green, with no warning signs") and d.endswith("Reviewed by Anita: no doctor's call needed.")
    assert client.post(f"/triage/episodes/{eid}/close", headers=ANITA, json={"summary": " "}).status_code == 422
    s = client.post(f"/triage/episodes/{eid}/close", headers=ANITA, json={"summary": d}).json()
    assert (s["status"], s["closed_by"], s["close_note"]) == ("closed", "u_anita", d)
    assert s["closing"]["edited"] is False and s["closing"]["recommendation"] is None
    # Routine messages resume after closure; the brief shows the record and the episode, with sources.
    b = client.get(f"/triage/brief/{MRN}", headers=SEKHAR).json()
    kinds = [line["kind"] for line in b["lines"]]
    assert kinds[:2] == ["record", "record"] and "message" in kinds and "result" in kinds
    voice = next(line for line in b["lines"] if line["kind"] == "message")
    assert voice["sources"][0]["kind"] == "audio"
    assert b["lines"][-1]["text"].startswith("Closed by Anita")


def test_play_as_on_a_finished_call_replays_the_same_voice_note(vt, client):
    voice_note(client)
    first = play(client, "amber")
    second = play(client, "green", first["episode_id"])
    assert second["episode_id"] != first["episode_id"] and second["tier"] == "TIER_3_GREEN"
    assert second["inbound_id"] == first["inbound_id"]
    old = client.get(f"/triage/episodes/{first['episode_id']}", headers=ANITA).json()
    assert old["status"] == "closed" and old["close_note"].startswith("Demo replay")


def test_routine_messages_are_held_while_an_episode_is_open(vt, client):
    voice_note(client)
    from app import db, messaging

    with db.connect() as c:
        p = c.execute("SELECT * FROM patient WHERE mrn = %s", (MRN,)).fetchone()
        assert messaging.guard(c, p, "booking_reminder", "patient", "agent")[0] == "held_triage_open"
        assert messaging.guard(c, p, "instruction_booking", "patient", "agent")[0] is None


def test_there_is_no_er_desk_any_more(vt, client):
    assert client.get("/triage/episodes", headers=users_header("u_er_nurse")).status_code == 401
    voice_note(client)
    eid = play(client, "amber")["episode_id"]
    for gone in ("er-log", "er-close", "ed-call", "nurse-callback", "confirm-closure", "followup", "digest-ack",
                 "outcome"):
        assert client.post(f"/triage/episodes/{eid}/{gone}", headers=ANANYA, json={}).status_code in (404, 405), gone


@pytest.mark.demo_template
def test_golden_path_health_message_goes_to_voice_triage(monkeypatch, seeded, client):
    from app import db, reference, seed

    monkeypatch.delenv("CAREPILOT_SEED_DIR")
    real = json.loads((reference.SEED_DIR / "settings.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(seed, "step_a_settings", lambda: real)
    with db.connect() as c:
        r = seed.reset(c, scenario="step_a")
    assert r["patients"] == 3 and r["users"] == 5  # triage v3: no ER Desk users
    priya = users_header("u_priya")
    out = voice_note(client, "SYN-0011", priya, clip="HI-04")
    assert out["triage_episode_id"] and out["handover_id"] is None
    eid = out["triage_episode_id"]
    s = client.post(f"/triage/episodes/{eid}/play", headers=priya, json={"scenario": "red"}).json()
    # Savitri speaks Hindi: the call, the scripted answers and her son's Red message are in Hindi.
    assert s["tier"] == "TIER_1_RED" and s["language"] == "Hindi" and "सावित्री जी के लिए" in s["turns"][1]["ta"]
    msg = [m for m in sent("SYN-0011") if m["template_id"] == "triage_red_caregiver"]
    assert len(msg) == 1 and msg[0]["to_phone"] == "+919000100011" and msg[0]["language"] == "Hindi"
    assert "सावित्री जी ने हमारी कॉल में" in msg[0]["body"]
    # Nirmala speaks Telugu.
    out = voice_note(client, "SYN-0013", priya, clip="TE-01")
    s = client.post(f"/triage/episodes/{out['triage_episode_id']}/play", headers=priya, json={"scenario": "green"}).json()
    assert s["tier"] == "TIER_3_GREEN" and s["language"] == "Telugu" and "నిర్మల గారి కోసం" in s["turns"][1]["ta"]
    assert any("షెల్‌కాల్-HD" in t["ta"] for t in s["turns"] if t["who"] == "agent")
    # Lakshmi V. is the third Golden Path patient: the guide's scenarios, with the Golden Path's doctors.
    out = voice_note(client, "SYN-0142", priya)
    assert out["triage_episode_id"] and out["handover_id"] is None
    s = client.post(f"/triage/episodes/{out['triage_episode_id']}/play", headers=priya, json={"scenario": "amber"}).json()
    assert s["tier"] == "TIER_2_AMBER" and set(s["result"]["reason_codes"]) == {
        "AMBER_BANGLES_TIGHTER_THAN_BASELINE", "AMBER_HEADACHE_RISE_2_PLUS", "AMBER_HEADACHE_WORSE_THAN_LAST_TIME",
        "AMBER_NAUSEA_DESPITE_MEALS", "AMBER_DROWSY_MOST_OF_TODAY"}
    assert any("2026-10-10" in w or "10 Oct" in w for w in s["result"]["why"])
    assert "Dr. Kavita Menon" in json.dumps(s["history"]["advice"])


# ---------------------------------------------------------------------------------------------------------------
# The call recording (the call put back together for a doctor or coordinator to check the transcript)
# ---------------------------------------------------------------------------------------------------------------
def wav(seconds: float, rate: int, channels: int = 1, hz: int = 440) -> bytes:
    import io
    import math
    import struct
    import wave

    frames = b"".join(struct.pack("<" + "h" * channels, *[int(8000 * math.sin(2 * math.pi * hz * k / rate))] * channels)
                      for k in range(int(seconds * rate)))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)
    return buf.getvalue()


def read_wav(data: bytes) -> tuple[int, float]:
    import io
    import wave

    with wave.open(io.BytesIO(data)) as w:
        return w.getframerate(), w.getnframes() / w.getframerate()


def test_call_recording_puts_the_call_back_together_and_is_audited(vt, client, monkeypatch):
    from app import ai

    monkeypatch.setattr(ai, "speak", lambda conn, text, language, cached_only=False: (wav(1.0, 22050), "audio/wav"))
    monkeypatch.setattr(ai, "hear", lambda conn, **kw: {"transcript": "இல்லை", "translation": "No", "model": "test"})
    voice_note(client)
    eid = episode(client)["episode_id"]
    client.post(f"/triage/episodes/{eid}/pickup", headers=ANITA)
    s = client.post(f"/triage/episodes/{eid}/answer-voice", headers=ANITA,
                    files={"file": ("answer.wav", wav(2.0, 8000, channels=2), "audio/wav")}).json()
    assert [t for t in s["turns"] if t["who"] == "patient"][0]["value"] == "NO"  # spoken answer to question 1
    s = client.post(f"/triage/episodes/{eid}/answer", headers=ANITA, json={"value": "YES"}).json()  # a button: Red
    assert s["tier"] == "TIER_1_RED" and s["has_call"] is True

    r = client.get(f"/triage/episodes/{eid}/recording", headers=ANANYA)
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    rate, seconds = read_wav(r.content)
    agent = sum(t["who"] == "agent" for t in s["turns"])
    # Bulbul lines (1 s each), her spoken answer (2 s, 8 kHz stereo resampled), the tone for the button (0.3 s),
    # a 0.45 s pause after each.
    assert rate == 16000 and abs(seconds - (agent * 1.0 + 2.0 + 0.3 + (agent + 2) * 0.45)) < 0.05
    rows = client.get("/audit", headers=ANITA, params={"mrn": MRN, "limit": 400}).json()["entries"]
    opened = [x for x in rows if x["action"] == "triage_recording_opened"]
    assert opened and opened[0]["actor_name"] == "u_ananya"
    # The ticket's summary lines jump to where she said each answer: her spoken answer to question 1 starts after
    # the opening and the question (1 s each, 0.45 s pause after each).
    s = episode(client, headers=ANANYA)
    q1 = next(x for x in s["summary"] if x["text"].startswith("Speech slurred"))
    assert s["rec_offsets"][str(q1["turn"])] == 2.9
    # Its play button plays just question 1 and her 2-second answer, then stops (defect 3)
    assert s["rec_spans"][str(q1["turn"])] == [1.45, 4.9]
    assert client.get(f"/triage/episodes/{eid}/recording", headers=RAVI).status_code == 200
    assert client.get(f"/triage/episodes/{eid}/recording", headers=users_header("u_suresh")).status_code == 403
    brief = client.get(f"/triage/brief/{MRN}", headers=SEKHAR).json()
    assert any(src["kind"] == "recording" for line in brief["lines"] for src in line["sources"])


def test_no_recording_when_she_never_answered_or_in_simulated_mode(vt, client):
    voice_note(client)
    eid = episode(client)["episode_id"]
    client.post(f"/triage/episodes/{eid}/no-answer", headers=ANITA)
    s = client.post(f"/triage/episodes/{eid}/no-answer", headers=ANITA).json()
    assert s["red_kind"] == "NO_ANSWER" and s["has_call"] is False
    assert client.get(f"/triage/episodes/{eid}/recording", headers=ANANYA).status_code == 404
    s = client.post(f"/triage/episodes/{eid}/play", headers=ANITA, json={"scenario": "amber"}).json()
    assert s["has_call"] is True  # a call took place, but simulated mode has no Bulbul audio and no voice answers
    assert client.get(f"/triage/episodes/{s['episode_id']}/recording", headers=ANANYA).status_code == 404


# ---------------------------------------------------------------------------------------------------------------
# The Triage Room: tickets, who may change the tier, the Green digest, the Amber cover alert
# ---------------------------------------------------------------------------------------------------------------
COORD_ONLY_RAISE = "Only the Doctor on Duty lowers the tier; the coordinator can only raise it"


def tier(client, eid, headers, to, reason="checked with her"):
    return client.post(f"/triage/episodes/{eid}/tier", headers=headers, json={"tier": to, "reason": reason})


def test_ticket_has_an_id_a_countdown_and_one_next_action_per_role(vt, client):
    voice_note(client)
    ep = episode(client)
    assert ep["ticket_id"] == "CP-20261027-0001"
    assert ep["due"]["what"] == "Automated call" and ep["due"]["at"][11:16] == "10:14"
    s = play(client, "amber", ep["episode_id"])
    assert s["tier_set_by"] == "Rules (ROUTED, protocol v1.2, DRAFT_UNSIGNED)"
    assert "Only the Doctor on Duty can lower it" in s["tier_rule"] and s["owner"] == "Anita"  # her review first
    assert s["next"]["coordinator"]["text"].startswith("Review the AI's triage: Confirm, Raise tier")
    assert s["next"]["doctor"] == {"text": "Dr. Ananya: waiting for the coordinator's review (Confirm, Raise tier or "
                                           "Flag as wrong)", "done": False, "due_at": None, "waiting": True}
    s = confirm(client, s["episode_id"])
    assert s["owner"] == "Dr. Ananya"
    # The countdown shows the nearest deadline: acknowledge by 90 minutes, before the 2-hour callback.
    assert [(d["what"], d["at"][11:16]) for d in s["deadlines"]] == [("Doctor: acknowledge", "11:42"),
                                                                     ("Doctor callback (2 hours)", "12:12")]
    assert s["due"]["what"] == "Doctor: acknowledge"
    nxt = s["next"]
    assert nxt["doctor"]["text"].startswith("Dr. Ananya: Acknowledge; call her by 12:12")
    assert "nurse" not in nxt and nxt["coordinator"] == {"text": "Confirmed by Anita", "done": True, "due_at": None}
    assert "a doctor will call within 2 hours" in nxt["ai"]["text"]
    # The summary: one line per answer, each linked to the turn where she said it; then the record.
    lines = s["summary"]
    assert lines[0]["text"] == "Speech slurred or confused: no" and lines[0]["turn"] is not None
    assert s["turns"][lines[0]["turn"]]["who"] == "patient" and s["turns"][lines[0]["turn"]]["id"] == "Q_SLURRED"
    assert any(x["source"] == "hospital record" and x["turn"] is None for x in lines)
    # Replaying makes a new ticket the same day.
    s2 = play(client, "green", ep["episode_id"])
    assert s2["ticket_id"] == "CP-20261027-0002"


def test_the_coordinator_raises_only_the_doctor_on_duty_lowers(vt, client):
    voice_note(client)
    eid = play(client, "green")["episode_id"]
    assert tier(client, eid, ANITA, "TIER_2_AMBER", "").status_code == 422
    s = tier(client, eid, ANITA, "TIER_2_AMBER", "she sounded breathless").json()
    assert s["tier"] == "TIER_2_AMBER" and s["tier_set_by"].startswith("Raised by Anita")
    assert s["callback_due_at"][11:16] == "12:12" and s["followup_due_on"] is None
    # Raising is the coordinator's review: it goes to the doctor at once
    assert s["review"]["action"] == "RAISED" and s["review_text"] == "Raised by Anita: “she sounded breathless”"
    assert s["next"]["coordinator"]["done"] and s["owner"] == "Dr. Ananya"
    r = tier(client, eid, ANITA, "TIER_3_GREEN")
    assert r.status_code == 409 and r.json()["detail"] == COORD_ONLY_RAISE
    s = tier(client, eid, ANANYA, "TIER_3_GREEN", "spoke to her: fine").json()
    assert s["tier"] == "TIER_3_GREEN" and s["followup_due_on"] is None
    # Raised to Red by hand: Red (unconfirmed), the coordinator calls back in 15 minutes, no family message by itself.
    s = tier(client, eid, RAVI, "TIER_1_RED", "arm now red on video call").json()
    assert (s["tier"], s["red_kind"], s["callback_asked"]) == ("TIER_1_RED", "UNCONFIRMED", True)
    assert s["callback_due_at"][11:16] == "10:27" and s["package_sent_at"] is None
    assert not [m for m in sent() if m["template_id"] == "triage_red_caregiver"]
    assert s["next"]["coordinator"]["text"].startswith("Call her back by 10:27")
    assert tier(client, eid, RAVI, "TIER_2_AMBER").status_code == 409
    r = client.post(f"/triage/episodes/{eid}/callback", headers=RAVI, json={"note": "Arm red; going to ED"})
    assert r.status_code == 200, r.text
    s = client.post(f"/triage/episodes/{eid}/send-package", headers=ANANYA).json()
    assert s["package_sent_at"] and "Raised to Red by Ravi" in s["doctor_note"]
    s = tier(client, eid, ANANYA, "TIER_2_AMBER", "seen in ED: settled").json()  # the doctor lowers any tier
    assert (s["tier"], s["red_kind"]) == ("TIER_2_AMBER", None)
    rows = client.get("/audit", headers=ANITA, params={"mrn": MRN, "limit": 400}).json()["entries"]
    assert {"tier_raised", "tier_lowered", "red_alert_created"} <= {x["action"] for x in rows}


def test_green_lowered_by_the_doctor_she_closes_it_herself(vt, client):
    voice_note(client)
    s = play(client, "green")
    eid = s["episode_id"]
    assert [(d["what"], d["at"]) for d in s["deadlines"]] == [("Review and close", "2026-10-28T18:00:00")]
    assert [p["persona"] for p in s["pending"]] == ["Care coordinator"]
    assert client.post(f"/triage/episodes/{eid}/close", headers=users_header("u_suresh"),
                       json={"summary": "x"}).status_code == 403
    s = tier(client, eid, ANITA, "TIER_2_AMBER", "she sounded worse").json()
    s = tier(client, eid, ANANYA, "TIER_3_GREEN", "called her: she is fine").json()
    d = client.post(f"/triage/episodes/{eid}/summary-draft", headers=ANANYA,
                    json={"recommendation": "No change", "contact": "VIDEO"}).json()["draft"]
    assert d.endswith("Dr. Ananya recommended after contacting her by video: “No change”.")
    s = client.post(f"/triage/episodes/{eid}/close", headers=ANANYA,
                    json={"summary": d + " Edited.", "recommendation": "No change", "contact": "VIDEO"}).json()
    assert (s["status"], s["closed_by"]) == ("closed", "u_ananya") and s["closing"]["edited"] is True
    rows = client.get("/audit", headers=ANITA, params={"mrn": MRN, "limit": 100}).json()["entries"]
    sub = next(x for x in rows if x["action"] == "closing_summary_submitted")
    assert sub["outcome"] == "EDITED" and sub["details"]["draft"] == d and sub["details"]["summary"] == d + " Edited."
    assert any(x["action"] == "green_closed_after_review" for x in rows)


def doctor_call(client, eid, outcome, headers=ANANYA, note=None):
    return client.post(f"/triage/episodes/{eid}/doctor-call", headers=headers, json={"outcome": outcome, "note": note})


def at(client, hhmm, day="2026-10-27"):
    client.post("/clock/set", headers=ANITA, json={"date": day, "time": hhmm})


def test_amber_reminds_the_duty_doctor_at_90_minutes_and_is_overdue_at_120(vt, client):
    from conftest import scalar

    voice_note(client)
    eid = amber(client)
    at(client, "11:41")
    assert episode(client)["reminded_at"] is None
    at(client, "11:42")
    s = episode(client)
    assert s["reminded_at"][11:16] == "11:42" and not s["overdue"]
    assert scalar("SELECT count(*) FROM coordinator_task WHERE triage_episode_id = %s", eid) == 0
    at(client, "12:12")
    s = episode(client)
    assert s["overdue"] and s["overdue_at"][11:16] == "12:12" and s["tier"] == "TIER_2_AMBER"
    assert s["owner"] == "Dr. Sekhar" and s["pending"][0]["persona"] == "Senior Oncologist (backup)"
    assert s["next"]["doctor"]["text"].startswith("OVERDUE.")
    assert s["next"]["coordinator"]["text"].startswith("OVERDUE: no doctor's call in 2 hours")
    episode(client)  # read again: one escalation, one task
    assert scalar("SELECT count(*) FROM coordinator_task WHERE task_type = 'triage_overdue'") == 1
    rows = client.get("/audit", headers=ANITA, params={"mrn": MRN, "limit": 100}).json()["entries"]
    esc = next(x for x in rows if x["action"] == "amber_overdue_escalated")
    assert "Dr. Sekhar (Senior Oncologist)" in esc["details"]["to"]
    assert {"doctor_reminder_sent"} <= {x["action"] for x in rows}
    # The backup (any oncologist) picks it up: reached, bloods ordered, closed now
    assert doctor_call(client, eid, "PICKED_UP", headers=SEKHAR).status_code == 200
    s = client.post(f"/triage/episodes/{eid}/order", headers=SEKHAR, json={"order": "bloods"}).json()
    assert "Book the blood tests brought forward" in s["next"]["coordinator"]["text"]
    assert not s["overdue"] and s["owner"] == "Dr. Ananya"  # a doctor called: the escalation is answered
    assert scalar("SELECT status FROM coordinator_task WHERE task_type = 'triage_overdue'") == "done"


def test_amber_picked_up_ordered_and_closed_with_the_summary(vt, client):
    """Proposal test 1: picked up and closed. Only the Doctor on Duty acknowledges and closes."""
    from conftest import scalar

    voice_note(client)
    eid = amber(client)
    r = client.post(f"/triage/episodes/{eid}/acknowledge", headers=ANITA)
    assert r.status_code == 409 and "the coordinator reviews it" in r.json()["detail"]
    s = client.post(f"/triage/episodes/{eid}/acknowledge", headers=ANANYA).json()
    assert (s["state"], s["coord_ack_by"]) == ("ACKNOWLEDGED", "u_ananya")
    body = {"summary": "s", "recommendation": "fine", "contact": "PHONE"}
    assert client.post(f"/triage/episodes/{eid}/close", headers=ANANYA, json=body).status_code == 409  # no call yet
    doctor_call(client, eid, "PICKED_UP", note="Spoke to her at 10:40")
    r = client.post(f"/triage/episodes/{eid}/close", headers=ANANYA, json=body)
    assert r.status_code == 409 and "orders first" in r.json()["detail"]
    client.post(f"/triage/episodes/{eid}/order", headers=ANANYA, json={"order": "none"})
    assert client.post(f"/triage/episodes/{eid}/order", headers=ANANYA, json={"order": "physio"}).status_code == 409
    assert client.post(f"/triage/episodes/{eid}/close", headers=ANANYA,
                       json={**body, "contact": "EMAIL"}).status_code == 422
    s = close_amber(client, eid, rec="Drowsy from a late night; no change needed")
    assert (s["status"], s["state"], s["closed_by"]) == ("closed", "CLOSED", "u_ananya")
    assert s["followup_due_on"] is None and s["pending"] == []
    assert scalar("SELECT count(*) FROM coordinator_task WHERE triage_episode_id = %s", eid) == 0
    assert s["next"]["doctor"]["text"].startswith("Closed by Dr. Ananya")


def test_amber_not_reached_goes_to_the_attender_then_red_unconfirmed(vt, client):
    """Proposal test 2: two attempts 10 minutes apart, the coordinator calls the attender, still unreached at the
    end of the 2 hours: Red (unconfirmed), and the doctor may send the emergency package."""
    from conftest import scalar

    voice_note(client)
    eid = amber(client)
    s = doctor_call(client, eid, "NO_ANSWER").json()
    assert ("Doctor: second call attempt", "10:22") in [(d["what"], d["at"][11:16]) for d in s["deadlines"]]
    assert "Call again" in s["next"]["doctor"]["text"]
    at(client, "10:22")
    s = doctor_call(client, eid, "NO_ANSWER").json()
    assert doctor_call(client, eid, "NO_ANSWER").status_code == 409  # two attempts are enough
    assert "call_attender" in s["open_tasks"] and s["owner"] == "Anita"
    assert s["next"]["coordinator"]["text"].startswith("Call her attender")
    assert client.post(f"/triage/episodes/{eid}/attender-call", headers=ANANYA,
                       json={"outcome": "NOT_REACHABLE"}).status_code == 403
    s = client.post(f"/triage/episodes/{eid}/attender-call", headers=ANITA, json={"outcome": "NOT_REACHABLE"}).json()
    assert s["attender_calls"][0]["outcome"] == "NOT_REACHABLE" and s["tier"] == "TIER_2_AMBER"
    at(client, "12:11")
    assert episode(client)["tier"] == "TIER_2_AMBER"
    at(client, "12:12")
    s = episode(client)
    assert (s["tier"], s["red_kind"], s["package_sent_at"]) == ("TIER_1_RED", "UNCONFIRMED", None)
    assert s["tier_changes"][-1]["reason"].startswith("Not reached by the end of the 2-hour callback")
    assert not [m for m in sent() if m["template_id"] == "triage_red_caregiver"]  # a person decides
    assert scalar("SELECT count(*) FROM coordinator_task WHERE task_type = 'triage_overdue'") == 0
    assert s["awaits_review"] is False  # reviewed as Amber: the doctor goes on (no second review)
    r = client.post(f"/triage/episodes/{eid}/send-package", headers=ANANYA)
    assert r.status_code == 200, r.text
    assert r.json()["package_sent_by"] == "u_ananya"
    assert len([m for m in sent() if m["template_id"] == "triage_red_caregiver"]) == 1


def test_amber_attender_reached_then_the_doctor_orders(vt, client):
    voice_note(client)
    eid = amber(client)
    doctor_call(client, eid, "NO_ANSWER")
    doctor_call(client, eid, "NO_ANSWER")
    r = client.post(f"/triage/episodes/{eid}/attender-call", headers=ANITA, json={"outcome": "REACHED"})
    assert r.status_code == 422  # what they said is needed
    s = client.post(f"/triage/episodes/{eid}/attender-call", headers=ANITA,
                    json={"outcome": "REACHED", "note": "Son: she is asleep, phone on silent; will call back"}).json()
    assert s["reached"] and "call_attender" not in s["open_tasks"] and s["owner"] == "Dr. Ananya"
    s = client.post(f"/triage/episodes/{eid}/order", headers=ANANYA, json={"order": "visit"}).json()
    assert s["orders"][0]["card_id"] and "Book the advise visit" in s["next"]["coordinator"]["text"]
    at(client, "12:30")
    assert episode(client)["tier"] == "TIER_2_AMBER"  # reached: no Red at 2 hours


# ---------------------------------------------------------------------------------------------------------------
# v1.2: the message route, the standard reply, ticket states, facts-only ticket text
# ---------------------------------------------------------------------------------------------------------------
def text_note(client, text, mrn=MRN) -> dict:
    r = client.post(f"/simulator/{mrn}/reply", headers=ANITA, json={"text": text})
    assert r.status_code == 201, r.text
    return r.json()


def test_scene_2_shopping_gets_one_standard_reply_and_nothing_else(vt, client):
    from conftest import scalar

    r = text_note(client, "I want to go shopping tomorrow")
    assert r["handling"] == ["ai_tags_not_health", "standard_reply:TPL_WA_NOT_HEALTH_V1"]
    assert r.get("triage_episode_id") is None and r["proposal_id"] is None and r["handover_id"] is None
    reply = [m for m in sent() if m["template_id"] == "triage_not_health"]
    assert len(reply) == 1 and reply[0]["body_en"].startswith("Thank you for your message. This number is only for")
    assert "உதவி மையத்தை" in reply[0]["body"]  # in her language (Tamil)
    assert scalar("SELECT count(*) FROM triage_episode") == 0 and scalar("SELECT count(*) FROM coordinator_task") == 0
    rows = client.get("/audit", headers=ANITA, params={"mrn": MRN, "limit": 50}).json()["entries"]
    decision = next(x for x in rows if x["reason_code"] == "not_health_standard_reply")
    assert decision["outcome"] == "standard_reply"
    # "Shopping, but my arm is swollen": a symptom always wins
    r = text_note(client, "I want to go shopping but my arm is swollen")
    assert any(h.startswith("triage_call_due") for h in r["handling"])
    ep = episode(client)
    assert "arm_swelling_or_redness" in ep["plan"]["modules"] and len(ep["table"]) == 8


def test_scene_7_severe_arm_pain_is_eight_questions_and_amber_by_her_word(vt, client):
    text_note(client, "I have severe pain in my left arm")
    ep = episode(client)
    assert len(ep["table"]) == 8 and ep["plan"]["floors"] == ["AMBER_PATIENT_SAYS_SEVERE"]
    assert ep["plan"]["heard"] == ["arm pain", "“severe”"]
    s = play(client, "mild_arm", ep["episode_id"])
    assert (s["tier"], s["result"]["reason_codes"]) == ("TIER_2_AMBER", ["AMBER_PATIENT_SAYS_SEVERE"])
    assert s["result"]["why"] == ["She described her problem as severe in her own message."]


def test_a_rash_gets_six_questions_and_the_duty_doctor(vt, client):
    text_note(client, "I have a rash on my back")
    ep = episode(client)
    assert len(ep["table"]) == 6 and ep["plan"]["floors"] == ["AMBER_OTHER_SYMPTOM_REPORTED"]
    s = play(client, "green", ep["episode_id"])
    assert (s["tier"], s["result"]["action_code"], s["callback_asked"]) == ("TIER_2_AMBER", "DOCTOR_CALLBACK_2H", False)
    assert set(s["next"]) == {"ai", "doctor", "coordinator"} and s["owner"] == "Anita"  # her review first
    assert confirm(client, s["episode_id"])["owner"] == "Dr. Ananya"
    assert s["callback_due_at"][11:16] == "12:12"


def test_amber_ticket_states_and_who_may_move_them(vt, client):
    voice_note(client)
    eid = amber(client)
    assert episode(client)["state"] == "OPEN"
    r = client.post(f"/triage/episodes/{eid}/acknowledge", headers=RAVI)  # the coordinator reviews, not acknowledges
    assert r.status_code == 409
    s = client.post(f"/triage/episodes/{eid}/acknowledge", headers=ANANYA).json()
    assert (s["state"], s["coord_ack_by"]) == ("ACKNOWLEDGED", "u_ananya")
    assert client.post(f"/triage/episodes/{eid}/acknowledge", headers=SEKHAR).status_code == 409  # already
    s = close_amber(client, eid, "none", rec="Better")
    assert (s["state"], s["closed_by"]) == ("CLOSED", "u_ananya")
    # Any staff reopens with a reason: Reopened until the doctor acknowledges again
    assert client.post(f"/triage/episodes/{eid}/reopen", headers=RAVI, json={}).status_code == 422
    s = client.post(f"/triage/episodes/{eid}/reopen", headers=RAVI, json={"note": "called again: worse"}).json()
    assert (s["status"], s["state"], s["closing"]) == ("open", "REOPENED", None)
    # A new 2-hour round for the duty doctor; the round that ended is kept
    assert s["orders"] == [] and len(s["rounds"]) == 1
    assert s["next"]["doctor"]["text"].startswith("Dr. Ananya: Acknowledge; call her by")


def close_amber(client, eid, *orders, rec="Reviewed on the phone", contact="PHONE"):
    """The duty doctor reaches her, records the orders and closes with her recommendation and the drafted summary."""
    if not episode(client)["reached"]:
        assert doctor_call(client, eid, "PICKED_UP").status_code == 200
    for o in orders:
        r = client.post(f"/triage/episodes/{eid}/order", headers=ANANYA, json={"order": o})
        assert r.status_code == 200, r.text
    body = {"recommendation": rec, "contact": contact}
    d = client.post(f"/triage/episodes/{eid}/summary-draft", headers=ANANYA, json=body).json()["draft"]
    r = client.post(f"/triage/episodes/{eid}/close", headers=ANANYA, json={**body, "summary": d})
    assert r.status_code == 200, r.text
    return r.json()


def test_red_the_doctor_acknowledges_and_closes(vt, client):
    voice_note(client)
    eid = play(client, "red")["episode_id"]
    r = client.post(f"/triage/episodes/{eid}/acknowledge", headers=ANITA)
    assert r.status_code == 409 and "Doctor on Duty acknowledges" in r.json()["detail"]
    confirm(client, eid)
    s = client.post(f"/triage/episodes/{eid}/acknowledge", headers=ANANYA).json()
    assert s["state"] == "ACKNOWLEDGED" and s["next"]["doctor"]["text"] == "Dr. Ananya: Record your recommendation and close"
    s = client.post(f"/triage/episodes/{eid}/close", headers=ANANYA,
                    json={"summary": "Admitted.", "recommendation": "Admitted at City General ED", "contact": "IN_PERSON"}).json()
    assert s["state"] == "CLOSED" and s["closing"]["contact"] == "IN_PERSON"


def test_an_open_ticket_is_raised_not_reopened(vt, client):
    voice_note(client)
    eid = play(client, "green")["episode_id"]
    assert episode(client)["state"] == "OPEN"
    r = client.post(f"/triage/episodes/{eid}/reopen", headers=ANITA, json={"note": "her son says she is worse"})
    assert r.status_code == 409 and "raise it to Amber" in r.json()["detail"]


FORBIDDEN = ("diagnos", "cellulitis", "lymphoedema", "lymphedema", "infection", "likely", "probably", "grade",
             "you should", "we recommend", "risk score")


@pytest.mark.parametrize("scenario", ["amber", "green", "red", "unclear_chest", "nurse"])
def test_tr21_no_sentence_on_the_ticket_is_an_assessment_or_advice(vt, client, scenario):
    voice_note(client)
    s = play(client, scenario)
    shown = json.dumps([s["result"]["why"], s["summary"], s["next"], s["proposed"], s["tier_set_by"],
                        s["doctor_note"], s["not_asked"]], ensure_ascii=False).lower()
    for word in FORBIDDEN:
        assert word not in shown, (scenario, word)


@pytest.mark.parametrize("clip,road", [(c, r) for r, cs in {"severe": ("HI-05", "TE-02", "TA-02"), "tired": ("HI-06", "TE-03", "TA-03"),
                                                                "shop": ("HI-07", "TE-04", "TA-04")}.items() for c in cs])
def test_v12_voice_notes_take_their_road_in_every_language(clip, road):
    from app import ai

    native, en = ai.FAKE_CLIPS[clip]
    assert (Path(__file__).parents[1] / "backend/app/seed_data/uc2a/audio" / f"{clip}.wav").stat().st_size > 10000
    reply = ai.Fake().chat("", f"{TR.TAG_PROMPT}\n\nMessage: {native}\n(English translation: {en})")
    p = TR.plan(LAKSHMI, TR.parse_tags(reply))
    if road == "shop":
        assert p["route"] == "STANDARD_REPLY"
    elif road == "severe":
        assert (len(p["question_ids"]), p["floors"]) == (8, ["AMBER_PATIENT_SAYS_SEVERE"])
    else:
        assert p["modules"][-1] == "tiredness" and p["floors"] == []


# ---------------------------------------------------------------------------------------------------------------
# Testing round 31 Oct 2026 (Defects/31oct-TestingV1.0.xlsx): the fixes, and the two mandatory scenarios
# ---------------------------------------------------------------------------------------------------------------
MIX = {"health_related": "YES", "symptoms": ["headache", "fever_chills", "nausea_vomiting", "tiredness"],
       "severity_word": "NONE"}  # mandatory scenario 1: headache, fever, vomiting and tiredness


def test_mandatory_1_headache_fever_vomiting_tiredness_asks_17_and_skips_the_rest():
    p = TR.plan(LAKSHMI, MIX)
    assert p["question_ids"] == ["Q_SLURRED", "Q_AWAKE", "Q_CHEST", "Q_ARM_RED", "Q_TEMP", "Q_BANGLES",
                                 "Q_CHILLS", "Q_FEELS_HOT", "Q_HEADACHE", "Q_HEAD_SCORE", "Q_HEAD_WORSE",
                                 "Q_NAUSEA", "Q_VOMIT", "Q_LIQUIDS", "Q_SUPP", "Q_TIRED_DAYS", "Q_TIRED_WORSE"]
    assert TR.not_asked(p) == ["dizziness and vision", "sleepiness", "arm pain", "arm swelling and skin"]
    # No headache today skips its score and "worse"; no nausea and no vomiting skip supplements and water
    a = {**R.blank(), "headache_present": R.NO, "nausea_since_yesterday": R.NO, "vomiting_count": 0.0}
    left = [q.id for q in R.questions(LAKSHMI, p["question_ids"]) if not R.skipped(q, a)]
    assert "Q_HEAD_SCORE" not in left and "Q_HEAD_WORSE" not in left and "Q_SUPP" not in left
    assert "Q_LIQUIDS" not in left and len(left) == 13


MIX_BASE = {"shaking_chills": R.NO, "feels_hot": R.NO, "tired_days": 2.0, "tired_getting_worse": R.NO,
            "cannot_stay_awake": R.NO, "chest_pain": R.NO}


@pytest.mark.parametrize("kw,tier,codes", [
    ({}, "TIER_3_GREEN", []),
    ({"vomiting_count": 3.0, "nausea_since_yesterday": R.YES, "can_keep_liquids_down": R.YES,
      "took_supplements_after_full_meals": R.NO}, "TIER_2_AMBER", ["AMBER_VOMITING_2_TO_5"]),
    ({"tired_days": 8.0}, "TIER_2_AMBER", ["AMBER_TIREDNESS_7_DAYS_PLUS"]),
    ({"tired_getting_worse": R.YES}, "TIER_2_AMBER", ["AMBER_TIREDNESS_GETTING_WORSE"]),
    ({"headache_present": R.YES, "headache_score": 5.0, "headache_worse_than_last_time": R.YES}, "TIER_2_AMBER",
     ["AMBER_HEADACHE_RISE_2_PLUS", "AMBER_HEADACHE_WORSE_THAN_LAST_TIME"]),
    ({"thermometer_used": R.NO, "temperature_value": None, "feels_hot": R.YES}, "TIER_2_AMBER",
     ["AMBER_FEELS_HOT_NO_THERMOMETER"]),
    ({"temperature_value": 100.8}, "TIER_1_RED", ["RED_FEVER_100_4F_PLUS"]),
    ({"shaking_chills": R.YES}, "TIER_1_RED", ["RED_SHAKING_CHILLS"]),
    ({"vomiting_count": 6.0, "nausea_since_yesterday": R.YES}, "TIER_1_RED", ["RED_VOMITING_6_PLUS"]),
    ({"nausea_since_yesterday": R.YES, "vomiting_count": 1.0, "can_keep_liquids_down": R.NO}, "TIER_1_RED",
     ["RED_CANNOT_KEEP_LIQUIDS"]),
    ({"headache_present": R.YES, "headache_score": 8.0}, "TIER_1_RED", ["RED_HEADACHE_SEVERE_7_PLUS"]),
])
def test_mandatory_2_every_rag_road_for_that_mix(kw, tier, codes):
    p = TR.plan(LAKSHMI, MIX)
    r = R.triage(answers(**{**MIX_BASE, **kw}), LAKSHMI, TODAY, floors=p["floors"], profile=p["profile"])
    assert r["tier"] == tier and sorted(c for c in r["reason_codes"] if c in codes) == sorted(codes), r["reason_codes"]


def test_d1_the_question_bank_is_listed_for_triage_staff(vt, client):
    bank = client.get("/triage/questions", headers=RAVI).json()
    assert [q["id"] for q in bank["questions"]] == list(R.QUESTION_IDS) and len(bank["questions"]) == 24
    head = next(q for q in bank["questions"] if q["id"] == "Q_HEAD_SCORE")
    assert head["module"] == "headache" and head["danger"] and head["skip"] == "Skipped if she has no headache today"
    assert set(head["text"]) == {"English", "Tamil", "Hindi", "Telugu"} and head["fixed_12"]
    assert [m["tag"] for m in bank["modules"]][:2] == ["core", "history_arm"]
    assert client.get("/triage/questions", headers=users_header("u_suresh")).status_code == 403


def test_d2_a_patients_name_opens_her_card_for_every_triage_role(vt, client):
    voice_note(client)
    for who in (ANITA, ANANYA, RAVI):
        c = client.get(f"/patients/{MRN}/card", headers=who)
        assert c.status_code == 200, c.text
    c = c.json()
    assert (c["record"]["title"], c["record"]["caregiver_relation"], c["caregiver_phone"]) == \
        ("Mrs. Lakshmi V.", "son", "+919000100142")
    assert c["record"]["medicines"][0].startswith("Shelcal-HD") and c["triage"][0]["status"] == "open"
    assert client.get("/patients/SYN-9999/card", headers=ANITA).status_code == 404
    assert client.get(f"/patients/{MRN}/card", headers=users_header("u_suresh")).status_code == 403


def test_d4_her_voice_notes_are_listed_with_date_and_time_newest_first(vt, client):
    voice_note(client)
    text_note(client, "My head hurts more now")
    notes = episode(client)["patient_notes"]
    assert [n["kind"] for n in notes] == ["text", "voice"] and notes[0]["text"] == "My head hurts more now"
    assert all(n["received_on"] == "2026-10-27" and n["received_time"] for n in notes)


def thread(client, mrn=MRN, headers=ANITA) -> list[dict]:
    return client.get(f"/patients/{mrn}/messages", headers=headers).json()


def test_d9_the_standard_reply_comes_after_her_message_in_the_chat(vt, client):
    text_note(client, "I want to go shopping tomorrow")
    t = thread(client)
    assert [(m["direction"], m["template_id"]) for m in t[-2:]] == [("in", None), ("out", "triage_not_health")]
    assert "standard_reply" in t[-2]["handling"] and t[-2]["triage_episode_id"] is None


@pytest.mark.parametrize("scenario,template,says", [
    ("amber", "triage_outcome_amber", "A doctor will call you within 2 hours"),
    ("red", "triage_outcome_red", "The Doctor on Duty has been alerted."),
    ("unclear_chest", "triage_outcome_nurse", "A member of hospital staff will call you within 15 minutes"),
    ("green", None, None),
])
def test_d10_d11_after_the_call_she_is_told_who_calls_and_when_and_the_guardrail(vt, client, scenario, template, says):
    voice_note(client)
    s = play(client, scenario)
    out = [m for m in sent() if m["template_id"].startswith("triage_outcome")]
    if template is None:
        assert out == []
        return
    assert len(out) == 1 and out[0]["template_id"] == template and out[0]["recipient"] == "patient"
    m = out[0]
    if template == "triage_outcome_amber":  # Amber workflow v2: the duty doctor, with the emergency line
        assert m["body_en"].startswith(says) and "chest pain, trouble breathing" in m["body_en"]
        assert "Go to the nearest emergency centre now." in m["body_en"]
    elif template == "triage_outcome_red":  # triage v3: no staff call is promised
        assert m["body_en"].endswith("Do not wait. " + says) and "will call" not in m["body_en"]
    else:
        assert "handed to a Hospital Care Coordinator" in m["body_en"] and says in m["body_en"]
        assert "go to the nearest hospital" in m["body_en"]
    assert m["language"] == "Tamil" and m["body"] != m["body_en"]
    assert m["triage_episode_id"] == s["episode_id"]
    note = next(x for x in thread(client) if x["direction"] == "in" and x["triage_episode_id"] == s["episode_id"])
    assert note["ep_tier"] == s["tier"] and note["ep_ticket"] == s["ticket_id"]


def test_d10_hybrid_shopping_and_tired_hands_aching_is_triaged_not_the_standard_reply(vt, client):
    r = text_note(client, "I thought about going shopping today, but I'm feeling tired and my hands are also aching")
    assert any(h.startswith("triage_call_due") for h in r["handling"])
    assert not [m for m in sent() if m["template_id"] == "triage_not_health"]
    assert "tiredness" in episode(client)["plan"]["modules"]


def test_d6_d8_taps_name_the_order_and_a_closed_episode_is_cleared_from_the_chat(vt, client):
    voice_note(client)
    eid = amber(client)
    doctor_call(client, eid, "PICKED_UP")
    client.post(f"/triage/episodes/{eid}/order", headers=ANANYA, json={"order": "physio"})
    booking = next(m for m in thread(client) if m["template_id"] == "instruction_booking")
    assert booking["order_name"] == "Physiotherapy referral" and booking["triage_episode_id"] == eid
    r = client.post(f"/simulator/{MRN}/reply", headers=ANITA, json={"button": "booked", "in_reply_to": booking["id"]})
    assert r.status_code == 201, r.text
    tap = next(m for m in thread(client) if m["direction"] == "in" and m["button"] == "booked")
    assert tap["order_name"] == "Physiotherapy referral" and tap["triage_episode_id"] == eid
    assert {m["ep_status"] for m in thread(client) if m["triage_episode_id"] == eid} == {"open"}
    close_amber(client, eid, rec="Physio booked")
    mine = [m for m in thread(client) if m["triage_episode_id"] == eid]
    # her voice note, the acknowledgement, the outcome, the booking message and her tap: all cleared together
    assert len(mine) == 5 and {m["ep_status"] for m in mine} == {"closed"}


def test_d12_the_patient_journey_shows_each_triage_episode_and_the_next_ticket_sees_the_last(vt, client):
    voice_note(client)
    eid = play(client, "amber")["episode_id"]
    client.post(f"/triage/episodes/{eid}/review", headers=ANITA, json={"action": "CONFIRM"})
    close_amber(client, eid, "none")
    t = client.get(f"/patients/{MRN}/timeline", headers=ANANYA).json()
    ev = next(e for e in t["events"] if e.get("triage"))
    assert ev["label"].startswith("Triage episode CP-") and "Amber" in ev["label"] and ev["label"].endswith("closed")
    details = ev["triage"]["details"]
    assert details[0].startswith("AI heard: headache, nausea, sleepy, arm pain")
    assert any(d.startswith("Why Amber:") for d in details) and details[-1].startswith("Closed by Dr. Ananya")
    assert "Confirmed by Anita" in details
    assert "Recommendation by Dr. Ananya (contacted by phone): “Reviewed on the phone”" in details
    assert "Dr. Ananya recommended after contacting her by phone: “Reviewed on the phone”." in details[-1]
    text_note(client, "I have a headache again")
    prev = episode(client)["previous"]
    assert prev["episode_id"] == eid and prev["details"] == details


# ---------------------------------------------------------------------------------------------------------------
# Testing round 1 Nov (Worksheet2): an empty message. Two automated calls; if nobody answers, hospital staff call
# her; reached, they close the ticket or escalate to the doctor; not reachable twice, the family gets the standard
# message with the PDF.
# ---------------------------------------------------------------------------------------------------------------
def empty_note(client, body=None) -> dict:
    r = client.post(f"/simulator/{MRN}/reply", headers=ANITA, json=body or {"empty": True})
    assert r.status_code == 201, r.text
    return r.json()


def unanswered(client) -> dict:
    eid = episode(client)["episode_id"]
    client.post(f"/triage/episodes/{eid}/no-answer", headers=ANITA)
    return client.post(f"/triage/episodes/{eid}/no-answer", headers=ANITA).json()


def reach(client, eid, outcome, next_=None, note=None, headers=ANITA):
    return client.post(f"/triage/episodes/{eid}/reach-call", headers=headers,
                       json={"outcome": outcome, "next": next_, "note": note})


@pytest.mark.parametrize("body", [{"empty": True}, {"text": "..."}, {"text": "🙏"}])
def test_an_empty_message_is_never_the_standard_reply_it_opens_a_ticket_with_the_fixed_12(vt, client, body):
    r = empty_note(client, body)
    assert r["handling"][0] == "empty_message" and r["triage_episode_id"]
    ep = episode(client)
    assert ep["empty_message"] and len(ep["plan"]["question_ids"]) == 12 and ep["call_status"] == "due"
    assert [m["template_id"] for m in sent()][-1] == "triage_ack"


def test_an_empty_message_needs_nothing_else_in_it(vt, client):
    r = client.post(f"/simulator/{MRN}/reply", headers=ANITA, json={"empty": True, "text": "hello"})
    assert r.status_code == 422


def test_empty_message_and_no_answer_brings_a_staff_call_not_the_family_message(vt, client):
    from conftest import scalar

    empty_note(client)
    s = unanswered(client)
    assert (s["tier"], s["red_kind"], s["result"]["action_code"]) == ("TIER_2_AMBER", None, "STAFF_CALL_15_MIN")
    assert s["reaching"] and s["callback_due_at"][11:16] == "10:32" and s["due"]["what"] == "Staff call: reach her"
    assert "Call her by 10:32" in s["next"]["coordinator"]["text"] and s["next"]["doctor"]["text"] == "Nothing"
    templates = [m["template_id"] for m in sent()]
    assert "triage_red_caregiver" not in templates and templates[-1] == "triage_outcome_unreached"
    assert "within 15 minutes" in sent()[-1]["body_en"] and "nearest hospital" in sent()[-1]["body_en"]
    assert scalar("SELECT count(*) FROM coordinator_task WHERE task_type = 'reach_patient' AND status = 'open'") == 1
    assert s["result"]["why"] == ["Her message had no words, and two automated calls were not answered, so hospital "
                                  "staff call her."]


def test_staff_reach_her_and_close_the_ticket(vt, client):
    from conftest import scalar

    empty_note(client)
    eid = unanswered(client)["episode_id"]
    assert reach(client, eid, "REACHED", "CLOSE").status_code == 422  # what she said is needed
    s = reach(client, eid, "REACHED", "CLOSE", "Sent by mistake; she is well").json()
    assert s["status"] == "closed" and s["close_note"] == "Staff call: Sent by mistake; she is well"
    assert scalar("SELECT status FROM coordinator_task WHERE task_type = 'reach_patient'") == "done"
    assert "triage_red_caregiver" not in [m["template_id"] for m in sent()]


def test_staff_reach_her_and_escalate_to_the_doctor(vt, client):
    empty_note(client)
    eid = unanswered(client)["episode_id"]
    s = reach(client, eid, "REACHED", "ESCALATE", "She has a fever since morning", headers=RAVI).json()
    assert not s["reaching"] and s["status"] == "open" and s["tier"] == "TIER_2_AMBER"
    assert s["callback_due_at"][11:16] == "12:17" and "call her by 12:17" in s["next"]["doctor"]["text"]
    assert s["coord_ack_by"] is None  # the duty doctor acknowledges
    assert doctor_call(client, eid, "PICKED_UP").status_code == 200  # the doctor still calls her
    s = client.post(f"/triage/episodes/{eid}/order", headers=ANANYA, json={"order": "bloods"}).json()
    assert s["orders"][0]["order"] == "bloods"


def test_not_reachable_twice_sends_her_family_the_standard_message_with_the_pdf(vt, client):
    empty_note(client)
    eid = unanswered(client)["episode_id"]
    s = reach(client, eid, "NOT_REACHABLE").json()
    assert s["reaching"] and s["callback_due_at"][11:16] == "10:32"  # again within 15 minutes of the call
    assert "triage_red_caregiver" not in [m["template_id"] for m in sent()]
    s = reach(client, eid, "NOT_REACHABLE").json()
    assert (s["tier"], s["red_kind"], s["reaching"]) == ("TIER_1_RED", "NO_ANSWER", False)
    assert s["result"]["reason_codes"] == ["RED_NOT_REACHED_AFTER_EMPTY_MESSAGE"]
    msg = [m for m in sent() if m["template_id"] == "triage_red_caregiver"][0]
    assert "a message with no words in it" in msg["body_en"] and msg["recipient"] == "caregiver"
    text = client.get(f"/triage/documents/{msg['attachment_doc_id']}", headers=ANITA).content.decode("latin-1")
    assert "Sent a message with no words" in text and "two calls by hospital staff were not answered" in text
    assert s["task"] is None  # triage v3: no family call; the Doctor on Duty closes


def test_the_staff_call_is_only_for_an_empty_message_nobody_answered(vt, client):
    voice_note(client)
    s = unanswered(client)  # a health message: Red at once, as before
    assert (s["tier"], s["red_kind"]) == ("TIER_1_RED", "NO_ANSWER")
    assert reach(client, s["episode_id"], "NOT_REACHABLE").status_code == 409
    assert reach(client, s["episode_id"], "NOT_REACHABLE", headers=ANANYA).status_code == 403


# ---------------------------------------------------------------------------------------------------------------
# Testing round 2 Nov: "pain in my left hand" was tagged other_symptom, so the arm questions were never asked.
# ---------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("words", [
    "இடது கையில வலியும் இருக்கு", "also pain in my left hand", "my hands are also aching", "मेरे हाथ में दर्द है",
    "నా చేయి నొప్పిగా ఉంది"])
def test_pain_in_a_hand_or_arm_always_adds_the_arm_questions(words):
    raw = {"health_related": "YES", "symptoms": ["sleepiness", "other_symptom"], "severity_word": "NONE"}
    tags = TR.arm_from_words(raw, words)
    assert tags["symptoms"] == ["sleepiness", "other_symptom", "arm_pain"]  # nothing is removed
    p = TR.plan(LAKSHMI, tags)
    assert "arm_pain" in p["modules"] and {"Q_ARM_SCORE", "Q_ARM_SUDDEN"} <= set(p["question_ids"])
    assert TR.arm_from_words(raw, "I have a headache") == raw
    assert "arm_pain" in TR.TAG_PROMPT and "கை" in TR.TAG_PROMPT and TR.TAG_PROMPT_VERSION == "triage-tags-v2"


def test_lakshmis_left_hand_pain_is_arm_pain_and_green_on_calm_answers(vt, client):
    text_note(client, "I feel sleepy, I have a headache and pain in my left hand")
    ep = episode(client)
    assert "arm_pain" in ep["plan"]["modules"] and ep["plan"]["floors"] == []
    assert "arm pain" in ep["plan"]["heard"] and "arm pain" not in ep["plan"]["not_asked"]
    s = play(client, "green", ep["episode_id"])
    asked = [q["id"] for q in s["table"] if q["asked"]]
    assert {"Q_ARM_SCORE", "Q_ARM_SUDDEN"} <= set(asked) and s["tier"] == "TIER_3_GREEN"


# ---------------------------------------------------------------------------------------------------------------
# Testing round 2 Nov: "Pending with" (persona and name) on every open ticket, and the triage outcome PDF of any
# episode, opened from the Patient journey.
# ---------------------------------------------------------------------------------------------------------------
def pend(s):
    return [(p["persona"], p["name"], p["user_id"]) for p in s["pending"]]


def test_pending_with_names_the_persona_and_the_person_at_every_stage(vt, client):
    voice_note(client)
    s = episode(client)
    assert pend(s) == [("CarePilot AI", "automated assistant", "u_anita")]  # the coordinator runs the call
    eid = play(client, "amber", s["episode_id"])["episode_id"]
    s = episode(client)
    assert pend(s) == [("Care coordinator", "Anita", "u_anita")] and "Review" in s["pending"][0]["what"]
    client.post(f"/triage/episodes/{eid}/review", headers=ANITA, json={"action": "CONFIRM"})
    s = episode(client)
    assert pend(s) == [("Doctor on Duty", "Dr. Ananya", "u_ananya")] and "call her by 12:12" in s["pending"][0]["what"]
    at(client, "12:12")
    assert pend(episode(client)) == [("Senior Oncologist (backup)", "Dr. Sekhar", "u_sekhar"),
                                     ("Care coordinator", "Anita", "u_anita")]
    s = close_amber(client, eid, "none")
    assert s["pending"] == []


def test_pending_with_on_red_and_green(vt, client):
    voice_note(client)
    s = play(client, "red")
    assert pend(s) == [("Care coordinator", "Anita", "u_anita")]  # the review first
    assert pend(confirm(client, s["episode_id"])) == [("Doctor on Duty", "Dr. Ananya", "u_ananya")]
    s = play(client, "green", s["episode_id"])
    assert pend(s) == [("Care coordinator", "Anita", "u_anita")]
    assert s["pending"][0]["what"].startswith("Review and close by")


@pytest.mark.parametrize("scenario,tier", [("green", "Green"), ("amber", "Amber"), ("red", "Red")])
def test_every_episode_has_a_triage_outcome_pdf_in_the_patient_journey(vt, client, scenario, tier):
    voice_note(client)
    eid = play(client, scenario)["episode_id"]
    r = client.get(f"/triage/episodes/{eid}/outcome.pdf", headers=ANANYA)
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF")
    text = r.content.decode("latin-1")
    assert f"Triage outcome - CP-20261027-0001 - {tier}" in text and "Staff only" in text
    assert "What happened" in text and "Lakshmi" in text
    assert client.get(f"/triage/episodes/{eid}/outcome.pdf", headers=users_header("u_suresh")).status_code == 403
    ev = next(e for e in client.get(f"/patients/{MRN}/timeline", headers=ANANYA).json()["events"] if e.get("triage"))
    t = ev["triage"]
    assert t["episode_id"] == eid and t["ticket_id"] == "CP-20261027-0001" and t["pending"]
    assert (t["emergency_pdf"] is not None) == (scenario == "red")


# ---------------------------------------------------------------------------------------------------------------
# Triage v3 (2 Nov): the coordinator's review (the gate before the doctor, no time limit) and the closing summary
# ---------------------------------------------------------------------------------------------------------------
def test_the_coordinators_review_is_the_gate_before_the_doctor(vt, client):
    voice_note(client)
    eid = play(client, "amber")["episode_id"]
    # The gate: the doctor's steps wait for the review (no time limit)
    for path, body in (("acknowledge", None), ("doctor-call", {"outcome": "PICKED_UP"}),
                       ("tier", {"tier": "TIER_3_GREEN", "reason": "x"}),
                       ("summary-draft", {}), ("close", {"summary": "s", "recommendation": "r", "contact": "PHONE"})):
        r = client.post(f"/triage/episodes/{eid}/{path}", headers=ANANYA, json=body)
        assert r.status_code == 409 and r.json()["detail"] == "Waiting for the coordinator's review (Confirm, Raise tier or Flag as wrong)", path
    review = lambda body, who=ANITA: client.post(f"/triage/episodes/{eid}/review", headers=who, json=body)  # noqa: E731
    assert review({"action": "CONFIRM"}, ANANYA).status_code == 403
    assert review({"action": "MAYBE"}).status_code == 422
    r = review({"action": "FLAG"})
    assert r.status_code == 422 and r.json()["detail"] == "Say why the AI's triage is wrong"
    s = review({"action": "FLAG", "reason": "Drowsy is her usual after the night shift"}).json()
    assert s["review"]["action"] == "FLAGGED" and s["tier"] == "TIER_2_AMBER" and s["status"] == "open"
    assert doctor_call(client, eid, "PICKED_UP").status_code == 200  # flagged: still to the doctor
    assert s["review_text"] == "Flagged as wrong by Anita: “Drowsy is her usual after the night shift”"
    assert s["next"]["coordinator"] == {"text": s["review_text"], "done": True, "due_at": None}
    assert [p["persona"] for p in s["pending"]] == ["Doctor on Duty"]  # with the doctor
    assert review({"action": "CONFIRM"}, RAVI).status_code == 409  # one review per ticket
    rows = client.get("/audit", headers=ANITA, params={"mrn": MRN, "limit": 100}).json()["entries"]
    assert next(x for x in rows if x["action"] == "triage_reviewed")["outcome"] == "FLAGGED"
    s = close_amber(client, eid, "bloods", rec="Blood tests this week; keep taking Shelcal-HD")
    assert "Flagged as wrong by Anita" in json.dumps(
        client.get(f"/patients/{MRN}/timeline", headers=ANANYA).json(), ensure_ascii=False)


def test_a_green_ticket_has_no_review(vt, client):
    voice_note(client)
    eid = play(client, "green")["episode_id"]
    r = client.post(f"/triage/episodes/{eid}/review", headers=ANITA, json={"action": "CONFIRM"})
    assert r.status_code == 409


def test_the_closing_summary_is_drafted_from_the_record_and_quotes_the_doctor(vt, client):
    voice_note(client)
    eid = amber(client)
    doctor_call(client, eid, "PICKED_UP")
    client.post(f"/triage/episodes/{eid}/order", headers=ANANYA, json={"order": "physio"})
    rec = "Arm ache from carrying water; physio this week and pause Neurobion Forte for 5 days"
    d = client.post(f"/triage/episodes/{eid}/summary-draft", headers=ANANYA,
                    json={"recommendation": rec, "contact": "PHONE"}).json()["draft"]
    one, two = d.split(". Dr. Ananya")
    assert one.startswith("Lakshmi reported headache, nausea, sleepy, arm pain; CarePilot asked 16 questions and found "
                          "Amber: ")
    assert two == f" recommended after contacting her by phone: “{rec}” (orders: physiotherapy referral)."
    for word in FORBIDDEN:
        assert word not in d.lower(), word
    s = client.post(f"/triage/episodes/{eid}/close", headers=ANANYA,
                    json={"summary": d, "recommendation": rec, "contact": "PHONE"}).json()
    assert s["close_note"] == d and s["closing"]["draft"] == d and s["closing"]["edited"] is False
    text = client.get(f"/triage/episodes/{eid}/outcome.pdf", headers=ANITA).content.decode("latin-1")
    assert "Closing summary" in text and "CarePilot asked 16 questions" in text


def test_raising_the_tier_is_the_review(vt, client):
    voice_note(client)
    eid = play(client, "green")["episode_id"]
    s = tier(client, eid, ANITA, "TIER_2_AMBER", "she sounded breathless").json()
    assert not s["awaits_review"] and doctor_call(client, eid, "PICKED_UP").status_code == 200


def test_a_staff_call_that_escalates_counts_as_the_review(vt, client):
    empty_note(client)
    eid = unanswered(client)["episode_id"]
    s = reach(client, eid, "REACHED", "ESCALATE", "She has a fever since morning").json()
    assert s["review"]["action"] == "ESCALATED" and not s["awaits_review"]
    assert s["review_text"] == "Escalated by Anita after her call: “She has a fever since morning”"
    assert doctor_call(client, eid, "PICKED_UP").status_code == 200
