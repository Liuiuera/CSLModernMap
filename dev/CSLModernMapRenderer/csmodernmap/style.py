"""样式系统：加载 YAML 主题配置。

样式文件结构参见 styles/*.yaml。
本模块负责：
- 加载/合并主题 YAML（支持嵌套默认值）
- 提供样式字段访问接口
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict
from .i18n import tr


THEME_ALIASES = {"apple_maps": "modern", "amap": "bright", "google": "warm_natural", "paper": "vintage"}


def canonical_theme_id(value: str) -> str:
    return THEME_ALIASES.get(value, value)


def hex_to_rgb(color: str):
    """解析 #RRGGBB / #RGB 为 (r,g,b)。"""
    c = color.strip().lstrip('#')
    if len(c) == 3:
        c = ''.join((ch * 2 for ch in c))
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))


def hex_to_rgba(color: str):
    """解析 #RGB / #RRGGBB / #RRGGBBAA，未指定透明度时为不透明。"""
    c = color.strip().lstrip('#')
    if len(c) not in (3, 6, 8):
        raise ValueError(f"Invalid hex color: {color!r}")
    return hex_to_rgb(color) + (int(c[6:8], 16) if len(c) == 8 else 255,)


def style_scalar(value, default):
    return float(default if value is None else value)


# 地铁图专用配置文件名（非地图主题：不参与主题枚举/选择器）。
# 地铁示意图纯图入口独立加载此文件，不读四套主题的 transports 区块。
METRO_STYLE_FILE = "metro_map.yaml"


class Style:
    """样式配置对象。"""

    def __init__(self, data: Dict[str, Any]):
        from .pixel_sizes import is_pixel_theme, validate
        if is_pixel_theme(data):
            validate(data)
        if isinstance(data.get("poi_icons"), dict):
            # 旧版主题不能再选择已停用的私有图稿。
            poi = {**data["poi_icons"], "icons_dir": "cn_icons"}
            poi.pop("sprite", None)
            data = {**data, "poi_icons": poi}
        self.data = data
        terrain = data.get("terrain")
        if isinstance(terrain, dict) and "ground" in terrain and "elevation_colors" not in terrain:
            from .theme_contract import migrate_terrain_colors
            self.data = {**data, "terrain": migrate_terrain_colors(terrain)}
        if isinstance(terrain, dict):
            from .terrain import resolve_terrain_config
            self.data = {**self.data, "terrain": resolve_terrain_config(self.data["terrain"])}

    def get(self, key: str, default: Any = None) -> Any:
        """按点分路径取配置，如 style.get('terrain.elevation_colors')。"""
        cur: Any = self.data
        for part in key.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur

    def merged(self, other: "Style | Dict[str, Any]") -> "Style":
        """深度合并另一个样式（other 覆盖 self）。"""
        merged = copy.deepcopy(self.data)
        _deep_merge(merged, other.data if isinstance(other, Style) else other)
        return Style(merged)


def strip_retired_region_palette_fields(data: dict) -> dict:
    """原地移除旧版区划和分区的分类配色配置。"""
    if not isinstance(data, dict):
        return data

    district = data.get("district")
    if isinstance(district, dict):
        district.pop("specialization", None)
        district.pop("specialization_colors", None)

    zone = data.get("zone")
    if isinstance(zone, dict):
        old_colors = zone.pop("colors", None)
        if "fill" not in zone and isinstance(old_colors, dict):
            default_fill = old_colors.get("default")
            if default_fill:
                zone["fill"] = default_fill
    return data





def load_style(path: str) -> Style:
    """从 YAML 文件加载样式；只接受新版地图主题。"""
    try:
        import yaml
    except ImportError:
        raise ImportError(tr("gui.error.pyyaml_missing"))

    source = Path(path)
    if not source.is_file() and source.stem in THEME_ALIASES:
        source = source.with_name(canonical_theme_id(source.stem) + source.suffix)
    with source.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    strip_retired_region_palette_fields(data)
    if Path(path).name != METRO_STYLE_FILE:
        from .theme_contract import cleanup_theme
        data = cleanup_theme(data)
        from .pixel_sizes import validate
        validate(data)
    return Style(data)


def load_metro_style(styles_dir: str) -> Style:
    """加载独立的 RMP 生成参数；不作为普通地图主题枚举。"""
    return load_style(str(Path(styles_dir) / METRO_STYLE_FILE))


def load_styles_dir(styles_dir: str) -> Dict[str, Style]:
    """加载目录下所有 *.yaml 主题，返回 {主题名: Style}。

    地铁图专用配置（metro_map.yaml）不是地图主题，不在此返回。
    """
    styles: Dict[str, Style] = {}
    for path in sorted(Path(styles_dir).glob("*.yaml")):
        if path.name == METRO_STYLE_FILE:
            continue
        styles[path.stem] = load_style(str(path))
    return styles


def _deep_merge(base: Dict, override: Dict) -> None:
    for key, value in override.items():
        if (
            key in base
            and isinstance(base[key], dict)
            and isinstance(value, dict)
        ):
            _deep_merge(base[key], value)
        else:
            base[key] = value
