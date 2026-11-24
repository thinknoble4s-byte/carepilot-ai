"""What decides Red, Amber and Green, for the Care Plan screen's triage tabs, and how the simulated voice call
(UC1-V) sorts a reply. Read only: nothing here decides anything. The tier itself is decided only by
triage_rules.triage(); every "what this answer does" line below carries a probe (the answer on a calm call) that
tests/test_triage_criteria.py runs through triage() to prove the screen says what the code does."""
from . import reference, triage_router as TR, triage_rules as R, voice_sorter as VS

Y, N, U = R.YES, R.NO, R.UNKNOWN

# question -> [(tier, what the answer does, probe)]; probe: the answer(s) on an otherwise calm call, or None when
# the effect needs her history (recurrence) and is covered by the rule tests instead.
EFFECTS = {
    "Q_SLURRED": [("red", "Yes → Red", {"slurred_or_confused": Y})],
    "Q_AWAKE": [("red", "Yes → Red", {"cannot_stay_awake": Y})],
    "Q_CHEST": [("red", "Yes → Red", {"chest_pain": Y})],
    "Q_ARM_RED": [("red", "Yes → Red", {"arm_red_or_hot": Y})],
    "Q_TEMP": [("red", "100.4 °F (38.0 °C) or more → Red", {"temperature_value": 100.4}),
               ("red", "38.0 °C said in Celsius → Red (a reading under 90 is taken as Celsius)", {"temperature_value": 38.0}),
               ("green", "Under 100.4 °F → no rule fires", {"temperature_value": 100.3}),
               ("amber", "No thermometer and she feels hot → Amber", {"thermometer_used": N, "feels_hot": Y}),
               ("amber", "No thermometer with headache, nausea, sleepiness or any vomiting → Amber",
                {"thermometer_used": N, "headache_present": Y})],
    "Q_BANGLES": [("amber", "Yes → Amber", {"bangles_tighter": Y})],
    "Q_DIZZY": [("red", "Yes → Red", {"unsteady_or_dizzy_walking": Y})],
    "Q_VISION": [("red", "Yes → Red", {"blurred_vision": Y})],
    "Q_CHILLS": [("red", "Yes → Red", {"shaking_chills": Y})],
    "Q_FEELS_HOT": [("amber", "Yes, with no thermometer → Amber", {"thermometer_used": N, "feels_hot": Y}),
                    ("green", "Yes, with a normal reading → no rule fires", {"feels_hot": Y})],
    "Q_HEADACHE": [("amber", "Yes, and it is her 3rd headache report in 14 days → Amber", None),
                   ("green", "Yes, nothing else → no rule fires (the next two questions are asked)", {"headache_present": Y})],
    "Q_HEAD_SCORE": [("red", "7 or more out of 10 → Red", {"headache_present": Y, "headache_score": 7}),
                     ("amber", "2 or more above her last recorded score → Amber", {"headache_present": Y, "headache_score": "LAST+2"}),
                     ("green", "Less than 2 above her last score, and under 7 → no rule fires",
                      {"headache_present": Y, "headache_score": "LAST+1"})],
    "Q_HEAD_WORSE": [("amber", "Yes → Amber", {"headache_present": Y, "headache_worse_than_last_time": Y})],
    "Q_NAUSEA": [("amber", "Yes, although she took the supplements after full meals → Amber",
                  {"nausea_since_yesterday": Y, "took_supplements_after_full_meals": Y}),
                 ("amber", "Yes, and it is her 3rd nausea report in 14 days → Amber", None),
                 ("green", "Yes with nothing else → Green, and the doctor's own earlier advice is repeated",
                  {"nausea_since_yesterday": Y})],
    "Q_VOMIT": [("red", "6 or more times since yesterday → Red", {"vomiting_count": 6}),
                ("amber", "2 to 5 times → Amber", {"vomiting_count": 2}),
                ("amber", "5 times → still Amber", {"vomiting_count": 5}),
                ("green", "Once → no rule fires", {"vomiting_count": 1})],
    "Q_LIQUIDS": [("red", "No, she cannot keep water down → Red", {"can_keep_liquids_down": N})],
    "Q_SUPP": [("amber", "Yes (with nausea) → Amber", {"nausea_since_yesterday": Y, "took_supplements_after_full_meals": Y})],
    "Q_DROWSY": [("amber", "Yes → Amber", {"drowsy_most_of_today": Y})],
    "Q_ARM_SCORE": [("amber", "7 or more out of 10 → Amber (never Red on its own)", {"arm_pain_score": 7}),
                    ("green", "6 or less → no rule fires", {"arm_pain_score": 6}),
                    ("amber", "Her 3rd arm-pain report in 14 days → Amber", None)],
    "Q_ARM_SUDDEN": [("amber", "Yes → Amber", {"arm_pain_sudden": Y})],
    "Q_SWOLLEN": [("amber", "Yes → Amber", {"arm_swollen": Y})],
    "Q_SKIN": [("amber", "Yes → Amber", {"skin_break_left_arm": Y})],
    "Q_TIRED_DAYS": [("amber", "7 days or more → Amber", {"tired_days": 7}),
                     ("green", "6 days or less → no rule fires", {"tired_days": 6})],
    "Q_TIRED_WORSE": [("amber", "Yes → Amber", {"tired_getting_worse": Y})],
}

AMBER_PLAIN = {
    "AMBER_BANGLES_TIGHTER_THAN_BASELINE": "Bangles or sleeve tighter on the left arm than her recorded baseline",
    "AMBER_HEADACHE_RISE_2_PLUS": "Headache score 2 or more above her last recorded score",
    "AMBER_HEADACHE_WORSE_THAN_LAST_TIME": "She says the headache is worse than last time",
    "AMBER_NAUSEA_DESPITE_MEALS": "Nausea although she took her supplements only after full meals",
    "AMBER_DROWSY_MOST_OF_TODAY": "Sleepy or groggy for most of today",
    "AMBER_VOMITING_2_TO_5": "Vomited 2 to 5 times since yesterday",
    "AMBER_ARM_SWOLLEN": "Left arm looks swollen or feels heavy",
    "AMBER_SKIN_BREAK_LEFT_ARM": "A cut, bite or prick on the left arm",
    "AMBER_FEELS_HOT_NO_THERMOMETER": "No thermometer, and she feels hot or feverish",
    "AMBER_NO_THERMOMETER_WITH_SYMPTOMS": "No thermometer, with headache, nausea, sleepiness or vomiting",
    "AMBER_ARM_PAIN_7_PLUS": "Left arm pain 7 out of 10 or more",
    "AMBER_ARM_PAIN_SUDDEN_ONSET": "Arm pain started suddenly",
    "AMBER_TIREDNESS_7_DAYS_PLUS": "Tired for 7 days or more",
    "AMBER_TIREDNESS_GETTING_WORSE": "Tiredness worse than last week",
    "AMBER_RECURRING_3X_14D_HEADACHE": "Third headache report in 14 days",
    "AMBER_RECURRING_3X_14D_NAUSEA": "Third nausea report in 14 days",
    "AMBER_RECURRING_3X_14D_ARM_PAIN": "Third arm-pain report in 14 days",
    "AMBER_CANNOT_CONFIRM": "An unclear answer to a question that is not a danger question",
    "AMBER_NURSE_REQUESTED": "She pressed 0 to ask for a doctor to call her back",
    "AMBER_PATIENT_SAYS_SEVERE": "Her own message called the problem “severe”",
    "AMBER_OTHER_SYMPTOM_REPORTED": "She named a symptom we have no questions for",
    "AMBER_EMPTY_MESSAGE_NOT_REACHED": "Her message had no words and two automated calls were not answered",
}

ACTIONS = {
    "red": [("Red, confirmed danger sign", "RED_PACKAGE_AND_DUTY_DOCTOR", "At once",
             "The call stops at the first danger answer. She is told to go to the emergency department; her family "
             "gets a message with the one-page PDF; a facts-only note goes to the Doctor on Duty and the Senior "
             "Oncologist."),
            ("Red, not confirmed (an unclear danger answer or a system fault)", "STAFF_CALLBACK_15_MIN", "15 minutes",
             "Hospital staff call her within 15 minutes."),
            ("Red, not reached (she said she was unwell, then 2 calls unanswered)", "RED_PACKAGE_NO_ANSWER", "At once",
             "Her family gets the standard message with the PDF.")],
    "amber": [("Amber (every Amber reason, pressing 0 included)", "DOCTOR_CALLBACK_2H", "2 hours",
               "The duty doctor calls her back within 2 hours. She hears the Amber closing line."),
              ("Amber, empty message and 2 calls unanswered", "STAFF_CALL_15_MIN", "15 minutes",
               "Hospital staff call her within 15 minutes.")],
    "green": [("Green (no rule fired)", "GREEN_COACH_AND_FOLLOWUP_24H", "24 hours",
               "Closing line; the doctor's own earlier advice is repeated if she reports nausea. A follow-up call the "
               "next morning, and the coordinator reviews the Green list.")],
}

NUMBERS = [("Fever", "100.4 °F (38.0 °C) or more → Red. A reading under 90 is taken as Celsius and converted."),
           ("Vomiting since yesterday", "6 or more → Red · 2 to 5 → Amber · 0 or 1 → nothing"),
           ("Headache (out of 10)", "7 or more → Red · 2 or more above her last recorded score → Amber"),
           ("Left arm pain (out of 10)", "7 or more → Amber (never Red on its own)"),
           ("Tiredness", "7 days or more → Amber"),
           ("Repeat reports", "3rd report of headache, nausea or arm pain within 14 days (today counted) → Amber"),
           ("Unanswered calls", "2 automated calls, 5 minutes apart")]

TIER_RULES = {
    "red": ["Red wins over everything: one Red reason is enough, whatever the other answers say.",
            "The danger questions are asked first, and the call stops at the first danger answer.",
            "An unclear answer to a danger question is Red (not confirmed): the system never assumes she is safe.",
            "If the system cannot read the answers (a fault), the call is Red (not confirmed) and a person calls."],
    "amber": ["Amber only when no Red reason fired. One Amber reason is enough.",
              "Her own word “severe”, or a symptom no question covers, keeps the call at least Amber: these can only "
              "raise the tier, never lower it.",
              "An unclear answer to a non-danger question is Amber."],
    "green": ["Green only when every question asked was answered clearly and no Red or Amber rule fired.",
              "Green is still followed up: a call the next morning and the coordinator's review."],
}


def _questions() -> list[dict]:
    asked = {q: "Always asked first (danger core)" for q in TR.CORE}
    asked.update({q: "When her record has a left-arm baseline" for q in TR.HISTORY_ARM})
    for tag, qs in TR.MODULES.items():
        for q in qs:
            asked.setdefault(q, f"When she mentions {TR.MODULE_LABELS.get(tag, tag)}")
    out = []
    for qid, field, kind, danger, _hist, en in R.QUESTION_SPEC:
        effects = [{"tier": t, "text": text} for t, text, _ in EFFECTS[qid]]
        effects.append({"tier": "red" if danger else "amber",
                        "text": "Unclear or “don't know” → Red (not confirmed)" if danger else "Unclear or “don't know” → Amber"})
        out.append({"id": qid, "danger": danger, "kind": kind, "asked": asked.get(qid, "—"),
                    "in_fixed_12": qid in TR.FALLBACK,
                    "text": {"English": en.format(day="<date of her last report>"),
                             "Hindi": R.QUESTION_TEXT["Hindi"][qid].format(day="<तारीख़>")},
                    "effects": effects})
    return out


# The English health words as a person reads them (tests check each one is caught by voice_sorter.HEALTH_EN).
HEALTH_EN_SHOWN = ["pain", "ache", "hurts", "fever", "tired", "fatigue", "dizzy", "swelling", "swollen", "bleeding",
                   "breathless", "nausea", "vomiting", "medicine", "medication", "tablet", "pill", "letrozole", "lump",
                   "itching", "fracture", "unwell", "weakness", "side effect"]


def _call() -> dict:
    """UC1-V: the simulated booking call and its reply sorter."""
    lines = reference.uc1v_library()["languages"]["Hindi"]["lines"]
    return {
        "labels": [
            ("HEALTH_CONCERN", "She mentions any symptom, pain, side effect, worry about the illness, or anything about a "
                               "medicine, even inside a yes.", "Safety stop: L9 spoken, WhatsApp W2, handover to a person "
                               "with a 2-hour callback. Nothing is booked; routine messages are held."),
            ("YES", "A clear agreement, in her own words.", "Identity → L2. Booking question → offer one slot (L5). "
                                                            "Slot → book it, L6, WhatsApp W1."),
            ("NO", "She declines, or the slot does not suit.", "First no → explain once (L4). Second no → accept (L8) and "
                                                             "tell the doctor's team. Slot no → L7, a person calls."),
            ("UNCLEAR", "No answer to the question, silence, or noise.", "Re-ask once (L3). A second unclear reply → "
                                                                         "coordinator task."),
        ],
        "rules": [
            "Health beats everything: “Yes, book it, but there is pain at the operation site” is HEALTH_CONCERN.",
            "Health is checked first, by a fixed word list, before the AI is asked anything. A word she plainly "
            "denies (“no pain”, “no fever either”) does not stop the call on its own: the AI still checks the reply "
            "for any health matter. “The pain is not going down” still stops at once.",
            "The AI only picks one of the four labels. It never writes what the agent says: every agent line is a "
            "fixed, approved sentence (L1 to L9).",
            "A yes counts only when she said a yes word herself; the AI cannot invent one.",
            "No health words are spoken until she has confirmed who she is.",
        ],
        "faults": [("Timeout, server error (500, 503) or revoked key (401)", "Safety stop", VS.FAULT_TEXT["ai_sorter_failed"]),
                   ("Answer that is not exactly one valid label (JSON, a sentence, an extra field, an empty label)",
                    "Safety stop", VS.FAULT_TEXT["ai_invalid_label"]),
                   ("Speech-to-text fails (corrupt audio)", "Safety stop", VS.FAULT_TEXT["ai_speech_failed"]),
                   ("Translation fails", "Safety stop", VS.FAULT_TEXT["ai_translation_failed"]),
                   ("Blank reply from the model (HTTP 200, empty)", "Safety stop", VS.FAULT_TEXT["ai_sorter_failed"]),
                   ("Silence or only noise from the patient", "UNCLEAR: re-ask once, then a coordinator task",
                    "not a fault: she said nothing")],
        "health_words_hi": VS.HEALTH_HI.pattern.split("|"),
        "health_words_en": HEALTH_EN_SHOWN,
        "lines": [{"id": ln["line_id"], "when": ln.get("when"), "en": ln.get("text_en"), "hi": ln.get("text"),
                   "status": ln.get("status")} for ln in lines if ln.get("channel") == "voice"],
        "prompt_version": VS.PROMPT_VERSION,
    }


def criteria() -> dict:
    qs = _questions()
    tiers = {}
    for tier in ("red", "amber", "green"):
        tiers[tier] = {
            "rules": TIER_RULES[tier],
            "actions": [{"when": w, "code": c, "within": t, "what": x} for w, c, t, x in ACTIONS[tier]],
            "reasons": ([{"code": c, "text": t} for c, t in R.RED_PLAIN.items()] if tier == "red" else
                        [{"code": c, "text": t} for c, t in AMBER_PLAIN.items()] if tier == "amber" else []),
            "questions": [{**q, "effects": [e for e in q["effects"] if e["tier"] == tier]}
                          for q in qs if any(e["tier"] == tier for e in q["effects"])],
        }
    return {"protocol_status": R.PROTOCOL["protocol_status"], "tiers": tiers, "numbers": NUMBERS,
            "questions": qs, "call": _call(),
            "router": ["The five danger questions are asked first, always, and never skipped.",
                       "Each symptom the AI heard in her message adds its questions; the AI never writes a question.",
                       "If the AI's symptom tags cannot be trusted, the fixed 12 questions are asked.",
                       "The AI never picks the tier: the fixed rules on this page do."]}
