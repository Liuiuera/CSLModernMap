"""Small integration checks for public file and export entry points."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "dev/CSLModernMapRenderer"
FIXTURES = Path(__file__).parent / "fixtures"
sys.path.insert(0, str(ENGINE))

from csmodernmap.cmm.loader import load_document


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.source = self.work / "中文 城市.cmm"
        self.source.write_bytes((FIXTURES / "tiny-city.cmm").read_bytes())
        self.source_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONUTF8="1",
                        CSLMM_USER_DATA_DIR=str(self.work / "settings"),
                        XDG_CONFIG_HOME=str(self.work / "config"))

    def cli(self, *args, expected=0):
        result = subprocess.run([sys.executable, str(ENGINE / "main.py"), *map(str, args)],
                                cwd=self.work, env=self.env, capture_output=True,
                                encoding="utf-8", timeout=90)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.source_hash)
        return result

    def test_cmm_plain_and_gzip(self):
        compressed = self.work / "中文 城市.cmm.gz"
        with gzip.open(compressed, "wb") as stream:
            stream.write(self.source.read_bytes())
        for path in (self.source, compressed):
            with self.subTest(path=path.name):
                document = load_document(path)
                self.assertEqual(document.metadata.origin.source_name, "测试城 Test City")
                self.assertEqual(len(document.networks), 2)
                self.assertEqual(len(document.buildings), 1)
                self.assertEqual(document.transit_lines[0].user_color, (220, 40, 30, 255))
                self.assertFalse([i for i in document.issues if i.severity == "error"])

    def test_cs1_origin_uses_same_cmm_reader(self):
        raw = json.loads(self.source.read_text(encoding="utf-8"))
        raw["metadata"]["origin"]["game"] = "cs1"
        path = self.work / "cs1.cmm"
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(load_document(path).metadata.origin.game, "cs1")

    def test_legacy_plain_and_gzip(self):
        source = FIXTURES / "tiny-legacy.mmap"
        compressed = self.work / "legacy.mmap.gz"
        with gzip.open(compressed, "wb") as stream:
            stream.write(source.read_bytes())
        for path in (source, compressed):
            with self.subTest(path=path.name):
                document = load_document(path)
                self.assertEqual(document.metadata.origin.source_name, "旧格式测试城")
                self.assertEqual(len(document.nodes), 2)
                self.assertEqual(document.networks[0].display_name, "测试路")

    def test_missing_road_level_reports_error(self):
        raw = json.loads(self.source.read_text(encoding="utf-8"))
        del raw["networks"][0]["level"]
        path = self.work / "missing-level.cmm"
        path.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "no valid road level"):
            load_document(path)

    def test_dangling_node_is_diagnosed(self):
        raw = json.loads(self.source.read_text(encoding="utf-8"))
        raw["networks"][0]["start_id"] = 999
        path = self.work / "dangling.cmm"
        path.write_text(json.dumps(raw), encoding="utf-8")
        self.assertIn("DANGLING_REF", {i.code for i in load_document(path).issues})

    def test_cli_parse_and_missing_file(self):
        self.assertIn("测试城 Test City", self.cli("parse", self.source).stdout)
        self.cli("parse", self.work / "missing.cmm", expected=1)

    def test_png_is_decodable_and_contains_map_content(self):
        output = self.work / "地图.png"
        self.cli("render", self.source, "--size", "256", "-o", output)
        with Image.open(output) as image:
            image.load()
            self.assertEqual(image.format, "PNG")
            self.assertEqual(max(image.size), 256)
            self.assertGreater(len(image.convert("RGB").getcolors(256 * 256) or []), 8)

    def test_transit_png_exports(self):
        output = self.work / "公交.png"
        self.cli("transit", self.source, "--size", "256", "-o", output)
        with Image.open(output) as image:
            image.load()
            self.assertEqual(image.format, "PNG")
            self.assertEqual(max(image.size), 256)

    def test_html_export_embeds_viewer(self):
        output = self.work / "地图.html"
        self.cli("export-web", self.source, "-o", output)
        html = output.read_text(encoding="utf-8")
        self.assertIn("<html", html.lower())
        self.assertIn("maplibre", html.lower())
        self.assertGreater(output.stat().st_size, 100_000)
        self.assertNotRegex(html, r'<script\b[^>]*\bsrc=["\']https?://')
        self.assertNotRegex(html, r'<link\b[^>]*\bhref=["\']https?://')

    def test_engine_protocol_handshake_error_and_close(self):
        requests = [
            {"id":1,"method":"hello","params":{"protocolVersion":1}},
            {"id":2,"method":"getSnapshot","params":{}},
            {"id":3,"method":"hello","params":{"protocolVersion":999}},
            {"id":4,"method":"close","params":{}},
        ]
        result = subprocess.run([sys.executable, str(ENGINE / "main.py"), "engine"],
                                input="".join(json.dumps(r) + "\n" for r in requests),
                                cwd=self.work, env=self.env, capture_output=True,
                                encoding="utf-8", timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        frames = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        replies = {f["id"]: f for f in frames if "id" in f}
        self.assertEqual(replies[1]["result"]["protocolVersion"], 1)
        self.assertRegex(replies[1]["result"]["viewerUrl"], r"^http://127\.0\.0\.1:")
        self.assertFalse(replies[2]["result"]["hasDocument"])
        self.assertEqual(replies[3]["error"]["code"], "ValueError")
        self.assertTrue(replies[4]["result"]["closed"])


if __name__ == "__main__":
    unittest.main()
