"""A2: import the patient register and the appointment list (CSV).

Good rows become patients or appointments; every bad row goes on the
exceptions list with a reason. Nothing is created silently, and the import
only creates: it never updates an existing patient. The whole import is one
transaction (the caller's), with one audit row per import.

This module is the importer adapter: a hospital feed can later replace the CSV
parsing while the checks and writes stay the same.
"""
import csv
import re
from datetime import date

from . import audit
from .errors import Conflict, Invalid, NotFound
from .reference import DEPARTMENTS, LANGUAGES

MRN_RE = re.compile(r"^SYN-\d{4}$")
PHONE_RE = re.compile(r"^\+91[6-9]\d{9}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

REGISTER_COLUMNS = ["mrn", "full_name", "phone", "caregiver_name", "caregiver_phone", "preferred_language"]
APPOINTMENT_COLUMNS = ["mrn", "appointment_date", "department"]


def parse_csv(file_bytes: bytes, required: list[str]) -> list[tuple[int, str, dict]]:
    """Return (row_number, raw_line, values) per data row; header is line 1."""
    try:
        text = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise Invalid("File is not UTF-8 text")
    lines = text.splitlines()
    if not lines:
        raise Invalid("File is empty")
    try:
        header = [h.strip() for h in next(csv.reader([lines[0]]))]
    except csv.Error:
        raise Invalid("File cannot be read as CSV")
    missing = [c for c in required if c not in header]
    if missing:
        raise Invalid(f"Missing column(s): {', '.join(missing)}")
    rows = []
    for number, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue
        try:
            values = next(csv.reader([line]))
        except csv.Error:
            raise Invalid(f"Line {number} cannot be read as CSV")
        row = {col: (values[i].strip() if i < len(values) else "") for i, col in enumerate(header)}
        rows.append((number, line, row))
    return rows


def _real_date(text: str) -> date | None:
    if not DATE_RE.match(text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _start_batch(conn, source_type: str, user_id: str, file_name: str | None) -> int:
    return conn.execute(
        """
        INSERT INTO import_batch (source_type, file_name, imported_by, rows_read, rows_created,
                                  rows_skipped_identical, rows_exception)
        VALUES (%s, %s, %s, 0, 0, 0, 0) RETURNING batch_id
        """,
        (source_type, file_name, user_id),
    ).fetchone()["batch_id"]


def _finish(conn, source_type, user_id, file_name, batch_id, rows_read, created, skipped, exceptions) -> dict:
    for number, raw, reason in exceptions:
        conn.execute(
            """
            INSERT INTO import_exception (batch_id, source_type, row_number, raw_row, reason_code)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (batch_id, source_type, number, raw, reason),
        )
    counts = {
        "rows_read": rows_read,
        "rows_created": created,
        "rows_skipped_identical": skipped,
        "rows_exception": len(exceptions),
    }
    conn.execute(
        """
        UPDATE import_batch SET rows_read = %(rows_read)s, rows_created = %(rows_created)s,
               rows_skipped_identical = %(rows_skipped_identical)s, rows_exception = %(rows_exception)s
        WHERE batch_id = %(batch_id)s
        """,
        {**counts, "batch_id": batch_id},
    )
    audit.append(
        conn,
        actor_type="person",
        actor_name=user_id,
        action="register_imported" if source_type == "register" else "appointments_imported",
        entity_type="import_batch",
        entity_id=str(batch_id),
        details={**counts, "file_name": file_name},
    )
    return {
        "batch_id": batch_id,
        **counts,
        "exceptions": [{"row_number": n, "reason_code": r, "raw_row": raw} for n, raw, r in exceptions],
    }


def _register_reason(row: dict, accepted: set[str]) -> str | None:
    """Checks 1-7, in order; the first failure decides the reason code."""
    mrn = row["mrn"]
    if not mrn:
        return "missing_mrn"
    if not MRN_RE.match(mrn):
        return "bad_mrn_format"
    if mrn in accepted:
        return "duplicate_in_file"
    if not row["full_name"]:
        return "missing_name"
    if not PHONE_RE.match(row["phone"]):
        return "malformed_phone"
    if row["caregiver_phone"] and not PHONE_RE.match(row["caregiver_phone"]):
        return "malformed_caregiver_phone"
    if row["preferred_language"] not in LANGUAGES:
        return "unsupported_language"
    return None


def import_register(conn, user_id: str, file_bytes: bytes, file_name: str | None = None) -> dict:
    rows = parse_csv(file_bytes, REGISTER_COLUMNS)
    batch_id = _start_batch(conn, "register", user_id, file_name)
    accepted: set[str] = set()
    created = skipped = 0
    exceptions = []
    for number, raw, row in rows:
        reason = _register_reason(row, accepted)
        if reason:
            exceptions.append((number, raw, reason))
            continue
        accepted.add(row["mrn"])
        values = (
            row["mrn"], row["full_name"], row["phone"],
            row["caregiver_name"] or None, row["caregiver_phone"] or None, row["preferred_language"],
        )
        existing = conn.execute(
            """
            SELECT mrn, full_name, phone, caregiver_name, caregiver_phone, preferred_language
            FROM patient WHERE mrn = %s
            """,
            (row["mrn"],),
        ).fetchone()
        if existing is not None:
            if tuple(existing.values()) == values:
                skipped += 1  # check 8: identical, not an exception
            else:
                exceptions.append((number, raw, "duplicate_existing"))  # check 9
            continue
        conn.execute(
            """
            INSERT INTO patient (mrn, full_name, phone, caregiver_name, caregiver_phone, preferred_language, batch_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (*values, batch_id),
        )
        created += 1
    return _finish(conn, "register", user_id, file_name, batch_id, len(rows), created, skipped, exceptions)


def _appointment_reason(conn, row: dict) -> str | None:
    if not row["mrn"]:
        return "missing_mrn"
    if conn.execute("SELECT 1 FROM patient WHERE mrn = %s", (row["mrn"],)).fetchone() is None:
        return "unknown_mrn"
    when = _real_date(row["appointment_date"])
    if when is None:
        return "bad_date"
    if row["department"] not in DEPARTMENTS:
        return "unknown_department"
    if conn.execute(
        "SELECT 1 FROM appointment WHERE mrn = %s AND appointment_date = %s AND department = %s",
        (row["mrn"], when, row["department"]),
    ).fetchone():
        return "duplicate"
    return None


def import_appointments(conn, user_id: str, file_bytes: bytes, file_name: str | None = None) -> dict:
    rows = parse_csv(file_bytes, APPOINTMENT_COLUMNS)
    batch_id = _start_batch(conn, "appointments", user_id, file_name)
    created = 0
    exceptions = []
    for number, raw, row in rows:
        reason = _appointment_reason(conn, row)
        if reason:
            exceptions.append((number, raw, reason))
            continue
        conn.execute(
            "INSERT INTO appointment (mrn, appointment_date, department, batch_id) VALUES (%s, %s, %s, %s)",
            (row["mrn"], _real_date(row["appointment_date"]), row["department"], batch_id),
        )
        created += 1
    return _finish(conn, "appointments", user_id, file_name, batch_id, len(rows), created, 0, exceptions)


def list_exceptions(conn, status: str | None = "open", batch_id: int | None = None) -> list[dict]:
    return conn.execute(
        """
        SELECT exception_id, batch_id, source_type, row_number, raw_row, reason_code, status,
               dismissed_by, dismissed_reason, created_at
        FROM import_exception
        WHERE (%(status)s::text IS NULL OR status = %(status)s)
          AND (%(batch_id)s::bigint IS NULL OR batch_id = %(batch_id)s)
        ORDER BY exception_id
        """,
        {"status": status, "batch_id": batch_id},
    ).fetchall()


def dismiss_exception(conn, user_id: str, exception_id: int, reason: str | None) -> dict:
    if not reason or not reason.strip():
        raise Invalid("A reason is required to dismiss an exception")
    row = conn.execute(
        "SELECT exception_id, status FROM import_exception WHERE exception_id = %s FOR UPDATE", (exception_id,)
    ).fetchone()
    if row is None:
        raise NotFound(f"Exception {exception_id} not found")
    if row["status"] != "open":
        raise Conflict(f"Exception {exception_id} is already {row['status']}")
    updated = conn.execute(
        """
        UPDATE import_exception SET status = 'dismissed', dismissed_by = %s, dismissed_reason = %s
        WHERE exception_id = %s
        RETURNING exception_id, batch_id, source_type, row_number, raw_row, reason_code, status,
                  dismissed_by, dismissed_reason
        """,
        (user_id, reason.strip(), exception_id),
    ).fetchone()
    audit.append(
        conn,
        actor_type="person",
        actor_name=user_id,
        action="exception_dismissed",
        entity_type="import_exception",
        entity_id=str(exception_id),
        reason_code=updated["reason_code"],
        details={"reason": reason.strip(), "row_number": updated["row_number"], "source_type": updated["source_type"]},
    )
    return updated
