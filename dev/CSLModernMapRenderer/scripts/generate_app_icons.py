#!/usr/bin/env python3
"""Generate macOS PNG and Windows multi-size ICO from the app icon geometry."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageColor, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
RESOURCES = ROOT / "resources"
MAC_PNG = RESOURCES / "app-icon-macos-1024.png"
WINDOWS_PNG = RESOURCES / "app-icon-windows-1024.png"
WINDOWS_ICO = RESOURCES / "app.ico"
SCALE = 4
SIZE = 1024
BACKGROUND = "#69B7E6"
SHADOW = "#2F7FAF"
WHITE = "#FFFFFF"

POLYGONS = (
    ((430, 145), (495, 145), (475, 390), (402, 390)),
    ((529, 145), (594, 145), (622, 390), (549, 390)),
    ((396, 468), (472, 468), (440, 865), (275, 865)),
    ((552, 468), (628, 468), (749, 865), (584, 865)),
)
RECTANGLES = (
    (150, 390, 874, 468),
    (235, 468, 283, 513),
    (488, 468, 536, 513),
    (741, 468, 789, 513),
)


def _scaled_points(points, offset=(0, 0)):
    ox, oy = offset
    return [((x + ox) * SCALE, (y + oy) * SCALE) for x, y in points]


def _scaled_rect(rect, offset=(0, 0)):
    ox, oy = offset
    x0, y0, x1, y1 = rect
    return tuple(v * SCALE for v in (x0 + ox, y0 + oy, x1 + ox, y1 + oy))


def _draw_symbol(draw: ImageDraw.ImageDraw) -> None:
    shadow = (*ImageColor.getrgb(SHADOW), 72)
    for polygon in POLYGONS:
        draw.polygon(_scaled_points(polygon, (12, 18)), fill=shadow)
    for rectangle in RECTANGLES:
        draw.rectangle(_scaled_rect(rectangle, (12, 18)), fill=shadow)
    for polygon in POLYGONS:
        draw.polygon(_scaled_points(polygon), fill=WHITE)
    for rectangle in RECTANGLES:
        draw.rectangle(_scaled_rect(rectangle), fill=WHITE)


def _render(*, windows_mask: bool) -> Image.Image:
    canvas = Image.new("RGBA", (SIZE * SCALE, SIZE * SCALE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas, "RGBA")
    if windows_mask:
        margin = 20 * SCALE
        draw.rounded_rectangle(
            (margin, margin, SIZE * SCALE - margin, SIZE * SCALE - margin),
            radius=180 * SCALE,
            fill=BACKGROUND,
        )
    else:
        draw.rectangle((0, 0, SIZE * SCALE, SIZE * SCALE), fill=BACKGROUND)
    _draw_symbol(draw)
    return canvas.resize((SIZE, SIZE), Image.Resampling.LANCZOS)


def main() -> None:
    mac = _render(windows_mask=False)
    windows = _render(windows_mask=True)
    mac.save(MAC_PNG, optimize=True)
    windows.save(WINDOWS_PNG, optimize=True)
    windows.save(
        WINDOWS_ICO,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
               (64, 64), (128, 128), (256, 256)],
    )
    print(MAC_PNG)
    print(WINDOWS_PNG)
    print(WINDOWS_ICO)


if __name__ == "__main__":
    main()
