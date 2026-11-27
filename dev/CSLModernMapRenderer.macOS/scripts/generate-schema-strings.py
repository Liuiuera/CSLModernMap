"""Refresh native translation-key fallbacks from the shared language catalogs."""
import json
from pathlib import Path

native = Path(__file__).resolve().parents[1]
catalogs = native.parent / "CSLModernMapRenderer/resources/i18n"
strings = {}
for language in ("zh-CN", "en", "de", "es", "fr", "it", "ja", "ko", "pl", "pt-BR", "ru", "zh-TW"):
    source = json.loads((catalogs / f"{language}.json").read_text(encoding="utf-8"))
    strings[language] = {key: value for key, value in source.items()
                         if key.startswith(("gui.", "style.", "ui.winui_engine."))}
(native / "Resources/NativeSchemaStrings.json").write_text(
    json.dumps(strings, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
