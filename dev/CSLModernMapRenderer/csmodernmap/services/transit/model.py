"""构建完整的交通线路附属模型。"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from collections.abc import Mapping

from ...cmm import (
    Direction,
    JobParams,
    Route,
    TransitKind,
    TransitModel,
    normalize_transit_kind,
)
from ...services.station import (
    StationSidecar,
    StationSidecarError,
    apply_station_fixes,
    group_stations,
    load_station_sidecar_if_present,
    merge_params_with_sidecar,
    validate_station_invariants,
    validate_station_sidecar,
)
from .color import resolve_auto_colors
from .facility import match_facilities
from .geometry import resolve_route_geometry
from .grouping import build_line_groups, normalize_transit
from .name import resolve_station_names, resolve_stop_names
from .visibility import build_visibility
from .topology import build_physical_topologies


@dataclass(frozen=True)
class TransitSelection:
    """定义渲染消费方共用的标准线路和类型选择；None 表示未筛选，空元组表示明确未选中任何项。"""

    types: tuple[TransitKind, ...] | None = None
    line_ids: tuple[int, ...] | None = None

    def __post_init__(self) -> None:
        if self.types is not None:
            normalized = []
            for value in self.types:
                kind = normalize_transit_kind(value)
                if kind is not TransitKind.OTHER and kind not in normalized:
                    normalized.append(kind)
            object.__setattr__(self, "types", tuple(normalized))
        if self.line_ids is not None:
            object.__setattr__(
                self, "line_ids",
                tuple(dict.fromkeys(int(value) for value in self.line_ids)),
            )

    @classmethod
    def from_mapping(cls, value: Mapping | None = None, **kwargs) -> "TransitSelection":
        """同时接受旧界面名称和标准名称。"""
        raw = dict(value or {})
        raw.update(kwargs)
        type_key = next(
            (key for key in ("types", "transit_types", "enabled_types")
             if key in raw),
            None,
        )
        line_key = next(
            (key for key in (
                "line_ids", "transit_line_ids", "enabled_line_ids",
            ) if key in raw),
            None,
        )
        types = raw.get(type_key) if type_key is not None else None
        line_ids = raw.get(line_key) if line_key is not None else None
        if isinstance(types, str):
            types = (types,)
        if isinstance(line_ids, (str, int)):
            line_ids = (line_ids,)
        return cls(
            types=None if types is None else tuple(types or ()),
            line_ids=None if line_ids is None else tuple(line_ids or ()),
        )

    def to_dict(self) -> dict:
        return {
            "types": (None if self.types is None
                      else [item.value for item in self.types]),
            "line_ids": (None if self.line_ids is None
                         else list(self.line_ids)),
        }

    def to_job_params(self, **overrides) -> JobParams:
        """构造 CMM 参数时保留 None 与空值的区别。"""
        values = {
            "enabled_types": self.types,
            "enabled_line_ids": self.line_ids,
            "view_all": True,
        }
        values.update(overrides)
        return JobParams(**values)


def _route_id(group_id: int, line_id: int, direction: Direction) -> int:
    token = f"{group_id}:{line_id}:{direction.value}"
    return int(hashlib.sha1(token.encode("utf-8")).hexdigest()[:14], 16)


def build_transit_model(
    mapdata,
    job_params: JobParams | None = None,
    station_fix=None,
    *,
    station_fix_path=None,
    station_fixes=None,
    fixes=None,
    name_index=None,
) -> TransitModel:
    """纯函数式规范化 MapData 并推导线路、路线、站点、颜色和细节等级，不修改源数据或渲染缓存。"""
    provided = [value is not None for value in
                (station_fix, station_fix_path, station_fixes, fixes)]
    if sum(provided) > 1:
        raise TypeError(
            "only one of station_fix, station_fix_path, station_fixes, fixes may be supplied"
        )
    if station_fixes is not None:
        station_fix = station_fixes
    if fixes is not None:
        station_fix = fixes
    if station_fix_path is not None:
        station_fix = station_fix_path
    params = job_params or JobParams()
    from .native import cs2_document, station_constraints, migrate_sidecar
    document = cs2_document(mapdata)
    selected_ids = {line.id for line in normalize_transit(mapdata, params).lines}
    normalized = normalize_transit(mapdata, replace(
        params, enabled_line_ids=None, enabled_types=None, view_all=True))
    groups = build_line_groups(mapdata, normalized, params)
    group_of = {line.id: group for group in groups for line in group.members}
    named_stops = resolve_stop_names(mapdata, normalized.stops, name_index=name_index)
    named_stops = tuple(replace(
        stop,
        serving_group_ids=tuple(dict.fromkeys(
            group_of[line_id].id for line_id in stop.serving_line_ids
            if line_id in group_of
        )),
    ) for stop in named_stops)
    normalized = replace(normalized, stops=named_stops,
                         stop_by_key={item.stop_key: item for item in named_stops})

    source_map = (getattr(mapdata, "_source_path", None)
                  or getattr(mapdata, "source_path", None))
    sidecar = station_fix
    if sidecar is None:
        sidecar = getattr(mapdata, "_station_fix_path", None)
    if not isinstance(sidecar, (StationSidecar, Mapping)) and sidecar is not False:
        sidecar = load_station_sidecar_if_present(source_map, sidecar)
    elif isinstance(sidecar, Mapping):
        from ...services.station import parse_station_sidecar
        sidecar = parse_station_sidecar(sidecar)
    sidecar_issues = []
    if sidecar is not None and sidecar is not False and document is not None:
        try:
            sidecar = migrate_sidecar(sidecar, named_stops, source_map)
        except StationSidecarError as exc:
            sidecar_issues.append(str(exc))
            sidecar = None
    if sidecar is not None and sidecar is not False:
        validate_station_sidecar(
            sidecar,
            named_stops,
            (group.id for group in groups),
            strict=True,
        )
        params = replace(
            params,
            station_merge=merge_params_with_sidecar(
                params.station_merge, sidecar
            ),
        )

    if document is not None:
        params = replace(params, station_merge=station_constraints(named_stops, params.station_merge))
    stations = group_stations(named_stops, group_of, params.station_merge)
    stations, physical_topologies, physical_legs, topology_issues = build_physical_topologies(
        mapdata, normalized.source_lines, named_stops, stations, group_of, params.station_merge)
    stations = resolve_station_names(mapdata, stations, named_stops, names_resolved=True)
    if sidecar is not None and sidecar is not False:
        applied = apply_station_fixes(
            stations, sidecar, strict=True, return_issues=True
        )
        stations = applied.stations
        sidecar_issues.extend(applied.issues)
    invariant_issues = validate_station_invariants(stations, params.station_merge)
    if invariant_issues:
        raise StationSidecarError(
            "站点模型校验失败", (*sidecar_issues, *invariant_issues)
        )
    facility_map = match_facilities(mapdata, stations)
    stations = tuple(replace(
        station,
        facilities=([facility_map[str(station.id)]]
                     if str(station.id) in facility_map else list(station.facilities)),
    ) for station in stations)

    routes_by_line = {route.line_id: route for route in normalized.routes}
    visible_groups = []
    for group in groups:
        members = [line for line in group.members if line.id in selected_ids]
        if members:
            visible_groups.append(replace(group, primary=members[0], aliases=tuple(members[1:]),
                                          overlay_ids=group.overlay_ids.intersection(selected_ids)))
    groups = tuple(visible_groups)
    derived_routes: list[Route] = []
    geometry_cache: dict = {}
    for group in groups:
        if document is not None:
            derived_routes.extend(replace(route, group_id=group.id)
                                  for route in normalized.routes
                                  if route.line_id in {line.id for line in group.members})
            continue
        primary_source = normalized.source_lines.get(group.primary.id)
        if primary_source is None:
            continue
        primary_route = routes_by_line.get(group.primary.id)
        if primary_route is None:
            continue
        loop = physical_topologies[group.primary.id].kind == "cycle"
        primary_geometry = resolve_route_geometry(
            mapdata, primary_source, group.primary.kind, cache=geometry_cache)
        if loop:
            derived_routes.append(replace(
                primary_route,
                id=_route_id(group.id, group.primary.id, Direction.LOOP),
                direction=Direction.LOOP,
                is_closed=True,
                geometry_kind=primary_geometry.kind,
                path_segment_ids=list(primary_geometry.path_segment_ids),
                group_id=group.id,
            ))
            continue
        for index, line in enumerate(group.members):
            source_route = routes_by_line.get(line.id, primary_route)
            derived_routes.append(replace(
                source_route,
                id=_route_id(group.id, line.id,
                            Direction.OUTBOUND if index == 0 else Direction.INBOUND),
                direction=(Direction.OUTBOUND if index == 0 else Direction.INBOUND),
                geometry_kind=primary_geometry.kind,
                path_segment_ids=list(primary_geometry.path_segment_ids),
                group_id=group.id,
            ))

    auto_colors = resolve_auto_colors(normalized.lines, groups, named_stops, params)
    visibility = build_visibility(params, [line.kind for line in normalized.lines])
    return TransitModel(
        job_params=params,
        lines=tuple(line for line in normalized.lines if line.id in selected_ids),
        groups=groups,
        routes=tuple(derived_routes),
        stops=tuple(replace(stop, serving_line_ids=[ident for ident in stop.serving_line_ids if ident in selected_ids])
                    for stop in named_stops if selected_ids.intersection(stop.serving_line_ids)),
        stations=tuple(replace(station,
                    member_stop_keys=[key for key in station.member_stop_keys
                                      if selected_ids.intersection(normalized.stop_by_key[key].serving_line_ids)],
                    member_stop_ids=[normalized.stop_by_key[key].id for key in station.member_stop_keys
                                      if selected_ids.intersection(normalized.stop_by_key[key].serving_line_ids)],
                    serving_line_ids=[ident for ident in station.serving_line_ids if ident in selected_ids],
                    serving_group_ids=[ident for ident in station.serving_group_ids
                                       if any(g.id == ident for g in groups)])
                       for station in stations if selected_ids.intersection(station.serving_line_ids)),
        group_of={line.id: group for group in groups for line in group.members},
        auto_colors=auto_colors,
        visibility=visibility,
        issues=(*tuple(str(issue) for issue in sidecar_issues),
                *(issue for issue in topology_issues if any(
                    issue.startswith((f"Line {ident}:", f"Line {ident},")) for ident in selected_ids))),
        physical_topologies={ident: value for ident, value in physical_topologies.items() if ident in selected_ids},
        physical_legs={ident: value for ident, value in physical_legs.items() if ident in selected_ids},
        source_lines={ident: line for ident, line in normalized.source_lines.items() if ident in selected_ids},
        station_fix_applied=bool(
            isinstance(sidecar, StationSidecar) and sidecar.edits
        ),
    )


# 保留站点领域的公开构建名称，让调用方无需知道后续推导属于哪个兼容模块。
build_station_model = build_transit_model
