"""Demo reset from the command line: rebuild the Step A seed state.

Same as POST /demo/reset. The database must be running, and starting it needs
the owner's approval (scripts/db.sh start). Connection settings as db/migrate.py.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "db"))
sys.path.insert(0, str(ROOT / "backend"))

from migrate import load_env  # noqa: E402


def main() -> int:
    load_env()
    from app import db, seed

    with db.connect() as conn:
        result = seed.reset(conn)
    print(json.dumps(result, default=str, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
