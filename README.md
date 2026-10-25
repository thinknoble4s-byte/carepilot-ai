# CarePilot AI — MVP demo

Follow-up and continuity-of-care agent for cancer survivors. This repo builds the MVP Golden Path in increments (see section 10 of `architecture/carepilot_technical_architecture_aws_sarvam_vertex.html`).

**Current state: Increments 1–3 (Golden Path Steps 0–8, no AI yet).**
- **Increment 1, records and plan:** register and appointment import with exceptions, consent, plan confirmation with two identifiers, order cards with owner and cover, card state rules, roles (coordinator, oncologist, chief), demo clock and demo reset. Spec: `documents/step-a-student-build-guide.md`; where it differs, `documents/build-guide-comparison.md` (Decisions) wins.
- **Increment 2, the deterministic loop:** the 07:00 gap check (G0 not booked by T-7, with a coordinator task at T-3; G1 missed item), the 07:30 agent run that checks consent, status, handover and the autonomy policy and records a reason code and the next planned action, the send-guard and outbox, approved Kannada/Hindi/English templates, a WhatsApp simulator, and button replies that update cards with no AI call.
- **Increment 3, the human safety path:** handovers (from a free-text reply, HELP, or opened by the coordinator), all automated sends blocked while one is open, Gate 1 coordinator, Gate 2 doctor's explicit Urgent/Routine, same-day urgent call, Gate 3 instruction creating a new order card and booking message, deadlines in working days with escalation to the named cover, and closure only by a named person with a reason, after which follow-up resumes.
- Until Sarvam arrives (Increment 4), free text has no classifier; that counts as uncertain, so it always takes the safe handover path.
- **Use Case 1** (`usecase-docs/CarePilot_UseCase1_Outbound_Messaging_v1.1.docx`) is the **default demo data**: TP001–TP003 at Sanjeevini Clinic, Anita (owner) and Ravi (cover), one "Review visit" whose due date the coordinator enters, rule G0 only, one message to the patient, replies saved without action, no tasks. **Reset demo** offers it or the Golden Path data (Sunita Verma and the 24 Step A patients, everything switched on). The differences are switches stored with each data set (`scenario_settings`), not separate code; tests UC1-T1 to UC1-T4 are in `tests/test_usecase1.py`.
- Permanent rules from Use Case 1: gaps go open → reminder_sent or blocked (with reason code gap_actionable / held_no_consent / held_not_active / held_agent_off / held_template_missing), and blocked gaps are checked again each run; the 07:30 run re-reads the card; one reminder per gap is enforced by the database; repeat runs log a suppressed duplicate; the sender refuses a language without an approved template; every audit entry carries the demo date and job time (07:00 / 07:30). The demo bar has **Set date**, **Run 07:00 gap check** and **Run 07:30 reminders**.
- **Use Case 2a** (`usecase-docs/CarePilot_UseCase2a_Reply_and_Voice_Note_v1.2.docx`; review of the gaps in `usecase-docs/UC2a_gap_review.html`): **Reset demo → After Use Case 1** loads Meena (TP001) and Kavita (TP004) with the T-7 reminder already sent at 07:30 on Mon 9 Nov and the clock at 10:15. A "Booked" tap moves the card to Booked (source: patient button, no AI). A voice note (pre-recorded clips HI-01 / HI-02, synthetic stand-ins made with Sarvam Bulbul v3) is stored in the database (`audio_blob`), transcribed and translated by Sarvam Saaras v3, and asked one yes/no question by `sarvam-105b` (Sarvam-30B is deprecated). Yes, doubt or any AI failure → fixed acknowledgement (English fallback), handover, send block (only `handover_ack` and `instruction_booking` pass, for anyone); no → an AI-drafted proposal for the coordinator and a hold on routine reminders (a setting, pending sign-off). The doctor chooses then submits; the instruction (text + days) creates a neutral "Doctor-requested visit" card that G0 never touches. Replayed event ids create nothing new. The demo bar adds a time of day and a **Sarvam** on/off switch (test UC2a-T4). Tests UC2a-T1 to T6 are in `tests/test_usecase2a.py` and use a simulated Sarvam; `scripts/local_demo.py` uses the real Sarvam when `SARVAM_API_KEY` is in `.env` (git-ignored), otherwise the simulated one.
- **Use Case 2b** (`usecase-docs/CarePilot_UseCase2b_Button_Replies_and_HELP_v1.2.docx`): **Reset demo → Use Case 2b** loads Meena (TP001), Kavita (TP004), Lakshmi (TP005), Farida (TP006) and Rekha (TP007), reminder sent 9 Nov 07:30, clock 10:15. "Please help me book", "I can't come" and a typed HELP (exact whole-message match, also मदद / ಸಹಾಯ, case, spaces and trailing punctuation ignored) each create one task for a person — owner and cover from the card, due 2/2/1 working days (`deadline_policy`) — with no AI and nothing sent to the patient. Routine reminders are held while the task is open (`held_open_task`, a setting). The task panel on the Patient page logs call attempts and closes the task with a result and a remarks note: Record booking (date and time; card becomes "Booked (confirmed by coordinator)"), Could not book, Rebooked, Reason recorded (cost, pre-authorisation, transport, work or family, other), Sorted, Needs follow-up, or Not reached (after two unanswered calls). The coordinator closes the task, never the order card. A sentence that only contains "help" goes to the Use Case 2a health check. In the other data sets HELP still opens a handover (`help_keyword` setting) and there is no task hold. Tests UC2b-T1 to T9 are in `tests/test_usecase2b.py`.
- **Use Case 2c** (`usecase-docs/CarePilot_UseCase2c_Urgent_Call_Today_v1.2.docx`): **Reset demo → Use Case 2c** loads Meena (TP001), Kavita (TP004), Lakshmi (TP005), Rekha (TP007) and Farida (TP006) "after Use Case 2a, step 6": each sent voice note HI-01 (flag yes from the 2a run, no AI call in the seed), the agent stopped, and Anita sent the handover to Dr. Sekhar. The clock starts at Tue 10 Nov 09:30. When the doctor submits Urgent, the system at once creates one "Call the patient today" task (owner Anita by 16:00, cover Ravi by 18:00, times in the `urgent_call` setting), shown first in Needs you with a red Urgent tag and on the Patient page. The task panel shows the words, translation, audio, the doctor and the instruction (or "Waiting for the doctor's instruction"); it logs call attempts. No answer keeps it open (no "Not reached"); Reached needs a note and may record the booking on the Doctor-requested visit card. Pass to cover needs a reason. Setting the demo time to 16:00 moves the same task to Ravi (once); 18:00 marks it Overdue. Close handover stays disabled until the call is Done. The doctor sees "Call pending", "Moved to cover" or "Reached by … at …" (read-only), and the chief sees the median time to call. Tests UC2c-T1 to T10 are in `tests/test_usecase2c.py`.
- **Use Case 2T v1.4** (`usecase-docs/CarePilot_UseCase2T_Test_Cycle_v1.4.docx`, the v1.3 changes after Shiv's review of 24 October; migration 011): **Reset demo → Use Case 2T** loads Meena (TP001), Kavita (TP004), Lakshmi (TP005), Farida (TP006), Rekha (TP007), Geeta (TP008), Shobha (TP009) and Asha (TP010), each with "Blood test (CBC)" (test code CBC) due Mon 23 Nov and a Review visit due 14 Dec (Meena's already Booked for Mon 14 Dec 10:30; Rekha's plan has the test only). Plans are confirmed on D0 (Mon 2 Nov) with no task and no message.
  - **Five states:** Due → Booked → Completed → Report received → Report verified (Ordered and Scheduled are gone). A date is optional on Booked. The Booked tap moves the card to Booked, with the label "Patient says booked (no date)", and closes the gap. Record booking or the appointment list add the date ("Booked (confirmed by coordinator)"). Two extra moves: a walk-in (Due → Completed from the list), and an outside document that is auto-matched or coordinator-verified (Due/Booked → Report received).
  - **Who verifies:** the system verifies a hospital report (the MRN and the test code both match: Report received → Report verified in the same run). A coordinator verifies an outside report. The doctor's decision does not need Report verified.
  - **Report check** (07:00, then 07:30): once the test date has passed (1 working day after the later of the appointment date and the due date), the hospital data is looked at first. A report found there is linked with no message (`report_check_skipped`). Otherwise the agent sends "Test report check" with three buttons:
    - **Done at an outside lab:** the upload request goes at once. If no file arrives, one nudge after 2 working days, then a Call about test task 2 working days later.
    - **Done at the hospital:** a Find hospital report task.
    - **Not done yet:** the check repeats after 3 working days, at most 2 more times, then a call task.
    - **No answer in 2 working days:** a call task.

    The SIMULATED lab can put a report "only in the hospital data".
  - **Outside report:** held Unverified, and identifiers read by the simulated Sarvam Vision (never result values). Then seven hard checks (report type, test name, patient name, report date, legible, not a duplicate, known sender) and a rule score out of 100 (name 30, test name 25, document type 15, age and sex 15, report date 10, sender 5). The routing:
    - All checks pass and the score is 95 or more: **auto-matched**. The card goes to Report received with "Outside report (auto-matched 97, not yet confirmed by a person)", the doctor gets the review item, and the coordinator gets a copy in **Copies for your information** (no due date). She may Confirm (→ Report verified) or Flag as wrong (doctor's item withdrawn, card back to its earlier state, a "Call the patient" task; after a decision the doctor sees "Report flagged after your decision").
    - The wrong kind of report or an unreadable file: the approved "possible wrong report" message, once. A second wrong file makes a call task instead.
    - Anything else (a score below 95, a different name, a future date, a duplicate, an unknown number): a "Verify outside report" task with the score and the match facts.

    Photos from one sender within 10 minutes are grouped as one document.
  - **Routing setting:** the threshold, the weights and **Shadow / Live** are a versioned setting that only the clinical lead (Dr. Sekhar) changes; each change is audited. In Shadow mode every outside report waits for the coordinator. The demo is Live; a pilot starts in Shadow. **Run the synthetic document test set** reports how many reports that should not pass reached the threshold (target zero).
  - **The doctor's "Report ready for review"** shows the road, how the report was checked (system, coordinator, or "auto-matched {score}, not yet confirmed"), the match facts, the report date, days since due, the reason for delay and the patient's own note. Continue as planned (one approved message naming the next visit, or a coordinator task when there is none) or Needs discussion (instruction → Doctor-requested visit card and booking message). After 1 working day the item moves to the doctor's cover (Dr. Anand Rao). CarePilot never shows a result value.
  - **Owner's changes of 25 Oct 2026 (override v1.4 REQ-05, REQ-25 and REQ-27):** "Booked" names what is booked ("Appointment booked", "Blood test booked"). A lab-test reminder has a fourth button, **Done at an outside lab** (4 buttons in the simulator; real WhatsApp allows 3). That tap, or the same answer to the report check, moves the test Booked (or Due) → **Completed** (source `patient_outside`) and sends the upload request; the upload timer runs as before. The upload box is at the top of the right-hand column of the Messages pop-up and is highlighted while a report is awaited. An outside report now also needs our **MRN** (hard check G8): when the name, the MRN and the test match, every hard check passes and the score is 95 or more, the card moves Report received → **Report verified by the system**, with no coordinator copy and no person confirming it; a report without our MRN, or anything else short of that, goes to the coordinator. The Golden Path routing is Live with MRN weight 25 (`report_routing.json` v1.1); Use Case 2T keeps its v1.4 weights.
  - Tests UC2T-T1 to T14 are in `tests/test_usecase2t.py`.
  - **Golden Path data too:** the same test cycle is switched on in the Step A data (`settings.json` v1.1 `test_cycle`). Its seven test-cycle messages (test reminder, report check, upload request, upload nudge, possible wrong report and the two "continue as planned" messages) are in English, Hindi and Kannada, with Dr. Anand Rao and Hospital A; Hindi and Kannada are drafts for a native speaker to check. Routing is in **Shadow** with Dr. Anand Rao as clinical lead (`report_routing.json`), and the deadlines add Find hospital report, Call about test, Call the patient (resend) and Verify outside report (`deadline_policy.json` v1.1). Golden Path patients have no age or sex, so the age/sex check (G3) never passes and an outside report always goes to the coordinator. Test: Savitri Patil (SYN-0011, Kannada) in `tests/test_care_plans.py`.
- **Care plans T1–T3 (replace BC-SURV-1 and the use-case plans, in every data set):** `backend/app/seed_data/care_plan_templates.json` holds the only plan templates: **T1 hormone receptor positive** (`BC-HRPOS-1`), **T2 hormone receptor negative** (`BC-HRNEG-1`) and **T3 HER2 positive** (`BC-HER2-1`). Visits fall at months 3, 6, 9 and 12 after the end of treatment, and each visit's tests are due 7 days before it. T1 has blood test, mammogram and DEXA at every visit, plus ultrasound abdomen at 6 and 12 months and PET-CT at 12. T2 has blood test and mammogram, plus ultrasound abdomen at 6 and 12 and PET-CT at 12. T3 has blood test and mammogram, plus 2D Echo and ultrasound abdomen at 6 and 12 and PET-CT at 12. **No data set seeds a plan** (the use-case paragraphs above describe their documented scripts; after a Reset those patients start with no plan, no reminder sent and no handover open): on each Patient page the coordinator chooses one of the three plans and enters the therapy and treatment end date. Test cards follow two roads (`order_card.specimen`, migration 010):
  - **Specimen-based (blood test):** Due → Booked → Completed → Report received → Report verified.
  - **Non-specimen (imaging, echo):** Due → Booked → Completed → Verified; it has no lab report.

  **Use Case 2T v1.4 decides who verifies:** a lab report is verified by its two identifiers (the system for a hospital report, a coordinator for an outside one). Only a non-specimen test is set Verified by the doctor (**Tests to verify** in Needs my decision, or the Verify button on the Patient page). Record booking moves a test Due → Booked. The SIMULATED appointment list and lab and the outside-report upload work in every data set (outside of Use Case 2T the routing setting is Shadow, so every outside report goes to the coordinator). A test code used at several visits (CBC at months 3, 6, 9 and 12) matches the earliest open card.
- **Screens match prototype v7.2** (`prototype/carepilot_ai_prototype_v7.2.html`; its stylesheet is copied verbatim into `frontend/src/prototype.css`): Workspace worklist with statuses and reasons, the patient page, Patient journey timeline with origin tracing, Needs my decision, Intake & verification (upload → preview → apply), Care Plan, Performance Indicators and a filterable Audit trail, each fed by real records. Panels that need data not built yet (documents, medicines, refills, measured KPIs) say so instead of showing invented values.

> **Demo profile:** synthetic data only, security deferred (no login, public endpoints, public database with a password). Do not load real patient data into this stack.

## Layout

| Path | What |
|---|---|
| `infra/` | AWS CDK (Python): S3 website, Lambda + Function URL, RDS PostgreSQL 16, DB stop/start schedules, budget |
| `infra/lambdas/db_idle_stop/` | Stops the database after 60 minutes with no API calls |
| `backend/` | FastAPI app (`app/`), Lambda handler, `build.py` (Linux arm64 bundle, no Docker needed) |
| `db/` | SQL migrations and `migrate.py` |
| `frontend/` | React + Vite shell |
| `backend/app/seed_data/` | Synthetic demo data from the Step A guide (register, appointments, consents, plan inputs, template, users) |
| `tests/` | API tests (no DB) and Increment 1 tests (need `TEST_DATABASE_URL`; they reset that database, so never point it at one you care about) |
| `documents/` | Build guide, Step A spec, decisions |
| `scripts/` | `deploy.sh`, `db.sh start|stop|status` (Git Bash) |

## Database is off by default

The database stays **stopped** and is started only by the owner, for a scheduled demo or a test run. The public website cannot start it; visitors see "Database is off" until it is running.

| Action | How |
|---|---|
| Start before a demo or tests | `scripts/db.sh start` (ready in about 5 minutes; check with `scripts/db.sh status`) |
| Stop right after | `scripts/db.sh stop` |
| Safety net: idle stop | Every 30 min: stopped if the API had no calls in the last 60 min (`dbIdleMinutes` in `infra/cdk.json`) |
| Safety net: nightly stop | 21:00 IST every day (`dbStopCron`) |
| Weekday auto-start | Disabled (`CAREPILOT_DB_AUTOSTART=false`); leave off to keep the DB always off |

AWS restarts a stopped RDS instance after 7 days; the idle stop switches it off again within 30 minutes. Direct DB sessions (e.g. migrations) do not count as activity, so run them right after starting.

## First deploy

Prerequisites: AWS CLI logged in to your account (`aws configure`), Python 3.12, Node 20+, Git Bash.

```bash
cp .env.example .env          # set CAREPILOT_DB_PASSWORD (16+ random chars) and BUDGET_EMAIL
npx aws-cdk@2 bootstrap aws://<account-id>/ap-south-1   # once per account/region
scripts/deploy.sh             # builds backend + frontend, deploys, prints the demo URL
scripts/db.sh start           # the DB is off by default; wait for "available" (scripts/db.sh status)
pip install -r backend/requirements-dev.txt
python db/migrate.py          # creates the tables
python db/demo_reset.py       # loads Use Case 1 data (add `step_a` for the Golden Path data)
scripts/db.sh stop            # switch it off again
```

Open the printed **Demo URL**. As the coordinator you see the Workspace (patients, exceptions, tasks, import, Advance 7 days, Reset demo) and can confirm a plan on the Patient journey; the oncologist reads; the chief sees the aggregate Summary only. Every action appears in the audit trail with an intact hash chain.

## Demo reset

`POST /demo/reset` (coordinator only, the **Reset demo** button) or `python db/demo_reset.py` empties the demo tables and reloads the seed data from `backend/app/seed_data/`: 25 patients, 10 appointments, 8 open exceptions, 24 consents (23 agents on, 1 task), demo date 2026-11-02, no plans or cards, and 27 audit entries starting with a `demo_reset` entry. Clearing the ledger is its only allowed exception to append-only. It needs the database running, so it is done during an approved demo session; there is no automatic nightly reset.

## For collaborators: pull, build, run

You need Python 3.12, Node.js 20+ and Git. No AWS account is needed to run or test locally.

```bash
git clone https://github.com/thinknoble4s-byte/carepilot-ai.git   # first time; afterwards: git pull
cd carepilot-ai
python -m venv .venv && source .venv/Scripts/activate              # Windows Git Bash; macOS/Linux: .venv/bin/activate
pip install -r backend/requirements-dev.txt
python scripts/local_demo.py      # builds the page when needed, starts everything: open http://127.0.0.1:5173
python scripts/run_tests.py       # the full test suite on a throwaway local PostgreSQL
```

The local demo starts on **Use Case 1** data; **Reset demo** switches to the Golden Path data. Deploying to AWS and starting the AWS database are owner-only (see below).

## Local demo (no AWS, no cost)

Runs the full demo on your machine with a throwaway local PostgreSQL; the AWS database is never touched.

```bash
pip install -r backend/requirements-dev.txt
python scripts/local_demo.py      # open http://127.0.0.1:5173 ; Ctrl+C stops and deletes the local database
```

## Local development

```bash
pip install -r backend/requirements-dev.txt
pytest -q tests                                   # API tests run without a DB
# against any Postgres 16:
DATABASE_URL=postgresql://... python db/migrate.py
TEST_DATABASE_URL=postgresql://... pytest -q tests
cd backend && DATABASE_URL=postgresql://... uvicorn app.main:app --reload --port 8000
cd frontend && npm install && npm run dev        # uses http://localhost:8000 when no config.json
```

## Cost (demo profile)

About US$20–25 a month with the database running all the time, mostly RDS (t4g.micro + 20 GB + public IPv4). With the stop schedules above it is typically well under that. A US$30 monthly budget alert is created when `BUDGET_EMAIL` is set.

## Tear down

```bash
cd infra && npx aws-cdk@2 destroy   # a final DB snapshot is kept; delete it in the RDS console if not needed
```

## Morning run

The Golden Path runs the gap check at 07:00 and the agent at 07:30. Because the database is off by default, there is no schedule: the coordinator's **Run the morning check** button (or `POST /demo/morning-run`) runs the deadline check, the gap check and the agent in that order. Use **Advance 1 day / 7 days** to move the demo clock.

Rules, the autonomy policy (live / shadow / off per task), deadlines and message wording are versioned config in `config_versions`, loaded from `backend/app/seed_data/` on demo reset. Kannada and Hindi wording are drafts for a native speaker to check.

## Next

Use Case 2a covered the Sarvam reply path (see above). Still open from the review: native Hindi recordings to replace the synthetic clips, clinician sign-off of the Hindi acknowledgement and booking drafts, the emergency line in the acknowledgement (D5), and sign-off of the hold (decision 10).
