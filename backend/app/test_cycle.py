"""Use Case 2T v1.4: the test cycle — from "test due" to the doctor's decision on the report.

Plain rules except one AI step (the identifier read on an outside report). CarePilot never reads or judges a
result: it stores the file, scores who it belongs to by plain rules and shows it to the doctor, who decides.

States: Due -> Booked -> Completed -> Report received -> Report verified (a date is optional on Booked; Ordered and
Scheduled are not used). A non-specimen test (imaging) has no lab report: Due -> Booked -> Completed -> Verified,
set by the doctor.
Hospital road: appointment list row -> Booked / Completed; the simulated lab sends a report with an MRN and a test
code -> linked (Report received) and checked by two identifiers (Report verified, by the system), or a "Report
mismatch" task, or an exception when no card matches.
Report check (Use Case 2T): once the test date has passed with no report, the hospital data is looked at first;
if nothing is there the agent asks the patient (Done at an outside lab / Done at the hospital / Not done yet).
Outside road (owner's decision of 25 Oct 2026, replacing REQ-25 and REQ-27 there): "Done at an outside lab" -- on
the report check or as the fourth button of a test reminder -- moves a lab test Booked (or Due) -> Completed and
sends the upload request. A photo or PDF is held Unverified; identifiers are read (never results); eight hard checks
and a rule score out of 100. Name, MRN and test match, every check passes and the score is 95 or more (Live mode):
Report received -> Report verified by the system, with no person confirming it, then the doctor's item. The wrong
kind of report or an unreadable file: the "possible wrong report" message. Anything else: the coordinator verifies
(two matching identifiers) or rejects. In Shadow mode every outside report waits for the coordinator.
In data sets whose settings carry "test_cycle" (Use Case 2T) a report that reaches the doctor becomes "Report
ready for review": Continue as planned (one approved message naming the next visit) or Needs discussion
(instruction -> Doctor-requested visit card + booking message). After 1 working day without a decision the item
moves to the doctor's cover."""
import hashlib
import re
from datetime import date, datetime, time, timedelta

from psycopg.types.json import Jsonb

from . import audit, clock, messaging, policy, states, tasks
from .agent import AGENT, set_next_action
from .errors import Conflict, Forbidden, Invalid, NotFound
from .reference import USERS_BY_ID

SYSTEM = "CarePilot"
LAB = "Simulated lab (hospital)"
VISION = "Identifier reader (simulated Sarvam Vision)"
CHECKER = "CarePilot report check"
IDENTIFIERS = ("name", "mrn", "age", "sex", "lab_id", "facility", "report_date", "document_type")
CHECKABLE = ("name", "age", "sex", "mrn", "phone", "lab_id")  # what a coordinator can tick as matching
LABELS = {"name": "Name", "age": "Age", "sex": "Sex", "lab_id": "Lab ID", "facility": "Facility",
          "report_date": "Report date", "document_type": "Document type", "mrn": "MRN", "phone": "Phone"}
HARD_CHECKS = {"G1": "Report type: the same kind of report as the test (laboratory, or radiology for a scan)",
               "G2": "Test name matches the prescribed test",
               "G3": "Patient's name matches (age and sex, if on the report, do not disagree)",
               "G4": "Report date: not in the future, not long before the card",
               "G5": "Clear: name, report date and document type all read",
               "G6": "Not a duplicate of another patient's file", "G7": "Known sender (patient or listed caregiver)",
               "G8": "MRN on the report matches the patient's MRN"}
OPEN_TEST = ("Due", "Booked")
CHECK_BUTTONS = ("done_outside", "done_hospital", "not_done")
CHECK_LABEL = {"done_outside": "Patient says done at outside lab (report awaited)",
               "done_hospital": "Patient says done at hospital", "not_done": "Patient says not done yet"}
BOOKED_BY_COORDINATOR = "Booked (confirmed by coordinator)"
VERIFIED_BY_COORDINATOR = "Outside report (verified by coordinator)"
DOCTOR_TO_VERIFY = " — doctor to verify"
DONE_OUTSIDE = "Patient says done at outside lab (report awaited)"

# REQ-12 / REQ-29: the rule score and its threshold are a setting held by the clinical lead (proposed values).
DEFAULT_ROUTING = {
    "version": "1.0", "threshold": 95, "mode": "shadow", "clinical_lead": "u_sekhar",
    "weights": {"name": 30, "mrn": 0, "test_name": 25, "document_type": 15, "age_sex": 15, "report_date": 10,
                "sender": 5},
    "report_date_days_before_card": 14, "group_minutes": 10,
    "synonyms": {"CBC": ["blood test", "complete blood count", "full blood count", "cbc", "haemogram", "hemogram"]},
}
DEFAULT_TIMINGS = {"check_after_working_days": 1, "no_answer_working_days": 2, "repeat_after_working_days": 3,
                   "max_repeats": 2, "upload_nudge_working_days": 2, "upload_call_working_days": 2}


def config(conn) -> dict | None:
    return policy.settings(conn).get("test_cycle")


def timings(conn) -> dict:
    return {**DEFAULT_TIMINGS, **((config(conn) or {}).get("report_check") or {})}


def routing(conn) -> dict:
    try:
        body = policy.active(conn, "report_routing", "default")
    except NotFound:
        body = {}
    return {**DEFAULT_ROUTING, **body, "weights": {**DEFAULT_ROUTING["weights"], **(body.get("weights") or {})}}


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


# The synthetic outside-lab documents of the demo (and of the REQ-29 document test set).
VARIANTS = {
    "clear": "Clear photo of the right report",
    "blurred": "Slightly blurred photo (the name is only partly read)",
    "ct_scan": "CT scan report instead of the blood test",
    "other_name": "Report in another person's name",
    "unreadable": "Photo too unclear to read",
    "future_date": "Report dated in the future",
    "no_mrn": "Report without the hospital MRN",
}
OTHER_PERSON, OTHER_MRN = "Pushpa Rao", "HOSP-77812"


def outside_report_pdf(patient: dict, day: date, test_name: str, variant: str = "clear",
                       imaging: bool = False) -> bytes:
    if variant not in VARIANTS:
        raise Invalid("variant must be one of: " + ", ".join(VARIANTS))
    name = OTHER_PERSON if variant == "other_name" else patient["full_name"]
    kind = "Radiology report - CT scan of the chest" if variant == "ct_scan" else \
        f"{'Radiology' if imaging else 'Laboratory'} report - {test_name}"
    when = day + timedelta(days=5) if variant == "future_date" else day
    quality = {"blurred": "slightly blurred", "unreadable": "unreadable"}.get(variant, "clear")
    mrn = [] if variant == "no_mrn" else [f"MRN: {OTHER_MRN if variant == 'other_name' else patient['mrn']}"]
    return _pdf([f"SUNRISE {'IMAGING' if imaging else 'DIAGNOSTICS'} (synthetic outside centre) - DEMO DOCUMENT",
                 f"Name: {name}", *mrn, f"Age: {patient.get('age') or 54}", f"Sex: {patient.get('sex') or 'F'}",
                 "Lab ID: SD-2026-48213", "Facility: Sunrise Diagnostics, Jayanagar (synthetic)",
                 f"Report date: {when.isoformat()}", f"Document type: {kind}", f"Scan quality: {quality}", "",
                 "Findings: synthetic values only" if variant == "ct_scan" else
                 "Haemoglobin 12.9 g/dL | WBC 5.8 x10^9/L | Platelets 260 x10^9/L (synthetic values)"])


def _store(conn, data: bytes, mime: str) -> int:
    return conn.execute("INSERT INTO audio_blob (mime, data) VALUES (%s, %s) RETURNING audio_id",
                        (mime, data)).fetchone()["audio_id"]


def _card(conn, mrn: str, test_code: str, prefer: tuple = OPEN_TEST) -> dict | None:
    """The patient's card for a test code. A plan can hold the same test several times (a blood test at months 3,
    6, 9 and 12): the earliest card in a preferred state wins, then the earliest one not yet verified."""
    return conn.execute(
        """SELECT * FROM order_card WHERE mrn = %s AND card_type = 'test' AND test_code = %s
           ORDER BY state = ANY(%s) DESC, state IN ('Report verified', 'Verified'), due_date, card_id LIMIT 1""",
        (mrn, test_code, list(prefer))).fetchone()


def _label(conn, card_id: int, label: str | None) -> None:
    conn.execute("UPDATE order_card SET label = %s WHERE card_id = %s", (label, card_id))


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
def schedule_by_coordinator(conn, card: dict, day: date, at: time | None, user_id: str) -> None:
    """REQ-06: Record booking on a test card needs a date and time: Due -> Booked (source coordinator), or the date
    is added to a card already Booked from a tap. The card shows "Booked (confirmed by coordinator)"."""
    if card["state"] == "Due":
        states.transition_card(conn, card["card_id"], "Booked", "system", SYSTEM, source="coordinator",
                               appointment_date=day.isoformat(), recorded_by=user_id)
    elif card["state"] == "Booked":
        conn.execute("UPDATE order_card SET appointment_date = %s WHERE card_id = %s", (day, card["card_id"]))
        audit.append(conn, actor_type="system", actor_name=SYSTEM, action="card_booking_confirmed",
                     entity_type="order_card", entity_id=str(card["card_id"]), mrn=card["mrn"],
                     details={"from_source": card["booking_source"], "to_source": "coordinator",
                              "appointment_date": day.isoformat(), "recorded_by": user_id})
    else:
        raise Conflict(f"The test is already {card['state']}")
    conn.execute("UPDATE order_card SET booking_source = 'coordinator', appointment_time = %s, label = %s, "
                 "patient_says_booked = false WHERE card_id = %s", (at, BOOKED_BY_COORDINATOR, card["card_id"]))


# ---------------------------------------------------------------------------------------------------------------
# Appointment list: test rows (REQ-07)
# ---------------------------------------------------------------------------------------------------------------
def load_appointment_rows(conn, user_id: str, rows: list[dict], file_name: str | None = None) -> dict:
    """Test rows of the daily appointment list. A future date: Due -> Booked (source appointment list), or the date
    is added to a card already Booked. Attended: -> Completed (from Booked, or from Due as a walk-in), with the date.
    The only source of Completed. A row that matches no card goes to the exceptions list; nothing is created
    silently."""
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
        if attended and card["state"] in OPEN_TEST:
            states.transition_card(conn, card["card_id"], "Completed", "system", SYSTEM, source="appointment_list",
                                   attended_on=day.isoformat(), walk_in=card["state"] == "Due", batch_id=batch)
            conn.execute("UPDATE order_card SET completed_on = %s, label = NULL WHERE card_id = %s",
                         (day, card["card_id"]))
            moved.append({"card_id": card["card_id"], "mrn": card["mrn"], "to": "Completed"})
            set_next_action(conn, card["mrn"], f"{card['name']} done on {day:%d %b}; " + (
                "waiting for the lab report" if card["specimen"] is not False else "waiting for the doctor to verify"))
        elif not attended and day >= today and card["state"] in OPEN_TEST:
            if card["state"] == "Due":
                states.transition_card(conn, card["card_id"], "Booked", "system", SYSTEM, source="appointment_list",
                                       appointment_date=day.isoformat(), batch_id=batch)
            else:
                conn.execute("UPDATE order_card SET appointment_date = %s WHERE card_id = %s", (day, card["card_id"]))
                audit.append(conn, actor_type="system", actor_name=SYSTEM, action="card_booking_confirmed",
                             entity_type="order_card", entity_id=str(card["card_id"]), mrn=card["mrn"],
                             details={"from_source": card["booking_source"], "to_source": "appointment_list",
                                      "appointment_date": day.isoformat(), "batch_id": batch})
            conn.execute("UPDATE order_card SET booking_source = 'appointment_list', appointment_time = %s, "
                         "label = NULL, patient_says_booked = false WHERE card_id = %s", (at, card["card_id"]))
            moved.append({"card_id": card["card_id"], "mrn": card["mrn"], "to": "Booked"})
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
               report_ref: str | None = None, pressed_by: str | None = None, deliver: bool = True) -> dict:
    """The simulated lab sends a report for a patient (MRN) and a test code, with a file. Linked and checked in the
    same run: card Completed + report MRN and test code match -> Report received -> Report verified (system), and in
    Use Case 2T the doctor's review item. Otherwise the card does not move: a Report mismatch task, or an exception
    (no card). deliver=False puts the report in the hospital data only, where the report check finds it (REQ-24).
    A non-specimen test (imaging) has no lab report."""
    if not with_file:
        raise Invalid("No file, no move: the lab report has no file attached")
    mrn, test_code = (mrn or "").strip(), (test_code or "").strip()
    report_mrn = (report_mrn or mrn).strip()
    if not mrn or not test_code:
        raise Invalid("The lab report needs an MRN and a test code")
    ref = report_ref or f"LAB-{mrn}-{test_code}-{report_mrn}"
    seen = conn.execute("SELECT * FROM test_report WHERE report_ref = %s", (ref,)).fetchone()
    if seen and seen["status"] not in ("verified", "held"):  # not linked before (mismatch / no card): sent again
        linked = conn.execute("SELECT * FROM test_report WHERE report_ref LIKE %s AND status = 'verified' "
                              "ORDER BY report_id LIMIT 1", (f"{ref}-R%",)).fetchone()
        n = conn.execute("SELECT count(*) AS n FROM test_report WHERE report_ref LIKE %s", (f"{ref}%",)).fetchone()["n"]
        seen, ref = linked, f"{ref}-R{n + 1}"
    if seen and seen["status"] == "held" and deliver:  # in the hospital data already: the lab now sends it
        card = _card(conn, mrn, test_code, ("Completed",))
        if card is not None and card["state"] == "Completed":
            return _link_hospital(conn, card, seen)
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
    if not deliver:  # REQ-24: in the hospital system, not sent to CarePilot; the report check looks here first
        report = conn.execute(
            """INSERT INTO test_report (mrn, road, report_ref, report_mrn, test_code, blob_id, file_name, mime, status,
                                        received_at_demo, channel)
               VALUES (%s, 'hospital', %s, %s, %s, %s, %s, 'application/pdf', 'held', %s, 'hospital data (simulated)')
               RETURNING *""", (mrn, ref, report_mrn, test_code, blob, file_name, now)).fetchone()
        audit.append(conn, actor_type="system", actor_name=LAB, action="lab_report_held", entity_type="test_report",
                     entity_id=str(report["report_id"]), mrn=mrn,
                     details={"report_ref": ref, "test_code": test_code, "report_mrn": report_mrn, "file": file_name,
                              "in_hospital_data": True, "sent_to_carepilot": False, "simulated": True})
        return report
    checks = {"mrn": [report_mrn, card["mrn"]], "test_code": [test_code, card["test_code"]],
              "card_state": card["state"]}
    ok = card["state"] == "Completed" and report_mrn == card["mrn"] and test_code == card["test_code"]
    report = conn.execute(
        """INSERT INTO test_report (mrn, road, report_ref, report_mrn, test_code, blob_id, file_name, mime, status,
                                    card_id, received_at_demo, channel, mismatch)
           VALUES (%s, 'hospital', %s, %s, %s, %s, %s, 'application/pdf', %s, %s, %s, 'lab feed (simulated)', %s)
           RETURNING *""",
        (mrn, ref, report_mrn, test_code, blob, file_name, "unverified" if ok else "mismatch", card["card_id"], now,
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
    return _link_hospital(conn, card, report)


def _link_hospital(conn, card: dict, report: dict) -> dict:
    """REQ-09/10: attach the file (Report received), check the two identifiers (MRN and test code) and set
    Report verified, actor System, no AI. Then the doctor's review item (Use Case 2T)."""
    file_name = report["file_name"]
    states.transition_card(conn, card["card_id"], "Report received", "system", SYSTEM, file_ref=file_name,
                           reason_code="report_linked", report_id=report["report_id"])
    audit.append(conn, actor_type="system", actor_name=SYSTEM, action="report_linked", entity_type="test_report",
                 entity_id=str(report["report_id"]), mrn=card["mrn"], reason_code="report_linked",
                 details={"card_id": card["card_id"], "file": file_name})
    states.transition_card(conn, card["card_id"], "Report verified", "system", SYSTEM, matched_identifiers=2,
                           identifiers="mrn, test_code", report_id=report["report_id"])
    now = clock.get_now(conn)
    report = conn.execute("""UPDATE test_report SET status = 'verified', card_id = %s, verified_by = %s,
                             verified_at_demo = %s, matched = %s WHERE report_id = %s RETURNING *""",
                          (card["card_id"], SYSTEM, now, ["mrn", "test_code"], report["report_id"])).fetchone()
    conn.execute("UPDATE order_card SET report_road = 'hospital', label = NULL WHERE card_id = %s", (card["card_id"],))
    _stop_checks(conn, card["card_id"], "report_linked", report["report_id"])
    _report_ready(conn, card, report, "hospital")
    return report


# ---------------------------------------------------------------------------------------------------------------
# Outside road: Unverified document, identifier read, score and hard checks, routing (REQ-11 to REQ-13, 27, 28)
# ---------------------------------------------------------------------------------------------------------------
def _validate_identifiers(out: dict) -> dict:
    """REQ-12: the extraction schema has no result fields; any other field rejects the whole output."""
    if not isinstance(out, dict) or set(out) - set(IDENTIFIERS):
        raise ValueError(f"unexpected fields: {sorted(set(out) - set(IDENTIFIERS)) if isinstance(out, dict) else out}")
    return {k: (str(v).strip() if v is not None else None) for k, v in out.items()}


CLEAR = {"name": 0.97, "mrn": 0.96, "age": 0.95, "sex": 0.95, "lab_id": 0.96, "facility": 0.95, "report_date": 0.97,
         "document_type": 0.97}


def read_identifiers(data: bytes, mime: str) -> tuple[dict, dict]:
    """Simulated Sarvam Vision: reads only identifier lines from the document, and how surely it read each. A file
    it cannot read returns nothing (nothing is guessed); a blurred photo is read only partly."""
    if not mime.startswith("application/pdf"):
        raise ValueError("cannot read this file type in the demo reader")
    text = data.decode("latin-1", errors="ignore")
    if "(Scan quality: unreadable)" in text:
        raise ValueError("too unclear to read")
    keys = {"Name": "name", "MRN": "mrn", "Age": "age", "Sex": "sex", "Lab ID": "lab_id", "Facility": "facility",
            "Report date": "report_date", "Document type": "document_type"}
    out = {}
    for label, key in keys.items():
        m = re.search(r"\(" + re.escape(label) + r": ([^)]*)\)", text)
        if m:
            out[key] = m.group(1)
    if not out:
        raise ValueError("no identifiers found")
    conf = {k: CLEAR[k] for k in out}
    if "(Scan quality: slightly blurred)" in text and out.get("name"):
        out["name"] = out["name"][:max(3, len(out["name"]) // 2)] + "…"
        conf["name"] = 0.80
    return _validate_identifiers(out), conf


def _norm_tokens(name: str) -> list[str]:
    return [t for t in re.sub(r"[^\w\s]", " ", name.casefold()).split() if t]


def _close(a: str, b: str) -> bool:
    """One letter different, added or missing (a spelling variant)."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1 or min(len(a), len(b)) < 3:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) <= 1
    short, long_ = sorted((a, b), key=len)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def name_match(read: str | None, record: str) -> float:
    """1.0 the same name (allowing initials, spelling and name order), 0.6 partly read and agreeing, 0 otherwise."""
    if not read:
        return 0.0
    if read.endswith("…"):
        part = read.rstrip("…").casefold().strip()
        return 0.6 if len(part) >= 3 and record.casefold().startswith(part) else 0.0
    got, want = _norm_tokens(read), _norm_tokens(record)
    if not got or not want:
        return 0.0
    unmatched = list(want)
    for t in got:
        hit = next((w for w in unmatched if _close(t, w) or (len(t) == 1 and w.startswith(t))
                    or (len(w) == 1 and t.startswith(w))), None)
        if hit is None:
            return 0.0
        unmatched.remove(hit)
    return 1.0


def _test_matches(text: str, card: dict, rules: dict) -> bool:
    words = text.casefold()
    names = {card["name"].casefold(), re.sub(r"\s*\(.*\)", "", card["name"]).casefold().strip(),
             (card["test_code"] or "").casefold(), *rules["synonyms"].get(card["test_code"] or "", [])}
    return any(n and n in words for n in names)


def score_report(ids: dict, conf: dict, patient: dict, card: dict, *, sender_known: bool, duplicate_elsewhere: bool,
                 card_created: date, today: date, rules: dict) -> dict:
    """REQ-12: seven hard checks and a rule score out of 100. Plain rules, not an AI opinion; never a result."""
    w = rules["weights"]
    doc = ids.get("document_type") or ""
    kind, _, test_part = doc.partition(" - ")
    is_lab = ("laboratory" in kind.casefold()) if card["specimen"] is not False else \
        any(w in kind.casefold() for w in ("radiology", "imaging"))
    test_ok = bool(doc) and _test_matches(test_part or doc, card, rules)
    name_deg = name_match(ids.get("name"), patient["full_name"])
    age = None if not (ids.get("age") or "").strip().isdigit() or patient.get("age") is None else \
        abs(int(ids["age"]) - int(patient["age"])) <= 1
    sex = None if not ids.get("sex") or not patient.get("sex") else \
        ids["sex"].strip().upper()[:1] == patient["sex"].upper()[:1]
    try:
        rdate = date.fromisoformat(ids.get("report_date") or "")
    except ValueError:
        rdate = None
    earliest = card_created - timedelta(days=int(rules["report_date_days_before_card"]))
    date_ok = rdate is not None and earliest <= rdate <= today
    mrn_read = re.sub(r"\s", "", ids.get("mrn") or "").casefold()
    mrn_ok = bool(mrn_read) and mrn_read == patient["mrn"].casefold()
    legible = all(ids.get(k) for k in ("name", "report_date", "document_type"))
    checks = {"G1": is_lab, "G2": test_ok, "G3": name_deg >= 0.9 and age is not False and sex is not False,
              "G4": date_ok, "G5": legible, "G6": not duplicate_elsewhere, "G7": sender_known, "G8": mrn_ok}
    c = lambda k: float(conf.get(k) or 0)
    parts = {"name": w["name"] * name_deg * c("name"), "mrn": w.get("mrn", 0) * mrn_ok * c("mrn"),
             "test_name": w["test_name"] * test_ok * c("document_type"),
             "document_type": w["document_type"] * is_lab * c("document_type"),
             "age_sex": w["age_sex"] * ((age is True) * c("age") + (sex is True) * c("sex")) / 2,
             "report_date": w["report_date"] * date_ok * c("report_date"),
             "sender": w["sender"] * sender_known}
    yes = lambda b, a, n: a if b else n
    facts = [
        "report type: " + ("not read" if not doc else yes(is_lab, "same kind of report as the test",
                                                           "not the kind of report this test gives")),
        "test name: " + ("not read" if not doc else yes(test_ok, "match", "no match")),
        "name: " + ("not read" if not ids.get("name") else "match" if name_deg >= 0.9 else
                    "partly read" if name_deg > 0 else "no match"),
        "MRN: " + ("not on the report" if not mrn_read else yes(mrn_ok, "match", "no match")),
        "age: " + {True: "match", False: "no match", None: "not compared"}[age],
        "sex: " + {True: "match", False: "no match", None: "not compared"}[sex],
        "report date: " + ("not read" if rdate is None else f"{rdate:%d %b %Y}, " + (
            "in the future" if rdate > today else "too early" if rdate < earliest else "fine")),
        "legible: " + yes(legible, "yes", "no"),
        "duplicate: " + yes(duplicate_elsewhere, "already received for another patient", "no"),
        "sender: " + yes(sender_known, "the patient's own or a listed caregiver's number", "unknown number"),
    ]
    return {"score": int(round(sum(parts.values()))), "parts": {k: round(v, 2) for k, v in parts.items()},
            "checks": checks, "facts": facts}


def route(checks: dict, score: int, threshold: int) -> str:
    """REQ-12: an unknown sender always goes to a person (she is not messaged); the wrong kind of report or an
    unreadable file goes back to the patient; all checks (name, MRN and test match) and the threshold: verified by the
    system and on to the doctor; anything else to the coordinator."""
    if not checks["G7"]:
        return "verify"
    if not (checks["G1"] and checks["G2"] and checks["G5"]):
        return "wrong_report"
    if all(checks.values()) and score >= threshold:
        return "auto_matched"
    return "verify"


def _card_created(conn, card: dict) -> date:
    row = conn.execute("SELECT confirmed_on FROM care_plan WHERE care_plan_id = %s", (card["care_plan_id"],)).fetchone()
    return (row and row["confirmed_on"]) or card["due_date"] - timedelta(days=60)


def _open_check(conn, mrn: str) -> dict | None:
    """The report check this file answers: the latest one sent (or answered Done at an outside lab) and not closed."""
    return conn.execute("""SELECT * FROM report_check WHERE mrn = %s AND status = 'sent' ORDER BY
                           (answer = 'done_outside') DESC NULLS LAST, check_id DESC LIMIT 1""", (mrn,)).fetchone()


def receive_outside(conn, mrn: str, *, data: bytes, mime: str, file_name: str, channel: str,
                    inbound_id: int | None = None, actor: str = SYSTEM, sender_phone: str | None = None,
                    note: str | None = None, note_translation: str | None = None) -> dict:
    """REQ-11: saved and held Unverified, tied to the sender, with channel and received date. It stops the upload
    timers of the open report check. Then the identifier read, the hard checks, the score and the routing."""
    if not data:
        raise Invalid("The file is empty")
    if not (mime.startswith("image/") or mime == "application/pdf"):
        raise Invalid("Only a photo or a PDF can be sent as a report")
    patient = conn.execute("SELECT * FROM patient WHERE mrn = %s", (mrn,)).fetchone()
    if patient is None:
        raise NotFound(f"Patient {mrn} not found")
    now, today, rules = clock.get_now(conn), clock.get_today(conn), routing(conn)
    digest = hashlib.sha256(data).hexdigest()
    blob = _store(conn, data, mime)
    check = _open_check(conn, mrn)
    awaiting = conn.execute("SELECT * FROM report_check WHERE mrn = %s AND status = 'sent' AND answer = 'done_outside' "
                            "ORDER BY check_id DESC", (mrn,)).fetchall()
    report = conn.execute(
        """INSERT INTO test_report (mrn, road, blob_id, file_name, mime, status, received_at_demo, inbound_id, channel,
                                    sender_phone, file_hash, patient_note, note_translation, check_id)
           VALUES (%s, 'outside', %s, %s, %s, 'unverified', %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
        (mrn, blob, file_name, mime, now, inbound_id, channel, sender_phone, digest, note, note_translation,
         check["check_id"] if check else None)).fetchone()
    rid = report["report_id"]
    audit.append(conn, actor_type="system", actor_name=actor, action="outside_report_received", entity_type="test_report",
                 entity_id=str(rid), mrn=mrn, outcome="unverified",
                 details={"file": file_name, "channel": channel, "inbound_id": inbound_id, "card_moved": False,
                          "sender_phone": sender_phone, "report_check": check["check_id"] if check else None})
    # REQ-12: the only AI step in this use case — identifiers only, validated, never a result.
    try:
        (ids, conf), failed = read_identifiers(data, mime), False
    except ValueError as e:
        ids, conf, failed = {}, {}, str(e)
    conn.execute("UPDATE test_report SET identifiers = %s, read_confidence = %s, ai_model = %s, ai_failed = %s "
                 "WHERE report_id = %s",
                 (Jsonb(ids), Jsonb(conf), "simulated-vision-identifiers-v1", bool(failed), rid))
    audit.append(conn, actor_type="ai_suggestion", actor_name=VISION, action="identifiers_read", entity_type="test_report",
                 entity_id=str(rid), mrn=mrn, model="simulated-vision-identifiers-v1",
                 outcome="read_failed" if failed else "identifiers_read",
                 details={"identifiers": ids, "confidence": conf, "error": failed or None, "result_values_read": False})

    # Several photos from one sender within a few minutes are one document: join the one waiting for a person.
    head = conn.execute(
        """SELECT * FROM test_report WHERE mrn = %s AND road = 'outside' AND report_id <> %s AND group_id IS NULL
           AND status = 'unverified' AND routing_applied = 'verify' AND received_at_demo >= %s
           AND coalesce(sender_phone, '') = coalesce(%s, '') ORDER BY report_id DESC LIMIT 1""",
        (mrn, rid, now - timedelta(minutes=int(rules["group_minutes"])), sender_phone)).fetchone()
    if head:
        return _join_group(conn, head, {**report, "identifiers": ids, "read_confidence": conf}, patient, rules)

    # Which test the file answers: of the tests the patient said were done outside, the one the document names;
    # else the open report check's test; else the earliest open lab test (or a scan marked done outside).
    doc = ids.get("document_type") or ""
    for ch in awaiting:
        c = conn.execute("SELECT * FROM order_card WHERE card_id = %s", (ch["card_id"],)).fetchone()
        if c and c["state"] == "Completed" and doc and _test_matches(doc.partition(" - ")[2] or doc, c, rules):
            check = ch
            break
    if check and check["timer_from"]:
        conn.execute("UPDATE report_check SET timer_from = NULL, nudge_due = false, report_id = %s WHERE check_id = %s",
                     (rid, check["check_id"]))
        conn.execute("UPDATE test_report SET check_id = %s WHERE report_id = %s", (check["check_id"], rid))
        audit.append(conn, actor_type="system", actor_name=SYSTEM, action="upload_timer_stopped",
                     entity_type="report_check", entity_id=str(check["check_id"]), mrn=mrn,
                     details={"report_id": rid})
    card = conn.execute("SELECT * FROM order_card WHERE card_id = %s", (check["card_id"],)).fetchone() \
        if check else None
    if card is None or card["state"] not in ("Due", "Booked", "Completed"):
        card = conn.execute(
            """SELECT * FROM order_card WHERE mrn = %s AND card_type = 'test'
               AND (specimen IS NOT FALSE AND state IN ('Due', 'Booked', 'Completed')
                    OR state = 'Completed' AND report_road = 'outside')
               ORDER BY state = 'Completed' DESC, due_date LIMIT 1""",
            (mrn,)).fetchone()
    conn.execute("UPDATE test_report SET proposed_card_id = %s WHERE report_id = %s",
                 (card["card_id"] if card else None, rid))
    if card is None:
        _verify_task(conn, patient, None, {**report, "score": None}, "no open test card")
        return {**report, "identifiers": ids, "routing": "verify"}
    s = _score(conn, report, ids, conf, patient, card, rules)
    proposed = route(s["checks"], s["score"], int(rules["threshold"]))
    applied = proposed if rules["mode"] == "live" else "verify"
    report = conn.execute(
        """UPDATE test_report SET score = %s, score_parts = %s, hard_checks = %s, match_facts = %s, routing = %s,
           routing_mode = %s, routing_applied = %s, report_date = %s WHERE report_id = %s RETURNING *""",
        (s["score"], Jsonb(s["parts"]), Jsonb(s["checks"]), Jsonb(s["facts"]), proposed, rules["mode"], applied,
         s["report_date"], rid)).fetchone()
    audit.append(conn, actor_type="system", actor_name=SYSTEM, action="report_scored", entity_type="test_report",
                 entity_id=str(rid), mrn=mrn, outcome=proposed,
                 details={"score": s["score"], "threshold": rules["threshold"], "checks": s["checks"],
                          "facts": s["facts"], "parts": s["parts"], "mode": rules["mode"], "applied": applied,
                          "card_id": card["card_id"], "routing_version": rules["version"]})
    if applied == "auto_matched":
        _auto_match(conn, card, report)
    elif applied == "wrong_report":
        _wrong_report(conn, card, report)
    else:
        _verify_task(conn, patient, card, report, None if rules["mode"] == "live" else
                     f"Shadow mode: the rules would have {_ROUTED[proposed]}")
    return {**report, "identifiers": ids}


_ROUTED = {"auto_matched": "sent it to the doctor as auto-matched", "wrong_report": "asked the patient for the "
           "right report", "verify": "sent it to the coordinator"}


def _score(conn, report: dict, ids: dict, conf: dict, patient: dict, card: dict, rules: dict) -> dict:
    known = {patient["phone"], patient["caregiver_phone"]} - {None}
    dup = conn.execute("SELECT 1 FROM test_report WHERE file_hash = %s AND mrn <> %s LIMIT 1",
                       (report["file_hash"], patient["mrn"])).fetchone() is not None
    s = score_report(ids, conf, patient, card, sender_known=report["sender_phone"] in known, duplicate_elsewhere=dup,
                     card_created=_card_created(conn, card), today=clock.get_today(conn), rules=rules)
    try:
        s["report_date"] = date.fromisoformat(ids.get("report_date") or "")
    except ValueError:
        s["report_date"] = None
    return s


def _join_group(conn, head: dict, report: dict, patient: dict, rules: dict) -> dict:
    """Photos of one report sent together: one document. The identifiers are merged and the score worked out again
    for the coordinator (the document is still waiting for her)."""
    ids, conf = dict(head["identifiers"] or {}), dict(head["read_confidence"] or {})
    for k, v in (report["identifiers"] or {}).items():
        if v and (not ids.get(k) or float(report["read_confidence"].get(k) or 0) > float(conf.get(k) or 0)):
            ids[k], conf[k] = v, report["read_confidence"][k]
    conn.execute("UPDATE test_report SET group_id = %s, proposed_card_id = %s, routing_applied = 'verify' "
                 "WHERE report_id = %s", (head["report_id"], head["proposed_card_id"], report["report_id"]))
    card = conn.execute("SELECT * FROM order_card WHERE card_id = %s", (head["proposed_card_id"],)).fetchone()
    s = _score(conn, head, ids, conf, patient, card, rules) if card else None
    conn.execute("""UPDATE test_report SET identifiers = %s, read_confidence = %s, score = %s, hard_checks = %s,
                    match_facts = %s, score_parts = %s WHERE report_id = %s""",
                 (Jsonb(ids), Jsonb(conf), s and s["score"], s and Jsonb(s["checks"]), s and Jsonb(s["facts"]),
                  s and Jsonb(s["parts"]), head["report_id"]))
    audit.append(conn, actor_type="system", actor_name=SYSTEM, action="report_grouped", entity_type="test_report",
                 entity_id=str(head["report_id"]), mrn=head["mrn"],
                 details={"joined": report["report_id"], "score": s and s["score"], "checks": s and s["checks"]})
    return {**report, "group_id": head["report_id"], "routing": "verify"}


def _verify_task(conn, patient: dict, card: dict | None, report: dict, note: str | None) -> dict:
    facts = "; ".join(report.get("match_facts") or [])
    score = f"score {report['score']}" if report.get("score") is not None else "not scored"
    reason = (f"Verify outside report: {card['name'] if card else 'no open test card'} — {score}"
              + (f"; {facts}" if facts else "") + (f". {note}" if note else ""))
    t = tasks.create_task(conn, mrn=patient["mrn"], task_type="outside_report",
                          owner_name=card["owner_name"] if card else patient["coordinator"],
                          card_id=card["card_id"] if card else None, reason=reason, actor_type="system", actor=SYSTEM,
                          dedupe_key=f"outside_report:{report['report_id']}")
    set_next_action(conn, patient["mrn"], "Outside report received: coordinator to verify or reject")
    return t


def _auto_match(conn, card: dict, report: dict) -> None:
    """One save (owner's decision of 25 Oct, replacing REQ-27's coordinator copy): the file passed every check --
    name, MRN and test match, clear, score at or above the threshold -- so the card moves Report received -> Report
    verified by the system, and the doctor gets the review item. No person confirms it; the patient is told nothing."""
    label = f"Outside report (checked by AI: name, MRN and test match, score {report['score']})"
    states.transition_card(conn, card["card_id"], "Report received", "system", SYSTEM,
                           source="outside_report_auto_matched", file_ref=report["file_name"],
                           report_id=report["report_id"], score=report["score"])
    if card["specimen"] is False:  # a scan done at an outside centre: the doctor verifies it (Verified)
        conn.execute("UPDATE order_card SET report_road = 'outside', label = %s WHERE card_id = %s",
                     (label + DOCTOR_TO_VERIFY, card["card_id"]))
        report = conn.execute("UPDATE test_report SET status = 'auto_matched', card_id = %s, prior_state = %s "
                              "WHERE report_id = %s RETURNING *",
                              (card["card_id"], card["state"], report["report_id"])).fetchone()
        audit.append(conn, actor_type="system", actor_name=SYSTEM, action="outside_report_auto_matched",
                     entity_type="test_report", entity_id=str(report["report_id"]), mrn=card["mrn"],
                     reason_code="outside_report_auto_matched",
                     details={"score": report["score"], "card_id": card["card_id"], "label": label,
                              "message_to_patient": None, "verified": False, "next": "doctor_verify"})
        _stop_checks(conn, card["card_id"], "outside_report_auto_matched", report["report_id"])
        set_next_action(conn, card["mrn"], f"{card['name']}: outside report received — doctor to verify")
        return
    states.transition_card(conn, card["card_id"], "Report verified", "system", SYSTEM,
                           matched_identifiers=3, identifiers=["name", "mrn", "test"], report_id=report["report_id"],
                           verified_by="system (outside report check)")
    conn.execute("UPDATE order_card SET report_road = 'outside', label = %s, patient_says_booked = false "
                 "WHERE card_id = %s", (label, card["card_id"]))
    report = conn.execute("UPDATE test_report SET status = 'verified', card_id = %s, prior_state = %s, "
                          "matched = %s, verified_at_demo = %s WHERE report_id = %s RETURNING *",
                          (card["card_id"], card["state"], ["name", "mrn", "test"], clock.get_now(conn),
                           report["report_id"])).fetchone()
    audit.append(conn, actor_type="system", actor_name=SYSTEM, action="outside_report_auto_matched",
                 entity_type="test_report", entity_id=str(report["report_id"]), mrn=card["mrn"],
                 reason_code="outside_report_auto_matched",
                 details={"score": report["score"], "card_id": card["card_id"], "label": label,
                          "message_to_patient": None, "verified": True, "matched": ["name", "mrn", "test"]})
    _stop_checks(conn, card["card_id"], "outside_report_auto_matched", report["report_id"])
    _report_ready(conn, card, report, "outside")


def _wrong_report(conn, card: dict, report: dict) -> None:
    """REQ-28: held Unverified, marked "possibly wrong or unclear report"; one approved message asking for the right
    report (never what the file seems to be); a second wrong file for the card: a "Call about test" task instead.
    No card moves and no doctor item. The coordinator gets a copy; the upload timer restarts."""
    earlier = conn.execute("SELECT count(*) AS n FROM test_report WHERE proposed_card_id = %s AND wrong_report "
                           "AND report_id <> %s", (card["card_id"], report["report_id"])).fetchone()["n"]
    conn.execute("UPDATE test_report SET wrong_report = true WHERE report_id = %s", (report["report_id"],))
    audit.append(conn, actor_type="system", actor_name=SYSTEM, action="report_possibly_wrong", entity_type="test_report",
                 entity_id=str(report["report_id"]), mrn=card["mrn"], reason_code="wrong_report_asked",
                 details={"failed": [k for k in ("G1", "G2", "G5") if not report["hard_checks"][k]],
                          "card_moved": False, "earlier_wrong_files": earlier})
    if earlier == 0:
        messaging.send_message(conn, mrn=card["mrn"], template_id="report_wrong_report", actor_type="agent", actor=AGENT,
                               reason_code="wrong_report_asked", card_id=card["card_id"])
        set_next_action(conn, card["mrn"], f"{card['name']}: asked the patient to send the right report")
    else:
        _call_task(conn, card, "a second wrong or unclear report file", f"wrong_report:{card['card_id']}")
    _copy(conn, card, report, "wrong_report")
    check = conn.execute("SELECT * FROM report_check WHERE card_id = %s AND status = 'sent' ORDER BY check_id DESC "
                         "LIMIT 1", (card["card_id"],)).fetchone()
    if check and earlier == 0:
        conn.execute("UPDATE report_check SET timer_from = %s, nudge_due = false WHERE check_id = %s",
                     (clock.get_today(conn), check["check_id"]))
    elif check:
        _close_check(conn, check, "call_about_test")


def _copy(conn, card: dict, report: dict, kind: str) -> None:
    copy = conn.execute(
        """INSERT INTO info_copy (mrn, report_id, card_id, kind, owner_name, created_at_demo)
           VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (report_id, kind) DO NOTHING RETURNING copy_id""",
        (card["mrn"], report["report_id"], card["card_id"], kind, card["owner_name"], clock.get_now(conn))).fetchone()
    if copy:
        audit.append(conn, actor_type="system", actor_name=SYSTEM, action="coordinator_copy_created",
                     entity_type="info_copy", entity_id=str(copy["copy_id"]), mrn=card["mrn"],
                     details={"kind": kind, "owner": card["owner_name"], "report_id": report["report_id"],
                              "due_date": None})


def _call_task(conn, card: dict, why: str, key: str, task_type: str = "call_about_test") -> dict:
    what = "Call the patient" if task_type == "call_patient_resend" else "Call about test"
    return tasks.create_task(conn, mrn=card["mrn"], task_type=task_type, owner_name=card["owner_name"],
                             card_id=card["card_id"], actor_type="system", actor=SYSTEM,
                             reason=f"{what}: {card['name']} — {why}", dedupe_key=f"{task_type}:{key}")


def facts_for(patient: dict, ids: dict) -> list[dict]:
    """Matches and mismatches shown to the coordinator as facts (never a verdict)."""
    out = []
    if ids.get("name"):
        deg = name_match(ids["name"], patient["full_name"])
        out.append({"identifier": "name", "report": ids["name"], "record": patient["full_name"],
                    "match": True if deg >= 0.9 else False if deg == 0 else None})
    if ids.get("age"):
        out.append({"identifier": "age", "report": ids["age"], "record": patient.get("age"),
                    "match": None if patient.get("age") is None or not ids["age"].isdigit()
                    else abs(int(ids["age"]) - patient["age"]) <= 1})
    if ids.get("sex"):
        out.append({"identifier": "sex", "report": ids["sex"], "record": patient.get("sex"),
                    "match": None if not patient.get("sex") else ids["sex"][:1].upper() == patient["sex"]})
    for k in ("lab_id", "facility", "report_date", "document_type"):
        if ids.get(k):
            out.append({"identifier": k, "report": ids[k], "record": None, "match": None})
    return out


def _load_report(conn, report_id: int) -> dict:
    r = conn.execute("SELECT * FROM test_report WHERE report_id = %s FOR UPDATE", (report_id,)).fetchone()
    if r is None:
        raise NotFound(f"Report {report_id} not found")
    return r


def verify_outside(conn, report_id: int, user_id: str, matched: list[str] | None, card_id: int | None = None) -> dict:
    """REQ-13: a person checks the document: a file attached and at least two matching identifiers. The file is
    attached and the card moves to Report verified, passing Report received in the same save (both audited);
    the card shows "Outside report (verified by coordinator)". Then the doctor's review item."""
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
    if card["state"] not in ("Due", "Booked", "Completed"):
        raise Conflict(f"The test card is already {card['state']}")
    scan = card["specimen"] is False
    if scan and card["state"] != "Completed":
        raise Invalid(f"{card['name']} is a scan: its outside report fits only once the patient said it was done")
    now = clock.get_now(conn)
    audit.append(conn, actor_type="person", actor_name=user_id, action="outside_report_verified", entity_type="test_report",
                 entity_id=str(report_id), mrn=r["mrn"], reason_code="outside_report_verified",
                 details={"matched": ticked, "card_id": card["card_id"], "file": r["file_name"], "score": r["score"]})
    states.transition_card(conn, card["card_id"], "Report received", "person", user_id,
                           source="outside_report_verified", file_ref=r["file_name"], report_id=report_id)
    if not scan:
        states.transition_card(conn, card["card_id"], "Report verified", "person", user_id,
                               source="outside_report_verified", matched_identifiers=len(ticked), report_id=report_id)
    conn.execute("UPDATE order_card SET report_road = 'outside', label = %s, patient_says_booked = false "
                 "WHERE card_id = %s", (VERIFIED_BY_COORDINATOR + (DOCTOR_TO_VERIFY if scan else ""), card["card_id"]))
    r = conn.execute("""UPDATE test_report SET status = 'verified', card_id = %s, matched = %s, verified_by = %s,
                        verified_at_demo = %s, prior_state = %s WHERE report_id = %s RETURNING *""",
                     (card["card_id"], ticked, user_id, now, card["state"], report_id)).fetchone()
    _close_tasks(conn, report_id, user_id, "Verified")
    _stop_checks(conn, card["card_id"], "outside_report_verified", report_id)
    if scan:
        set_next_action(conn, card["mrn"], f"{card['name']}: outside report verified — doctor to verify")
    else:
        _report_ready(conn, card, r, "outside")
    return r


def reject_outside(conn, report_id: int, user_id: str, reason: str | None) -> dict:
    """REQ-13: Reject needs a reason; the card is unchanged and the file is kept; a "Call the patient" task asks
    her to resend (2 working days). The doctor sees nothing."""
    r = _load_report(conn, report_id)
    if r["road"] != "outside" or r["status"] != "unverified":
        raise Conflict(f"Report {report_id} is not an unverified outside report ({r['status']})")
    if not reason or not reason.strip():
        raise Invalid("Rejecting needs a reason")
    r = conn.execute("UPDATE test_report SET status = 'rejected', rejected_by = %s, reject_reason = %s "
                     "WHERE report_id = %s RETURNING *", (user_id, reason.strip(), report_id)).fetchone()
    audit.append(conn, actor_type="person", actor_name=user_id, action="outside_report_rejected", entity_type="test_report",
                 entity_id=str(report_id), mrn=r["mrn"], reason_code="outside_report_rejected",
                 details={"reason": reason.strip(), "file_kept": True, "card_moved": False})
    _close_tasks(conn, report_id, user_id, f"Rejected: {reason.strip()}")
    card = conn.execute("SELECT * FROM order_card WHERE card_id = %s", (r["proposed_card_id"],)).fetchone() \
        if r["proposed_card_id"] else None
    if card:
        _call_task(conn, card, f"outside report rejected ({reason.strip()}); ask her to resend it",
                   f"rejected:{report_id}", "call_patient_resend")
    set_next_action(conn, r["mrn"], "Outside report rejected; coordinator to ask the patient to resend it")
    return r


def _close_tasks(conn, report_id: int, user_id: str, note: str) -> None:
    for t in conn.execute("SELECT task_id FROM coordinator_task WHERE dedupe_key = %s AND status = 'open'",
                          (f"outside_report:{report_id}",)).fetchall():
        tasks.complete_task(conn, t["task_id"], user_id, note)


# ---------------------------------------------------------------------------------------------------------------
# Coordinator copies: Confirm or Flag as wrong (REQ-27)
# ---------------------------------------------------------------------------------------------------------------
def copies(conn, owner: str | None = None) -> list[dict]:
    return conn.execute(
        """SELECT k.*, p.full_name, c.name AS test_name, c.state AS card_state, r.score, r.match_facts, r.file_name,
                  r.received_at_demo, r.status AS report_status, r.hard_checks
           FROM info_copy k JOIN patient p USING (mrn) JOIN test_report r ON r.report_id = k.report_id
           LEFT JOIN order_card c ON c.card_id = k.card_id
           WHERE (%s::text IS NULL OR k.owner_name = %s) AND (k.status = 'open' OR k.acted_at_demo >= %s)
           ORDER BY k.status = 'open' DESC, k.copy_id DESC""",
        (owner, owner, clock.get_now(conn) - timedelta(days=7))).fetchall()


def _load_copy(conn, copy_id: int) -> dict:
    k = conn.execute("SELECT * FROM info_copy WHERE copy_id = %s FOR UPDATE", (copy_id,)).fetchone()
    if k is None:
        raise NotFound(f"Copy {copy_id} not found")
    if k["status"] != "open":
        raise Conflict(f"This copy is already {k['status']}")
    return k


def confirm_copy(conn, copy_id: int, user_id: str) -> dict:
    """Optional Confirm on an auto-matched report: Report received -> Report verified, label "Outside report
    (verified by coordinator)"; the doctor's item then shows "confirmed by ..."."""
    k = _load_copy(conn, copy_id)
    if k["kind"] != "auto_matched":
        raise Invalid("Only an auto-matched report can be confirmed")
    r = _load_report(conn, k["report_id"])
    if r["status"] != "auto_matched":
        raise Conflict(f"The report is {r['status']}")
    matched = [f.split(":")[0] for f in r["match_facts"] or [] if f.endswith(": match")
               and f.split(":")[0] in ("name", "age", "sex")]
    now = clock.get_now(conn)
    states.transition_card(conn, k["card_id"], "Report verified", "person", user_id, source="coordinator_confirm",
                           matched_identifiers=len(matched), report_id=r["report_id"])
    _label(conn, k["card_id"], VERIFIED_BY_COORDINATOR)
    conn.execute("""UPDATE test_report SET status = 'verified', matched = %s, verified_by = %s, verified_at_demo = %s,
                    confirmed_by = %s, confirmed_at_demo = %s WHERE report_id = %s""",
                 (matched, user_id, now, user_id, now, r["report_id"]))
    k = conn.execute("UPDATE info_copy SET status = 'confirmed', acted_by = %s, acted_at_demo = %s WHERE copy_id = %s "
                     "RETURNING *", (user_id, now, copy_id)).fetchone()
    audit.append(conn, actor_type="person", actor_name=user_id, action="auto_matched_report_confirmed",
                 entity_type="test_report", entity_id=str(r["report_id"]), mrn=r["mrn"],
                 reason_code="outside_report_verified", details={"matched": matched, "copy_id": copy_id})
    return k


def flag_copy(conn, copy_id: int, user_id: str, reason: str | None) -> dict:
    """Flag as wrong (reason required): the doctor's item is withdrawn, the card returns to its earlier state with
    the file kept Unverified, and a "Call the patient" task goes to the owner. If the doctor has already decided,
    he and the owner are told "Report flagged after your decision"; nothing is sent to the patient."""
    k = _load_copy(conn, copy_id)
    if k["kind"] != "auto_matched":
        raise Invalid("Only an auto-matched report can be flagged as wrong")
    if not reason or not reason.strip():
        raise Invalid("Flag as wrong needs a reason")
    r = _load_report(conn, k["report_id"])
    if r["status"] != "auto_matched":
        raise Conflict(f"The report is {r['status']}")
    now, reason = clock.get_now(conn), reason.strip()
    card = conn.execute("SELECT * FROM order_card WHERE card_id = %s", (k["card_id"],)).fetchone()
    back = r["prior_state"] if r["prior_state"] in OPEN_TEST else "Due"
    states.transition_card(conn, card["card_id"], back, "person", user_id, source="flagged_wrong",
                           report_id=r["report_id"], reason=reason)
    conn.execute("UPDATE order_card SET label = %s, report_road = NULL WHERE card_id = %s",
                 ("Booked outside" if back == "Booked" and card["booking_source"] == "patient_elsewhere" else None,
                  card["card_id"]))
    conn.execute("""UPDATE test_report SET status = 'flagged', card_id = NULL, flagged_by = %s, flag_reason = %s,
                    flagged_at_demo = %s WHERE report_id = %s""", (user_id, reason, now, r["report_id"]))
    rv = conn.execute("SELECT * FROM report_review WHERE report_id = %s FOR UPDATE", (r["report_id"],)).fetchone()
    after = rv is not None and rv["status"] == "decided"
    if rv and not after:
        conn.execute("UPDATE report_review SET status = 'withdrawn', withdrawn_reason = %s WHERE review_id = %s",
                     ("withdrawn: flagged by coordinator", rv["review_id"]))
    elif after:
        conn.execute("UPDATE report_review SET flagged_after_decision = true, withdrawn_reason = %s WHERE review_id = %s",
                     ("Report flagged after your decision", rv["review_id"]))
    _call_task(conn, card, ("Report flagged after your decision — " if after else "")
               + f"auto-matched report flagged as wrong ({reason}); ask her to send the right report",
               f"flagged:{r['report_id']}", "call_patient_resend")
    k = conn.execute("UPDATE info_copy SET status = 'flagged', acted_by = %s, acted_at_demo = %s, reason = %s "
                     "WHERE copy_id = %s RETURNING *", (user_id, now, reason, copy_id)).fetchone()
    audit.append(conn, actor_type="person", actor_name=user_id, action="auto_matched_report_flagged",
                 entity_type="test_report", entity_id=str(r["report_id"]), mrn=r["mrn"], outcome="flagged",
                 details={"reason": reason, "card_back_to": back, "file_kept": True,
                          "review": rv["review_id"] if rv else None, "after_decision": after,
                          "message_to_patient": None})
    set_next_action(conn, r["mrn"], f"{card['name']}: outside report flagged as wrong; coordinator to call")
    return k


def read_copy(conn, copy_id: int, user_id: str) -> dict:
    k = _load_copy(conn, copy_id)
    if k["kind"] != "wrong_report":
        raise Invalid("Confirm or flag an auto-matched report")
    return conn.execute("UPDATE info_copy SET status = 'read', acted_by = %s, acted_at_demo = %s WHERE copy_id = %s "
                        "RETURNING *", (user_id, clock.get_now(conn), copy_id)).fetchone()


def open_file(conn, report_id: int, user: dict) -> dict:
    """The source file. The doctor sees a report once it has reached him (verified, or auto-matched); every
    opening is audited (REQ-14)."""
    r = conn.execute("SELECT * FROM test_report WHERE report_id = %s", (report_id,)).fetchone()
    if r is None or not r["blob_id"]:
        raise NotFound(f"Report {report_id} has no file")
    reached = r["status"] in ("verified", "auto_matched") or conn.execute(
        "SELECT 1 FROM report_review WHERE report_id = %s", (report_id,)).fetchone() is not None
    if user["role"] == "oncologist" and not reached:
        raise Conflict("The doctor sees a report only once it is verified or auto-matched")
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
# The report check (REQ-24 to REQ-26)
# ---------------------------------------------------------------------------------------------------------------
def _close_check(conn, check: dict, why: str) -> None:
    conn.execute("UPDATE report_check SET status = 'closed', closed_reason = %s, timer_from = NULL, nudge_due = false "
                 "WHERE check_id = %s", (why, check["check_id"]))


def _stop_checks(conn, card_id: int, why: str, report_id: int | None = None) -> None:
    for ch in conn.execute("SELECT * FROM report_check WHERE card_id = %s AND status IN ('open', 'blocked', 'sent')",
                           (card_id,)).fetchall():
        _close_check(conn, ch, why)
        if report_id:
            conn.execute("UPDATE report_check SET report_id = %s WHERE check_id = %s", (report_id, ch["check_id"]))


def _hospital_data(conn, card: dict) -> dict | None:
    """A report for this MRN and test code already in the hospital data (held, or sent while the card was not
    Completed)."""
    return conn.execute(
        """SELECT * FROM test_report WHERE road = 'hospital' AND mrn = %s AND report_mrn = %s AND test_code = %s
           AND blob_id IS NOT NULL AND (status = 'held' OR (status = 'mismatch' AND mismatch->>'card_state' <> 'Completed'))
           ORDER BY report_id LIMIT 1""", (card["mrn"], card["mrn"], card["test_code"])).fetchone()


def _raise_check(conn, card: dict, round_: int, today: date) -> str:
    """Look in the hospital data first: a report there is linked and nothing is sent (report_check_skipped).
    Otherwise one report check is raised; the 07:30 run sends it."""
    held = _hospital_data(conn, card)
    status = "skipped" if held else "open"
    ch = conn.execute(
        """INSERT INTO report_check (card_id, mrn, due_date, round, raised_on, status, closed_reason)
           VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (card_id, due_date, round) DO NOTHING RETURNING *""",
        (card["card_id"], card["mrn"], card["due_date"], round_, today, status,
         "report_check_skipped" if held else None)).fetchone()
    if ch is None:
        return "duplicate"
    audit.append(conn, actor_type="system", actor_name=CHECKER, action="report_check_raised" if not held
                 else "report_check_skipped", entity_type="report_check", entity_id=str(ch["check_id"]),
                 mrn=card["mrn"], rule="report_check", trigger_event="gap_check_0700",
                 reason_code="report_check_skipped" if held else "report_check_due",
                 details={"card": card["name"], "due_date": card["due_date"].isoformat(), "round": round_,
                          "hospital_data_found": bool(held), "report_id": held["report_id"] if held else None})
    if held:
        for t in conn.execute("SELECT task_id FROM coordinator_task WHERE dedupe_key = %s AND status = 'open'",
                              (f"report_mismatch:{held['report_id']}",)).fetchall():
            tasks.complete_task(conn, t["task_id"], SYSTEM, "Linked by the report check (hospital data)")
        states.transition_card(conn, card["card_id"], "Completed", "system", CHECKER, source="hospital_feed",
                               report_id=held["report_id"], via="report_check")
        conn.execute("UPDATE order_card SET completed_on = %s, label = NULL WHERE card_id = %s",
                     (held["received_at_demo"].date(), card["card_id"]))
        card = conn.execute("SELECT * FROM order_card WHERE card_id = %s", (card["card_id"],)).fetchone()
        r = _link_hospital(conn, card, held)
        conn.execute("UPDATE report_check SET report_id = %s WHERE check_id = %s", (r["report_id"], ch["check_id"]))
        return "skipped"
    set_next_action(conn, card["mrn"], f"{card['name']}: no report yet — report check at 07:30")
    return "raised"


def report_check_job(conn) -> dict:
    """07:00 (Use Case 2T): raise the report checks and run their timers. Running it twice raises nothing new."""
    if not config(conn):
        return {}
    t, today, out = timings(conn), clock.get_today(conn), {"raised": 0, "skipped": 0, "tasks": 0, "nudges_due": 0}
    for ch in conn.execute("""SELECT k.* FROM report_check k JOIN order_card c USING (card_id)
                              WHERE k.status IN ('open', 'blocked', 'sent') AND c.state NOT IN ('Due', 'Booked')
                                AND NOT (k.answer = 'done_outside' AND c.state = 'Completed')""").fetchall():
        _close_check(conn, ch, "card_moved_on")
    cards = conn.execute(
        """SELECT c.* FROM order_card c JOIN patient p USING (mrn)
           WHERE c.card_type = 'test' AND c.origin = 'plan_item' AND c.specimen IS NOT FALSE
             AND c.state IN ('Due', 'Booked') AND p.status NOT IN ('Paused', 'Lost to follow-up', 'Followed up elsewhere')
             AND NOT EXISTS (SELECT 1 FROM report_check k WHERE k.card_id = c.card_id AND k.due_date = c.due_date)
           ORDER BY c.due_date, c.card_id""").fetchall()
    for c in cards:
        later = max(c["appointment_date"] or c["due_date"], c["due_date"])
        if today >= policy.add_working_days(later, int(t["check_after_working_days"])):
            out["skipped" if _raise_check(conn, c, 1, today) == "skipped" else "raised"] += 1
    for ch in conn.execute("""SELECT k.*, c.name, c.owner_name, c.mrn AS card_mrn FROM report_check k
                              JOIN order_card c USING (card_id) WHERE k.status = 'sent' ORDER BY k.check_id
                              FOR UPDATE OF k""").fetchall():
        card = conn.execute("SELECT * FROM order_card WHERE card_id = %s", (ch["card_id"],)).fetchone()
        if ch["answer"] is None and ch["sent_on"] and \
                today >= policy.add_working_days(ch["sent_on"], int(t["no_answer_working_days"])):
            _call_task(conn, card, f"no answer to the report check of {ch['sent_on']:%d %b}", f"noanswer:{ch['check_id']}")
            _close_check(conn, ch, "no_answer")
            out["tasks"] += 1
        elif ch["answer"] == "done_outside" and ch["timer_from"]:
            if ch["nudged_on"] is None and not ch["nudge_due"] and \
                    today >= policy.add_working_days(ch["timer_from"], int(t["upload_nudge_working_days"])):
                conn.execute("UPDATE report_check SET nudge_due = true WHERE check_id = %s", (ch["check_id"],))
                out["nudges_due"] += 1
            elif ch["nudged_on"] is not None and today >= policy.add_working_days(
                    max(ch["nudged_on"], ch["timer_from"]), int(t["upload_call_working_days"])):
                _call_task(conn, card, "no report file after the upload request and one nudge",
                           f"noupload:{ch['check_id']}")
                _close_check(conn, ch, "no_upload")
                out["tasks"] += 1
        elif ch["answer"] == "not_done" and \
                today >= policy.add_working_days(ch["answered_on"], int(t["repeat_after_working_days"])):
            _close_check(conn, ch, "repeat")
            if ch["round"] <= int(t["max_repeats"]):
                out["skipped" if _raise_check(conn, card, ch["round"] + 1, today) == "skipped" else "raised"] += 1
            else:
                _call_task(conn, card, f"patient said Not done yet {ch['round']} times", f"notdone:{card['card_id']}")
                out["tasks"] += 1
    return out


def report_check_send(conn) -> list[dict]:
    """07:30 (Use Case 2T): the report check and the upload nudge, through the send checks of the reminder."""
    if not config(conn):
        return []
    today, out = clock.get_today(conn), []
    mode = (policy.autonomy(conn)["tasks"].get("report_check") or {}).get("mode", "live")
    for ch in conn.execute("SELECT * FROM report_check WHERE status IN ('open', 'blocked') ORDER BY check_id "
                           "FOR UPDATE").fetchall():
        card = conn.execute("SELECT * FROM order_card WHERE card_id = %s", (ch["card_id"],)).fetchone()
        if card["state"] not in OPEN_TEST:  # re-read: the report or the list arrived since 07:00
            _close_check(conn, ch, "card_moved_on")
            continue
        audit.append(conn, actor_type="agent", actor_name=AGENT, action="agent_decision", entity_type="report_check",
                     entity_id=str(ch["check_id"]), mrn=ch["mrn"], reason_code="report_check_due", rule="report_check",
                     task="report_check", mode=mode, outcome="send", trigger_event=f"report_check:{ch['check_id']}",
                     template_id="test_report_check",
                     details={"card": card["name"], "round": ch["round"], "hospital_data_found": False})
        msg = messaging.send_message(conn, mrn=ch["mrn"], template_id="test_report_check", actor_type="agent",
                                     actor=AGENT, reason_code="report_check_due", card_id=card["card_id"], mode=mode)
        if msg["status"] == "blocked":
            conn.execute("UPDATE report_check SET status = 'blocked', blocked_reason = %s WHERE check_id = %s",
                         (msg["blocked_reason"], ch["check_id"]))
        else:
            conn.execute("UPDATE report_check SET status = 'sent', message_id = %s, sent_on = %s, blocked_reason = NULL "
                         "WHERE check_id = %s", (msg["message_id"], today, ch["check_id"]))
            set_next_action(conn, ch["mrn"], f"Await the patient's answer to the {card['name']} report check")
        out.append({"check_id": ch["check_id"], "mrn": ch["mrn"], "kind": "report_check", "outcome": msg["status"],
                    "reason_code": msg["blocked_reason"] or "report_check_due"})
    for ch in conn.execute("SELECT * FROM report_check WHERE status = 'sent' AND nudge_due AND nudged_on IS NULL "
                           "ORDER BY check_id FOR UPDATE").fetchall():
        msg = messaging.send_message(conn, mrn=ch["mrn"], template_id="report_upload_nudge", actor_type="agent",
                                     actor=AGENT, reason_code="upload_nudge", card_id=ch["card_id"], mode=mode)
        if msg["status"] != "blocked":
            conn.execute("UPDATE report_check SET nudge_due = false, nudged_on = %s, nudge_message_id = %s "
                         "WHERE check_id = %s", (today, msg["message_id"], ch["check_id"]))
        out.append({"check_id": ch["check_id"], "mrn": ch["mrn"], "kind": "upload_nudge", "outcome": msg["status"],
                    "reason_code": msg["blocked_reason"] or "upload_nudge"})
    return out


def _complete_outside(conn, card: dict, inbound_id: int | None, actor: str) -> bool:
    """The patient says she did the test outside (a lab, or an imaging centre for a scan): Due or Booked ->
    Completed (source patient_outside)."""
    if card["state"] not in OPEN_TEST:
        return False
    states.transition_card(conn, card["card_id"], "Completed", "system", actor, source="patient_outside",
                           inbound_id=inbound_id)
    conn.execute("UPDATE order_card SET completed_on = %s, report_road = 'outside', label = %s WHERE card_id = %s",
                 (clock.get_today(conn), DONE_OUTSIDE, card["card_id"]))
    conn.execute("UPDATE gap_event SET status = 'resolved', outcome = 'patient_done_outside' "
                 "WHERE card_id = %s AND status <> 'resolved'", (card["card_id"],))
    return True


def _request_upload(conn, card: dict) -> None:
    messaging.send_message(conn, mrn=card["mrn"], template_id="report_upload_request", actor_type="agent",
                           actor=AGENT, reason_code="upload_requested", card_id=card["card_id"])
    set_next_action(conn, card["mrn"], f"{card['name']}: waiting for the outside report (photo or PDF)")


def tap_done_outside(conn, card: dict, inbound_id: int, actor: str) -> str:
    """"Done at an outside lab" on a test reminder (the fourth button): as the report-check answer -- the card moves
    to Completed, the upload request goes at once, and the upload timer (one nudge, then a call task) starts."""
    if card["card_type"] != "test" or card["state"] not in OPEN_TEST:
        return "already_progressed" if card["card_type"] == "test" else "not_a_test"
    open_check = conn.execute("SELECT * FROM report_check WHERE card_id = %s AND status = 'sent' AND answer IS NULL "
                              "ORDER BY check_id DESC LIMIT 1 FOR UPDATE", (card["card_id"],)).fetchone()
    if open_check:
        return answer_check(conn, open_check, "done_outside", inbound_id, actor)
    today = clock.get_today(conn)
    round_ = conn.execute("SELECT coalesce(max(round), 0) + 1 AS r FROM report_check WHERE card_id = %s AND "
                          "due_date = %s", (card["card_id"], card["due_date"])).fetchone()["r"]
    ch = conn.execute(
        """INSERT INTO report_check (card_id, mrn, due_date, round, raised_on, status, sent_on, answer, answered_on,
                                     answer_inbound_id, timer_from)
           VALUES (%s, %s, %s, %s, %s, 'sent', %s, 'done_outside', %s, %s, %s) RETURNING *""",
        (card["card_id"], card["mrn"], card["due_date"], round_, today, today, today, inbound_id, today)).fetchone()
    _complete_outside(conn, card, inbound_id, actor)
    audit.append(conn, actor_type="system", actor_name=actor, action="card_label_set", entity_type="order_card",
                 entity_id=str(card["card_id"]), mrn=card["mrn"], reason_code="reminder_done_outside",
                 details={"label": DONE_OUTSIDE, "state": "Completed", "inbound_id": inbound_id,
                          "check_id": ch["check_id"], "card_moved": True})
    _request_upload(conn, card)
    return "test_done_outside"


def check_for_message(conn, message_id: int | None) -> dict | None:
    if not message_id:
        return None
    return conn.execute("SELECT * FROM report_check WHERE message_id = %s FOR UPDATE", (message_id,)).fetchone()


def answer_check(conn, check: dict, button: str, inbound_id: int, actor: str) -> str:
    """REQ-25: the three buttons. Saved and audited, no AI. Done at an outside lab moves a lab test to Completed
    (owner's decision of 25 Oct) and gets a reply: the upload request (how to attach the file in the chat)."""
    if check["answer"] is not None or check["status"] != "sent":
        audit.append(conn, actor_type="system", actor_name=actor, action="duplicate_suppressed",
                     entity_type="report_check", entity_id=str(check["check_id"]), mrn=check["mrn"],
                     outcome="already_answered", details={"button": button, "answer": check["answer"]})
        return "report_check_already_answered"
    today = clock.get_today(conn)
    card = conn.execute("SELECT * FROM order_card WHERE card_id = %s", (check["card_id"],)).fetchone()
    label = CHECK_LABEL[button]
    conn.execute("UPDATE report_check SET answer = %s, answered_on = %s, answer_inbound_id = %s WHERE check_id = %s",
                 (button, today, inbound_id, check["check_id"]))
    _label(conn, card["card_id"], label)
    moved = button == "done_outside" and _complete_outside(conn, card, inbound_id, actor)
    audit.append(conn, actor_type="system", actor_name=actor, action="card_label_set", entity_type="order_card",
                 entity_id=str(card["card_id"]), mrn=card["mrn"], reason_code=f"report_check_{button}",
                 details={"label": label, "state": "Completed" if moved else card["state"], "inbound_id": inbound_id,
                          "check_id": check["check_id"], "card_moved": moved})
    if button == "done_outside":
        conn.execute("UPDATE report_check SET timer_from = %s WHERE check_id = %s", (today, check["check_id"]))
        _request_upload(conn, card)
    elif button == "done_hospital":
        if card["state"] != "Completed" or not card["report_road"]:
            _call_task(conn, card, "patient says the test was done at the hospital: check the hospital record",
                       f"{check['check_id']}", "find_hospital_report")
        _close_check(conn, check, "done_hospital")
        set_next_action(conn, card["mrn"], f"{card['name']}: coordinator to find the hospital report")
    else:
        set_next_action(conn, card["mrn"], f"{card['name']}: patient says not done yet; report check repeats")
    return f"report_check_{button}"


# ---------------------------------------------------------------------------------------------------------------
# The doctor's review (REQ-14 to REQ-18)
# ---------------------------------------------------------------------------------------------------------------
def _report_ready(conn, card: dict, report: dict, road: str) -> None:
    """A report that reached the doctor: in Use Case 2T his review item."""
    if config(conn):
        _create_review(conn, card, report, road)
    else:
        set_next_action(conn, card["mrn"], f"{card['name']}: report {'verified' if report['status'] == 'verified' else 'received'}")


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
                              "checked": report["status"], "score": report.get("score"), "message_to_patient": None})
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
    """REQ-15/16: Continue as planned or Needs discussion, nothing pre-selected; saved once with name and time.
    The decision does not need Report verified (an auto-matched report stays Report received)."""
    rv = _load_review(conn, review_id)
    if rv["status"] == "withdrawn":
        raise Conflict("This report was withdrawn: flagged by the coordinator")
    if rv["status"] != "open":
        raise Conflict("This report has already been decided")
    if decision not in ("continue", "discussion"):
        raise Invalid("Choose Continue as planned or Needs discussion")
    now = clock.get_now(conn)
    card = conn.execute("SELECT * FROM order_card WHERE card_id = %s", (rv["card_id"],)).fetchone()
    rv = conn.execute(
        """UPDATE report_review SET decision = %s, decided_by = %s, decided_at_demo = %s, status = %s
           WHERE review_id = %s RETURNING *""",
        (decision, user["user_id"], now, "decided" if decision == "continue" else "awaiting_instruction", review_id)
    ).fetchone()
    audit.append(conn, actor_type="person", actor_name=user["user_id"], action="report_review_decided",
                 entity_type="report_review", entity_id=str(review_id), mrn=rv["mrn"], outcome=decision,
                 details={"test": card["name"], "card_state": card["state"],
                          "opened_file_first": rv["opened_by"] is not None, "suggested": None})
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
    approved booking message; no "continue as planned" message. The test card does not move."""
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


def noted(conn, review_id: int, user: dict) -> dict:
    """The doctor has seen that a report was withdrawn or flagged after his decision."""
    rv = _load_review(conn, review_id)
    if rv["status"] != "withdrawn" and not rv["flagged_after_decision"]:
        raise Conflict("Nothing to acknowledge on this report")
    return conn.execute("UPDATE report_review SET notice_seen_by = %s WHERE review_id = %s RETURNING *",
                        (user["user_id"], review_id)).fetchone()


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
    """A non-specimen test (imaging) has no lab report: the doctor sets it Verified once Completed, or once the
    report of a scan done at an outside centre is received. Lab reports are verified by the system or a coordinator,
    never by this button."""
    card = conn.execute("SELECT * FROM order_card WHERE card_id = %s FOR UPDATE", (card_id,)).fetchone()
    if card is None or card["card_type"] != "test":
        raise NotFound(f"Test card {card_id} not found")
    if user["role"] != "oncologist":
        raise Invalid("Only the doctor sets a non-specimen test to Verified")
    if card["state"] in ("Completed", "Report received") and card["specimen"] is False:
        states.transition_card(conn, card_id, "Verified", "person", user["user_id"], role=user["role"], via=via)
    else:
        raise Conflict(f"{card['name']} is {card['state']}: " + (
            "a report is verified by its two identifiers (system or coordinator)" if card["specimen"] is not False
            else "nothing to verify yet"))
    set_next_action(conn, card["mrn"], f"{card['name']} verified by {user['name']}")
    return conn.execute("SELECT * FROM order_card WHERE card_id = %s", (card_id,)).fetchone()


def to_verify(conn) -> list[dict]:
    """The doctor's list: non-specimen tests completed (no lab report), or with the report of a scan done at an
    outside centre received (the file is there to open). A scan the patient says was done outside waits for its
    report first."""
    return conn.execute(
        """SELECT c.card_id, c.mrn, p.full_name, c.item_code, c.name, c.state, c.specimen, c.completed_on,
                  c.report_road, c.due_date, c.label,
                  (SELECT row_to_json(r) FROM (SELECT report_id, status, score, routing_applied, file_name
                     FROM test_report WHERE card_id = c.card_id AND status IN ('verified', 'auto_matched')
                     ORDER BY report_id DESC LIMIT 1) r) AS report
           FROM order_card c JOIN patient p USING (mrn)
           WHERE c.card_type = 'test' AND c.specimen IS FALSE
             AND (c.state = 'Report received' OR c.state = 'Completed' AND c.report_road IS DISTINCT FROM 'outside')
           ORDER BY c.due_date, c.card_id""").fetchall()


def doctor_items(conn) -> list[dict]:
    """Needs my decision: reports ready for review, with the plain facts of REQ-14 (never a result value), and
    reports withdrawn or flagged after the decision until the doctor has seen it."""
    rows = conn.execute(
        """SELECT v.*, p.full_name, p.preferred_language, c.name AS test_name, c.test_code, c.due_date, c.state AS card_state,
                  r.received_at_demo, r.file_name, r.verified_by, r.verified_at_demo, r.status AS report_status,
                  r.score, r.match_facts, r.report_date, r.patient_note, r.note_translation, r.confirmed_by,
                  r.confirmed_at_demo, r.flag_reason, r.routing_applied,
                  (SELECT t.result_code FROM coordinator_task t WHERE t.mrn = v.mrn AND t.task_type = 'cant_come'
                     AND t.result_code IS NOT NULL ORDER BY t.task_id DESC LIMIT 1) AS delay_reason
           FROM report_review v JOIN patient p USING (mrn) JOIN order_card c ON c.card_id = v.card_id
           JOIN test_report r ON r.report_id = v.report_id
           WHERE v.status IN ('open', 'awaiting_instruction')
              OR ((v.status = 'withdrawn' OR v.flagged_after_decision) AND v.notice_seen_by IS NULL)
           ORDER BY v.created_at_demo""").fetchall()
    out = []
    for v in rows:
        if v["road"] == "hospital":
            checked = "by the system (MRN and test code match)"
        elif v["report_status"] == "verified" and v["routing_applied"] == "auto_matched":
            checked = f"by the system (AI read: name, MRN and test match, score {v['score']})"
        elif v["report_status"] == "auto_matched" or (v["report_status"] == "flagged" and not v["verified_by"]):
            checked = f"auto-matched {v['score']}, not yet confirmed by a person"
        elif v["confirmed_by"]:
            checked = f"auto-matched {v['score']}, confirmed by {_name(v['confirmed_by'])} " \
                      f"{v['confirmed_at_demo']:%d %b %H:%M}"
        else:
            checked = f"by the coordinator, {_name(v['verified_by'])} {v['verified_at_demo']:%d %b %H:%M}"
        out.append({**v, "checked": checked, "days_since_due": (v["created_at_demo"].date() - v["due_date"]).days,
                    "unverified_label": f"Unverified outside report: auto-matched {v['score']}"
                    if v["report_status"] == "auto_matched" else None})
    return out


def _name(user_id: str | None) -> str:
    return USERS_BY_ID.get(user_id, {}).get("name", user_id or "")


def recent_decisions(conn) -> list[dict]:
    return conn.execute(
        """SELECT v.review_id, v.mrn, p.full_name, c.name AS test_name, v.decision, v.decided_by, v.decided_at_demo,
                  v.instruction, v.road, v.message_id, v.flagged_after_decision
           FROM report_review v JOIN patient p USING (mrn) JOIN order_card c ON c.card_id = v.card_id
           WHERE v.status = 'decided' ORDER BY v.decided_at_demo DESC LIMIT 20""").fetchall()


def reports_for(conn, mrn: str) -> dict:
    patient = conn.execute("SELECT * FROM patient WHERE mrn = %s", (mrn,)).fetchone()
    if patient is None:
        raise NotFound(f"Patient {mrn} not found")
    rows = conn.execute(
        """SELECT r.report_id, r.mrn, r.road, r.report_ref, r.report_mrn, r.test_code, r.file_name, r.mime, r.status,
                  r.card_id, r.received_at_demo, r.channel, r.identifiers, r.read_confidence, r.ai_failed,
                  r.proposed_card_id, r.matched, r.verified_by, r.verified_at_demo, r.rejected_by, r.reject_reason,
                  r.mismatch, r.score, r.hard_checks, r.match_facts, r.routing, r.routing_mode, r.routing_applied,
                  r.wrong_report, r.group_id, r.sender_phone, r.patient_note, r.note_translation, r.confirmed_by,
                  r.flagged_by, r.flag_reason,
                  (r.blob_id IS NOT NULL) AS has_file, c.name AS card_name, pc.name AS proposed_card_name,
                  pc.state AS proposed_card_state
           FROM test_report r LEFT JOIN order_card c ON c.card_id = r.card_id
           LEFT JOIN order_card pc ON pc.card_id = r.proposed_card_id
           WHERE r.mrn = %s ORDER BY r.report_id DESC""", (mrn,)).fetchall()
    return {"reports": [{**r, "facts": facts_for(patient, r["identifiers"] or {}) if r["road"] == "outside" else None}
                        for r in rows],
            "checkable": list(CHECKABLE), "labels": LABELS, "hard_check_labels": HARD_CHECKS,
            "variants": VARIANTS, "routing": routing(conn),
            "checks": conn.execute("""SELECT k.*, c.name AS card_name FROM report_check k JOIN order_card c USING (card_id)
                                      WHERE k.mrn = %s ORDER BY k.check_id DESC""", (mrn,)).fetchall(),
            "open_tests": conn.execute(
                """SELECT card_id, name, test_code, state, specimen FROM order_card WHERE mrn = %s AND card_type = 'test'
                   ORDER BY card_id""", (mrn,)).fetchall()}


# ---------------------------------------------------------------------------------------------------------------
# Threshold, weights and Shadow / Live (REQ-29)
# ---------------------------------------------------------------------------------------------------------------
def set_routing(conn, user: dict, *, mode: str | None = None, threshold: int | None = None,
                weights: dict | None = None) -> dict:
    """Changed only by the clinical lead; every change is a new signed version and an audit entry."""
    cur = routing(conn)
    if user["role"] != "oncologist" or user["user_id"] != cur["clinical_lead"]:
        raise Forbidden("Only the clinical lead changes the threshold, the weights or the mode")
    new = {**cur}
    if mode is not None:
        if mode not in ("shadow", "live"):
            raise Invalid("mode must be shadow or live")
        new["mode"] = mode
    if threshold is not None:
        if not isinstance(threshold, int) or not 50 <= threshold <= 100:
            raise Invalid("threshold must be a whole number from 50 to 100")
        new["threshold"] = threshold
    if weights is not None:
        if set(weights) != set(DEFAULT_ROUTING["weights"]) or any(not isinstance(v, int) or v < 0 for v in weights.values()) \
                or sum(weights.values()) != 100:
            raise Invalid("weights need " + ", ".join(DEFAULT_ROUTING["weights"]) + " as whole numbers adding up to 100")
        new["weights"] = weights
    if new == cur:
        return cur
    row = conn.execute("SELECT coalesce(max(version), 0) AS v FROM config_versions WHERE kind = 'report_routing' "
                       "AND name = 'default'").fetchone()
    new["version"] = f"{row['v'] + 1}.0"
    conn.execute("UPDATE config_versions SET status = 'retired' WHERE kind = 'report_routing' AND name = 'default' "
                 "AND status = 'active'")
    conn.execute("""INSERT INTO config_versions (kind, name, version, body, status, signed_off_by, signed_off_at)
                    VALUES ('report_routing', 'default', %s, %s, 'active', %s, now())""",
                 (row["v"] + 1, Jsonb(new), user["name"]))
    audit.append(conn, actor_type="person", actor_name=user["user_id"], action="report_routing_changed",
                 entity_type="config", entity_id="report_routing", outcome=new["mode"],
                 details={"before": {k: cur[k] for k in ("mode", "threshold", "weights")},
                          "after": {k: new[k] for k in ("mode", "threshold", "weights")}, "version": new["version"]})
    return new


def evaluate(conn) -> dict:
    """REQ-29: run the synthetic document set (the right report for each patient) plus wrong-patient, wrong-test and
    other bad files through the same rules, without saving anything. Counts the reports that should not pass but
    reached the threshold (target zero) and those that would have been auto-routed."""
    rules, today = routing(conn), clock.get_today(conn)
    thr = int(rules["threshold"])
    cards = conn.execute(
        """SELECT DISTINCT ON (c.mrn) c.*, p.full_name FROM order_card c JOIN patient p USING (mrn)
           WHERE c.card_type = 'test' AND c.specimen IS NOT FALSE ORDER BY c.mrn, c.due_date""").fetchall()
    patients = {p["mrn"]: p for p in conn.execute("SELECT * FROM patient").fetchall()}
    rows = []
    for i, card in enumerate(cards):
        p = patients[card["mrn"]]
        other = patients[cards[(i + 1) % len(cards)]["mrn"]] if len(cards) > 1 else None
        cases = [("clear", p, True, True), ("blurred", p, True, False), ("ct_scan", p, True, False),
                 ("future_date", p, True, False), ("unknown_sender", p, False, False)]
        if other and other["full_name"] != p["full_name"]:
            cases.append(("wrong_patient", other, True, False))
        for name, source, known, should in cases:
            variant = {"unknown_sender": "clear", "wrong_patient": "clear"}.get(name, name)
            try:
                ids, conf = read_identifiers(outside_report_pdf(source, today, card["name"], variant), "application/pdf")
            except ValueError:
                ids, conf = {}, {}
            s = score_report(ids, conf, p, card, sender_known=known, duplicate_elsewhere=False,
                             card_created=_card_created(conn, card), today=today, rules=rules)
            routed = route(s["checks"], s["score"], thr)
            rows.append({"mrn": p["mrn"], "case": name, "should_pass": should, "score": s["score"],
                         "checks_failed": [k for k, v in s["checks"].items() if not v], "routing": routed})
    bad = [r for r in rows if not r["should_pass"]]
    return {"threshold": thr, "mode": rules["mode"], "documents": len(rows), "should_not_pass": len(bad),
            "reached_threshold": sum(1 for r in bad if r["score"] >= thr),
            "would_auto_route": sum(1 for r in bad if r["routing"] == "auto_matched"),
            "right_reports_auto_routed": sum(1 for r in rows if r["should_pass"] and r["routing"] == "auto_matched"),
            "right_reports": sum(1 for r in rows if r["should_pass"]), "rows": rows}
