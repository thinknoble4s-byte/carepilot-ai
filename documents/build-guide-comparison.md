# Build guides compared

Oct 11, 2026 · Compares [build-guide.md](build-guide.md) (deploy and run the AWS demo, Increment 0) with [step-a-student-build-guide.md](step-a-student-build-guide.md) (build the Step A features). Neither guide has been changed.

They cover different layers: the build guide is how to **deploy and run** the demo; the Step A guide is how to **build the features** (register import, consent, care plan, order cards). Step A is the same scope as the build guide's "Increment 1 (next)", so they overlap there, and they make different technology choices.

## Purpose and scope

| | Build guide | Step A student guide |
|---|---|---|
| Audience | Whoever deploys and operates the demo | A student building the feature logic |
| Covers | Accounts, setup, deploy, database on/off, CI, troubleshooting, cost, teardown | Key terms, mock data, tables, build steps A1–A7, full test list with exact numbers |
| Status | Describes what is built and live (Increment 0) | Specification for work not yet built |
| Mock data | None | Full CSV/JSON (29 register rows, 14 appointments, 24 consents, 25 plans) + expected due-date table |
| AWS, cost, CI | Detailed | Not covered; runs locally only |
| Sarvam / Vertex AI | Listed as accounts (Vertex not wired yet) | Out of scope ("comes after Step A") |

## Where they conflict (decide before Increment 1)

| Topic | Build guide (live repo) | Step A student guide |
|---|---|---|
| Database | PostgreSQL 16 on AWS RDS, off by default | SQLite file (`CAREPILOT_DB`, default `carepilot.db`) |
| Schema changes | Numbered SQL in `db/migrations/` + `db/migrate.py` | `create_schema()` in `db.py`; `reset()` deletes the database file and rebuilds |
| Audit table | `audit_ledger`: hash-chained, verify function, blocks UPDATE/DELETE/TRUNCATE | `audit_event`: no hash chain, blocks UPDATE/DELETE only, ~20 extra columns for later agent/AI fields |
| Audit action names | Dotted, e.g. `session.role_selected` | Snake case, e.g. `register_imported`, `plan_confirmed` |
| Config table | `config_versions`, one active version per type | `config_version`, unique (type, version); plan template + rule thresholds |
| Who is acting | "Viewing as" switcher + `POST /session`; no 401/403 | `X-User` header; 401 unknown user, 403 wrong role, full permission matrix |
| People and roles | Coordinator Priya Nair, oncologist Dr. Ananya Raghunathan, chief Dr. Mohan Rao, plus a Demo reviewer role | u_priya Priya Nair, u_meera Meera Joseph, Dr. Anand Rao, Dr. Kavita Menon, chief Dr. Suresh Pillai; no reviewer |
| `GET /health` reply | `{"api","db","env"}` | `{"status":"ok"}` |
| Front end | React page on S3 | None; tested through the API only |
| Dates | Real time | Demo clock (starts Mon 2 Nov 2026); code never reads the real date |
| Reset | None; DB stays off unless the owner approves a start | `POST /demo/reset` rebuilds the seed state (needs the DB on) |
| Folder layout | `backend/app/`, `db/`, `infra/`, `frontend/` | `carepilot/app/` (`clock.py`, `seed.py`, `importer.py`, `plan.py`, `states.py`, `auth.py`), `data/` |
| Python, dependencies | 3.12, pinned `requirements*.txt` | 3.11+, unpinned `pip install` |
| Naming | "Increment 0, 1, …" mapped to Golden Path steps | "Step A", then "G0 and G1" |

## Where they agree

FastAPI, pytest and httpx; an append-only audit trail enforced by database triggers; versioned config including rule thresholds; synthetic data only; coordinator, oncologist and chief roles with Priya Nair as coordinator; the same next feature set (import with exceptions, consent, plan confirmation, order cards with owner and cover).

## Decisions

Decided 13 Oct 2026 for Increment 1. Where the Step A guide differs, these win.

| # | Topic | Decision |
| --- | --- | --- |
| 1 | Database | **PostgreSQL 16** (RDS in the demo, any local Postgres 16 for development and CI). Step A's SQLite schema is translated into numbered migrations in `db/migrations/`; no SQLite file. |
| 2 | Audit | **Keep the hash-chained `audit_ledger`** (append-only, chain verify). Add Step A's extra fields (`mrn`, `reason_code`, `decision_inputs`, `source_link`, `trigger_event`, `rule`, `task`, `mode`, `policy_version`, `template_id`, `template_version`, `model`, `prompt_version`, `outcome`) and actor types `person`, `system`, `agent`, `ai` through a new migration. Same transaction as the action, as Step A requires. |
| 3 | Demo reset | **Built in Increment 1** as a coordinator-only `POST /demo/reset` plus a script. It empties the demo tables and reloads the Step A seed state (25 patients, 10 appointments, 8 exceptions, 24 consents, clock 2026-11-02, 0 plans, 0 cards). Clearing the ledger is the one allowed exception to its no-delete rule; the new chain starts with a reset entry. Runs only while the database is on with the owner's approval. **No nightly automatic reset** (dropped from Increment 6). The demo clock is part of it. |
| 4 | People and caller identity | **Open:** one list of people, and "Viewing as" switcher or `X-User` header. |
