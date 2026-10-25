"""Send-guard and outbox. Every outbound message goes through send_message:
only approved templates, only with consent, and no automated message while a
handover is open (except the fixed acknowledgement). Blocked messages are kept
in the outbox with the reason, so a blocked attempt is as visible as a sent one.

Delivery is simulated: a 'sent' message appears in the WhatsApp simulator.
"""
import re
from datetime import date

from psycopg.types.json import Jsonb

from . import audit, clock, policy
from .errors import Conflict, NotFound
from .reference import item_label

AUTOMATED = ("agent", "system")
ALWAYS_ALLOWED_AUTOMATED = ("handover_ack",)  # the fixed acknowledgement after a health mention or HELP
# While a handover is open only these two pass, whoever sends (UC2a REQ-06): the acknowledgement and the
# booking message for a doctor's instruction (a person decided that one). Everything else is refused.
ALLOWED_DURING_HANDOVER = ("handover_ack", "instruction_booking",
                           # Use Case 2T REQ-20: decided by the doctor's click on a report, so they pass too
                           "report_continue_booked", "report_continue_due")
# Use Case 2b: while a help-booking, can't-come or help-request task is open, routine reminders (and, later,
# slot offers) to the patient are held for every sender (held_open_task). A setting, pending decision 10.
HELP_TASK_TYPES = ("help_booking", "cant_come", "help_request")
# Use Case 2c REQ-16: the urgent call-today task holds routine reminders too, until a person reaches the patient.
HOLD_TASK_TYPES = HELP_TASK_TYPES + ("urgent_call",
                                     # Use Case 2T REQ-20: an open report-check task holds routine messages too
                                     "find_hospital_report", "call_about_test", "call_patient_resend")
ROUTINE_TEMPLATES = ("booking_reminder", "missed_item_reminder", "test_booking_reminder", "test_report_check",
                     "report_upload_nudge")


def task_hold(conn, mrn: str) -> bool:
    if not policy.settings(conn).get("hold_on_task", False):
        return False
    return conn.execute("SELECT 1 FROM coordinator_task WHERE mrn = %s AND status = 'open' AND task_type = ANY(%s)",
                        (mrn, list(HOLD_TASK_TYPES))).fetchone() is not None


def latest_scopes(conn, mrn: str) -> list[str]:
    row = conn.execute("SELECT scopes FROM consent WHERE mrn = %s ORDER BY consent_id DESC LIMIT 1",
                       (mrn,)).fetchone()
    return list(row["scopes"]) if row else []


def open_handover(conn, mrn: str) -> dict | None:
    return conn.execute("SELECT * FROM handover WHERE mrn = %s AND stage <> 'closed'", (mrn,)).fetchone()


def guard(conn, patient: dict, template_id: str, recipient: str, actor_type: str) -> tuple[str | None, dict]:
    """Return (blocked_reason or None, the inputs it was decided on)."""
    scopes = latest_scopes(conn, patient["mrn"])
    handover = open_handover(conn, patient["mrn"])
    inputs = {"status": patient["status"], "agent_on": patient["agent_on"], "scopes": scopes,
              "handover_open": handover is not None, "actor_type": actor_type, "recipient": recipient}
    if patient["status"] in ("Lost to follow-up", "Followed up elsewhere"):
        return "patient_closed", inputs
    if f"whatsapp_{recipient}" not in scopes:
        return "no_whatsapp_consent", inputs
    if recipient == "caregiver" and not patient["caregiver_phone"]:
        return "no_caregiver_phone", inputs
    if handover is not None and template_id not in ALLOWED_DURING_HANDOVER:
        return "held_handover_open", inputs
    if template_id in ROUTINE_TEMPLATES and task_hold(conn, patient["mrn"]):
        inputs["open_help_task"] = True
        return "held_open_task", inputs
    if actor_type in AUTOMATED and template_id not in ALWAYS_ALLOWED_AUTOMATED:
        if "automated_messaging" not in scopes:
            return "no_automated_consent", inputs
        if not patient["agent_on"] or patient["status"] == "Agent off":
            return "agent_off", inputs
        if patient["status"] == "Paused":
            return "patient_paused", inputs
    return None, inputs


def render(tmpl: dict, patient: dict, card: dict | None, extra: dict | None = None) -> str:
    """Fill an approved template. Placeholders: {patient_name}, {first_name}, {item}, {item_name} (the card's
    name, e.g. "Blood test (CBC)"), {due}, {due_date}, and any extra values (e.g. {next_visit_date})."""
    lang = tmpl["language"]
    item = ""
    if card:
        item = item_label(card["item_code"], lang) or card["name"].lower()
    due = card["due_date"].strftime(tmpl.get("date_format") or "%d/%m/%Y") if card else ""
    text = tmpl["text"].format(**{"patient_name": patient["full_name"], "first_name": patient["full_name"].split()[0],
                                  "item": item, "item_name": card["name"] if card else "", "due": due,
                                  "due_date": due, **(extra or {})})
    return f"{text}\n\n{tmpl['closing']}" if tmpl.get("closing") else text


APPOINTMENT = {"English": "Appointment", "Hindi": "अपॉइंटमेंट", "Kannada": "ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್"}


def what_booked(card: dict | None, lang: str) -> str:
    """What a "Booked" button names: "Appointment" for a visit, the test for a test ("Blood test booked")."""
    if card is None or card["card_type"] == "visit":
        return APPOINTMENT.get(lang, APPOINTMENT["English"])
    label = item_label(card["item_code"], lang) or re.sub(r"\s*\(.*\)", "", card["name"])
    return label[:1].upper() + label[1:]


def render_buttons(conn, tmpl: dict, card: dict | None) -> list[dict]:
    """The template's buttons for this card: {what} names what is booked; a button marked "only": "test" (Done at
    an outside lab) shows only on a test card, and only where the test cycle is on."""
    lang, test_cycle = tmpl["language"], policy.settings(conn).get("test_cycle")
    out = []
    for b in tmpl["buttons"]:
        if b.get("only") == "test" and not (card and card["card_type"] == "test" and card["specimen"] is not False
                                            and test_cycle):
            continue
        out.append({"id": b["id"], "label": b["label"].replace("{what}", what_booked(card, lang)),
                    "label_en": b["label_en"].replace("{what}", what_booked(card, "English"))})
    return out


def send_message(conn, *, mrn: str, template_id: str, actor_type: str, actor: str, reason_code: str,
                 recipient: str = "patient", card_id: int | None = None, gap_id: int | None = None,
                 handover_id: int | None = None, mode: str = "live", extra: dict | None = None) -> dict:
    """Send one approved template through the send-guard. The sender refuses a template that is not
    approved and effective for the patient's language (held_template_missing): no fallback language."""
    patient = conn.execute("SELECT * FROM patient WHERE mrn = %s", (mrn,)).fetchone()
    if patient is None:
        raise NotFound(f"Patient {mrn} not found")
    lang = patient["preferred_language"]
    tmpl = policy.template(conn, template_id, lang)
    fallback = None
    if tmpl is None and template_id in ALWAYS_ALLOWED_AUTOMATED and lang != "English":
        # REQ-07: the acknowledgement is never blocked for want of an approved translation; silence is worse.
        tmpl = policy.template(conn, template_id, "English")
        fallback = f"{lang} template not approved; English sent"
        lang = "English" if tmpl else lang
    card = conn.execute("SELECT * FROM order_card WHERE card_id = %s", (card_id,)).fetchone() if card_id else None
    blocked, inputs = guard(conn, patient, template_id, recipient, actor_type)
    if tmpl is None and not blocked:
        blocked = "held_template_missing"
    if tmpl is not None:
        body = render(tmpl, patient, card, extra)
        english = tmpl if lang == "English" else policy.template(conn, template_id, "English")
        body_en = render(english, patient, card, extra) if english else body
        buttons = render_buttons(conn, tmpl, card)
    else:
        body = body_en = ""
        buttons = []
    status = "blocked" if blocked else ("pending_approval" if mode == "shadow" else "sent")
    version = tmpl["version"] if tmpl else "none"
    msg = conn.execute(
        """
        INSERT INTO outbox_message (mrn, recipient, to_phone, language, template_id, template_version, body, body_en,
                                    buttons, card_id, gap_id, handover_id, actor_type, actor, reason_code, status,
                                    blocked_reason, created_on)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (mrn, recipient, patient["phone"] if recipient == "patient" else patient["caregiver_phone"], lang,
         template_id, version, body, body_en, Jsonb(buttons), card_id, gap_id, handover_id,
         actor_type, actor, reason_code, status, blocked, clock.get_today(conn)),
    ).fetchone()
    audit.append(
        conn, actor_type=actor_type, actor_name=actor,
        action={"sent": "message_sent", "blocked": "message_blocked", "pending_approval": "message_drafted"}[status],
        entity_type="outbox_message", entity_id=str(msg["message_id"]), mrn=mrn, reason_code=blocked or reason_code,
        template_id=template_id, template_version=version, mode=mode, outcome=status,
        decision_inputs=inputs, details={"recipient": recipient, "card_id": card_id, "gap_id": gap_id, "language": lang,
                                         "language_fallback": fallback},
    )
    return msg


def approve(conn, message_id: int, user_id: str) -> dict:
    """A coordinator approves an agent draft (shadow mode); the guard runs again at send time."""
    msg = conn.execute("SELECT * FROM outbox_message WHERE message_id = %s FOR UPDATE", (message_id,)).fetchone()
    if msg is None:
        raise NotFound(f"Message {message_id} not found")
    if msg["status"] != "pending_approval":
        raise Conflict(f"Message {message_id} is {msg['status']}, not waiting for approval")
    patient = conn.execute("SELECT * FROM patient WHERE mrn = %s", (msg["mrn"],)).fetchone()
    blocked, inputs = guard(conn, patient, msg["template_id"], msg["recipient"], msg["actor_type"])
    status = "blocked" if blocked else "sent"
    updated = conn.execute(
        "UPDATE outbox_message SET status = %s, blocked_reason = %s, approved_by = %s WHERE message_id = %s RETURNING *",
        (status, blocked, user_id, message_id),
    ).fetchone()
    audit.append(conn, actor_type="person", actor_name=user_id, action="message_approved", entity_type="outbox_message",
                 entity_id=str(message_id), mrn=msg["mrn"], outcome=status, reason_code=blocked,
                 decision_inputs=inputs, template_id=msg["template_id"], template_version=msg["template_version"])
    return updated


def reject(conn, message_id: int, user_id: str) -> dict:
    msg = conn.execute("SELECT * FROM outbox_message WHERE message_id = %s FOR UPDATE", (message_id,)).fetchone()
    if msg is None:
        raise NotFound(f"Message {message_id} not found")
    if msg["status"] != "pending_approval":
        raise Conflict(f"Message {message_id} is {msg['status']}, not waiting for approval")
    updated = conn.execute(
        "UPDATE outbox_message SET status = 'rejected', approved_by = %s WHERE message_id = %s RETURNING *",
        (user_id, message_id),
    ).fetchone()
    audit.append(conn, actor_type="person", actor_name=user_id, action="message_rejected",
                 entity_type="outbox_message", entity_id=str(message_id), mrn=msg["mrn"], outcome="rejected")
    return updated
