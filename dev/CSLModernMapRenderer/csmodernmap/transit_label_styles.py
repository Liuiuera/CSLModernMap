"""公共交通线路标签预设；预览和图片导出共用尺寸、颜色及图标。"""
from __future__ import annotations

import base64
from functools import lru_cache
from io import BytesIO

from PIL import Image, ImageChops, ImageDraw, ImageFont

from ._paths import resource_dir
from .style import hex_to_rgb


DEFAULT_PRESET = "split-sign"
# 按下拉框小样的标签宽度递增排列，同宽款式相邻。
PRESET_IDS = ("solid-sign", "underline-sign", "side-sign", "split-sign", "frame-sign")
PRESETS = {
    "side-sign": dict(padding=7 / 14, border=1 / 14, radius=5 / 14,
                      height=1.6, accent="side", icon=False),
    "split-sign": dict(padding=7 / 14, border=1 / 14, radius=5 / 14,
                       height=1.65, accent="split", icon=True,
                       icon_size=17 / 14, icon_gap=6 / 14, icon_width=22 / 14),
    "solid-sign": dict(padding=7 / 14, border=0, radius=5 / 14,
                       height=1.6, accent="fill", icon=False),
    "frame-sign": dict(padding=7 / 14, border=1.5 / 14, radius=3 / 14,
                       height=1.6, accent="frame", icon=True,
                       icon_size=17 / 14, icon_gap=6 / 14),
    "underline-sign": dict(padding=7 / 14, border=0, radius=5 / 14,
                           height=1.8, accent="bottom", icon=False),
}

# 来自随附 MIT Tabler 字体；轨道类型共用正面列车标识。
_GLYPHS = {
    "bus": "\uebe4", "trolleybus": "\uebe4",
    "metro": "\ued96", "train": "\ued96", "monorail": "\ued96", "tram": "\ued96",
    "cablecar": "\uedfe", "ship": "\uec84", "ferry": "\uec84",
    "airplane": "\ueb6f", "helicopter": "\ued8e",
}


def contrast_text(rgb) -> str:
    def luminance(color):
        channels = [v / 255 for v in color]
        linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in channels]
        return sum(v * w for v, w in zip(linear, (.2126, .7152, .0722)))
    lightness = luminance(rgb)
    dark = hex_to_rgb("#26352F")
    dark_ratio = (max(lightness, luminance(dark)) + .05) / (min(lightness, luminance(dark)) + .05)
    return "#26352F" if dark_ratio >= 1.05 / (lightness + .05) else "#FFFFFF"


def palette(background: str) -> dict:
    dark = contrast_text(hex_to_rgb(background)) == "#FFFFFF"
    return ({"surface": "#29332F", "text": "#EDF5F0", "border": "#526158"} if dark else
            {"surface": "#FFFFFF", "text": "#26352F", "border": "#D4DDD7"})


@lru_cache(maxsize=12)
def icon_mask(kind: str) -> Image.Image:
    font = ImageFont.truetype(str(resource_dir("resources") / "fonts/tabler-icons.ttf"), 64)
    glyph = _GLYPHS[kind.lower()]
    mask = Image.new("L", (64, 64))
    draw = ImageDraw.Draw(mask)
    box = draw.textbbox((0, 0), glyph, font=font)
    draw.text(((64 - box[2] + box[0]) / 2 - box[0],
               (64 - box[3] + box[1]) / 2 - box[1]), glyph, font=font, fill=255)
    return mask


@lru_cache(maxsize=1)
def preview_icons() -> dict:
    result = {}
    for kind in _GLYPHS:
        stream = BytesIO()
        image = Image.new("RGBA", (64, 64), "white")
        image.putalpha(icon_mask(kind))
        image.save(stream, format="PNG")
        result[kind] = "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode("ascii")
    return result


def badge_layout_options(tv):
    from .theme_contract import field_default

    defaults = field_default(("transports", "transit_view"))
    return {key.removeprefix("label_"): value
            for key, value in {"label_preset": DEFAULT_PRESET, "label_candidate_limit": 72,
                               **defaults, **tv}.items() if key.startswith("label_")}


def render_badges(items, kinds, preset: str, size: float, font, background: str) -> Image.Image:
    """在 2 倍画布绘制，尺寸与 Viewer 的 border-box 计算一致。"""
    style = PRESETS[preset]
    colors = palette(background)
    scale = size * 2
    border = style["border"] * scale
    pad = style["padding"] * scale
    height = max(round(style["height"] * scale), 2)
    radius = min(round(style["radius"] * scale), height // 2)
    icon_size = round(scale * style["icon_size"]) if style["icon"] else 0
    icon_gap = scale * style["icon_gap"] if style["icon"] else 0
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    badges = []
    for index, (name, rgb) in enumerate(items):
        box = probe.textbbox((0, 0), name, font=font)
        text_width = box[2] - box[0]
        if style["accent"] == "split":
            text_x = border + scale * style["icon_width"] + icon_gap
        else:
            text_x = border + pad + icon_size + icon_gap
        width = max(round(text_x + text_width + pad + border), 2)
        badge = Image.new("RGBA", (width, height))
        draw = ImageDraw.Draw(badge)
        accent = style["accent"]
        fill = tuple(rgb) if accent == "fill" else colors["surface"]
        outline = tuple(rgb) if accent == "frame" else colors["border"]
        draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=fill,
                               outline=outline if border else None,
                               width=max(round(border), 1))
        # 色带先在独立层绘制，再按标签圆角裁剪。
        if accent in {"side", "split", "bottom"}:
            band = Image.new("RGBA", badge.size)
            bd = ImageDraw.Draw(band)
            if accent == "bottom":
                bd.rectangle((0, height - max(round(scale * 3 / 14), 1), width, height), fill=tuple(rgb))
            else:
                band_width = scale * (style["icon_width"] if accent == "split" else 4 / 14)
                bd.rectangle((round(border), round(border), round(border + band_width),
                              height - 1 - round(border)), fill=tuple(rgb))
            clip = Image.new("L", badge.size)
            ImageDraw.Draw(clip).rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=255)
            band.putalpha(ImageChops.multiply(band.getchannel("A"), clip))
            badge.alpha_composite(band)
        text_color = contrast_text(rgb) if accent == "fill" else colors["text"]
        if style["icon"] and kinds is not None:
            ink = contrast_text(rgb) if accent == "split" else colors["text"]
            icon = Image.new("RGBA", (icon_size, icon_size), ink)
            icon.putalpha(icon_mask(kinds[index].lower()).resize(icon.size, Image.Resampling.LANCZOS))
            icon_x = border + (scale * style["icon_width"] - icon_size) / 2 if accent == "split" else border + pad
            badge.alpha_composite(icon, (round(icon_x), round((height - icon_size) / 2)))
        text_y = (height - (scale * 3 / 14 if accent == "bottom" else 0) - box[3] + box[1]) / 2 - box[1]
        ImageDraw.Draw(badge).text((text_x - box[0], text_y), name, font=font, fill=text_color)
        badges.append(badge)
    seam = max(round(scale * .2), 1)
    result = Image.new("RGBA", (sum(b.width for b in badges) + seam * (len(badges) - 1), height))
    x = 0
    for badge in badges:
        result.alpha_composite(badge, (x, 0))
        x += badge.width + seam
    return result.resize((max(round(result.width / 2), 1), max(round(height / 2), 1)), Image.Resampling.LANCZOS)


@lru_cache(maxsize=len(PRESET_IDS))
def preset_thumbnail(preset: str) -> Image.Image:
    """下拉框共用的 80×32 逻辑像素小样，以 2 倍分辨率保留细线。"""
    badge = render_badges([("12", (20, 137, 128))], ["Bus"], preset, 28,
                          ImageFont.load_default(size=56), "#F6F7F9")
    image = Image.new("RGBA", (160, 64))
    image.alpha_composite(badge, (4, (image.height - badge.height) // 2))
    return image
