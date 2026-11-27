"""Generate unambiguous shared translations from WinUI resources."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET


native = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--winui-strings", type=Path,
                    default=native.parent / "CSLModernMapRenderer/winui/Strings")
args = parser.parse_args()

languages = {
    "en-US": "en", "de-DE": "de", "es-ES": "es", "fr-FR": "fr",
    "it-IT": "it", "ja-JP": "ja", "ko-KR": "ko", "pl-PL": "pl",
    "pt-BR": "pt-BR", "ru-RU": "ru", "zh-TW": "zh-TW",
}


def read(locale):
    return {item.attrib["name"]: item.findtext("value") or ""
            for item in ET.parse(args.winui_strings / locale / "Resources.resw").findall("data")}


def placeholders(value):
    return sorted(re.findall(r"\{\d+(?:[^{}]*)\}|%(?:\d+\$)?[\d.]*[@dfis]", value))


source = read("zh-CN")
result = {}
for win_locale, mac_locale in languages.items():
    translations = read(win_locale)
    groups = defaultdict(set)
    for name, chinese in source.items():
        translated = translations.get(name, "")
        if chinese and translated:
            groups[chinese].add(translated)
    result[mac_locale] = {
        chinese: next(iter(values))
        for chinese, values in groups.items()
        if len(values) == 1
        and placeholders(chinese) == placeholders(next(iter(values)))
    }

output = native / "Resources/WinUIStrings.json"
output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                  encoding="utf-8")
print(f"{output}: {sum(map(len, result.values()))} translations across {len(result)} languages")
