"""编译版本化的 MapLibre 预览场景并提供本地资源服务。

预览直接使用 DocumentSession，不重复解析存档；地形图像复用正式地形图层。场景资源目录在发布后保持不变，便于 WebView 按需读取大型 GeoJSON。"""
from __future__ import annotations

from .zone_geometry import grid_rings
from .path_model import chaikin

import hashlib
import json
import logging
import mimetypes
import os
import secrets
import shutil
import tempfile
import threading
import time
from math import cos, hypot, isfinite, log2, sin
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import numpy as np
from PIL import Image, ImageFilter
from .road_geometry import clip_polyline_span
from .road_hierarchy import RoadLevel, road_style_candidates
from .preview_road_order import VERSION as ROAD_ORDER_VERSION, build_road_order
from .traffic import DIRECTION_BACKWARD, DIRECTION_FORWARD
from .i18n import tr


LOGGER = logging.getLogger(__name__)

# 路况分色的两个方向（顺序 = 产出 feature 的顺序）。提到模块级避免每条路段重建。
_TRAFFIC_BANDS = ((DIRECTION_FORWARD, "forward"), (DIRECTION_BACKWARD, "backward"))


SCHEMA_VERSION = 1
WORLD_HALF = 8640.0
METERS_PER_DEGREE = 111_195.0
_LEVEL_RANK = {
    RoadLevel.BEAUTIFICATION: 0,
    RoadLevel.SPECIAL: 1,
    RoadLevel.BRANCH: 2,
    RoadLevel.MAIN: 3,
    RoadLevel.HIGHWAY: 4,
}
_LEVEL_MIN_FILL_PX = {
    RoadLevel.HIGHWAY: 1.8,
    RoadLevel.MAIN: 1.4,
    RoadLevel.BRANCH: 0.8,
    RoadLevel.SPECIAL: 0.8,
    RoadLevel.BEAUTIFICATION: 0.5,
}

_ROAD_CHAIN_VERSION = 3
_ROAD_WIDTH_EPSILON = 0.1
_ROAD_TRANSITION_SLICES = 8
_WATER_PREVIEW_VERSION = 3
_WATER_PREVIEW_UPSCALE = 4
# 保留 CS1 1081² 网格的四倍采样，CS2 4096² 网格使用原始分辨率。
_WATER_PREVIEW_MAX_GRID_EDGE = 1081 * _WATER_PREVIEW_UPSCALE
_WATER_PREVIEW_RDP = 3.0
_WATER_PREVIEW_CHAIKIN = 2
_WATER_PREVIEW_MIN_AREA = 45.0
_BUILT_PREVIEW_CHAIKIN_MAX = 2


def _preview_render_stack() -> tuple[str, ...]:
    """从共享图层目录读取实际绘制顺序。"""
    from .layers.registry import painter_order_keys

    return painter_order_keys()


def world_to_geo(x: float, z: float) -> list[float]:
    """与正式渲染器一致：屏幕向右是负 Z，向上是正 X。"""
    return [-float(z) / METERS_PER_DEGREE, float(x) / METERS_PER_DEGREE]


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def _document_fingerprint(document) -> str:
    metadata = getattr(document, "metadata", None)
    origin = getattr(metadata, "origin", None)
    payload = {
        "schema": getattr(document, "schema_version", "cmm-v1"),
        "source": getattr(origin, "source_name", ""),
        "exported": getattr(metadata, "exported_at", ""),
        "nodes": len(getattr(document, "nodes", ())),
        "networks": len(getattr(document, "networks", ())),
        "lastNetwork": max(
            (int(getattr(item, "id", 0)) for item in document.networks),
            default=0,
        ),
        "streetNames": [[item.id, item.display_name, item.game_name] for item in document.networks],
        # 设施由这些精确事实推导，场景身份必须包含它们，避免父级链接或网格改变后复用旧预览。
        "buildings": [
            {
                "id": int(getattr(item, "id", 0)),
                "parent": str(getattr(item, "parent_building_id", "")
                               or getattr(item, "parent_id", "") or ""),
                "child": str(getattr(item, "sub_building_id", "") or ""),
                "displayName": getattr(item, "display_name").to_dict(),
                "category": [item.category_id, item.category_name, item.category_locale],
                "growth": [item.growth_kind, item.growth_use],
                "children": sorted(int(value) for value in
                                   (getattr(item, "child_ids", ()) or ())),
                "points": [
                    [float(getattr(point, "x", 0.0)),
                     float(getattr(point, "z", 0.0))]
                    for point in getattr(getattr(item, "footprint", None),
                                         "points", ())
                ],
            }
            for item in document.buildings
        ],
        "zones": [
            {
                "id": int(getattr(item, "id", 0)),
                "kind": str(getattr(item, "kind", "")),
                "raw": str(getattr(item, "source_raw", "")),
                "cell": float(getattr(item, "grid_cell", 0.0) or 0.0),
                "grid": [
                    [float(getattr(point, "x", 0.0)),
                     float(getattr(point, "z", 0.0))]
                    for point in getattr(item, "grid_points", ())
                ],
            }
            for item in document.districts if not item.is_background
        ],
    }
    return hashlib.sha256(_json_bytes(payload)).hexdigest()[:24]


def _border_width(cfg: dict) -> tuple[float, float]:
    value = cfg.get("border_width", 1.0)
    if isinstance(value, dict):
        return float(value.get("fixed", 0.0)), float(value.get("scaled", 0.0))
    return float(value), 0.0


def road_theme(style: Any) -> dict:
    """返回预览器使用的渲染器无关道路绘制参数。"""
    data = getattr(style, "data", style) or {}
    roads = data.get("roads", {})
    levels = {}
    for level in RoadLevel:
        for mode in ("ground", "elevated", "tunnel"):
            cfg = {}
            for key in road_style_candidates(level, mode):
                candidate = roads.get(key, {})
                if candidate:
                    cfg = candidate
                    break
            fixed, scaled = _border_width(cfg)
            mode_enabled = True
            if mode == "tunnel":
                mode_enabled = bool(roads.get("tunnel", {}).get(
                    "enabled", True))
            elif mode == "elevated":
                mode_enabled = bool(roads.get("bridge", {}).get(
                    "enabled", True))
            levels[f"{level.value}:{mode}"] = {
                "fill": cfg.get("fill", "#FFFFFF"),
                "border": cfg.get("border", "#C3C8D2"),
                "minFillPx": float(cfg.get(
                    "min_fill_px", _LEVEL_MIN_FILL_PX[level])),
                "borderFixedPx": fixed,
                "borderScale": scaled,
                "dash": cfg.get("dash"),
                "enabled": bool(cfg.get("enabled", True)) and mode_enabled,
            }
    return {
        "background": data.get("canvas", {}).get("background", "#F6F7F9"),
        "widthScale": float(roads.get("width_scale", 0.15)),
        "levels": levels,
    }


def _preview_number(value: Any, default: float) -> float:
    """在标称比例下解析标量或固定值加缩放值的尺寸。"""
    if isinstance(value, dict):
        value = float(value.get("fixed", 0.0)) + float(value.get("scaled", 0.0))
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


# 公共交通视图线路标签规范的尺寸基准 = 长边 1024px 的成图。
# 导出侧 render_scale = 输出宽 / 4096，故 1024px 对应 0.25（见 transport_layer._resolve_size）。
_TRANSIT_LABEL_BASELINE = 0.25


def _preview_baseline_size(value: Any, default: float) -> float:
    """把 ``{fixed, scaled}``／标量尺寸换算到「长边 1024px 成图」的等效值。

    与导出侧 ``transport_layer._resolve_size`` 同一套公式，只是把
    ``render_scale`` 固定成 ``_TRANSIT_LABEL_BASELINE``。预览拿到的是
    「1024px 成图上的像素值」，再按 ``画布宽 / 1024`` 折算成 CSS 像素，
    从而与导出的标签保持同样的相对大小。
    """
    if isinstance(value, dict):
        value = (float(value.get("fixed", 0.0))
                 + float(value.get("scaled", 0.0)) * _TRANSIT_LABEL_BASELINE)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _transit_label_style(data: dict) -> dict:
    """公共交通视图线路标签的视觉 token。

    与导出侧共用主题 ``transit_view`` 段（规范全文见
    尺寸统一换算到「长边 1024px 成图」基准后下发。
    """
    transport = data.get("transports") or data.get("transport") or {}
    # ``transit_view`` 与 ``label`` 都是 transports 段的子键（导出侧 cfg 即该段）；
    # 兼容主题把 ``transit_view`` 放在顶层的写法。
    view = transport.get("transit_view") or data.get("transit_view") or {}
    label = transport.get("label") or {}

    size = _preview_baseline_size(view.get("label_size"), 15.0)

    return {
        "fontSizeBase": size,
        # 内边距 / 轮廓 / 缝与导出侧同式，都由**字号**等比例派生
        # （规范基准：字号 15 → 内边距 9 / 轮廓 2 / 缝 3，即 0.60 / 2⁄15 / 0.20）。
        # 主题显式给出时以主题为准，避免预览与导出的口径分叉。
        "paddingRatio": 0.60,
        "outlineWidth": _preview_baseline_size(
            view.get("label_border_width"), size * 2.0 / 15.0),
        "outlineColor": str(view.get(
            "label_outer_border_color", "#101828") or "#101828"),
        "outlineAlpha": float(view.get("label_outer_border_alpha", 0.55) or 0.0),
        "seamWidth": _preview_baseline_size(
            view.get("label_seam_width"), size * 3.0 / 15.0),
        "textOnLight": str(view.get(
            "label_text_on_light", "#101828") or "#101828"),
        "textOnDark": str(view.get(
            "label_text_on_dark", "#FFFFFF") or "#FFFFFF"),
        "luminanceThreshold": float(view.get(
            "label_luminance_threshold", 165.0) or 0.0),
        # 导出侧 Pillow 只有「加粗 / 不加粗」两档，固定 700 与之对应
        "fontWeight": 700,
        "font": str(label.get("font") or "Microsoft YaHei"),
        # 重合线路合并半径（与导出 label_merge_dist 对齐，供后续拼接使用）
        "mergeDistance": _preview_baseline_size(
            view.get("label_merge_dist"), 40.0),
    }


def _label_halo(section: Any) -> bool:
    """该文字类别是否画描边。

    主题里描边由 ``stroke``（颜色）+ ``stroke_width`` 一起表达：颜色缺失、或宽度
    解析出来是 0，都算关闭（例如 apple_maps 的 building / park / stop 就没有这两项）。

    注意：这条规则对 POI 名字**不适用** —— 它的描边在导出侧是内置的、不读主题，
    单独走 :func:`_poi_name_halo`。
    """
    if not isinstance(section, dict) or not section.get("stroke"):
        return False
    return _preview_number(section.get("stroke_width"), 1.0) > 0


def _poi_name_halo(poi: Any) -> bool:
    """POI 描边使用名称字号比例；缺省为白色、0.12。"""
    if not isinstance(poi, dict):
        return True
    return bool(poi.get("name_stroke", "#FFFFFF")) and float(poi.get("name_stroke_ratio", .12)) > 0


def _label_stroke(section: Any, default_color: str = "#FFFFFF",
                  default_width: float = 1.0) -> dict:
    """该文字类别的描边参数：颜色 + 「长边 1024px 成图」基准下的宽度。

    与导出侧 ``layers/label_layer.py`` 读同一组主题键（``stroke`` /
    ``stroke_width``）。没有颜色或宽度为 0 视为关闭，Viewer 直接不画。
    """
    if not isinstance(section, dict) or not section.get("stroke"):
        return {"color": default_color, "width": 0.0}
    return {
        "color": str(section.get("stroke")),
        "width": float(_preview_baseline_size(
            section.get("stroke_width"), default_width)),
    }


def preview_theme(style: Any, options: dict[str, Any] | None = None) -> dict:
    """返回中央预览器使用的全部轻量绘制参数。"""
    data = getattr(style, "data", style) or {}
    from .style import Style
    data = Style(data).data
    options = options or {}
    buildings = data.get("buildings", {})
    land_use = data.get("land_use", {})
    district = data.get("district", {})
    zone = data.get("zone", {})
    transport = data.get("transports", data.get("transport", data.get("transit", {})))
    labels = data.get("labels", data.get("label", {}))
    label_default = labels.get("road", labels) if isinstance(labels, dict) else {}
    label_sections = {
        key: (labels.get(key, {}) if isinstance(labels, dict) else {})
        for key in ("district", "road", "building", "park", "stop")
    }
    railway = data.get("railway", {})
    poi = data.get("poi_icons", data.get("poi", {}))
    poi_visibility = poi.get("visibility_snapshot", {}) if isinstance(poi, dict) else {}
    if not isinstance(poi_visibility, dict):
        poi_visibility = {}
    from .poi_profiles import normalize_poi_visibility_snapshot
    poi_visibility = normalize_poi_visibility_snapshot(poi_visibility)
    po = data.get("procedural_objects", {})
    elevation_colors = data.get("terrain", {}).get("elevation_colors") or []
    built_area = data.get("built_area", {})
    from .poi_color import poi_tint_mix_white
    poi_tint_mix = poi_tint_mix_white(buildings)
    from .layers.traffic_layer import resolve_traffic_style
    from .layers.railway_layer import resolve_railway_style
    traffic = resolve_traffic_style(data.get("traffic", {}))
    return {
        "road": road_theme(data),
        "terrain": {
            "color": (elevation_colors[0].get("color") if elevation_colors else
                      data.get("canvas", {}).get("background", "#F6F7F9")),
            "opacity": 1.0,
            "forest": data.get("forest", {}).get("color", "#B8DCA0"),
        },
        "water": {"fill": data.get("water", {}).get("color", "#80D4FE"),
                  "border": data.get("water", {}).get("border", "#60C0E8"),
                  "width": _preview_number(
                      data.get("water", {}).get("border_width"), 1.0)},
        "building": {
            "fill": buildings.get("default", "#D8D2C6"),
            "border": buildings.get("border", "#B9B2A5"),
            "colorMode": str(buildings.get("color_mode", "poi")),
            "poiTintMixWhite": poi_tint_mix,
        },
        "poiVisibility": poi_visibility,
        "built_area": {
            "fill": built_area.get("color", "#F1F0EB"),
            "opacity": float(built_area.get("opacity", 1)),
            "bufferMeters": float(built_area.get("buffer_meters", 60.0)),
            "edgeMeters": float(built_area.get("edge_meters", 0.0) or 0.0),
        },
        "land_use": {"opacity": float(land_use.get("alpha", 110)) / 255.0,
                     "colors": land_use.get("colors", {})},
        "railway": resolve_railway_style(railway),
        "traffic": {"ramp": traffic["ramp"],
                    "noDataColor": traffic["no_data_color"],
                    "widthScale": traffic["width_scale"],
                    "minLanePx": traffic["min_lane_px"]},
        "transit": {"width": _preview_number(transport.get("width"), 3.0),
                    "typeWidths": {str(kind).lower(): _preview_number(
                        cfg.get("width", transport.get("width")), 3.0)
                        for kind, cfg in transport.get("types", {}).items()
                        if isinstance(cfg, dict)},
                    "stationSize": _preview_number(
                        transport.get("station", {}).get("size"), 8.0),
                    "label": {
                        "enabled": bool(transport.get("label", {}).get(
                            "enabled", True)),
                        "size": _preview_number(transport.get("label", {}).get(
                            "size"), 12.0),
                        "repeatInterval": _preview_number(
                            transport.get("label", {}).get("repeat_interval"),
                            600.0),
                        "maxPerLine": int(transport.get("label", {}).get(
                            "max_per_line", 4) or 4),
                        "background": str(transport.get("label", {}).get(
                            "bg", "#FFFFFF") or "#FFFFFF"),
                        "border": str(transport.get("label", {}).get(
                            "border", "#D4D9E0") or "#D4D9E0"),
                    },
                    # 公共交通视图线路标签的视觉规范：与导出共用主题
                    # ``transit_view`` 段，预览按画布宽度折算成 CSS 像素。
                    "labelStyle": _transit_label_style(data)},
        "district": {"border": district.get("border", "#A6A6AD"),
                     "width": _preview_number(district.get("border_width"), 1.0),
                     "fill": (district.get("fill_colors") or ["#D9E8CC"])[0],
                     "opacity": float(district.get("fill_alpha", 90)) / 255.0},
        "zone": {"border": zone.get("border", "#A89880"),
                 "width": _preview_number(zone.get("border_width"), 1.0),
                 "fill": zone.get("fill",
                     zone.get("colors", {}).get("default", "#C8C8C8")),
                 "opacity": float(zone.get("fill_alpha", 90)) / 255.0},
        "poi": {"size": _preview_number(poi.get("icon_size", poi.get("size")), 18.0)},
        "baseText": data.get("labels", {}),
        "label": {"color": label_default.get("color", "#39424C"),
                  "size": _preview_number(label_default.get("size"), 12.0),
                  "metrics": {
                      "sizes": {**{key: section.get("size", 12.0)
                                   for key, section in label_sections.items()},
                                "poi": poi.get("name_size", {"fixed": 11, "scaled": 2})},
                      "strokeWidths": {key: section.get("stroke_width", 0)
                                       for key, section in label_sections.items()},
                      "poiMaxSize": poi.get("name_max_size"),
                      "poiStrokeRatio": float(poi.get("name_stroke_ratio", .12)),
                      "poiTransitScale": min(2.0, max(1.05, float(poi.get("transit_view_scale", 1.15)))),
                      "station": (transport.get("station", {}).get("names") or {}),
                  },
                  # 文字开关属于展示状态，不属于数据源。Viewer 用这组值
                  # 即时重排标签，切换时无需重新生成整份预览场景。
                  "visibility": {
                      "road": bool(options.get("road_names", True)),
                      "roadPlayer": bool(options.get(
                          "road_player_names", True)),
                      "gameNames": bool(data.get("roads", {}).get("use_game_names", False)),
                      "district": bool(options.get("district_names", True)),
                  },
                  "sizes": {**{
                      key: _preview_number(section.get("size"), 12.0)
                      for key, section in label_sections.items()
                  }, "poi": _preview_number(poi.get("name_size"), 12.0)},
                  # 字体按文字类别各自下发（主题 labels 段的 road / district /
                  # building / park / stop 各有一个 font）。缺项留空串，
                  # 由 Viewer 回退到通用字体，不会画出方框。
                  "fonts": {**{
                      key: str(section.get("font") or "").strip()
                      for key, section in label_sections.items()
                  }, "poi": str(poi.get("font") or "").strip()},
                  "colors": {
                      key: section.get("color", label_default.get(
                          "color", "#39424C"))
                      for key, section in label_sections.items()
                  },
                  # 描边开关也按类别下发（Viewer 用它决定要不要画 text-shadow）。
                  # POI 走 _poi_name_halo：导出侧那圈白色描边是内置的，不看主题。
                  "halo": bool(label_default.get("stroke", True)),
                  "halos": {**{
                      key: _label_halo(section)
                      for key, section in label_sections.items()
                  }, "poi": _poi_name_halo(poi)},
                  # 描边宽度 / 颜色同样按类别下发。导出侧按主题 stroke_width 画
                  # 1.4~2.6px，而预览曾把宽度写死成 1px 白色 —— 两端粗细对不上，
                  # 这正是「预览描边偏细、看着像丢了」的一半原因。
                  "strokes": {**{
                      key: _label_stroke(section)
                      for key, section in label_sections.items()
                  }, "poi": {
                      "color": str(poi.get("name_stroke", "#FFFFFF") or "#FFFFFF"),
                      # 导出侧 poi_icon_layer：描边宽 = 名字字号 × 0.12
                      "width": (max(float(round(_preview_baseline_size(
                          poi.get("name_size"), 11.0) * float(poi.get("name_stroke_ratio", .12)))), 1.0)
                          if _poi_name_halo(poi) else 0.0),
                  }}},
        "procedural_objects": {
            "fill": po.get("color", po.get("colors", {}).get("unknown", "#D8D2C6")),
            "border": po.get("border", "#8E877C")},
        "watermark": {
            "text": data.get("watermark", {}).get(
                "text", data.get("watermark", {}).get("mod_name", "CSLModernMap")),
            "position": ("bl" if data.get("watermark", {}).get(
                "position", "right_bottom") in ("bl", "left_bottom") else "br"),
            "opacity": float(data.get("watermark", {}).get("opacity", .4)),
        },
    }


def _enum_text(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _building_display_name(value: Any) -> str:
    """返回导出的玩家或游戏显示名称，绝不使用资源预制件标签。"""
    text = str(getattr(value, "text", "") or "").strip()
    source = str(getattr(value, "source", "") or "").casefold()
    return text if text and source in {"custom", "game"} else ""


def _preview_transit_selection(style: Any, options: dict[str, Any]):
    """为全部预览交通使用方解析同一线路选择。"""
    from .services.transit import TransitSelection

    data = getattr(style, "data", style) or {}
    transport_cfg = data.get("transports", {}) or {}
    raw_types = (options["transit_types"] if "transit_types" in options else
                 transport_cfg.get("enabled")
                 if isinstance(transport_cfg.get("enabled"), (list, tuple))
                 else None)
    raw_lines = next(
        (options[key] for key in
         ("transit_line_ids", "enabled_line_ids", "line_ids")
         if key in options),
        transport_cfg.get("enabled_line_ids", transport_cfg.get("line_ids")),
    )
    return TransitSelection.from_mapping(
        types=raw_types,
        line_ids=raw_lines,
    )


def _preview_transit_model(mapdata, style: Any, options: dict[str, Any], *, session=None):
    """构建线路、站点和 POI 共用的已筛选 CMM 模型。"""
    from .cmm import GroupingParams, StationMergeParams
    from .services.transit import build_transit_model

    data = getattr(style, "data", style) or {}
    transport_cfg = data.get("transports", {}) or {}
    station_cfg = transport_cfg.get("station", {}) or {}
    grouping_cfg = transport_cfg.get("grouping", {}) or {}
    selection = _preview_transit_selection(style, options)
    merge_enabled = bool(options.get(
        "station_merge_enabled", station_cfg.get("merge_enabled", True)))
    merge_range = float(options.get(
        "station_merge_range", station_cfg.get("merge_range", 48.0)) or 48.0)
    params = selection.to_job_params(
        station_merge=StationMergeParams(
            merge_enabled=merge_enabled,
            merge_range=merge_range,
            merge_bus_tram=bool(station_cfg.get("merge_bus_tram", True)),
            merge_train_metro=bool(station_cfg.get("merge_train_metro", False)),
            overrides=station_cfg.get("merge_overrides") or {},
        ),
        grouping=GroupingParams(
            by_name=bool(grouping_cfg.get("by_name", True)),
            sequence_fallback=bool(grouping_cfg.get("sequence_fallback", True)),
            share_ratio=float(grouping_cfg.get("share_ratio", 0.0) or 0.0),
            merge_partial_overlap=bool(
                grouping_cfg.get("merge_partial_overlap", True)),
            share_corridors=bool(grouping_cfg.get("share_corridors", False)),
        ),
        auto_color=bool(transport_cfg.get("auto_color", True)),
    )
    return (session.transit_model(params) if session is not None
            else build_transit_model(mapdata, params))


def _preview_water_upscale(document) -> int:
    water = document.terrain.water_depth.values
    if water is None:
        return _WATER_PREVIEW_UPSCALE
    return max(1, min(_WATER_PREVIEW_UPSCALE,
                      _WATER_PREVIEW_MAX_GRID_EDGE // max(water.shape)))


def _feature(feature_id: str, geometry: dict, **properties: Any) -> dict:
    return {"type": "Feature", "id": feature_id,
            "properties": {"featureId": feature_id, **properties},
            "geometry": geometry}


def _polygon_geometry(points) -> dict | None:
    ring = [world_to_geo(point.x, point.z) for point in points]
    if len(ring) < 3:
        return None
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return {"type": "Polygon", "coordinates": [ring]}


def _grid_contour_geometries(items, cell: float, layer, cfg: dict) -> dict[int, list]:
    """复用行政区和功能区 PNG 图层的平滑轮廓规则，避免预览暴露每个原始网格方块。"""
    items = [item for item in items if item.grid_points]
    if not items:
        return {}
    if cell <= 0:
        cell = max((float(getattr(item, "grid_cell", 0.0) or 0.0)
                    for item in items), default=0.0)
    if cell <= 0:
        cell = float(layer._infer_cell(items))
    if cell <= 0:
        return {}

    class _FullWorld:
        world_x0 = -WORLD_HALF
        world_z0 = -WORLD_HALF
        world_x1 = WORLD_HALF
        world_z1 = WORLD_HALF

    result = layer._build_mask(items, cell, _FullWorld())
    if result is None:
        return {}
    mask, ox, oz = result
    array = np.asarray(mask, dtype=np.int32)
    blur = float(cfg.get("smooth_blur", 1.0) or 0.0)
    upscale = max(1, int(cfg.get("contour_upscale", 2) or 1))
    iterations = max(0, int(cfg.get("smooth_iters", 3) or 0))
    output: dict[int, list] = {}
    for index, item in enumerate(items):
        polygons = []
        for ring in grid_rings(
                array, index + 1, cell, ox, oz, upscale, blur):
            points = chaikin(ring, iterations)
            geo_ring = [world_to_geo(x, z) for x, z in points]
            if len(geo_ring) < 3:
                continue
            if geo_ring[0] != geo_ring[-1]:
                geo_ring.append(geo_ring[0])
            polygons.append([geo_ring])
        if polygons:
            output[int(item.id)] = polygons
    return output


def _line_geometry(points) -> dict | None:
    coordinates = [world_to_geo(point.x, point.z) for point in points]
    if len(coordinates) < 2:
        return None
    return {"type": "LineString", "coordinates": coordinates}


def _unit_normals(points) -> list[tuple[float, float]]:
    """每个点处切线 ``(dx, dz)`` 的右法线 ``(-dz, dx)/|·|``（世界坐标单位向量）。

    中间点用中心差分、端点用单侧差分，保证拐角处的偏移线仍然平行。
    重合点没有切线，记 ``(0, 0)`` 原地不动，避免除零。
    双向路两个方向共用同一份法线，省掉一半的切线计算。
    """
    last = len(points) - 1
    if last < 1:  # 退化成点/空：返回零法线而不是抛异常（否则整层路况都会被拖没）
        return [(0.0, 0.0)] * len(points)
    normals = []
    for index, point in enumerate(points):
        if index == 0:
            dx, dz = points[1].x - point.x, points[1].z - point.z
        elif index == last:
            dx, dz = point.x - points[index - 1].x, point.z - points[index - 1].z
        else:
            dx = points[index + 1].x - points[index - 1].x
            dz = points[index + 1].z - points[index - 1].z
        span = hypot(dx, dz)
        normals.append((0.0, 0.0) if span <= 1e-9 else (-dz / span, dx / span))
    return normals


def _band_geometry(points, offset: float, normals) -> dict | None:
    """沿法线把世界坐标折线平行挪 ``offset`` 米，再转经纬度 LineString。

    偏移必须在**世界坐标（米）**里做：直接改经纬度会随纬度变形（同样米数，
    经度跨度随纬度变化）。法线取行进方向右侧，正好对上 ``TrafficLane.position``
    的符号约定（正 = 前进方向右侧）。世界偏移 ``(ox, oz)`` 对应的经纬度增量是
    ``(-oz/M, ox/M)``（见 ``world_to_geo``），于是每点只需两次除法。
    这里刻意写成列表推导而不是 ``for``+``append``：同一条预览里有几万条路段 ×
    4 个点，循环开销会直接被放大成几百毫秒。
    """
    if len(points) < 2:
        return None
    return {"type": "LineString", "coordinates": [
        [(-point.z - nz * offset) / METERS_PER_DEGREE,
         (point.x + nx * offset) / METERS_PER_DEGREE]
        for point, (nx, nz) in zip(points, normals)]}


def _traffic_features(segment) -> list[dict]:
    """把一条路段拆成按行驶方向分色的一到两条色带。

    - 两个方向都有有效车道 → 两条，id ``traffic:{id}:f`` / ``:b``。
    - 只有一个方向有有效车道 → 一条，id 沿用旧的 ``traffic:{id}``（兼容旧逻辑）。
    - 完全没有有效车道（全是非机动车道 / 无统计 / 降级数据）→ 退回旧的
      「一条中心线 + 路段加权均值」：宁可不分方向，也不让原本有色的路段变空白。
      路段加权均值也是 ``-1`` 时不产出 feature（前端本就按 ``congestion >= 0`` 过滤）。
    """
    points = segment.world_points()
    segment_id = int(segment.segment_id)
    center_geometry = _line_geometry(points)
    if not center_geometry:
        return []
    road_width = float(segment.width if segment.width > 0 else sum(
        max(float(lane.width), 0.0) for lane in segment.lanes))
    features = [_feature(
        f"traffic:{segment_id}:footprint", center_geometry,
        segmentId=segment_id, roadWidthMeters=road_width,
        geometryRole="traffic-footprint")]
    buckets = segment.directional_lanes()
    if not buckets:
        stats = getattr(segment, "seg_stats", None)
        congestion = float(getattr(stats, "weighted_avg_congestion", -1.0))
        if isfinite(congestion) and 0.0 <= congestion <= 1.0:
            features.append(_feature(
                f"traffic:{segment_id}", center_geometry,
                segmentId=segment_id, congestion=congestion,
                directionWidthMeters=road_width, geometryRole="traffic"))
        return features

    two_way = len(buckets) > 1
    normals = None
    for direction, label in _TRAFFIC_BANDS:
        lanes = buckets.get(direction)
        if not lanes:
            continue
        offset = segment.direction_offset(direction, lanes)
        if offset == 0.0:
            geometry = _line_geometry(points)
        else:
            if normals is None:
                normals = _unit_normals(points)
            geometry = _band_geometry(points, offset, normals)
        if not geometry:
            continue
        total_width = sum(max(float(lane.width), 0.0) for lane in lanes)
        if total_width <= 0.0:
            continue
        congestion = sum(lane.congestion * max(float(lane.width), 0.0)
                         for lane in lanes) / total_width
        feature_id = (f"traffic:{segment_id}:{label[0]}" if two_way
                      else f"traffic:{segment_id}")
        features.append(_feature(
            feature_id, geometry, segmentId=segment_id, direction=label,
            congestion=congestion, directionWidthMeters=total_width,
            geometryRole="traffic"))
    return features


def _road_style_key(road) -> str:
    return f"{road.level.value}:{road.mode}"


def _road_sort_value(road, mean_y: float | None = None) -> float:
    """按实际高度确定道路绘制顺序；道路所属地面、高架或隧道模式不能单独决定交叉处上下关系。"""
    height = float(road.mean_y if mean_y is None else mean_y)
    mode_tie = {
        "tunnel": -1.0,
        "ground": 0.0,
        "elevated": 1.0,
    }.get(road.mode, 0.0)
    return height * 1000.0 + _LEVEL_RANK.get(road.level, 0) + mode_tie


def _points_from_node(road, node_id):
    if road.start_node_id == node_id:
        return tuple(road.points)
    if road.end_node_id == node_id:
        return tuple(reversed(road.points))
    return ()


def _compatible_chain_nodes(scene, road_ids: set[int]) -> dict[int, tuple[int, int]]:
    """查找可合并成一条 MapLibre 描边的二度节点。"""
    result = {}
    for junction in scene.junctions:
        if len(junction.segment_ids) != 2:
            continue
        first = scene.roads_by_id.get(junction.segment_ids[0])
        second = scene.roads_by_id.get(junction.segment_ids[1])
        if first is None or second is None:
            continue
        if first.id not in road_ids or second.id not in road_ids:
            continue
        if (_road_style_key(first) == _road_style_key(second)
                and abs(float(first.width) - float(second.width))
                <= _ROAD_WIDTH_EPSILON):
            result[junction.node_id] = (first.id, second.id)
    return result


def _road_chains(scene, roads=None):
    """跨普通二度节点拼接兼容的源路段。"""
    roads = tuple(scene.roads if roads is None else roads)
    road_ids = {road.id for road in roads}
    connections = _compatible_chain_nodes(scene, road_ids)
    visited: set[int] = set()
    chains = []

    def follow(first_id: int, start_node: int):
        records = []
        points = []
        road_id = first_id
        node_id = start_node
        while road_id not in visited:
            road = scene.roads_by_id.get(road_id)
            if road is None:
                break
            oriented = _points_from_node(road, node_id)
            if len(oriented) < 2:
                break
            visited.add(road_id)
            records.append(road)
            if not points:
                points.extend(oriented)
            elif (abs(float(points[-1].x) - float(oriented[0].x))
                  + abs(float(points[-1].z) - float(oriented[0].z)) <= 1e-6):
                points.extend(oriented[1:])
            else:
                points.extend(oriented)
            exit_node = (road.end_node_id if road.start_node_id == node_id
                         else road.start_node_id)
            pair = connections.get(exit_node)
            if pair is None:
                break
            next_id = pair[1] if pair[0] == road_id else pair[0]
            if next_id in visited:
                break
            road_id, node_id = next_id, exit_node
        if records and len(points) >= 2:
            chains.append((records, tuple(points)))

    # 开放链从不兼容连接的端点开始。
    for road in roads:
        if road.id in visited:
            continue
        start = next((node for node in (road.start_node_id, road.end_node_id)
                      if node not in connections), None)
        if start is not None:
            follow(road.id, start)

    # 余下记录是闭合的兼容环。
    for road in roads:
        if road.id not in visited:
            follow(road.id, road.start_node_id)
    return chains


def _road_chain_feature(records, points, chain_index: int):
    road = records[0]
    total_length = sum(max(float(item.length), 0.0) for item in records)
    if total_length > 0:
        physical_width = sum(
            float(item.width) * float(item.length) for item in records
        ) / total_length
        mean_y = sum(
            float(item.mean_y) * float(item.length) for item in records
        ) / total_length
    else:
        physical_width = float(road.width)
        mean_y = float(road.mean_y)
    road_name = next((
        str(getattr(item.segment, "display_name", "")
            or getattr(item.segment, "custom_name", "") or "").strip()
        for item in records
        if str(getattr(item.segment, "display_name", "")
               or getattr(item.segment, "custom_name", "") or "").strip()
    ), "")
    feature_id = f"road-chain:{chain_index}:{min(item.id for item in records)}"
    return _feature(
        feature_id, _line_geometry(points), roadId=int(road.id),
        segmentCount=len(records), level=road.level.value, mode=road.mode,
        styleKey=_road_style_key(road), physicalWidth=physical_width,
        lengthMeters=total_length, name=road_name,
        meanHeight=mean_y, sortKey=_road_sort_value(road, mean_y),
        geometryRole="road-edge")


def _road_surface_feature(road):
    geometry = _polygon_geometry(road.footprint.points)
    if geometry is None:
        return None
    name = str(
        getattr(road.segment, "display_name", "")
        or getattr(road.segment, "custom_name", "")
        or ""
    ).strip()
    return _feature(
        f"road-surface:{road.id}", geometry, roadId=int(road.id),
        level=road.level.value, mode=road.mode,
        styleKey=_road_style_key(road), physicalWidth=float(road.width),
        lengthMeters=float(road.length), name=name,
        meanHeight=float(road.mean_y),
        sortKey=_road_sort_value(road), geometryRole="road-surface")


def _ordered_road_features(scene, order=None):
    order = build_road_order(scene) if order is None else order
    features = []
    fallback_by_rank = {}
    fallback_ids = set()
    for road in scene.roads:
        pieces = order.pieces[road.id]
        surface = road.footprint is not None and len(road.footprint.points) >= 3
        if not surface and len(pieces) == 1:
            fallback_by_rank.setdefault(pieces[0].rank, []).append(road)
            fallback_ids.add(road.id)
            continue
        for piece in pieces:
            for paint_index, region in enumerate(piece.paint_regions):
                if surface:
                    if not region.rings:
                        continue
                    feature = _road_surface_feature(road)
                    polygons = region.polygons or tuple((ring,) for ring in region.rings)
                    coordinates = [[[world_to_geo(x, z) for x, z in ring]
                                    for ring in polygon] for polygon in polygons]
                    feature['geometry'] = ({'type': 'MultiPolygon', 'coordinates': coordinates}
                        if len(coordinates) > 1 else {'type': 'Polygon', 'coordinates': coordinates[0]})
                    feature['properties'].update(styleKey=_road_style_key(region.road),
                        styleOwnerId=region.road.id)
                    if piece.paints:
                        feature['properties']['sourceStyleKey'] = _road_style_key(road)
                else:
                    feature = _road_chain_feature([road], piece.points, piece.index)
                    feature['properties']['geometryRole'] = 'road-piece'
                feature_id = f'road-fragment:{road.id}:{piece.index}'
                if piece.paints:
                    feature_id += f':paint:{paint_index}'
                feature['id'] = feature_id
                feature['properties'].update(featureId=feature_id, rank=piece.rank,
                                             fragmentIndex=piece.index)
                features.append(feature)
                if surface and region.boundaries:
                    features.append(_feature(
                        feature_id + ':boundary', {
                            'type': 'MultiLineString',
                            'coordinates': [[world_to_geo(x, z) for x, z in line]
                                            for line in region.boundaries],
                        }, **{**feature['properties'],
                              'featureId': feature_id + ':boundary',
                              'geometryRole': 'road-boundary'}))
    for rank, records in sorted(fallback_by_rank.items()):
        for index, (chain, points) in enumerate(_road_chains(scene, records)):
            feature = _road_chain_feature(chain, points, index)
            # 索引只在单个绘制等级内有效，稳定标识必须包含等级。
            feature_id = f"{feature['id']}:rank:{rank}"
            feature['id'] = feature_id
            feature['properties'].update(featureId=feature_id, rank=rank)
            features.append(feature)
    for feature in _legacy_width_transition_features(scene, fallback_ids):
        road_id = feature['properties']['roadId']
        feature['properties']['rank'] = order.pieces[road_id][0].rank
        features.append(feature)
    return features, order.diagnostics


def _legacy_width_transition_features(scene, road_ids=None):
    """将狭窄入射道路延伸到最宽的兼容路口分支。"""
    features = []
    for junction in scene.junctions:
        records = [scene.roads_by_id.get(item) for item in junction.segment_ids]
        if road_ids is not None:
            records = [item for item in records
                       if item is not None and item.id in road_ids]
        compatible = {}
        for record in (item for item in records if item is not None):
            compatible.setdefault(_road_style_key(record), []).append(record)
        for group in compatible.values():
            if len(group) < 2:
                continue
            target_width = max(float(item.width) for item in group)
            for narrow in group:
                if target_width - float(narrow.width) <= _ROAD_WIDTH_EPSILON:
                    continue
                transition = min(
                    max(12.0, min(30.0, 1.5 * target_width)),
                    max(float(narrow.length) * 0.45, 0.0),
                )
                if transition <= 1e-6:
                    continue
                node_at_start = narrow.start_node_id == junction.node_id
                for index in range(_ROAD_TRANSITION_SLICES):
                    near = transition * index / _ROAD_TRANSITION_SLICES
                    far = transition * (index + 1) / _ROAD_TRANSITION_SLICES
                    center_from_node = (near + far) * 0.5
                    center = (center_from_node if node_at_start
                              else float(narrow.length) - center_from_node)
                    points = clip_polyline_span(
                        narrow.points, center, far - near)
                    if len(points) < 2:
                        continue
                    fraction = center_from_node / transition
                    width = target_width + (
                        float(narrow.width) - target_width) * fraction
                    feature_id = (
                        f"road-transition:{junction.node_id}:{narrow.id}:{index}")
                    features.append(_feature(
                        feature_id, _line_geometry(points), roadId=int(narrow.id),
                        nodeId=int(junction.node_id), level=narrow.level.value,
                        mode=narrow.mode, styleKey=_road_style_key(narrow),
                        physicalWidth=width, meanHeight=float(junction.mean_y),
                        sortKey=_road_sort_value(narrow, junction.mean_y),
                        geometryRole="road-transition"))
    return features


def _region_geometry(region, rings):
    coordinates = []
    for index in [region["outer"], *region["holes"]]:
        ring = [world_to_geo(x, z) for x, z in rings[index]]
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        signed = sum(a[0]*b[1] - b[0]*a[1] for a, b in zip(ring, ring[1:]))
        if (signed > 0) != (index == region["outer"]):
            ring.reverse()
        coordinates.append(ring)
    return {"type": "Polygon", "coordinates": coordinates}


def _ring_geometries(rings):
    """复用建成区环判断；凹多边形质心可能落在相邻区域或区域外，用边界顶点判断父环。"""
    import numpy as np
    from .built_area import _poly_area, _point_in_poly
    rings = sorted((np.asarray(r) for r in rings if len(r) >= 3),
                   key=_poly_area, reverse=True)
    parents, depths, regions = [], [], {}
    for i, ring in enumerate(rings):
        x, z = ring[0]
        parent = next((j for j in range(i-1, -1, -1)
                       if _point_in_poly(x, z, rings[j])), -1)
        parents.append(parent)
        depth = depths[parent] + 1 if parent >= 0 else 0
        depths.append(depth)
        if depth % 2 == 0:
            regions[i] = {"outer": i, "holes": []}
        else:
            regions[parent]["holes"].append(i)
    return [_region_geometry(reg, rings) for reg in regions.values()]


def _compile_poi_atlas(root, cfg, keys, *, diagnostics=None):
    from PIL import Image, ImageOps
    from .layers.poi_icon_layer import POIIconLayer
    from .poi_color import dominant_icon_rgb
    layer = POIIconLayer()
    layer._init_icon_tools(cfg)
    layer._load_sprite(cfg)
    hidden_keys = {key for key in keys
                   if (info := layer.icon_map.get_icon(key)) is not None
                   and info.get("hidden", False)}
    if diagnostics is not None:
        diagnostics.setdefault("hidden", []).extend(sorted(hidden_keys))
    keys = sorted(keys - hidden_keys)
    side, columns = 48, 16
    atlas = Image.new("RGBA", (columns*side, max(1, (len(keys)+columns-1)//columns)*side))
    index = {}
    colors = {}
    for n, key in enumerate(keys):
        try:
            icon = layer.icon_map.render_icon(key, layer._sprite_img)
        except FileNotFoundError:
            LOGGER.warning("Missing POI icon asset: %s", key)
            icon = None
        if diagnostics is not None:
            diagnostics.setdefault("missing", []).extend(
                () if icon is not None else (key,))
        if icon is None:
            hidden_keys.add(key)
            continue
        icon = icon.convert("RGBA")
        rgb = dominant_icon_rgb(icon)
        luminance = .299 * rgb[0] + .587 * rgb[1] + .114 * rgb[2]
        if luminance > 200:
            rgb = tuple(max(0, int(value * .7)) for value in rgb)
        colors[key] = "#%02X%02X%02X" % rgb
        icon = ImageOps.contain(icon,
                                (side-4, side-4), Image.Resampling.LANCZOS)
        if not layer.icon_map.get_icon(key).get('borderless', False):
            # 光晕随图稿切换，保持深色图标的灰色外缘。
            icon = icon.convert("RGBA")
            icon_alpha = icon.getchannel("A")
            halo = icon_alpha.filter(ImageFilter.MaxFilter(5))
            halo_rgb = (115, 119, 128) if cfg.get("appearance") == "dark" else (255, 255, 255)
            halo_layer = Image.new("RGBA", icon.size, (*halo_rgb, 220))
            halo_layer.putalpha(halo)
            outlined = Image.new("RGBA", icon.size, (0, 0, 0, 0))
            outlined.alpha_composite(halo_layer)
            outlined.alpha_composite(icon)
            icon = outlined
        x, y = (n % columns)*side, (n // columns)*side
        atlas.paste(icon, (x+(side-icon.width)//2, y+(side-icon.height)//2))
        index[key] = {"x": x, "y": y, "width": side, "height": side, "pixelRatio": 2}
    atlas.save(root / "poi-sprite.png")
    (root / "poi-sprite.json").write_bytes(_json_bytes(index))
    return colors, hidden_keys


def _apply_poi_density(features, cfg):
    """在预览器的整数缩放级别复用正式的两阶段筛选器。"""
    from types import SimpleNamespace
    from .layers.poi_density import select_pois, poi_density_params
    profile = cfg.get("active_profile", {})
    modern = bool(profile.get("view"))
    grid, limit = poi_density_params(profile.get("density_tier", "standard"))
    for feature in features:
        feature["properties"]["visibleZooms"] = []
    for zoom in range(0, 25):
        ppm = 2**zoom / 156543.03392804097
        items = []
        for feature in features:
            props = feature["properties"]
            if (zoom < props["minZoom"]
                    or props.get("iconStatus") in {"missing", "hidden", "orphan"}):
                continue
            lng, lat = feature["geometry"]["coordinates"]
            items.append(SimpleNamespace(
                px=lng*METERS_PER_DEGREE*ppm, py=-lat*METERS_PER_DEGREE*ppm,
                priority=props["priority"], category=props["category"],
                name_source=props.get("nameSource", "fallback").lower(), label=props["name"], icon_id=props["iconKey"],
                source=props["poiId"], feature=feature,
                group_id=props.get("groupId", ""),
                semantic_level=props.get("semanticLevel", "BUILDING")))
        selected = items
        if (profile.get("density_enabled", True) if modern else cfg.get("visual_merge_enabled", True)):
            selected = select_pois(items,
                grid_size=grid if modern else _preview_number(cfg.get("grid_size", 100), 100),
                max_per_cell=limit if modern else int(cfg.get("max_per_cell", 2)),
                distance_px=0 if modern else float(cfg.get("visual_merge_distance", cfg.get("merge_distance", 48)))*ppm,
                pixels_per_world=ppm, overrides=cfg.get("merge_overrides", {}))
        for item in selected:
            item.feature["properties"]["visibleZooms"].append(zoom)


def _building_category(building) -> str:
    text = f"{getattr(building, 'service', '')} {_enum_text(getattr(building, 'role', ''))}".lower()
    for needle, category in (("residential", "residential"),
                             ("commercial", "commercial"),
                             ("industrial", "industrial"),
                             ("office", "office"),
                             ("transport", "publictransport"),
                             ("beautification", "green")):
        if needle in text:
            return category
    return "public"


def _write_geojson(root: Path, name: str, features: list[dict],
                   revision: int, sources: dict) -> None:
    filename = f"{name}.geojson"
    payload = _json_bytes({"type": "FeatureCollection", "features": features})
    (root / filename).write_bytes(payload)
    sources[name] = f"{filename}?revision={revision}"


def _write_distance_texture(root: Path, field, revision: int) -> dict:
    """写出可供 WebGL 线性采样的有符号距离纹理。"""
    step_meters = 1.0
    distance = np.asarray(field.data, dtype=np.float32)
    packed = np.clip(
        np.rint(distance / step_meters) + 32768.0, 0, 65535,
    ).astype(np.uint16)
    encoded = np.zeros((*packed.shape, 3), dtype=np.uint8)
    encoded[..., 0] = (packed >> 8).astype(np.uint8)
    encoded[..., 1] = (packed & 255).astype(np.uint8)
    filename = "built-area-surface.png"
    Image.fromarray(encoded, mode="RGB").save(
        root / filename, format="PNG", optimize=True)
    return {
        "url": f"{filename}?revision={revision}",
        "encoding": "signed-distance-rg16",
        "zero": 32768.0,
        "stepMeters": step_meters,
        "sampling": "linear",
    }


def preview_source_signatures(style, options):
    """只把几何依赖计入数据身份，绘制和可见性变化不重新编译数据。"""
    data = getattr(style, "data", style)
    transit_selection = _preview_transit_selection(style, options)
    built_cfg = data.get("built_area", {})
    if (str(built_cfg.get("backend", "surface")).lower() == "legacy"
            or bool(built_cfg.get("include_parks", False))):
        built = {k: v for k, v in built_cfg.items()
                 if k not in {"color", "opacity", "enabled"}}
        built["webglChaikinMax"] = _BUILT_PREVIEW_CHAIKIN_MAX
    else:
        built = {
            "surfaceSchema": "surface-v1",
            "cellMeters": float(
                built_cfg.get("surface_cell_meters", 8.0) or 8.0),
            "excludeServices": tuple(
                built_cfg.get("exclude_services") or ()),
        }
    poi = {k: v for k, v in data.get("poi_icons", {}).items()
           if k not in {"icon_size", "size", "enabled", "show_name",
                        "visibility_snapshot"}}
    # 图标及名称开关只改变预览展示；源保留所有合格图标，重新启用时无需编译。
    for owner in ("building", "transit"):
        if isinstance(poi.get(owner), dict):
            poi[owner] = {
                key: value for key, value in poi[owner].items()
                if key not in {"show_name", "enabled"}
            }
            if not poi[owner]:
                poi.pop(owner)
    road_geometry = {"chainVersion": _ROAD_CHAIN_VERSION,
                     "orderVersion": ROAD_ORDER_VERSION,
                     "accurateIntersections": bool(data.get("roads", {}).get("accurate_intersections", True)),
                     "widthEpsilon": _ROAD_WIDTH_EPSILON,
                     "transitionSlices": _ROAD_TRANSITION_SLICES}
    water_geometry = {"version": _WATER_PREVIEW_VERSION,
                      "upscale": _WATER_PREVIEW_UPSCALE,
                      "maxGridEdge": _WATER_PREVIEW_MAX_GRID_EDGE,
                      "rdp": _WATER_PREVIEW_RDP,
                      "chaikin": _WATER_PREVIEW_CHAIKIN,
                      "minArea": _WATER_PREVIEW_MIN_AREA}
    dependencies = {
        "terrain": [data.get("terrain", {}), options.get("terrain", {}), options.get("bounds")],
        "roads": road_geometry,
        "builtArea": built,
        "pois": ["line-membership-v3", poi,
                 transit_selection.to_dict(),
                 options.get("poi_transit_types"), options.get("poi_transit_line_ids"),
                 options.get("poi_hidden_categories"),
                 data.get("_poi_mapping_override"),
                 options.get("poi_mapping_revision", 0)],
        # 公共交通视图的图标只来自线路站点；普通地图的 POI 设置不能裁剪或使其失效。
        "transitViewPois": [
            options.get("transit_view_types"), options.get("transit_view_line_ids"),
            (data.get("poi_icons") or {}).get("use_game_names", False),
            (data.get("poi_icons") or {}).get("facility_selection", {}),
            (data.get("poi_icons") or {}).get("facility_hierarchy", {}),
            "line-stop-icons-v4",
            (data.get("poi_icons") or {}).get("transit_profile", {}),
            data.get("_poi_mapping_override"),
            options.get("poi_mapping_revision", 0),
            (data.get("poi_icons") or {}).get("icon_overrides", {}),
            {
                key: (data.get("poi_icons") or {}).get(key)
                for key in (
                    "appearance", "building_merge_enabled", "transit_merge_enabled",
                    "block_gap_m", "hub_core_m", "merge_overrides",
                )
            },
        ],
        "transit": [transit_selection.to_dict(),
                    (data.get("transports") or {}).get("grouping", {})],
        "stations": [transit_selection.to_dict(),
                     options.get("station_merge_enabled", True),
                     options.get("station_merge_range", 48.0),
                     (data.get("transports") or {}).get("station", {})],
        "districts": [
            "facility-union-v1",
            sorted(options.get("district_ids") or []),
            sorted(options.get("park_ids") or []),
            options.get("district_ids") is None,
            options.get("park_ids") is None,
            {key: data.get("district", {}).get(key)
             for key in ("smooth_blur", "smooth_iters", "contour_upscale")},
            {key: data.get("zone", {}).get(key)
             for key in ("smooth_blur", "smooth_iters", "contour_upscale")},
        ],
    }
    dependencies["zones"] = dependencies["districts"]
    poi_icon_colors = {
        key: data.get("poi_icons", {}).get(key)
        for key in ("icons_dir", "appearance", "sprite", "icon_overrides")
        if key in data.get("poi_icons", {})
    }
    poi_icon_colors["mappingOverride"] = data.get("_poi_mapping_override")
    dependencies["buildings"] = ["building-poi-colors-v1", poi_icon_colors, options.get("poi_mapping_revision", 0),
                                 data.get("poi_icons", {}).get("use_game_names", False)]
    dependencies["landUse"] = 2
    for key in ("forest", "railway", "traffic", "proceduralObjects"):
        dependencies[key] = 1
    # labels.geojson 始终携带完整的道路 / 区划 / 区域文字。显示开关由
    # preview_theme 的轻量展示状态控制，不能让开关文字触发场景重编译。
    dependencies["labels"] = {"schema": "native-street-lines-v5-owned-names"}
    dependencies["labelFonts"] = _preview_font_inputs(data)
    dependencies["water"] = [water_geometry, options.get("bounds")]
    return {key: hashlib.sha256(_json_bytes(value)).hexdigest() for key, value in dependencies.items()}


def _preview_font_inputs(style):
    from .fonts import _custom_font_candidates

    data = getattr(style, "data", style) or {}
    labels = data.get("labels", data.get("label", {}))
    result = {}
    for kind in ("road", "district", "park"):
        cfg = labels.get(kind, {})
        family = str(cfg.get("font") or "").strip()
        bold = bool(cfg.get("bold", False))
        candidates = _custom_font_candidates(family, bold) if family else []
        if candidates:
            path = candidates[0]
            info = path.stat()
            result[kind] = {"path": str(path), "bold": bold,
                            "mtime": info.st_mtime_ns, "size": info.st_size}
    return result


def _preview_font_assets(root, inputs):
    # 浏览器字体需要单个 SFNT 字体面，用户提供 TTC 集合时也要提取。
    assets = {}
    files = {}
    for kind, entry in inputs.items():
        path = Path(entry["path"])
        if path not in files:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()[:24]
            filename = f"label-font-{digest}.{'otf' if path.suffix.lower() == '.otf' else 'ttf'}"
            target = root / filename
            if path.suffix.lower() in {".ttc", ".otc"}:
                from fontTools.ttLib import TTFont

                with TTFont(str(path), fontNumber=0) as font:
                    font.save(target)
            else:
                shutil.copyfile(path, target)
            files[path] = (filename, digest)
        filename, digest = files[path]
        weight = "Bold" if entry["bold"] else "Regular"
        assets[kind] = {"family": f"CSLPreview-{digest}-{weight}", "url": filename}
    return assets


@dataclass
class PreviewSceneBundle:
    root: Path
    scene_id: str
    revision: int
    manifest: dict
    _lock: Any = field(default_factory=threading.Lock, repr=False)
    _readers: int = field(default=0, init=False, repr=False)
    _retired: bool = field(default=False, init=False, repr=False)

    def retain(self) -> None:
        with self._lock:
            if self._retired:
                raise RuntimeError("Preview snapshot has already been released")
            self._readers += 1

    def release(self) -> None:
        with self._lock:
            self._readers -= 1
            remove = self._retired and self._readers == 0
        if remove:
            shutil.rmtree(self.root, ignore_errors=True)

    def close(self) -> None:
        with self._lock:
            self._retired = True
            remove = self._readers == 0
        if remove:
            shutil.rmtree(self.root, ignore_errors=True)


class PreviewSceneCompiler:
    """编译不可变且完整的中央预览场景资源包。"""

    @classmethod
    def compile_roads(
        cls, session, style: Any, revision: int,
        *, visible: bool = True, visibility: dict[str, bool] | None = None,
        options: dict[str, Any] | None = None, previous: PreviewSceneBundle | None = None,
    ) -> PreviewSceneBundle:
        """将道路几何和局部上下顺序编译为预览图层数据。"""
        started = time.perf_counter()
        options = {**(options or {}), "style": getattr(style, "data", style)}
        phase = options.get("phase", "full")
        if phase == "base":
            from copy import deepcopy
            options["style"] = deepcopy(options["style"])
            options["style"].setdefault("roads", {})["accurate_intersections"] = False
        report = options.get("progress", lambda _pct, _message: None)
        check_cancel = options.get("check_cancel", lambda: None)
        check_cancel()
        document = session.document
        # 这些字符串会原样出现在状态栏里 —— 只写用户看得懂的动作，
        # 不带 WebGL / 场景 / 编译 / 版本号这类内部词。
        report(3, tr("gui.progress.scene.roads"))
        scene = session.road_scene()
        report(12, tr("gui.progress.scene.data"))
        fingerprint = session.service_cache.get_or_compute("preview_identity", {}, lambda: _document_fingerprint(document))
        check_cancel()
        scene_id = f"{fingerprint}-{id(session):x}"
        root = Path(tempfile.mkdtemp(prefix="csl-preview-scene-"))
        try:
            signatures = preview_source_signatures(options["style"], options)
            if phase == "base":
                signatures = {key: value for key, value in signatures.items() if key in {"roads", "buildings", "landUse"}}
            previous_manifest = previous.manifest if previous else {}
            old_signatures = previous_manifest.get("sourceSignatures", {})
            changed = ({key for key, value in signatures.items()
                        if old_signatures.get(key) != value}
                       if not options.get("force") else set(signatures))
            sources = dict(previous_manifest.get("sources", {}))
            surface_fields = dict(previous_manifest.get("surfaceFields", {}))
            if previous:
                # 不可变文件可链接进下一快照，避免重新复制 GeoJSON。
                for path in previous.root.iterdir():
                    if path.name != "manifest.json":
                        try:
                            os.link(path, root / path.name)
                        except OSError:
                            shutil.copy2(path, root / path.name)
            # 路口由道路描边的圆形端点组合，渐进链接时也必须移除旧版点圆源。
            sources.pop("roadJunctions", None)
            (root / "road-junctions.geojson").unlink(missing_ok=True)
            options["changed"] = changed
            options["terrainCorners"] = previous_manifest.get("terrainCorners")
            options["surfaceFields"] = surface_fields
            for key in changed:
                filename = sources.get(key, "").split("?")[0]
                if filename and (root / filename).exists():
                    (root / filename).unlink()
            if {"pois", "transitViewPois"} & changed:
                for name in ("poi-sprite.png", "poi-sprite.json"):
                    (root / name).unlink(missing_ok=True)
            if "builtArea" in changed:
                (root / "built-area-surface.png").unlink(missing_ok=True)
            if "roads" in changed:
                report(16, tr("gui.progress.scene.draw_roads"))
                accurate = bool(options["style"].get("roads", {}).get("accurate_intersections", True))
                line_features, road_order = _ordered_road_features(scene, session.road_order(accurate=accurate))
                check_cancel()
                options["roadOrder"] = road_order

                _write_geojson(root, "roads", line_features, revision, sources)
            report(26, tr("gui.progress.scene.layers"))
            terrain_corners = cls._compile_domain_sources(
                document, session, root, int(revision), sources, options or {},
                )
            check_cancel()

            fonts = previous_manifest.get("fonts", {})
            if "labelFonts" in changed:
                for path in root.glob("label-font-*"):
                    path.unlink()
                fonts = _preview_font_assets(root, _preview_font_inputs(options["style"]))
            report(91, tr("gui.progress.scene.write"))
            from .export_region import document_bounds
            wx0, wz0, wx1, wz1 = options.get("bounds") or document_bounds(document)
            manifest = {
                "schemaVersion": SCHEMA_VERSION,
                "sceneId": scene_id,
                "revision": int(revision),
                "phase": phase,
                "completion": "base" if phase == "base" else "complete",
                "compileSeconds": time.perf_counter()-started,
                "documentFingerprint": fingerprint,
                "world": {
                    "bounds": [wx0, wz0, wx1, wz1],
                    "geoBounds": [[-wz1 / METERS_PER_DEGREE, wx0 / METERS_PER_DEGREE],
                                  [-wz0 / METERS_PER_DEGREE, wx1 / METERS_PER_DEGREE]],
                    "projection": "csl-local-v1",
                    "orientation": "screen-right=-z;screen-up=+x",
                    "worldToGeo": {"lng": "-z/111195", "lat": "x/111195"},
                    "geoToWorld": {"x": "lat*111195", "z": "-lng*111195"},
                },
                "sources": sources,
                "roadOrder": options.get("roadOrder", previous_manifest.get("roadOrder", {})),
                "sourceSignatures": signatures,
                "sourceRevisions": {key: (int(revision) * 2 + (phase == "full") if "phase" in options else int(revision)) if key in changed else
                    previous_manifest.get("sourceRevisions", {}).get(key, int(revision)) for key in signatures},
                "terrainCorners": terrain_corners,
                "surfaceFields": surface_fields,
                "poiAtlas": {"image": "poi-sprite.png", "index": "poi-sprite.json"},
                "poiIconDiagnostics": options.get("poiIconDiagnostics", {}),
                "layers": {**{key: True for key in (
                    "terrain", "built_area", "poi_area", "land_use", "water",
                    "building", "road", "railway", "traffic",
                    "procedural_objects", "transit", "district", "zone", "poi",
                    "label", "watermark")}, **(visibility or {}), "road": bool(visible)},
                # 此顺序与 PNG 渲染器的标准绘制顺序一致；预览器可拆分图层，但必须保留分组顺序。
                "renderStack": list(_preview_render_stack()),
                "theme": preview_theme(style, options),
                "fonts": fonts,
                "capabilities": {
                    "nativeText": True,
                    "pan": True,
                    "zoom": True,
                    "fitBounds": True,
                    "selection": True,
                    "roadTheme": True,
                    "roadVisibility": True,
                    "fullScene": True,
                    "layerVisibility": True,
                    "liveTheme": True,
                },
            }
            if not (root / "poi-sprite.png").exists():
                Image.new("RGBA", (1, 1)).save(root / "poi-sprite.png")
                (root / "poi-sprite.json").write_text("{}", encoding="utf-8")
            (root / "manifest.json").write_bytes(_json_bytes(manifest))
            report(94, tr("gui.progress.scene.ready"))
            return PreviewSceneBundle(root, scene_id, int(revision), manifest)
        except Exception:
            shutil.rmtree(root, ignore_errors=True)
            raise

    @classmethod
    def compile_full(cls, session, style: Any, revision: int,
                     *, visibility: dict[str, bool] | None = None,
                     options: dict[str, Any] | None = None, previous: PreviewSceneBundle | None = None,
                     ) -> PreviewSceneBundle:
        """完整场景的公开入口，以道路几何作为基础。"""
        layer_state = dict(visibility or {})
        return cls.compile_roads(
            session, style, revision,
            visible=layer_state.get("road", True), visibility=layer_state,
            options=options, previous=previous)

    @staticmethod
    def _compile_domain_sources(document, session, root: Path, revision: int,
                                sources: dict, options: dict[str, Any]) -> list[list[float]] | None:
        terrain_corners = options.get("terrainCorners")
        from .preview_terrain import raster_session_for_bounds
        raster_session = raster_session_for_bounds(session, options.get("bounds"))
        from .building_names import BuildingNames
        building_names = BuildingNames(document.buildings, options["style"].get(
            "poi_icons", {}).get("use_game_names", False))
        report = options.get("progress", lambda _pct, _message: None)
        domain_order = [
            ("terrain", "地形纹理"), ("buildings", "建筑与用地"),
            ("builtArea", "建成区"), ("forest", "森林"),
            ("water", "平滑水域"), ("railway", "铁路"),
            ("transit", "公共交通"), ("stations", "站点"),
            ("traffic", "交通状态"), ("districts", "区划"),
            ("pois", "兴趣点"),
            ("labels", "文字标注"),
            ("proceduralObjects", "程序化物体"),
        ]
        active_domains = [(key, label) for key, label in domain_order
                          if key in options["changed"]]
        domain_index = {key: index for index, (key, _label)
                        in enumerate(active_domains)}

        def facility_catalog_for_style():
            """返回预览各领域共享的单一语义目录。"""
            cached = options.get("_facility_catalog")
            if cached is not None:
                return cached
            cached = session.facility_catalog()
            options["_facility_catalog"] = cached
            return cached

        def building_semantics(building, catalog):
            group = (catalog.group_for_building(int(building.id))
                     if catalog else None)
            if group is None:
                return ("BUILDING", "", "",
                        None if catalog is None else catalog.building_zone_id.get(
                            int(building.id)), 0, 0)
            level = _enum_text(group.semantic_level)
            parent = (group.id if level == "FACILITY"
                      else group.parent_group_id)
            return (level, str(group.id), str(parent or ""),
                    group.source_zone_id, len(group.member_building_ids),
                    len(group.root_building_ids))

        def start_domain(key: str, label: str) -> None:
            options.get("check_cancel", lambda: None)()
            index = domain_index.get(key, 0)
            total = max(len(active_domains), 1)
            report(28 + 64 * index / total, tr("gui.progress.scene.draw_layer", label=label))

        if "terrain" in options["changed"]:
            start_domain("terrain", "地形纹理")
            from .preview_terrain import PreviewTerrainCompiler
            terrain_corners = PreviewTerrainCompiler.compile(
                raster_session, options["style"], options.get("terrain", {}), root,
                bounds=options.get("bounds"))
            if terrain_corners:
                sources["terrain"] = f"terrain.png?revision={revision}"
        if "buildings" in options["changed"]:
            start_domain("buildings", "建筑与用地")
            facility_catalog = facility_catalog_for_style()
            from .services.poi import facility_container_building_ids
            facility_container_ids = facility_container_building_ids(document)
            from .poi_color import POIColorResolver
            color_resolver = POIColorResolver(
                {**(options["style"].get("poi_icons", {}) or {}),
                 "_poi_mapping_override": options["style"].get("_poi_mapping_override")})
            buildings = []
            land_use = []
            for item in document.buildings:
                if int(item.id) in facility_container_ids:
                    continue
                geometry = _polygon_geometry(item.footprint.points)
                if geometry is None:
                    continue
                category = _building_category(item)
                selected_name = building_names.building(item)
                name = selected_name.text
                (semantic_level, group_id, parent_poi_id, area_id,
                 group_member_count, group_root_count) = (
                    building_semantics(item, facility_catalog))
                resolved_color = (color_resolver.building_color(item, document.metadata.origin.game.lower())
                                  if color_resolver is not None else None)
                poi_color = ("#%02X%02X%02X" % tuple(resolved_color[1])
                             if resolved_color is not None else None)
                buildings.append(_feature(
                    f"building:{item.id}", geometry, buildingId=int(item.id),
                    category=category, role=_enum_text(item.role), name=name,
                    nameSource=selected_name.source, categoryId=item.category_id,
                    categoryName=item.category_name, categoryLocale=item.category_locale,
                    poiColor=poi_color,
                    stationId=item.station_id, semanticLevel=semantic_level,
                    groupId=group_id, parentPoiId=parent_poi_id,
                    areaId=area_id, groupMemberCount=group_member_count,
                    groupRootCount=group_root_count,
                    geometryRole="building"))
                land_use.append(_feature(
                    f"land-use:{item.id}", geometry, category=category,
                    geometryRole="land-use"))
            _write_geojson(root, "buildings", buildings, revision, sources)
            _write_geojson(root, "landUse", land_use, revision, sources)

        if "builtArea" in options["changed"]:
            start_domain("builtArea", "建成区")
            cfg = options["style"].get("built_area", {})
            use_surface = (
                str(cfg.get("backend", "surface")).lower() != "legacy"
                and not bool(cfg.get("include_parks", False))
            )
            if use_surface:
                from .built_area import DEFAULT_EXCLUDE_SERVICES
                model = session.surface_model({
                    "include_water": False,
                    "include_districts": False,
                    "cell_meters": float(
                        cfg.get("surface_cell_meters", 8.0) or 8.0),
                    "exclude_services": tuple(
                        cfg.get("exclude_services") or DEFAULT_EXCLUDE_SERVICES),
                })
                if model.built_area_distance is not None:
                    descriptor = _write_distance_texture(
                        root, model.built_area_distance, revision)
                    descriptor.update({
                        "cacheKey": model.cache_key,
                        "schemaVersion": model.schema_version,
                        "worldBounds": [-WORLD_HALF, -WORLD_HALF,
                                        WORLD_HALF, WORLD_HALF],
                    })
                    options["surfaceFields"]["builtArea"] = descriptor
                    sources.pop("builtArea", None)
                else:
                    options["surfaceFields"].pop("builtArea", None)
                    _write_geojson(root, "builtArea", [], revision, sources)
            else:
                from .built_area import _poly_area, compute_built_area_rings
                from .layers.built_area_layer import _chaikin
                _regions, rings = compute_built_area_rings(
                    document, **{key: cfg[key] for key in (
                        "cell_meters", "buffer_meters", "exclude_services",
                        "include_parks", "contour_upscale", "smooth_blur")
                        if key in cfg})
                smooth_iters = min(
                    _BUILT_PREVIEW_CHAIKIN_MAX,
                    max(0, int(cfg.get("smooth_iters", 4) or 0)))
                rings = [smooth for ring in rings
                         for smooth in (_chaikin(ring, smooth_iters),)
                         if len(smooth) >= 3 and _poly_area(smooth) > 1e-6]
                built = [_feature(
                    f"built:{i}", geometry, geometryRole="built-area")
                    for i, geometry in enumerate(_ring_geometries(rings))]
                _write_geojson(root, "builtArea", built, revision, sources)
                options["surfaceFields"].pop("builtArea", None)

        if "forest" in options["changed"]:
            start_domain("forest", "森林")
            forest_cells: set[tuple[int, int]] = set()
            for forest in document.forests:
                values = forest.data
                for index in range(0, len(values) - 1, 2):
                    forest_cells.add((int(values[index] // 64),
                                      int(values[index + 1] // 64)))
            _write_geojson(root, "forest", [_feature(
                f"forest:{gx}:{gz}", {"type": "Point",
                    "coordinates": world_to_geo(gx * 64 + 32, gz * 64 + 32)},
                geometryRole="forest") for gx, gz in sorted(forest_cells)],
                revision, sources)

        if "water" in options["changed"]:
            start_domain("water", "平滑水域")
            from .built_area import _poly_area
            from .layers.water_layer import _chaikin, _rdp
            water_upscale = _preview_water_upscale(document)
            # 简化距离与面积跟随采样倍数换算，保持原世界单位阈值。
            grid_scale = water_upscale / _WATER_PREVIEW_UPSCALE
            water = raster_session.water_geometry({
                "upscale": water_upscale,
                "min_area": _WATER_PREVIEW_MIN_AREA * grid_scale ** 2,
            })
            if water is None:
                rings = [[(p.x, p.z) for p in body.polygon.points]
                         for body in getattr(document.derived, "water_bodies", ())]
            else:
                rings = []
                for polygon in water.polygons:
                    smooth = _chaikin(
                        _rdp(polygon, _WATER_PREVIEW_RDP * grid_scale),
                        iterations=_WATER_PREVIEW_CHAIKIN,
                    )
                    if len(smooth) < 3 or _poly_area(smooth) <= 1e-6:
                        continue
                    rings.append([
                        (water.origin_x
                         + (float(x) + .5) / water.upscale * water.cell,
                         water.origin_z
                         + (float(z) + .5) / water.upscale * water.cell)
                        for x, z in smooth
                    ])
            water_features = [_feature(f"water:{i}", geometry, geometryRole="water")
                              for i, geometry in enumerate(_ring_geometries(rings))]
            _write_geojson(root, "water", water_features, revision, sources)

        if "railway" in options["changed"]:
            start_domain("railway", "铁路")
            from .layers.railway_layer import RailwayLayer
            tracks = []
            compiler = RailwayLayer()
            compiler._lvl = 0
            chains = compiler._build_chains(document)
            for kind, items in chains.items():
                for index, (points, station) in enumerate(items):
                    geometry = _line_geometry(points)
                    if geometry:
                        tracks.append(_feature(
                            f"track:{kind}:{index}", geometry,
                            railType=kind, station=bool(station),
                            geometryRole="track"))
            _write_geojson(root, "railway", tracks, revision, sources)

        if "transit" in options["changed"]:
            start_domain("transit", "公共交通")
            from .services.transit.geometry import resolve_route_geometry
            from .transit import line_display_color

            mapdata = session._legacy_view()
            transit = []
            geometry_cache = {}
            model = _preview_transit_model(
                mapdata, options["style"], options, session=session)
            raw_by_id = model.source_lines
            stops_by_line = {}
            for route in model.routes:
                stops_by_line[route.line_id] = max(
                    stops_by_line.get(route.line_id, 0), len(route.stop_ids))
            for line in model.lines:
                source_line = raw_by_id.get(int(line.id))
                if source_line is None:
                    continue
                kind = line.kind
                resolved = resolve_route_geometry(
                    mapdata, source_line, kind, cache=geometry_cache)
                parts = [part for part in resolved.parts or (resolved.points,) if len(part) >= 2]
                coordinates = [[world_to_geo(point.x, point.z) for point in part] for part in parts]
                if not coordinates:
                    continue
                rgb = line_display_color(source_line)
                color = ("#%02X%02X%02X" % tuple(rgb)
                         if rgb else "#4277C9")
                line_id = int(line.id)
                route_length = sum(
                    ((float(a.x) - float(b.x)) ** 2
                     + (float(a.z) - float(b.z)) ** 2) ** .5
                    for part in parts for a, b in zip(part, part[1:]))
                transit.append(_feature(
                    f"route:{line_id}",
                    ({"type": "LineString", "coordinates": coordinates[0]} if len(parts) == 1
                     else {"type": "MultiLineString", "coordinates": coordinates}),
                    routeId=line_id, lineId=line_id,
                    groupId=(model.group_of.get(line_id).id
                             if model.group_of.get(line_id) is not None else None),
                    name=str(line.name or ""),
                    kind=_enum_text(kind).lower(), pathKind=_enum_text(resolved.kind),
                    color=color,
                    stopCount=stops_by_line.get(line_id, 0),
                    lengthMeters=route_length,
                    geometryRole="transit-line"))
            _write_geojson(root, "transit", transit, revision, sources)

        if "stations" in options["changed"]:
            start_domain("stations", "站点")
            stations = []
            mapdata = session._legacy_view()
            model = _preview_transit_model(
                mapdata, options["style"], options, session=session)
            for item in model.stations:
                serving = {int(value) for value in getattr(
                    item, "serving_line_ids", ())}
                position = item.position
                if position is None:
                    continue
                name_obj = getattr(item, "name", "")
                name = str(getattr(name_obj, "text", name_obj) or "").strip()
                stations.append(_feature(
                    f"station:{item.id}", {"type": "Point",
                        "coordinates": world_to_geo(position.x, position.z)},
                    stationId=str(item.id), name=name,
                    kind=_enum_text(getattr(item, "kind", "station")).lower(),
                    servingLineIds=sorted(serving),
                    servingGroupIds=sorted(int(value) for value in getattr(
                        item, "serving_group_ids", ())),
                    servingLineCount=len(serving),
                    nameSource=str(getattr(name_obj, "source", "") or ""),
                    priority=len(serving),
                    geometryRole="station"))
            _write_geojson(root, "stations", stations, revision, sources)

        if "traffic" in options["changed"]:
            start_domain("traffic", "交通状态")
            traffic = []
            try:
                snapshot = session.traffic_snapshot()
                if snapshot is not None:
                    for item in snapshot.segments:
                        traffic.extend(_traffic_features(item))
                    for item in snapshot.lights:
                        traffic.append(_feature(
                            f"traffic-light:{item.node_id}", {"type": "Point",
                                "coordinates": world_to_geo(item.position.x, item.position.z)},
                            nodeId=int(item.node_id), active=bool(item.active),
                            geometryRole="traffic-light"))
            except Exception:
                # 交通数据是可选图层，坏了不该拖垮整张预览，但也不能再静默吞掉。
                LOGGER.warning("生成交通状态图层失败", exc_info=True)
            _write_geojson(root, "traffic", traffic, revision, sources)

        if "districts" in options["changed"]:
            start_domain("districts", "区划")
            district_features, zone_features = [], []
            district_ids = options.get("district_ids")
            park_ids = options.get("park_ids")
            from .layers.district_layer import (
                DistrictLayer, visible_administrative_districts,
            )
            from .layers.zone_layer import ZoneLayer

            administrative = [
                item for item in visible_administrative_districts(document)
                if district_ids is None or int(item.id) in district_ids
            ]
            functional = [
                item for item in document.districts
                if item.kind == "PARK_ZONE"
                and str(getattr(item, "type_label", "") or "").strip().lower()
                not in ("", "none", "0")
                and (park_ids is None or int(item.id) in park_ids)
            ]
            style = options["style"]
            district_contours = _grid_contour_geometries(
                administrative,
                float(getattr(document, "district_cell", 0.0) or 0.0),
                DistrictLayer(), style.get("district", {}))
            zone_contours = _grid_contour_geometries(
                functional,
                float(getattr(document, "park_cell", 0.0) or 0.0),
                ZoneLayer(), style.get("zone", {}))
            from .zone_geometry import functional_zone_geometries
            for item, polygons, source_ids in functional_zone_geometries(functional):
                coordinates = [[
                    [world_to_geo(x, z) for x, z in ring] for ring in rings
                ] for rings in polygons]
                geometry = ({"type": "Polygon", "coordinates": coordinates[0]}
                            if len(coordinates) == 1 else
                            {"type": "MultiPolygon", "coordinates": coordinates})
                zone_features.append(_feature(
                    f"area:{item.id}", geometry, areaId=int(item.id),
                    sourceAreaIds=source_ids, name=item.name.text, kind=item.kind,
                    zoneKind=_enum_text(item.zone_kind), geometryRole="area"))
            for item in (*administrative, *(item for item in functional if item.grid_points)):
                target = zone_features if item.kind == "PARK_ZONE" else district_features
                contours = zone_contours if item.kind == "PARK_ZONE" else district_contours
                geometries = list(contours.get(int(item.id), ()))
                # DistrictGrid 和 ParkGrid 是正式渲染器使用的权威几何；只有无法生成有效网格时才使用旧边界。
                if not geometries and item.boundary:
                    geometry = _polygon_geometry(item.boundary.points)
                    if geometry:
                        geometries.append(geometry["coordinates"])
                if geometries:
                    geometry = ({"type": "Polygon", "coordinates": geometries[0]}
                                if len(geometries) == 1 else
                                {"type": "MultiPolygon", "coordinates": geometries})
                    target.append(_feature(
                        f"area:{item.id}", geometry, areaId=int(item.id),
                        name=item.name.text, kind=item.kind,
                        zoneKind=_enum_text(item.zone_kind), geometryRole="area"))
            _write_geojson(root, "districts", district_features, revision, sources)
            _write_geojson(root, "zones", zone_features, revision, sources)

        if {"pois", "transitViewPois"} & options["changed"]:
            start_domain("pois", "兴趣点")
            from dataclasses import asdict, replace
            from .services.poi import POIJobParams
            cfg = options["style"].get("poi_icons", {})
            from .building_names import BuildingNames
            names = BuildingNames(document.buildings, cfg.get("use_game_names", False))
            visibility_snapshot = cfg.get("visibility_snapshot")
            has_visibility_snapshot = isinstance(visibility_snapshot, dict)
            active_profile = cfg.get("active_profile", {})
            modern_profile = isinstance(active_profile, dict) and bool(active_profile)
            hidden_categories = {
                str(value) for value in cfg.get("hidden_categories", ())
            }
            params = POIJobParams.from_style(options["style"])
            selection = _preview_transit_selection(options["style"], options)
            params = replace(
                params,
                transit_types=options.get("poi_transit_types", selection.types),
                transit_line_ids=options.get("poi_transit_line_ids", selection.line_ids),
            )
            # 名字完整进入 POI 数据，建筑 / 交通文字开关由 Viewer 即时控制。
            # 两个细分筛选（玩家命名设施 / 重要公共设施）仍只作用于导出 PNG。
            poi_catalog = session.poi_catalog(
                asdict(params),
                override_path=options["style"].get("_poi_mapping_override"),
            )
            # 清除普通地图筛选时保留层级和配置设置。
            transit_view_params = replace(
                params, scope="transit", min_priority=0, categories=(),
                active_profile=cfg.get("transit_profile", active_profile),
                transit_types=options.get("transit_view_types"), transit_line_ids=options.get("transit_view_line_ids"))
            transit_view_catalog = session.poi_catalog(
                asdict(transit_view_params),
                override_path=options["style"].get("_poi_mapping_override"),
            )
            from .services.poi.visibility import visible_pois, icon_visibility_dependencies
            from .services.poi.ownership import poi_group_for, poi_visibility_group_for, sublayer_for
            poi_items = visible_pois(
                poi_catalog, cfg, visibility_snapshot=visibility_snapshot,
                include_hidden_icons=True)
            normal_visibility_dependencies = icon_visibility_dependencies(poi_catalog)
            transit_visibility_dependencies = icon_visibility_dependencies(transit_view_catalog)
            catalog_kinds = set(getattr(poi_catalog, "kinds", ()))
            group_by_id = {
                str(group.id): group
                for group in getattr(poi_catalog, "facility_groups", ())
            }
            pois = []
            transit_view_pois = []
            icon_diagnostics = {"hidden": [], "missing": [], "orphan": []}
            for item in poi_items:
                if not modern_profile and item.priority < params.min_priority:
                    continue
                group_name = poi_group_for(item)
                owner = sublayer_for(item)
                if not (has_visibility_snapshot or modern_profile) and params.categories and item.category not in params.categories:
                    continue
                if not has_visibility_snapshot and item.category in hidden_categories:
                    continue
                selected_name = names.poi(item, group_by_id)
                display_name = selected_name.text
                group = group_by_id.get(str(getattr(item, "group_id", "") or ""))
                pois.append(_feature(
                    f"poi:{item.id}", {"type": "Point", "coordinates": world_to_geo(item.anchor.x, item.anchor.z)},
                    poiId=str(item.id), category=item.category, kind=item.kind,
                    servingLineIds=list(item.serving_line_ids), lineRelated=item.source.kind == "stop" or bool(item.serving_line_ids),
                    poiGroup=group_name, visibilityGroup=poi_visibility_group_for(item),
                    visibilityGroups=normal_visibility_dependencies[str(item.id)],
                    iconKey=item.icon_key, name=display_name,
                    semanticLevel=str(getattr(item, "semantic_level", "BUILDING")),
                    groupId=str(getattr(item, "group_id", "") or ""),
                    parentPoiId=str(getattr(item, "parent_poi_id", "") or ""),
                    areaId=(getattr(item, "area_id", None)
                            if getattr(item, "area_id", None) is not None
                            else (None if group is None else group.source_zone_id)),
                    groupMemberCount=(0 if group is None
                                      else len(group.member_building_ids)),
                    groupRootCount=(0 if group is None
                                    else len(group.root_building_ids)),
                    # 地图类型自己的 no_names（红绿灯、出入口）仍不能画名字；
                    # 建筑 / 交通总开关由 Viewer 处理。
                    drawName=bool(item.draw_name or selected_name.source == "category"),
                    priority=int(item.priority),
                    # 独立交通设施保持可见，子对象沿用所属设施的细节阈值。
                    minZoom=(0. if owner == "transit" and not item.parent_poi_id else max(
                        8., log2(max(float(item.min_zoom), 1e-6)
                                 * 4096 / 17280 * 156543.03392804097))),
                    owner=owner,
                    nameSource=selected_name.source,
                    iconStatus=("orphan" if item.kind not in catalog_kinds
                                else "pending"),
                    geometryRole="poi"))
            # 交通视图标注沿用普通地图 POI 的名称和 drawName 协议，可见性由预览器单独控制。
            transit_view_kinds = set(getattr(transit_view_catalog, "kinds", ()))
            transit_visibility_cfg = {
                "facility_hierarchy": cfg.get("facility_hierarchy", {}),
                "facility_selection": cfg.get("facility_selection", {}),
                "active_profile": cfg.get("transit_profile", active_profile),
            }
            transit_group_by_id = {
                str(value.id): value
                for value in getattr(transit_view_catalog, "facility_groups", ())
            }
            for item in visible_pois(
                    transit_view_catalog, transit_visibility_cfg,
                    visibility_snapshot=visibility_snapshot, include_hidden_icons=True):
                selected_name = names.poi(item, group_by_id)
                display_name = selected_name.text
                group_name = poi_group_for(item)
                # 源保留完整交通目录；各设施的展示距离由预览器按缩放应用，避免全城视图过密。
                transit_min_zoom = max(
                    8., log2(max(float(item.min_zoom), 1e-6)
                             * 4096 / 17280 * 156543.03392804097))
                group = transit_group_by_id.get(str(getattr(item, "group_id", "") or ""))
                transit_view_pois.append(_feature(
                    f"transit-view-poi:{item.id}",
                    {"type": "Point", "coordinates": world_to_geo(
                        item.anchor.x, item.anchor.z)},
                    poiId=str(item.id), category=item.category, kind=item.kind,
                    servingLineIds=list(item.serving_line_ids), lineRelated=item.source.kind == "stop" or bool(item.serving_line_ids),
                    poiGroup=group_name, visibilityGroup=poi_visibility_group_for(item),
                    visibilityGroups=transit_visibility_dependencies[str(item.id)],
                    iconKey=item.icon_key, name=display_name,
                    semanticLevel=str(getattr(item, "semantic_level", "BUILDING")),
                    groupId=str(getattr(item, "group_id", "") or ""),
                    parentPoiId=str(getattr(item, "parent_poi_id", "") or ""),
                    areaId=(getattr(item, "area_id", None)
                            if getattr(item, "area_id", None) is not None
                            else (None if group is None else group.source_zone_id)),
                    groupMemberCount=(0 if group is None
                                      else len(group.member_building_ids)),
                    groupRootCount=(0 if group is None
                                    else len(group.root_building_ids)),
                    drawName=bool(item.draw_name or selected_name.source == "category"),
                    priority=max(1000, int(item.priority)),
                    transitPriority=int(item.priority),
                    minZoom=transit_min_zoom,
                    owner="transit", nameSource=selected_name.source,
                    iconStatus=("orphan" if item.kind not in transit_view_kinds
                                else "pending"),
                    geometryRole="poi"))
            icon_colors, hidden_icon_keys = _compile_poi_atlas(
                root, {**cfg, "_poi_mapping_override": options["style"].get("_poi_mapping_override")}, {p["properties"]["iconKey"] for p in (
                    pois + transit_view_pois)},
                diagnostics=icon_diagnostics)
            pois = [p for p in pois
                    if p["properties"]["visibilityGroup"] == "area"
                    or p["properties"]["iconKey"] not in hidden_icon_keys]
            transit_view_pois = [p for p in transit_view_pois
                                 if p["properties"]["iconKey"] not in hidden_icon_keys]
            for feature in pois + transit_view_pois:
                props = feature["properties"]
                if props.get("iconStatus") == "orphan":
                    icon_diagnostics["orphan"].append(props["poiId"])
                else:
                    props["iconStatus"] = (
                        "missing" if props["iconKey"] in icon_diagnostics["missing"]
                        else ("hidden" if props["iconKey"] in hidden_icon_keys else "visible")
                    )
            for key in icon_diagnostics:
                icon_diagnostics[key] = sorted(
                    set(str(value) for value in icon_diagnostics[key]))
            options["poiIconDiagnostics"] = icon_diagnostics
            _apply_poi_density(pois, cfg)
            for feature in pois:
                if feature["properties"].get("owner") == "transit":
                    feature["properties"]["priority"] = max(
                        1000, int(feature["properties"].get("priority", 0)))
                feature["properties"]["labelColor"] = icon_colors.get(
                    feature["properties"]["iconKey"], "#505564")
            _write_geojson(root, "pois", pois, revision, sources)
            _write_geojson(
                root, "transitViewPois", transit_view_pois, revision, sources)

        if "labels" in options["changed"]:
            start_domain("labels", "文字标注")
            # 通用标注只保留道路和行政区，功能区名称由 POI 负责。
            generic_label_types = {"DISTRICT_NAME"}
            background_labels = {("DISTRICT_NAME", item.id)
                                 for item in document.districts if item.is_background}
            labels = []
            label_keys: set[tuple[str, str]] = set()
            for item in getattr(document.derived, "labels", ()):
                if item.position.kind != "POINT" or item.position.x is None:
                    continue
                label_type = _enum_text(item.type).upper()
                if (label_type, item.ref_id) in background_labels:
                    continue
                source_type = _enum_text(item.source).upper()
                if label_type not in generic_label_types:
                    continue
                scope = "district"
                labels.append(_feature(
                    f"label:{item.id}", {"type": "Point", "coordinates": world_to_geo(
                        item.position.x, item.position.z)}, text=item.text,
                    labelType="district", priority=int(item.priority),
                    labelScope=scope, nameSource=source_type.lower(),
                    geometryRole="label"))
                label_keys.add((scope, item.text))
            for item in document.districts:
                if item.is_background:
                    continue
                point = item.position
                if point is None or not item.name.text:
                    continue
                if str(item.kind) == "PARK_ZONE":
                    continue
                scope = "district"
                key = (scope, item.name.text)
                if key not in label_keys:
                    labels.append(_feature(
                        f"label:district:{item.id}", {"type": "Point",
                        "coordinates": world_to_geo(point.x, point.z)},
                        text=item.name.text, labelType="district", priority=90,
                        labelScope=scope,
                        geometryRole="label"))
                    label_keys.add(key)
            from .labeling.road_paths import street_chains
            road_ids = {item.id for item in document.networks if item.is_road}
            for chain in street_chains(session._legacy_view(), road_ids=road_ids):
                coordinates = []
                for point in chain.points:
                    coordinate = world_to_geo(point.x, point.z)
                    if not coordinates or coordinate != coordinates[-1]:
                        coordinates.append(coordinate)
                if len(coordinates) < 2:
                    continue
                rank = _LEVEL_RANK[RoadLevel(chain.level)]
                labels.append(_feature(
                    f"label:road:{chain.id}", {"type": "LineString", "coordinates": coordinates},
                    text=chain.text, labelType="road", priority=35 + rank,
                    labelScope="road", roadLevel=chain.level,
                    nameSource=chain.source.lower(), geometryRole="label"))
            _write_geojson(root, "labels", labels, revision, sources)

        if "proceduralObjects" in options["changed"]:
            start_domain("proceduralObjects", "程序化物体")
            objects = []
            for item in document.procedural_objects:
                angle = float(item.rotation_y)
                cx, cz = item.center_x, item.center_z
                ex, ez = item.extent_x, item.extent_z
                ring = []
                for dx, dz in ((-ex, -ez), (ex, -ez), (ex, ez), (-ex, ez), (-ex, -ez)):
                    x = cx + dx*cos(angle) + dz*sin(angle)
                    z = cz - dx*sin(angle) + dz*cos(angle)
                    ring.append(world_to_geo(x, z))
                objects.append(_feature(
                    f"po:{item.id}", {"type": "Polygon", "coordinates": [ring]},
                    objectId=int(item.id), name=item.prefab_name,
                    geometryRole="procedural-object"))
            _write_geojson(root, "proceduralObjects", objects, revision, sources)
        return terrain_corners


class _PreviewHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address, owner):
        self.owner = owner
        super().__init__(address, _PreviewRequestHandler)


class _PreviewRequestHandler(BaseHTTPRequestHandler):
    server_version = "CSLPreview/1"

    def do_GET(self) -> None:  # noqa: N802
        """仅在本地资源服务中返回当前场景及预览器资源。"""
        owner = self.server.owner
        parts = [unquote(item) for item in urlsplit(self.path).path.split("/")
                 if item]
        if not parts or parts[0] != owner.token:
            self.send_error(404)
            return
        if len(parts) == 1:
            parts.extend(("viewer", "index.html"))
        if parts[1] == "viewer":
            relative = parts[2:] or ["index.html"]
            root = owner.viewer_dir
        elif parts[1] == "scene":
            relative = parts[3:] or ["manifest.json"]
            with owner._lock:
                bundle = owner._bundles.get(parts[2]) if len(parts) > 2 else None
                root = bundle.root if bundle is not None else None
        else:
            root = None
            relative = []
        if root is None:
            self.send_error(404)
            return
        try:
            target = (Path(root) / Path(*relative)).resolve()
            target.relative_to(Path(root).resolve())
        except (OSError, ValueError):
            self.send_error(404)
            return
        if not target.is_file():
            self.send_error(404)
            return
        digest = hashlib.sha256()
        with target.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        etag = '"' + digest.hexdigest()[:24] + '"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.end_headers()
            return
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix in (".json", ".geojson"):
            mime = "application/geo+json; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(target.stat().st_size))
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", "no-store" if target.name == "manifest.json"
                         else "private, max-age=31536000, immutable")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        with target.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                self.wfile.write(chunk)

    def log_message(self, _format: str, *_args) -> None:
        return


class PreviewSceneServer:
    """在本地回环地址提供预览器资源和当前不可变场景包。"""

    def __init__(self, viewer_dir: str | os.PathLike[str]):
        self.viewer_dir = Path(viewer_dir).resolve()
        self.token = secrets.token_urlsafe(24)
        self._bundle: PreviewSceneBundle | None = None
        self._server: _PreviewHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._bundles: dict[str, PreviewSceneBundle] = {}

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("preview scene server is not running")
        return f"http://127.0.0.1:{self._server.server_address[1]}/{self.token}"

    @property
    def viewer_url(self) -> str:
        return self.base_url + "/viewer/index.html"

    @property
    def manifest_url(self) -> str:
        with self._lock:
            if self._bundle is None:
                raise RuntimeError("No preview scene")
            return self.base_url + "/scene/" + self._bundle.root.name + "/manifest.json"

    def start(self) -> str:
        if not (self.viewer_dir / "index.html").is_file():
            raise FileNotFoundError(
                f"Viewer 构建产物不存在：{self.viewer_dir}；请先运行 "
                "python tools/renderer/build_preview_viewer.py")
        if self._server is None:
            self._server = _PreviewHTTPServer(("127.0.0.1", 0), self)
            self._thread = threading.Thread(
                target=self._server.serve_forever,
                name="preview-scene-http", daemon=True,
            )
            self._thread.start()
        return self.viewer_url

    def replace_bundle(self, bundle: PreviewSceneBundle) -> None:
        with self._lock:
            self._bundle = bundle
            # 当前预览器确认展示后才清理旧版本。
            self._bundles[bundle.root.name] = bundle

    def acknowledge_bundle(self, name: str) -> bool:
        with self._lock:
            if self._bundle is None or name != self._bundle.root.name:
                return False
            retired = [bundle for key, bundle in self._bundles.items() if key != name]
            self._bundles = {name: self._bundle}
        for bundle in retired:
            bundle.close()
        return True


    def close(self) -> None:
        server = self._server
        self._server = None
        if server is not None:
            server.shutdown()
            server.server_close()
        with self._lock:
            bundle = self._bundle
            self._bundle = None
            retired = list(self._bundles.values())
            self._bundles.clear()
        if bundle is not None:
            bundle.close()
        for item in retired:
            item.close()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
