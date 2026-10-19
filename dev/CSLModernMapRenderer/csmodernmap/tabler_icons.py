"""使用随附字体绘制 Tabler 图标。

图标来自采用 MIT 许可证的 Tabler Icons 字体，替代旧的程序绘制图标。"""
from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageTk

_FONT_PATH = Path(__file__).resolve().parent.parent / "resources" / "fonts" / "tabler-icons.ttf"
_CSS_PATH = _FONT_PATH.with_suffix(".css")

_cache: dict = {}
_codepoints: dict[str, str] | None = None


def _load_codepoints() -> dict[str, str]:
    """解析随附 CSS，提取图标名与 Unicode 码点的对应关系。"""
    global _codepoints
    if _codepoints is not None:
        return _codepoints
    mapping: dict[str, str] = {}
    try:
        text = _CSS_PATH.read_text(encoding="utf-8")
    except Exception:
        _codepoints = mapping
        return mapping
    for m in re.finditer(r'\.ti-([a-z0-9-]+):before\{content:"\\([0-9a-fA-F]+)"', text):
        name, cp = m.group(1), m.group(2)
        mapping[name] = chr(int(cp, 16))
    _codepoints = mapping
    return mapping


def tabler_icon(master, name: str, size: int, color: str = "#2B579A"):
    """按名称绘制 Tabler 图标并返回 PhotoImage；资源或语义名称缺失时返回 None，供调用方显示原文字标记。"""
    # PhotoImage 属于单个 Tcl 解释器；缓存键包含解释器，防止后续窗口拿到已销毁 Tk 根对象的图像。
    key = (master.tk, name, size, color)
    if key in _cache:
        return _cache[key]

    cps = _load_codepoints()
    glyph = cps.get(name)
    if glyph is None or not _FONT_PATH.is_file():
        return None

    # 画布略大于字形，为抗锯齿保留边距。
    canvas = int(size * 1.5)
    img = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype(str(_FONT_PATH), int(size * 1.0))
    except Exception:
        return None
    bbox = draw.textbbox((0, 0), glyph, font=font)
    gw, gh = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if gw <= 0 or gh <= 0:
        return None
    ox = (canvas - gw) / 2 - bbox[0]
    oy = (canvas - gh) / 2 - bbox[1]
    draw.text((ox, oy), glyph, fill=color, font=font)

    # 使用 LANCZOS 缩小图像以平滑边缘。
    if canvas != size:
        img = img.resize((size, size), Image.Resampling.LANCZOS)

    photo = ImageTk.PhotoImage(img, master=master)
    _cache[key] = photo
    return photo


# CSLModernMap 按钮使用的 Tabler 图标名称映射。
# 语义名称到 Tabler 图标名称的映射。
TABLER_NAME_MAP = {
    # 文件操作。
    "open": "folder-open",
    "save": "device-floppy",
    "copy": "copy",
    "export_png": "file-export",
    "export_svg": "file-code",
    "import": "folder-open",
    # 地图和视图。
    "map": "map",
    "preview": "eye",
    "render": "player-play-filled",
    # 图层。
    "terrain": "mountain",
    "water": "droplet",
    "road": "road",
    "railway": "train",
    "traffic": "traffic-lights",
    "building": "building",
    "procedural_objects": "building-bridge-2",
    "metro": "route-2",
    "transit": "bus",
    "poi": "map-pin",
    "add_icon": "map-pin-plus",
    "label": "tag",
    "watermark": "writing-sign",
    "add_label": "tag",
    "district": "map-pin-share",
    "zone": "map-2",
    "land_use": "layout-grid",
    "built_area": "building-community",
    "poi_area": "map-pin",
    # 顶点和换乘。
    "vertices": "git-merge",
    "metro_vertices": "git-merge",
    # 操作。
    "undo": "arrow-back-up",
    "redo": "arrow-forward-up",
    "reset_object": "refresh",
    "delete": "trash",
    "add": "plus",
    "poi_editor": "map-pin-cog",
    "zoom_in": "zoom-in",
    "zoom_out": "zoom-out",
    "larger": "zoom-in",
    "smaller": "zoom-out",
    "move": "arrows-move",
    "select_mode": "pointer",
    "properties": "adjustments",
    "theme_settings": "palette",
    "settings": "settings",
    "help": "help",
    "cache": "database",
    "diagnostics": "stethoscope",
    "wizard": "wand",
    "merge": "arrow-merge",
    "copy2": "clipboard",
    "new_window": "window",
    "compare": "arrows-horizontal",
    "layers": "stack-2",
    "grid": "layout-grid",
    "edit": "edit",
    "refresh": "refresh",
    "interchange": "git-merge",
    "random": "dice",
    "spot": "grid-dots",
    "transit_preview": "bus",
    "preview_transit": "bus",
    "web": "world",
}


def icon_for(master, semantic_name: str, size: int, color: str = "#2B579A"):
    """查找语义图标名称并绘制。"""
    tabler_name = TABLER_NAME_MAP.get(semantic_name, semantic_name)
    return tabler_icon(master, tabler_name, size, color)
