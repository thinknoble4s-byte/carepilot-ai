"""Use Case 1 — Outbound messaging: G0 booking reminder (T-7), v1.1 (usecase-docs/), tests UC1-T1 to UC1-T4.

Seed: TP001 (consent, no plan yet), TP002 (no automated-messaging consent; plan and card seeded),
TP003 (card already Booked). D0 = Mon 2 Nov 2026, due = D0 + 14, T-8 = 8 Nov, T-7 = 9 Nov.
"""
from conftest import scalar, users_header

ANITA = users_header("u_anita")
DUE = "2026-11-16"
EXPECTED_BODY = (
    "Dear Kavya Rao,\nGreetings from Sanjeevini Clinic. We understand that you have not yet booked your appointment "
    "with Dr. Sekhar for your review due on 16-Nov. Please select one of the options below.\n\n"
    "If you have any concerns, please write to us or feel free to leave a voice message."
)


def cards(client, mrn):
    return client.get(f"/patients/{mrn}/cards", headers=ANITA).json()


def confirm_tp001(client):
    return client.post("/patients/TP001/plan/confirm", headers=ANITA,
                       json={"template_id": "UC1-REVIEW", "due_dates": {"RV": DUE}})


def set_date(client, day):
    r = client.post("/clock/set", headers=ANITA, json={"date": day})
    assert r.status_code == 200, r.text


def gap_check(client):
    return client.post("/rules/gap-check", headers=ANITA).json()


def reminder_run(client):
    return client.post("/agent/run", headers=ANITA).json()["actions"]


def gaps():
    from app import db

    with db.connect() as c:
        return {r["mrn"]: r for r in c.execute("SELECT * FROM gap_event ORDER BY gap_id").fetchall()}


def messages(mrn):
    from app import db

    with db.connect() as c:
        return c.execute("SELECT * FROM outbox_message WHERE mrn = %s ORDER BY message_id", (mrn,)).fetchall()


def run_to_t7(client):
    confirm_tp001(client)
    set_date(client, "2026-11-08")
    gap_check(client)
    set_date(client, "2026-11-09")
    gap_check(client)
    return reminder_run(client)


def test_seed_matches_section_4(uc1, client):
    assert uc1["scenario"] == "uc1" and uc1["patients"] == 3 and str(uc1["clock"]) == "2026-11-02"
    assert [u["id"] for u in client.get("/users").json()] == ["u_anita", "u_ravi", "u_sekhar", "u_suresh"]
    assert cards(client, "TP001") == []
    tp2, tp3 = cards(client, "TP002"), cards(client, "TP003")
    assert [(c["name"], c["due_date"], c["state"]) for c in tp2] == [("Review visit", DUE, "Due")]
    assert [(c["state"], c["booking_source"]) for c in tp3] == [("Booked", "coordinator")]
    assert scalar("SELECT count(*) FROM coordinator_task") == 0  # tasks are added later


def test_uc1_t1_happy_path(uc1, client):
    # Step 1: no card before confirmation; one card after; confirming again adds no second card.
    assert cards(client, "TP001") == []
    r = confirm_tp001(client)
    assert r.status_code == 201, r.text
    [card] = cards(client, "TP001")
    assert (card["card_type"], card["name"], card["due_date"], card["state"], card["owner_name"], card["cover_name"],
            card["origin"], card["booking_source"], card["appointment_date"]) == \
        ("visit", "Review visit", DUE, "Due", "Anita", "Ravi", "plan_item", None, None)
    assert card["care_plan_id"] == r.json()["care_plan"]["care_plan_id"]
    assert confirm_tp001(client).status_code == 409
    assert len(cards(client, "TP001")) == 1
    assert any(c["mrn"] == "TP001" and c["state"] == "Due" for c in client.get("/cards", headers=ANITA).json())

    # Step 2: T-8 raises nothing.
    set_date(client, "2026-11-08")
    assert gap_check(client)["new_gaps"] == []

    # Step 3: T-7 raises one gap each for TP001 and TP002, none for TP003.
    set_date(client, "2026-11-09")
    new = gap_check(client)["new_gaps"]
    assert sorted((g["mrn"], g["rule"], g["status"]) for g in new) == [("TP001", "G0", "open"), ("TP002", "G0", "open")]

    # Step 4: TP001 sent, TP002 blocked (no consent), TP003 nothing.
    actions = {a["mrn"]: (a["reason_code"], a["outcome"]) for a in reminder_run(client)}
    assert actions == {"TP001": ("gap_actionable", "sent"), "TP002": ("held_no_consent", "blocked")}
    g = gaps()
    assert g["TP001"]["status"] == "reminder_sent" and g["TP002"]["status"] == "blocked"

    # Step 5: one message in the simulator, the approved text and the three buttons.
    [msg] = messages("TP001")
    assert msg["status"] == "sent" and msg["recipient"] == "patient" and msg["language"] == "English"
    assert msg["body"] == EXPECTED_BODY
    assert [b["label"] for b in msg["buttons"]] == [
        "Booked (I have already booked)", "Please help me book (our team will help me)", "I can't come"]
    assert cards(client, "TP001")[0]["state"] == "Due"


def test_uc1_t2_repeat_run(uc1, client):
    run_to_t7(client)
    assert gap_check(client)["new_gaps"] == []
    assert reminder_run(client) == []
    assert len([g for g in gaps().values() if g["mrn"] == "TP001"]) == 1
    assert len(messages("TP001")) == 1
    assert scalar("SELECT count(*) FROM audit_ledger WHERE action = 'duplicate_suppressed' AND mrn = 'TP001'") == 1


def test_uc1_t3_no_consent(uc1, client):
    run_to_t7(client)
    assert messages("TP002") == []
    [card] = cards(client, "TP002")
    assert (card["gap_status"], card["gap_reason"]) == ("blocked", "held_no_consent")  # shown on the Patient page
    assert scalar("SELECT reason_code FROM audit_ledger WHERE action = 'agent_decision' AND mrn = 'TP002'") == \
        "held_no_consent"
    row = next(p for p in client.get("/worklist", headers=ANITA).json()["patients"] if p["mrn"] == "TP002")
    assert any("held_no_consent" in r["txt"] for r in row["reasons"])


def test_uc1_t4_already_booked(uc1, client):
    run_to_t7(client)
    assert "TP003" not in gaps() and messages("TP003") == []


def test_audit_sequence_with_demo_time(uc1, client):
    run_to_t7(client)
    from app import db

    with db.connect() as c:
        rows = c.execute(
            """SELECT id, action, actor_type, demo_date, demo_time FROM audit_ledger
               WHERE mrn = 'TP001' OR action IN ('gap_check_run', 'reminder_run') ORDER BY id""").fetchall()
    actions = [r["action"] for r in rows]
    order = ["plan_confirmed", "card_created", "gap_check_run", "gap_detected", "agent_decision", "message_sent"]
    positions = [actions.index(a) for a in order]
    assert positions == sorted(positions)
    assert all(r["actor_type"] and r["demo_date"] for r in rows)
    by_action = {r["action"]: r for r in rows}
    assert str(by_action["gap_detected"]["demo_date"]) == "2026-11-09" and by_action["gap_detected"]["demo_time"] == "07:00"
    assert by_action["agent_decision"]["demo_time"] == "07:30" and by_action["message_sent"]["demo_time"] == "07:30"
    assert scalar("SELECT count(*) FROM audit_ledger WHERE actor_type IN ('ai', 'ai_suggestion')") == 0
    assert client.get("/audit?limit=1").json()["chain"]["valid"]


def test_button_tap_is_saved_without_action(uc1, client):
    run_to_t7(client)
    [msg] = messages("TP001")
    r = client.post("/simulator/TP001/reply", headers=ANITA, json={"button": "booked", "in_reply_to": msg["message_id"]})
    assert r.json()["handling"] == ["saved_no_action"]
    assert cards(client, "TP001")[0]["state"] == "Due"
    assert scalar("SELECT count(*) FROM handover") == 0


def test_template_missing_blocks_instead_of_falling_back(uc1, client):
    from app import db

    with db.connect() as c:
        c.execute("UPDATE patient SET preferred_language = 'Hindi' WHERE mrn = 'TP001'")
    actions = {a["mrn"]: a["reason_code"] for a in run_to_t7(client)}
    assert actions["TP001"] == "held_template_missing" and messages("TP001") == []


def test_blocked_gap_is_checked_again_next_run(uc1, client):
    run_to_t7(client)
    client.post("/patients/TP002/consent", headers=ANITA, json={
        "consent_reference": "CONS-TP002b", "scopes": ["whatsapp_patient", "automated_messaging"],
        "recorded_on": "2026-11-09"})
    assert {a["mrn"]: a["outcome"] for a in reminder_run(client)} == {"TP002": "sent"}
    assert gaps()["TP002"]["status"] == "reminder_sent"


def test_set_date_moves_forward_only(uc1, client):
    set_date(client, "2026-11-09")
    assert client.post("/clock/set", headers=ANITA, json={"date": "2026-11-08"}).status_code == 422


def test_database_allows_one_reminder_per_gap(uc1, client):
    import psycopg
    import pytest

    run_to_t7(client)
    from app import db

    [msg] = messages("TP001")
    with pytest.raises(psycopg.errors.UniqueViolation):
        with db.connect() as c:
            c.execute(
                """INSERT INTO outbox_message (mrn, recipient, language, template_id, template_version, body, body_en,
                                               gap_id, actor_type, actor, reason_code, status, created_on)
                   SELECT mrn, recipient, language, template_id, template_version, body, body_en, gap_id, actor_type,
                          actor, reason_code, 'sent', created_on FROM outbox_message WHERE message_id = %s""",
                (msg["message_id"],))


def test_worklist_knows_seeded_plans(uc1, client):
    rows = {p["mrn"]: p for p in client.get("/worklist", headers=ANITA).json()["patients"]}
    assert any("not confirmed" in r["txt"] for r in rows["TP001"]["reasons"])
    assert not any("not confirmed" in r["txt"] for r in rows["TP002"]["reasons"] + rows["TP003"]["reasons"])
    assert client.get("/care-plan", headers=ANITA).json()["settings"]["clinic_name"] == "Sanjeevini Clinic"
