"""Compile gettext PO catalogs with Babel; JSON files are generated audit views."""
from __future__ import annotations

import argparse
from io import BytesIO
import json
from pathlib import Path

from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po

ROOT = Path(__file__).resolve().parents[2] / "dev/CSLModernMapRenderer/resources/i18n"


def compile_catalogs(root: Path = ROOT, *, check: bool = False) -> None:
    manifest = {}
    outputs = {}
    for po_path in sorted(root.glob("*/LC_MESSAGES/cslmodernmap.po")):
        with po_path.open("rb") as stream:
            catalog = read_po(stream)
        errors = [(message.id, list(message.check(catalog))) for message in catalog if message.id]
        errors = [(key, problems) for key, problems in errors if problems]
        if errors:
            raise ValueError(f"{po_path}: {errors}")
        stream = BytesIO()
        write_mo(stream, catalog)
        outputs[po_path.with_suffix(".mo")] = stream.getvalue()
        language = po_path.parent.parent.name
        entries = {message.id: message.string for message in catalog
                   if isinstance(message.id, str) and message.id and message.string and not message.fuzzy}
        manifest[language] = sorted(entries)
        outputs[root / f"{language}.json"] = (json.dumps(entries, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if not manifest:
        raise ValueError(f"No PO catalogs in {root}")
    outputs[root / "catalogs.json"] = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
    stale = []
    for path, content in outputs.items():
        if path.is_file() and path.read_bytes() == content:
            continue
        if check:
            stale.append(str(path))
        else:
            path.write_bytes(content)
    if stale:
        raise ValueError("Recompile gettext catalogs: " + ", ".join(stale))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--check", action="store_true", help="Check generated MO and JSON files without writing")
    args = parser.parse_args()
    compile_catalogs(args.root, check=args.check)
