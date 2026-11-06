"""Inbound WhatsApp replies (simulated channel gateway) — Use Case 2a.

Every inbound message (button tap, typed text, voice note) is always saved and audited; only sending
is ever blocked, never receiving. A replayed event (same event id) creates nothing new.

Buttons are structured, so they update the workflow directly with no AI: "Booked" moves a visit card
to Booked (source: patient button, appointment date empty). Text and voice are never acted on by
themselves: the AI layer (Sarvam) gives the text, an English translation and one yes/no health flag
(failure or doubt counts as yes). A plain function then picks one action:
  flag yes -> stop and hand over (health_flag_handover): fixed acknowledgement, handover, send block
  flag no  -> hold while a proposal waits for a coordinator (held_open_proposal)
Voice Triage v1.2 (patients on the triage path): the model tags the message instead; a health message goes to the
triage call, and one that is clearly not about health gets one standard reply (no call, ticket or task).
HELP (or ಸಹಾಯ / मदद / உதவி / సహాయం) always reaches a person directly. In data sets with replies switched off
(Use Case 1) the reply is only saved.
"""
from pathlib import Path

from . import ai, audit, booking, clock, handover, help_tasks, messaging, policy, tasks, triage
from .agent import AGENT, set_next_action
from .errors import Invalid, NotFound

GATEWAY = "WhatsApp gateway (simulated)"
SARVAM = "Sarvam (AI)"
BUTTONS = ("booked", "help_book", "cant_come", "booked_elsewhere", *booking.SLOT_BUTTONS,  # 5 Nov: self-booking
           "done_outside", "done_hospital", "not_done")  # Use Case 2T: the report check
CLIP_DIR = Path(__file__).resolve().parent / "seed_data" / "uc2a" / "audio"
MAX_AUDIO_BYTES = 5 * 1024 * 1024  # a recorded note of up to 30 seconds is about 1 MB as 16 kHz WAV
SPOKEN = ("Hindi", "Kannada", "Tamil", "Telugu", "English", "auto")  # "auto": Sarvam detects the language


def clips(conn) -> list[dict]:
    return policy.settings(conn).get("voice_clips") or []


def choose_action(flag: str) -> tuple[str, str]:
    """REQ-05: a plain function, no AI call. It never overrides the send block."""
    return ("handover", "health_flag_handover") if flag == "yes" else ("hold", "held_open_proposal")


def receive_reply(conn, mrn: str, *, from_role: str = "patient", button: str | None = None, text: str | None = None,
                  in_reply_to: int | None = None, clip_id: str | None = None, event_id: str | None = None,
                  audio: bytes | None = None, mime: str | None = None, spoken: str | None = None,
                  understood: dict | None = None, empty: bool = False) -> dict:
    """A reply from the simulator. A tap together with text or a voice note is two events, handled in turn.
    empty: the patient sent a message with nothing in it (Testing round 1 Nov, Worksheet2)."""
    content = bool((text or "").strip()) or clip_id is not None
    if button and content:
        first = _receive(conn, mrn, from_role=from_role, button=button, in_reply_to=in_reply_to,
                         event_id=f"{event_id}:tap" if event_id else None)
        second = _receive(conn, mrn, from_role=from_role, text=text, clip_id=clip_id, in_reply_to=in_reply_to,
                          event_id=event_id)
        return {**second, "handling": first["handling"] + second["handling"],
                "inbound_ids": [first["inbound_id"], second["inbound_id"]]}
    return _receive(conn, mrn, from_role=from_role, button=button, text=text, clip_id=clip_id,
                    in_reply_to=in_reply_to, event_id=event_id, audio=audio, mime=mime, spoken=spoken,
                    understood=understood, empty=empty)


def has_words(text: str | None) -> bool:
    """A message with no letter or digit in any script (blank, dots, an emoji, a silent voice note) is empty."""
    return any(ch.isalnum() for ch in text or "")


def _receive(conn, mrn, *, from_role, button=None, text=None, clip_id=None, in_reply_to=None, event_id=None,
             audio=None, mime=None, spoken=None, understood=None, empty=False) -> dict:
    if conn.execute("SELECT 1 FROM patient WHERE mrn = %s", (mrn,)).fetchone() is None:
        raise NotFound(f"Patient {mrn} not found")
    if from_role not in ("patient", "caregiver"):
        raise Invalid("from_role must be patient or caregiver")
    if button is not None and button not in BUTTONS:
        raise Invalid("button must be one of: " + ", ".join(BUTTONS))
    text = (text or "").strip() or None
    if audio is not None:  # a voice note recorded or uploaded in the simulator (stored in the database)
        if clip_id is not None or button is not None or text is not None:
            raise Invalid("Send a recorded voice note on its own")
        if not audio:
            raise Invalid("The recording is empty")
        if len(audio) > MAX_AUDIO_BYTES:
            raise Invalid("The recording is too large (keep it under 30 seconds)")
        if not (mime or "").startswith("audio/"):
            raise Invalid("Only audio files can be sent as a voice note")
    if spoken is not None and spoken not in SPOKEN:
        raise Invalid("language must be one of: " + ", ".join(SPOKEN))
    if empty and (button is not None or text is not None or clip_id is not None or audio is not None):
        raise Invalid("An empty message has no button, text or voice note")
    if not empty and button is None and text is None and clip_id is None and audio is None:
        raise Invalid("A reply needs a button, some text or a voice note")

    if event_id:  # REQ-20: a replayed event changes nothing
        seen = conn.execute("SELECT * FROM inbound_message WHERE event_id = %s", (event_id,)).fetchone()
        if seen:
            prop = conn.execute("SELECT proposal_id FROM proposal WHERE inbound_id = %s",
                                (seen["inbound_id"],)).fetchone()
            audit.append(conn, actor_type="system", actor_name=GATEWAY, action="duplicate_suppressed",
                         entity_type="inbound_message", entity_id=str(seen["inbound_id"]), mrn=seen["mrn"],
                         outcome="replay_ignored", details={"event_id": event_id})
            return {"inbound_id": seen["inbound_id"], "handling": ["duplicate_ignored"], "duplicate": True,
                    "handover_id": seen["handover_id"], "proposal_id": prop["proposal_id"] if prop else None}

    if in_reply_to is not None:
        target = conn.execute("SELECT * FROM outbox_message WHERE message_id = %s AND mrn = %s AND status = 'sent'",
                              (in_reply_to, mrn)).fetchone()
        if target is None:
            raise NotFound(f"Message {in_reply_to} was not sent to {mrn}")
    else:
        target = conn.execute(
            "SELECT * FROM outbox_message WHERE mrn = %s AND status = 'sent' AND card_id IS NOT NULL "
            "ORDER BY message_id DESC LIMIT 1", (mrn,)).fetchone()

    audio_id, kind = None, "button" if button else "empty" if empty else "text"
    if clip_id is not None:
        clip = next((c for c in clips(conn) if c["id"] == clip_id), None)
        path = CLIP_DIR / f"{clip_id}.wav"
        if clip is None or not path.exists():
            raise NotFound(f"Voice clip {clip_id} is not available")
        audio_id = conn.execute("INSERT INTO audio_blob (mime, data) VALUES ('audio/wav', %s) RETURNING audio_id",
                                (path.read_bytes(),)).fetchone()["audio_id"]
        kind = "voice"
    elif audio is not None:
        audio_id = conn.execute("INSERT INTO audio_blob (mime, data) VALUES (%s, %s) RETURNING audio_id",
                                (mime.split(";")[0], audio)).fetchone()["audio_id"]
        kind = "voice"
    now = clock.get_now(conn)
    inbound = conn.execute(
        """
        INSERT INTO inbound_message (mrn, from_role, in_reply_to, button, text, handling, received_on, received_time,
                                     kind, event_id, audio_id, clip_id)
        VALUES (%s, %s, %s, %s, %s, 'received', %s, %s, %s, %s, %s, %s) RETURNING *
        """,
        (mrn, from_role, target["message_id"] if target else None, button, text, now.date(), now.time(), kind,
         event_id, audio_id, clip_id),
    ).fetchone()
    iid = inbound["inbound_id"]
    source = f"WhatsApp {'voice note' + (' (recorded)' if audio is not None else '') if kind == 'voice' else 'reply'} " \
             f"#{iid} from {from_role}, " \
             f"{now:%Y-%m-%d %H:%M}"
    audit.append(conn, actor_type="system", actor_name=GATEWAY,
                 action="voice_note_received" if kind == "voice" else "reply_received",
                 entity_type="inbound_message", entity_id=str(iid), mrn=mrn,
                 details={"kind": kind, "button": button, "text": text, "clip": clip_id, "from_role": from_role,
                          "recorded": audio is not None, "spoken_language": spoken,
                          "in_reply_to": target["message_id"] if target else None, "audio_stored": audio_id is not None})

    result = {"inbound_id": iid, "handling": [], "handover_id": None, "proposal_id": None}
    if not policy.settings(conn)["act_on_replies"]:
        # Use Case 1: the reply is kept in the message history and CarePilot takes no action.
        conn.execute("UPDATE inbound_message SET handling = 'saved_no_action' WHERE inbound_id = %s", (iid,))
        return {**result, "handling": ["saved_no_action"]}

    if button:
        result["handling"].append(_apply_button(conn, mrn, button, target, iid))
    elif help_tasks.is_keyword(text, policy.deadlines(conn)["help_keywords"]) and \
            policy.settings(conn).get("help_keyword") == "task":
        # Use Case 2b: HELP (whole message, exact match) is a help-request task for a person. No AI, no message.
        audit.append(conn, actor_type="system", actor_name=GATEWAY, action="keyword_matched", entity_type="inbound_message",
                     entity_id=str(iid), mrn=mrn, reason_code="help_keyword", details={"text": text})
        card = conn.execute("SELECT * FROM order_card WHERE card_id = %s", (target["card_id"],)).fetchone() \
            if target and target["card_id"] else None
        r = help_tasks.escalate(conn, mrn=mrn, task_type="help_request", card=card, inbound_id=iid, actor=GATEWAY)
        result["handling"].append(f"help_request_{'created' if r['created'] else 'already_open'}:help_keyword")
        result["task_id"] = r["task"]["task_id"]
    elif help_tasks.is_keyword(text, policy.deadlines(conn)["help_keywords"]):
        ho, created = handover.open_handover(conn, mrn, trigger="help_keyword", actor_type="system", actor=GATEWAY,
                                             patient_words=text, source=source, inbound_id=iid)
        result["handover_id"] = ho["handover_id"]
        result["handling"].append(f"handover_{'opened' if created else 'updated'}:help_keyword")
        if created:
            _acknowledge(conn, mrn, from_role, ho["handover_id"], "ack_help_keyword")
    else:
        result.update(_understand_and_act(conn, mrn, inbound, source, from_role, spoken, understood))
    conn.execute("UPDATE inbound_message SET handling = %s, handover_id = %s WHERE inbound_id = %s",
                 ("; ".join(result["handling"]), result["handover_id"], iid))
    return result


def receive_document(conn, mrn: str, *, data: bytes, mime: str, file_name: str, from_role: str = "patient",
                     event_id: str | None = None, from_phone: str | None = None, note: str | None = None) -> dict:
    """Use Case 2T REQ-11: a photo or PDF from the patient. Always saved; held Unverified; moves no card by itself.
    Text sent with it goes through the Use Case 2a path; its words are kept verbatim for the doctor."""
    from . import test_cycle

    patient = conn.execute("SELECT * FROM patient WHERE mrn = %s", (mrn,)).fetchone()
    if patient is None:
        raise NotFound(f"Patient {mrn} not found")
    if from_role not in ("patient", "caregiver"):
        raise Invalid("from_role must be patient or caregiver")
    if len(data or b"") > MAX_AUDIO_BYTES:
        raise Invalid("The file is too large (5 MB at most)")
    if event_id:
        seen = conn.execute("SELECT * FROM inbound_message WHERE event_id = %s", (event_id,)).fetchone()
        if seen:
            return {"inbound_id": seen["inbound_id"], "duplicate": True}
    # The sender's number (hard check G7): the patient's own, or the listed caregiver's, unless the demo says other.
    phone = (from_phone or "").strip() or (patient["phone"] if from_role == "patient" else patient["caregiver_phone"])
    note = (note or "").strip() or None
    translation = None
    if note:  # the words go through the Use Case 2a path (a person sees them) and are kept with the report
        r = receive_reply(conn, mrn, from_role=from_role, text=note, event_id=f"{event_id}:note" if event_id else None)
        row = conn.execute("SELECT translation FROM inbound_message WHERE inbound_id = %s",
                           (r["inbound_id"],)).fetchone()
        translation = row["translation"] if patient["preferred_language"] in ("Hindi", "Kannada", "Tamil", "Telugu") else None
    now = clock.get_now(conn)
    blob = conn.execute("INSERT INTO audio_blob (mime, data) VALUES (%s, %s) RETURNING audio_id",
                        (mime, data)).fetchone()["audio_id"] if data else None
    inbound = conn.execute(
        """INSERT INTO inbound_message (mrn, from_role, text, handling, received_on, received_time, kind, event_id, audio_id)
           VALUES (%s, %s, %s, 'document_held_unverified', %s, %s, 'document', %s, %s) RETURNING *""",
        (mrn, from_role, file_name, now.date(), now.time(), event_id, blob)).fetchone()
    audit.append(conn, actor_type="system", actor_name=GATEWAY, action="document_received", entity_type="inbound_message",
                 entity_id=str(inbound["inbound_id"]), mrn=mrn,
                 details={"file": file_name, "mime": mime, "from_role": from_role, "from_phone": phone,
                          "bytes": len(data or b""), "note": note})
    report = test_cycle.receive_outside(conn, mrn, data=data, mime=mime, file_name=file_name,
                                        channel=f"WhatsApp from {from_role}", inbound_id=inbound["inbound_id"],
                                        actor=GATEWAY, sender_phone=phone, note=note, note_translation=translation)
    return {"inbound_id": inbound["inbound_id"], "report_id": report["report_id"], "status": report["status"],
            "identifiers": report["identifiers"], "score": report.get("score"), "routing": report.get("routing"),
            "routing_applied": report.get("routing_applied"), "hard_checks": report.get("hard_checks"),
            "group_id": report.get("group_id")}


def _acknowledge(conn, mrn, recipient, handover_id, reason) -> dict:
    return messaging.send_message(conn, mrn=mrn, template_id="handover_ack", actor_type="agent", actor=AGENT,
                                  reason_code=reason, recipient=recipient, handover_id=handover_id)


def _understand_and_act(conn, mrn, inbound, source, from_role, spoken=None, understood=None) -> dict:
    patient = conn.execute("SELECT * FROM patient WHERE mrn = %s", (mrn,)).fetchone()
    iid = inbound["inbound_id"]
    # Voice Triage v1.2: a patient on the triage path has her message tagged, and the router picks the route
    triage_path = triage.enabled(conn) and triage.history(conn, mrn) is not None
    # Testing round 1 Nov (Worksheet2): a text with no words needs no AI. It is never the standard reply: we cannot
    # tell what she meant, so it counts as health and we call her.
    empty = understood is None and inbound["kind"] in ("text", "empty") and not has_words(inbound["text"])
    if empty:
        u = {"transcript": inbound["text"] or "", "translation": "", "flag": "yes", "failed": False, "unsure": True,
             "attempts": 0, "model": "none (empty message, no AI call)", "prompt_version": None,
             "errors": ["empty message: no words to read"]}
    elif understood is not None:
        # Demo seed only (Use Case 2c section 6): the flag and transcript come from the Use Case 2a run, no AI call.
        u = {"transcript": understood["transcript"], "translation": understood["translation"], "flag": understood["flag"],
             "failed": False, "unsure": False, "attempts": 0, "model": "seed (no AI call)", "prompt_version": None,
             "errors": []}
    elif inbound["kind"] == "voice":
        audio = conn.execute("SELECT data, mime FROM audio_blob WHERE audio_id = %s", (inbound["audio_id"],)).fetchone()
        # The patient may speak a language other than her profile language (UC2a: English profile, Hindi note).
        clip = next((c for c in clips(conn) if c["id"] == inbound["clip_id"]), {})
        language = spoken or clip.get("language", patient["preferred_language"])
        u = ai.understand(conn, language=language, audio=bytes(audio["data"]),
                          mime=audio["mime"], clip_id=inbound["clip_id"], tags=triage_path)
    else:
        u = ai.understand(conn, language=patient["preferred_language"], text=inbound["text"], tags=triage_path)
    if inbound["kind"] == "voice" and not u["failed"] and not has_words(u["transcript"]):
        # a silent voice note: Sarvam heard no words. Never the standard reply; we call her.
        empty = True
        u = {**u, "flag": "yes", "unsure": True, "errors": u["errors"] + ["empty voice note: no words heard"]}
        u.pop("route", None)
    conn.execute(
        """
        UPDATE inbound_message SET transcript = %s, translation = %s, ai_flag = %s, ai_failed = %s, ai_attempts = %s,
               ai_model = %s, ai_prompt_version = %s WHERE inbound_id = %s
        """,
        (u["transcript"], u["translation"], u["flag"], u["failed"], u["attempts"], u["model"], u["prompt_version"], iid),
    )
    audit.append(conn, actor_type="system" if understood is not None else "ai_suggestion",
                 actor_name="Demo seed" if understood is not None else SARVAM,
                 action="ai_understanding_seeded" if understood is not None else "ai_understanding",
                 entity_type="inbound_message", entity_id=str(iid), mrn=mrn, model=u["model"],
                 prompt_version=u["prompt_version"], outcome=f"flag={u['flag']}",
                 reason_code="empty_message" if empty else
                 "ai_call_failed" if u["failed"] else ("ai_unsure" if u["unsure"] else None),
                 details={"transcript": u["transcript"], "translation": u["translation"], "attempts": u["attempts"],
                          "failed": u["failed"], "errors": u["errors"],
                          **({"tags": u["clean_tags"], "route": u["route"]} if "route" in u else {})})

    open_h = messaging.open_handover(conn, mrn)
    if u.get("route") == "STANDARD_REPLY" and open_h is None and not messaging.open_triage(conn, mrn):
        return _standard_reply(conn, mrn, iid, from_role, u)
    action, reason = choose_action(u["flag"])
    facts = {"flag": u["flag"], "ai_failed": u["failed"], "ai_unsure": u["unsure"],
             "handover_already_open": open_h is not None}
    audit.append(conn, actor_type="agent", actor_name=AGENT, action="agent_decision", entity_type="inbound_message",
                 entity_id=str(iid), mrn=mrn, reason_code=reason, task="reply_understanding", outcome=action,
                 trigger_event=f"inbound_message:{iid}", decision_inputs=facts,
                 details={"action": "stop and hand over" if action == "handover" else "hold while a proposal waits"})
    words, translation = u["transcript"] or inbound["text"], u["translation"]

    # Voice Triage (where switched on): a health message, or any message while an episode is open, goes to the
    # triage episode instead of the Use Case 2c handover: the acknowledgement, then the automated call.
    if open_h is None and triage.enabled(conn) and (action == "handover" or messaging.open_triage(conn, mrn)):
        opened = triage.open_episode(conn, mrn, inbound=inbound, words=words or None,
                                     translation=translation or None, from_role=from_role, tags=u.get("tags"),
                                     tagged="route" in u, empty=empty)
        if opened is not None:
            ep, created = opened
            return {"handling": (["empty_message"] if empty else []) +
                                [f"ai_flag_{u['flag']}" + ("_failed" if u["failed"] else ""),
                                 f"triage_{'call_due' if created else 'episode_updated'}:episode_{ep['episode_id']}"],
                    "handover_id": None, "proposal_id": None, "triage_episode_id": ep["episode_id"]}

    if action == "handover" or open_h is not None:
        # Flag yes (or a person already owns the conversation): the words go to a person.
        if open_h is None:
            _acknowledge(conn, mrn, from_role, None, "ack_health_flag")  # sent before the handover opens
        ho, created = handover.open_handover(
            conn, mrn, trigger="health_matter", actor_type="system", actor=GATEWAY, patient_words=words,
            source=source, translation=translation, inbound_id=iid, ai_failed=u["failed"])
        if created:
            conn.execute("UPDATE outbox_message SET handover_id = %s WHERE mrn = %s AND template_id = 'handover_ack' "
                         "AND handover_id IS NULL", (ho["handover_id"], mrn))
        return {"handling": [f"ai_flag_{u['flag']}" + ("_failed" if u["failed"] else ""),
                             f"handover_{'opened' if created else 'updated'}:health_flag_handover"],
                "handover_id": ho["handover_id"], "proposal_id": None}

    # Flag no: a proposal for a coordinator, and a hold on routine reminders (REQ-16/17).
    d = ai.draft(conn, transcript=words, translation=translation)
    now = clock.get_now(conn)
    prop = conn.execute(
        """
        INSERT INTO proposal (mrn, inbound_id, summary, intent, intent_reason, draft_failed, created_at_demo,
                              ai_model, ai_prompt_version)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *
        """,
        (mrn, iid, d["summary"], d["intent"], d["intent_reason"], d["failed"], now, d["model"], d["prompt_version"]),
    ).fetchone()
    audit.append(conn, actor_type="ai_suggestion", actor_name=SARVAM, action="ai_draft", entity_type="proposal",
                 entity_id=str(prop["proposal_id"]), mrn=mrn, model=d["model"], prompt_version=d["prompt_version"],
                 outcome="draft_failed" if d["failed"] else "drafted",
                 details={"summary": d["summary"], "intent": d["intent"], "intent_reason": d["intent_reason"],
                          "attempts": d["attempts"], "errors": d["errors"]})
    audit.append(conn, actor_type="system", actor_name=GATEWAY, action="proposal_created", entity_type="proposal",
                 entity_id=str(prop["proposal_id"]), mrn=mrn, outcome="drafted",
                 details={"hold_on": bool(policy.settings(conn).get("hold_on_proposal", True))})
    if policy.settings(conn)["people_tasks"]:
        tasks.create_task(conn, mrn=mrn, task_type="proposal_review", actor_type="system", actor=GATEWAY,
                          reason="Read the patient's message and approve or reject the AI draft",
                          dedupe_key=f"proposal:{prop['proposal_id']}")
        conn.execute("UPDATE coordinator_task SET proposal_id = %s WHERE dedupe_key = %s",
                     (prop["proposal_id"], f"proposal:{prop['proposal_id']}"))
    set_next_action(conn, mrn, "Reminders on hold: proposal waiting for the coordinator"
                    if policy.settings(conn).get("hold_on_proposal", True) else "Proposal waiting for the coordinator")
    return {"handling": [f"ai_flag_{u['flag']}", "proposal_created:held_open_proposal"], "handover_id": None,
            "proposal_id": prop["proposal_id"]}


def _standard_reply(conn, mrn: str, iid: int, from_role: str, u: dict) -> dict:
    """Voice Triage v1.2 (Part 4.3): a message that is clearly not about health gets one fixed reply and nothing
    else: no call, no ticket, no task. It never answers what she asked; it points her to the hospital helpdesk."""
    msg = messaging.send_message(conn, mrn=mrn, template_id="triage_not_health", actor_type="agent", actor=AGENT,
                                 reason_code="not_health_standard_reply", recipient=from_role)
    audit.append(conn, actor_type="agent", actor_name=AGENT, action="agent_decision", entity_type="inbound_message",
                 entity_id=str(iid), mrn=mrn, reason_code="not_health_standard_reply", task="reply_understanding",
                 outcome="standard_reply", trigger_event=f"inbound_message:{iid}", template_id="TPL_WA_NOT_HEALTH_V1",
                 decision_inputs={"tags": u["clean_tags"], "route": u["route"]},
                 details={"action": "one standard reply; no call, no ticket, no task", "message_id": msg["message_id"],
                          "status": msg["status"]})
    return {"handling": ["ai_tags_not_health", "standard_reply:TPL_WA_NOT_HEALTH_V1"], "handover_id": None,
            "proposal_id": None}


def _apply_button(conn, mrn: str, button: str, target: dict | None, inbound_id: int) -> str:
    """Deterministic: no AI call. A tap is a structured fact recorded by the system (REQ-01)."""
    card = conn.execute("SELECT * FROM order_card WHERE card_id = %s",
                        (target["card_id"],)).fetchone() if target and target["card_id"] else None
    if button in ("done_outside", "done_hospital", "not_done"):  # Use Case 2T REQ-25: the report check (and reminder)
        from . import test_cycle

        check = test_cycle.check_for_message(conn, target["message_id"] if target else None)
        if check:
            outcome = test_cycle.answer_check(conn, check, button, inbound_id, GATEWAY)
        elif button == "done_outside" and card and policy.settings(conn).get("test_cycle"):
            outcome = test_cycle.tap_done_outside(conn, card, inbound_id, GATEWAY)  # the test reminder's 4th button
        else:
            outcome = "no_report_check"
    elif button in booking.SLOT_BUTTONS:  # 5 Nov: the slot she chose books the card, and she is told so
        outcome = booking.tap_slot(conn, mrn, button, target, card, inbound_id, GATEWAY)
    elif button in ("booked", "booked_elsewhere"):  # 5 Nov: booked for the day of the tap; no task, no "no date"
        if card is None:
            outcome = "no_card"
        elif card["state"] != "Due":
            outcome = "already_progressed"
        elif button == "booked_elsewhere" and card["card_type"] != "test":
            outcome = "not_a_test"
        else:
            outcome = booking.tap_booked(conn, card, inbound_id, GATEWAY, elsewhere=button == "booked_elsewhere")
    elif button == "help_book" and card and card["state"] == "Due":  # 5 Nov: four slots to choose from
        outcome = booking.offer_slots(conn, mrn, card, inbound_id, GATEWAY)
    elif button == "help_book" and card:
        outcome = "already_progressed"
    else:
        # Use Case 2b: the agent escalates to the coordinator (I can't come; Please help me book with no card);
        # one task, owner and cover from the card; the card stays Due; nothing is sent to the patient.
        task_type = "help_booking" if button == "help_book" else "cant_come"
        r = help_tasks.escalate(conn, mrn=mrn, task_type=task_type, card=card, inbound_id=inbound_id, actor=GATEWAY)
        outcome = f"{task_type}_{'created' if r['created'] else 'already_open'}"
    return f"button_{button}:{outcome}"
