"""Demo reset builds the same starting state every time (Step A section 4.9, plus the reset entry)."""
from datetime import date

from conftest import ANAND, PRIYA

SEED_STATE = {
    "clock": date(2026, 11, 2), "users": 5, "config_versions": 2, "patients": 25, "appointments": 10,
    "open_exceptions": 8, "import_batches": 2, "consents": 24, "agent_on": 23, "tasks": 1,
    "care_plans": 0, "order_cards": 0,
    "audit_rows": 27,  # demo_reset + 1 register import + 1 appointment import + 24 consents
}


def test_reset_builds_the_seed_state(seeded):
    assert {k: seeded[k] for k in SEED_STATE} == SEED_STATE


def test_reset_is_repeatable(seeded):
    from app import db, seed

    with db.connect() as c:
        c.execute("UPDATE patient SET phone = '+919999999999' WHERE mrn = 'SYN-0007'")
        assert seed.fingerprint(c) != seeded["fingerprint"]
    with db.connect() as c:
        again = seed.reset(c)
    assert again["fingerprint"] == seeded["fingerprint"]
    assert again["audit_rows"] == 27


def test_reset_endpoint_is_coordinator_only(seeded, client):
    r = client.post("/demo/reset", headers=PRIYA)
    assert r.status_code == 200
    assert r.json()["patients"] == 25
    assert r.json()["fingerprint"] == seeded["fingerprint"]
    assert client.post("/demo/reset", headers=ANAND).status_code == 403


def test_reset_entry_names_who_reset(seeded, client):
    client.post("/demo/reset", headers=PRIYA)
    entries = client.get("/audit?limit=200").json()["entries"]
    first = entries[-1]
    assert (first["id"], first["action"], first["actor_name"]) == (1, "demo_reset", "u_priya")
