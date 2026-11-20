"""Run public regression tests and keep a readable test log."""
import argparse
from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]

SMOKE_TESTS = (
    "tests/renderer/test_png_export_labels.py::"
    "test_png_contains_each_enabled_name_and_respects_switches[cs1-512-False]",
    "tests/renderer/test_artwork.py::test_latest_revision_and_document_are_required_before_export",
    "tests/renderer/test_artwork.py::test_raster_quality_does_not_mutate_layout_or_object_set[1]",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("smoke", "full"), default="full")
    args = parser.parse_args()
    targets = SMOKE_TESTS if args.suite == "smoke" else (str(ROOT / "tests"),)
    output = ROOT / "artifacts"
    output.mkdir(exist_ok=True)
    report = output / "tests.txt"
    with report.open("w", encoding="utf-8") as stream:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", *targets, "-q",
             "-p", "no:cacheprovider", "--basetemp", str(output / "pytest")],
            cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        )
    print(report.read_text(encoding="utf-8"), end="")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
