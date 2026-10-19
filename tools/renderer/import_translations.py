"""Validate completed translation units and write framework-native catalogs."""
from __future__ import annotations

import argparse
import json
import re
from io import BytesIO
from pathlib import Path
import xml.etree.ElementTree as ET

from babel.messages.catalog import Catalog
from babel.messages.pofile import write_po
from compile_locales import compile_catalogs

ROOT = Path(__file__).resolve().parents[2]
RENDERER = ROOT / "dev/CSLModernMapRenderer"


def read_json(path: Path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{path}: duplicate key {key}")
            result[key] = value
        return result

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)


def placeholders(value):
    return sorted(re.findall(r"\{\{[^{}]+\}\}|(?<!\{)\{[^{}]+\}(?!\})", value))


def boundary_newlines(value):
    return len(value) - len(value.lstrip("\n")), len(value) - len(value.rstrip("\n"))


def import_catalogs(directory: Path, *, check: bool = False):
    source = read_json(directory / "source.json")
    units = {unit["id"]: unit for unit in source["units"]}
    if len(units) != len(source["units"]):
        raise ValueError("Duplicate source unit IDs")
    options = json.loads((RENDERER / "resources/i18n/languages.json").read_text(encoding="utf-8"))
    translations = {"zh-CN": {key: unit["zh"] for key, unit in units.items()}}
    for option in options:
        language = option["code"]
        if language == "zh-CN":
            continue
        entries = {}
        for path in sorted((directory / "translations" / language).glob("*.json")):
            batch = read_json(path)
            if not isinstance(batch, dict):
                raise ValueError(f"{path}: translation batch must be an object")
            if entries.keys() & batch.keys():
                raise ValueError(f"Duplicate units: {path}")
            entries.update(batch)
        if entries.keys() != units.keys():
            raise ValueError(f"{language}: missing {len(units.keys() - entries.keys())}, extra {len(entries.keys() - units.keys())}")
        for key, value in entries.items():
            chinese = units[key]["zh"]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{language}/{key}: empty translation")
            if placeholders(value) != placeholders(chinese):
                raise ValueError(f"{language}/{key}: placeholders changed")
            if boundary_newlines(value) != boundary_newlines(chinese):
                raise ValueError(f"{language}/{key}: boundary line breaks changed")
        translations[language] = entries

    if check:
        print(f"Validated {len(units)} units for {len(options)} languages; all placeholders matched.")
        return

    def catalog(module, language):
        return {key: translations[language][unit] for key, unit in source["bindings"][module].items()}

    def json_file(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def xml_file(template, path, values):
        tree = ET.parse(template)
        for node in tree.getroot().findall("data"):
            node.find("value").text = values[node.get("name")]
        path.parent.mkdir(parents=True, exist_ok=True)
        tree.write(path, encoding="utf-8", xml_declaration=True)

    version = read_json(ROOT / "version.json")["version"]
    winui_template = RENDERER / "winui/Strings/zh-CN/Resources.resw"
    for option in options:
        language = option["code"]
        if language != "zh-CN":
            xml_file(winui_template, RENDERER / f"winui/Strings/{option['resourceLanguage']}/Resources.resw", catalog("winui", language))
            po = Catalog(locale=language.replace("-", "_"), domain="cslmodernmap", project="CSLModernMap", version=version, fuzzy=False)
            for key, value in catalog("python", language).items():
                po.add(key, value)
            stream = BytesIO()
            write_po(stream, po, width=100, sort_output=True)
            path = RENDERER / f"resources/i18n/{language}/LC_MESSAGES/cslmodernmap.po"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(stream.getvalue())
            json_file(RENDERER / f"viewer/src/locales/{language}.json", catalog("viewer", language))
        option["automaticAnnotation"] = "" if language == "zh-CN" else catalog("language_metadata", language)["automatic"]
    json_file(RENDERER / "resources/i18n/languages.json", options)
    compile_catalogs()
    print(f"Imported {len(units)} units for {len(options)} languages; all placeholders matched.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--check", action="store_true", help="Validate without changing catalogs")
    arguments = parser.parse_args()
    import_catalogs(arguments.directory, check=arguments.check)
