"""独立进程渲染入口，向宿主返回进度与结果。"""
from __future__ import annotations

import copy
import time
import traceback
from typing import Any, Callable

from PIL import Image
from csmodernmap import transit
from csmodernmap.services.poi.settings import apply_merge_settings

from csmodernmap._paths import resource_dir
from csmodernmap.i18n import detect_lang, set_lang, tr
from csmodernmap.labeling import visibility_overrides
from csmodernmap.poi_profiles import normalize_poi_visibility_snapshot
from csmodernmap.layers.built_area_layer import BuiltAreaLayer
from csmodernmap.layers.district_layer import (
    DistrictLayer, visible_administrative_districts,
)
from csmodernmap.layers.label_layer import LabelLayer
from csmodernmap.layers.poi_icon_layer import POIIconLayer
from csmodernmap.layers.poi_density import poi_density_params
from csmodernmap.layers.registry import layer_directory
from csmodernmap.layers.transport_layer import TransportLayer
from csmodernmap.layers.water_layer import WaterLayer
from csmodernmap.layers.watermark_layer import WatermarkLayer
from csmodernmap.layers.zone_layer import ZoneLayer
from csmodernmap.metro_features import (
    metro_content_canvas_size,
    recommended_metro_output_size,
)
from csmodernmap.cmm import load_document
from csmodernmap.render_jobs import RegionPolicy, build_render_job, build_transit_jobs
from csmodernmap.ribbon_schema import normalize_terrain_mode
from csmodernmap.renderer import (
    AUTO_THRESHOLD,
    DISK_BACK_MIN_PIXELS,
    EST_BYTES_PER_PIXEL,
    SAFE_FULL_IMAGE_PEAK,
    Renderer,
)
from csmodernmap.spots import (
    has_traffic,
    pick_region_for_policy,
)
from csmodernmap.style import (
    Style,
    load_metro_style,
    load_style,
    strip_retired_region_palette_fields,
)
from csmodernmap.system_info import available_memory_gb

WATER_SMOOTH_PRESETS = {
    "低": (2, 4, 2.0, 30),
    "标准": (4, 6, 3.0, 45),
    "强": (4, 8, 4.0, 60),
    "极强": (4, 10, 5.0, 80),
}

from .export_region import output_dimensions, size_pair


_CLASS_BY_KEY = {item.settings_key: item.layer_cls for item in layer_directory()}

_THUMB_MAX_EDGE = 2048

PRESET_PALETTES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("default", (
        "#E4002B", "#024FA2", "#009943", "#F39800", "#8F76D6",
        "#00A0E9", "#E60079", "#00A497", "#6B4F2A", "#C8A200",
    )),
    ("warm", (
        "#D7263D", "#F46036", "#E8871E", "#C2571B", "#8C2F39",
        "#B23A48", "#DB504A", "#994636", "#E3B505", "#7A1F1F",
    )),
    ("colorblind", (
        "#0072B2", "#E69F00", "#009E73", "#CC79A7", "#D55E00",
        "#56B4E9", "#882255", "#117733", "#666666", "#AA4499",
    )),
)

SPACING_RANGE = (200, 800)


def _palette_colors(index) -> list[str] | None:
    try:
        idx = int(index)
    except (TypeError, ValueError):
        return None
    if idx <= 0 or idx >= len(PRESET_PALETTES):
        return None
    return list(PRESET_PALETTES[idx][1])


def _clamped_view_scale(value, default: float = 1.0) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = default
    return max(0.5, min(value, 2.0))


def _scale_size_value(value, factor: float):
    if isinstance(value, dict):
        return {
            key: (item * factor if isinstance(item, (int, float)) else item)
            for key, item in value.items()
        }
    if isinstance(value, (int, float)):
        return value * factor
    return value


def _scale_keys(config: dict, factor: float, *keys: str) -> None:
    if factor == 1.0:
        return
    for key in keys:
        if key in config:
            config[key] = _scale_size_value(config[key], factor)


def _metro_canvas_bg(style) -> tuple:
    try:
        c = str(style.get("canvas.background", "#FAFAF8")).strip().lstrip("#")
        if len(c) == 3:
            c = "".join(ch * 2 for ch in c)
        return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))
    except (ValueError, IndexError):
        return (250, 250, 248)


def _load_ui_style(ui: dict, stem: str) -> Style:
    overrides = ui["style_overrides"]
    if stem in overrides:
        data = copy.deepcopy(overrides[stem])
        style = Style(strip_retired_region_palette_fields(data))
    else:
        style_path = resource_dir("styles") / f"{stem}.yaml"
        style = load_style(str(style_path))
    style.data["_poi_mapping_override"] = ui.get("poi_mapping_override", "")
    _attach_poi_runtime_state(style, ui)
    return style


def _attach_poi_runtime_state(style: Style, ui: dict) -> None:
    """解析持久化主题后附加本次作业的 POI 控制项。"""
    style.data.setdefault("poi_icons", {})["use_game_names"] = bool(ui.get("poi_game_names", False))
    style.data.setdefault("roads", {})["use_game_names"] = bool(ui.get("road_game_names", False))
    profile = ui.get("poi_profile")
    visibility = ui.get("poi_visibility_snapshot")
    if not isinstance(profile, dict) or not isinstance(visibility, dict):
        return
    cfg = style.data.setdefault("poi_icons", {})
    cfg["active_profile"] = copy.deepcopy(profile)
    cfg["visibility_snapshot"] = normalize_poi_visibility_snapshot(visibility)


def _auto_memory_decision(
    render_size: int,
    aa_rate: int = 1,
    available_bytes: int | None = None,
) -> dict:
    size = max(1, int(render_size))
    aa = max(1, int(aa_rate))
    estimated = (size * aa) ** 2 * EST_BYTES_PER_PIXEL
    available = available_bytes
    if available is None:
        memory_gb = available_memory_gb()
        available = int(memory_gb * 1024 ** 3) if memory_gb is not None else None
    low_memory = size > AUTO_THRESHOLD or estimated > SAFE_FULL_IMAGE_PEAK
    if available is not None:
        low_memory = low_memory or estimated > int(available * 0.55)
    disk = low_memory and size * size >= DISK_BACK_MIN_PIXELS
    return {
        "policy": "auto",
        "memory_friendly": bool(low_memory),
        "disk_backing": bool(disk),
        "tile_size": 0,
        "estimated_peak": int(estimated),
        "available_memory": available,
    }


def _build_normal_renderer(
    ui: dict,
    style: str,
    layers: list,
    render_size: int,
    preview: bool,
    job_kind: str,
    *, poi_sublayer: str | None = None,
) -> Renderer:
    canvas_w, canvas_h = size_pair(render_size)
    edge = max(canvas_w, canvas_h)
    # 静态导出没有交互缩放，内容由图层和 PNG 图标密度决定，不读取停用的全局细节或尺寸选项。
    detail_mode = "auto" if preview else "detailed"
    s = _load_ui_style(ui, style)
    s.data.setdefault("roads", {})["accurate_intersections"] = bool(ui.get("accurate_intersections", True))
    terrain_cfg = s.data.setdefault("terrain", {})
    hillshade_cfg = terrain_cfg.setdefault("hillshade", {})
    terrain_ui = ui["terrain"]
    # 「地形表现」只有两档：分层设色（simple）/ 分层设色 + 山影（shade）。
    mode = normalize_terrain_mode(terrain_ui.get("mode"))
    # 高度场可插值，颜色只按等高区间取固定值。
    terrain_cfg["ground"] = {"enabled": False}
    terrain_cfg["elevation_mode"] = "stepped"
    hillshade_cfg["enabled"] = mode == "shade"
    # 面片参数与 polygon 模式随「立体棱角」一并删除：旧主题/旧主题覆写里可能
    # 还留着这些键，就地清掉，免得把渲染端引回已不存在的分支。
    for retired in ("mode", "facet_size_meters", "facet_jitter"):
        hillshade_cfg.pop(retired, None)
    # 界面上「山影强度」是 0~100 的百分比、「光照方向」是 0~360 度，渲染端读的
    # 却是 hillshade.intensity（0~1）与 hillshade.azimuth。此前这里把 shade / sun
    # 两个名字原样写进配置，渲染端根本不认这两个键 → 两个滑块在导出时形同虚设
    # （预览那条路一直是对的，见 preview_terrain）。这里按真实字段名写回去。
    try:
        shade_percent = float(terrain_ui.get("shade", 35.0))
        azimuth = float(terrain_ui.get("sun", 315.0))
    except (TypeError, ValueError):
        shade_percent, azimuth = 35.0, 315.0
    hillshade_cfg["intensity"] = max(0.0, min(1.0, shade_percent / 100.0))
    hillshade_cfg["azimuth"] = azimuth
    renderer = Renderer(
        s, width=canvas_w, height=canvas_h,
        rotation=ui["rotation"],
        detail_mode=detail_mode,
    )
    renderer.parallel_workers = 0
    poi_cfg = s.data.setdefault("poi_icons", {})
    poi_cfg["show_name"] = True
    poi_cfg["name_filters"] = {"player": True, "important": True}
    for owner in ("building", "transit"):
        poi_cfg.setdefault(owner, {})["enabled"] = True
        poi_cfg[owner]["show_name"] = True
    _apply_poi_display_settings(poi_cfg, ui, legacy=False)
    transports_cfg = s.data.setdefault("transports", {})
    transports_cfg["enabled"] = list(ui["transit_types"])
    transports_cfg["enabled_line_ids"] = ui.get("transit_line_ids")
    station_cfg = transports_cfg.setdefault("station", {})
    metro = ui["merge_overrides"].get("metro", {})
    station_cfg["merge_enabled"] = bool(metro.get("enabled", ui["merge_enabled"]))
    station_cfg["merge_range"] = float(metro.get("distance", ui["merge_distance"]))
    station_cfg["merge_overrides"] = ui["merge_overrides"]
    decision = _auto_memory_decision(edge)
    renderer.memory_friendly = decision["memory_friendly"]
    renderer.disk_backing = decision["disk_backing"]
    renderer.tile_size = decision["tile_size"]
    renderer._cslm_memory_decision = decision
    preset = WATER_SMOOTH_PRESETS[ui["water_smooth"]]
    for name in layers:
        cls = _CLASS_BY_KEY[name]
        if preview and cls is BuiltAreaLayer:
            continue
        if cls is WaterLayer:
            if preview:
                renderer.add_layer(WaterLayer(
                    upscale=1, smooth_iters=2,
                    rdp_epsilon=6.0, min_area=250))
            else:
                renderer.add_layer(
                    WaterLayer(
                        upscale=preset[0],
                        smooth_iters=preset[1],
                        rdp_epsilon=preset[2],
                        min_area=preset[3],
                    )
                )
        elif cls is DistrictLayer:
            renderer.add_layer(DistrictLayer(
                show_ids=ui["district_ids"]))
        elif cls is ZoneLayer:
            renderer.add_layer(ZoneLayer(
                show_ids=ui["park_ids"]))
        elif cls is LabelLayer:
            renderer.add_layer(LabelLayer(
                rule_overrides=visibility_overrides(
                    road=ui["road_names"],
                    district=ui["district_names"],
                ),
                name_filters={
                    "road_player_names": ui.get("road_player_names", True),
                    "road_asset_names": ui.get("road_asset_names", False),
                },
            ))
        elif cls is POIIconLayer:
            renderer.add_layer(POIIconLayer(sublayer=poi_sublayer))
        else:
            layer = cls()
            if cls is WatermarkLayer:
                layer._custom_text = ui["watermark_text"]
            renderer.add_layer(layer)
    selected_layers = tuple(layer.name for layer in renderer._layers)
    if job_kind == "transit":
        renderer.render_job, _overlay_job = build_transit_jobs(
            width=canvas_w, height=canvas_h,
            zoom=edge / 4096.0, detail_mode=detail_mode,
            base_layers=selected_layers,
        )
    else:
        renderer.render_job = build_render_job(
            job_kind, layers=selected_layers,
            width=canvas_w, height=canvas_h,
            zoom=edge / 4096.0, detail_mode=detail_mode,
        )
    return renderer


def _apply_poi_display_settings(cfg: dict, ui: dict, *, legacy: bool = True) -> None:
    # 城市专属设施选择属于对象排除规则，独立于配置的展示方式。
    cfg["facility_selection"] = copy.deepcopy(ui.get("facility_poi_overrides", {}))
    if legacy:
        # 地铁继续使用独立的编辑流程和旧控件。
        apply_merge_settings(cfg, ui)
        cfg["hidden_categories"] = list(ui.get("poi_hidden_categories", ()))
        cfg["visual_merge_enabled"] = bool(ui.get("merge_enabled", True))
        cfg["visual_merge_distance"] = float(ui.get("merge_distance", 100.0))
        grid, count = poi_density_params(ui.get("poi_density", "auto"))
        cfg.setdefault("grid_size", grid)
        cfg.setdefault("max_per_cell", count)
        return
    # 配置负责语义合并与可见性；PNG 布局仍使用主题显示网格，停用的界面合并字段不能充当门槛。
    cfg["hidden_categories"] = list(ui.get("poi_hidden_categories", ()))
    for key in ("visual_merge_enabled",
                "visual_merge_distance",
                "merge_enabled", "merge_distance", "merge_overrides",
                "facility_hierarchy",
                "facility_child_min_zoom"):
        cfg.pop(key, None)


def _apply_metro_options(
    ui: dict,
    s: Style,
    opts: dict,
    types: list,
    label_scale: float,
    icon_scale: float,
    station_names_mode: str,
    stations: bool,
) -> None:
    from PIL import ImageColor

    cfg = s.data.setdefault("transports", {})
    cfg["enabled"] = list(types)
    # 「选择线路」勾选出来的行车线白名单。这条映射原先只写在非 pure 分支
    # （_build_transit_renderer 的公共交通图），而地铁示意图走的是 pure 分支，
    # 于是勾选框点了、选项也存住了，画面却纹丝不动——PM 2026-09-10 报的
    # 「线路选择功能依旧不起效」。只有真拿到列表才设键：缺键 = 不过滤。
    raw_line_ids = opts.get("line_ids")
    if isinstance(raw_line_ids, list):
        cfg["enabled_line_ids"] = [int(value) for value in raw_line_ids]
    cfg["abstract"] = True
    cfg["octilinear"] = True
    try:
        line_width = float(opts.get("line_width", 0) or 0)
    except (TypeError, ValueError):
        line_width = 0
    if line_width > 0:
        cfg["width"] = line_width

    scale_opts = opts.get("schematic_options") or {}
    adaptive_enabled = bool(scale_opts.get("adaptive_scale", True))
    try:
        adaptive_strength = float(scale_opts.get("adaptive_strength", 0.40))
    except (TypeError, ValueError):
        adaptive_strength = 0.40
    adaptive_strength = max(0.0, min(adaptive_strength, 1.0))
    schematic_cfg = dict(cfg.get("schematic") or {})
    schematic_cfg["scale_mode"] = "adaptive" if adaptive_enabled else "fixed"
    adaptive_cfg = dict(schematic_cfg.get("adaptive") or {})
    adaptive_cfg["strength"] = adaptive_strength
    schematic_cfg["adaptive"] = adaptive_cfg
    try:
        spacing = int(opts.get("spacing", 0) or 0)
    except (TypeError, ValueError):
        spacing = 0
    if spacing > 0:
        schematic_cfg["spacing"] = float(
            max(SPACING_RANGE[0], min(spacing, SPACING_RANGE[1])))
    try:
        parallel_gap = float(opts.get("parallel_gap", 0.0) or 0.0)
    except (TypeError, ValueError):
        parallel_gap = 0.0
    if parallel_gap > 0:
        schematic_cfg["parallel_gap"] = max(1.05, min(parallel_gap, 3.0))
    # 未来矢量编辑器只发送小型 JSON 覆盖，保存在渲染作业中而不修改源地图或主题文件。
    edit_document = opts.get("metro_edits", opts.get("edit_document"))
    if isinstance(edit_document, dict):
        schematic_cfg["edits"] = dict(edit_document)
    cfg["schematic"] = schematic_cfg

    cfg["auto_color"] = False
    cfg.pop("palette", None)

    legend_enabled = bool(opts.get("legend_enabled", True))
    legend_position = str(opts.get("legend_position", "right_top"))
    cfg["legend"] = {"enabled": legend_enabled,
                     "position": legend_position}

    background = str(opts.get("background", "") or "").strip()
    if background:
        try:
            ImageColor.getrgb(background)
            s.data.setdefault("canvas", {})["background"] = background
        except ValueError:
            pass

    water_cfg = dict(cfg.get("schematic_water") or {})
    if not bool(opts.get("schematic_water", True)):
        water_cfg["enabled"] = False
    water_cfg["smoothing"] = float(opts.get("water_smoothing", water_cfg.get("smoothing", 8)))
    cfg["schematic_water"] = water_cfg

    ann_values = {}
    for name, yaml_default in (("disclaimer", True), ("scale_info", True),
                               ("data_notice", True), ("generated_at", True),
                               ("north_arrow", False)):
        value = opts.get(f"ann_{name}")
        if value is None:
            continue
        if bool(value) != yaml_default:
            ann_values[name] = bool(value)
    credit_on = opts.get("ann_credit")
    if credit_on is not None and not bool(credit_on):
        ann_values["credit"] = False
    if ann_values:
        ann_cfg = dict(cfg.get("annotations") or {})
        ann_cfg.update(ann_values)
        cfg["annotations"] = ann_cfg

    if station_names_mode not in ("none", "custom", "all"):
        station_names_mode = "all"
    st = copy.deepcopy(cfg.get("station") or {})
    st["enabled"] = bool(stations)
    try:
        station_size = float(opts.get("station_size", 0) or 0)
    except (TypeError, ValueError):
        station_size = 0
    if station_size > 0:
        st["size"] = station_size
    st["design"] = str(opts.get("station_style", "rmg"))
    st["merge_enabled"] = bool(opts.get("merge_enabled", True))
    st["merge_range"] = float(opts.get("merge_range", 48.0))
    st["merge_overrides"] = {}
    st["diameter_per_1k"] = float(opts.get("station_diameter", 12))
    facilities_cfg = copy.deepcopy(st.get("facilities") or {})
    facilities_cfg["enabled"] = bool(opts.get("facilities_enabled", True))
    facilities_cfg.setdefault("size", {"fixed": 12, "scaled": 8})
    facilities_cfg.setdefault("offset", {"fixed": 10, "scaled": 7})
    _scale_keys(facilities_cfg, _clamped_view_scale(
        opts.get("facilities_scale", 1.0)), "size", "offset")
    st["facilities"] = facilities_cfg
    names_cfg = dict(st.get("names") or {})
    names_cfg["font_size_per_1k"] = float(opts.get("station_name_size", 16))
    cfg.setdefault("label", {})["font"] = opts.get("station_font", "SimHei")
    names_cfg["color"] = opts.get("station_name_color", "#4A4F5A")
    names_cfg["stroke_per_1k"] = float(opts.get("station_name_stroke", 2))
    names_cfg["stroke_fill"] = opts.get("station_name_stroke_color", "#FFFFFF")
    names_cfg["halo"] = names_cfg["stroke_per_1k"] > 0
    names_cfg["enabled"] = station_names_mode != "none"
    names_cfg["position"] = str(opts.get("station_label_position", "auto"))
    language = str(opts.get("language", "zh"))
    names_cfg["language"] = language if language in ("zh", "bilingual") else "zh"
    st["names"] = names_cfg
    cfg["station"] = st

    title_cfg = dict(cfg.get("title") or {})
    _scale_keys(title_cfg, _clamped_view_scale(
            opts.get("title_scale", 1.0)), "size", "secondary_size")
    title_cfg["language"] = names_cfg["language"]
    if "title_enabled" in opts and not bool(opts["title_enabled"]):
        title_cfg["enabled"] = False
    title_template = str(opts.get("title_template", "") or "").strip()
    if title_template:
        title_cfg["template"] = title_template
    cfg["title"] = title_cfg

    lb = copy.deepcopy(cfg.get("label") or {})
    _scale_keys(lb, _clamped_view_scale(
            opts.get("legend_scale", 1.0)), "size", "title_size")
    lb["enabled"] = True
    cfg["label"] = lb


def _build_transit_renderer(
    ui: dict,
    style: str,
    types: list,
    render_size,
    stations: bool = True,
    abstract: bool = False,
    pure: bool = False,
    station_names_mode: str = "all",
    opts: dict | None = None,
) -> Renderer:
    if isinstance(render_size, tuple):
        canvas_w, canvas_h = int(render_size[0]), int(render_size[1])
    else:
        canvas_w = canvas_h = int(render_size)
    opts = opts or {}
    label_scale = _clamped_view_scale(opts.get("label_scale", 1.0))
    icon_scale = _clamped_view_scale(opts.get("icon_scale", 1.0))
    if pure:
        s = load_metro_style(str(resource_dir("styles")))
        _attach_poi_runtime_state(s, ui)
        _apply_metro_options(ui, s, opts, types, label_scale, icon_scale,
                             station_names_mode, stations)
        renderer = Renderer(
            s, width=canvas_w, height=canvas_h, rotation=ui["rotation"])
        renderer.parallel_workers = 0
        renderer.transparent = True
        decision = _auto_memory_decision(max(canvas_w, canvas_h))
        renderer.memory_friendly = decision["memory_friendly"]
        renderer.disk_backing = decision["disk_backing"]
        renderer.tile_size = decision["tile_size"]
        renderer._cslm_memory_decision = decision
        renderer.capture_rmp = bool(opts.get("capture_rmp", False))
        renderer.add_layer(TransportLayer(transit_view=True, metro=True))
        renderer.render_job = build_render_job(
            "metro", layers=("transit",), width=canvas_w, height=canvas_h,
            zoom=float(max(canvas_w, canvas_h)) / 4096.0,
            detail_mode="detailed",
            derive_params={"schematic": True, "transparent": True},
        )
        return renderer
    s = _load_ui_style(ui, style)
    cfg = s.data.setdefault("transports", {})
    cfg["enabled"] = list(types)
    if "line_ids" in opts:
        raw_ids = opts["line_ids"]
        cfg["enabled_line_ids"] = None if raw_ids is None else [int(value) for value in raw_ids]
    cfg["abstract"] = bool(abstract)
    cfg["lines_visible"] = _transit_option_enabled(opts, "lines", "lines")
    if "line_width" in opts:
        cfg["width"] = float(opts["line_width"])
    auto_color = bool(opts.get("auto_color", False))
    cfg["auto_color"] = auto_color
    colors = _palette_colors(opts.get("palette_index", 0))
    if auto_color and colors:
        cfg["palette"] = colors
    station_cfg = dict(cfg.get("station") or {})
    station_cfg["enabled"] = bool(stations) and _transit_option_enabled(opts, "stations", "stations")
    station_cfg["min_zoom"] = 0.0
    metro = ui["merge_overrides"].get("metro", {})
    st_enabled = bool(metro.get("enabled", ui["merge_enabled"]))
    st_range = float(metro.get("distance", ui["merge_distance"]))
    station_cfg["merge_enabled"] = bool(opts.get("merge_enabled", st_enabled))
    station_cfg["merge_range"] = float(opts.get("merge_range", st_range))
    station_cfg["merge_overrides"] = ui["merge_overrides"]
    station_cfg.setdefault("size", {"fixed": 2.5, "scaled": 5.0})
    if "station_size" in opts:
        station_cfg["size"] = float(opts["station_size"]) / 2.0
    names_cfg = dict(station_cfg.get("names") or {})
    names_cfg["enabled"] = bool(opts.get("station_names_enabled", False))
    if "station_name_size" in opts:
        names_cfg["size"] = {"fixed": 0.0, "scaled": float(opts["station_name_size"]) * 4.0}
    station_cfg["names"] = names_cfg
    _scale_keys(station_cfg, icon_scale, "size")
    cfg["station"] = station_cfg
    label_cfg = copy.deepcopy(cfg.get("label") or {})
    label_cfg["enabled"] = _transit_option_enabled(opts, "line_label_enabled", "line_labels")
    label_cfg["min_zoom"] = 0.0
    if "line_label_interval" in opts:
        label_cfg["repeat_interval"] = float(opts["line_label_interval"])
    _scale_keys(label_cfg, _clamped_view_scale(
            opts.get("legend_scale", 1.0)), "size", "title_size")
    cfg["label"] = label_cfg
    transit_view_cfg = copy.deepcopy(cfg.get("transit_view") or {})
    if "line_label_size" in opts:
        transit_view_cfg["label_size"] = {"fixed": 0.0, "scaled": float(opts["line_label_size"]) * 4.0}
    if "line_label_interval" in opts:
        transit_view_cfg["label_repeat_interval"] = float(opts["line_label_interval"])
    transit_view_cfg["label_size"] = _scale_size_value(
        transit_view_cfg.get("label_size", {"fixed": 18.0, "scaled": 12.0}),
        label_scale)
    cfg["transit_view"] = transit_view_cfg
    poi_cfg = copy.deepcopy(s.data.setdefault("poi_icons", {}))
    # POI 图标及名称可见性来自共享图层快照；旧交通视图开关不得再增加第二道门槛。
    poi_cfg["show_name"] = True
    poi_cfg["enabled"] = True
    for owner in ("building", "transit"):
        poi_cfg.setdefault(owner, {})["show_name"] = True
        poi_cfg[owner]["enabled"] = True
    if "facility_icon_size" in opts:
        # 界面尺寸按 1024 像素基准表示完整徽标直径。
        diameter = float(opts["facility_icon_size"])
        poi_cfg["icon_size"] = diameter * .75
        poi_cfg["bg_circle_size"] = diameter
    _apply_poi_display_settings(poi_cfg, ui)
    poi_cfg["name_filters"] = {"player": True, "important": True}
    _scale_keys(poi_cfg, icon_scale, "icon_size", "bg_circle_size")
    _scale_keys(poi_cfg, label_scale, "name_size")
    if "station_name_size" in opts:
        poi_cfg["transit_name_size"] = {"fixed": 0.0, "scaled": float(opts["station_name_size"]) * 4.0}
    _apply_poi_display_settings(poi_cfg, ui, legacy=False)
    visibility = copy.deepcopy(poi_cfg.get("visibility_snapshot", {}))
    if visibility:
        visibility["icons"]["transit"] = bool(opts.get("facilities_enabled", True))
        visibility["names"]["transit"] = bool(opts.get("facility_names_enabled", True))
        poi_cfg["visibility_snapshot"] = visibility
    s.data["poi_icons"] = poi_cfg
    renderer = Renderer(
        s, width=canvas_w, height=canvas_h, rotation=ui["rotation"])
    renderer.parallel_workers = 0
    renderer.transparent = True
    decision = _auto_memory_decision(max(canvas_w, canvas_h))
    renderer.memory_friendly = decision["memory_friendly"]
    renderer.disk_backing = decision["disk_backing"]
    renderer.tile_size = decision["tile_size"]
    renderer._cslm_memory_decision = decision
    renderer.add_layer(TransportLayer(transit_view=True))
    renderer.add_layer(POIIconLayer(transit_only=True, force_detail=True))
    _base_job, overlay_job = build_transit_jobs(
        width=canvas_w, height=canvas_h,
        zoom=float(max(canvas_w, canvas_h)) / 4096.0,
        detail_mode="auto",
    )
    renderer.render_job = overlay_job
    return renderer


def _transit_option_enabled(opts, option, layer):
    return bool(opts.get(option, True)) and bool((opts.get("layers") or {}).get(layer, True))


def _transit_base_layers(layers, opts):
    hidden = {"metro", "poi", "label", "watermark"}
    for option, names in (("base_water", ("water",)), ("base_road", ("road",)),
                          ("base_district", ("district", "zone"))):
        if not _transit_option_enabled(opts, option, option):
            hidden.update(names)
    return [name for name in layers if name not in hidden]


def _parse_for_render(file: str, progress: Callable | None) -> Any:
    """直接把源文件解析为标准 CMM 文档；派生服务和旧版兼容视图由 DocumentSession 按需创建。"""
    return load_document(file, progress=progress, validate=False)


def _session_for(document: Any) -> Any:
    """返回并缓存与指定文档绑定的 DocumentSession。"""
    from csmodernmap.cmm.session import DocumentSession

    session = getattr(document, "_session", None)
    if session is None:
        session = DocumentSession(document=document)
        document._session = session
    return session


def _attach_traffic(mapdata: Any) -> tuple[str, str]:
    snap = getattr(mapdata, "traffic", None)
    if snap and getattr(snap, "segments", None):
        n = len(snap.segments)
        paused = getattr(snap, "simulation_paused", False)
        suffix = " (暂停)" if paused else ""
        return tr("gui.status.traffic_embedded", n=n) + suffix, "#16a34a"
    return tr("gui.status.no_traffic"), "#888888"


def _extract_zone_items(mapdata: Any) -> tuple[list, list]:
    districts = [
        (d.id, d.name or tr("gui.zone.district_fallback", id=d.id))
        for d in visible_administrative_districts(mapdata)
        if d.grid_points or len(d.points or ()) >= 3
    ]
    parks = [
        (p.id, (p.name or tr("gui.zone.park_fallback", id=p.id))
         + (f"（{p.ptype}）" if p.ptype else ""))
        for p in mapdata.parks
        if (p.grid_points or len(p.points or ()) >= 3)
        and (p.ptype or "").strip().lower() not in ("none", "0")
    ]
    return districts, parks


def _extract_city_info(mapdata: Any) -> dict:
    snap = getattr(mapdata, "traffic", None)
    got_traffic = bool(snap and getattr(snap, "segments", None))
    traff = len(snap.segments) if got_traffic else 0
    return {
        "name": (getattr(mapdata, "city_name", "") or "").strip()
        or tr("gui.status.city_unknown"),
        "generated": (getattr(mapdata, "generated", "") or "").strip() or "—",
        "buildings": int(len(getattr(mapdata, "buildings", ()) or ())),
        "districts": len(visible_administrative_districts(mapdata)),
        "parks": int(len(getattr(mapdata, "parks", ()) or ())),
        "transports": int(len(getattr(mapdata, "transports", ()) or ())),
        "forests": int(len(getattr(mapdata, "forests", ()) or ())),
        "nodes": int(len(getattr(mapdata, "nodes", ()) or ())),
        "segments": int(len(getattr(mapdata, "segments", ()) or ())),
        "traffic": traff if got_traffic else -1,
    }


def _build_context(mapdata: Any, ui: dict) -> dict:
    traffic_status = _attach_traffic(mapdata)
    districts, parks = _extract_zone_items(mapdata)
    return {
        "city_info": _extract_city_info(mapdata),
        "traffic_status": traffic_status,
        "district_items": districts,
        "park_items": parks,
    }


def _resolve_region(job: dict, mapdata: Any) -> tuple[Any, int, bool, Any]:
    """解析作业区域：固定区域或可序列化区域策略（子进程内执行）。

    返回 ``(region, out_size, is_region, center)``。center 为策略选中的
    区域中心（随机特写避让用），无法确定时为 None。
    """
    region = job.get("region")
    center = None
    policy_raw = job.get("region_policy")
    if policy_raw:
        policy = RegionPolicy.from_dict(policy_raw)
        region, center = pick_region_for_policy(mapdata, policy)
    out_size = int(job["size"])
    return region, out_size, region is not None, center


def _job_canvas(job, region, edge):
    return size_pair(job["output_size"]) if job.get("output_size") else output_dimensions(
        region, edge, job.get("ui", {}).get("rotation", 0))


def _make_thumbnail(image: Image.Image) -> Image.Image:
    width, height = image.size
    scale = min(1.0, _THUMB_MAX_EDGE / max(width, height, 1))
    if scale >= 1.0:
        return image.copy()
    target = (max(1, int(round(width * scale))),
              max(1, int(round(height * scale))))
    return image.resize(target, Image.LANCZOS)


def run_render_task(
    job: dict,
    *,
    progress: Callable[[float, str], None] | None = None,
    mapdata_cache: dict | None = None,
    session=None,
    check_cancel=lambda: None,
) -> dict:
    """在同一进程内同步执行一个渲染任务并返回结果负载。

    mapdata_cache: 传入跨任务复用的 {file: mapdata} 字典可避免重复解析。
    """
    from .progress import StageTimer
    timer = StageTimer()
    t0 = time.time()
    file = job["file"]

    def p_parse(pct: float, msg: str) -> None:
        if progress is not None:
            if hasattr(progress, "phase"):
                progress.phase = "load"
            progress(pct * 0.40, tr("gui.status.parsing", msg=msg))

    def p_render(pct: float, msg: str) -> None:
        if progress is not None:
            if hasattr(progress, "phase"):
                progress.phase = "render"
            progress(0.40 + pct * 0.60, msg)

    timer.start("load.xml")
    check_cancel()
    cache = mapdata_cache if mapdata_cache is not None else {}
    document = session.document if session is not None else cache.get(file)
    if document is None:
        document = _parse_for_render(file, p_parse)
        cache[file] = document
    timer.end("load.xml")
    timer.start("derive.session")
    session = session if session is not None else _session_for(document)
    mapdata = session._legacy_view()
    timer.end("derive.session")

    timer.start("build_context")
    context = _build_context(mapdata, job["ui"])
    if getattr(mapdata, "traffic", None) is not None:
        document.traffic = mapdata.traffic
    timer.end("build_context")

    timer.start("resolve_region")
    region, out_size, is_region, region_center = _resolve_region(job, mapdata)
    timer.end("resolve_region")
    timer.start("build_renderer")
    renderer = _build_normal_renderer(
        job["ui"], job["style"], job["layers"], _job_canvas(job, region, out_size),
        preview=bool(job.get("preview")),

        job_kind=job.get("job_kind", "normal"),
    )
    aa_rate = 2 if job.get("aa") else 1
    renderer.preview_callback = None if job.get("final_png") else getattr(progress, "preview", None)
    renderer.cancel_check = check_cancel
    decision = _auto_memory_decision(out_size, aa_rate=aa_rate)
    renderer.memory_friendly = decision["memory_friendly"]
    renderer.disk_backing = decision["disk_backing"]
    renderer.tile_size = decision["tile_size"]
    renderer._cslm_memory_decision = decision
    timer.end("build_renderer")
    timer.start("render.layers")
    check_cancel()
    image = renderer.render(
        document,
        progress=p_render,
        region=region,
        aa_downsample=aa_rate,
        session=session,
    )
    timer.end("render.layers")
    check_cancel()
    timer.start("output.thumbnail")
    thumbnail = None if job.get("final_png") else _make_thumbnail(image)
    timer.end("output.thumbnail")
    elapsed = time.time() - t0
    stats = dict(renderer.last_stats)
    stats["_memory_decision"] = getattr(
        renderer, "_cslm_memory_decision", None)
    timer.merge_into(stats)
    return {
        "image": image,
        "thumbnail": thumbnail,
        "size": out_size,
        "stats": stats,
        "elapsed": elapsed,
        "is_region": is_region,
        "region_center": region_center,
        "context": context,
    }


class TransitSelectionError(ValueError):
    """路线选择不满足渲染要求，可直接向用户展示。"""


def run_transit_task(
    job: dict,
    *,
    progress: Callable[[float, str], None] | None = None,
    mapdata_cache: dict | None = None,
) -> dict:
    """公共交通图 / 地铁示意图：底图 + 透明叠加两段式渲染（或纯图）。"""
    t0 = time.time()
    file = job["file"]
    ui = job["ui"]
    style = job["style"]
    types = list(job["types"])
    opts = job["opts"] or {}
    layers = list(job["layers"])
    aa_rate = int(job.get("aa") or 1)
    region = job.get("region")
    watermark_text = job.get("watermark_text")

    def p_parse(pct: float, msg: str, stage=None) -> None:
        if progress is not None:
            progress.phase = "load"
            progress(pct * 0.30, tr("gui.status.parsing", msg=msg), stage)

    def p_render(pct: float, msg: str, stage=None) -> None:
        if progress is not None:
            progress.phase = "base"
            progress(0.30 + pct * 0.55, msg, stage)

    def p_transit(pct: float, msg: str, stage=None) -> None:
        if progress is not None:
            progress.phase = "transit"
            start, share = (0.30, 0.67) if opts.get("pure") else (0.85, 0.12)
            progress(start + pct * share, msg, stage)

    p_parse.supports_stage_progress = True
    p_render.supports_stage_progress = True
    p_transit.supports_stage_progress = True

    cache = mapdata_cache if mapdata_cache is not None else {}
    document = cache.get(file)
    if document is None:
        document = _parse_for_render(file, p_parse)
        cache[file] = document
    session = _session_for(document)
    mapdata = session._legacy_view()
    present_types = {
        transit.normalize_type(item.type) for item in mapdata.transports
    }
    types = [item for item in types if item in present_types]
    if opts.get("pure"):
        selected_ids = opts.get("line_ids")
        if not any(
            transit.normalize_type(line.type) in types
            and (selected_ids is None or line.id in selected_ids)
            for line in mapdata.transports
        ):
            raise TransitSelectionError(tr("gui.transit.no_lines_selected"))
    # 显式空线路白名单是有效交通视图，表示仅显示弱化底图；缺失类型选择仍是配置错误。
    if not types and opts.get("line_ids") != []:
        raise ValueError(tr("gui.transit.no_transport"))
    context = _build_context(mapdata, ui)
    if getattr(mapdata, "traffic", None) is not None:
        document.traffic = mapdata.traffic

    if bool(opts.get("pure")):
        requested = int(opts.get("size", 0) or 0)
        out_size = (recommended_metro_output_size(mapdata, types)
                    if requested <= 0 else max(requested, 128))
        canvas_w, canvas_h = metro_content_canvas_size(
            mapdata, types, out_size,
            rotation=int(ui.get("rotation", 0) or 0))
        r_tr = _build_transit_renderer(
            ui, style, types, (canvas_w, canvas_h),
            stations=bool(opts["stations"]), abstract=True, pure=True,
            station_names_mode=opts.get("station_names_mode", "all"),
            opts=opts)
        decision = _auto_memory_decision(max(canvas_w, canvas_h),
                                         aa_rate=aa_rate)
        r_tr.memory_friendly = decision["memory_friendly"]
        r_tr.disk_backing = decision["disk_backing"]
        r_tr.tile_size = decision["tile_size"]
        r_tr._cslm_memory_decision = decision
        img_tr = r_tr.render(document, region=None, aa_downsample=aa_rate,
                             progress=p_transit, session=session)
        bg = _metro_canvas_bg(r_tr.style)
        img = Image.new("RGBA", img_tr.size, bg + (255,))
        img.alpha_composite(img_tr)
        img = img.convert("RGB")
        stats = dict(r_tr.last_stats)
        stats["transit_overlay"] = r_tr.last_stats.get("transport", 0)
        stats["_memory_decision"] = getattr(
            r_tr, "_cslm_memory_decision", None)
        is_region = False
    else:
        region, out_size, _is_region, _center = _resolve_region(job, mapdata)
        canvas_size = _job_canvas(job, region, out_size)
        base_layers = _transit_base_layers(layers, opts)
        r_base = _build_normal_renderer(
            ui, style, base_layers, canvas_size,
            preview=False, job_kind="transit")
        decision = _auto_memory_decision(out_size, aa_rate=aa_rate)
        r_base.memory_friendly = decision["memory_friendly"]
        r_base.disk_backing = decision["disk_backing"]
        r_base.tile_size = decision["tile_size"]
        r_base._cslm_memory_decision = decision
        img = r_base.render(document, progress=p_render, region=region,
                            aa_downsample=aa_rate, session=session)
        ordinary_mode = str(opts.get("ordinary_pois", "hidden"))
        ordinary_image = None
        if ordinary_mode in {"dim", "visible"}:
            ordinary_ui = {**ui, "poi_transit": False, "poi_building": True,
                           "text_enabled": True, "poi_names": True}
            ordinary = _build_normal_renderer(
                ordinary_ui, style, ["poi"], canvas_size, False, "normal",
                poi_sublayer="building")
            ordinary.transparent = True
            ordinary_image = ordinary.render(document, region=region,
                                             aa_downsample=aa_rate, session=session)
            if ordinary_mode == "dim":
                img = Image.alpha_composite(img.convert("RGBA"), ordinary_image.convert("RGBA")).convert("RGB")
        mask_override = opts.get("transit_mask")
        if mask_override is None:
            _tv = ((r_base.style.data.get("transports") or {})
                   .get("transit_view") or {})
            try:
                mask = float(_tv.get("mask_intensity", 0.5))
            except (TypeError, ValueError):
                mask = 0.5
        else:
            try:
                mask = float(mask_override)
            except (TypeError, ValueError):
                mask = 0.0
        mask = max(0.0, min(1.0, mask))
        if mask > 0:
            white = Image.new("RGB", img.size, (255, 255, 255))
            img = Image.blend(img, white, mask)
        stats = dict(r_base.last_stats)
        if ordinary_mode == "visible" and ordinary_image is not None:
            img = Image.alpha_composite(img.convert("RGBA"), ordinary_image.convert("RGBA")).convert("RGB")
        # 空线路选择仍允许没有服务线路的设施。
        r_tr = _build_transit_renderer(
            ui, style, types, canvas_size,
            stations=bool(opts["stations"]),
            abstract=bool(opts.get("abstract", False)), opts=opts)
        decision = _auto_memory_decision(out_size, aa_rate=aa_rate)
        r_tr.memory_friendly = decision["memory_friendly"]
        r_tr.disk_backing = decision["disk_backing"]
        r_tr.tile_size = decision["tile_size"]
        r_tr._cslm_memory_decision = decision
        img_tr = r_tr.render(document, region=region, aa_downsample=aa_rate,
                             progress=p_transit, session=session)
        img = img.convert("RGBA")
        img.alpha_composite(img_tr)
        img = img.convert("RGB")
        stats["transit_overlay"] = r_tr.last_stats.get("transport", 0)
        if "transit_layout" in r_tr.last_stats:
            stats["transit_layout"] = r_tr.last_stats["transit_layout"]
        stats["_memory_decision"] = getattr(
            r_base, "_cslm_memory_decision", None)
        is_region = region is not None

    if "watermark" in layers:
        s = _load_ui_style(ui, style)
        WatermarkLayer.draw_on_image(
            img, s, mapdata,
            render_scale=min(img.size) / 4096,
            custom_text=watermark_text,
        )
    elapsed = time.time() - t0
    return {
        "image": img,
        "thumbnail": None if job.get("final_png") else _make_thumbnail(img),
        "size": out_size,
        "stats": stats,
        "elapsed": elapsed,
        "is_region": is_region,
        "context": context,
        "rmp_snapshot": getattr(r_tr, "last_rmp_snapshot", None),
    }


class _WorkerProgress:
    """把渲染器进度事件转发到结果队列，并记录完整任务生命周期的阶段耗时。"""

    supports_stage_progress = True

    def __init__(self, result_q: Any, serial=None, source_file=None):
        from .progress import ProgressEmitter, StageTimer
        self._result_q = result_q
        self._serial = serial
        self._source_file = source_file
        self._timer = StageTimer()
        self._phase = "render"
        self._emitter = ProgressEmitter(self._emit, phase="render")

    @property
    def phase(self) -> str:
        return self._phase

    @phase.setter
    def phase(self, value: str) -> None:
        self._phase = value
        self._emitter.phase = value

    @property
    def timer(self):
        return self._timer

    @property
    def _sink(self):
        return self._emitter._sink

    def _emit(self, event) -> None:
        self._result_q.put(("progress_event", event))

    def preview(self, image, phase: str) -> None:
        self._result_q.put(("preview", self._serial, self._source_file,
                            image, phase))

    def __call__(self, pct: float, msg: str,
                 stage: tuple[str, float] | None = None) -> None:
        self._emitter(pct, msg, stage)

    def emit(self, phase: str, phase_fraction, overall_fraction, message: str) -> None:
        from .progress import ProgressEvent
        event = ProgressEvent(phase, phase_fraction, overall_fraction, message)
        self._result_q.put(("progress_event", event))


def worker_loop(job_q: Any, result_q: Any) -> None:
    # 语言是进程级全局：本进程是 spawn 出来的，不主动初始化就会停在模块默认的
    # 中文，于是英文界面下渲染进度与导出提示仍是中文（渲染器里的 tr() 白键化）。
    # 父进程在 spawn 前把语言写进了 CSLMM_LANG，detect_lang() 优先读它。
    set_lang(detect_lang())
    cache: dict = {}
    while True:
        job = job_q.get()
        if job is None:
            break
        try:
            progress_cb = _WorkerProgress(result_q, job.get("serial"), job.get("file"))
            kind = job.get("kind", "normal")
            if kind == "transit":
                result = run_transit_task(
                    job, progress=progress_cb, mapdata_cache=cache)
            else:
                result = run_render_task(
                    job, progress=progress_cb, mapdata_cache=cache)
            if job.get("png_path"):
                from .export import export_png
                image = result.pop("image")
                result["image_size"] = image.size
                result["png_path"] = export_png(image, job["png_path"])
                result.pop("thumbnail", None)
            result["serial"] = job.get("serial")
            result_q.put(("done", result))
        except TransitSelectionError as exc:
            result_q.put(("error", str(exc)))
        except Exception:  # noqa: BLE001
            result_q.put(("error", traceback.format_exc()))
