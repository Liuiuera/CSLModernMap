"""Prepare reviewed Markdown pages for a separate GitHub Wiki checkout."""
import argparse
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "docs/wiki"
REPO_URL = "https://github.com/Liuiuera/CSLModernMap/blob/main/"


def wiki_text(source: Path) -> str:
    def convert(match):
        label, target = match.groups()
        path, marker, anchor = target.partition("#")
        candidate = (source.parent / path).resolve()
        if path and not target.startswith(("http:", "https:", "mailto:")):
            if candidate.parent == SOURCE.resolve() and candidate.suffix == ".md":
                return f"[{label}]({candidate.stem}{marker}{anchor})"
            if candidate.is_relative_to(ROOT) and candidate.exists():
                return f"[{label}]({REPO_URL}{candidate.relative_to(ROOT).as_posix()}{marker}{anchor})"
        return match.group(0)
    return re.sub(r"\[([^\]]*)\]\(([^)\s]+)\)", convert, source.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    destination = args.output.resolve()
    if destination == SOURCE.resolve() or destination.is_relative_to(SOURCE.resolve()):
        parser.error("Choose a destination outside docs/wiki")
    destination.mkdir(parents=True, exist_ok=True)
    for source in sorted(SOURCE.glob("*.md")):
        (destination / source.name).write_text(wiki_text(source), encoding="utf-8")
    print(f"Prepared {len(list(SOURCE.glob('*.md')))} pages: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
