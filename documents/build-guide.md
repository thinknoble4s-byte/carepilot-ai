# CarePilot AI — Build Guide

Oct 11, 2026 · @Barani · Live version: [Claude Doc](https://claude.ai/code/artifact/e38441c6-8f1d-4bea-b5ce-44e4251f5f03)

## Overview

One command builds and deploys the whole CarePilot demo to AWS ap-south-1 (Mumbai). The database stays off unless the owner starts it. The repo is at Increment 0 (walking skeleton): a public page with a role switcher and light/dark theme, an API, and PostgreSQL with a hash-chained audit trail.

| What | Where |
| --- | --- |
| Demo website | [carepilotdemo-sitee53d7754-entyxsqdcchy.s3-website.ap-south-1.amazonaws.com](http://carepilotdemo-sitee53d7754-entyxsqdcchy.s3-website.ap-south-1.amazonaws.com) |
| API (Lambda Function URL) | https://hlnrhsyghhwbxummjvsm6jxiiq0yisiy.lambda-url.ap-south-1.on.aws/ |
| Code | github.com/thinknoble4s-byte/carepilot-ai (private) |
| AWS stack | CarePilotDemo, account 090427454998, ap-south-1 |

Demo profile: synthetic data only, and security is deferred to the pilot. There is no login, the endpoints are public and the database is reachable with a password. Never load real patient data into this stack.

```mermaid
flowchart LR
  B["Visitor's browser<br/>demo page, any device"] -- loads page --> S3["S3 static website<br/>React page + config.json"]
  B -- API calls --> API["API Lambda<br/>Function URL, FastAPI<br/>Python 3.12, arm64"]
  API --> RDS[("RDS PostgreSQL 16<br/>off unless owner starts<br/>hash-chained audit ledger")]
  API --> SV["Sarvam AI<br/>speech and translation"]
  SCH["EventBridge Scheduler<br/>idle stop after 60 min<br/>nightly stop 21:00 IST"] -- stops --> RDS
  OWN["Owner, with approval<br/>scripts/db.sh start / stop"] -- start / stop --> RDS
```

The page and the API are always available; only the owner or the stop schedules change the database's state, and the website cannot start it.

## Prerequisites

You need three accounts and five local tools. Everything below runs on the AWS Free plan and its credits (US$139.96, expiring 2026-12-17).

| Account | Used for | Notes |
| --- | --- | --- |
| AWS (Free plan) | Hosting: S3, Lambda, RDS, EventBridge Scheduler, Budgets | Region ap-south-1. CDK is bootstrapped there already. |
| GitHub | Private repo and CI (GitHub Actions) | The pushing token needs the `workflow` scope to change `.github/workflows`. |
| Sarvam AI | Indian-language speech, translation and voice | The key lives only in `.env` and the API Lambda's settings, never in the browser. |
| Google Cloud, Vertex AI | General LLM (Gemini, asia-south1, prepaid) | Not wired yet; it arrives with the AI increments. |

| Tool | Version | Check |
| --- | --- | --- |
| AWS CLI | v2, logged in (`aws configure`) | `aws sts get-caller-identity` |
| Python | 3.12 | `python --version` |
| Node.js | 20 or later (CI uses 22) | `node --version` |
| Git Bash | any recent | runs `scripts/*.sh` on Windows |
| AWS CDK | v2, via `npx aws-cdk@2` | no global install needed |

Docker is not needed: the backend is packaged for Lambda (Linux arm64) by `backend/build.py` using pip's platform wheels.

## Repository layout

Infrastructure, backend, database, frontend and scripts each have one folder.

| Path | What it holds |
| --- | --- |
| `infra/` | AWS CDK app (Python): `app.py`, `carepilot_infra/stack.py`, `cdk.json` (settings), `cdk.context.json` (cached AZs, committed) |
| `infra/lambdas/db_idle_stop/` | Lambda that stops the DB after 60 minutes with no API calls |
| `backend/app/` | FastAPI app: `main.py` (endpoints), `db.py`, `audit.py`, `roles.py` |
| `backend/handler.py` | Lambda entry point (Mangum) |
| `backend/build.py` | Builds `backend/.build`, the Linux arm64 bundle for Lambda |
| `db/migrations/` | Numbered SQL files; `001_init.sql` creates the audit ledger and config tables |
| `db/migrate.py` | Applies new migrations, tracked in `schema_migrations` |
| `frontend/` | React 18 + Vite 5 page; reads `config.json` for the API URL |
| `tests/` | `test_api.py` (no DB needed), `test_audit_ledger.py` (needs `TEST_DATABASE_URL`) |
| `scripts/deploy.sh` | Build everything and deploy |
| `scripts/db.sh` | `start`, `stop`, `status` for the demo DB |
| `.github/workflows/ci.yml` | CI: Postgres 16 service, migrate, tests, build, CDK synth |
| `documents/` | Guides and design notes (this file) |
| `.env` | Local secrets and settings, git-ignored; copy from `.env.example` |

## One-time setup

Clone the repo, fill in `.env`, and bootstrap CDK once per account and region. Run all commands from the repo root in Git Bash.

1. Clone the repo.

   ```bash
   git clone https://github.com/thinknoble4s-byte/carepilot-ai.git
   cd carepilot-ai
   ```

2. Create `.env` from the template and fill it in.

   ```bash
   cp .env.example .env
   ```

3. Bootstrap CDK. This is already done for account 090427454998 in ap-south-1, so skip it there.

   ```bash
   npx aws-cdk@2 bootstrap aws://<account-id>/ap-south-1
   ```

4. Create a Python virtual environment for the CDK app and dev tools.

   ```bash
   python -m venv .venv && source .venv/Scripts/activate
   pip install -r infra/requirements.txt -r backend/requirements-dev.txt
   ```

| `.env` key | Value | Used by |
| --- | --- | --- |
| `AWS_REGION` | `ap-south-1` | CDK app, `db.sh` |
| `CAREPILOT_DB_PASSWORD` | 16+ random characters | RDS master password, API Lambda |
| `BUDGET_EMAIL` | the account owner's email | US$30/month budget alert (80% actual, 100% forecast) |
| `SARVAM_API_KEY` | key from the Sarvam dashboard | API Lambda only (server side) |
| `CAREPILOT_DB_AUTOSTART` | `false` | keep `false`: the DB is started only by the owner |

Never commit `.env` or paste its values into chat or tickets. `.gitignore` already excludes it.

## Build and deploy

`scripts/deploy.sh` builds and deploys everything in about 5 minutes and prints the demo and API URLs. The deploy itself does not start the database.

```bash
scripts/deploy.sh
```

What it runs, in order:

1. `python backend/build.py`: installs the backend's Linux arm64 wheels into `backend/.build` and copies the app.
2. `npm ci && npm run build` in `frontend/`: writes `frontend/dist`.
3. `npx aws-cdk@2 deploy --outputs-file outputs.json` in `infra/`: creates or updates the stack and writes `infra/outputs.json` (git-ignored; `db.sh` and `migrate.py` read it).
4. Prints `WebsiteUrl` and `ApiUrl` from the outputs.

The deploy writes `config.json` (the API URL) next to the website files, so the page always calls the right API.

### First deploy only: create the tables

The database is created empty and the idle stop switches it off soon after. Ask the owner before starting it, then run the migration and stop it again.

```bash
scripts/db.sh start      # owner approval first; about 5 minutes
scripts/db.sh status     # repeat until it prints: available
python db/migrate.py     # applies db/migrations/*.sql not yet applied
scripts/db.sh stop
```

### Check it worked

- [ ] The demo URL loads and shows "API running".
- [ ] With the DB running, switching **Viewing as** adds a row to the audit trail.
- [ ] The audit trail says "Hash chain intact".
- [ ] `POST <ApiUrl>db/start` returns 404 (the public cannot start the DB).
- [ ] The DB is stopped again afterwards (`scripts/db.sh status` prints `stopped`).

## Database on and off

The database is always off unless the owner approves a start for a demo or a test run, and it is stopped right after. The website and API cannot start it; visitors see "Database is off" until it runs.

| When | Command or mechanism |
| --- | --- |
| Before a demo or tests (owner approves) | `scripts/db.sh start`, then `scripts/db.sh status` until `available` (about 5 minutes) |
| Right after | `scripts/db.sh stop` |
| Safety net: idle stop | Every 30 minutes; stops the DB if the API had no calls in the last 60 minutes (`dbIdleMinutes` in `infra/cdk.json`) |
| Safety net: nightly stop | 21:00 IST every day (`dbStopCron`) |
| Weekday auto-start | Disabled (`CAREPILOT_DB_AUTOSTART=false`); keep it disabled |

- AWS restarts a stopped RDS instance after 7 days. The idle stop switches it off again within about 30 minutes.
- Direct database sessions (migrations, ledger tests) do not count as API activity, so run them right after starting.
- `scripts/db.sh stop` fails while the instance is in a transitional state (for example `configuring-enhanced-monitoring` after a start). Wait for `available`, then stop.
- When working with Claude Code in this repo, Claude asks for approval before every database start.

## Local development, tests and CI

Most work needs no AWS database: run the API tests without one, and use any local PostgreSQL 16 for the rest.

```bash
pip install -r backend/requirements-dev.txt
pytest -q tests                                    # API tests, no DB needed

# against a local Postgres 16
export DATABASE_URL=postgresql://user:pass@localhost:5432/carepilot
python db/migrate.py
TEST_DATABASE_URL=$DATABASE_URL pytest -q tests     # adds the audit-ledger tests
cd backend && uvicorn app.main:app --reload --port 8000

# frontend (another terminal)
cd frontend && npm install && npm run dev            # uses http://localhost:8000 when there is no config.json
```

| Test file | Needs DB | Checks |
| --- | --- | --- |
| `tests/test_api.py` | No | health answers with the DB down, role list, bad role rejected, session needs DB, no public `/db/start` |
| `tests/test_audit_ledger.py` | Yes (`TEST_DATABASE_URL`) | hash chain, append-only (UPDATE/DELETE/TRUNCATE blocked), chain verify |

CI (`.github/workflows/ci.yml`) runs on every push to GitHub: it starts a Postgres 16 service, applies migrations, runs all tests, builds backend and frontend, and runs `cdk synth`. It never touches AWS. Synth uses `CDK_DEFAULT_ACCOUNT=090427454998` so it matches the committed `cdk.context.json`.

## Making changes and redeploying

Every change follows the same loop: change, test locally, push (CI goes green), deploy. Only schema changes need the database on.

| Change | Steps | DB needed |
| --- | --- | --- |
| Frontend only (page, styles, theme) | `cd frontend && npm run build`, then `cd infra && npx aws-cdk@2 deploy --outputs-file outputs.json`; reload the page | No |
| Backend (API code, dependencies) | `python backend/build.py`, then the CDK deploy | No |
| Infrastructure or settings (`stack.py`, `cdk.json`) | `cd infra && npx aws-cdk@2 diff` to review, then deploy | No |
| Database schema | Add `db/migrations/00N_<name>.sql` (never edit an applied file), test locally, deploy, then start the DB with approval, `python db/migrate.py`, stop it | Yes, briefly |
| `.env` value (e.g. Sarvam key) | Edit `.env`, then the CDK deploy (it copies the value to the Lambda) | No |
| Everything at once | `scripts/deploy.sh` | No |

- Settings that change behaviour without code live in `infra/cdk.json`: `dbIdleMinutes`, `dbStopCron`, `dbStartCron`, `dbAutoStart`, `budgetUsd`, `appEnv`.
- The audit ledger is append-only by design. Fix wrong entries by appending a correcting entry, never by editing rows.
- Commit with a clear message and push; check that CI is green before demoing.

## Troubleshooting

These are the problems actually hit while building Increment 0, with the fix that worked.

| Symptom | Cause | Fix |
| --- | --- | --- |
| Page says "Database is off" | DB stopped (the normal state) | Owner approves, `scripts/db.sh start`, wait for `available`, reload |
| `db.sh stop` fails with InvalidDBInstanceState | Instance still in a transitional state after start | Wait until `status` prints `available`, then stop |
| DB stops right after a deploy or start | Idle stop: no API calls in 60 minutes | Expected. Open the demo page (that is an API call) or run migrations straight after starting |
| CI synth: "Need to perform AWS calls for account 111111111111" | Synth account did not match `cdk.context.json` | Set `CDK_DEFAULT_ACCOUNT` to the real account in `ci.yml` (done) |
| `git push` rejected for workflow files | Token lacks the `workflow` scope | Re-authenticate with `gh auth refresh -s workflow` |
| CDK cannot find the Python venv on Windows | `C:/` style path in `PATH` | Use the Git Bash form `/c/...` for the venv `Scripts` folder |
| AWS CLI values break scripts (trailing `\r`) | Windows line endings in CLI output | Pipe through `tr -d '\r'` |
| `backend/.build is missing` or `frontend/dist is missing` at synth | Build step skipped | Run `python backend/build.py` and `npm run build`, or use `scripts/deploy.sh` |
| `CAREPILOT_DB_PASSWORD` error at synth | `.env` missing or password under 16 characters | Fill in `.env` |
| Migration fails on `pgcrypto` | Extension not available everywhere | Schema uses the built-in `sha256()`; do not add `pgcrypto` |

## Cost, teardown and next increments

With the database off between demos, the stack costs a few dollars a month, all drawn from the free credits. Running the DB around the clock would be about US$20–25 a month, mostly RDS (t4g.micro, 20 GB gp3, public IPv4). A US$30 monthly budget alert emails the owner at 80% actual and 100% forecast.

To remove everything (a final DB snapshot is kept; delete it in the RDS console if not needed):

```bash
cd infra && npx aws-cdk@2 destroy
```

| Increment | Adds | Golden Path steps |
| --- | --- | --- |
| 0 (done) | Walking skeleton: page, roles, theme, API, audit ledger, DB on/off | — |
| 1 (next) | Register and appointment import, MRN matching and exceptions, plan confirmation, consent, order cards with owner and cover | 0–1 |
| 2–6 | Morning run and gap checks, then the AI features (Sarvam voice and translation, Vertex AI Gemini), as set out in the build plan | 2–9 |

Each increment uses the same build, deploy and DB-approval loop in this guide. Section 10 of `architecture/carepilot_technical_architecture_aws_sarvam_vertex.html` holds the full build plan.
