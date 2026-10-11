# CarePilot AI — MVP demo

Follow-up and continuity-of-care agent for cancer survivors. This repo builds the MVP Golden Path in increments (see section 10 of `architecture/carepilot_technical_architecture_aws_sarvam_vertex.html`).

**Current state: Increment 0 (walking skeleton).** Public demo page with a "Viewing as" role switcher, the API on a Lambda Function URL, PostgreSQL with a hash-chained audit ledger, and the database stopped automatically when it isn't used.

> **Demo profile:** synthetic data only, security deferred (no login, public endpoints, public database with a password). Do not load real patient data into this stack.

## Layout

| Path | What |
|---|---|
| `infra/` | AWS CDK (Python): S3 website, Lambda + Function URL, RDS PostgreSQL 16, DB stop/start schedules, budget |
| `infra/lambdas/db_idle_stop/` | Stops the database after 60 minutes with no API calls |
| `backend/` | FastAPI app (`app/`), Lambda handler, `build.py` (Linux arm64 bundle, no Docker needed) |
| `db/` | SQL migrations and `migrate.py` |
| `frontend/` | React + Vite shell |
| `tests/` | API tests (no DB) and audit-ledger tests (need `TEST_DATABASE_URL`) |
| `scripts/` | `deploy.sh`, `db.sh start|stop|status` (Git Bash) |

## Database stops when not used

| Mechanism | When |
|---|---|
| Idle stop | Every 30 min: if the API had no calls in the last 60 min and the DB is running, stop it (`dbIdleMinutes` in `infra/cdk.json`) |
| Nightly stop | 21:00 IST every day (`dbStopCron`) |
| Start on demand | "Start database" button on the demo page, or `scripts/db.sh start` (ready in about 5 minutes) |
| Optional weekday start | 06:30 IST Mon–Fri, **off by default** (`CAREPILOT_DB_AUTOSTART=true` in `.env` to enable) |

AWS restarts a stopped RDS instance after 7 days; the idle stop catches that within 30 minutes. Direct DB sessions (e.g. migrations) do not count as activity, so run them right after starting.

## First deploy

Prerequisites: AWS CLI logged in to your account (`aws configure`), Python 3.12, Node 20+, Git Bash.

```bash
cp .env.example .env          # set CAREPILOT_DB_PASSWORD (16+ random chars) and BUDGET_EMAIL
npx aws-cdk@2 bootstrap aws://<account-id>/ap-south-1   # once per account/region
scripts/deploy.sh             # builds backend + frontend, deploys, prints the demo URL
scripts/db.sh status          # wait for "available" (first create takes ~10 min)
pip install -r backend/requirements-dev.txt
python db/migrate.py          # creates the audit ledger and config tables
```

Open the printed **Demo URL**, switch roles, and each switch appears in the audit trail with an intact hash chain.

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

## Next: Increment 1

Register and appointment import with MRN matching and exceptions, plan confirmation, consent, and order cards with owner and cover (Golden Path Steps 0–1).
