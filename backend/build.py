"""Build the Lambda bundle in backend/.build without Docker.

Installs Linux arm64 wheels for Python 3.12 (the Lambda runtime), then copies
the app code next to them. Run from anywhere: `python backend/build.py`.
"""
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / ".build"


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    subprocess.run(
        [
            sys.executable, "-m", "pip", "install",
            "-r", str(HERE / "requirements.txt"),
            "--target", str(OUT),
            "--platform", "manylinux2014_aarch64",
            "--platform", "manylinux_2_28_aarch64",
            "--implementation", "cp",
            "--python-version", "3.12",
            "--only-binary=:all:",
            "--quiet",
        ],
        check=True,
    )
    shutil.copytree(HERE / "app", OUT / "app", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(HERE / "handler.py", OUT / "handler.py")
    print(f"Lambda bundle ready: {OUT}")


if __name__ == "__main__":
    main()
