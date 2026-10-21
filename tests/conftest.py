"""Shared fixtures.

Tests marked with the `seeded` fixture need TEST_DATABASE_URL (a migrated
PostgreSQL 16 database) and start from a fresh demo reset each time. They
wipe that database's demo data, so never point it at a database you care about.
"""
import csv
import io
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

DSN = os.environ.get("TEST_DATABASE_URL")
# Tests never call the real Sarvam API: a deterministic fake stands in (see app/ai.py).
os.environ["AI_PROVIDER"] = "fake"

# The demo data sets seed no plan and offer the three care plans (backend/app/seed_data/care_plan_templates.json),
# which people edit. Engine tests use fixed copies so editing the demo plans does not break them: the Golden Path
# v1.0 schedule (6/12 months, the Step A answer key) with the plan inputs of tests/fixtures/plan_inputs.csv, and
# each use case's documented plan with the plans its start state was scripted from (tests/fixtures/
# usecase_plans.json). Tests marked `demo_template` use the real data sets.
FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXED_TEMPLATE = FIXTURES / "care_plan_template_v1_0.json"
USECASE_PLANS = FIXTURES / "usecase_plans.json"


def pytest_configure(config):
    config.addinivalue_line("markers", "demo_template: use the real demo data sets (the three care plans, no seeded "
                                       "plans), not the fixed copies")


@pytest.fixture(autouse=True)
def fixed_plan_template(request, monkeypatch):
    if request.node.get_closest_marker("demo_template"):
        return
    import json

    from app import reference, seed

    template = json.loads(FIXED_TEMPLATE.read_text(encoding="utf-8"))
    monkeypatch.setattr(reference, "plan_templates", lambda departments=None: [template])
    monkeypatch.setattr(seed, "plan_templates", lambda departments=None: [template])
    fixed = json.loads(USECASE_PLANS.read_text(encoding="utf-8"))
    for name, plans in fixed.items():
        real = getattr(reference, name)

        def with_plans(real=real, plans=plans):
            uc = json.loads(json.dumps(real()))
            uc.update({k: v for k, v in plans.items() if k != "plans"})
            for pt in uc["patients"]:
                pt["plan"] = plans["plans"][pt["mrn"]]
            return uc

        monkeypatch.setattr(reference, name, with_plans)
        monkeypatch.setattr(seed, name, with_plans)


def users_header(user_id: str) -> dict:
    return {"X-User": user_id}


PRIYA = users_header("u_priya")
MEERA = users_header("u_meera")
ANAND = users_header("u_anand")
SURESH = users_header("u_suresh")


@pytest.fixture()
def offline(monkeypatch):
    """Point the API at a closed port so the database is 'unreachable' quickly."""
    for key, value in {"DB_HOST": "127.0.0.1", "DB_PORT": "1", "DB_SSLMODE": "disable",
                       "DB_CONNECT_TIMEOUT": "1"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DB_INSTANCE_ID", raising=False)


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


@pytest.fixture()
def seeded(monkeypatch):
    if not DSN:
        pytest.skip("TEST_DATABASE_URL not set")
    monkeypatch.setenv("DATABASE_URL", DSN)
    from app import db, seed

    with db.connect() as c:
        result = seed.reset(c, scenario="step_a")
    return result


@pytest.fixture()
def uc2a(monkeypatch):
    """Use Case 2a "after Use Case 1" data set."""
    if not DSN:
        pytest.skip("TEST_DATABASE_URL not set")
    monkeypatch.setenv("DATABASE_URL", DSN)
    from app import db, seed

    with db.connect() as c:
        return seed.reset(c, scenario="uc2a")


@pytest.fixture()
def uc2b(monkeypatch):
    """Use Case 2b "after Use Case 1" data set."""
    if not DSN:
        pytest.skip("TEST_DATABASE_URL not set")
    monkeypatch.setenv("DATABASE_URL", DSN)
    from app import db, seed

    with db.connect() as c:
        return seed.reset(c, scenario="uc2b")


@pytest.fixture()
def uc2c(monkeypatch):
    """Use Case 2c "after Use Case 2a, step 6" data set."""
    if not DSN:
        pytest.skip("TEST_DATABASE_URL not set")
    monkeypatch.setenv("DATABASE_URL", DSN)
    from app import db, seed

    with db.connect() as c:
        return seed.reset(c, scenario="uc2c")


@pytest.fixture()
def uc1(monkeypatch):
    """Use Case 1 data set (the default demo reset)."""
    if not DSN:
        pytest.skip("TEST_DATABASE_URL not set")
    monkeypatch.setenv("DATABASE_URL", DSN)
    from app import db, seed

    with db.connect() as c:
        return seed.reset(c)


@pytest.fixture()
def conn(seeded):
    """A connection on the freshly seeded database; rolled back at the end."""
    from app import db

    with db.connect() as c:
        yield c
        c.rollback()


def scalar(sql: str, *params):
    from app import db

    with db.connect() as c:
        row = c.execute(sql, params).fetchone()
    return next(iter(row.values()))


def plan_inputs() -> dict[str, dict]:
    """Golden Path plan inputs for the fixed v1.0 template (Step A answer key)."""
    return {r["mrn"]: r for r in csv.DictReader(io.StringIO((FIXTURES / "plan_inputs.csv").read_text(encoding="utf-8")))}


def plan_body(mrn: str, **overrides) -> dict:
    row = plan_inputs()[mrn]
    body = {
        "template_id": row["template_id"],
        "therapy": row["therapy"],
        "treatment_end_date": row["treatment_end_date"],
        "identifiers_checked": row["identifiers_checked"].split(";"),
    }
    return {**body, **overrides}


def seed_file(name: str) -> bytes:
    from app.reference import read_seed

    return read_seed(name).encode()


def confirm_plan(client, mrn: str) -> dict:
    r = client.post(f"/patients/{mrn}/plan/confirm", headers=PRIYA, json=plan_body(mrn))
    assert r.status_code == 201, r.text
    return r.json()


def advance(client, days: int) -> str:
    return client.post("/clock/advance", headers=PRIYA, json={"days": days}).json()["today"]


def morning(client) -> dict:
    r = client.post("/demo/morning-run", headers=PRIYA)
    assert r.status_code == 200, r.text
    return r.json()


def outbox(mrn: str) -> list[dict]:
    from app import db

    with db.connect() as c:
        return c.execute("SELECT * FROM outbox_message WHERE mrn = %s ORDER BY message_id", (mrn,)).fetchall()


def last_audit(action: str) -> dict:
    from app import db

    with db.connect() as c:
        return c.execute("SELECT * FROM audit_ledger WHERE action = %s ORDER BY id DESC LIMIT 1", (action,)).fetchone()
