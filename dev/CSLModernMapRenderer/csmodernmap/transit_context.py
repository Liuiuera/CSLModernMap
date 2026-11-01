"""普通交通绘制与示意工程共用的交通模型输入。"""
from dataclasses import replace
from .cmm import GroupingParams, JobParams, StationMergeParams
from .data import MapData
from .services.transit import build_transit_model, match_facilities, resolve_route_geometry
from .services.transit.color import hex_palette


def build_transit_context(mapdata: MapData, cfg: dict, enabled: list, *, facilities=False):
    """构建当前渲染共用的交通视图；源 MapData 保持兼容输入，后续消费只读取 T02 派生模型。"""
    st_cfg = cfg.get("station") or {}
    gcfg = cfg.get("grouping") or {}
    station_params = StationMergeParams(
        merge_enabled=bool(st_cfg.get("merge_enabled", True)),
        merge_range=float(st_cfg.get("merge_range", 48.0)),
        merge_bus_tram=bool(st_cfg.get("merge_bus_tram", True)),
        merge_train_metro=bool(st_cfg.get("merge_train_metro", False)),
        overrides=st_cfg.get("merge_overrides") or {},
    )
    grouping_params = GroupingParams(
        by_name=bool(gcfg.get("by_name", True)),
        sequence_fallback=bool(gcfg.get("sequence_fallback", True)),
        share_ratio=float(gcfg.get("share_ratio", 0.0)),
        merge_partial_overlap=bool(
            gcfg.get("merge_partial_overlap", True)),
        share_corridors=bool(gcfg.get("share_corridors", False)),
    )
    palette = tuple(hex_palette(cfg.get("palette") or ()))
    line_selection = cfg.get(
        "enabled_line_ids", cfg.get("line_ids"))
    params = JobParams(
        enabled_types=tuple(enabled),
        enabled_line_ids=(
            None if line_selection is None
            else tuple(line_selection or ())),
        view_all=False,
        auto_color=bool(cfg.get("auto_color", False)),
        palette=palette,
        station_merge=station_params,
        grouping=grouping_params,
    )
    # None 启用地图旁自动发现，显式路径原样传递，False 则关闭。
    station_fix = st_cfg.get("sidecar", st_cfg.get("station_fix"))
    model = build_transit_model(
        mapdata, params, station_fix=station_fix,
    )
    # 设施匹配属于站点上下文，服务按 T02 站点标识返回关联而不修改源地图。
    if facilities:
        facility_cfg = st_cfg.get("facilities") or {}
        if bool(facility_cfg.get("enabled", True)):
            facility_map = match_facilities(
                mapdata, model.stations,
                distances=facility_cfg.get("distances") or {},
                merge_distances=facility_cfg.get("merge_distances") or {},
            )
            model = replace(model, stations=tuple(
                replace(station, facilities=[facility_map[str(station.id)]])
                if str(station.id) in facility_map else station
                for station in model.stations
            ))
        else:
            # 标准交通模型可能已有设施关联；关闭该视图时也必须清空这些关联。
            model = replace(model, stations=tuple(
                replace(station, facilities=[]) for station in model.stations))

    raw_by_id = model.source_lines
    render_lines = tuple(
        raw_by_id[line.id] for line in model.lines if line.id in raw_by_id
    )
    geometry_cache = {}
    geometries = {
        line.id: resolve_route_geometry(
            mapdata, raw_by_id[line.id], line.kind, cache=geometry_cache)
        for line in model.lines if line.id in raw_by_id
    }
    return model, render_lines, geometries
