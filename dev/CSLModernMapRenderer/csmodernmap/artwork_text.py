"""将一次完整文字运行固化为局部资源，保留实际字体、基线与缺字诊断。"""
from __future__ import annotations

import hashlib
import math

from PIL import Image, ImageDraw, ImageFont

from .artwork import bitmap, color, image_resource
from fontTools.ttLib import TTFont

from .fonts import resolve_text_font


def text_primitive(object_id: str, text: str, baseline, *, size: float,
                   font_name: str, bold: bool = False, fill: str = "#202830",
                   stroke: str = "white", stroke_width: float = 0,
                   raster_scale: int = 4, angle: float = 0):
    if not text or len(text) > 2048 or "\n" in text:
        raise ValueError("A text run requires 1–2048 characters on one resolved line")
    if not math.isfinite(size) or size*raster_scale < 1 or not 0 <= stroke_width <= size or raster_scale not in (1, 2, 4):
        raise ValueError("Invalid artwork text size")
    path = resolve_text_font(font_name, bold=bold, text=text)
    if path is None:
        raise ValueError(f"No usable font for artwork run {object_id!r}")
    font = ImageFont.truetype(str(path), round(size*raster_scale))
    with TTFont(str(path), fontNumber=font.index, lazy=True) as face:
        coverage = set(face.getBestCmap())
        weight = int(face['OS/2'].usWeightClass)
    if bold and weight < 600:
        raise ValueError(f"Resolved font {path.name!r} does not supply bold; select an explicit bold face")
    outline = round(stroke_width*raster_scale)
    bounds = font.getbbox(text, anchor="ls", stroke_width=outline)
    left, top, right, bottom = bounds
    if right <= left or bottom <= top or (right-left)*(bottom-top) > 16_777_216:
        raise ValueError("Artwork text has an empty or oversized raster box")
    image = Image.new("RGBA", (right-left, bottom-top))
    ImageDraw.Draw(image).text((-left, -top), text, font=font, anchor="ls",
                              fill=color(fill), stroke_fill=color(stroke), stroke_width=outline)
    x, y = baseline
    cx = x+(left+right)/2/raster_scale
    cy = y+(top+bottom)/2/raster_scale
    if angle:
        image = image.rotate(-angle, Image.Resampling.BICUBIC, expand=True)
    resource = image_resource(image)
    bbox = [cx-image.width/2/raster_scale, cy-image.height/2/raster_scale,
            cx+image.width/2/raster_scale, cy+image.height/2/raster_scale]
    run = {"text": text, "fontId": hashlib.sha256(path.read_bytes()).hexdigest(),
           "fontFile": path.name, "bold": weight >= 600, "fontWeight": weight,
           "fontSize": font.size/raster_scale,
           "baseline": list(baseline), "advance": font.getlength(text)/raster_scale,
           "angle": angle, "rasterScale": raster_scale,
           "missing": sorted({f"U+{ord(char):04X}" for char in text if ord(char) not in coverage})}
    return bitmap(object_id, resource, bbox, text_run=run), resource
