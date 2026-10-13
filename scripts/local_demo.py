"""Run the whole CarePilot demo on this machine, with no AWS and no cost.

Starts a throwaway PostgreSQL (pgserver), applies the migrations, loads the
demo data, then serves the API on http://127.0.0.1:8000 and the page on
http://127.0.0.1:5173. Ctrl+C stops everything and deletes the database.

    pip install -r backend/requirements-dev.txt
    python scripts/local_demo.py
"""
import functools
import http.server
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "frontend" / "dist"
PAGE_PORT, API_PORT = 5173, 8000


def build_frontend() -> None:
    if (DIST / "index.html").exists():
        return
    npm = shutil.which("npm")
    if not npm:
        sys.exit("frontend/dist is missing and npm is not installed: install Node.js 20+ first")
    print("Building the page (first run only) ...")
    subprocess.run([npm, "ci"], cwd=ROOT / "frontend", check=True)
    subprocess.run([npm, "run", "build"], cwd=ROOT / "frontend", check=True)


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def main() -> None:
    import pgserver
    import uvicorn

    build_frontend()
    data_dir = tempfile.mkdtemp(prefix="carepilot_pg_")
    server = pgserver.get_server(data_dir, cleanup_mode="delete")
    try:
        # Never touch a real database: only this throwaway one.
        os.environ["DATABASE_URL"] = server.get_uri()
        for var in ("DB_HOST", "DB_INSTANCE_ID"):
            os.environ.pop(var, None)
        subprocess.run([sys.executable, str(ROOT / "db" / "migrate.py")], check=True, env=os.environ)
        subprocess.run([sys.executable, str(ROOT / "db" / "demo_reset.py")], check=True, env=os.environ,
                       stdout=subprocess.DEVNULL)

        handler = functools.partial(QuietHandler, directory=str(DIST))
        page = http.server.ThreadingHTTPServer(("127.0.0.1", PAGE_PORT), handler)
        threading.Thread(target=page.serve_forever, daemon=True).start()

        print(f"\nCarePilot local demo is running (synthetic data, local database, no AWS).")
        print(f"  Open  http://127.0.0.1:{PAGE_PORT}")
        print("  Stop  Ctrl+C (the local database is deleted)\n")
        sys.path.insert(0, str(ROOT / "backend"))
        uvicorn.run("app.main:app", host="127.0.0.1", port=API_PORT, log_level="warning")
    finally:
        server.cleanup()


if __name__ == "__main__":
    main()
