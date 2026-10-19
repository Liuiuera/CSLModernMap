"""无损规范化主题编辑器支持的地图主题配置。

只填充绘图器已经使用的默认值；可选章节及受启用状态影响的设置仍保持可选。"""
from __future__ import annotations

import copy
import math

from .road_hierarchy import RoadLevel, road_style_candidates

ROAD_MIN_FILL = {
    RoadLevel.HIGHWAY: 1.8, RoadLevel.MAIN: 1.4, RoadLevel.BRANCH: 0.8,
    RoadLevel.SPECIAL: 0.8, RoadLevel.BEAUTIFICATION: 0.5,
}
ROAD_WIDTH_SCALE = 0.15
ROAD_DEFAULTS = {"fill": "#FFFFFF", "border": "#C3C8D2", "border_width": 1.0}
TERRAIN_DEFAULTS = {"elevation_mode": "stepped",
                    "elevation_colors": [{"threshold": 0.0, "color": "#F6F7F9"}]}
HILLSHADE_DEFAULTS = {
    "enabled": False, "intensity": 0.3, "azimuth": 315.0, "altitude": 45.0,
    "large_scale_meters": 320.0, "small_scale_meters": 0.0,
    "large_scale_weight": 0.35, "small_scale_weight": 0.65,
}
SECTION_DEFAULTS = {
    "water": {"color": "#80D4FE"},
    "canvas": {"background": "#F6F7F9"},
    "district": {"border": "#B0B0B0", "border_width": 1, "fill_alpha": 64,
                 "smooth_blur": 1.0, "smooth_iters": 3, "contour_upscale": 2},
    "zone": {"border": "#B08A5C", "border_width": 1, "fill_alpha": 100,
             "fill": "#C8C8C8", "smooth_blur": 1.0, "smooth_iters": 3,
             "contour_upscale": 2},
    "land_use": {"detail": "base", "cell_meters": 16.0,
                 "smooth_blur": 1.5, "smooth_iters": 3, "contour_upscale": 2,
                 "alpha": 110},
    "built_area": {"cell_meters": 8.0, "buffer_meters": 60.0,
                   "smooth_blur": 1.0},
    "buildings": {"border": "#D7DAE1", "border_width": {"fixed": 0.25, "scaled": 0.1}},
    "procedural_objects": {"border": "#C6CAD3", "border_width": {"fixed": 0.20, "scaled": 0.08},
                           "custom_color_alpha": 1.0},
    "watermark": {"font_size": {"fixed": 11, "scaled": 1.5},
                  "separator": " · ", "margin": {"x": 20, "y": 20},
                  "padding": {"x": 12, "y": 6}, "border_radius": 6,
                  "background": "transparent", "city_color": "#1A1A1A",
                  "info_color": "#3A3A3A", "meta_color": "#6A6A6A"},
}
RETIRED_THEME_KEYS = {
    "transports": {"octilinear_snap"},
    "transit_view": {"corridor_parallel_gap", "corridor_taper"},
    "railway": {"width", "dash_width", "pattern"},
}
LABEL_DEFAULTS = {
    "road": {"size": 16, "color": "#505564", "stroke": "#FFFFFF", "stroke_width": 2},
    "district": {"size": 20, "color": "#32415A", "stroke": "#FFFFFF", "stroke_width": 2},
    "park": {"size": 12, "color": "#328246", "stroke": "#FFFFFF", "stroke_width": 1.5},
}


def field_default(path: tuple):
    """保存编辑副本中实际绘图器使用的默认值。"""
    if path == ("roads", "width_scale"):
        return ROAD_WIDTH_SCALE
    if len(path) == 3 and path[0] == "roads":
        level = next((level for level in RoadLevel if road_style_candidates(level)[0] == path[1]), None)
        if level is not None:
            return ROAD_MIN_FILL[level] if path[2] == "min_fill_px" else ROAD_DEFAULTS.get(path[2])
    if len(path) == 2:
        return (TERRAIN_DEFAULTS if path[0] == "terrain" else SECTION_DEFAULTS.get(path[0], {})).get(path[1])
    if len(path) == 3 and path[:2] == ("terrain", "hillshade"):
        return HILLSHADE_DEFAULTS.get(path[2])
    if len(path) == 3 and path[0] == "labels":
        return LABEL_DEFAULTS.get(path[1], {}).get(path[2])
    return None


def cleanup_theme(data: dict) -> dict:
    """Return an independent theme with retired fields removed and aliases resolved."""
    out = copy.deepcopy(data)
    out.pop("poi_area", None)
    buildings = out.get("buildings")
    if isinstance(buildings, dict):
        for key in ("residential", "commercial", "industrial", "office",
                    "publictransport", "beautification", "public"):
            buildings.pop(key, None)
        tint = buildings.get("poi_tint")
        if isinstance(tint, dict):
            for key in ("enabled", "mix_white", "categories"):
                tint.pop(key, None)
    for section, keys in RETIRED_THEME_KEYS.items():
        current = out.get(section)
        if isinstance(current, dict):
            for key in keys:
                current.pop(key, None)
    transports = out.get("transports")
    if isinstance(transports, dict):
        view = transports.get("transit_view")
        if isinstance(view, dict):
            for key in RETIRED_THEME_KEYS["transit_view"]:
                view.pop(key, None)
    roads = out.get("roads")
    original_roads = data.get("roads")
    if isinstance(roads, dict) and isinstance(original_roads, dict):
        aliases_to_remove = set()
        canonical_keys = set()
        for mode in ("ground", "elevated", "tunnel"):
            if mode != "ground" and not any(
                key.endswith("_elevated" if mode == "elevated" else "_tunnel")
                and key in original_roads
                for level in RoadLevel
                for key in road_style_candidates(level, mode)
            ):
                continue
            for level in RoadLevel:
                candidates = road_style_candidates(level, mode)
                canonical = candidates[0]
                canonical_keys.add(canonical)
                if canonical not in original_roads:
                    alias = next((key for key in candidates[1:]
                                  if isinstance(original_roads.get(key), dict)
                                  and original_roads[key]), None)
                    if alias is not None:
                        roads[canonical] = copy.deepcopy(original_roads[alias])
                aliases_to_remove.update(candidates[1:])
        for alias in aliases_to_remove - canonical_keys:
            roads.pop(alias, None)
        for config in roads.values():
            if isinstance(config, dict):
                config.pop("width", None)
    railway = out.get("railway")
    source_railway = data.get("railway")
    if isinstance(railway, dict) and isinstance(source_railway, dict):
        train = railway.get("train")
        if not isinstance(train, dict) or not train:
            if "line" in source_railway or "dash" in source_railway:
                railway["train"] = {
                    "line": copy.deepcopy(source_railway.get("line", "#C36E69")),
                    "dash": copy.deepcopy(source_railway.get("dash", "#FFFFFF")),
                }
        railway.pop("line", None)
        railway.pop("dash", None)
    return out


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


def normalize_theme(data: dict) -> dict:
    """返回独立的标准化编辑副本，并保留额外字段。"""
    out = cleanup_theme(data)
    roads = out.get("roads")
    if isinstance(roads, dict):
        target = out["roads"]
        target.setdefault("width_scale", ROAD_WIDTH_SCALE)
        # 始终依据原字典解析，避免填充新章节改变旧版特殊道路的回退外观。
        for level in RoadLevel:
            candidates = road_style_candidates(level)
            canonical = candidates[0]
            source = roads.get(canonical, {})
            cfg = copy.deepcopy(source)
            for key, value in ROAD_DEFAULTS.items():
                cfg.setdefault(key, value)
            cfg.setdefault("min_fill_px", ROAD_MIN_FILL[level])
            target[canonical] = cfg
    terrain = out.get("terrain")
    if isinstance(terrain, dict):
        terrain = out["terrain"] = migrate_terrain_colors(terrain)
        for key, value in TERRAIN_DEFAULTS.items():
            terrain.setdefault(key, copy.deepcopy(value))
        shade = terrain.setdefault("hillshade", {})
        if isinstance(shade, dict):
            for key, value in HILLSHADE_DEFAULTS.items():
                shade.setdefault(key, value)
    for section, defaults in SECTION_DEFAULTS.items():
        current = out.get(section)
        # 部分绘图器将空缺章节视为关闭。
        if isinstance(current, dict) and current:
            for key, value in defaults.items():
                current.setdefault(key, copy.deepcopy(value))
    labels = out.get("labels", {})
    if isinstance(labels, dict):
        for kind, defaults in LABEL_DEFAULTS.items():
            current = labels.get(kind)
            if isinstance(current, dict):
                for key, value in defaults.items():
                    current.setdefault(key, copy.deepcopy(value))
    return out


def validate_theme_values(data: dict) -> None:
    """在编辑边界拒绝格式错误的数值、尺寸或渐变输入。"""
    from .theme_metadata import field_kind
    def walk(value, path):
        if path[:2] == ("terrain", "ground"):
            return
        dotted = ".".join(path)
        if isinstance(value, dict):
            if ("fixed" in value or "scaled" in value) and field_kind(path) in {"size", "number"}:
                for part in ("fixed", "scaled"):
                    number = value.get(part, 0)
                    if isinstance(number, bool) or not isinstance(number, (int, float)):
                        raise ValueError(f"{dotted}.{part}: expected a number")
                    if not math.isfinite(number) or number < 0:
                        raise ValueError(f"{dotted}.{part}: expected a finite nonnegative number")
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


def convert_theme_file(source, destination) -> None:
    """仅显式文件转换使用此入口，打开草稿时不会调用。"""
    from pathlib import Path
    from .theme_metadata import validate_theme_fields
    import yaml
    source, destination = Path(source), Path(destination)
    if source.name == "metro_map.yaml":
        raise ValueError("metro_map.yaml uses the independent metro contract")
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("theme data must be an object")
    converted = normalize_theme(data)
    validate_theme_values(converted)
    validate_theme_fields(converted, data)
    destination.write_text(yaml.safe_dump(converted, allow_unicode=True, sort_keys=False), encoding="utf-8")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Normalize a map theme without changing size representations or unknown data.")
    parser.add_argument("source")
    parser.add_argument("--output", required=True, help="Explicit destination YAML path")
    args = parser.parse_args()
    convert_theme_file(args.source, args.output)
