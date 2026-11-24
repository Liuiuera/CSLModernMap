"""Build macOS UI catalogs using shared translations and native overrides."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import re

native = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--target", type=Path, default=native)
parser.add_argument("--check", action="store_true", help="Verify coverage and generated resources without writing")
args = parser.parse_args()
target = args.target
resources = native / "Resources"
shared = native.parent / "CSLModernMapRenderer/resources/i18n"
base = json.loads((resources / "NativeStrings.json").read_text())
win = json.loads((resources / "WinUIStrings.json").read_text())
langs = ("de", "en", "es", "fr", "it", "ja", "ko", "pl", "pt-BR", "ru", "zh-TW")
schema = {lang: json.loads((shared / f"{lang}.json").read_text())
          for lang in ("zh-CN", *langs)}


def norm(value):
    return value.strip().strip("….。").casefold()


def unambiguous(pairs):
    values = defaultdict(set)
    for key, value in pairs:
        if key and value:
            values[norm(key)].add(value)
    return {key: next(iter(group)) for key, group in values.items() if len(group) == 1}


def placeholders(value):
    return (re.findall(r"%[\d.]*[@dfis]", value),
            sorted(re.findall(r"\{[^{}]+\}", value)))


keys = set()
for file in (native / "Sources").glob("*.swift"):
    if file.name != "Localization.swift":
        keys.update(re.findall(r'"([^"\\\n]*[\u4e00-\u9fff][^"\\\n]*)"', file.read_text()))

# Explicit native translations and aliases have priority over automatic matching.
rows = {}
for line in (resources / "NativeLocalization.tsv").read_text().splitlines():
    if not line or line.startswith("#"):
        continue
    fields = line.split("\t")
    if len(fields) == 2:
        rows.setdefault(fields[0], {}).update({"en": fields[1]})
    elif len(fields) == 12:
        rows.setdefault(fields[0], {}).update(zip(langs, fields[1:]))
    else:
        raise ValueError(f"Expected 2 or 12 columns: {fields[0]}")

aliases = {}
for line in (resources / "NativeLocalizationAliases.tsv").read_text().splitlines():
    if line and not line.startswith("#"):
        key, alias = line.split("\t")
        aliases[key] = alias
keys.update(rows)
keys.update(aliases)

result = {}
for lang in langs:
    shared_pairs = [(chinese, schema[lang].get(key, ""))
                    for key, chinese in schema["zh-CN"].items()]
    direct = unambiguous(shared_pairs)
    direct_base = unambiguous(base.get(lang, {}).items())
    from_en = unambiguous([
        *((v, win[lang].get(k, "")) for k, v in win["en"].items()),
        *((v, base.get(lang, {}).get(k, "")) for k, v in base["en"].items()),
        *((v, schema[lang].get(k, "")) for k, v in schema["en"].items()),
    ])
    preferred = unambiguous(win[lang].items())
    catalog = {}
    for key in sorted(keys):
        english = rows.get(key, {}).get("en") or rows.get(norm(key), {}).get("en") or base["en"].get(key)
        alias = aliases.get(key, key)
        value = (rows.get(key, {}).get(lang) or win[lang].get(key) or preferred.get(norm(key))
                 or win[lang].get(alias) or preferred.get(norm(alias))
                 or base.get(lang, {}).get(alias) or direct_base.get(norm(alias)) or direct.get(norm(alias))
                 or (from_en.get(norm(english)) if english else None)
                 or (english if lang == "en" else None))
        if value and placeholders(key) == placeholders(value):
            if key.endswith("…") and not value.endswith("…"):
                value += "…"
            catalog[key] = value
    result[lang] = catalog

out = target / "Resources"
schema_result = {lang: {k: v for k, v in catalog.items() if k.startswith(("gui.", "style.", "ui.winui_engine."))}
                 for lang, catalog in schema.items()}
failed = False
for name, data in (("NativeUILocalization.json", result), ("NativeSchemaStrings.json", schema_result)):
    expected = json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    path = out / name
    if args.check:
        if not path.is_file() or path.read_text() != expected:
            print(f"Stale generated resource: {path}")
            failed = True
    else:
        path.write_text(expected)

# These literals are symbols or language autonyms, not UI translation keys.
exempt = {"…", "日本語", "繁體中文"}
for lang, catalog in result.items():
    missing = keys - catalog.keys() - exempt
    print(f"{lang}: {len(catalog)}/{len(keys - exempt)} translated, {len(missing)} missing")
    if missing:
        print("\n".join(sorted(missing)))
        failed = True
    if schema_result[lang].keys() != schema_result["zh-CN"].keys():
        print(f"{lang}: schema key set differs from source")
        failed = True
    for key, value in schema_result[lang].items():
        if not value or placeholders(value) != placeholders(schema_result["zh-CN"].get(key, "")):
            print(f"{lang}: invalid schema translation: {key}")
            failed = True

if args.check:
    # Detect common native controls that bypass localized() or the shared UI wrappers.
    sink = re.compile(r'(?:labelWithString|wrappingLabelWithString|withTitle|NSMenu\(title|NSMenuItem\(title|\.stringValue\s*|\.title\s*)\s*[:=]\s*"[^"\n]*[\u4e00-\u9fff]|showStatus\(\s*"[^"\n]*[\u4e00-\u9fff]')
    for file in (target / "Sources").glob("*.swift"):
        for number, line in enumerate(file.read_text().splitlines(), 1):
            if sink.search(line):
                print(f"Unlocalized UI literal: {file.name}:{number}")
                failed = True
if failed:
    raise SystemExit(1)
