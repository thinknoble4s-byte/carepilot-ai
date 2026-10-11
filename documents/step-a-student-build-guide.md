# CarePilot AI: Student Build Guide, Step A (Patient and Plan Set-up)

Oct 11, 2026 · @Shiv

## 1. Start here

Step A builds the foundation that every later step stands on: loading patients, recording consent, confirming a care plan and generating order cards. You can build and test all of it from this one document and the mock data in section 4. You do not need any other document.

**What you are building.** A small web service with a database and a test suite. A coordinator uploads a patient register (a CSV file); the service turns good rows into patients and puts bad rows on an exceptions list. The coordinator records each patient's consent, then confirms a care plan. The service then creates "order cards" (tasks such as "follow-up visit due on 16 Nov") with a due date, an owner and a backup person. Every action writes an audit row.

**Stack.** Python 3.11 or later, FastAPI, SQLite (the `sqlite3` module that ships with Python), pytest and httpx. No ORM and no front end are needed; you test through the API.

| Part | What it covers | In Step A? |
| --- | --- | --- |
| A1 | Foundations: database, transactions, audit log, demo clock, reset | Yes |
| A2 | Import the patient register and the appointment list | Yes |
| A3 | Record consent | Yes |
| A4 | Confirm the care plan | Yes |
| A5 | Generate order cards | Yes |
| A6 | State machines for visit and test cards | Yes |
| A7 | Roles (coordinator, oncologist, chief) | Yes |
| Next | Gap rules G0 and G1, sending messages, Sarvam AI, screens | No. These come after Step A. |

**How to use this guide.** Read sections 2 and 3, copy the mock data files from section 4, create the tables from section 5, then build A1 to A7 in order (section 6). Each part lists what to build, the rules it must follow, the endpoints, and its tests. Section 7 is the full test list with the exact numbers your tests must produce.

**You are done with Step A when** `pytest` passes every test in section 7 with one command, and a reset brings the system back to exactly the same starting state each time.

## 2. Key ideas in plain words

CarePilot helps a hospital make sure cancer survivors do not miss their follow-up care. Step A sets up the people and the plan. All data here is synthetic (made up).

| Word | What it means |
| --- | --- |
| **Patient** | A survivor being followed up. Identified by an MRN. |
| **MRN** | Medical record number, the hospital's patient id. In this project it looks like `SYN-0007` (four digits). It is the unique key for a patient. |
| **Register** | The coordinator's CSV list of patients to follow up (`register.csv`). |
| **Appointment list** | A CSV list of booked appointments (`appointments.csv`). Step A only stores it; later steps use it to mark visits as attended. |
| **Exception** | A CSV row that cannot be used (unknown MRN, duplicate, bad phone and so on). It goes on the exceptions list with a reason. **The system never creates a patient silently from a bad row.** |
| **Consent and scopes** | Permission to contact the patient. Each scope is one permission: `whatsapp_patient`, `whatsapp_caregiver`, `automated_messaging`, `outside_records`, `external_care`. Without `automated_messaging` the agent must never message the patient. |
| **Agent on or off** | A flag on the patient. It starts off, and turns on only when consent with `automated_messaging` is recorded. |
| **Coordinator task** | A to-do for a person. Step A creates one when a patient's consent lacks `automated_messaging`. |
| **Care plan** | The follow-up plan confirmed for one patient: a template, the therapy, and the treatment end date. |
| **Template** | The signed-off recipe for a plan. Ours is `BC-SURV-1` (breast cancer survivorship, mock). It lists five items (two visits, three tests) and when each is due. |
| **Order card** | A tracked task made from a plan item: type (visit or test), due date, state, a named owner and a named cover (the backup if the owner is away). |
| **Audit event** | A row recording who did what and when. The audit table is append-only: rows can never be changed or deleted. |
| **Transaction** | A group of database changes that succeed or fail together. Every action and its audit row must be in the same transaction. |
| **Demo clock** | A stored "today" date. Code never reads the real date; it always asks the clock. This lets us move time forward in demos and tests. The seed date is **Mon 2 Nov 2026**. |
| **Seed and reset** | The known starting state. `reset` wipes the database and rebuilds exactly that state. |
| **Role** | Coordinator, oncologist (doctor) or chief. Each role may do different things. |

**Four rules to keep in mind from the start**

1. Nothing is created silently. A bad row becomes an exception, never a patient.
2. Every action writes an audit row in the same transaction. If the action fails, no audit row and no data change remain.
3. No cards exist for a patient until a coordinator confirms the plan.
4. The same input always produces the same cards.

## 3. Project setup

**Folders.** Create this layout. One file per concern keeps each piece small and testable.

```
carepilot/
  app/
    __init__.py
    main.py        FastAPI app and routes
    db.py          connect(), tx() helper, create_schema()
    audit.py       write_audit()
    clock.py       get_today(), advance(), set_today()
    seed.py        reset() builds the known starting state
    importer.py    import_register(), import_appointments()
    consent.py     record_consent()
    plan.py        confirm_plan(), generate_cards(), due-date rules
    states.py      transition tables for visit and test cards
    auth.py        current_user(), require_role()
  data/            the files from section 4, saved with exactly these names
  tests/
    conftest.py    fresh database per test, test client, helper logins
    test_foundations.py  test_import.py  test_consent.py
    test_plan_cards.py   test_states.py   test_roles.py   test_reset.py
  requirements.txt
```

**Install and run.**

```
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install fastapi uvicorn httpx pytest python-multipart
pytest -q                          # run all tests
uvicorn app.main:app --reload      # run the service on http://127.0.0.1:8000
```

**Rules for the project**

- The database file is read from the environment variable `CAREPILOT_DB` (default `carepilot.db`). Tests point it at a new temporary file for every test, so no test depends on another.
- Open SQLite connections with `PRAGMA foreign_keys = ON`.
- Use plain `sqlite3` with `?` placeholders. Never build SQL by joining strings with user input.
- All dates are stored as text in ISO format `YYYY-MM-DD`. All timestamps are stored as ISO text in UTC.
- Every change to data happens inside the `tx()` helper (section 6, A1) and writes its audit row inside that same block.

## 4. Mock data

Save each file below in your `data/` folder with exactly the name shown. Everything is made up: names, phone numbers and dates. The seed date ("today" at reset) is **Mon 2 Nov 2026**. Line numbers below count the header as line 1.

### 4.1 register.csv

29 data rows. Your importer must create **25 patients** and put **4 rows** on the exceptions list.

```csv
mrn,full_name,phone,caregiver_name,caregiver_phone,preferred_language
SYN-0001,Meena Hegde,+919000000001,Suresh Hegde,+919000100001,Kannada
SYN-0002,Lakshmi Gowda,+919000000002,Ramesh Gowda,+919000100002,Kannada
SYN-0003,Anita Desai,+919000000003,,,English
SYN-0004,Kavitha Rao,+919000000004,Mohan Rao,+919000100004,Kannada
SYN-0005,Rekha Shetty,+919000000005,Prakash Shetty,+919000100005,Kannada
SYN-0006,Pooja Nambiar,+919000000006,Arun Nambiar,+919000100006,English
SYN-0007,Sunita Verma,+919000000007,Rakesh Verma,+919000100007,Hindi
SYN-0008,Geeta Sharma,+919000000008,Vikram Sharma,+919000100008,Hindi
SYN-0009,Shanti Kulkarni,+919000000009,,,Kannada
SYN-0010,Deepa Menon,+919000000010,Sanjay Menon,+919000100010,English
SYN-0011,Savitri Patil,+919000000011,Ganesh Patil,+919000100011,Kannada
SYN-0012,Farida Khan,+919000000012,Imran Khan,+919000100012,Hindi
SYN-0012,Farida Khan,+919000099999,Imran Khan,+919000100012,Hindi
SYN-0013,Nirmala Reddy,+919000000013,,,Kannada
SYN-0014,Usha Joshi,+919000000014,Mahesh Joshi,+919000100014,Hindi
SYN-0015,Padma Iyer,+919000000015,Gopal Iyer,+919000100015,English
SYN-0016,Kamala Bhat,+919000000016,Raghu Bhat,+919000100016,Kannada
SYN-0017,Sarita Singh,+919000000017,Ajay Singh,+919000100017,Hindi
SYN-0018,Vimala Naik,+919000000018,,,Kannada
SYN-0026,Maya Pillai,98765,,,Kannada
SYN-0019,Jayanthi Murthy,+919000000019,Ravi Murthy,+919000100019,Kannada
SYN-0020,Rani Thomas,+919000000020,Joseph Thomas,+919000100020,English
SYN-0021,Mala Acharya,+919000000021,Sudhir Acharya,+919000100021,Kannada
SYN-0022,Bharathi Kamath,+919000000022,,,Kannada
,Tara Bose,+919000000027,,,Hindi
SYN-0023,Anjali Mishra,+919000000023,Dinesh Mishra,+919000100023,Hindi
SYN-0024,Neelam Gupta,+919000000024,,,Hindi
SYN-0028,Leena Fernandes,+919000000028,,,French
SYN-0025,Sheela Dsouza,+919000000025,Peter Dsouza,+919000100025,English
```

The four bad rows and the reason code your importer must give each:

| Line | Row | Reason code | Why |
| --- | --- | --- | --- |
| 14 | SYN-0012 (second time) | `duplicate_in_file` | Same MRN as line 13, which was already created. The first valid row wins. |
| 21 | SYN-0026, phone `98765` | `malformed_phone` | Phone must match `^\+91[6-9]\d{9}$`. |
| 26 | blank MRN, Tara Bose | `missing_mrn` | MRN is required. |
| 29 | SYN-0028, language French | `unsupported_language` | Allowed: Kannada, Hindi, English. |

### 4.2 appointments.csv

14 data rows. Your importer must store **10 appointments** and put **4 rows** on the exceptions list.

```csv
mrn,appointment_date,department
SYN-0002,2026-11-04,Oncology OPD
SYN-0004,2026-11-09,Oncology OPD
SYN-0005,2026-11-10,Oncology OPD
SYN-0006,2026-11-05,Laboratory
SYN-0006,2026-11-12,Oncology OPD
SYN-0099,2026-11-09,Oncology OPD
SYN-0008,2026-11-16,Oncology OPD
SYN-0010,2026-11-16,Laboratory
SYN-0010,2026-11-23,Oncology OPD
SYN-0003,2026-13-45,Oncology OPD
SYN-0011,2026-11-26,Oncology OPD
SYN-0004,2026-11-09,Oncology OPD
SYN-0016,2026-12-15,Oncology OPD
SYN-0026,2026-11-18,Oncology OPD
```

| Line | Row | Reason code | Why |
| --- | --- | --- | --- |
| 7 | SYN-0099 | `unknown_mrn` | No such patient. |
| 11 | SYN-0003, date `2026-13-45` | `bad_date` | Not a real date. |
| 13 | SYN-0004, 2026-11-09, Oncology OPD | `duplicate` | Same MRN, date and department as line 3. |
| 15 | SYN-0026 | `unknown_mrn` | Its register row was rejected, so the patient does not exist. |

### 4.3 consent\_records.csv

24 rows. **SYN-0013 has no row on purpose** (no consent on record). **SYN-0003 has only `whatsapp_patient`** (no `automated_messaging`). Scopes are separated by semicolons.

```csv
mrn,consent_reference,scopes,recorded_by,recorded_on
SYN-0001,CONS-0001,whatsapp_patient;automated_messaging;outside_records;whatsapp_caregiver,u_priya,2026-10-28
SYN-0002,CONS-0002,whatsapp_patient;automated_messaging;outside_records;whatsapp_caregiver;external_care,u_priya,2026-10-28
SYN-0003,CONS-0003,whatsapp_patient,u_priya,2026-10-28
SYN-0004,CONS-0004,whatsapp_patient;automated_messaging;outside_records;whatsapp_caregiver;external_care,u_priya,2026-10-28
SYN-0005,CONS-0005,whatsapp_patient;automated_messaging;outside_records;whatsapp_caregiver,u_priya,2026-10-28
SYN-0006,CONS-0006,whatsapp_patient;automated_messaging;outside_records;whatsapp_caregiver;external_care,u_priya,2026-10-28
SYN-0007,CONS-0007,whatsapp_patient;automated_messaging;outside_records;whatsapp_caregiver,u_priya,2026-10-28
SYN-0008,CONS-0008,whatsapp_patient;automated_messaging;outside_records;whatsapp_caregiver;external_care,u_priya,2026-10-28
SYN-0009,CONS-0009,whatsapp_patient;automated_messaging;outside_records,u_priya,2026-10-28
SYN-0010,CONS-0010,whatsapp_patient;automated_messaging;outside_records;whatsapp_caregiver;external_care,u_priya,2026-10-28
SYN-0011,CONS-0011,whatsapp_patient;automated_messaging;outside_records;whatsapp_caregiver,u_priya,2026-10-28
SYN-0012,CONS-0012,whatsapp_patient;automated_messaging;outside_records;whatsapp_caregiver;external_care,u_priya,2026-10-28
SYN-0014,CONS-0014,whatsapp_patient;automated_messaging;outside_records;whatsapp_caregiver;external_care,u_priya,2026-10-28
SYN-0015,CONS-0015,whatsapp_patient;automated_messaging;outside_records;whatsapp_caregiver,u_priya,2026-10-28
SYN-0016,CONS-0016,whatsapp_patient;automated_messaging;outside_records;whatsapp_caregiver;external_care,u_priya,2026-10-28
SYN-0017,CONS-0017,whatsapp_patient;automated_messaging;outside_records;whatsapp_caregiver,u_priya,2026-10-28
SYN-0018,CONS-0018,whatsapp_patient;automated_messaging;outside_records;external_care,u_priya,2026-10-28
SYN-0019,CONS-0019,whatsapp_patient;automated_messaging;outside_records;whatsapp_caregiver,u_priya,2026-10-28
SYN-0020,CONS-0020,whatsapp_patient;automated_messaging;outside_records;whatsapp_caregiver;external_care,u_priya,2026-10-28
SYN-0021,CONS-0021,whatsapp_patient;automated_messaging;outside_records,u_priya,2026-10-28
SYN-0022,CONS-0022,whatsapp_patient;automated_messaging;outside_records;external_care,u_priya,2026-10-28
SYN-0023,CONS-0023,whatsapp_patient;automated_messaging;outside_records;whatsapp_caregiver,u_priya,2026-10-28
SYN-0024,CONS-0024,whatsapp_patient;automated_messaging;outside_records;external_care,u_priya,2026-10-28
SYN-0025,CONS-0025,whatsapp_patient;automated_messaging;outside_records;whatsapp_caregiver,u_priya,2026-10-28
```

Expected result after recording all 24: **23 patients have the agent on** and 2 have it off (SYN-0003 because the scope is missing, SYN-0013 because no consent exists). **One coordinator task** is created, for SYN-0003 (`consent_scope_missing`). No task is created for SYN-0013.

### 4.4 plan\_inputs.csv

This is what the coordinator types in when confirming a plan (A4). The seed does **not** confirm any plan; your tests do it, one patient at a time, using these rows. `identifiers_checked` lists the two identifiers the coordinator ticked.

```csv
mrn,template_id,therapy,treatment_end_date,identifiers_checked,confirmed_by
SYN-0001,BC-SURV-1,"Surgery, radiotherapy and endocrine therapy",2026-04-20,name;mrn,u_priya
SYN-0002,BC-SURV-1,Surgery and adjuvant chemotherapy,2026-05-04,name;mrn,u_priya
SYN-0003,BC-SURV-1,Surgery and endocrine therapy,2026-05-08,name;mrn,u_priya
SYN-0004,BC-SURV-1,Neoadjuvant chemotherapy and surgery,2026-05-09,name;mrn,u_priya
SYN-0005,BC-SURV-1,"Surgery, radiotherapy and endocrine therapy",2026-05-10,name;mrn,u_priya
SYN-0006,BC-SURV-1,Surgery and adjuvant chemotherapy,2026-05-12,name;mrn,u_priya
SYN-0007,BC-SURV-1,Surgery and endocrine therapy,2026-05-15,name;mrn,u_priya
SYN-0008,BC-SURV-1,Neoadjuvant chemotherapy and surgery,2026-05-16,name;mrn,u_priya
SYN-0009,BC-SURV-1,"Surgery, radiotherapy and endocrine therapy",2026-05-20,name;mrn,u_priya
SYN-0010,BC-SURV-1,Surgery and adjuvant chemotherapy,2026-05-23,name;mrn,u_priya
SYN-0011,BC-SURV-1,Surgery and endocrine therapy,2026-05-26,name;mrn,u_priya
SYN-0012,BC-SURV-1,Neoadjuvant chemotherapy and surgery,2026-05-29,name;mrn,u_priya
SYN-0013,BC-SURV-1,"Surgery, radiotherapy and endocrine therapy",2026-06-02,name;mrn,u_priya
SYN-0014,BC-SURV-1,Surgery and adjuvant chemotherapy,2026-06-06,name;mrn,u_priya
SYN-0015,BC-SURV-1,Surgery and endocrine therapy,2026-06-10,name;mrn,u_priya
SYN-0016,BC-SURV-1,Neoadjuvant chemotherapy and surgery,2026-06-15,name;mrn,u_priya
SYN-0017,BC-SURV-1,"Surgery, radiotherapy and endocrine therapy",2026-06-20,name;mrn,u_priya
SYN-0018,BC-SURV-1,Surgery and adjuvant chemotherapy,2026-06-25,name;mrn,u_priya
SYN-0019,BC-SURV-1,Surgery and endocrine therapy,2026-07-04,name;mrn,u_priya
SYN-0020,BC-SURV-1,Neoadjuvant chemotherapy and surgery,2026-08-31,name;mrn,u_priya
SYN-0021,BC-SURV-1,"Surgery, radiotherapy and endocrine therapy",2026-03-15,name;mrn,u_priya
SYN-0022,BC-SURV-1,Surgery and adjuvant chemotherapy,2026-04-05,name;mrn,u_priya
SYN-0023,BC-SURV-1,Surgery and endocrine therapy,2026-07-18,name;mrn,u_priya
SYN-0024,BC-SURV-1,Neoadjuvant chemotherapy and surgery,2026-09-14,name;mrn,u_priya
SYN-0025,BC-SURV-1,"Surgery, radiotherapy and endocrine therapy",2026-05-31,name;mrn,u_priya
```

### 4.5 care\_plan\_template.json (the care plan mock data)

This is the signed-off recipe (mock). It is not clinical advice. Load it into the `plan_template` table at reset. The five items turn into five order cards for each patient.

```json
{
  "template_id": "BC-SURV-1",
  "version": "1.0",
  "name": "Breast cancer survivorship follow-up (MOCK, not clinical advice)",
  "signed_off_by": "MOCK sign-off: Dr. Anand Rao (demo clinical lead)",
  "effective_from": "2026-10-05",
  "weekend_rule": "move_forward_to_monday",
  "items": [
    {"item_code": "V1", "type": "visit", "name": "Oncology follow-up visit (6 months)", "months": 6, "days": 0, "department": "Oncology OPD"},
    {"item_code": "T1", "type": "test", "name": "Pre-visit blood tests (CBC, liver function)", "months": 6, "days": -7, "department": "Laboratory"},
    {"item_code": "V2", "type": "visit", "name": "Oncology follow-up visit (12 months)", "months": 12, "days": 0, "department": "Oncology OPD"},
    {"item_code": "T2", "type": "test", "name": "Mammogram", "months": 12, "days": -14, "department": "Radiology"},
    {"item_code": "T3", "type": "test", "name": "Bone density scan (DEXA)", "months": 12, "days": -14, "department": "Radiology"}
  ],
  "departments": {
    "Oncology OPD": {"owner": "Priya Nair", "cover": "Meera Joseph"},
    "Laboratory": {"owner": "Sunil Kumar", "cover": "Priya Nair"},
    "Radiology": {"owner": "Lakshmi Rao", "cover": "Priya Nair"}
  }
}
```

How to read it: each item's due date is the treatment end date plus `months`, then plus `days` (a negative number means earlier), then moved forward to the next Monday if it lands on a Saturday or Sunday. The owner and cover come from the item's department.

### 4.6 users.json and config.json

Load `users.json` into the `app_user` table and `config.json` into the `config_version` table at reset.

```json
[
  {"user_id": "u_priya", "name": "Priya Nair", "role": "coordinator"},
  {"user_id": "u_meera", "name": "Meera Joseph", "role": "coordinator"},
  {"user_id": "u_anand", "name": "Dr. Anand Rao", "role": "oncologist"},
  {"user_id": "u_kavita", "name": "Dr. Kavita Menon", "role": "oncologist"},
  {"user_id": "u_suresh", "name": "Dr. Suresh Pillai", "role": "chief"}
]
```

```json
{
  "demo_start_date": "2026-11-02",
  "supported_languages": ["Kannada", "Hindi", "English"],
  "departments": ["Oncology OPD", "Laboratory", "Radiology"],
  "consent_scopes": ["whatsapp_patient", "whatsapp_caregiver", "automated_messaging", "outside_records", "external_care"],
  "identifier_options": ["name", "mrn", "phone"],
  "rule_thresholds": {"version": "1.0", "g0_days_before_due": 7, "g1_days_after_due": 2, "g4_unanswered_messages": 2, "g4_hours_each": 24}
}
```

The thresholds are not used in Step A. Store them now so the next step (rules G0 and G1) reads its numbers from the database, not from code.

### 4.7 Expected due dates for all 25 patients

Once a plan is confirmed with the dates in 4.4, your code must create exactly these due dates (5 cards per patient, 125 in total). T2 (mammogram) and T3 (bone density scan) always have the same date. Use this table as the answer key for your tests.

| MRN | V1 visit (6 mo) | T1 blood tests | V2 visit (12 mo) | T2 and T3 (12 mo, 14 days earlier) |
| --- | --- | --- | --- | --- |
| SYN-0001 | 2026-10-20 | 2026-10-13 | 2027-04-20 | 2027-04-06 |
| SYN-0002 | 2026-11-04 | 2026-10-28 | 2027-05-04 | 2027-04-20 |
| SYN-0003 | 2026-11-09 | 2026-11-02 | 2027-05-10 | 2027-04-26 |
| SYN-0004 | 2026-11-09 | 2026-11-02 | 2027-05-10 | 2027-04-26 |
| SYN-0005 | 2026-11-10 | 2026-11-03 | 2027-05-10 | 2027-04-26 |
| SYN-0006 | 2026-11-12 | 2026-11-05 | 2027-05-12 | 2027-04-28 |
| SYN-0007 | 2026-11-16 | 2026-11-09 | 2027-05-17 | 2027-05-03 |
| SYN-0008 | 2026-11-16 | 2026-11-09 | 2027-05-17 | 2027-05-03 |
| SYN-0009 | 2026-11-20 | 2026-11-13 | 2027-05-20 | 2027-05-06 |
| SYN-0010 | 2026-11-23 | 2026-11-16 | 2027-05-24 | 2027-05-10 |
| SYN-0011 | 2026-11-26 | 2026-11-19 | 2027-05-26 | 2027-05-12 |
| SYN-0012 | 2026-11-30 | 2026-11-23 | 2027-05-31 | 2027-05-17 |
| SYN-0013 | 2026-12-02 | 2026-11-25 | 2027-06-02 | 2027-05-19 |
| SYN-0014 | 2026-12-07 | 2026-11-30 | 2027-06-07 | 2027-05-24 |
| SYN-0015 | 2026-12-10 | 2026-12-03 | 2027-06-10 | 2027-05-27 |
| SYN-0016 | 2026-12-15 | 2026-12-08 | 2027-06-15 | 2027-06-01 |
| SYN-0017 | 2026-12-21 | 2026-12-14 | 2027-06-21 | 2027-06-07 |
| SYN-0018 | 2026-12-25 | 2026-12-18 | 2027-06-25 | 2027-06-11 |
| SYN-0019 | 2027-01-04 | 2026-12-28 | 2027-07-05 | 2027-06-21 |
| SYN-0020 | 2027-03-01 | 2027-02-22 | 2027-08-31 | 2027-08-17 |
| SYN-0021 | 2026-09-15 | 2026-09-08 | 2027-03-15 | 2027-03-01 |
| SYN-0022 | 2026-10-05 | 2026-09-28 | 2027-04-05 | 2027-03-22 |
| SYN-0023 | 2027-01-18 | 2027-01-11 | 2027-07-19 | 2027-07-05 |
| SYN-0024 | 2027-03-15 | 2027-03-08 | 2027-09-14 | 2027-08-31 |
| SYN-0025 | 2026-11-30 | 2026-11-23 | 2027-05-31 | 2027-05-17 |

Owners and covers come from the department: **Oncology OPD** (V1, V2) owner Priya Nair, cover Meera Joseph. **Laboratory** (T1) owner Sunil Kumar, cover Priya Nair. **Radiology** (T2, T3) owner Lakshmi Rao, cover Priya Nair.

**Worked example, SYN-0007 (Sunita Verma, treatment ended 2026-05-15):** V1 is 15 May plus 6 months = Sun 15 Nov 2026, so it moves forward to **Mon 16 Nov 2026**. T1 is 15 Nov minus 7 days = Sun 8 Nov, so it moves to **Mon 9 Nov 2026**. V2 is 15 May 2027 (a Saturday), so it moves to Mon 17 May 2027. T2 and T3 are 15 May 2027 minus 14 days = Sat 1 May, so they move to Mon 3 May 2027.

### 4.8 What the special patients are for

| MRN | Patient | Use it to test |
| --- | --- | --- |
| SYN-0007 | Sunita Verma, Hindi | The golden path patient. V1 due Mon 16 Nov, so the gap check day (7 days before) is Mon 9 Nov. Her caregiver is Rakesh Verma. |
| SYN-0003 | Anita Desai | Consent without `automated_messaging`: agent stays off, one coordinator task. No caregiver. |
| SYN-0013 | Nirmala Reddy | No consent record at all: agent stays off, no task. |
| SYN-0012 | Farida Khan | Duplicate MRN inside the file. Also use her to test re-import with a changed phone number (duplicate against an existing patient). |
| SYN-0014 | Usha Joshi | Weekend shift: V1 falls on Sun 6 Dec and moves to Mon 7 Dec. |
| SYN-0020 | Rani Thomas | Month-end rule: 31 Aug plus 6 months has no 31 Feb, so use the last day, 28 Feb 2027 (a Sunday), then move to Mon 1 Mar 2027. |
| SYN-0021 | Mala Acharya | Has a caregiver but consent lacks `whatsapp_caregiver`. V1 was due long ago (2026-09-15). |
| SYN-0001, SYN-0022 | Meena Hegde, Bharathi Kamath | V1 already past due on the seed date (20 Oct and 5 Oct). Used by rule G1 in the next step. |
| SYN-0002, SYN-0003, SYN-0004, SYN-0005 |  | V1 due 4, 9, 9 and 10 Nov: they enter the 7-days-before window on 28 Oct and 2 or 3 Nov. Used by rule G0 in the next step. |
| SYN-0006, SYN-0010 | Pooja Nambiar, Deepa Menon | Each has a laboratory and an oncology appointment in the appointment list. |
| SYN-0026, SYN-0028, blank MRN |  | Rejected register rows. The appointment for SYN-0026 must also be rejected (`unknown_mrn`). |

### 4.9 The seed state after a reset

After `reset()` the database must contain exactly this:

| Item | Expected |
| --- | --- |
| Demo clock | 2026-11-02 |
| Users | 5 (from users.json) |
| Config versions | 2: plan template `BC-SURV-1` v1.0 and rule thresholds v1.0 |
| Patients | 25 |
| Appointments | 10 |
| Open exceptions | 8 (4 from the register, 4 from the appointment list) |
| Import batches | 2 (one register, one appointments) |
| Consent records | 24 |
| Patients with the agent on | 23 |
| Coordinator tasks | 1 (SYN-0003, `consent_scope_missing`) |
| Care plans | 0 |
| Order cards | 0 |
| Audit rows | 26 (1 register import, 1 appointment import, 24 consent records) |

## 5. Database

Put this SQL in `db.py` as `create_schema(conn)`. It is the complete set of tables for Step A. The audit table already has columns for the agent and AI fields that later steps will fill; leave them empty for now.

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE app_user (
  user_id TEXT PRIMARY KEY,
  name    TEXT NOT NULL,
  role    TEXT NOT NULL CHECK (role IN ('coordinator','oncologist','chief'))
);

CREATE TABLE clock (
  id    INTEGER PRIMARY KEY CHECK (id = 1),
  today TEXT NOT NULL                       -- YYYY-MM-DD, the demo date
);

CREATE TABLE config_version (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  type          TEXT NOT NULL,              -- 'plan_template' or 'rule_thresholds'
  version       TEXT NOT NULL,
  signed_off_by TEXT,
  effective_from TEXT,
  content_json  TEXT NOT NULL,
  UNIQUE (type, version)
);

CREATE TABLE import_batch (
  batch_id               INTEGER PRIMARY KEY AUTOINCREMENT,
  source_type            TEXT NOT NULL CHECK (source_type IN ('register','appointments')),
  file_name              TEXT,
  imported_by            TEXT NOT NULL REFERENCES app_user(user_id),
  imported_at            TEXT NOT NULL,
  rows_read              INTEGER NOT NULL,
  rows_created           INTEGER NOT NULL,
  rows_skipped_identical INTEGER NOT NULL,
  rows_exception         INTEGER NOT NULL
);

CREATE TABLE patient (
  mrn                TEXT PRIMARY KEY,      -- e.g. SYN-0007
  full_name          TEXT NOT NULL,
  phone              TEXT NOT NULL,
  caregiver_name     TEXT,
  caregiver_phone    TEXT,
  preferred_language TEXT NOT NULL,
  status             TEXT NOT NULL DEFAULT 'Active'
    CHECK (status IN ('Active','Paused','Agent off','Lost to follow-up','Followed up elsewhere')),
  agent_on           INTEGER NOT NULL DEFAULT 0 CHECK (agent_on IN (0,1)),
  batch_id           INTEGER NOT NULL REFERENCES import_batch(batch_id),
  created_at         TEXT NOT NULL
);

CREATE TABLE appointment (
  appointment_id   INTEGER PRIMARY KEY AUTOINCREMENT,
  mrn              TEXT NOT NULL REFERENCES patient(mrn),
  appointment_date TEXT NOT NULL,
  department       TEXT NOT NULL,
  batch_id         INTEGER NOT NULL REFERENCES import_batch(batch_id),
  UNIQUE (mrn, appointment_date, department)
);

CREATE TABLE import_exception (
  exception_id     INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id         INTEGER NOT NULL REFERENCES import_batch(batch_id),
  source_type      TEXT NOT NULL,
  row_number       INTEGER NOT NULL,        -- line in the file, header = 1
  raw_row          TEXT NOT NULL,
  reason_code      TEXT NOT NULL,
  status           TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','dismissed')),
  dismissed_by     TEXT,
  dismissed_reason TEXT,
  created_at       TEXT NOT NULL
);

CREATE TABLE consent (
  consent_id        INTEGER PRIMARY KEY AUTOINCREMENT,
  mrn               TEXT NOT NULL REFERENCES patient(mrn),
  consent_reference TEXT NOT NULL,
  scopes            TEXT NOT NULL,          -- semicolon-separated
  recorded_by       TEXT NOT NULL REFERENCES app_user(user_id),
  recorded_on       TEXT NOT NULL,
  created_at        TEXT NOT NULL
);

CREATE TABLE coordinator_task (
  task_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  mrn        TEXT NOT NULL REFERENCES patient(mrn),
  task_type  TEXT NOT NULL,                 -- e.g. consent_scope_missing
  reason     TEXT,
  status     TEXT NOT NULL DEFAULT 'open',
  created_at TEXT NOT NULL
);

CREATE TABLE care_plan (
  care_plan_id       INTEGER PRIMARY KEY AUTOINCREMENT,
  mrn                TEXT NOT NULL UNIQUE REFERENCES patient(mrn),  -- one plan per patient in Step A
  template_id        TEXT NOT NULL,
  template_version   TEXT NOT NULL,
  therapy            TEXT NOT NULL,
  treatment_end_date TEXT NOT NULL,
  identifiers_checked TEXT NOT NULL,        -- e.g. name;mrn
  confirmed_by       TEXT NOT NULL REFERENCES app_user(user_id),
  confirmed_at       TEXT NOT NULL
);

CREATE TABLE order_card (
  card_id          INTEGER PRIMARY KEY AUTOINCREMENT,
  mrn              TEXT NOT NULL REFERENCES patient(mrn),
  care_plan_id     INTEGER NOT NULL REFERENCES care_plan(care_plan_id),
  item_code        TEXT NOT NULL,           -- V1, T1, V2, T2, T3
  card_type        TEXT NOT NULL CHECK (card_type IN ('visit','test')),
  name             TEXT NOT NULL,
  due_date         TEXT NOT NULL,
  state            TEXT NOT NULL,           -- see states.py
  appointment_date TEXT,
  owner_name       TEXT NOT NULL,
  cover_name       TEXT NOT NULL,
  origin           TEXT NOT NULL DEFAULT 'plan_item',
  created_at       TEXT NOT NULL,
  UNIQUE (care_plan_id, item_code)
);

CREATE TABLE audit_event (
  audit_id        INTEGER PRIMARY KEY AUTOINCREMENT,
  ts              TEXT NOT NULL,
  actor_type      TEXT NOT NULL CHECK (actor_type IN ('person','system','agent','ai')),
  actor           TEXT NOT NULL,            -- a user_id, or 'system'
  action          TEXT NOT NULL,            -- e.g. register_imported
  entity_type     TEXT,
  entity_id       TEXT,
  mrn             TEXT,
  details_json    TEXT,
  reason_code     TEXT,                     -- used from the agent step onward
  decision_inputs TEXT,                     -- used from the agent step onward
  source_link     TEXT,
  trigger_event   TEXT, rule TEXT, task TEXT, mode TEXT,
  policy_version  TEXT, template_id TEXT, template_version TEXT,
  model           TEXT, prompt_version TEXT,
  outcome         TEXT
);

CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_event
BEGIN SELECT RAISE(ABORT, 'audit_event is append-only'); END;

CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_event
BEGIN SELECT RAISE(ABORT, 'audit_event is append-only'); END;
```

**Notes**

- The two triggers make the audit table append-only: any `UPDATE` or `DELETE` fails. `reset()` should delete the whole database file and rebuild it, rather than try to delete audit rows.
- `agent_on` starts at 0 and is changed only by recording consent (A3).
- `care_plan.mrn` is `UNIQUE`, so a second confirmation for the same patient cannot create a second plan.
- Card states are checked in code (A6), not by a `CHECK`, because the allowed states differ for visit and test cards.

## 6. Build steps A1 to A7

Build in this order and run the tests for each part before starting the next.

**Conventions for every endpoint**

- The caller is identified by a header, for example `X-User: u_priya`. A missing or unknown user gets **401**; a known user in the wrong role gets **403**. (A7 builds this; until then you may fake it in tests.)
- Errors return JSON `{"detail": "message"}`. Use **404** for a missing patient or template, **409** for a conflict (for example a plan already confirmed), and **422** for bad input.
- A failed request changes nothing: no data, no audit row.

**Audit action names** (use exactly these): `register_imported`, `appointments_imported`, `exception_dismissed`, `consent_recorded`, `plan_confirmed`, `cards_generated`, `clock_advanced`, `card_state_changed`.

### A1. Foundations

**Goal:** a database that can only change inside a transaction, an audit log nobody can edit, a demo clock, and a reset button.

**Build**

- `db.py`: `connect()` reads `CAREPILOT_DB`, opens SQLite with `isolation_level=None` (so you control transactions yourself), sets `row_factory = sqlite3.Row` and turns on foreign keys. `create_schema(conn)` runs the SQL from section 5. `tx(conn)` is the transaction helper:

```python
from contextlib import contextmanager

@contextmanager
def tx(conn):
    conn.execute('BEGIN')
    try:
        yield conn
        conn.execute('COMMIT')
    except BaseException:
        conn.execute('ROLLBACK')
        raise
```

- `audit.py`: `write_audit(conn, actor_type, actor, action, entity_type=None, entity_id=None, mrn=None, details=None, **extra)` inserts one row into `audit_event` with the current UTC time and `details` stored as JSON text. It must refuse to run outside a transaction (`assert conn.in_transaction`). Actor type is `person` (actor = a user id) or `system` (actor = `'system'`).
- `clock.py`: `get_today(conn)`, `set_today(conn, iso_date)` and `advance(conn, days)`. **No other code may read the real date for business decisions.** (The audit timestamp is the only place that reads the real time.)
- `seed.py`: `reset()` does exactly this, in order: delete the database file; create the schema; load `users.json`; load `config.json` (clock = `demo_start_date`; one `rule_thresholds` row; one `plan_template` row from `care_plan_template.json`, with its JSON stored in `content_json`); then, acting as `u_priya`, run the register import, the appointment import, and `record_consent` for each row of `consent_records.csv`. Loading users, config and the clock is set-up, so it is not audited.
- `fingerprint(conn)` returns a SHA-256 hex string of the JSON text of these queries, joined together: patients (`mrn, full_name, phone, caregiver_name, caregiver_phone, preferred_language, status, agent_on` ordered by mrn); appointments (`mrn, appointment_date, department` ordered); exceptions (`source_type, row_number, reason_code, status` ordered); consents (`mrn, consent_reference, scopes, recorded_by, recorded_on` ordered by mrn); tasks (`mrn, task_type, status` ordered); config (`type, version, content_json` ordered); users (`user_id, name, role` ordered); the clock; and the counts of care plans and order cards. It deliberately leaves out ids and timestamps, which differ on every run.

**Endpoints**

| Endpoint | Who | Result |
| --- | --- | --- |
| `GET /health` | anyone | `{"status": "ok"}` |
| `GET /clock` | any user | `{"today": "2026-11-02"}` |
| `POST /clock/advance` body `{"days": 7}` | coordinator | new date; days must be 1 to 60, else 422; audit `clock_advanced` |
| `POST /demo/reset` | coordinator | runs `reset()`, returns the counts in 4.9 |

**Tests for A1** (file `test_foundations.py`, `test_reset.py`)

- Rows written inside `tx()` are saved; an exception inside `tx()` leaves no rows behind.
- `write_audit` outside a transaction raises an error.
- `UPDATE audit_event ...` and `DELETE FROM audit_event` both raise a database error.
- After `reset()`, every count in 4.9 matches (clock, 5 users, 2 config rows, 25 patients, 10 appointments, 8 open exceptions, 24 consents, 23 agents on, 1 task, 0 plans, 0 cards, 26 audit rows).
- `advance(conn, 7)` moves 2026-11-02 to 2026-11-09.
- Calling `reset()` twice gives the same fingerprint. Changing a patient's phone and then calling `reset()` brings the fingerprint back to the original.

### A2. Import the register and the appointment list

**Goal:** turn good CSV rows into patients and appointments, and put every bad row on the exceptions list with a reason. Nothing is created silently.

**Build**

- `import_register(conn, user_id, file_bytes, file_name)` and `import_appointments(conn, user_id, file_bytes, file_name)`. Both return a summary: `batch_id, rows_read, rows_created, rows_skipped_identical, rows_exception` and the list of exceptions (`row_number, reason_code, raw_row`).
- Trim spaces from every value. Row numbers count the header as line 1, so the first data row is 2.
- If the file cannot be read as CSV or a required column is missing, return 422 and write nothing at all (no batch, no audit row).
- The whole import is **one transaction**: the batch row, the patients or appointments, the exception rows and one audit row. If anything fails, nothing is kept.
- The audit row is `register_imported` or `appointments_imported`, actor type `person`, actor = the coordinator, with the four counts in `details`. **One audit row per import, not one per patient.**
- New patients get `status = 'Active'` and `agent_on = 0`. The import only creates; it never updates an existing patient.

**Register validation, checked in this order. The first failing check decides the reason code.**

| Order | Check | Reason code |
| --- | --- | --- |
| 1 | MRN is empty | `missing_mrn` |
| 2 | MRN does not match `^SYN-\d{4}$` | `bad_mrn_format` |
| 3 | MRN already accepted earlier in this same file | `duplicate_in_file` |
| 4 | Full name is empty | `missing_name` |
| 5 | Phone does not match `^\+91[6-9]\d{9}$` | `malformed_phone` |
| 6 | Caregiver phone is given but does not match the same pattern | `malformed_caregiver_phone` |
| 7 | Language is not Kannada, Hindi or English | `unsupported_language` |
| 8 | MRN already exists in the database with exactly the same six values | **not an exception**: counted in `rows_skipped_identical` |
| 9 | MRN already exists in the database with any different value | `duplicate_existing` |

Only rows that pass checks 1 to 7 count as "accepted" for check 3. So the first valid row for an MRN wins and any later row with that MRN is a duplicate.

**Appointment validation, checked in this order**

| Order | Check | Reason code |
| --- | --- | --- |
| 1 | MRN is empty | `missing_mrn` |
| 2 | MRN is not a patient in the database | `unknown_mrn` |
| 3 | Date is not a real date written as `YYYY-MM-DD` | `bad_date` |
| 4 | Department is not Oncology OPD, Laboratory or Radiology | `unknown_department` |
| 5 | Same MRN, date and department already stored | `duplicate` |

**Endpoints (coordinator only)**

| Endpoint | Result |
| --- | --- |
| `POST /imports` (multipart: `file`, `source_type` = `register` or `appointments`) | **201** with the summary above |
| `GET /exceptions?status=open` (optional `batch_id`) | the list of exceptions |
| `POST /exceptions/{id}/dismiss` body `{"reason": "..."}` | marks it `dismissed` with who and why (reason is required, else 422); audit `exception_dismissed` |

**Tests for A2** (`test_import.py`)

- Importing `register.csv` gives `rows_read 29, rows_created 25, rows_skipped_identical 0, rows_exception 4`. The exceptions are exactly lines 14 `duplicate_in_file`, 21 `malformed_phone`, 26 `missing_mrn`, 29 `unsupported_language`.
- After that import there are 25 patients, none of them with MRN SYN-0026 or SYN-0028, and exactly one new audit row `register_imported`.
- Importing `appointments.csv` (after the register) gives `rows_read 14, rows_created 10, rows_exception 4`: line 7 `unknown_mrn`, 11 `bad_date`, 13 `duplicate`, 15 `unknown_mrn`.
- Importing `register.csv` a second time gives `rows_created 0, rows_skipped_identical 25, rows_exception 4` and still 25 patients.
- A one-row file with SYN-0012 and a different phone (`+919000099999`) gives `duplicate_existing` and leaves the stored phone unchanged.
- A valid row with MRN `SYN-31` gives `bad_mrn_format`; a row with an empty name gives `missing_name`; a bad caregiver phone gives `malformed_caregiver_phone`.
- If the audit write is forced to fail (for example by patching `write_audit` to raise an error), no patients, no batch and no audit row exist afterwards.
- A file missing the `mrn` column returns 422 and writes nothing.
- Dismissing an exception without a reason returns 422. With a reason, it is saved with the user and the audit row exists.

### A3. Record consent

**Goal:** record what each patient has agreed to, and switch the agent on only when automated messaging is allowed.

**Build**

- `record_consent(conn, user_id, mrn, consent_reference, scopes, recorded_on)`.
- The patient must exist (else 404). `scopes` must be a non-empty list, every item one of `whatsapp_patient, whatsapp_caregiver, automated_messaging, outside_records, external_care` (else 422). `recorded_on` must be a real date.
- A patient may have several consent records; **the latest one is the current one.**
- If the scopes include `automated_messaging`: set `agent_on = 1`. If they do not: set `agent_on = 0` and create one `coordinator_task` with `task_type = 'consent_scope_missing'`.
- One audit row `consent_recorded` (person), with the scopes, the resulting `agent_on` and the task id (or null) in `details`. All of this in one transaction.
- Helpers for later steps: `has_valid_consent(conn, mrn)` (true only if the latest record includes `automated_messaging`) and `consent_allows(conn, mrn, scope)`.

**Endpoint (coordinator only):** `POST /patients/{mrn}/consent` with body `{"consent_reference": "CONS-0007", "scopes": ["whatsapp_patient", "automated_messaging"], "recorded_on": "2026-10-28"}` returns 201.

**Tests for A3** (`test_consent.py`)

- After recording all 24 rows of `consent_records.csv`: 23 patients have `agent_on = 1`; SYN-0003 has `agent_on = 0` and exactly one open task `consent_scope_missing`; SYN-0013 has `agent_on = 0`, no consent row and no task.
- `has_valid_consent` is true for SYN-0007, false for SYN-0003 and false for SYN-0013.
- Recording a new consent for SYN-0003 that includes `automated_messaging` sets `agent_on = 1` (the latest record wins). The old task stays as history.
- Unknown MRN returns 404. Scope `telepathy` returns 422. An empty scopes list returns 422.
- Each successful call writes exactly one `consent_recorded` audit row; a failed call writes none.

### A4. Confirm the care plan

**Goal:** the coordinator confirms one patient's plan after checking two identifiers. This creates the plan record and the order cards together, or nothing at all.

**Build**

- `confirm_plan(conn, user_id, mrn, template_id, therapy, treatment_end_date, identifiers_checked)`.
- The system records which identifiers the coordinator ticked; it cannot check them itself. The two-identifier rule is what stops a plan being confirmed against the wrong person on a single guess.
- Checks, in this order (the first failure wins and nothing is written):
  1. Patient exists, else **404**.
  2. Template exists (find the `plan_template` config row whose JSON `template_id` matches), else **404**.
  3. `identifiers_checked` has at least 2 **different** values, all from `name`, `mrn`, `phone`, else **422**.
  4. Therapy is not empty, else **422**.
  5. Treatment end date is a real date in `YYYY-MM-DD` form, else **422**; and it is not later than the demo clock's today, else **422**.
  6. The patient has no plan yet, else **409**.
- Then, in **one transaction**: insert the `care_plan` row (store the template version); write audit `plan_confirmed` (person, the coordinator's id); create the cards with A5 and insert them; write audit `cards_generated` (system). That is two audit rows.
- **No cards exist for a patient until this succeeds.**

**Endpoints**

| Endpoint | Who | Result |
| --- | --- | --- |
| `GET /templates/BC-SURV-1` | coordinator, oncologist | the template JSON from 4.5 |
| `POST /patients/{mrn}/plan/confirm` body `{"template_id": "BC-SURV-1", "therapy": "Surgery and endocrine therapy", "treatment_end_date": "2026-05-15", "identifiers_checked": ["name", "mrn"]}` | coordinator | **201** with `{"care_plan": {...}, "cards": [5 cards]}` |

**Tests for A4** (`test_plan_cards.py`)

- Confirming SYN-0007 with its row from `plan_inputs.csv` returns 201, creates 1 care plan and 5 cards, and adds exactly 2 audit rows: `plan_confirmed` (actor type person, actor `u_priya`) and `cards_generated` (actor type system).
- Before confirming, `GET /patients/SYN-0007/cards` returns an empty list.
- One identifier (`["name"]`) returns 422; `["name", "name"]` returns 422; `["name", "fax"]` returns 422. In each case there is no plan, no card and no audit row.
- Unknown MRN `SYN-0999` returns 404. Template `XYZ` returns 404.
- An end date after the clock's today (for example `2026-12-01` when today is 2026-11-02) returns 422.
- A second confirmation for SYN-0007 returns 409 and there are still 5 cards.
- If the card insert or the second audit write is forced to fail, there is no plan, no cards and no audit rows afterwards.
- Confirming all 25 plans from `plan_inputs.csv` gives 25 plans, 125 cards and 50 new audit rows.

### A5. Generate the order cards

**Goal:** a pure function that turns the template and a treatment end date into cards. Same input, same cards, every time.

**Build**

- `generate_cards(template: dict, treatment_end_date: date) -> list[dict]`. It must not touch the database, the clock or any random value, so it is easy to test.
- For each template item, in template order, compute the due date in this order: (1) add `months` to the end date, keeping the same day of the month, or the last day of that month if it is shorter; (2) add `days` (a negative number means earlier); (3) if the result is a Saturday or Sunday, move it **forward** to the next Monday.

```python
import calendar
from datetime import date, timedelta

def add_months(d, months):
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))

def due_date(end, months, days):
    d = add_months(end, months) + timedelta(days=days)
    while d.weekday() >= 5:          # 5 = Saturday, 6 = Sunday
        d += timedelta(days=1)
    return d
```

- Each card is a dict with `item_code, card_type, name, due_date` (ISO text), `state = 'Due'`, `owner_name`, `cover_name` (both taken from the item's department) and `origin = 'plan_item'`.
- Endpoint `GET /patients/{mrn}/cards` (coordinator, oncologist) returns the patient's cards in the order they were created, with `item_code, card_type, name, due_date, state, owner_name, cover_name`.

**Tests for A5** (`test_plan_cards.py`)

- For all 25 patients, `generate_cards` with the end date from `plan_inputs.csv` gives exactly the dates in table 4.7 (V1, T1, V2, T2 and T3).
- SYN-0007: V1 2026-11-16, T1 2026-11-09, V2 2027-05-17, T2 and T3 2027-05-03.
- SYN-0014: V1 is 2026-12-07 (weekend shift). SYN-0020: V1 is 2027-03-01 and T1 is 2027-02-22 (month-end rule, then weekend shift).
- `add_months(date(2026,8,31), 6) == date(2027,2,28)` and `add_months(date(2026,11,30), 3) == date(2027,2,28)`.
- Calling `generate_cards` twice with the same input gives equal lists.
- Across all 125 cards: every due date falls on Monday to Friday; every card has a non-empty owner and cover; every card is in state `Due`.
- Owners and covers: visit cards Priya Nair / Meera Joseph; T1 Sunil Kumar / Priya Nair; T2 and T3 Lakshmi Rao / Priya Nair.

### A6. State machines for the cards

**Goal:** a card can only move along the allowed path, and the rules that protect the path live in code, not in a loose database field. Step A only creates cards in `Due`; you build the state machine now so every later step moves cards through one safe door.

**Build** (`states.py`)

```python
VISIT_FLOW = {'Due': {'Booked'}, 'Booked': {'Attended'}, 'Attended': set()}

TEST_FLOW = {
    'Due': {'Ordered'},
    'Ordered': {'Scheduled'},
    'Scheduled': {'Completed'},
    'Completed': {'Report received'},
    'Report received': {'Report verified'},
    'Report verified': set(),
}
```

- `check_transition(card_type, from_state, to_state, **ctx)` raises `IllegalTransition` with a clear message, or returns nothing if the move is allowed. First it checks the table for that card type; then it checks the guards below.
- `transition_card(conn, card_id, new_state, actor_type, actor, **ctx)` runs in one transaction: load the card, call `check_transition`, update the state (and store `appointment_date` when moving to Scheduled), and write audit `card_state_changed` with the old and new state in `details`. An illegal move changes nothing and writes no audit row.
- Step A has no endpoint for this. Later steps call `transition_card`.

| Move | Guard (what `ctx` must contain) | Why |
| --- | --- | --- |
| Visit: Due to Booked | `source` is `button_tap` or `coordinator` | A booking is recorded from a button tap or by a person. |
| Visit: Booked to Attended | `source` is `appointment_list` or `hospital_feed` | **Attendance never comes from a patient's reply.** |
| Test: Ordered to Scheduled | `appointment_date` is given | A card cannot sit at Scheduled without a date. |
| Test: Completed to Report received | `file_ref` is given | A report needs an attached file. |
| Test: Report received to Report verified | `matched_identifiers` is 2 or more | Verification needs two matching identifiers. |
| Every other legal move | none |  |

**Tests for A6** (`test_states.py`)

- Every legal move in `VISIT_FLOW` and `TEST_FLOW` is allowed when its guard is met.
- Every other pair of states raises, for example Due to Attended, Booked to Due, Attended to Booked, Due to Scheduled.
- A visit card cannot move to a test state (Due to Ordered raises). An unknown state name raises.
- Visit Due to Booked with `source='patient_reply'` raises; with `source='button_tap'` it works.
- Visit Booked to Attended with `source='patient_reply'` raises; with `source='appointment_list'` it works.
- Test Ordered to Scheduled without `appointment_date` raises; with a date it works and the card stores that date.
- Completed to Report received without `file_ref` raises. Report received to Report verified with `matched_identifiers=1` raises, with 2 it works.
- A successful `transition_card` writes one `card_state_changed` audit row; a refused one writes none and leaves the state unchanged.

### A7. Roles

**Goal:** three kinds of user, each allowed to do only their own work. There is no real login in the sprint; the header `X-User` picks the user.

**Build** (`auth.py`)

- `current_user(conn, x_user)` looks the id up in `app_user`. Missing header or unknown id gives **401**.
- `require_role(*roles)` is a FastAPI dependency. A known user whose role is not listed gets **403**. A refused call changes no data and writes no audit row.

| Endpoint | coordinator | oncologist | chief |
| --- | --- | --- | --- |
| `GET /health` | yes (no login needed) | yes | yes |
| `GET /clock` | yes | yes | yes |
| `POST /clock/advance`, `POST /demo/reset` | yes | no | no |
| `POST /imports`, `GET /exceptions`, `POST /exceptions/{id}/dismiss` | yes | no | no |
| `POST /patients/{mrn}/consent` | yes | no | no |
| `POST /patients/{mrn}/plan/confirm` | yes | no | no |
| `GET /templates/{id}` | yes | yes | no |
| `GET /patients`, `GET /patients/{mrn}`, `GET /patients/{mrn}/cards` | yes | yes | **no** |
| `GET /summary` | yes | yes | yes |

**Read endpoints to add**

- `GET /patients` returns a list of `mrn, full_name, preferred_language, status, agent_on, consent_status, has_plan`. `consent_status` is `none` (no consent record), `automated_ok` (latest record includes `automated_messaging`) or `no_automated_messaging`.
- `GET /patients/{mrn}` returns the patient's fields plus the latest consent scopes, the plan (or null), the number of cards and any open tasks. Unknown MRN gives 404.
- `GET /summary` returns **aggregates only**: `patients`, `agent_on`, `open_exceptions`, `plans_confirmed`, and `cards_by_state` (a count per state). It never contains a name, a phone number or an MRN. This is what the chief sees.

**Tests for A7** (`test_roles.py`)

- No `X-User` header gives 401. An unknown user id gives 401.
- The oncologist (`u_anand`) gets 403 on `POST /imports`, the consent endpoint, the plan-confirm endpoint, `POST /demo/reset` and `POST /clock/advance`, and 200 on `GET /patients`, `GET /patients/SYN-0007` and `GET /patients/SYN-0007/cards`.
- The chief (`u_suresh`) gets 403 on `GET /patients`, `GET /patients/SYN-0007` and `GET /patients/SYN-0007/cards`, and 200 on `GET /summary`.
- The `GET /summary` response for the chief contains none of the 25 patient names and no text starting with `SYN-`.
- Right after a reset, `GET /summary` returns `patients 25, agent_on 23, open_exceptions 8, plans_confirmed 0` and no cards. After confirming all 25 plans it returns `plans_confirmed 25` and `cards_by_state {"Due": 125}`.
- A second coordinator (`u_meera`) can do everything `u_priya` can.
- A forbidden call (for example the oncologist trying to import) leaves the patient count and the audit row count unchanged.

## 7. Complete test list

Step A is done when every test below passes from a clean `pytest` run. Each test starts from a fresh database (`conftest.py` calls `reset()` and the data files from section 4). The detailed wording of each test sits under its build step in section 6; this table is the checklist.

| Test file | Build step | What it must prove | Key numbers |
| --- | --- | --- | --- |
| `test_foundations.py` | A1 | `tx()` commits on success and rolls back on error; `write_audit` outside a transaction raises; UPDATE and DELETE on `audit_event` raise | 0 rows left after a failed transaction |
| `test_reset.py` | A1 | `reset()` builds the section 4.9 starting state; `advance(conn, 7)` moves 2026-11-02 to 2026-11-09; the fingerprint is stable across resets | 5 users, 2 config rows, 25 patients, 10 appointments, 8 open exceptions, 24 consents, 23 agents on, 1 task, 0 plans, 0 cards, 26 audit rows |
| `test_import.py` | A2 | Good rows are created, bad rows go to exceptions with the right reason, a second import creates nothing, a failed audit write rolls everything back, a missing column returns 422, dismissal needs a reason | register: 29 read, 25 created, 4 exceptions (lines 14, 21, 26, 29); appointments: 14 read, 10 created, 4 exceptions (lines 7, 11, 13, 15); re-import: 0 created, 25 skipped, 4 exceptions |
| `test_consent.py` | A3 | Scopes decide `agent_on`; the latest consent wins; bad input is refused; one audit row per success, none per failure | 23 agent\_on = 1; SYN-0003 off with 1 task; SYN-0013 off, no consent, no task |
| `test_plan_cards.py` | A4 and A5 | Plan confirmation checks run in order and are all-or-nothing; due dates match table 4.7 for all 25 patients | SYN-0007: 1 plan, 5 cards, 2 audit rows; all 25 plans: 25 plans, 125 cards, 50 new audit rows |
| `test_states.py` | A6 | Only the legal moves work, guards are enforced, a refused move changes nothing and writes no audit row | 1 audit row per successful move, 0 per refused move |
| `test_roles.py` | A7 | 401 and 403 rules from the permission matrix; the chief sees aggregates only; a forbidden call changes no data | after reset: patients 25, agent\_on 23, open\_exceptions 8, plans\_confirmed 0; after all plans: plans\_confirmed 25, cards\_by\_state {"Due": 125} |

### One end-to-end test to finish with

Put this in `tests/test_step_a_end_to_end.py`. It walks the whole of Step A the way the demo will, using only the API and the files from section 4.

1. Call `POST /demo/reset` as u\_priya. Check `GET /summary` shows 25 patients, 23 agents on, 8 open exceptions, 0 plans.
2. Call `POST /imports` with `register.csv`, then `appointments.csv`. The data is already seeded, so the register import creates 0 patients and skips 25 identical rows, and the patient count stays 25. This proves the import is safe to repeat.
3. Call `POST /patients/SYN-0007/plan/confirm` with the SYN-0007 row of `plan_inputs.csv` and identifiers `["name", "mrn"]`. Check you get 5 cards with V1 on 2026-11-16 and T1 on 2026-11-09.
4. Call the same endpoint again. Check you get 409 and still 5 cards.
5. Call `GET /patients/SYN-0007/cards` as u\_anand (200) and as u\_suresh (403).
6. Call `GET /summary` as u\_suresh. Check plans\_confirmed is 1, cards\_by\_state is {"Due": 5}, and the response contains no patient name.
7. Call `POST /demo/reset` again. Check the fingerprint equals the one from step 1 and the audit table has 26 rows.

### Numbers to keep on one page

| What | Number |
| --- | --- |
| Register rows / patients / exceptions | 29 / 25 / 4 |
| Appointment rows / stored / exceptions | 14 / 10 / 4 |
| Consent rows / agent on / consent tasks | 24 / 23 / 1 |
| Plans / cards after all confirmations | 25 / 125 |
| Audit rows after reset | 26 |
| Audit rows added per plan confirmation | 2 |
| Demo clock at reset | 2026-11-02 |

## 8. Done checklist, common mistakes, and what comes next

### Done checklist

- [ ] `pytest` passes with no skipped tests, from a fresh clone and a fresh database file.
- [ ] `POST /demo/reset` returns the same fingerprint every time.
- [ ] Every write (import, consent, plan, state change) happens inside one `tx()` and writes its audit row in the same transaction.
- [ ] The `audit_event` table rejects UPDATE and DELETE.
- [ ] No card exists for a patient who has no confirmed plan.
- [ ] A confirmation needs two different identifiers; one identifier is refused.
- [ ] All 125 due dates are Monday to Friday and match table 4.7.
- [ ] The chief's summary contains no name, phone number or MRN.
- [ ] Only synthetic data is in the repository. No real patient names, numbers or records anywhere.
- [ ] A teammate can run the end-to-end test on their own machine using only this guide.

### Common mistakes

| Mistake | What goes wrong | Fix |
| --- | --- | --- |
| Writing the audit row after the transaction ends | A crash leaves a change with no record | Call `write_audit` inside the same `tx()` block |
| Letting the import update an existing patient | A changed phone silently overwrites a good one | The import only creates; a different value is `duplicate_existing` |
| Counting a rejected row as "accepted" for the duplicate check | The wrong copy of a repeated MRN is kept | Only rows that pass checks 1 to 7 count as accepted |
| Reading today's date from the computer clock | Tests give different answers on different days | Always read the demo clock table |
| Adding months by adding 30 days | Month-end dates drift | Use `add_months` with the month-end clamp |
| Forgetting the weekend shift after adding days | Cards fall due on Saturday or Sunday | Shift forward to Monday as the last step |
| Using `isolation_level` defaults in `sqlite3` | Python opens transactions silently and `BEGIN` fails | Connect with `isolation_level=None` and use the `tx()` helper |
| Checking roles inside each function | One endpoint is forgotten and leaks data | Use `require_role` on every route and test the matrix |
| Generating cards at import or consent time | Patients get cards without a confirmed plan | Cards come only from `confirm_plan` |
| Moving a card state with a plain UPDATE | The guards are bypassed | Always go through `transition_card` |

### What comes next: G0 and G1

Step A gives the next steps a safe starting point: patients, consent, a confirmed plan and cards in `Due`. Build the gap rules in this order, each one on top of the last.

- **G0 (not booked by T-7, MVP).** Reads the cards that are still `Due` and finds the ones whose due date is seven days away or less. For each, it asks `select_action` what to do, checks consent with `has_valid_consent`, and creates a coordinator-reviewed message. It uses the demo clock, so you test it by calling `POST /clock/advance`.
- **G1 (not booked or attended by due date + 2, MVP).** Same pattern, one level later: it reads cards that are still not booked or attended two days after the due date. The exact action for each gap is set in the gap rules, not in this guide.

Both rules depend on three things from Step A: the demo clock, the consent helpers and `transition_card`. If those three work and their tests pass, G0 and G1 are a small amount of new code.
