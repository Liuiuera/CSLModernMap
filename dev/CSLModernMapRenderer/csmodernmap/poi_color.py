"""统一提取图标、建筑和区域着色使用的 POI 颜色。"""
from __future__ import annotations

import os
import sys

from PIL import Image

from ._paths import cn_icons_dir, icons_library_dir
from .services.poi.direct_mapping import load_mapping, entry_key


_RESOLVER_KEY = "poi_color_resolver"


def mix_white(rgb, amount: float):
    """将 RGB 颜色按指定比例与白色混合。"""
    amount = min(max(float(amount), 0.0), 1.0)
    return tuple(int(round(c * (1.0 - amount) + 255 * amount)) for c in rgb)


def poi_contrast(buildings_cfg: dict) -> float:
    """返回 POI 共用的色调对比度，零为白色，一为图标原色。"""
    tint = (buildings_cfg or {}).get("poi_tint", {}) or {}
    try:
        value = float(tint.get("contrast", 0.30))
    except (TypeError, ValueError):
        value = 0.30
    return min(max(value, 0.0), 1.0)


def poi_tint_mix_white(buildings_cfg: dict) -> float:
    """把对比度转换为渲染器的白色混合系数。"""
    return 1.0 - poi_contrast(buildings_cfg)


def dominant_icon_rgb(image: Image.Image) -> tuple[int, int, int]:
    """从 POI 图标提取可见且饱和的前景色。"""
    rgba = image.convert("RGBA")
    if max(rgba.size) > 24:
        scale = 24.0 / max(rgba.size)
        rgba = rgba.resize(
            (max(1, int(round(rgba.width * scale))),
             max(1, int(round(rgba.height * scale)))),
            Image.Resampling.LANCZOS,
        )
    weighted = []
    fallback = []
    pixels = (rgba.get_flattened_data()
              if hasattr(rgba, "get_flattened_data") else rgba.getdata())
    for r, g, b, a in pixels:
        if a < 40:
            continue
        fallback.append((r, g, b, a))
        if r > 225 and g > 225 and b > 225:
            continue
        hi, lo = max(r, g, b), min(r, g, b)
        saturation = hi - lo
        # 饱和像素确定徽标颜色，透明度防止抗锯齿边缘偏向透明背景。
        weight = (a / 255.0) * (0.35 + saturation / 255.0)
        weighted.append((r, g, b, weight))
    if not weighted:
        weighted = [(r, g, b, max(a / 255.0, 0.1))
                    for r, g, b, a in fallback]
    if not weighted:
        return (80, 85, 100)
    total = sum(p[3] for p in weighted) or 1.0
    return tuple(int(round(sum(p[i] * p[3] for p in weighted) / total))
                 for i in range(3))


class POIColorResolver:
    """解析映射的图标标识及提取出的前景色。"""

    def __init__(self, cfg: dict):
        icon_utils_dir = os.path.normpath(cn_icons_dir())
        if icon_utils_dir not in sys.path:
            sys.path.insert(0, icon_utils_dir)
        from poi_icon_utils import POIIconMap

        default_dir = os.path.normpath(str(icons_library_dir("cn_icons")))
        icons_dir = default_dir

        self.icons_dir = icons_dir
        self.icon_map = POIIconMap(os.path.join(icons_dir, "icons_map.json"),
                                   appearance=cfg.get("appearance", "light"))
        self.mapping = load_mapping(cfg.get("_poi_mapping_override"))
        self.sprite = self.icon_map.load_sprite(cfg.get("sprite"))
        self._colors: dict[str, tuple[int, int, int]] = {}

    def icon_rgb(self, icon_id: str) -> tuple[int, int, int]:
        cached = self._colors.get(icon_id)
        if cached is not None:
            return cached
        raw = self.icon_map.crop_icon(icon_id, sprite_image=self.sprite)
        rgb = dominant_icon_rgb(raw) if raw is not None else (80, 85, 100)
        self._colors[icon_id] = rgb
        return rgb

    def building_color(self, building, game=""):
        key = entry_key(game, getattr(building, "category_id", ""))
        entry = self.mapping.get(key)
        if entry is None:
            return None
        filename = entry["icons"].get(self.icon_map.library, "")
        return (key, self.icon_rgb(filename)) if filename else None


def get_poi_color_resolver(ctx):
    """返回渲染共用解析器，最多构造一次。"""
    resolver = ctx.shared.get(_RESOLVER_KEY)
    if resolver is not None:
        return resolver
    resolver = POIColorResolver({**(ctx.style.get("poi_icons", {}) or {}),
        "_poi_mapping_override": ctx.style.get("_poi_mapping_override")})
    ctx.shared[_RESOLVER_KEY] = resolver
    return resolver
