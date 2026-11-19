"""Theme editing validates pixel-v2. Historical data stays untouched until Update Theme."""
from __future__ import annotations
import copy
import math
import json
from functools import lru_cache

@lru_cache(maxsize=1)
def _defaults():
    from ._paths import resource_dir
    return json.loads((resource_dir('resources')/'theme-pixel-defaults.json').read_text(encoding='utf-8'))

def field_default(path):
    value = _defaults()
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return copy.deepcopy(value)

def cleanup_theme(data):
    # The library can retain an old source for explicit update, without interpreting its sizes.
    from .pixel_sizes import is_pixel_theme, validate
    if is_pixel_theme(data):
        validate(data)
    return copy.deepcopy(data)

def normalize_theme(data):
    from .pixel_sizes import validate
    validate(data)
    return copy.deepcopy(data)

_POI_ICON_PROCESSING_KEYS = {
    "show_decorative_pois",
    "enabled", "merge_enabled", "merge_distance", "facility_hierarchy",
    "max_per_cell", "min_priority", "categories",
    "hidden_categories", "show_name", "name_filters", "visual_merge_enabled",
    "visual_merge_distance", "active_profile", "visibility_snapshot",
    "target", "use_game_names",
}

def sanitize_theme_data(raw: object) -> dict:
    """只保留主题外观设置，移除临时状态。"""
    if not isinstance(raw, dict):
        raise ValueError("theme data must be an object")
    from .ribbon_schema import FIELDS
    data = cleanup_theme(raw)
    buildings = data.setdefault("buildings", {})
    if isinstance(buildings, dict):
        for key in ("residential", "commercial", "industrial", "office",
                    "publictransport", "beautification", "public"):
            buildings.pop(key, None)
        buildings.setdefault("color_mode", "poi")
        buildings.setdefault("default", FIELDS["building.default"].default)
        tint = buildings.setdefault("poi_tint", {})
        if isinstance(tint, dict):
            for key in ("enabled", "mix_white", "categories"):
                tint.pop(key, None)
            tint.setdefault("contrast", 0.30)
    data.pop("poi_area", None)
    icons = data.setdefault("poi_icons", {})
    if isinstance(icons, dict):
        for key in _POI_ICON_PROCESSING_KEYS:
            icons.pop(key, None)
        icons.setdefault("font", "SimHei")
        for owner in ('building', 'transit'):
            if isinstance(icons.get(owner), dict):
                for key in _POI_ICON_PROCESSING_KEYS:
                    icons[owner].pop(key, None)
    return data

def migrate_terrain_colors(terrain: dict) -> dict:
    """将旧版仅含地表设置的主题复制到统一高程配置；显式高程渐变优先，旧数据保持原样。"""
    out = copy.deepcopy(terrain)
    ground = terrain.get("ground")
    if "elevation_colors" not in terrain and isinstance(ground, dict) and ground.get("enabled"):
        from .ground_model import effective_elevation_colors, parse_ground_cfg
        parsed = parse_ground_cfg(ground)
        if parsed is not None:
            by_height = {node["threshold"]: node for node in effective_elevation_colors(parsed)}
            out["elevation_colors"] = sorted(by_height.values(), key=lambda node: node["threshold"])
            out.setdefault("elevation_mode", "continuous")
    return out

def validate_theme_values(data: dict) -> None:
    """在编辑边界拒绝格式错误的数值、尺寸或渐变输入。"""
    from .theme_metadata import field_kind
    from .building_categories import validate_category_colors
    from .satellite_filter import image_key
    from .pixel_sizes import is_pixel_theme, validate
    validate(data)
    image_key(data)
    buildings = data.get('buildings', {})
    if isinstance(buildings, dict):
        validate_category_colors(buildings)
    def walk(value, path):
        if path[:2] == ("terrain", "ground"):
            return
        dotted = ".".join(path)
        if path == ("roads", "opacity"):
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not 0 <= value <= 1):
                raise ValueError(f"{dotted}: expected an opacity between 0 and 1")
        if isinstance(value, dict):
            for key, child in value.items():
                walk(child, path + (str(key),))
        elif isinstance(value, list):
            if path[:1] == ("terrain",) and path[-1:] in (("elevation_colors",), ("mountain_colors",)):
                for index, stop in enumerate(value):
                    prefix = f"{dotted}.{index}"
                    if not isinstance(stop, dict) or not {"threshold", "color"} <= stop.keys():
                        raise ValueError(f"{prefix}: expected height and color")
                    height = stop["threshold"]
                    if isinstance(height, bool) or not isinstance(height, (int, float)) or not math.isfinite(height):
                        raise ValueError(f"{prefix}.threshold: expected a finite height")
            for index, child in enumerate(value):
                walk(child, path + (str(index),))
        elif isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{dotted}: expected a finite number")
    walk(data, ())
