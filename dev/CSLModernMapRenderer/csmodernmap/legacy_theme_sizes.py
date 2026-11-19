"""Frozen 2026-11-17 size rules, used only by explicit theme updates."""
from __future__ import annotations

import copy

import math

from .road_hierarchy import RoadLevel, road_style_candidates, road_style_config

from .satellite_filter import DEFAULTS as SATELLITE_DEFAULTS

TRANSIT_DEFAULTS = {
    'Metro': {'width': 1.4, 'dash': ()}, 'Train': {'width': 1.4, 'dash': ()},
    'Monorail': {'width': 1.2, 'dash': ()}, 'Tram': {'width': 1.0, 'dash': ()},
    'CableCar': {'width': 1.0, 'dash': ()}, 'Ship': {'width': 1.1, 'dash': (8, 6)},
    'Ferry': {'width': 1.1, 'dash': (8, 6)}, 'Bus': {'width': .9, 'dash': ()},
    'Trolleybus': {'width': .9, 'dash': (4, 4)}, 'Airplane': {'width': 1.0, 'dash': (10, 8)},
    'Helicopter': {'width': .8, 'dash': (6, 6)},
}

ROAD_MIN_FILL = {
    RoadLevel.HIGHWAY: 1.8, RoadLevel.MAIN: 1.4, RoadLevel.BRANCH: 0.8,
    RoadLevel.SPECIAL: 0.8, RoadLevel.QUAY: 2.0,
    RoadLevel.BEAUTIFICATION: 0.5,
}

ROAD_WIDTH_SCALE = 0.15

QUAY_FOOTPRINT_SCALE = 0.5

ROAD_DEFAULTS = {"fill": "#FFFFFF", "border": "#C3C8D2", "border_width": 1.0}

TERRAIN_DEFAULTS = {"mode": "simple", "elevation_mode": "stepped",
                    "elevation_colors": [{"threshold": 0.0, "color": "#F6F7F9"}]}

HILLSHADE_DEFAULTS = {
    "enabled": False, "intensity": 0.3, "azimuth": 315.0, "altitude": 45.0,
    "large_scale_meters": 320.0, "small_scale_meters": 0.0,
    "large_scale_weight": 0.35, "small_scale_weight": 0.65,
}

SECTION_DEFAULTS = {
    "satellite": SATELLITE_DEFAULTS,
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
    "poi_icons": {"font": "SimHei"},
    "transports": {"fit_preview": True},
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

_POI_ICON_PROCESSING_KEYS = {
    "show_decorative_pois",
    "enabled", "merge_enabled", "merge_distance", "facility_hierarchy",
    "grid_size", "max_per_cell", "min_priority", "categories",
    "hidden_categories", "show_name", "name_filters", "visual_merge_enabled",
    "visual_merge_distance", "active_profile", "visibility_snapshot",
    "target", "building", "transit", "use_game_names",
}

def cleanup_theme(data: dict) -> dict:
    """Return an independent theme with retired fields removed and aliases resolved."""
    from .pixel_sizes import is_pixel_theme, validate
    if is_pixel_theme(data):
        validate(data)
        return copy.deepcopy(data)
    out = copy.deepcopy(data)
    out.pop("poi_area", None)
    buildings = out.get("buildings")
    if isinstance(buildings, dict):
        from .building_categories import validate_category_colors
        validate_category_colors(buildings)
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
                if level is RoadLevel.QUAY:
                    cfg = road_style_config(original_roads, level, mode)
                    if cfg:
                        roads[canonical] = copy.deepcopy(cfg)
                elif canonical not in original_roads:
                    alias = next((key for key in candidates[1:]
                                  if isinstance(original_roads.get(key), dict)
                                  and original_roads[key]), None)
                    if alias is not None:
                        roads[canonical] = copy.deepcopy(original_roads[alias])
                aliases_to_remove.update(candidates[1:])
                if level is RoadLevel.QUAY and isinstance(roads.get(canonical), dict):
                    roads[canonical].pop("min_fill_px", None)
                    if mode != "ground":
                        roads[canonical].pop("footprint_scale", None)
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
    terrain = out.get("terrain")
    if isinstance(terrain, dict):
        from .terrain import resolve_terrain_config
        out["terrain"] = resolve_terrain_config(migrate_terrain_colors(terrain))
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
    from .pixel_sizes import is_pixel_theme
    if is_pixel_theme(out):
        return out
    transports = out.setdefault("transports", {})
    if not isinstance(transports, dict):
        raise ValueError("transports must be an object")
    transports.setdefault("fit_preview", True)
    satellite = out.setdefault("satellite", {})
    if isinstance(satellite, dict):
        for key, value in SATELLITE_DEFAULTS.items():
            satellite.setdefault(key, value)
    from .ribbon_schema import FIELDS
    traffic = out.setdefault("traffic", {})
    if isinstance(traffic, dict) and "ramp" not in traffic:
        for key in ("free", "slow", "busy"):
            traffic.setdefault(key, FIELDS[f"traffic.{key}"].default)
    buildings = out.get('buildings')
    if isinstance(buildings, dict):
        from .building_categories import CATEGORIES
        buildings.setdefault('color_mode', 'poi')
        colors = buildings.setdefault('category_colors', {})
        for category in CATEGORIES:
            entry = colors.setdefault(category, {})
            entry.setdefault('enabled', False)
            entry.setdefault('color', buildings.get('default', '#D8D2C6'))
    roads = out.get("roads")
    if isinstance(roads, dict):
        target = out["roads"]
        target.setdefault("width_scale", ROAD_WIDTH_SCALE)
        target.setdefault("opacity", 1.0)
        # 始终依据原字典解析，避免填充新章节改变旧版特殊道路的回退外观。
        for level in RoadLevel:
            candidates = road_style_candidates(level)
            canonical = candidates[0]
            source = roads.get(canonical, {})
            cfg = copy.deepcopy(source)
            for key, value in ROAD_DEFAULTS.items():
                cfg.setdefault(key, value)
            if level is RoadLevel.QUAY:
                cfg.pop("min_fill_px", None)
                cfg.setdefault("footprint_scale", QUAY_FOOTPRINT_SCALE)
            else:
                cfg.setdefault("min_fill_px", ROAD_MIN_FILL[level])
            target[canonical] = cfg
    terrain = out.get("terrain")
    if isinstance(terrain, dict):
        terrain = out["terrain"] = migrate_terrain_colors(terrain)
        from .terrain import resolve_terrain_config
        terrain = out["terrain"] = resolve_terrain_config(terrain)
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
                if kind in {"road", "district"}:
                    current.setdefault("stroke_blur", 0.0)
    return out

def _resolve_size(val, render_scale: float) -> float:
    if isinstance(val, dict):
        return val.get("fixed", 0.0) + val.get("scaled", 0.0) * render_scale
    return float(val)

def _size_parts(value, default_fixed: float, default_scaled: float) -> dict:
    if value is None:
        return {"fixed": default_fixed, "scaled": default_scaled}
    if isinstance(value, dict):
        return {"fixed": float(value.get("fixed", default_fixed)),
                "scaled": float(value.get("scaled", default_scaled))}
    return {"fixed": float(value), "scaled": 0.0}

def railway_kind_config(raw, kind: str) -> dict:
    """按 PNG 图层的规则精确解析铁路类型。"""
    root = raw if isinstance(raw, dict) else {}
    cfg = root.get(kind) or {}
    if not isinstance(cfg, dict):
        cfg = {}
    if not cfg and kind == "train":
        cfg = {"line": root.get("line", "#C36E69"),
               "dash": root.get("dash", "#FFFFFF")}
    return cfg
