"""Check literal ribbon icon references and the bundled font before building."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
WINUI = ROOT / "dev/CSLModernMapRenderer/winui"


def validate_icons(winui: Path = WINUI, *, definitions_path: Path | None = None,
                   font_css: Path | None = None) -> int:
    definitions = json.loads((definitions_path or winui / "Assets/ribbon-icons.json").read_text("utf-8"))
    css = (font_css or winui.parent / "resources/fonts/tabler-icons.css").read_text("utf-8")
    glyphs = set(re.findall(r"\.ti-([\w-]+):before", css))
    references: set[str] = set()
    for source in winui.glob("*.cs"):
        text = source.read_text("utf-8")
        references.update(re.findall(r'RibbonIcons\.Create\(\s*"([\w.-]+)"', text))
        references.update(re.findall(r'AddButton\([^;]*?,\s*"(\w+\.[\w.-]+)"', text))
    # RMP's local Button helper constructs IDs as "rmp." + command.
    rmp = (winui / "MainWindow.Rmp.cs").read_text("utf-8")
    references.update("rmp." + command for command in re.findall(
        r'\bButton\(\w+,\s*"\w+",\s*"([\w-]+)"', rmp))
    references = {reference for reference in references if not reference.endswith(".")}
    missing = sorted(references - definitions.keys())
    if missing:
        raise ValueError("Unregistered ribbon icon IDs: " + ", ".join(missing))
    for icon_id, definition in definitions.items():
        if not isinstance(definition, list) or len(definition) != 2 or definition[0] not in glyphs:
            raise ValueError(f"Invalid bundled ribbon icon: {icon_id}: {definition}")
    return len(references)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", type=Path)
    args = parser.parse_args()
    options = {} if args.app_dir is None else {
        "definitions_path": args.app_dir / "Assets/ribbon-icons.json",
        "font_css": args.app_dir / "Assets/Fonts/tabler-icons.css",
    }
    print(f"Validated {validate_icons(**options)} literal ribbon icon references")
