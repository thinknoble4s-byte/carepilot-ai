# CarePilot AI — MVP demo

Follow-up and continuity-of-care agent for cancer survivors. This repo builds the MVP Golden Path in increments (see section 10 of `architecture/carepilot_technical_architecture_aws_sarvam_vertex.html`).

**Current state: Increment 1 (records and plan, Golden Path Steps 0–1).** On top of the Increment 0 skeleton (public page, "Viewing as" switcher, Lambda API, PostgreSQL with a hash-chained audit ledger, database off by default): register and appointment import with exceptions, consent, care plan confirmation with two identifiers, order cards with owner and cover, card state rules, roles (coordinator, oncologist, chief), a demo clock and demo reset. The detailed spec is `documents/step-a-student-build-guide.md`; where it differs, `documents/build-guide-comparison.md` (Decisions) wins.

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
python db/demo_reset.py       # loads the synthetic demo data (25 patients, demo date 2026-11-02)
scripts/db.sh stop            # switch it off again
```

Open the printed **Demo URL**. As the coordinator you see the Workspace (patients, exceptions, tasks, import, Advance 7 days, Reset demo) and can confirm a plan on the Patient journey; the oncologist reads; the chief sees the aggregate Summary only. Every action appears in the audit trail with an intact hash chain.

## Demo reset

`POST /demo/reset` (coordinator only, the **Reset demo** button) or `python db/demo_reset.py` empties the demo tables and reloads the seed data from `backend/app/seed_data/`: 25 patients, 10 appointments, 8 open exceptions, 24 consents (23 agents on, 1 task), demo date 2026-11-02, no plans or cards, and 27 audit entries starting with a `demo_reset` entry. Clearing the ledger is its only allowed exception to append-only. It needs the database running, so it is done during an approved demo session; there is no automatic nightly reset.

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

## Next: Increment 2

The deterministic loop: the G0 gap check (not booked 7 days before due), send-guard, outbox, WhatsApp simulator and patient buttons, reading thresholds from `config_versions` and moving cards only through `transition_card`.
