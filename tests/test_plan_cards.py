"""A4 and A5: plan confirmation is checked in order and all-or-nothing; due dates match the answer key."""
from datetime import date

import pytest

from conftest import PRIYA, plan_body, plan_inputs, scalar

# Step A guide, table 4.7: V1, T1, V2, and T2/T3 (always the same date).
EXPECTED_DUE = {
    "SYN-0001": ("2026-10-20", "2026-10-13", "2027-04-20", "2027-04-06"),
    "SYN-0002": ("2026-11-04", "2026-10-28", "2027-05-04", "2027-04-20"),
    "SYN-0003": ("2026-11-09", "2026-11-02", "2027-05-10", "2027-04-26"),
    "SYN-0004": ("2026-11-09", "2026-11-02", "2027-05-10", "2027-04-26"),
    "SYN-0005": ("2026-11-10", "2026-11-03", "2027-05-10", "2027-04-26"),
    "SYN-0006": ("2026-11-12", "2026-11-05", "2027-05-12", "2027-04-28"),
    "SYN-0007": ("2026-11-16", "2026-11-09", "2027-05-17", "2027-05-03"),
    "SYN-0008": ("2026-11-16", "2026-11-09", "2027-05-17", "2027-05-03"),
    "SYN-0009": ("2026-11-20", "2026-11-13", "2027-05-20", "2027-05-06"),
    "SYN-0010": ("2026-11-23", "2026-11-16", "2027-05-24", "2027-05-10"),
    "SYN-0011": ("2026-11-26", "2026-11-19", "2027-05-26", "2027-05-12"),
    "SYN-0012": ("2026-11-30", "2026-11-23", "2027-05-31", "2027-05-17"),
    "SYN-0013": ("2026-12-02", "2026-11-25", "2027-06-02", "2027-05-19"),
    "SYN-0014": ("2026-12-07", "2026-11-30", "2027-06-07", "2027-05-24"),
    "SYN-0015": ("2026-12-10", "2026-12-03", "2027-06-10", "2027-05-27"),
    "SYN-0016": ("2026-12-15", "2026-12-08", "2027-06-15", "2027-06-01"),
    "SYN-0017": ("2026-12-21", "2026-12-14", "2027-06-21", "2027-06-07"),
    "SYN-0018": ("2026-12-25", "2026-12-18", "2027-06-25", "2027-06-11"),
    "SYN-0019": ("2027-01-04", "2026-12-28", "2027-07-05", "2027-06-21"),
    "SYN-0020": ("2027-03-01", "2027-02-22", "2027-08-31", "2027-08-17"),
    "SYN-0021": ("2026-09-15", "2026-09-08", "2027-03-15", "2027-03-01"),
    "SYN-0022": ("2026-10-05", "2026-09-28", "2027-04-05", "2027-03-22"),
    "SYN-0023": ("2027-01-18", "2027-01-11", "2027-07-19", "2027-07-05"),
    "SYN-0024": ("2027-03-15", "2027-03-08", "2027-09-14", "2027-08-31"),
    "SYN-0025": ("2026-11-30", "2026-11-23", "2027-05-31", "2027-05-17"),
}

OWNERS = {"V1": ("Priya Nair", "Meera Joseph"), "V2": ("Priya Nair", "Meera Joseph"),
          "T1": ("Sunil Kumar", "Priya Nair"), "T2": ("Lakshmi Rao", "Priya Nair"),
          "T3": ("Lakshmi Rao", "Priya Nair")}


def cards_for(mrn: str) -> list[dict]:
    from app.plan import generate_cards
    from app.reference import plan_template

    return generate_cards(plan_template(), date.fromisoformat(plan_inputs()[mrn]["treatment_end_date"]))


# ---------- A5: pure card generation (no database) ----------
@pytest.mark.parametrize("mrn", sorted(EXPECTED_DUE))
def test_due_dates_match_answer_key(mrn):
    due = {c["item_code"]: c["due_date"] for c in cards_for(mrn)}
    v1, t1, v2, t23 = EXPECTED_DUE[mrn]
    assert due == {"V1": v1, "T1": t1, "V2": v2, "T2": t23, "T3": t23}


def test_month_end_and_weekend_rules():
    from app.plan import add_months

    assert add_months(date(2026, 8, 31), 6) == date(2027, 2, 28)
    assert add_months(date(2026, 11, 30), 3) == date(2027, 2, 28)
    assert {c["item_code"]: c["due_date"] for c in cards_for("SYN-0014")}["V1"] == "2026-12-07"
    sy20 = {c["item_code"]: c["due_date"] for c in cards_for("SYN-0020")}
    assert (sy20["V1"], sy20["T1"]) == ("2027-03-01", "2027-02-22")


def test_generation_is_deterministic():
    assert cards_for("SYN-0007") == cards_for("SYN-0007")


def test_all_cards_weekday_owned_and_due():
    all_cards = [c for mrn in EXPECTED_DUE for c in cards_for(mrn)]
    assert len(all_cards) == 125
    for c in all_cards:
        assert date.fromisoformat(c["due_date"]).weekday() < 5
        assert (c["owner_name"], c["cover_name"]) == OWNERS[c["item_code"]]
        assert c["state"] == "Due"


# ---------- A4: confirmation through the API ----------
def confirm(client, mrn, **overrides):
    return client.post(f"/patients/{mrn}/plan/confirm", headers=PRIYA, json=plan_body(mrn, **overrides))


def test_confirm_creates_plan_cards_and_two_audit_rows(seeded, client):
    assert client.get("/patients/SYN-0007/cards", headers=PRIYA).json() == []
    audits = scalar("SELECT count(*) FROM audit_ledger")
    r = confirm(client, "SYN-0007")
    assert r.status_code == 201
    assert len(r.json()["cards"]) == 5
    assert scalar("SELECT count(*) FROM care_plan") == 1
    assert scalar("SELECT count(*) FROM order_card") == 5
    assert scalar("SELECT count(*) FROM audit_ledger") == audits + 2
    from app import db

    with db.connect() as c:
        last = c.execute("SELECT action, actor_type, actor_name, mrn FROM audit_ledger ORDER BY id DESC LIMIT 2").fetchall()
    assert last == [
        {"action": "cards_generated", "actor_type": "system", "actor_name": "system", "mrn": "SYN-0007"},
        {"action": "plan_confirmed", "actor_type": "person", "actor_name": "u_priya", "mrn": "SYN-0007"},
    ]
    cards = client.get("/patients/SYN-0007/cards", headers=PRIYA).json()
    assert [(c["item_code"], c["due_date"]) for c in cards][:2] == [("V1", "2026-11-16"), ("T1", "2026-11-09")]


@pytest.mark.parametrize("mrn, overrides, status", [
    ("SYN-0007", {"identifiers_checked": ["name"]}, 422),
    ("SYN-0007", {"identifiers_checked": ["name", "name"]}, 422),
    ("SYN-0007", {"identifiers_checked": ["name", "fax"]}, 422),
    ("SYN-0007", {"therapy": "  "}, 422),
    ("SYN-0007", {"treatment_end_date": "2026-12-01"}, 422),  # after the demo clock's today
    ("SYN-0007", {"treatment_end_date": "2026-02-30"}, 422),
    ("SYN-0999", {}, 404),
    ("SYN-0007", {"template_id": "XYZ"}, 404),
])
def test_refused_confirmations_write_nothing(seeded, client, mrn, overrides, status):
    audits = scalar("SELECT count(*) FROM audit_ledger")
    body_mrn = "SYN-0007" if mrn == "SYN-0999" else mrn
    r = client.post(f"/patients/{mrn}/plan/confirm", headers=PRIYA, json=plan_body(body_mrn, **overrides))
    assert r.status_code == status
    assert scalar("SELECT count(*) FROM care_plan") == 0
    assert scalar("SELECT count(*) FROM order_card") == 0
    assert scalar("SELECT count(*) FROM audit_ledger") == audits


def test_second_confirmation_is_409(seeded, client):
    assert confirm(client, "SYN-0007").status_code == 201
    assert confirm(client, "SYN-0007").status_code == 409
    assert scalar("SELECT count(*) FROM order_card WHERE mrn = 'SYN-0007'") == 5


def test_failure_after_plan_insert_keeps_nothing(seeded, client, monkeypatch):
    from app import audit as audit_module
    from app import plan

    real = audit_module.append

    def fail_on_cards(conn, **kwargs):
        if kwargs["action"] == "cards_generated":
            raise RuntimeError("audit unavailable")
        return real(conn, **kwargs)

    monkeypatch.setattr(plan.audit, "append", fail_on_cards)
    audits = scalar("SELECT count(*) FROM audit_ledger")
    with pytest.raises(RuntimeError):
        confirm(client, "SYN-0007")
    assert scalar("SELECT count(*) FROM care_plan") == 0
    assert scalar("SELECT count(*) FROM order_card") == 0
    assert scalar("SELECT count(*) FROM audit_ledger") == audits


def test_confirm_all_25(seeded, client):
    audits = scalar("SELECT count(*) FROM audit_ledger")
    for mrn in plan_inputs():
        assert confirm(client, mrn).status_code == 201, mrn
    assert scalar("SELECT count(*) FROM care_plan") == 25
    assert scalar("SELECT count(*) FROM order_card") == 125
    assert scalar("SELECT count(*) FROM audit_ledger") == audits + 50
    stored = scalar("""SELECT count(*) FROM order_card oc
                       WHERE extract(isodow FROM oc.due_date) > 5""")
    assert stored == 0


def test_template_endpoint(seeded, client):
    r = client.get("/templates/BC-SURV-1", headers=PRIYA)
    assert r.status_code == 200 and r.json()["template_id"] == "BC-SURV-1" and len(r.json()["items"]) == 5
    assert client.get("/templates/XYZ", headers=PRIYA).status_code == 404
