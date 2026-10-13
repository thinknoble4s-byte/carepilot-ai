"""The whole of Increment 1 the way the demo runs it, through the API only."""
from conftest import ANAND, PRIYA, SURESH, plan_body, seed_file


def test_increment_1_end_to_end(seeded, client):
    # 1. Reset, then check the summary.
    reset = client.post("/demo/reset", headers=PRIYA).json()
    summary = client.get("/summary", headers=PRIYA).json()
    assert (summary["patients"], summary["agent_on"], summary["open_exceptions"], summary["plans_confirmed"]) == \
        (25, 23, 8, 0)

    # 2. Importing again is safe: nothing new is created.
    reg = client.post("/imports", headers=PRIYA, data={"source_type": "register"},
                      files={"file": ("register.csv", seed_file("register.csv"))}).json()
    assert (reg["rows_created"], reg["rows_skipped_identical"]) == (0, 25)
    client.post("/imports", headers=PRIYA, data={"source_type": "appointments"},
                files={"file": ("appointments.csv", seed_file("appointments.csv"))})
    assert client.get("/summary", headers=PRIYA).json()["patients"] == 25

    # 3. Confirm the golden-path patient's plan.
    r = client.post("/patients/SYN-0007/plan/confirm", headers=PRIYA, json=plan_body("SYN-0007"))
    due = {c["item_code"]: c["due_date"] for c in r.json()["cards"]}
    assert len(due) == 5 and (due["V1"], due["T1"]) == ("2026-11-16", "2026-11-09")

    # 4. A second confirmation is refused.
    assert client.post("/patients/SYN-0007/plan/confirm", headers=PRIYA, json=plan_body("SYN-0007")).status_code == 409
    assert len(client.get("/patients/SYN-0007/cards", headers=ANAND).json()) == 5

    # 5. Oncologist can read the cards; the chief cannot.
    assert client.get("/patients/SYN-0007/cards", headers=SURESH).status_code == 403

    # 6. The chief's summary: aggregates only.
    chief = client.get("/summary", headers=SURESH)
    assert chief.json()["plans_confirmed"] == 1 and chief.json()["cards_by_state"] == {"Due": 5}
    assert "Sunita" not in chief.text and "SYN-" not in chief.text

    # 7. Reset again: same fingerprint, fresh chain.
    again = client.post("/demo/reset", headers=PRIYA).json()
    assert again["fingerprint"] == reset["fingerprint"]
    assert again["audit_rows"] == 27
    assert client.get("/audit").json()["chain"]["valid"]
