"""从冻结文档生成 RMP 编辑草稿；不创建画布或调用图片排版。"""
from __future__ import annotations

from copy import deepcopy
from statistics import median
from shapely.geometry import mapping

from . import transit
from ._paths import resource_dir
from .cmm import DocumentSession
from .metro_edit import CompiledEdits, EditIssue, MetroEditDocument, build_editor_scene
from .metro_features import recommended_metro_output_size, metro_content_canvas_size
from .schematic_water import layout_and_water
from .style import load_metro_style
from .transit_context import build_transit_context


class RmpSelectionError(ValueError):
    """生成工程需要至少一条明确选择的线路。"""


class RmpCancelled(RuntimeError):
    pass


def generate_rmp(document, options, *, progress=None, cancelled=None):
    """返回站点、线路链、水域、设施和诊断；样式由编辑器注册表初始化。"""
    def report(fraction, message):
        if cancelled is not None and cancelled():
            raise RmpCancelled("RMP 生成已取消")
        if progress is not None:
            progress(fraction, message)

    report(0, "准备交通模型")
    session = DocumentSession(document)
    mapdata = session._legacy_view()
    cfg = deepcopy(load_metro_style(str(resource_dir("styles"))).data["transports"])
    enabled = transit.resolve_enabled(options.get("types", cfg["enabled"]))
    cfg["enabled_line_ids"] = options.get("line_ids")
    cfg["auto_color"] = False
    cfg.pop("palette", None)
    st_cfg = cfg["station"]
    st_cfg["merge_enabled"] = bool(options.get("merge_enabled", True))
    st_cfg["merge_range"] = float(options.get("merge_range", 48))
    st_cfg["facilities"]["enabled"] = bool(options.get("facilities_enabled", True))
    model, lines, _ = build_transit_context(mapdata, cfg, enabled, facilities=True)
    if not lines:
        from .i18n import tr
        raise RmpSelectionError(tr("gui.transit.no_lines_selected"))
    report(.15, "生成示意布局与水域")
    canvas = metro_content_canvas_size(mapdata, enabled, recommended_metro_output_size(mapdata, enabled))
    zoom = max(canvas) / 4096
    def size(value):
        return value.get("fixed", 0) + value.get("scaled", 0) * zoom if isinstance(value, dict) else float(value)

    edits = CompiledEdits()
    raw_edits = options.get("metro_edits")
    if isinstance(raw_edits, dict):
        try:
            edits = MetroEditDocument.from_dict(raw_edits).for_map(mapdata).compile(model.stations, lines)
        except (KeyError, TypeError, ValueError) as exc:
            # 可选编辑覆盖失效时保留基础布局，并将原始问题交给编辑器。
            edits.issues.append(EditIssue("edit_document_invalid", str(exc), severity="error"))
    raw_by_id = {line.id: line for line in lines}
    aliases = {id(raw_by_id[alias.id]): id(raw_by_id[group.primary.id])
               for group in model.groups for alias in group.aliases
               if alias.id in raw_by_id and group.primary.id in raw_by_id}
    schematic = cfg["schematic"]
    adaptive = schematic["adaptive"]
    scale_options = options.get("schematic_options") or {}
    widths = [max(size((cfg.get("types", {}).get(transit.type_def(line.type).key) or {}).get(
        "width", cfg["width"])) * cfg.get("transit_view", {}).get("width_scale", 1.7),
        2 if transit.type_def(line.type).track_flag else 1.5) for line in lines]
    layout, water, water_report = layout_and_water(
        mapdata, list(lines), model.stations, enabled, session=session,
        water_smoothing=float(options.get("water_smoothing", cfg["schematic_water"]["smoothing"])),
        line_width_pixels=median(widths), canvas=canvas, rotation=0,
        spacing=float(options.get("spacing", schematic["spacing"])), alias=aliases,
        share_corridors=cfg["grouping"]["share_corridors"],
        station_merge_distance=st_cfg["merge_range"], merge_nearby_stations=st_cfg["merge_enabled"],
        adaptive_scale=bool(scale_options.get("adaptive_scale", True)),
        adaptive_strength=float(scale_options.get("adaptive_strength", .85)),
        adaptive_max_compress=adaptive["max_compress"], adaptive_max_expand=adaptive["max_expand"],
        adaptive_dense_gap=adaptive["dense_gap"], adaptive_dense_threshold=adaptive["dense_threshold"],
        transit_model=model, station_grid_overrides=edits.station_cells,
        line_loop_overrides=edits.line_loops, shared_corridor_overrides=edits.shared_segments)
    report(.85, "整理可编辑站线与设施")
    stations, facilities = [], []
    facility_cfg = st_cfg["facilities"]
    canvas_scale = max(min(canvas) / 1024, .5)
    offset = max(round(size(facility_cfg["offset"]) * canvas_scale), 7)
    for station in model.stations:
        if station.id not in layout.node_grid:
            continue
        x, y = layout.to_px(*layout.node_grid[station.id])
        station_type = edits.station_types.get(station.id)
        transfer = station_type == "interchange" or (station_type not in {"normal", "terminal"}
                    and len(station.serving_group_ids) >= 2)
        stations.append({"id": str(station.id), "x": x, "y": y, "name": station.name.text.strip(),
                         "name_source": station.name.source, "transfer": transfer})
        if station.facilities:
            facility = station.facilities[0]
            facilities.append({"station_id": str(station.id), "kind": facility.kind.value.lower(),
                               "x": int(x + offset), "y": int(y - offset), "name": facility.name.text})
    display_water = bool(options.get("schematic_water", True)) and water is not None and not water.is_empty
    model_lines = {line.id: line for line in model.lines}
    result = {
        "stations": stations,
        "lines": [{"source_line_id": line.id, "source_name": model_lines[line.id].name,
                   "source_color": model_lines[line.id].user_color,
                   "chains": [[str(ident) for ident in chain] for chain in layout.source_chains[line.id]]}
                  for line in lines if id(line) not in layout.route_alias and layout.source_chains.get(line.id)],
        "water": mapping(water) if display_water else None, "facilities": facilities,
        "diagnostics": {
            "mode": layout.fallback_strategy, "failures": list(layout.layout_failures),
            "needs_visual_adjustment": layout.needs_visual_adjustment or layout.fallback_strategy != "requested",
            "model_issues": list(model.issues), "placement": dict(layout.placement_metrics),
            "source_chains": layout.source_chains, "station_positions": dict(layout.node_grid),
            "station_types": dict(edits.station_types), "shared_segments": list(edits.shared_segments),
            "edit_issues": [issue.to_dict() for issue in edits.issues],
            "editor_scene": build_editor_scene(layout, model.stations, lines, edits.issues),
            "water": {**water_report, "displayed": display_water},
        },
    }
    report(1, "工程草稿已生成")
    return result
