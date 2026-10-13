"""Send-guard and outbox. Every outbound message goes through send_message:
only approved templates, only with consent, and no automated message while a
handover is open (except the fixed acknowledgement). Blocked messages are kept
in the outbox with the reason, so a blocked attempt is as visible as a sent one.

Delivery is simulated: a 'sent' message appears in the WhatsApp simulator.
"""
from datetime import date

from psycopg.types.json import Jsonb

from . import audit, clock, policy
from .errors import Conflict, NotFound
from .reference import ITEM_LABELS

AUTOMATED = ("agent", "system")
ALWAYS_ALLOWED_AUTOMATED = ("handover_ack",)  # the fixed acknowledgement after a health mention or HELP


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
    if actor_type in AUTOMATED and template_id not in ALWAYS_ALLOWED_AUTOMATED:
        if "automated_messaging" not in scopes:
            return "no_automated_consent", inputs
        if not patient["agent_on"] or patient["status"] == "Agent off":
            return "agent_off", inputs
        if patient["status"] == "Paused":
            return "patient_paused", inputs
        if handover is not None:
            return "handover_open", inputs
    return None, inputs


def render(tmpl: dict, patient: dict, language: str, card: dict | None, due: date | None) -> tuple[str, str]:
    def fill(lang: str) -> str:
        item = ""
        if card:
            item = ITEM_LABELS.get(card["item_code"], {}).get(lang) or card["name"].lower()
        return tmpl["text"][lang].format(first_name=patient["full_name"].split()[0], item=item,
                                         due=due.strftime("%d/%m/%Y") if due else "")

    lang = language if language in tmpl["text"] else "English"
    return fill(lang), fill("English")


def send_message(conn, *, mrn: str, template_id: str, actor_type: str, actor: str, reason_code: str,
                 recipient: str = "patient", card_id: int | None = None, gap_id: int | None = None,
                 handover_id: int | None = None, mode: str = "live") -> dict:
    patient = conn.execute("SELECT * FROM patient WHERE mrn = %s", (mrn,)).fetchone()
    if patient is None:
        raise NotFound(f"Patient {mrn} not found")
    tmpl = policy.template(conn, template_id)
    card = conn.execute("SELECT * FROM order_card WHERE card_id = %s", (card_id,)).fetchone() if card_id else None
    body, body_en = render(tmpl, patient, patient["preferred_language"], card, card["due_date"] if card else None)
    blocked, inputs = guard(conn, patient, template_id, recipient, actor_type)
    status = "blocked" if blocked else ("pending_approval" if mode == "shadow" else "sent")
    lang = patient["preferred_language"] if patient["preferred_language"] in tmpl["text"] else "English"
    buttons = [{"id": b["id"], "label": b.get(lang, b["English"]), "label_en": b["English"]} for b in tmpl["buttons"]]
    msg = conn.execute(
        """
        INSERT INTO outbox_message (mrn, recipient, to_phone, language, template_id, template_version, body, body_en,
                                    buttons, card_id, gap_id, handover_id, actor_type, actor, reason_code, status,
                                    blocked_reason, created_on)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (mrn, recipient, patient["phone"] if recipient == "patient" else patient["caregiver_phone"], lang,
         tmpl["template_id"], tmpl["version"], body, body_en, Jsonb(buttons), card_id, gap_id, handover_id,
         actor_type, actor, reason_code, status, blocked, clock.get_today(conn)),
    ).fetchone()
    audit.append(
        conn, actor_type=actor_type, actor_name=actor,
        action={"sent": "message_sent", "blocked": "message_blocked", "pending_approval": "message_drafted"}[status],
        entity_type="outbox_message", entity_id=str(msg["message_id"]), mrn=mrn, reason_code=blocked or reason_code,
        template_id=tmpl["template_id"], template_version=tmpl["version"], mode=mode, outcome=status,
        decision_inputs=inputs, details={"recipient": recipient, "card_id": card_id, "gap_id": gap_id},
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
