"""Run the whole test suite against a throwaway local PostgreSQL (no AWS, nothing to install
beyond the Python dev requirements). The database is deleted afterwards.

    pip install -r backend/requirements-dev.txt
    python scripts/run_tests.py            # extra arguments go to pytest, e.g. -k usecase1
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pgserver

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    server = pgserver.get_server(tempfile.mkdtemp(prefix="carepilot_test_pg_"), cleanup_mode="delete")
    try:
        uri = server.get_uri()
        env = {**os.environ, "DATABASE_URL": uri, "TEST_DATABASE_URL": uri}
        for var in ("DB_HOST", "DB_INSTANCE_ID"):
            env.pop(var, None)
        subprocess.run([sys.executable, str(ROOT / "db" / "migrate.py")], env=env, check=True)
        return subprocess.run([sys.executable, "-m", "pytest", "-q", str(ROOT / "tests"), *sys.argv[1:]],
                              env=env, cwd=ROOT).returncode
    finally:
        server.cleanup()


if __name__ == "__main__":
    sys.exit(main())
