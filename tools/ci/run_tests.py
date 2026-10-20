"""Run the regression suite and keep a readable test log."""
from pathlib import Path
import io
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    output = ROOT / "artifacts"
    output.mkdir(exist_ok=True)
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    report = stream.getvalue()
    (output / "tests.txt").write_text(report, encoding="utf-8")
    print(report, end="")
    return 0 if result.wasSuccessful() and result.testsRun > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
