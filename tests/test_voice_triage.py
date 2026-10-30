"""Voice Triage MVP-lite (CarePilot_Voice_Triage_MVP_Lite_v1.1): the rules, the call, the Red package and PDF, the
Amber order, the Green coaching, the follow-up, closure and the Pre-OPD brief. Synthetic data only."""
import json
from datetime import date
from pathlib import Path

import pytest

from conftest import users_header

from app import triage_rules as R

ANITA, RAVI = users_header("u_anita"), users_header("u_ravi")
ANANYA, SEKHAR = users_header("u_ananya"), users_header("u_sekhar")
NURSE, ER_DOCTOR = users_header("u_er_nurse"), users_header("u_er_doctor")
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


def run_script(scenario: str) -> tuple[dict, dict]:
    """The scripted Tamil answers through the parser, as the call does."""
    a, script = R.blank(), R.SCENARIOS[scenario]
    q = R.next_question(LAKSHMI, a, None)
    while q is not None:
        said = script.get(q.id)
        value = R.check(q.kind, R.parse(q.kind, said))
        R.store(a, q, value)
        if R.red_codes(a):
            break
        q = R.next_question(LAKSHMI, a, q.id)
    return a, R.triage(a, LAKSHMI, TODAY)


def test_amber_hero_scene_gives_the_guides_four_reasons():
    a, r = run_script("amber")
    assert r["tier"] == "TIER_2_AMBER" and r["action_code"] == "DOCTOR_CALLBACK_2H" and r["response_minutes"] == 120
    assert r["reason_codes"] == ["AMBER_BANGLES_TIGHTER_THAN_BASELINE", "AMBER_HEADACHE_RISE_2_PLUS",
                                 "AMBER_HEADACHE_WORSE_THAN_LAST_TIME", "AMBER_NAUSEA_DESPITE_MEALS"]
    assert (r["protocol_profile"], r["protocol_status"]) == ("DEMO_LITE", "DRAFT_UNSIGNED")
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


def test_red_stops_at_question_two():
    a, r = run_script("red")
    assert (r["tier"], r["red_kind"], r["reason_codes"]) == ("TIER_1_RED", "CONFIRMED", ["RED_ARM_RED_OR_HOT"])
    assert a["thermometer_used"] == R.NOT_ASKED and a["headache_present"] == R.NOT_ASKED


@pytest.mark.parametrize("temp,tier", [(100.3, "TIER_3_GREEN"), (100.4, "TIER_1_RED"), (38.0, "TIER_1_RED"),
                                       (37.9, "TIER_3_GREEN")])
def test_ec_d_fever_threshold(temp, tier):
    assert R.triage(answers(temperature_value=temp), LAKSHMI, TODAY)["tier"] == tier


def test_unknown_on_a_danger_question_is_red_unconfirmed_and_elsewhere_amber():
    r = R.triage(answers(arm_red_or_hot=R.UNKNOWN), LAKSHMI, TODAY)
    assert (r["tier"], r["red_kind"], r["action_code"]) == ("TIER_1_RED", "UNCONFIRMED", "NURSE_CALLBACK_15_MIN")
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
def voice_note(client, mrn=MRN, headers=ANITA) -> dict:
    r = client.post(f"/simulator/{mrn}/reply", headers=headers, json={"clip_id": "TA-01"})
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


def sent(mrn=MRN) -> list[dict]:
    from conftest import outbox

    return [m for m in outbox(mrn) if m["status"] == "sent"]


def test_vt_reset_is_lakshmi_with_her_history(vt, client):
    assert (vt["patients"], vt["consents"], vt["clock"].isoformat()) == (1, 1, "2026-10-27")
    assert client.get("/clock", headers=ANITA).json()["time"] == "10:12"
    users = {u["id"]: u["role"] for u in client.get("/users").json()}
    assert users == {"u_anita": "coordinator", "u_ravi": "coordinator", "u_ananya": "oncologist",
                     "u_sekhar": "oncologist", "u_er_nurse": "er_desk", "u_er_doctor": "er_desk",
                     "u_suresh": "chief"}
    assert client.get("/demo/settings", headers=NURSE).json()["voice_triage"]["enabled"] is True


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
    assert s["current"]["id"] == "q1" and s["turns"][1]["template_id"] == "TPL_CALL_OPEN_LITE_V1"
    assert "லட்சுமி அம்மாவுக்காக" in s["turns"][1]["ta"]
    # An unclear answer is asked once more; then the on-screen buttons and Tamil words.
    s = client.post(f"/triage/episodes/{eid}/answer", headers=ANITA, json={"text": "ம்ம்"}).json()
    assert s["current"]["id"] == "q1" and s["turns"][-1].get("repeat") is True
    s = client.post(f"/triage/episodes/{eid}/answer", headers=ANITA, json={"value": "NO"}).json()
    assert s["current"]["id"] == "q2"
    s = play(client, "amber", eid)
    assert (s["tier"], s["call_status"]) == ("TIER_2_AMBER", "done")
    q5b = next(q for q in s["table"] if q["id"] == "q5b")
    assert q5b["history"] and "அக்டோபர் 19" in q5b["ta"] and q5b["value"] == "YES"
    assert s["turns"][-2]["template_id"] == "TPL_CALL_AMBER_V1"
    assert s["callback_due_at"][11:16] == "12:12" and s["followup_due_on"] == "2026-10-28"
    assert len(s["result"]["why"]) == 4
    # Only the doctor orders; a button creates the card and sends the approved booking message.
    assert client.post(f"/triage/episodes/{eid}/order", headers=ANITA, json={"order": "physio"}).status_code == 403
    r = client.post(f"/triage/episodes/{eid}/order", headers=ANANYA,
                    json={"order": "physio", "note": "pause Neurobion Forte for 5 days"})
    assert r.status_code == 200, r.text
    card = client.get(f"/patients/{MRN}/cards", headers=ANITA).json()
    card = card["cards"] if isinstance(card, dict) else card
    physio = [c for c in card if c["name"] == "Physiotherapy referral"]
    assert len(physio) == 1 and physio[0]["state"] == "Due" and physio[0]["department"] == "Physiotherapy"
    booking = [m for m in sent() if m["template_id"] == "instruction_booking"]
    assert len(booking) == 1 and "பிசியோதெரபி பரிந்துரை" in booking[0]["body"]
    assert client.post(f"/triage/episodes/{eid}/order", headers=ANANYA, json={"order": "none"}).status_code == 409


def test_red_package_message_pdf_note_and_task(vt, client):
    voice_note(client)
    s = play(client, "red")
    assert (s["tier"], s["red_kind"], s["stopped_early"]) == ("TIER_1_RED", "CONFIRMED", True)
    assert [q["id"] for q in s["table"] if q["asked"]] == ["q1", "q2"]
    msg = [m for m in sent() if m["template_id"] == "triage_red_caregiver"]
    assert len(msg) == 1 and msg[0]["recipient"] == "caregiver" and msg[0]["to_phone"] == "+919000100142"
    assert msg[0]["body_en"].startswith("This is CarePilot's automated assistant. Lakshmi Amma gave answers to our "
                                        "call that are warning signs. Please take her to the nearest emergency")
    assert "லட்சுமி அம்மா எங்கள் அழைப்பில்" in msg[0]["body"]
    pdf = client.get(f"/triage/documents/{msg[0]['attachment_doc_id']}", headers=NURSE)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    text = pdf.content.decode("latin-1")
    for must in ("EMERGENCY SUMMARY FOR THE TREATING DOCTOR", "SYNTHETIC DEMO", "Left arm reported as red or hot",
                 "Asked and answered No: speech slurred or confusion", "Shelcal-HD", "Note from her treating oncologist",
                 "It does not diagnose or advise"):
        assert must in text, must
    for never in ("ambulance", "maps.", "risk score", "grade", "recommend"):
        assert never not in text.lower(), never
    assert s["doctor_note"].startswith("RED alert: Mrs. Lakshmi V. (SYN-0142). Why: Left arm reported as red or "
                                       "hot to touch. At 27 Oct 2026 10:12 the family (Karthik, her son) was sent")
    assert s["task"]["due_at_demo"][11:16] == "10:42" and s["task"]["reason"].startswith("Call Karthik within 30")
    # The coordinator's call does not close the case; the nurse cannot close it; only the ER doctor can.
    eid = s["episode_id"]
    r = client.post(f"/triage/episodes/{eid}/ed-call", headers=ANITA,
                    json={"outcome": "REACHED", "hospital": "City General ED"}).json()
    assert r["status"] == "open" and r["task"]["status"] == "done"
    assert client.post(f"/triage/episodes/{eid}/er-close", headers=NURSE, json={"note": "seen"}).status_code == 409
    assert client.post(f"/triage/episodes/{eid}/confirm-closure", headers=ANANYA,
                       json={"note": "x"}).status_code == 409
    assert client.post(f"/triage/episodes/{eid}/acknowledge", headers=NURSE).status_code == 200
    r = client.post(f"/triage/episodes/{eid}/er-close", headers=ER_DOCTOR, json={"note": "Seen in ED, admitted"})
    assert r.status_code == 200 and r.json()["status"] == "closed"


def test_ed_call_not_yet_twice_tells_the_doctor(vt, client):
    voice_note(client)
    eid = play(client, "fever")["episode_id"]
    s = client.post(f"/triage/episodes/{eid}/ed-call", headers=ANITA, json={"outcome": "NOT_YET"}).json()
    assert s["task"]["status"] == "open" and s["task"]["due_at_demo"][11:16] == "10:27" and not s["doctor_told_at"]
    s = client.post(f"/triage/episodes/{eid}/ed-call", headers=ANITA, json={"outcome": "NOT_YET"}).json()
    assert s["task"]["status"] == "done" and s["doctor_told_at"]
    assert client.post(f"/triage/episodes/{eid}/ed-call", headers=ANITA,
                       json={"outcome": "REACHED"}).status_code in (409, 422)


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


def test_unconfirmed_red_waits_for_a_nurse_then_a_person_sends_the_package(vt, client):
    voice_note(client)
    s = play(client, "unclear_arm")
    eid = s["episode_id"]
    assert (s["tier"], s["red_kind"]) == ("TIER_1_RED", "UNCONFIRMED")
    assert s["result"]["reason_codes"] == ["RED_CANNOT_CONFIRM_SAFE"] and s["package_sent_at"] is None
    assert not [m for m in sent() if m["template_id"] == "triage_red_caregiver"]
    assert s["callback_due_at"][11:16] == "10:27"
    assert client.post(f"/triage/episodes/{eid}/nurse-callback", headers=NURSE,
                       json={"note": "No answer on two calls"}).status_code == 200
    r = client.post(f"/triage/episodes/{eid}/send-package", headers=NURSE)
    assert r.status_code == 200 and r.json()["package_sent_by"] == "u_er_nurse"
    msg = [m for m in sent() if m["template_id"] == "triage_red_caregiver"]
    assert len(msg) == 1 and "could not be confirmed as safe" in msg[0]["body_en"]
    assert client.post(f"/triage/episodes/{eid}/send-package", headers=ANITA).status_code == 409


def test_keypad_zero_asks_for_a_nurse(vt, client):
    voice_note(client)
    s = play(client, "nurse")
    assert (s["tier"], s["result"]["action_code"]) == ("TIER_2_AMBER", "NURSE_CALLBACK_15_MIN")
    assert s["result"]["reason_codes"] == ["AMBER_NURSE_REQUESTED"]


def test_sarvam_off_mid_call_fails_closed_to_red(vt, client):
    voice_note(client)
    eid = episode(client)["episode_id"]
    client.post(f"/triage/episodes/{eid}/pickup", headers=ANITA)
    client.post("/demo/sarvam", headers=ANITA, json={"enabled": False})
    s = client.post(f"/triage/episodes/{eid}/answer", headers=ANITA, json={"text": "ம்ம் சரி"}).json()
    assert s["tier"] == "TIER_1_RED" and "RED_SYSTEM_FAULT" in s["result"]["reason_codes"]
    assert s["red_kind"] == "UNCONFIRMED" and s["package_sent_at"] is None


def test_green_follow_up_better_closure_and_the_pre_opd_brief(vt, client):
    voice_note(client)
    s = play(client, "green")
    eid = s["episode_id"]
    assert s["tier"] == "TIER_3_GREEN" and LAKSHMI["advice"]["ta"] in s["turns"][-2]["ta"]
    assert client.post(f"/triage/episodes/{eid}/followup", headers=ANITA, json={"answer": "BETTER"}).status_code == 409
    client.post("/clock/advance", headers=ANITA, json={"days": 1})
    s = client.post(f"/triage/episodes/{eid}/followup", headers=ANITA, json={"text": "நன்றாக இருக்கிறேன்"}).json()
    assert s["followup"]["answer"] == "BETTER"
    assert client.post(f"/triage/episodes/{eid}/tier", headers=ANANYA,
                       json={"tier": "TIER_2_AMBER"}).status_code == 422
    assert client.post(f"/triage/episodes/{eid}/confirm-closure", headers=ANANYA, json={}).json()["status"] == "closed"
    # Routine messages resume after closure; the brief shows the record and the episode, with sources.
    b = client.get(f"/triage/brief/{MRN}", headers=SEKHAR).json()
    kinds = [line["kind"] for line in b["lines"]]
    assert kinds[:2] == ["record", "record"] and "message" in kinds and "result" in kinds
    voice = next(line for line in b["lines"] if line["kind"] == "message")
    assert voice["sources"][0]["kind"] == "audio"
    assert b["lines"][-1]["text"].startswith("Closed by Dr. Ananya")


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


def test_er_desk_cannot_use_the_coordinator_screens(vt, client):
    assert client.get("/worklist", headers=NURSE).status_code == 403
    assert client.get("/triage/episodes", headers=NURSE).status_code == 200
    assert client.get(f"/triage/brief/{MRN}", headers=NURSE).status_code == 403


@pytest.mark.demo_template
def test_golden_path_health_message_goes_to_voice_triage(monkeypatch, seeded, client):
    from app import db, reference, seed

    monkeypatch.delenv("CAREPILOT_SEED_DIR")
    real = json.loads((reference.SEED_DIR / "settings.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(seed, "step_a_settings", lambda: real)
    with db.connect() as c:
        r = seed.reset(c, scenario="step_a")
    assert r["patients"] == 3 and r["users"] == 7
    priya = users_header("u_priya")
    out = voice_note(client, "SYN-0011", priya)
    assert out["triage_episode_id"] and out["handover_id"] is None
    eid = out["triage_episode_id"]
    s = client.post(f"/triage/episodes/{eid}/play", headers=priya, json={"scenario": "red"}).json()
    assert s["tier"] == "TIER_1_RED" and "சாவித்ரி அம்மாவுக்காக" in s["turns"][1]["ta"]
    msg = [m for m in sent("SYN-0011") if m["template_id"] == "triage_red_caregiver"]
    assert len(msg) == 1 and msg[0]["to_phone"] == "+919000100011"
    q5b = R.question({**LAKSHMI, **{"last_headache": {"date": "2026-10-26", "score": 3}}}, "q5b")
    assert "அக்டோபர் 26" in q5b.ta
    # Lakshmi V. is the third Golden Path patient: the guide's scenarios, with the Golden Path's doctors.
    out = voice_note(client, "SYN-0142", priya)
    assert out["triage_episode_id"] and out["handover_id"] is None
    s = client.post(f"/triage/episodes/{out['triage_episode_id']}/play", headers=priya, json={"scenario": "amber"}).json()
    assert s["tier"] == "TIER_2_AMBER" and set(s["result"]["reason_codes"]) == {
        "AMBER_BANGLES_TIGHTER_THAN_BASELINE", "AMBER_HEADACHE_RISE_2_PLUS", "AMBER_HEADACHE_WORSE_THAN_LAST_TIME",
        "AMBER_NAUSEA_DESPITE_MEALS"}
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
    assert client.get(f"/triage/episodes/{eid}/recording", headers=NURSE).status_code == 200
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
