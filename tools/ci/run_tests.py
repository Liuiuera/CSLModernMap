"""Run the public regression suite and keep a readable test log."""
from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    output = ROOT / "artifacts"
    output.mkdir(exist_ok=True)
    report = output / "tests.txt"
    with report.open("w", encoding="utf-8") as stream:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(ROOT / "tests"), "-q",
             "-p", "no:cacheprovider", "--basetemp", str(output / "pytest")],
            cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        )
    print(report.read_text(encoding="utf-8"), end="")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
