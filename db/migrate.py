"""Apply db/migrations/*.sql in order, once each.

Connection: DATABASE_URL, or DB_HOST/DB_PASSWORD (+ DB_PORT, DB_NAME, DB_USER).
DB_HOST defaults to the deployed endpoint in infra/outputs.json and DB_PASSWORD
to CAREPILOT_DB_PASSWORD in .env. The database must be running (scripts/db.sh start).
"""
import json
import os
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "db" / "migrations"


def load_env() -> None:
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    outputs = ROOT / "infra" / "outputs.json"
    if outputs.exists() and "DB_HOST" not in os.environ:
        stack = next(iter(json.loads(outputs.read_text(encoding="utf-8")).values()))
        os.environ["DB_HOST"] = stack["DbHost"]
    os.environ.setdefault("DB_PASSWORD", os.environ.get("CAREPILOT_DB_PASSWORD", ""))


def main() -> int:
    load_env()
    sys.path.insert(0, str(ROOT / "backend"))
    from app.db import conninfo

    with psycopg.connect(conninfo(), autocommit=True) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        done = {row[0] for row in conn.execute("SELECT name FROM schema_migrations")}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name in done:
                continue
            print(f"Applying {path.name} ...")
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
        print("Migrations up to date.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
