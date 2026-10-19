from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str((Path(__file__).resolve().parents[2] / "dev/CSLModernMapRenderer")))

import json
import re
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
RENDER_ROOT = (Path(__file__).resolve().parents[2] / "dev/CSLModernMapRenderer")
sys.path.insert(0, str(RENDER_ROOT))

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QImage, QPainter

from csmodernmap.ui_palette import DARK, LIGHT


def generate() -> int:
    application = QGuiApplication.instance() or QGuiApplication([])
    fonts = RENDER_ROOT / "resources/fonts"
    font_id = QFontDatabase.addApplicationFont(str(fonts / "tabler-icons.ttf"))
    families = QFontDatabase.applicationFontFamilies(font_id)
    if not families:
        raise ValueError("Unable to load bundled Tabler font")
    codepoints = {name: int(value, 16) for name, value in re.findall(
        r'.ti-([\w-]+):before\s*\{[^}]*content:\s*[\"\']\\([0-9a-fA-F]+)',
        (fonts / "tabler-icons.css").read_text(encoding="utf-8"))}
    assets = RENDER_ROOT.parent / "CSLModernMapRenderer" / "winui" / "Assets"
    definitions = json.loads((assets / "ribbon-icons.json").read_text(encoding="utf-8"))
    count = 0
    variants = [(name, domain, theme, getattr(tokens, domain), getattr(tokens, domain + "_TILE"))
                for name, domain in sorted({tuple(value) for value in definitions.values()})
                for theme, tokens in (("light", LIGHT), ("dark", DARK))]
    variants += [(name, "", "mono", "#FFFFFF", None)
                 for name in sorted({value[0] for value in definitions.values()})]
    for name, domain, theme, color, tile in variants:
        if name not in codepoints:
            raise ValueError(f"Missing Tabler glyph: {name}")
        image = QImage(128, 128, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        if tile:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(tile))
            painter.drawRoundedRect(0, 0, 128, 128, 28, 28)
        painter.setPen(QColor(color))
        painter.setFont(QFont(families[0], int(128 * 0.62)))
        painter.drawText(QRect(0, 0, 128, 128), Qt.AlignmentFlag.AlignCenter,
                         chr(codepoints[name]))
        painter.end()
        folder = assets / "Ribbon" / theme
        folder.mkdir(parents=True, exist_ok=True)
        filename = f"{domain}-{name}.png" if domain else f"{name}.png"
        if not image.save(str(folder / filename)):
            raise OSError(f"Unable to save {filename}")
        count += 1
    print(f"Generated {count} Ribbon icons at 128 px")
    application.processEvents()
    return count


if __name__ == "__main__":
    generate()
