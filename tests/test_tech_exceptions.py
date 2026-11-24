"""documents/TechExceptions.xlsx: a technical fault during the simulated call (UC1-V) never becomes a wrong next
step. Every fault ends in a safety stop with a person in the loop (handover, 2-hour callback, nothing booked); a
silent patient is the one exception (UNCLEAR: one re-ask, then a coordinator task). One test per row of the sheet,
plus the real Sarvam client's own handling of the raw responses (HTTP errors, empty bodies, timeouts)."""
import io
import json
import time
import urllib.error

import pytest
from test_usecase1v import ANITA, card, labels, say, spoken, sql, task_types, to_stage, uc1v  # noqa: F401

P3 = "अच्छा, ठीक है। बुक कर दीजिए।"   # clip P3: "Okay, fine. Please book it."
P4 = "हाँ, चलेगा।"                    # clip P4: "Yes, that works."
YES_BOOK = "हाँ, बुक कर दीजिए।"        # "Yes, please book it."


def model_says(monkeypatch, answer=None, raises=None):
    """The reply sorter's model (simulated provider) answers `answer`, or raises."""
    from app import ai

    def chat(self, system, user):
        if raises:
            raise raises
        return answer

    monkeypatch.setattr(ai.Fake, "chat", chat)


def assert_person_takes_over(call, reason: str, text_on_handover: str):
    """The row's 'Expected': a safety stop, nothing booked, a handover a person must close, and why it stopped."""
    assert call["outcome"] == "safety_stop"
    assert labels(call)[-1] == "HEALTH_CONCERN"
    assert spoken(call)[-1] == "L9"                      # the fixed safety line, never an AI sentence
    assert [t["reason_code"] for t in call["turns"] if t["who"] == "patient"][-1] == reason
    assert card()["state"] == "Due"                      # nothing booked despite the yes
    assert not sql("SELECT 1 FROM voice_slot WHERE taken_by_card = %s", card()["card_id"])
    assert not sql("SELECT 1 FROM appointment WHERE card_id = %s", card()["card_id"])
    h = sql("SELECT * FROM handover")[0]
    assert h["ai_failed"] is True and h["callback_due_at"] and h["closed_by"] is None and h["stage"] != "closed"
    assert text_on_handover in h["source"]
    assert "coordinator_review" in task_types()
    assert sql("SELECT 1 FROM audit_ledger WHERE action = 'handover_created' AND reason_code = 'ai_could_not_sort'")


# ---- row 2: Sarvam timeout at L2
def test_row2_timeout_at_l2_is_a_safety_stop_within_the_time_budget(uc1v, client, monkeypatch):
    from app import policy

    real = policy.settings

    def short(conn):  # the same rule with a short budget, so the test does not wait 16 seconds
        s = real(conn)
        return {**s, "voice_escalation": {**s["voice_escalation"], "sorter_budget_seconds": 1.2,
                                          "sorter_try_seconds": 0.4}}

    monkeypatch.setattr(policy, "settings", short)
    call = to_stage(client, "booking")
    from app import ai

    def never_answers(self, system, user):  # the API would answer only after 30 s: the client gives up first
        time.sleep(self.timeout)
        raise TimeoutError(f"no answer within {self.timeout:.1f} s")

    monkeypatch.setattr(ai.Fake, "chat", never_answers)
    started = time.monotonic()
    call = say(client, call, text=YES_BOOK)
    assert time.monotonic() - started < 3, "the patient must not wait beyond the budget"
    assert_person_takes_over(call, "ai_sorter_failed", "no answer in time")


# ---- row 3: HTTP 500 / 503 / revoked key 401 at L2
@pytest.mark.parametrize("status", [500, 503, 401])
def test_row3_server_error_or_revoked_key_is_a_safety_stop(uc1v, client, monkeypatch, status):
    from app import ai

    call = to_stage(client, "booking")
    model_says(monkeypatch, raises=ai.AIFailure(f"chat/completions: HTTP Error {status}: internal_server_error"))
    call = say(client, call, text="ठीक है, बुक कर दो।")
    assert_person_takes_over(call, "ai_sorter_failed", "no answer in time")


# ---- row 4: invalid label at L2 (four shapes)
@pytest.mark.parametrize("answer", ['{"label":"MAYBE"}', '{"label":"YES, HEALTH_CONCERN"}', "The patient wants to book",
                                    '{"label":"YES","severity":"mild"}', "MAYBE", "YES."])
def test_row4_anything_but_one_valid_label_is_a_safety_stop(uc1v, client, monkeypatch, answer):
    call = to_stage(client, "booking")
    model_says(monkeypatch, answer)
    call = say(client, call, text=P3)
    assert_person_takes_over(call, "ai_invalid_label", "not one valid label")


# ---- row 5: empty label at L5, the slot answer
@pytest.mark.parametrize("answer", ['{"label":""}', "", None, {}])
def test_row5_empty_label_at_the_slot_books_nothing(uc1v, client, monkeypatch, answer):
    call = to_stage(client, "slot")
    model_says(monkeypatch, answer)
    call = say(client, call, text=P4)
    assert_person_takes_over(call, "ai_sorter_failed" if answer is None else "ai_invalid_label",
                             "no answer in time" if answer is None else "not one valid label")


# ---- row 6: failed transcription at L2 (corrupt or 0-byte audio)
def test_row6_failed_transcription_hands_over_with_the_audio_and_says_so(uc1v, client, monkeypatch):
    from app import ai

    call = to_stage(client, "booking")

    def broken(self, audio, mime, language, mode, clip_id=None):
        raise ai.AIFailure('speech-to-text: HTTP Error 422: {"error":"audio_could_not_be_decoded"}')

    monkeypatch.setattr(ai.Fake, "speech", broken)
    call = say(client, call)  # the simulated patient's recorded clip, not typed text
    assert_person_takes_over(call, "ai_speech_failed", "transcription failed")
    turn = call["turns"][-2]
    assert turn["who"] == "patient" and turn["text"] in (None, "")
    # not a blank screen: the recording is there for the person to listen to
    assert client.get(f"/voice/calls/{call['call_id']}/turns/{turn['seq']}/audio", headers=ANITA).status_code == 200
    from app import db, voice_escalation

    with db.connect() as c:
        h = voice_escalation.safety_handover(c, sql("SELECT * FROM handover")[0])
    assert h["voice_audio"] is True


# ---- row 7: blank reply from the model (HTTP 200, empty body)
def test_row7_blank_model_reply_is_a_safety_stop(uc1v, client, monkeypatch):
    from app import ai

    call = to_stage(client, "booking")
    model_says(monkeypatch, raises=ai.AIFailure("empty model answer"))
    call = say(client, call, text=YES_BOOK)
    assert_person_takes_over(call, "ai_sorter_failed", "no answer in time")


# ---- row 8: blank reply from the patient (silence or only noise): UNCLEAR, never a stop
def test_row8_silence_is_unclear_reask_once_then_a_coordinator_task(uc1v, client):
    call = to_stage(client, "booking")
    call = say(client, call, silence=True)
    assert (labels(call)[-1], spoken(call)[-1], call["status"]) == ("UNCLEAR", "L3", "live")
    call = say(client, call, silence=True)
    assert (call["outcome"], spoken(call)[-1]) == ("unclear", "L7")
    assert "voice_unclear" in task_types()
    assert sql("SELECT count(*) AS n FROM handover")[0]["n"] == 0
    assert card()["state"] == "Due"


def test_row8_noise_only_audio_that_gives_an_empty_transcript_is_unclear(uc1v, client, monkeypatch):
    from app import ai

    call = to_stage(client, "booking")
    monkeypatch.setattr(ai.Fake, "speech", lambda self, *a, **k: "")
    call = say(client, call)
    assert (labels(call)[-1], spoken(call)[-1]) == ("UNCLEAR", "L3")


def test_translation_failure_is_a_safety_stop(uc1v, client, monkeypatch):
    from app import ai

    call = to_stage(client, "booking")
    monkeypatch.setattr(ai.Fake, "translate", lambda self, text, language: (_ for _ in ()).throw(ai.AIFailure("503")))
    call = say(client, call, text=YES_BOOK)
    assert_person_takes_over(call, "ai_translation_failed", "translation failed")


# ---- the real Sarvam client: each raw response of the sheet becomes AIFailure (never a crash, never a label)
class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _sarvam(monkeypatch, behaviour):
    from app import ai

    def urlopen(req, timeout=None):
        return behaviour(req, timeout)

    monkeypatch.setattr(ai.urllib.request, "urlopen", urlopen)
    return ai.Sarvam(3, "saaras:v3", "sarvam-105b")


def _http(status, body=b"{}"):
    def raise_it(req, timeout):
        raise urllib.error.HTTPError(req.full_url, status, "error", {}, io.BytesIO(body))
    return raise_it


@pytest.mark.parametrize("name,behaviour", [
    ("timeout", lambda req, timeout: (_ for _ in ()).throw(TimeoutError("timed out"))),
    ("500", _http(500, b'{"error":"internal_server_error"}')),
    ("503", _http(503)),
    ("401 revoked key", _http(401)),
    ("200 empty body", lambda req, timeout: _Resp(b"")),
    ("200 no choices", lambda req, timeout: _Resp(b"{}")),
    ("200 empty content", lambda req, timeout: _Resp(json.dumps({"choices": [{"message": {"content": ""}}]}).encode())),
    ("200 not json", lambda req, timeout: _Resp(b"<html>gateway</html>")),
])
def test_sarvam_client_turns_every_raw_fault_into_ai_failure(monkeypatch, name, behaviour):
    from app import ai

    sarvam = _sarvam(monkeypatch, behaviour)
    with pytest.raises(ai.AIFailure):
        sarvam.chat("system", "user")


@pytest.mark.parametrize("behaviour", [_http(422, b'{"error":"audio_could_not_be_decoded"}'),
                                       lambda req, timeout: _Resp(b'{"no_transcript": 1}')])
def test_sarvam_speech_to_text_fault_is_ai_failure(monkeypatch, behaviour):
    from app import ai

    with pytest.raises(ai.AIFailure):
        _sarvam(monkeypatch, behaviour).speech(b"", "audio/wav", "Hindi", "transcribe")


@pytest.mark.parametrize("answer", [None, {}, [], 7, '{"label":"YES"}', "yes", " HEALTH_CONCERN\nYES"])
def test_sorter_accepts_only_one_exact_label(answer):
    from app import voice_sorter

    assert voice_sorter.valid_label(answer) is None


# ---- negation (pre-demo pack false alarms N18, N28, N29): a plainly denied symptom goes on to the model,
# anything else with a health word still stops at once
@pytest.mark.parametrize("hindi,english", [
    ("मुझे कोई दर्द या तकलीफ़ नहीं है, बुक कर दीजिए।", "I'm not feeling pain or discomfort, so please book it."),
    ("सब ठीक है, बुखार भी नहीं है, आप बुक कर दीजिए।", "Everything is fine, I don't have a fever, please book it."),
    ("मैं बिल्कुल ठीक हूँ, कोई तकलीफ़ नहीं है।", "I am perfectly fine, and I'm not feeling unwell."),
    ("थकान नहीं है, बुक कर दीजिए।", "I have no tiredness, book it."),
])
def test_a_plainly_denied_symptom_is_not_a_word_list_stop(hindi, english):
    from app import voice_sorter

    assert voice_sorter.health_word(hindi, english) is False


@pytest.mark.parametrize("hindi,english", [
    ("दर्द कम नहीं हो रहा है।", "The pain is not reducing."),
    ("बुखार नहीं उतर रहा, बुक कर दीजिए।", "The fever is not coming down, book it."),
    ("दवा नहीं ले पा रही हूँ।", "I am not able to take the medicine."),
    ("सूजन अभी भी नहीं गई।", "The swelling has still not gone."),
    ("बुखार नहीं है, पर दर्द बहुत है।", "No fever, but a lot of pain."),
    ("बुखार तो नहीं है, पर बहुत थकान है।", "There is no fever, but I am very tired."),
    ("कोई दर्द नहीं, लेकिन तीन दिन से ख़ून आ रहा है।", "No pain, but bleeding for three days."),
    ("हाँ बुक कर दीजिए, पर ऑपरेशन वाली जगह पर दर्द हो रहा है।", "Yes, book it, but there is pain at the site."),
    ("दर्द", None),
])
def test_a_health_word_that_is_not_plainly_denied_still_stops(hindi, english):
    from app import voice_sorter

    assert voice_sorter.health_word(hindi, english) is True
