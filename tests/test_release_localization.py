"""Verify shipped catalogs, including the new map and metro controls."""
import gettext
from io import BytesIO
import json
from pathlib import Path
import re
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "dev/CSLModernMapRenderer"


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate translation key: {key}")
        result[key] = value
    return result


def read(path):
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)


def flatten(values, prefix=""):
    return {name: value for key, item in values.items()
            for name, value in (flatten(item, prefix + key + ".").items()
                                if isinstance(item, dict) else [(prefix + key, item)])}


def fields(value):
    return sorted(re.findall(r"\{\{?([A-Za-z_0-9]+)[^{}]*\}\}?", value))


class ReleaseLocalizationTests(unittest.TestCase):
    def test_engine_catalogs_are_complete_and_compile(self):
        folder = RENDERER / "resources/i18n"
        source = read(folder / "en.json")
        for language in read(folder / "languages.json"):
            code = language["code"]
            with self.subTest(language=code):
                translated = read(folder / f"{code}.json")
                self.assertEqual(source.keys(), translated.keys())
                from babel.messages.mofile import write_mo
                from babel.messages.pofile import read_po
                with (folder / code / "LC_MESSAGES/cslmodernmap.po").open("rb") as stream:
                    catalog = read_po(stream)
                compiled = BytesIO()
                write_mo(compiled, catalog)
                compiled.seek(0)
                runtime = gettext.GNUTranslations(compiled)
                for key, value in source.items():
                    self.assertTrue(translated[key], (code, key))
                    self.assertEqual(fields(value), fields(translated[key]), (code, key))
                    self.assertEqual(runtime.gettext(key), translated[key], (code, key))

    def test_viewer_has_all_feature_text_in_each_language(self):
        folder = RENDERER / "viewer/src/locales"
        source = read(folder / "en.json")
        for language in read(RENDERER / "resources/i18n/languages.json"):
            code = language["code"]
            with self.subTest(language=code):
                translated = read(folder / f"{code}.json")
                self.assertEqual(source.keys(), translated.keys())
                for key, value in source.items():
                    self.assertTrue(translated[key], (code, key))
                    self.assertEqual(fields(value), fields(translated[key]), (code, key))

    def test_metro_custom_controls_and_toolbar(self):
        folder = ROOT / "dev/third_party/rmp/src/i18n/translations"
        source = {key: value for key, value in flatten(read(folder / "en.json")).items()
                  if key.startswith(("cmm.", "embed."))}
        for language in read(RENDERER / "resources/i18n/languages.json"):
            code = {"zh-CN": "zh-Hans", "zh-TW": "zh-Hant"}.get(language["code"], language["code"])
            with self.subTest(language=code):
                translated = flatten(read(folder / f"{code}.json"))
                self.assertFalse(source.keys() - translated.keys())
                for key, value in source.items():
                    if key != "cmm.direction4":  # Decorative center of the direction grid.
                        self.assertTrue(translated[key], (code, key))
                    self.assertEqual(fields(value), fields(translated[key]), (code, key))

    def test_winui_resources_have_all_keys_and_placeholders(self):
        folder = RENDERER / "winui/Strings"
        def entries(path):
            return {node.get("name"): node.findtext("value", "")
                    for node in ET.parse(path).getroot().findall("data")}
        source = entries(folder / "en-US/Resources.resw")
        for language in read(RENDERER / "resources/i18n/languages.json"):
            code = language["resourceLanguage"]
            with self.subTest(language=code):
                translated = entries(folder / code / "Resources.resw")
                self.assertEqual(source.keys(), translated.keys())
                for key, value in source.items():
                    self.assertEqual(fields(value), fields(translated[key]), (code, key))

    def test_station_preview_assets_cover_the_registry(self):
        folder = ROOT / "dev/third_party/rmp"
        source = (folder / "src/constants/stations.ts").read_text(encoding="utf-8")
        registry = source.split("export enum StationType {", 1)[1].split("}", 1)[0]
        station_types = re.findall(r"= '([^']+)'", registry)
        self.assertTrue(station_types)
        for name in station_types:
            with self.subTest(station=name):
                image = ET.parse(folder / "public/station-previews" / f"{name}.svg").getroot()
                self.assertEqual(image.tag, "{http://www.w3.org/2000/svg}svg")
                self.assertGreater(float(image.get("width")), 0)
                self.assertGreater(float(image.get("height")), 0)


if __name__ == "__main__":
    unittest.main()
