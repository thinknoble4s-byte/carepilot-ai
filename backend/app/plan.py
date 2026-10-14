"""A4 and A5: confirm a care plan and generate its order cards.

No cards exist for a patient until a coordinator confirms the plan; the plan,
its cards and both audit rows commit together or not at all."""
import calendar
import re
from datetime import date, timedelta

from . import audit, clock
from .errors import Conflict, Invalid, NotFound
from .reference import IDENTIFIER_OPTIONS

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def add_months(d: date, months: int) -> date:
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def due_date(end: date, months: int, days: int) -> date:
    d = add_months(end, months) + timedelta(days=days)
    while d.weekday() >= 5:  # Saturday or Sunday -> next Monday
        d += timedelta(days=1)
    return d


def generate_cards(template: dict, treatment_end_date: date) -> list[dict]:
    """Pure: same template and date, same cards. No database, clock or randomness."""
    cards = []
    for item in template["items"]:
        dept = template["departments"][item["department"]]
        cards.append({
            "item_code": item["item_code"],
            "card_type": item["type"],
            "name": item["name"],
            "department": item["department"],
            "due_date": due_date(treatment_end_date, item["months"], item["days"]).isoformat(),
            "state": "Due",
            "owner_name": dept["owner"],
            "cover_name": dept["cover"],
            "origin": "plan_item",
        })
    return cards


def get_template(conn, template_id: str) -> dict | None:
    row = conn.execute(
        """
        SELECT body FROM config_versions
        WHERE kind = 'plan_template' AND name = %s AND status = 'active'
        """,
        (template_id,),
    ).fetchone()
    return None if row is None else row["body"]


def _parse_date(value) -> date | None:
    if isinstance(value, str) and DATE_RE.match(value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def confirm_plan(conn, user_id, mrn, template_id, therapy, treatment_end_date, identifiers_checked) -> dict:
    # Checks in order; the first failure wins and nothing is written.
    if conn.execute("SELECT 1 FROM patient WHERE mrn = %s FOR UPDATE", (mrn,)).fetchone() is None:
        raise NotFound(f"Patient {mrn} not found")
    template = get_template(conn, str(template_id))
    if template is None:
        raise NotFound(f"Template {template_id} not found")
    ids = identifiers_checked if isinstance(identifiers_checked, list) else []
    if len(set(ids)) < 2 or any(i not in IDENTIFIER_OPTIONS for i in ids):
        raise Invalid("Check at least two different identifiers from: " + ", ".join(IDENTIFIER_OPTIONS))
    if not therapy or not str(therapy).strip():
        raise Invalid("therapy is required")
    end = _parse_date(treatment_end_date)
    if end is None:
        raise Invalid("treatment_end_date must be a real date (YYYY-MM-DD)")
    today = clock.get_today(conn)
    if end > today:
        raise Invalid(f"treatment_end_date {end} is after today ({today})")
    if conn.execute("SELECT 1 FROM care_plan WHERE mrn = %s", (mrn,)).fetchone():
        raise Conflict(f"Patient {mrn} already has a confirmed plan")

    plan = conn.execute(
        """
        INSERT INTO care_plan (mrn, template_id, template_version, therapy, treatment_end_date,
                               identifiers_checked, confirmed_by, confirmed_on)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING care_plan_id, mrn, template_id, template_version, therapy, treatment_end_date,
                  identifiers_checked, confirmed_by, confirmed_at
        """,
        (mrn, template["template_id"], template["version"], str(therapy).strip(), end,
         list(dict.fromkeys(ids)), user_id, today),
    ).fetchone()
    tmpl = {"template_id": template["template_id"], "template_version": template["version"]}
    audit.append(
        conn, actor_type="person", actor_name=user_id, action="plan_confirmed",
        entity_type="care_plan", entity_id=str(plan["care_plan_id"]), mrn=mrn, **tmpl,
        details={"therapy": plan["therapy"], "treatment_end_date": end.isoformat(),
                 "identifiers_checked": plan["identifiers_checked"]},
    )
    cards = [
        conn.execute(
            """
            INSERT INTO order_card (mrn, care_plan_id, item_code, card_type, name, department, due_date,
                                    state, owner_name, cover_name, origin)
            VALUES (%(mrn)s, %(care_plan_id)s, %(item_code)s, %(card_type)s, %(name)s, %(department)s,
                    %(due_date)s, %(state)s, %(owner_name)s, %(cover_name)s, %(origin)s)
            RETURNING card_id, item_code, card_type, name, department, due_date, state, appointment_date,
                      owner_name, cover_name, origin
            """,
            {**card, "mrn": mrn, "care_plan_id": plan["care_plan_id"]},
        ).fetchone()
        for card in generate_cards(template, end)
    ]
    audit.append(
        conn, actor_type="system", actor_name="system", action="cards_generated",
        entity_type="care_plan", entity_id=str(plan["care_plan_id"]), mrn=mrn, **tmpl,
        details={"count": len(cards),
                 "cards": [{"item_code": c["item_code"], "due_date": c["due_date"].isoformat()} for c in cards]},
    )
    return {"care_plan": plan, "cards": cards}
