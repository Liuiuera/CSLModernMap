from pathlib import Path
import importlib.util
import json
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_icons", ROOT / "tools/renderer/check_winui_icons.py")
icons = importlib.util.module_from_spec(spec)
spec.loader.exec_module(icons)


class RibbonIconChecks(unittest.TestCase):
    def test_current_references_and_font(self):
        self.assertGreater(icons.validate_icons(), 0)

    def test_missing_rotation_registration_is_rejected(self):
        definitions = json.loads((icons.WINUI / "Assets/ribbon-icons.json").read_text("utf-8"))
        for icon_id in ("rmp.rotate_counterclockwise", "rmp.rotate_clockwise"):
            with self.subTest(icon_id=icon_id), tempfile.TemporaryDirectory() as temporary:
                altered = definitions.copy()
                del altered[icon_id]
                path = Path(temporary) / "ribbon-icons.json"
                path.write_text(json.dumps(altered), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, icon_id):
                    icons.validate_icons(definitions_path=path)

    def test_missing_font_glyph_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "font.css"
            path.write_text("", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Invalid bundled ribbon icon"):
                icons.validate_icons(font_css=path)


if __name__ == "__main__":
    unittest.main()
