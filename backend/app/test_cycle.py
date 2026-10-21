"""Use Case 2T: the test cycle — from "test due" to the doctor's decision on the report.

Plain rules except one AI step (the identifier read on an outside report). CarePilot never reads or judges a
result: it stores the file, checks who it belongs to and shows it to the doctor, who decides.

Hospital road: appointment list row -> Scheduled / Completed; the simulated lab sends a report with an MRN and a
test code -> linked and checked by two identifiers (Report received), or a "Report mismatch" task, or an exception
when no card matches. Only the doctor then sets Report verified (or Verified for a non-specimen test, which has no
lab report).
Outside road: a photo or PDF from the patient is held Unverified; identifiers are read (never results); a
coordinator verifies (two matching identifiers) or rejects with a reason.
In data sets whose settings carry "test_cycle" (Use Case 2T) a checked report also becomes "Report ready for
review" for the doctor: Continue as planned (one approved message naming the next visit) or Needs discussion
(instruction -> Doctor-requested visit card + booking message); the decision verifies the card. After 1 working day
without a decision the item moves to the doctor's cover. The appointment list rows, the simulated lab, outside
reports and the doctor's Verify work in every data set."""
import re
from datetime import date, datetime, time

from psycopg.types.json import Jsonb

from . import audit, clock, messaging, policy, states, tasks
from .agent import AGENT, set_next_action
from .errors import Conflict, Invalid, NotFound
from .reference import USERS_BY_ID

SYSTEM = "CarePilot"
LAB = "Simulated lab (hospital)"
VISION = "Identifier reader (simulated Sarvam Vision)"
IDENTIFIERS = ("name", "age", "sex", "lab_id", "facility", "report_date", "document_type")
CHECKABLE = ("name", "age", "sex", "mrn", "phone", "lab_id")  # what a coordinator can tick as matching
LABELS = {"name": "Name", "age": "Age", "sex": "Sex", "lab_id": "Lab ID", "facility": "Facility",
          "report_date": "Report date", "document_type": "Document type", "mrn": "MRN", "phone": "Phone"}


def config(conn) -> dict | None:
    return policy.settings(conn).get("test_cycle")


# ---------------------------------------------------------------------------------------------------------------
# Synthetic report files (demo only: made-up names, values and labs)
# ---------------------------------------------------------------------------------------------------------------
def _pdf(lines: list[str]) -> bytes:
    """A one-page PDF with plain text lines (no external library)."""
    esc = lambda s: s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    body = "BT /F1 11 Tf 50 790 Td 16 TL " + " ".join(f"({esc(line)}) '" for line in lines) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> "
            "/Contents 5 0 R >>",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            f"<< /Length {len(body)} >>\nstream\n{body}\nendstream"]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def hospital_report_pdf(patient: dict, report_mrn: str, test_code: str, test_name: str, day: date) -> bytes:
    return _pdf(["SANJEEVINI CLINIC LABORATORY - SYNTHETIC DEMO REPORT (not a real patient)",
                 f"Name: {patient['full_name']}", f"MRN: {report_mrn}", f"Test code: {test_code}",
                 f"Test: {test_name}", f"Report date: {day.isoformat()}", "",
                 "Haemoglobin 12.6 g/dL | WBC 6.1 x10^9/L | Platelets 245 x10^9/L (synthetic values)",
                 "", "Electronically released by the laboratory (simulated)."])


def outside_report_pdf(patient: dict, day: date, test_name: str) -> bytes:
    return _pdf(["SUNRISE DIAGNOSTICS (synthetic outside lab) - DEMO DOCUMENT",
                 f"Name: {patient['full_name']}", "Age: 54", "Sex: F", "Lab ID: SD-2026-48213",
                 "Facility: Sunrise Diagnostics, Jayanagar (synthetic)", f"Report date: {day.isoformat()}",
                 f"Document type: Laboratory report - {test_name}", "",
                 "Haemoglobin 12.9 g/dL | WBC 5.8 x10^9/L | Platelets 260 x10^9/L (synthetic values)"])


def _store(conn, data: bytes, mime: str) -> int:
    return conn.execute("INSERT INTO audio_blob (mime, data) VALUES (%s, %s) RETURNING audio_id",
                        (mime, data)).fetchone()["audio_id"]


def _card(conn, mrn: str, test_code: str, prefer: tuple = ("Due", "Scheduled")) -> dict | None:
    """The patient's card for a test code. A plan can hold the same test several times (a blood test at months 3,
    6, 9 and 12): the earliest card in a preferred state wins, then the earliest one not yet verified."""
    return conn.execute(
        """SELECT * FROM order_card WHERE mrn = %s AND card_type = 'test' AND test_code = %s
           ORDER BY state = ANY(%s) DESC, state IN ('Report verified', 'Verified'), due_date, card_id LIMIT 1""",
        (mrn, test_code, list(prefer))).fetchone()


def _batch(conn, source_type: str, user_id: str, file_name: str, rows: int) -> int:
    return conn.execute(
        """INSERT INTO import_batch (source_type, file_name, imported_by, rows_read, rows_created,
                                     rows_skipped_identical, rows_exception)
           VALUES (%s, %s, %s, %s, 0, 0, 0) RETURNING batch_id""", (source_type, file_name, user_id, rows)
    ).fetchone()["batch_id"]


def _exception(conn, batch_id: int, source_type: str, row_number: int, raw: str, reason: str) -> None:
    conn.execute("INSERT INTO import_exception (batch_id, source_type, row_number, raw_row, reason_code) "
                 "VALUES (%s, %s, %s, %s, %s)", (batch_id, source_type, row_number, raw, reason))
    conn.execute("UPDATE import_batch SET rows_exception = rows_exception + 1 WHERE batch_id = %s", (batch_id,))


# ---------------------------------------------------------------------------------------------------------------
# Buttons and booking (REQ-05, REQ-06)
# ---------------------------------------------------------------------------------------------------------------
def tap_booked(conn, card: dict, inbound_id: int, actor: str) -> str:
    """REQ-05: a tap has no date, so a test card cannot be Scheduled. It gets the label "Patient says booked
    (no date)", stays Due, and its G0 gap is closed so no further reminder goes. No task."""
    conn.execute("UPDATE order_card SET patient_says_booked = true WHERE card_id = %s", (card["card_id"],))
    closed = conn.execute("UPDATE gap_event SET status = 'resolved', outcome = 'patient_says_booked' "
                          "WHERE card_id = %s AND status <> 'resolved' RETURNING gap_id", (card["card_id"],)).fetchall()
    audit.append(conn, actor_type="system", actor_name=actor, action="card_label_set", entity_type="order_card",
                 entity_id=str(card["card_id"]), mrn=card["mrn"], reason_code="patient_says_booked",
                 details={"label": "Patient says booked (no date)", "state": card["state"], "inbound_id": inbound_id,
                          "gaps_closed": [g["gap_id"] for g in closed], "task": None})
    set_next_action(conn, card["mrn"], f"{card['name']}: patient says booked (no date); waiting for the date, "
                                       "the appointment list or the report")
    return "test_label_patient_says_booked"


def schedule_by_coordinator(conn, card: dict, day: date, at: time | None, user_id: str) -> None:
    """REQ-06: Record booking on a test card: Due -> Scheduled with the date (never without one)."""
    if card["state"] == "Due":
        states.transition_card(conn, card["card_id"], "Scheduled", "system", SYSTEM, source="coordinator",
                               appointment_date=day.isoformat(), recorded_by=user_id)
    elif card["state"] == "Scheduled":
        conn.execute("UPDATE order_card SET appointment_date = %s WHERE card_id = %s", (day, card["card_id"]))
        audit.append(conn, actor_type="system", actor_name=SYSTEM, action="card_booking_confirmed",
                     entity_type="order_card", entity_id=str(card["card_id"]), mrn=card["mrn"],
                     details={"appointment_date": day.isoformat(), "recorded_by": user_id})
    else:
        raise Conflict(f"The test is already {card['state']}")
    conn.execute("UPDATE order_card SET booking_source = 'coordinator', appointment_time = %s, "
                 "patient_says_booked = false WHERE card_id = %s", (at, card["card_id"]))


# ---------------------------------------------------------------------------------------------------------------
# Appointment list: test rows (REQ-07)
# ---------------------------------------------------------------------------------------------------------------
def load_appointment_rows(conn, user_id: str, rows: list[dict], file_name: str | None = None) -> dict:
    """Test rows of the daily appointment list. A future date: Due -> Scheduled (source appointment list).
    Attended: -> Completed (from Scheduled, or from Due as a walk-in), with the date. The only source of
    Completed. A row that matches no card goes to the exceptions list; nothing is created silently."""
    if not rows:
        raise Invalid("The appointment list has no rows")
    today = clock.get_today(conn)
    name = file_name or f"appointment list (tests) {today.isoformat()}"
    batch = _batch(conn, "test_appointments", user_id, name, len(rows))
    mrns = {str(r.get("mrn") or "") for r in rows}
    audit.append(conn, actor_type="person", actor_name=user_id, action="test_appointment_list_loaded",
                 entity_type="import_batch", entity_id=str(batch),
                 mrn=next(iter(mrns)) if len(mrns) == 1 and conn.execute(
                     "SELECT 1 FROM patient WHERE mrn = %s", (next(iter(mrns)),)).fetchone() else None,
                 details={"rows": len(rows), "file_name": name, "patients": sorted(mrns)})
    moved, exceptions = [], 0
    for n, row in enumerate(rows, start=2):
        raw = ",".join(str(row.get(k, "")) for k in ("mrn", "test_code", "date", "time", "attended"))
        try:
            day = date.fromisoformat(str(row.get("date")))
            at = time.fromisoformat(str(row["time"])) if row.get("time") else None
        except ValueError:
            _exception(conn, batch, "test_appointments", n, raw, "invalid_date")
            exceptions += 1
            continue
        card = _card(conn, str(row.get("mrn") or ""), str(row.get("test_code") or ""))
        attended = bool(row.get("attended"))
        if card is None:
            _exception(conn, batch, "test_appointments", n, raw, "no_matching_test_card")
            exceptions += 1
            continue
        if attended and card["state"] in ("Due", "Scheduled"):
            states.transition_card(conn, card["card_id"], "Completed", "system", SYSTEM, source="appointment_list",
                                   attended_on=day.isoformat(), walk_in=card["state"] == "Due", batch_id=batch)
            conn.execute("UPDATE order_card SET completed_on = %s WHERE card_id = %s", (day, card["card_id"]))
            moved.append({"card_id": card["card_id"], "mrn": card["mrn"], "to": "Completed"})
            set_next_action(conn, card["mrn"], f"{card['name']} done on {day:%d %b}; " + (
                "waiting for the lab report" if card["specimen"] is not False else "waiting for the doctor to verify"))
        elif not attended and day >= today and card["state"] == "Due":
            states.transition_card(conn, card["card_id"], "Scheduled", "system", SYSTEM, source="appointment_list",
                                   appointment_date=day.isoformat(), batch_id=batch)
            conn.execute("UPDATE order_card SET booking_source = 'appointment_list', appointment_time = %s, "
                         "patient_says_booked = false WHERE card_id = %s", (at, card["card_id"]))
            moved.append({"card_id": card["card_id"], "mrn": card["mrn"], "to": "Scheduled"})
        else:
            _exception(conn, batch, "test_appointments", n, raw,
                       "date_in_the_past" if not attended else f"card_is_{card['state'].lower().replace(' ', '_')}")
            exceptions += 1
    conn.execute("UPDATE import_batch SET rows_created = %s WHERE batch_id = %s", (len(moved), batch))
    return {"batch_id": batch, "moved": moved, "exceptions": exceptions}


# ---------------------------------------------------------------------------------------------------------------
# Hospital road: the simulated lab (REQ-09, REQ-10)
# ---------------------------------------------------------------------------------------------------------------
def lab_report(conn, *, mrn: str, test_code: str, report_mrn: str | None = None, with_file: bool = True,
               report_ref: str | None = None, pressed_by: str | None = None) -> dict:
    """The simulated lab sends a report for a patient (MRN) and a test code, with a file. Linked and checked in the
    same run: card Completed + report MRN and test code match -> Report received (the doctor verifies it), and in
    Use Case 2T the doctor's review item. Otherwise the card does not move: a Report mismatch task, or an exception
    (no card). A non-specimen test (imaging) has no lab report."""
    if not with_file:
        raise Invalid("No file, no move: the lab report has no file attached")
    mrn, test_code = (mrn or "").strip(), (test_code or "").strip()
    report_mrn = (report_mrn or mrn).strip()
    if not mrn or not test_code:
        raise Invalid("The lab report needs an MRN and a test code")
    ref = report_ref or f"LAB-{mrn}-{test_code}-{report_mrn}"
    seen = conn.execute("SELECT * FROM test_report WHERE report_ref = %s", (ref,)).fetchone()
    if seen:  # REQ-09: the same report sent twice is attached once
        audit.append(conn, actor_type="system", actor_name=LAB, action="duplicate_suppressed", entity_type="test_report",
                     entity_id=str(seen["report_id"]), mrn=seen["mrn"], outcome="report_already_received",
                     details={"report_ref": ref})
        return {**seen, "duplicate": True}
    patient = conn.execute("SELECT * FROM patient WHERE mrn = %s", (mrn,)).fetchone()
    now, today = clock.get_now(conn), clock.get_today(conn)
    card = _card(conn, mrn, test_code, ("Completed",)) if patient else None
    if card is not None and card["specimen"] is False:
        raise Invalid(f"{card['name']} is not specimen-based: there is no lab report; the doctor verifies it once "
                      "it is Completed")
    if card is None:  # no card for this MRN and test code: the exceptions list, nothing attached
        batch = _batch(conn, "lab_reports", pressed_by, ref, 1)  # demo control: recorded under who pressed it
        _exception(conn, batch, "lab_reports", 2, f"{mrn},{test_code},{report_mrn}", "report_no_matching_card")
        report = conn.execute(
            """INSERT INTO test_report (mrn, road, report_ref, report_mrn, test_code, status, received_at_demo, channel)
               VALUES (%s, 'hospital', %s, %s, %s, 'exception', %s, 'lab feed (simulated)') RETURNING *""",
            (mrn if patient else None, ref, report_mrn, test_code, now)).fetchone() if patient else None
        audit.append(conn, actor_type="system", actor_name=LAB, action="report_unmatched", entity_type="import_batch",
                     entity_id=str(batch), mrn=mrn if patient else None, reason_code="report_no_matching_card",
                     details={"mrn": mrn, "test_code": test_code, "report_mrn": report_mrn, "attached": False})
        return {**(report or {}), "status": "exception", "exception_batch": batch}

    pdf = hospital_report_pdf(patient, report_mrn, test_code, card["name"], today)
    blob = _store(conn, pdf, "application/pdf")
    file_name = f"{ref}.pdf"
    checks = {"mrn": [report_mrn, card["mrn"]], "test_code": [test_code, card["test_code"]],
              "card_state": card["state"]}
    ok = card["state"] == "Completed" and report_mrn == card["mrn"] and test_code == card["test_code"]
    report = conn.execute(
        """INSERT INTO test_report (mrn, road, report_ref, report_mrn, test_code, blob_id, file_name, mime, status,
                                    card_id, received_at_demo, channel, mismatch)
           VALUES (%s, 'hospital', %s, %s, %s, %s, %s, 'application/pdf', %s, %s, %s, 'lab feed (simulated)', %s)
           RETURNING *""",
        (mrn, ref, report_mrn, test_code, blob, file_name, "verified" if ok else "mismatch", card["card_id"], now,
         None if ok else Jsonb(checks))).fetchone()
    audit.append(conn, actor_type="system", actor_name=LAB, action="lab_report_received", entity_type="test_report",
                 entity_id=str(report["report_id"]), mrn=mrn,
                 details={"report_ref": ref, "test_code": test_code, "report_mrn": report_mrn, "file": file_name,
                          "simulated": True})
    if not ok:  # REQ-10: the card stays where it is; a person looks at it; nobody sees it as verified
        why = ("the card is not Completed" if card["state"] != "Completed"
               else "the MRN on the report does not match" if report_mrn != card["mrn"] else "the test code does not match")
        tasks.create_task(conn, mrn=mrn, task_type="report_mismatch", owner_name=card["owner_name"], card_id=card["card_id"],
                          reason=f"Report mismatch for {card['name']}: {why} (report MRN {report_mrn}, test {test_code}; "
                                 f"card {card['mrn']}, {card['test_code']}, {card['state']})",
                          actor_type="system", actor=SYSTEM, dedupe_key=f"report_mismatch:{report['report_id']}")
        audit.append(conn, actor_type="system", actor_name=SYSTEM, action="report_mismatch", entity_type="test_report",
                     entity_id=str(report["report_id"]), mrn=mrn, reason_code="report_mismatch",
                     details={**checks, "why": why, "card_moved": False})
        set_next_action(conn, mrn, f"Report mismatch on {card['name']}: coordinator to check")
        return report
    states.transition_card(conn, card["card_id"], "Report received", "system", SYSTEM, file_ref=file_name,
                           reason_code="report_linked", report_id=report["report_id"])
    audit.append(conn, actor_type="system", actor_name=SYSTEM, action="report_linked", entity_type="test_report",
                 entity_id=str(report["report_id"]), mrn=mrn, reason_code="report_linked",
                 details={"card_id": card["card_id"], "file": file_name})
    conn.execute("UPDATE order_card SET report_road = 'hospital' WHERE card_id = %s", (card["card_id"],))
    _report_ready(conn, card, report, "hospital")
    return report


# ---------------------------------------------------------------------------------------------------------------
# Outside road: Unverified document, identifier read, verify or reject (REQ-11 to REQ-13)
# ---------------------------------------------------------------------------------------------------------------
def _validate_identifiers(out: dict) -> dict:
    """REQ-12: the extraction schema has no result fields; any other field rejects the whole output."""
    if not isinstance(out, dict) or set(out) - set(IDENTIFIERS):
        raise ValueError(f"unexpected fields: {sorted(set(out) - set(IDENTIFIERS)) if isinstance(out, dict) else out}")
    return {k: (str(v).strip() if v is not None else None) for k, v in out.items()}


def read_identifiers(data: bytes, mime: str) -> dict:
    """Simulated Sarvam Vision: reads only identifier lines from the document. A file it cannot read returns
    nothing (the coordinator reads it herself); nothing is guessed."""
    if not mime.startswith("application/pdf"):
        raise ValueError("cannot read this file type in the demo reader")
    text = data.decode("latin-1", errors="ignore")
    keys = {"Name": "name", "Age": "age", "Sex": "sex", "Lab ID": "lab_id", "Facility": "facility",
            "Report date": "report_date", "Document type": "document_type"}
    out = {}
    for label, key in keys.items():
        m = re.search(r"\(" + re.escape(label) + r": ([^)]*)\)", text)
        if m:
            out[key] = m.group(1)
    if not out:
        raise ValueError("no identifiers found")
    return _validate_identifiers(out)


def receive_outside(conn, mrn: str, *, data: bytes, mime: str, file_name: str, channel: str,
                    inbound_id: int | None = None, actor: str = SYSTEM) -> dict:
    """REQ-11: saved and held Unverified, tied to the sender, with channel and received date. Moves no card."""
    if not data:
        raise Invalid("The file is empty")
    if not (mime.startswith("image/") or mime == "application/pdf"):
        raise Invalid("Only a photo or a PDF can be sent as a report")
    patient = conn.execute("SELECT * FROM patient WHERE mrn = %s", (mrn,)).fetchone()
    if patient is None:
        raise NotFound(f"Patient {mrn} not found")
    now = clock.get_now(conn)
    blob = _store(conn, data, mime)
    report = conn.execute(
        """INSERT INTO test_report (mrn, road, blob_id, file_name, mime, status, received_at_demo, inbound_id, channel)
           VALUES (%s, 'outside', %s, %s, %s, 'unverified', %s, %s, %s) RETURNING *""",
        (mrn, blob, file_name, mime, now, inbound_id, channel)).fetchone()
    audit.append(conn, actor_type="system", actor_name=actor, action="outside_report_received", entity_type="test_report",
                 entity_id=str(report["report_id"]), mrn=mrn, outcome="unverified",
                 details={"file": file_name, "channel": channel, "inbound_id": inbound_id, "card_moved": False})
    # REQ-12: the only AI step in this use case — identifiers only, validated, never a result.
    try:
        ids, failed = read_identifiers(data, mime), False
    except ValueError as e:
        ids, failed = {}, str(e)
    conn.execute("UPDATE test_report SET identifiers = %s, ai_model = %s, ai_failed = %s WHERE report_id = %s",
                 (Jsonb(ids), "simulated-vision-identifiers-v1", bool(failed), report["report_id"]))
    audit.append(conn, actor_type="ai_suggestion", actor_name=VISION, action="identifiers_read", entity_type="test_report",
                 entity_id=str(report["report_id"]), mrn=mrn, model="simulated-vision-identifiers-v1",
                 outcome="read_failed" if failed else "identifiers_read",
                 details={"identifiers": ids, "error": failed or None, "result_values_read": False})
    # Rules propose the patient (sender's phone and MRN) and the open test card; mismatches are facts.
    card = conn.execute(
        """SELECT * FROM order_card WHERE mrn = %s AND card_type = 'test' AND state IN ('Due', 'Scheduled', 'Completed')
           ORDER BY state = 'Completed' DESC, due_date LIMIT 1""", (mrn,)).fetchone()
    conn.execute("UPDATE test_report SET proposed_card_id = %s WHERE report_id = %s",
                 (card["card_id"] if card else None, report["report_id"]))
    facts = facts_for(patient, ids)
    owner = card["owner_name"] if card else patient["coordinator"]
    task = tasks.create_task(conn, mrn=mrn, task_type="outside_report", owner_name=owner,
                             card_id=card["card_id"] if card else None,
                             reason=f"Outside report: {card['name'] if card else 'no open test card'} — open the file, "
                                    "verify or reject", actor_type="system", actor=SYSTEM,
                             dedupe_key=f"outside_report:{report['report_id']}")
    audit.append(conn, actor_type="system", actor_name=SYSTEM, action="report_match_proposed", entity_type="test_report",
                 entity_id=str(report["report_id"]), mrn=mrn, outcome="proposal",
                 details={"card_id": card["card_id"] if card else None, "facts": facts, "applied": False,
                          "task_id": task["task_id"]})
    set_next_action(conn, mrn, "Outside report received: coordinator to verify or reject")
    return {**report, "identifiers": ids}


def facts_for(patient: dict, ids: dict) -> list[dict]:
    """Matches and mismatches shown as facts (never a verdict). The sender is the patient (same phone)."""
    out = [{"identifier": "phone", "report": "sender's WhatsApp number", "record": patient["phone"], "match": True}]
    if ids.get("name"):
        out.append({"identifier": "name", "report": ids["name"], "record": patient["full_name"],
                    "match": ids["name"].strip().casefold() == patient["full_name"].strip().casefold()})
    for k in ("age", "sex", "lab_id", "facility", "report_date", "document_type"):
        if ids.get(k):
            out.append({"identifier": k, "report": ids[k], "record": None, "match": None})
    return out


def _load_report(conn, report_id: int) -> dict:
    r = conn.execute("SELECT * FROM test_report WHERE report_id = %s FOR UPDATE", (report_id,)).fetchone()
    if r is None:
        raise NotFound(f"Report {report_id} not found")
    return r


def verify_outside(conn, report_id: int, user_id: str, matched: list[str] | None, card_id: int | None = None) -> dict:
    """REQ-13: a person checks the document: a file attached and at least two matching identifiers. The test was
    done outside, so the card moves to Completed and, for a specimen-based test, Report received (each audited) and
    shows "Outside report (checked by coordinator)". The verified status stays with the doctor."""
    r = _load_report(conn, report_id)
    if r["road"] != "outside" or r["status"] != "unverified":
        raise Conflict(f"Report {report_id} is not an unverified outside report ({r['status']})")
    if not r["blob_id"]:
        raise Invalid("No file attached: nothing to verify")
    ticked = sorted({m for m in (matched or []) if m in CHECKABLE})
    if len(ticked) < 2:
        raise Invalid("Verify needs at least two matching identifiers (for example name and age)")
    card = conn.execute("SELECT * FROM order_card WHERE card_id = %s AND mrn = %s AND card_type = 'test' FOR UPDATE",
                        (card_id or r["proposed_card_id"], r["mrn"])).fetchone()
    if card is None:
        raise Invalid("Choose the patient's open test card this report answers")
    if card["state"] not in ("Due", "Scheduled", "Completed"):
        raise Conflict(f"The test card is already {card['state']}")
    now = clock.get_now(conn)
    audit.append(conn, actor_type="person", actor_name=user_id, action="outside_report_verified", entity_type="test_report",
                 entity_id=str(report_id), mrn=r["mrn"], reason_code="outside_report_verified",
                 details={"matched": ticked, "card_id": card["card_id"], "file": r["file_name"]})
    if card["state"] != "Completed":
        states.transition_card(conn, card["card_id"], "Completed", "person", user_id, source="outside_report_verified",
                               report_id=report_id)
        conn.execute("UPDATE order_card SET completed_on = %s WHERE card_id = %s", (now.date(), card["card_id"]))
    if card["specimen"] is not False:
        states.transition_card(conn, card["card_id"], "Report received", "person", user_id,
                               source="outside_report_verified", file_ref=r["file_name"], report_id=report_id)
    conn.execute("UPDATE order_card SET report_road = 'outside', patient_says_booked = false WHERE card_id = %s",
                 (card["card_id"],))
    r = conn.execute("""UPDATE test_report SET status = 'verified', card_id = %s, matched = %s, verified_by = %s,
                        verified_at_demo = %s WHERE report_id = %s RETURNING *""",
                     (card["card_id"], ticked, user_id, now, report_id)).fetchone()
    _close_tasks(conn, report_id, user_id, "Verified")
    _report_ready(conn, card, r, "outside")
    return r


def reject_outside(conn, report_id: int, user_id: str, reason: str | None) -> dict:
    """REQ-13: Reject needs a reason; the card is unchanged and the file is kept; the doctor sees nothing."""
    r = _load_report(conn, report_id)
    if r["road"] != "outside" or r["status"] != "unverified":
        raise Conflict(f"Report {report_id} is not an unverified outside report ({r['status']})")
    if not reason or not reason.strip():
        raise Invalid("Rejecting needs a reason")
    r = conn.execute("UPDATE test_report SET status = 'rejected', rejected_by = %s, reject_reason = %s "
                     "WHERE report_id = %s RETURNING *", (user_id, reason.strip(), report_id)).fetchone()
    audit.append(conn, actor_type="person", actor_name=user_id, action="outside_report_rejected", entity_type="test_report",
                 entity_id=str(report_id), mrn=r["mrn"], outcome="rejected",
                 details={"reason": reason.strip(), "file_kept": True, "card_moved": False})
    _close_tasks(conn, report_id, user_id, f"Rejected: {reason.strip()}")
    set_next_action(conn, r["mrn"], "Outside report rejected; test still open")
    return r


def _close_tasks(conn, report_id: int, user_id: str, note: str) -> None:
    for t in conn.execute("SELECT task_id FROM coordinator_task WHERE dedupe_key = %s AND status = 'open'",
                          (f"outside_report:{report_id}",)).fetchall():
        tasks.complete_task(conn, t["task_id"], user_id, note)


def open_file(conn, report_id: int, user: dict) -> dict:
    """The source file. The doctor sees only verified reports; every opening is audited (REQ-14)."""
    r = conn.execute("SELECT * FROM test_report WHERE report_id = %s", (report_id,)).fetchone()
    if r is None or not r["blob_id"]:
        raise NotFound(f"Report {report_id} has no file")
    if user["role"] == "oncologist" and r["status"] != "verified":
        raise Conflict("The doctor sees a report only once it is verified")
    now = clock.get_now(conn)
    audit.append(conn, actor_type="person", actor_name=user["user_id"], action="report_file_opened",
                 entity_type="test_report", entity_id=str(report_id), mrn=r["mrn"],
                 details={"file": r["file_name"], "status": r["status"], "role": user["role"]})
    if user["role"] == "oncologist":
        conn.execute("UPDATE report_review SET opened_by = %s, opened_at_demo = %s WHERE report_id = %s "
                     "AND opened_by IS NULL", (user["user_id"], now, report_id))
    blob = conn.execute("SELECT data, mime FROM audio_blob WHERE audio_id = %s", (r["blob_id"],)).fetchone()
    return {"data": bytes(blob["data"]), "mime": blob["mime"], "file_name": r["file_name"]}


# ---------------------------------------------------------------------------------------------------------------
# The doctor's review (REQ-14 to REQ-18)
# ---------------------------------------------------------------------------------------------------------------
def _report_ready(conn, card: dict, report: dict, road: str) -> None:
    """A checked report: in Use Case 2T the doctor's review item; elsewhere the card waits for the doctor's Verify."""
    if config(conn):
        _create_review(conn, card, report, road)
    else:
        set_next_action(conn, card["mrn"], f"{card['name']}: report received; waiting for the doctor to verify")


def _create_review(conn, card: dict, report: dict, road: str) -> dict | None:
    rules, now = policy.deadlines(conn), clock.get_now(conn)
    doctor = USERS_BY_ID[rules["default_doctor"]]["name"]
    due = datetime.combine(policy.add_working_days(now.date(), 1), now.time())
    review = conn.execute(
        """INSERT INTO report_review (card_id, report_id, mrn, road, doctor, created_at_demo, due_at_demo)
           VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (report_id) DO NOTHING RETURNING *""",
        (card["card_id"], report["report_id"], card["mrn"], road, doctor, now, due)).fetchone()
    if review:
        audit.append(conn, actor_type="system", actor_name=SYSTEM, action="review_item_created", entity_type="report_review",
                     entity_id=str(review["review_id"]), mrn=card["mrn"],
                     details={"test": card["name"], "road": road, "doctor": doctor, "due": f"{due:%Y-%m-%d %H:%M}",
                              "message_to_patient": None})
        set_next_action(conn, card["mrn"], f"{card['name']} report ready for review by {doctor}")
    return review


def _load_review(conn, review_id: int) -> dict:
    r = conn.execute("SELECT * FROM report_review WHERE review_id = %s FOR UPDATE", (review_id,)).fetchone()
    if r is None:
        raise NotFound(f"Review item {review_id} not found")
    return r


def next_visit(conn, mrn: str) -> dict | None:
    return conn.execute(
        """SELECT * FROM order_card WHERE mrn = %s AND card_type = 'visit' AND state IN ('Due', 'Booked')
           AND coalesce(appointment_date, due_date) >= %s ORDER BY coalesce(appointment_date, due_date) LIMIT 1""",
        (mrn, clock.get_today(conn))).fetchone()


def decide(conn, review_id: int, user: dict, decision: str | None) -> dict:
    """REQ-15/16: Continue as planned or Needs discussion, nothing pre-selected; saved once with name and time."""
    rv = _load_review(conn, review_id)
    if rv["status"] != "open":
        raise Conflict("This report has already been decided")
    if decision not in ("continue", "discussion"):
        raise Invalid("Choose Continue as planned or Needs discussion")
    now = clock.get_now(conn)
    card = conn.execute("SELECT * FROM order_card WHERE card_id = %s", (rv["card_id"],)).fetchone()
    if card["state"] in ("Report received", "Completed"):  # the doctor's decision verifies the test
        card = doctor_verify(conn, card["card_id"], user, via="report_review")
    rv = conn.execute(
        """UPDATE report_review SET decision = %s, decided_by = %s, decided_at_demo = %s, status = %s
           WHERE review_id = %s RETURNING *""",
        (decision, user["user_id"], now, "decided" if decision == "continue" else "awaiting_instruction", review_id)
    ).fetchone()
    audit.append(conn, actor_type="person", actor_name=user["user_id"], action="report_review_decided",
                 entity_type="report_review", entity_id=str(review_id), mrn=rv["mrn"], outcome=decision,
                 details={"test": card["name"], "opened_file_first": rv["opened_by"] is not None, "suggested": None})
    if decision == "discussion":
        set_next_action(conn, rv["mrn"], f"{card['name']} needs discussion: doctor's instruction in the hospital record")
        return rv
    visit = next_visit(conn, rv["mrn"])
    if visit is None:  # REQ-16: no next visit card -> no message; a task for the coordinator instead
        tasks.create_task(conn, mrn=rv["mrn"], task_type="no_next_visit", owner_name=card["owner_name"],
                          card_id=card["card_id"], actor_type="system", actor=SYSTEM,
                          reason=f"{card['name']} reviewed (continue as planned) but there is no next visit card: "
                                 "tell the patient and arrange the next visit",
                          dedupe_key=f"no_next_visit:{review_id}")
        set_next_action(conn, rv["mrn"], "Report reviewed; no next visit card — coordinator to arrange it")
        return rv
    if visit["state"] == "Booked" and visit["appointment_date"]:
        when = f"{visit['appointment_date']:%a %d %b}" + \
            (f" at {visit['appointment_time']:%H:%M}" if visit["appointment_time"] else "")
        template, extra = "report_continue_booked", {"next_visit_date": when}
    else:
        when = f"due by {visit['due_date']:%a %d %b}"
        template, extra = "report_continue_due", {"due_date": f"{visit['due_date']:%a %d %b}"}
    msg = messaging.send_message(conn, mrn=rv["mrn"], template_id=template, actor_type="agent", actor=AGENT,
                                 reason_code="report_reviewed_continue", card_id=card["card_id"], extra=extra)
    conn.execute("UPDATE report_review SET message_id = %s WHERE review_id = %s", (msg["message_id"], review_id))
    set_next_action(conn, rv["mrn"], f"{card['name']} reviewed: continue as planned; next visit {when}")
    return {**rv, "message_id": msg["message_id"]}


def instruction(conn, review_id: int, user: dict, text: str | None, days) -> dict:
    """REQ-17: the instruction in the SIMULATED hospital record creates one Doctor-requested visit card and the
    approved booking message; no "continue as planned" message. The test card stays Report verified."""
    from . import handover

    rv = _load_review(conn, review_id)
    if rv["status"] != "awaiting_instruction":
        raise Conflict("This review is not waiting for an instruction")
    if not text or not text.strip():
        raise Invalid("The instruction text is required")
    if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 365:
        raise Invalid("days must be a whole number from 1 to 365")
    audit.append(conn, actor_type="person", actor_name=user["user_id"], action="instruction_recorded",
                 entity_type="report_review", entity_id=str(review_id), mrn=rv["mrn"],
                 details={"instruction": text.strip(), "days": days, "source": "SIMULATED hospital record"})
    card = handover.create_instruction_card(conn, rv["mrn"], days, review_id=review_id)
    return conn.execute(
        """UPDATE report_review SET instruction = %s, instruction_days = %s, instruction_card = %s, status = 'decided'
           WHERE review_id = %s RETURNING *""", (text.strip(), days, card["card_id"], review_id)).fetchone()


def sweep(conn) -> list[dict]:
    """REQ-18: not decided within 1 working day -> the item moves to the doctor's cover (once), stays visible."""
    if not config(conn):
        return []
    now, covers, out = clock.get_now(conn), policy.deadlines(conn)["covers"], []
    for rv in conn.execute("""SELECT * FROM report_review WHERE status = 'open' AND moved_at_demo IS NULL
                              AND due_at_demo <= %s ORDER BY review_id FOR UPDATE""", (now,)).fetchall():
        cover = covers.get(rv["doctor"])
        if not cover or cover == rv["doctor"]:
            continue
        conn.execute("UPDATE report_review SET doctor = %s, moved_from = %s, moved_at_demo = %s WHERE review_id = %s",
                     (cover, rv["doctor"], rv["due_at_demo"], rv["review_id"]))
        audit.append(conn, actor_type="system", actor_name=SYSTEM, action="review_moved_to_cover",
                     entity_type="report_review", entity_id=str(rv["review_id"]), mrn=rv["mrn"],
                     reason_code="doctor_deadline",
                     details={"from": rv["doctor"], "to": cover, "due": f"{rv['due_at_demo']:%Y-%m-%d %H:%M}",
                              "closed": False, "message_to_patient": None})
        out.append({"review_id": rv["review_id"], "to": cover})
    return out


def doctor_verify(conn, card_id: int, user: dict, via: str = "verify_button") -> dict:
    """Only the doctor sets the verified status. Specimen-based: Report received -> Report verified, with the two
    identifiers already checked on the report. Non-specimen: Completed -> Verified."""
    card = conn.execute("SELECT * FROM order_card WHERE card_id = %s FOR UPDATE", (card_id,)).fetchone()
    if card is None or card["card_type"] != "test":
        raise NotFound(f"Test card {card_id} not found")
    if user["role"] != "oncologist":
        raise Invalid("Only the doctor sets a test to verified")
    if card["state"] == "Report received":
        rep = conn.execute("""SELECT * FROM test_report WHERE card_id = %s AND status = 'verified'
                              ORDER BY report_id DESC LIMIT 1""", (card_id,)).fetchone()
        matched = 0 if rep is None else 2 if rep["road"] == "hospital" else len(rep["matched"] or [])
        states.transition_card(conn, card_id, "Report verified", "person", user["user_id"], role=user["role"],
                               matched_identifiers=matched, report_id=rep["report_id"] if rep else None, via=via)
    elif card["state"] == "Completed" and card["specimen"] is False:
        states.transition_card(conn, card_id, "Verified", "person", user["user_id"], role=user["role"], via=via)
    else:
        raise Conflict(f"{card['name']} is {card['state']}: nothing to verify yet" + (
            " (a specimen-based test needs its report first)" if card["state"] == "Completed" else ""))
    set_next_action(conn, card["mrn"], f"{card['name']} verified by {user['name']}")
    return conn.execute("SELECT * FROM order_card WHERE card_id = %s", (card_id,)).fetchone()


def to_verify(conn) -> list[dict]:
    """The doctor's list: specimen-based tests with a report received, and non-specimen tests completed."""
    return conn.execute(
        """SELECT c.card_id, c.mrn, p.full_name, c.item_code, c.name, c.state, c.specimen, c.completed_on,
                  c.report_road, c.due_date,
                  (SELECT row_to_json(r) FROM (SELECT report_id, file_name, received_at_demo, road FROM test_report
                     WHERE card_id = c.card_id AND status = 'verified' ORDER BY report_id DESC LIMIT 1) r) AS report
           FROM order_card c JOIN patient p USING (mrn)
           WHERE c.card_type = 'test' AND (c.state = 'Report received'
                                           OR (c.state = 'Completed' AND c.specimen IS FALSE))
           ORDER BY c.due_date, c.card_id""").fetchall()


def doctor_items(conn) -> list[dict]:
    """Needs my decision: reports ready for review (open or waiting for the instruction). Never a result value."""
    return conn.execute(
        """SELECT v.*, p.full_name, p.preferred_language, c.name AS test_name, c.test_code, r.received_at_demo,
                  r.file_name, r.verified_by
           FROM report_review v JOIN patient p USING (mrn) JOIN order_card c ON c.card_id = v.card_id
           JOIN test_report r ON r.report_id = v.report_id
           WHERE v.status <> 'decided' ORDER BY v.created_at_demo""").fetchall()


def recent_decisions(conn) -> list[dict]:
    return conn.execute(
        """SELECT v.review_id, v.mrn, p.full_name, c.name AS test_name, v.decision, v.decided_by, v.decided_at_demo,
                  v.instruction, v.road, v.message_id
           FROM report_review v JOIN patient p USING (mrn) JOIN order_card c ON c.card_id = v.card_id
           WHERE v.status = 'decided' ORDER BY v.decided_at_demo DESC LIMIT 20""").fetchall()


def reports_for(conn, mrn: str) -> dict:
    patient = conn.execute("SELECT * FROM patient WHERE mrn = %s", (mrn,)).fetchone()
    if patient is None:
        raise NotFound(f"Patient {mrn} not found")
    rows = conn.execute(
        """SELECT r.report_id, r.mrn, r.road, r.report_ref, r.report_mrn, r.test_code, r.file_name, r.mime, r.status,
                  r.card_id, r.received_at_demo, r.channel, r.identifiers, r.ai_failed, r.proposed_card_id, r.matched,
                  r.verified_by, r.verified_at_demo, r.rejected_by, r.reject_reason, r.mismatch,
                  (r.blob_id IS NOT NULL) AS has_file, c.name AS card_name, pc.name AS proposed_card_name,
                  pc.state AS proposed_card_state
           FROM test_report r LEFT JOIN order_card c ON c.card_id = r.card_id
           LEFT JOIN order_card pc ON pc.card_id = r.proposed_card_id
           WHERE r.mrn = %s ORDER BY r.report_id DESC""", (mrn,)).fetchall()
    return {"reports": [{**r, "facts": facts_for(patient, r["identifiers"] or {}) if r["road"] == "outside" else None}
                        for r in rows],
            "checkable": list(CHECKABLE), "labels": LABELS,
            "open_tests": conn.execute(
                """SELECT card_id, name, test_code, state FROM order_card WHERE mrn = %s AND card_type = 'test'
                   ORDER BY card_id""", (mrn,)).fetchall()}
