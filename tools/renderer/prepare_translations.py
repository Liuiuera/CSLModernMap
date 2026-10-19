"""Create translation work units and a map back to the native resource catalogs."""
from __future__ import annotations

import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def prepare(output: Path) -> None:
    units, bindings = {}, {}
    renderer = ROOT / "dev/CSLModernMapRenderer"

    def add(module, key, chinese, english="", context=""):
        identity = hashlib.sha256((chinese + "\0" + english).encode()).hexdigest()[:14]
        unit = units.setdefault(identity, {"id": identity, "zh": chinese, "reference": english, "contexts": []})
        if context and context not in unit["contexts"]:
            unit["contexts"].append(context)
        bindings.setdefault(module, {})[key] = identity

    def xml(path):
        return {node.get("name"): (node.findtext("value"), node.findtext("comment", ""))
                for node in ET.parse(path).getroot().findall("data")}

    english = xml(renderer / "winui/Strings/en-US/Resources.resw")
    for key, (chinese, context) in xml(renderer / "winui/Strings/zh-CN/Resources.resw").items():
        add("winui", key, chinese, english.get(key, ("",))[0], context or key)
    chinese = json.loads((renderer / "resources/i18n/zh-CN.json").read_text(encoding="utf-8"))
    english = json.loads((renderer / "resources/i18n/en.json").read_text(encoding="utf-8"))
    for key, value in chinese.items():
        add("python", key, value, english.get(key, ""), key)
    for key, value in json.loads((renderer / "viewer/src/locales/zh-CN.json").read_text(encoding="utf-8")).items():
        add("viewer", key, value, context="Web preview: " + key)
    add("language_metadata", "automatic", "自动翻译", context="Annotation appended in each translated language to its autonym in the viewer's language picker; Simplified Chinese is the original and has no annotation")
    values = sorted(units.values(), key=lambda unit: unit["id"])
    output.mkdir(parents=True, exist_ok=True)
    (output / "source.json").write_text(json.dumps({"units": values, "bindings": bindings}, ensure_ascii=False, indent=2), encoding="utf-8")
    jobs = output / "jobs"
    jobs.mkdir(exist_ok=True)
    for offset in range(0, len(values), 160):
        (jobs / f"{offset // 160:02d}.json").write_text(json.dumps(values[offset:offset + 160], ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Translation units: {len(values)}; module keys: { {key:len(value) for key,value in bindings.items()} }")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    prepare(arguments.output)
