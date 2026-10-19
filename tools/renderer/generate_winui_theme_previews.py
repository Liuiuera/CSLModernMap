"""Generate WinUI theme gallery images from the shared theme palette."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str((Path(__file__).resolve().parents[2] / "dev/CSLModernMapRenderer")))

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication  # noqa: E402

render_dir = (Path(__file__).resolve().parents[2] / "dev/CSLModernMapRenderer")
sys.path.insert(0, str(render_dir))

from csmodernmap.guir_backend import THEME_CHOICES  # noqa: E402


from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap

# Gallery images use a 2:1 aspect ratio.
THUMB_WIDTH = 76
THUMB_HEIGHT = 38
# 超采样倍数：先在 4 倍画布上画再缩小，斜向路网才不会出现锯齿。
_SUPERSAMPLE = 4

from csmodernmap.theme_palette import theme_palette

def theme_thumb(stem: str) -> QPixmap:
    """主题画廊用的 76×38 示意小地图。"""
    colors = theme_palette(stem)
    canvas = QPixmap(THUMB_WIDTH * _SUPERSAMPLE, THUMB_HEIGHT * _SUPERSAMPLE)
    canvas.fill(QColor(colors["ground"]))
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(_SUPERSAMPLE, _SUPERSAMPLE)
    _paint_land(painter, colors)
    _paint_river(painter, colors)
    _paint_buildings(painter, colors)
    _paint_roads(painter, colors)
    painter.end()
    return canvas.scaled(
        THUMB_WIDTH, THUMB_HEIGHT,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation)


def _stroke(painter: QPainter, color: str, width: float,
            path: QPainterPath) -> None:
    pen = QPen(QColor(color))
    pen.setWidthF(width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)


def _paint_land(painter: QPainter, colors: dict) -> None:
    """两片绿地：左上大片（山林）+ 右上小片。"""
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(colors["green"]))

    west = QPainterPath(QPointF(-2, -2))
    west.lineTo(30, -2)
    west.cubicTo(36, 6, 28, 10, 20, 12)
    west.cubicTo(12, 14, 4, 16, -2, 17)
    west.closeSubpath()
    painter.drawPath(west)

    east = QPainterPath(QPointF(56, -2))
    east.lineTo(78, -2)
    east.lineTo(78, 6)
    east.cubicTo(70, 8, 62, 7, 56, 4)
    east.closeSubpath()
    painter.drawPath(east)


def _paint_river(painter: QPainter, colors: dict) -> None:
    """一条自左下蜿蜒到右中的河，先描边色打底再填水色。"""
    river = QPainterPath(QPointF(-2, 33))
    river.cubicTo(QPointF(16, 28), QPointF(24, 38), QPointF(42, 33))
    river.cubicTo(QPointF(58, 28.5), QPointF(62, 22), QPointF(78, 23))
    _stroke(painter, colors["water_edge"], 7.0, river)
    _stroke(painter, colors["water"], 4.4, river)


# (x, y, w, h)：城区街区，只出现在河北岸的空地上。
# 刻意画大：76×38 里小于 5px 的块会直接糊成噪点，宁可少画几块也要看得见。
_BUILDINGS = (
    (40.0, 5.0, 7.0, 5.0),
    (49.5, 4.0, 6.0, 4.5),
    (43.0, 12.5, 6.0, 4.5),
    (52.0, 11.0, 6.0, 4.5),
)


def _paint_buildings(painter: QPainter, colors: dict) -> None:
    pen = QPen(QColor(colors["building_edge"]))
    pen.setWidthF(0.7)
    painter.setPen(pen)
    painter.setBrush(QColor(colors["building"]))
    for x, y, w, h in _BUILDINGS:
        painter.drawRect(QRectF(x, y, w, h))


def _paint_roads(painter: QPainter, colors: dict) -> None:
    """主干道斜穿全图，次干道竖向接入，两条都按「描边 + 填充」双层画。"""
    main = QPainterPath(QPointF(-2, 22))
    main.cubicTo(QPointF(18, 18), QPointF(44, 10), QPointF(78, 5))
    _stroke(painter, colors["road_edge"], 4.2, main)
    _stroke(painter, colors["road"], 2.6, main)

    branch = QPainterPath(QPointF(34, -2))
    branch.lineTo(46, 40)
    _stroke(painter, colors["road2_edge"], 2.4, branch)
    _stroke(painter, colors["road2"], 1.3, branch)


def main() -> None:
    app = QGuiApplication.instance() or QGuiApplication([])
    output = render_dir.parent / "CSLModernMapRenderer" / "winui" / "Assets" / "Themes"
    output.mkdir(parents=True, exist_ok=True)
    for _, stem in THEME_CHOICES:
        if not theme_thumb(stem).save(str(output / f"{stem}.png"), "PNG"):
            raise RuntimeError(f"Could not save theme preview: {stem}")
    app.quit()


if __name__ == "__main__":
    main()
