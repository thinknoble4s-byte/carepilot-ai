"""Demo reset from the command line: rebuild a seed state.

    python db/demo_reset.py            # Use Case 1 data (default)
    python db/demo_reset.py step_a     # Golden Path / Step A data

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
        result = seed.reset(conn, scenario=sys.argv[1] if len(sys.argv) > 1 else seed.DEFAULT_SCENARIO)
    print(json.dumps(result, default=str, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
