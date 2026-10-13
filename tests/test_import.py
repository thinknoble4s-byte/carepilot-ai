"""A2: register and appointment import, exceptions, nothing created silently."""
import pytest

from conftest import PRIYA, scalar, seed_file

REGISTER_HEADER = "mrn,full_name,phone,caregiver_name,caregiver_phone,preferred_language\n"


@pytest.fixture()
def blank(conn):
    """Seeded database with the imported records removed (the ledger stays)."""
    conn.execute("DELETE FROM coordinator_task; DELETE FROM consent; DELETE FROM appointment;"
                 "DELETE FROM import_exception; DELETE FROM patient; DELETE FROM import_batch")
    return conn


def audit_count(c, action=None):
    sql = "SELECT count(*) AS n FROM audit_ledger" + (" WHERE action = %s" if action else "")
    return c.execute(sql, (action,) if action else ()).fetchone()["n"]


def codes(result):
    return [(e["row_number"], e["reason_code"]) for e in result["exceptions"]]


def test_register_import(blank):
    from app import importer

    before = audit_count(blank)
    r = importer.import_register(blank, "u_priya", seed_file("register.csv"), "register.csv")
    assert (r["rows_read"], r["rows_created"], r["rows_skipped_identical"], r["rows_exception"]) == (29, 25, 0, 4)
    assert codes(r) == [(14, "duplicate_in_file"), (21, "malformed_phone"), (26, "missing_mrn"),
                        (29, "unsupported_language")]
    assert blank.execute("SELECT count(*) AS n FROM patient").fetchone()["n"] == 25
    assert blank.execute("SELECT count(*) AS n FROM patient WHERE mrn IN ('SYN-0026', 'SYN-0028')").fetchone()["n"] == 0
    assert audit_count(blank) == before + 1
    assert audit_count(blank, "register_imported") == 2  # seed + this one


def test_appointment_import(blank):
    from app import importer

    importer.import_register(blank, "u_priya", seed_file("register.csv"))
    r = importer.import_appointments(blank, "u_priya", seed_file("appointments.csv"))
    assert (r["rows_read"], r["rows_created"], r["rows_exception"]) == (14, 10, 4)
    assert codes(r) == [(7, "unknown_mrn"), (11, "bad_date"), (13, "duplicate"), (15, "unknown_mrn")]


def test_reimport_creates_nothing(conn):
    from app import importer

    r = importer.import_register(conn, "u_priya", seed_file("register.csv"))
    assert (r["rows_created"], r["rows_skipped_identical"], r["rows_exception"]) == (0, 25, 4)
    assert conn.execute("SELECT count(*) AS n FROM patient").fetchone()["n"] == 25


def test_changed_phone_is_duplicate_existing(conn):
    from app import importer

    r = importer.import_register(conn, "u_priya",
                                 (REGISTER_HEADER + "SYN-0012,Farida Khan,+919000099999,Imran Khan,+919000100012,Hindi\n").encode())
    assert codes(r) == [(2, "duplicate_existing")]
    assert conn.execute("SELECT phone FROM patient WHERE mrn = 'SYN-0012'").fetchone()["phone"] == "+919000000012"


@pytest.mark.parametrize("row, code", [
    ("SYN-31,Test Person,+919000000031,,,Hindi", "bad_mrn_format"),
    ("SYN-0031,,+919000000031,,,Hindi", "missing_name"),
    ("SYN-0031,Test Person,+919000000031,Carer,12345,Hindi", "malformed_caregiver_phone"),
])
def test_register_checks(conn, row, code):
    from app import importer

    assert codes(importer.import_register(conn, "u_priya", (REGISTER_HEADER + row + "\n").encode())) == [(2, code)]


def test_unknown_department(conn):
    from app import importer

    r = importer.import_appointments(conn, "u_priya", b"mrn,appointment_date,department\nSYN-0007,2026-11-16,Cardiology\n")
    assert codes(r) == [(2, "unknown_department")]


def test_failed_audit_write_keeps_nothing(seeded, client, monkeypatch):
    from app import importer

    def broken(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(importer.audit, "append", broken)
    batches, audits = scalar("SELECT count(*) FROM import_batch"), scalar("SELECT count(*) FROM audit_ledger")
    with pytest.raises(RuntimeError):
        client.post("/imports", headers=PRIYA, data={"source_type": "register"},
                    files={"file": ("one.csv", REGISTER_HEADER + "SYN-0050,New Person,+919000000050,,,Hindi\n")})
    assert scalar("SELECT count(*) FROM patient WHERE mrn = 'SYN-0050'") == 0
    assert scalar("SELECT count(*) FROM import_batch") == batches
    assert scalar("SELECT count(*) FROM audit_ledger") == audits


def test_upload_endpoint(seeded, client):
    r = client.post("/imports", headers=PRIYA, data={"source_type": "register"},
                    files={"file": ("one.csv", REGISTER_HEADER + "SYN-0050,New Person,+919000000050,,,Hindi\n")})
    assert r.status_code == 201
    assert r.json()["rows_created"] == 1


def test_missing_column_is_422_and_writes_nothing(seeded, client):
    batches = scalar("SELECT count(*) FROM import_batch")
    r = client.post("/imports", headers=PRIYA, data={"source_type": "register"},
                    files={"file": ("bad.csv", "full_name,phone\nA,+919000000001\n")})
    assert r.status_code == 422
    assert scalar("SELECT count(*) FROM import_batch") == batches


def test_dismiss_exception(seeded, client):
    open_list = client.get("/exceptions", headers=PRIYA).json()
    assert len(open_list) == 8
    eid = open_list[0]["exception_id"]
    assert client.post(f"/exceptions/{eid}/dismiss", json={}, headers=PRIYA).status_code == 422
    assert client.post(f"/exceptions/{eid}/dismiss", json={"reason": "  "}, headers=PRIYA).status_code == 422
    r = client.post(f"/exceptions/{eid}/dismiss", json={"reason": "Checked with ward"}, headers=PRIYA)
    assert r.status_code == 200
    assert (r.json()["status"], r.json()["dismissed_by"]) == ("dismissed", "u_priya")
    assert len(client.get("/exceptions", headers=PRIYA).json()) == 7
    assert scalar("SELECT count(*) FROM audit_ledger WHERE action = 'exception_dismissed'") == 1
    assert client.post(f"/exceptions/{eid}/dismiss", json={"reason": "again"}, headers=PRIYA).status_code == 409
