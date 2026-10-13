"""A7: each role does only its own work; the chief sees aggregates only."""
import pytest

from conftest import ANAND, MEERA, PRIYA, SURESH, plan_body, plan_inputs, scalar


@pytest.mark.parametrize("method, path, kwargs", [
    ("post", "/imports", {"data": {"source_type": "register"}, "files": {"file": ("x.csv", "mrn\n")}}),
    ("post", "/patients/SYN-0007/consent", {"json": {"consent_reference": "C", "scopes": ["whatsapp_patient"],
                                                      "recorded_on": "2026-11-02"}}),
    ("post", "/patients/SYN-0007/plan/confirm", {"json": plan_body("SYN-0007")}),
    ("post", "/demo/reset", {}),
    ("post", "/clock/advance", {"json": {"days": 7}}),
    ("get", "/exceptions", {}),
    ("get", "/tasks", {}),
])
def test_oncologist_cannot_change_records(seeded, client, method, path, kwargs):
    audits, patients = scalar("SELECT count(*) FROM audit_ledger"), scalar("SELECT count(*) FROM patient")
    assert getattr(client, method)(path, headers=ANAND, **kwargs).status_code == 403
    assert scalar("SELECT count(*) FROM audit_ledger") == audits
    assert scalar("SELECT count(*) FROM patient") == patients


@pytest.mark.parametrize("path", ["/patients", "/patients/SYN-0007", "/patients/SYN-0007/cards",
                                  "/templates/BC-SURV-1"])
def test_oncologist_can_read_patients(seeded, client, path):
    assert client.get(path, headers=ANAND).status_code == 200


@pytest.mark.parametrize("path", ["/patients", "/patients/SYN-0007", "/patients/SYN-0007/cards",
                                  "/templates/BC-SURV-1"])
def test_chief_cannot_see_patients(seeded, client, path):
    assert client.get(path, headers=SURESH).status_code == 403


def test_chief_summary_is_aggregate_only(seeded, client):
    r = client.get("/summary", headers=SURESH)
    assert r.status_code == 200
    body = r.json()
    assert {k: body[k] for k in ("patients", "agent_on", "open_exceptions", "plans_confirmed")} == \
        {"patients": 25, "agent_on": 23, "open_exceptions": 8, "plans_confirmed": 0}
    assert body["cards_by_state"] == {}
    text = r.text
    assert "SYN-" not in text
    from app.reference import read_seed

    names = [line.split(",")[1] for line in read_seed("register.csv").splitlines()[1:]]
    assert not any(name in text for name in names)


def test_summary_after_all_plans(seeded, client):
    for mrn in plan_inputs():
        client.post(f"/patients/{mrn}/plan/confirm", headers=PRIYA, json=plan_body(mrn))
    body = client.get("/summary", headers=SURESH).json()
    assert body["plans_confirmed"] == 25
    assert body["cards_by_state"] == {"Due": 125}


def test_second_coordinator_has_same_rights(seeded, client):
    assert client.post("/patients/SYN-0007/plan/confirm", headers=MEERA, json=plan_body("SYN-0007")).status_code == 201
    assert client.get("/exceptions", headers=MEERA).status_code == 200


def test_patient_list_fields(seeded, client):
    rows = {p["mrn"]: p for p in client.get("/patients", headers=PRIYA).json()}
    assert len(rows) == 25
    assert rows["SYN-0013"]["consent_status"] == "none"
    assert rows["SYN-0003"]["consent_status"] == "no_automated_messaging"
    assert rows["SYN-0007"]["consent_status"] == "automated_ok"
    assert rows["SYN-0007"]["has_plan"] is False
    assert client.get("/patients/SYN-0999", headers=PRIYA).status_code == 404
    detail = client.get("/patients/SYN-0003", headers=PRIYA).json()
    assert detail["consent"]["scopes"] == ["whatsapp_patient"]
    assert [t["task_type"] for t in detail["open_tasks"]] == ["consent_scope_missing"]
