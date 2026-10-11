"""API tests that need no database: the API must answer even when the DB is stopped."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

# Point at a closed port so the DB is "unreachable" quickly.
os.environ.update({"DB_HOST": "127.0.0.1", "DB_PORT": "1", "DB_SSLMODE": "disable", "DB_CONNECT_TIMEOUT": "1"})
os.environ.pop("DATABASE_URL", None)
os.environ.pop("DB_INSTANCE_ID", None)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


def test_health_reports_db_unreachable_without_failing():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["api"] == "ok"
    assert r.json()["db"] == "unreachable"


def test_roles_listed_without_db():
    ids = [role["id"] for role in client.get("/roles").json()]
    assert ids == ["coordinator", "oncologist", "chief", "reviewer"]


def test_unknown_role_rejected():
    assert client.post("/session", json={"role": "admin"}).status_code == 422


def test_session_needs_db():
    assert client.post("/session", json={"role": "coordinator"}).status_code == 503


def test_db_start_is_noop_outside_aws():
    assert client.post("/db/start").json() == {"db": "not-managed"}
