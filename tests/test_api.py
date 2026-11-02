"""API tests that need no database: the API must answer even when the DB is stopped."""
import pytest

from conftest import ANAND, PRIYA, SURESH

pytestmark = pytest.mark.usefixtures("offline")


def test_health_reports_db_unreachable_without_failing(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["api"] == "ok"
    assert r.json()["db"] == "unreachable"


def test_start_database_button(client, monkeypatch):
    from app import db

    assert client.post("/demo/start-db").status_code == 409  # not on AWS: nothing to start
    status, started = ["stopped"], []
    monkeypatch.setattr(db, "instance_status", lambda: status[0])
    monkeypatch.setattr(db, "start_instance", lambda: started.append(1) or "starting")
    assert client.post("/demo/start-db").json() == {"db": "starting"} and started == [1]
    status[0] = "starting"  # a second press while it starts does nothing
    assert client.post("/demo/start-db").json() == {"db": "starting"} and started == [1]


def test_users_listed_without_db(client):
    users = client.get("/users").json()
    assert [u["id"] for u in users] == ["u_priya", "u_meera", "u_anand", "u_kavita", "u_suresh",
                                        "u_anita", "u_ravi", "u_sekhar", "u_ananya"]
    assert {u["role"] for u in users} == {"coordinator", "oncologist", "chief"}


def test_unknown_or_missing_user_is_401_without_db(client):
    assert client.post("/session").status_code == 401
    assert client.post("/session", headers={"X-User": "admin"}).status_code == 401
    assert client.get("/patients").status_code == 401


def test_wrong_role_is_403_without_db(client):
    assert client.get("/patients", headers=SURESH).status_code == 403
    assert client.post("/demo/reset", headers=ANAND).status_code == 403


def test_session_needs_db(client):
    assert client.post("/session", headers=PRIYA).status_code == 503


def test_public_api_cannot_start_the_database(client):
    assert client.post("/db/start").status_code in (404, 405)
