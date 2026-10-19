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


def hex_to_rgb(color: str):
    """解析 #RRGGBB / #RGB 为 (r,g,b)。"""
    c = color.strip().lstrip('#')
    if len(c) == 3:
        c = ''.join((ch * 2 for ch in c))
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))


def style_scalar(value, default):
    if isinstance(value, dict):
        value = value.get("scaled", value.get("fixed", default))
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


# 地铁图专用配置文件名（非地图主题：不参与主题枚举/选择器）。
# 地铁示意图纯图入口独立加载此文件，不读四套主题的 transports 区块。
METRO_STYLE_FILE = "metro_map.yaml"


class Style:
    """样式配置对象。"""

    def __init__(self, data: Dict[str, Any]):
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

    def get(self, key: str, default: Any = None) -> Any:
        """按点分路径取配置，如 style.get('terrain.elevation_colors')。"""
        cur: Any = self.data
        for part in key.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur

    def merged(self, other: "Style") -> "Style":
        """深度合并另一个样式（other 覆盖 self）。"""
        merged = copy.deepcopy(self.data)
        _deep_merge(merged, other.data)
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


def scale_style_sizes(data, factor: float):
    """深度遍历样式数据，把 size / font_size 数值字段乘以 factor。

    用于「全局字号倍率」（W2）：只缩放字号相关字段，不改动线宽/间距等。
    - 标量：size: 20 → 20 * factor
    - dict 形式（fixed+scaled 分量同时缩放）：size: {fixed: 8, scaled: 1.5}
    factor == 1.0 时原样返回（零拷贝），保持默认路径无开销。
    """
    if factor == 1.0:
        return data
    if isinstance(data, dict):
        out = {}
        for key, val in data.items():
            if key in ("size", "font_size"):
                if isinstance(val, dict):
                    out[key] = {
                        k: (v * factor if isinstance(v, (int, float)) else v)
                        for k, v in val.items()
                    }
                elif isinstance(val, (int, float)):
                    out[key] = val * factor
                else:
                    out[key] = val
            else:
                out[key] = scale_style_sizes(val, factor)
        return out
    if isinstance(data, list):
        return [scale_style_sizes(v, factor) for v in data]
    return data


def load_style(path: str, font_scale: float = 1.0) -> Style:
    """从 YAML 文件加载样式；font_scale 为全局字号倍率（W2，默认 1.0）。"""
    try:
        import yaml
    except ImportError:
        raise ImportError(tr("gui.error.pyyaml_missing"))

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    strip_retired_region_palette_fields(data)
    if Path(path).name != METRO_STYLE_FILE:
        from .theme_contract import cleanup_theme
        data = cleanup_theme(data)
    if font_scale != 1.0:
        data = scale_style_sizes(data, font_scale)
    return Style(data)


def load_metro_style(styles_dir: str, font_scale: float = 1.0) -> Style:
    """加载地铁图专用配置（styles/metro_map.yaml）。

    与地图主题完全独立：地铁示意图纯图模式的线宽/站点/站名/徽章/
    图例/色板/布局参数全部来自此文件。文件缺失时抛 FileNotFoundError。
    """
    return load_style(str(Path(styles_dir) / METRO_STYLE_FILE),
                      font_scale=font_scale)


def load_styles_dir(styles_dir: str, font_scale: float = 1.0) -> Dict[str, Style]:
    """加载目录下所有 *.yaml 主题，返回 {主题名: Style}。

    font_scale：全局字号倍率（W2，透传给 load_style）。
    地铁图专用配置（metro_map.yaml）不是地图主题，不在此返回。
    """
    styles: Dict[str, Style] = {}
    for path in sorted(Path(styles_dir).glob("*.yaml")):
        if path.name == METRO_STYLE_FILE:
            continue
        styles[path.stem] = load_style(str(path), font_scale=font_scale)
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
