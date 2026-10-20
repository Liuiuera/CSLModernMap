"""从地图数据纯函数式生成 POI 目录。"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable, Mapping, Sequence

from ...cmm import DisplayName, JobParams as TransitJobParams, ObjectRef, POI, SemanticLevel, Vec3
from ...building_names import is_unresolved_building_name
from ..transit import build_transit_model
from ..transit.name import resolve_stop_names
from .catalog import PoiCatalogDefinition, PoiKindSpec, load_catalog
from .mapping_registry import load_mapping_registry
from .direct_mapping import load_mapping, entry_key, game_for
from .merge import MergeProfile, merge, strong_name_key
from .model import POIJobParams, PoiCatalog
from .identity import resolve_identities
from .block_geometry import BlockGeometry
from .facility import FacilityGroupCatalog, build_facility_groups


FORMAL_SOURCES = frozenset({"building", "stop", "park"})
CATALOG_SOURCES = frozenset({*FORMAL_SOURCES, "traffic_light", "highway_gate"})


def _value_text(value: Any) -> str:
    return str(value or "").strip()


def _kind_value(value: Any) -> str:
    return _value_text(getattr(value, "value", value)).casefold()


def _source_id(value: Any) -> int:
    try:
        return int(getattr(value, "id", value))
    except (TypeError, ValueError):
        return 0


def _point_xy(value: Any) -> tuple[float, float]:
    return float(getattr(value, "x", 0.0)), float(getattr(value, "z", 0.0))


def _center(points: Iterable[Any], *, fallback: Any | None = None) -> Vec3:
    values = list(points or ())[:4]
    if values:
        xs = [float(getattr(item, "x", 0.0)) for item in values]
        zs = [float(getattr(item, "z", 0.0)) for item in values]
        ys = [float(getattr(item, "y", 0.0)) for item in values]
        return Vec3(sum(xs) / len(xs), sum(ys) / len(ys), sum(zs) / len(zs))
    if fallback is not None:
        x, z = _point_xy(fallback)
        return Vec3(x, float(getattr(fallback, "y", 0.0)), z)
    return Vec3(0.0, 0.0, 0.0)


def _building_points(building: Any) -> Sequence[Any]:
    points = getattr(building, "points", None)
    if points is not None:
        return points
    footprint = getattr(building, "footprint", None)
    return getattr(footprint, "points", ()) if footprint is not None else ()


def _building_fields(building: Any) -> tuple[str, str, str]:
    icls = _value_text(getattr(building, "icls", ""))
    service = _value_text(getattr(building, "service", ""))
    sub_service = _value_text(getattr(building, "sub_service", ""))
    if not any((icls, service, sub_service)):
        icls = _value_text(getattr(building, "source_raw", ""))
        if not icls:
            service = _building_role_service(building)
    return icls, service, sub_service


def _building_name(building: Any) -> DisplayName:
    custom = _value_text(getattr(building, "custom_name", ""))
    if custom:
        return DisplayName(custom, "custom")
    display = getattr(building, "display_name", None)
    text = _value_text(getattr(display, "text", display))
    if not text:
        return DisplayName()
    source = _value_text(getattr(display, "source", "")) or _value_text(
        getattr(building, "name_source", ""))
    return DisplayName(text, source if source in {"custom", "game"} else "game")


def _building_role_service(building: Any) -> str:
    role = _kind_value(getattr(building, "role", ""))
    return {
        "healthcare": "healthcare",
        "emergency": "emergency",
        "education": "education",
        "transit_station": "publictransport",
        "airport": "airport",
        "commercial": "commercial",
        "industrial": "industrial",
        "office": "office",
        "residential": "residential",
        "infrastructure": "infrastructure",
        "park_service": "park",
        "leisure": "leisure",
    }.get(role, "")


def _mapdata_node(mapdata: Any, node_id: int) -> Any | None:
    try:
        node = mapdata.node(int(node_id))
    except (AttributeError, TypeError, ValueError):
        node = None
    if node is not None:
        return node
    return next((item for item in getattr(mapdata, "nodes", ())
                 if int(getattr(item, "id", -1)) == int(node_id)), None)


def _explicit_name(value: Any) -> str:
    if isinstance(value, DisplayName):
        return value.text.strip()
    return _value_text(value)


def _resolve_name(
    explicit: Any,
) -> DisplayName:
    explicit_text = _explicit_name(explicit)
    if explicit_text:
        if isinstance(explicit, DisplayName):
            return explicit
        return DisplayName(explicit_text, "custom")
    return DisplayName()


def _priority(spec: PoiKindSpec, kind: str, params: POIJobParams) -> int:
    raw = params.priority_overrides.get(kind)
    if raw is None:
        return spec.priority
    if isinstance(raw, Mapping):
        if "absolute" in raw:
            return int(raw["absolute"])
        if "value" in raw and str(raw.get("mode", "delta")).casefold() in {"absolute", "abs"}:
            return int(raw["value"])
        raw = raw.get("delta", raw.get("value", 0))
    return spec.priority + int(raw)


def _overrides_for(spec: PoiKindSpec, kind: str, params: POIJobParams) -> tuple[bool, float, bool, bool]:
    enabled = spec.dedupe.enabled
    distance = spec.dedupe.distance_m
    same_name = spec.dedupe.same_name
    same_icon = spec.dedupe.same_icon
    raw = params.dedupe_overrides.get(kind)
    if isinstance(raw, Mapping):
        enabled = bool(raw.get("enabled", raw.get("merge_enabled", enabled)))
        distance = float(raw.get("distance_m", raw.get("distance", raw.get("merge_distance", distance))))
        same_name = bool(raw.get("same_name", same_name))
        same_icon = bool(raw.get("same_icon", same_icon))
    elif raw is not None:
        distance = float(raw)
    return enabled, max(distance, 0.0), same_name, same_icon


def _allowed(params: POIJobParams, source_kind: str, kind: str, spec: PoiKindSpec) -> bool:
    if spec.deprecated or params.scope not in spec.scopes:
        return False
    if params.scope == "transit":
        return source_kind == "stop"
    if params.scope == "metro":
        return False
    if source_kind not in CATALOG_SOURCES and source_kind != "custom":
        return False
    return True


def _make_poi(
    mapdata: Any,
    catalog: PoiCatalogDefinition,
    params: POIJobParams,
    *,
    poi_id: str,
    kind: str,
    source_kind: str,
    source_id: int,
    anchor: Vec3,
    explicit_name: Any = "",
    derived_from: Sequence[str] = (),
    priority: int | None = None,
    icon_key: str | None = None,
    semantic_level: str | SemanticLevel = SemanticLevel.BUILDING,
    group_id: str = "",
    parent_poi_id: str = "",
    area_id: int | None = None,
    min_zoom: float | None = None,
    source_raw: str = "",
) -> POI | None:
    spec = catalog.get(kind) or PoiKindSpec(
        kind, kind, kind, "", 50, 0.0,
        scopes=("normal", "transit", "svg"),
        draw_name=source_kind not in {"traffic_light", "highway_gate"})
    if not _allowed(params, source_kind, kind, spec):
        return None
    resolved_name = _resolve_name(explicit_name)
    enabled, distance, same_name, same_icon = _overrides_for(spec, kind, params)
    rule = "facility" if source_kind != "stop" else "stop"
    if source_kind == "stop":
        distance = 32.0
    dedupe_group = (
        f"{rule}:{int(distance)}m:kind+icon+name"
        if enabled and (same_name or same_icon)
        else ""
    )
    return POI(
        id=poi_id,
        kind=kind,
        category=kind,
        source=ObjectRef(source_kind, source_id),
        anchor=anchor,
        icon_key=icon_key or "",
        name=resolved_name,
        priority=_priority(spec, kind, params) if priority is None else int(priority),
        min_zoom=(params.min_zoom_overrides.get(kind, spec.min_zoom)
                  if min_zoom is None else float(min_zoom)),
        pool_key="",
        name_pool_key="",
        draw_name=spec.draw_name and bool(resolved_name.text),
        dedupe_group=dedupe_group,
        derived_from=tuple(derived_from) or (f"{source_kind}:{source_id}",),
        semantic_level=semantic_level,
        group_id=group_id,
        parent_poi_id=parent_poi_id,
        area_id=area_id,
        source_raw=source_raw,
    )


def _transport_stops(mapdata: Any, params: POIJobParams, *, session=None) -> tuple[Any, ...]:
    """读取 MapData 对应的 T02 标准化且已解析名称的站点结果。"""
    if hasattr(mapdata, "transports"):
        transit_params = TransitJobParams(
            view_all=True,
            enabled_types=params.transit_types,
            enabled_line_ids=params.transit_line_ids,
        )
        transit_model = (session.transit_model(transit_params) if session is not None
                         else build_transit_model(mapdata, transit_params))
        stops = transit_model.stops
        if abs(params.station_name_distance_m - 90.0) > 1e-9:
            stops = resolve_stop_names(
                mapdata, stops, max_distance=params.station_name_distance_m,
                name_index=(session.station_name_index() if session is not None else None),
            )
        # 旧线路投影会按网络节点和类型折叠站点；此处保留 CMM 源实体及不同的玩家名称。
        # 源名称为空时仍可使用 T02 解析结果回退。
        from ...cmm import normalize_stop_kind

        native_stops = tuple(getattr(mapdata, "stops", ()) or ())
        native_by_key = {}
        for native in native_stops:
            node_id = int(getattr(native, "network_node_id", -1))
            native_kind = normalize_stop_kind(getattr(native, "kind", ""))
            native_by_key.setdefault(
                (node_id, native_kind.value.casefold()), native)
        if native_stops and native_by_key:
            resolved_by_key = {}
            for stop in stops:
                key = (int(getattr(stop, "network_node_id", -1)),
                       normalize_stop_kind(
                           getattr(stop, "kind", "")).value.casefold())
                resolved_by_key.setdefault(key, stop)
            enriched = []
            selected_lines = {line.id for line in transit_model.lines}
            for native in native_stops:
                key = (int(getattr(native, "network_node_id", -1)),
                       normalize_stop_kind(
                           getattr(native, "kind", "")).value.casefold())
                resolved = resolved_by_key.get(key)
                serving = tuple(native.serving_line_ids) or tuple(
                    resolved.serving_line_ids if resolved is not None else ())
                if serving:
                    if not selected_lines.intersection(serving):
                        continue
                elif params.transit_line_ids is not None or params.transit_types is not None:
                    continue
                native_name = getattr(native, "name", DisplayName())
                native_text = str(getattr(native_name, "text", native_name) or "").strip()
                resolved_name = getattr(resolved, "name", DisplayName())
                if not native_text and resolved is not None:
                    enriched.append(replace(native, name=resolved_name, serving_line_ids=list(serving)))
                else:
                    enriched.append(replace(native, serving_line_ids=list(serving)))
            stops = tuple(enriched)
        return tuple(stops)
    return tuple(getattr(mapdata, "stops", ()) or ())


def _collect_buildings(
    mapdata: Any, catalog: PoiCatalogDefinition,
    params: POIJobParams, facility_catalog: FacilityGroupCatalog | None = None,
) -> list[POI]:
    facility_catalog = facility_catalog or FacilityGroupCatalog()
    result: list[POI] = []
    is_cs1 = game_for(mapdata) == "cs1"
    area_owners = {
        owner for park in _park_values(mapdata)
        if (owner := getattr(park, "owner_building_id", None)) is not None
        and _source_id(park) in facility_catalog.zone_to_group
    }
    for building in getattr(mapdata, "buildings", ()) or ():
        building_id = _source_id(building)
        group = facility_catalog.group_for_building(building_id)
        if building_id in area_owners:
            continue
        # 区域已代表主建筑时，明确的内部建筑仍保留各自 POI。
        if (group is not None
                and group.semantic_level is SemanticLevel.COMPOSITE_BUILDING
                and area_owners.intersection(group.root_building_ids)
                and group.parent_group_id):
            group = facility_catalog.by_id[group.parent_group_id]
        if (group is not None
                and group.semantic_level is SemanticLevel.COMPOSITE_BUILDING
                and building_id not in group.root_building_ids):
            continue
        name = _building_name(building)
        if is_cs1 and is_unresolved_building_name(name.text, name.source):
            continue
        native_id = _value_text(getattr(building, "category_id", ""))
        if not native_id:
            continue
        kind = entry_key(game_for(mapdata), native_id)
        points = _building_points(building)
        semantic_level = SemanticLevel.BUILDING
        group_id = ""
        parent_poi_id = ""
        area_id = facility_catalog.building_zone_id.get(building_id)
        min_zoom = None
        derived_from = (f"building:{building_id}",)
        if group is not None:
            group_id = group.id
            area_id = group.source_zone_id
            if group.semantic_level is SemanticLevel.COMPOSITE_BUILDING:
                derived_from = tuple(
                    f"building:{item}" for item in group.member_building_ids)
            if group.semantic_level is SemanticLevel.COMPOSITE_BUILDING:
                semantic_level = SemanticLevel.COMPOSITE_BUILDING
                parent_poi_id = group.parent_group_id
            elif group.semantic_level in {SemanticLevel.FACILITY, SemanticLevel.AREA}:
                parent_poi_id = group.id
            if parent_poi_id:
                min_zoom = max(
                    params.children_min_zoom,
                    params.min_zoom_overrides.get(kind,
                                                   (catalog.get(kind).min_zoom if catalog.get(kind) else 0.0)),
                )
        poi = _make_poi(
            mapdata, catalog, params,
            poi_id=f"b:{building_id}:{kind}", kind=kind,
            source_kind="building", source_id=building_id,
            anchor=_center(points), explicit_name=name,
            derived_from=derived_from,
            semantic_level=semantic_level,
            group_id=group_id,
            parent_poi_id=parent_poi_id,
            area_id=area_id,
            icon_key=kind,
            min_zoom=min_zoom,
        )
        if poi is not None:
            poi.growth_kind = getattr(building, "growth_kind", "unknown")
            poi.growth_use = getattr(building, "growth_use", "")
            result.append(poi)
    return result


def _collect_stops(
    mapdata: Any, catalog: PoiCatalog,
    params: POIJobParams, *, session=None,
) -> list[POI]:
    by_source: dict[tuple[int, int, str], list[POI]] = {}
    for stop in _transport_stops(mapdata, params, session=session):
        stop_id = _source_id(stop)
        node_id = int(getattr(stop, "network_node_id", getattr(stop, "node_id", stop_id)))
        kind = _kind_value(getattr(stop, "kind", ""))
        node = _mapdata_node(mapdata, node_id)
        anchor = getattr(stop, "position", None)
        if anchor is None and node is not None:
            anchor = getattr(node, "pos", None)
        anchor = anchor if isinstance(anchor, Vec3) else _center((), fallback=anchor)
        poi = _make_poi(
            mapdata, catalog, params,
            poi_id=f"s:{stop_id}:{kind}", kind=kind,
            source_kind="stop", source_id=stop_id, anchor=anchor,
            explicit_name=getattr(stop, "name", ""),
            derived_from=(f"stop:{stop_id}",),
            source_raw=_value_text(getattr(stop, "source_raw", "")),
            icon_key=entry_key(game_for(mapdata), "stop:" + _kind_value(getattr(stop, "kind", ""))),
        )
        if poi is None:
            continue
        poi.serving_line_ids = list(stop.serving_line_ids)
        # 网络节点只是空间锚点，不能当作源实体键；同节点的不同源标识仍需保留。
        # 若相同源标识有冲突的明确名称，仍须分别保留，避免有损投影抹掉名称保护。
        entity_key = (stop_id, node_id, kind)
        by_source.setdefault(entity_key, []).append(poi)

    result: list[POI] = []
    for candidates in by_source.values():
        named: dict[str, POI] = {}
        unnamed: list[POI] = []
        for poi in candidates:
            name_key = strong_name_key(poi)
            if name_key:
                previous = named.get(name_key)
                if previous is None or _representative_key(poi) < _representative_key(previous):
                    named[name_key] = poi
            else:
                unnamed.append(poi)
        if len(named) > 1:
            # 冲突的明确名称比同源身份的不完整重复记录更有证据力。
            result.extend(named.values())
        elif named:
            named_poi = next(iter(named.values()))
            result.append(min([named_poi, *unnamed], key=_representative_key))
        elif unnamed:
            result.append(min(unnamed, key=_representative_key))
    return list(sorted(result, key=lambda item: str(item.id)))


def _annotate_stop_groups(
    stops: Iterable[POI], facility_catalog: FacilityGroupCatalog,
    params: POIJobParams,
) -> list[POI]:
    result = []
    for poi in stops:
        group = facility_catalog.group_for_point(poi.anchor.x, poi.anchor.z)
        if group is not None:
            poi.area_id = group.source_zone_id
        result.append(poi)
    return result


def _park_values(mapdata: Any) -> Iterable[Any]:
    document = getattr(mapdata, "_document", mapdata)
    if hasattr(document, "park_zones"):
        yield from (zone for zone in document.park_zones if not zone.is_background)
        return
    parks = getattr(mapdata, "parks", None)
    if parks is not None:
        yield from parks
        return
    # MapDocument 将公园并入 PARK_ZONE 区划；适配器只读并仅选择明确标记的公园。
    for district in getattr(mapdata, "districts", ()) or ():
        if _kind_value(getattr(district, "kind", "")) == "park_zone":
            yield district


def _park_type(park: Any) -> str:
    raw = _value_text(getattr(park, "ptype", ""))
    return (raw or _value_text(getattr(park, "source_raw", ""))
            or _kind_value(getattr(park, "zone_kind", "")))


def _park_name(park: Any) -> DisplayName:
    raw_name = getattr(park, "name", "")
    name = _value_text(getattr(raw_name, "text", raw_name))
    if not name or name.casefold().startswith("area_pattern"):
        return DisplayName()
    source = _value_text(getattr(raw_name, "source", ""))
    return DisplayName(name, source or "game")


def _collect_parks(
    mapdata: Any, catalog: PoiCatalogDefinition,
    params: POIJobParams, facility_catalog: FacilityGroupCatalog | None = None,
) -> list[POI]:
    facility_catalog = facility_catalog or FacilityGroupCatalog()
    result: list[POI] = []
    game = game_for(mapdata)
    buildings = {_source_id(item): item for item in getattr(mapdata, "buildings", ())}
    for park in _park_values(mapdata):
        park_id = _source_id(park)
        owner_id = getattr(park, "owner_building_id", None)
        owner = buildings.get(owner_id)
        group_id = facility_catalog.zone_to_group.get(park_id, "")
        group = facility_catalog.by_id.get(group_id)
        # CS2 SubArea 是主建筑的几何片段；按既有设施身份选主体，不受邻近合并开关影响。
        cs2_owned_area = game == "cs2" and owner_id is not None and group is not None
        if cs2_owned_area and park_id != group.source_zone_id:
            continue
        native_id = _value_text(getattr(owner, "category_id", ""))
        if game == "cs1":
            icon_key = entry_key(game, f"park:{_park_type(park)}")
        else:
            icon_key = entry_key(game, native_id) if native_id else ""
        kind = icon_key or _park_type(park)
        position = getattr(park, "position", None)
        if position is None:
            raise ValueError(f"功能区 {park_id} 缺少游戏默认名称位置，无法生成区域 POI。")
        poi = _make_poi(
            mapdata, catalog, params,
            poi_id=f"p:{park_id}", kind=kind,
            source_kind="park", source_id=park_id,
            anchor=_center((), fallback=position), explicit_name=_park_name(park),
            semantic_level=SemanticLevel.AREA, area_id=park_id,
            source_raw=_park_type(park), icon_key=icon_key,
            group_id=group_id,
            parent_poi_id=group.parent_group_id if cs2_owned_area else "",
            derived_from=tuple(
                [f"park:{ident}" for ident in
                 (group.source_zone_ids if cs2_owned_area else (park_id,))]
                + ([f"building:{_source_id(owner)}"] if owner is not None else [])),
        )
        if poi is not None:
            result.append(poi)
    return result


def _collect_facility_parents(
    mapdata: Any, facility_catalog: FacilityGroupCatalog,
    catalog: PoiCatalogDefinition,
    params: POIJobParams,
) -> list[POI]:
    result: list[POI] = []
    buildings = {_source_id(b): b for b in getattr(mapdata, 'buildings', ()) or ()}
    parks = {_source_id(p): p for p in _park_values(mapdata)}
    for group in facility_catalog.groups:
        zone_ids = tuple(group.source_zone_ids) or (
            (group.source_zone_id,) if group.source_zone_id is not None else ())
        is_area = any(zone_id in parks for zone_id in zone_ids)
        if is_area or group.semantic_level is not SemanticLevel.FACILITY:
            continue
        zone_id = int(group.source_zone_id or 0)
        owner_id = (int(group.id.rsplit(':', 1)[1])
                    if group.id.startswith('facility:building:') else None)
        owner = buildings.get(owner_id)
        native_id = _value_text(getattr(owner, 'category_id', ''))
        icon_key = entry_key(game_for(mapdata), native_id) if native_id else ""
        kind = icon_key
        explicit_name = group.name
        poi = _make_poi(
            mapdata, catalog, params,
            poi_id=group.id, kind=kind, source_kind="park", source_id=zone_id,
            anchor=group.anchor, explicit_name=explicit_name,
            icon_key=icon_key,
            derived_from=tuple([f"park:{item}" for item in zone_ids] + [
                f"building:{item}" for item in group.member_building_ids
            ]),
            semantic_level=SemanticLevel.FACILITY,
            group_id=group.id,
            parent_poi_id=group.parent_group_id,
            area_id=group.source_zone_id,
        )
        if poi is not None:
            result.append(poi)
    return result


def _collect_custom(
    mapdata: Any, catalog: PoiCatalogDefinition, params: POIJobParams,
) -> list[POI]:
    result: list[POI] = []
    if params.scope == "transit":
        return result
    for index, item in enumerate(params.custom_pois or ()):
        kind = _value_text(item.get("kind", "other")) or "other"
        custom_id = item.get("id", index)
        try:
            source_id = int(custom_id)
        except (TypeError, ValueError):
            source_id = index
        position = item.get("position") or {}
        x = item.get("x", position.get("x", 0.0) if isinstance(position, Mapping) else 0.0)
        z = item.get("z", position.get("z", 0.0) if isinstance(position, Mapping) else 0.0)
        y = item.get("y", position.get("y", 0.0) if isinstance(position, Mapping) else 0.0)
        icon_key = _value_text(item.get("icon_key", "")) or None
        poi = _make_poi(
            mapdata, catalog, params,
            poi_id=f"c:{source_id}:{kind}", kind=kind,
            source_kind="custom", source_id=source_id,
            anchor=Vec3(float(x), float(y), float(z)),
            explicit_name=item.get("name", ""),
            priority=item.get("priority"), icon_key=icon_key,
            derived_from=(f"custom:{source_id}",),
        )
        if poi is not None:
            result.append(poi)
    return result


def _collect_lights(
    mapdata: Any, catalog: PoiCatalogDefinition,
    params: POIJobParams,
) -> list[POI]:
    """从共享世界坐标目录推导有效交通灯 POI。"""
    if params.scope == "transit":
        return []
    traffic = getattr(mapdata, "traffic", None)
    lights = getattr(traffic, "lights", ()) if traffic is not None else ()
    result: list[POI] = []
    for index, light in enumerate(lights or ()):
        if not bool(getattr(light, "active", True)):
            continue
        position = getattr(light, "position", None)
        if position is None:
            continue
        raw_id = getattr(light, "node_id", getattr(light, "id", index))
        try:
            source_id = int(raw_id)
        except (TypeError, ValueError):
            source_id = index
        poi = _make_poi(
            mapdata, catalog, params,
            poi_id=f"traffic_light:{source_id}:traffic_light",
            kind="traffic_light", source_kind="traffic_light",
            icon_key=entry_key(game_for(mapdata), "traffic_light"),
            source_id=source_id, anchor=_center((), fallback=position),
            derived_from=(f"traffic_light:{source_id}",),
        )
        if poi is not None:
            result.append(poi)
    return result


def _segment_type(mapdata: Any, segment: Any) -> Any | None:
    segment_types = getattr(mapdata, "segment_types", {}) or {}
    return (segment_types.get(getattr(segment, "name", ""))
            or segment_types.get(getattr(segment, "icls", "")))


def _collect_highway_gates(
    mapdata: Any, catalog: PoiCatalogDefinition,
    params: POIJobParams,
) -> list[POI]:
    """为位图和 SVG 消费者统一推导高速公路出入口 POI。"""
    if params.scope == "transit":
        return []
    segments = sorted(
        getattr(mapdata, "segments", ()) or (),
        key=lambda item: int(getattr(item, "id", 0)),
    )
    highway_count: dict[int, int] = {}
    road_count: dict[int, int] = {}
    for segment in segments:
        segment_type = _segment_type(mapdata, segment)
        if segment_type is None:
            continue
        targets = (int(segment.start_node_id), int(segment.end_node_id))
        counter = highway_count if bool(getattr(segment_type, "highway", False)) else road_count
        if bool(getattr(segment_type, "highway", False)) or bool(getattr(segment_type, "road", False)):
            for node_id in targets:
                counter[node_id] = counter.get(node_id, 0) + 1

    gates: dict[int, str] = {}
    for segment in segments:
        segment_type = _segment_type(mapdata, segment)
        if segment_type is None or not bool(getattr(segment_type, "highway", False)):
            continue
        start_id, end_id = int(segment.start_node_id), int(segment.end_node_id)
        lane_dir = int(getattr(segment_type, "lane_dir", 0) or 0)
        source_id, target_id = ((start_id, end_id) if lane_dir != -1 else (end_id, start_id))
        highway_at_source = highway_count.get(source_id, 0) - 1
        highway_at_target = highway_count.get(target_id, 0) - 1
        road_at_source = road_count.get(source_id, 0)
        road_at_target = road_count.get(target_id, 0)
        if highway_at_source >= 1 and highway_at_target == 0 and road_at_target >= 1:
            gates.setdefault(source_id, "highway_exit")
        elif highway_at_target >= 1 and highway_at_source == 0 and road_at_source >= 1:
            gates.setdefault(target_id, "highway_entrance")

    result: list[POI] = []
    for node_id, kind in sorted(gates.items()):
        node = _mapdata_node(mapdata, node_id)
        position = getattr(node, "pos", None) if node is not None else None
        if position is None:
            continue
        poi = _make_poi(
            mapdata, catalog, params,
            poi_id=f"highway_gate:{node_id}:{kind}", kind=kind,
            icon_key=entry_key(game_for(mapdata), kind),
            source_kind="highway_gate", source_id=node_id,
            anchor=_center((), fallback=position),
            derived_from=(f"highway_gate:{node_id}",),
        )
        if poi is not None:
            result.append(poi)
    return result


def _representative_key(poi: POI) -> tuple[int, int, str]:
    return (-int(poi.priority), 0 if poi.name.source == "custom" else 1, str(poi.id))


def produce(
    mapdata: Any,
    job_params: POIJobParams | Mapping[str, Any] | None = None,
    *,
    catalog: PoiCatalogDefinition | None = None,
    style: Mapping[str, Any] | None = None,
    override_path: str | None = None,
    session=None,
) -> PoiCatalog:
    """生成 POI 目录，不修改源 MapData 或其实体。"""
    if job_params is None:
        params = POIJobParams.from_style(style) if style is not None else POIJobParams()
    elif isinstance(job_params, POIJobParams):
        params = job_params
    else:
        params = POIJobParams(**dict(job_params))
    direct = load_mapping(override_path)
    definition = catalog or PoiCatalogDefinition(2, {})
    facility_mapping = load_mapping_registry()
    if params.scope == "metro":
        return PoiCatalog(
            pois=(), kinds=definition.active_kind_keys, scope=params.scope,
            params_fingerprint=params.fingerprint,
        )

    # 当前 POI 配置只控制设施子对象的展示，身份与分类始终完整推导。
    facility_catalog = (session.facility_catalog(facility_mapping, legacy=True)
                        if session is not None else
                        build_facility_groups(mapdata, mapping=facility_mapping))
    # 交通视图图标只来自线路站点；建筑仍供站名和设施服务使用。
    if params.scope == "transit":
        buildings, parks, facility_parents = [], [], []
    else:
        buildings = _collect_buildings(
            mapdata, definition, params, facility_catalog)
        parks = _collect_parks(
            mapdata, definition, params, facility_catalog)
        facility_parents = _collect_facility_parents(
            mapdata, facility_catalog, definition, params)
    stops = _annotate_stop_groups(
        _collect_stops(mapdata, definition, params, session=session),
        facility_catalog, params)
    lights = _collect_lights(mapdata, definition, params)
    highway_gates = _collect_highway_gates(mapdata, definition, params)
    custom = _collect_custom(mapdata, definition, params)
    active_profile = params.active_profile
    if active_profile:
        profile = MergeProfile(
            view=params.scope,
            merge_enabled=bool(active_profile.get("merge_enabled", True)),
            groups=dict(active_profile.get("groups", {}) or {}),
            allow_different_station_names=bool(
                active_profile.get("allow_different_station_names", False)),
            building_merge_enabled=params.building_merge_enabled,
            transit_merge_enabled=params.transit_merge_enabled,
            block_gap_m=params.block_gap_m,
            hub_core_m=params.hub_core_m,
            block_geometry=(BlockGeometry(mapdata, buildings)
                            if (params.building_merge_enabled
                                and bool(active_profile.get("merge_enabled", True)))
                            else None),
        )
    else:
        profile = MergeProfile(
            view=params.scope,
            building_merge_enabled=params.building_merge_enabled,
            transit_merge_enabled=params.transit_merge_enabled,
            block_gap_m=params.block_gap_m,
            hub_core_m=params.hub_core_m,
            category_rules=dict(params.dedupe_overrides or {}),
            block_geometry=BlockGeometry(mapdata, buildings)
            if params.building_merge_enabled else None,
        )
    buildings, stops = resolve_identities(mapdata, buildings, stops)
    if params.transit_types is not None:
        enabled = {_kind_value(kind) for kind in params.transit_types}
        stops = [p for p in stops if p.kind in enabled]
    merged = merge([*facility_parents, *buildings, *stops, *parks], profile)

    def included(poi):
        if poi.source.kind == "custom":
            return True
        entry = direct.get(poi.icon_key)
        if str(poi.semantic_level) == "AREA":
            return entry is None or entry["display"]
        return entry is not None and entry["display"] and any(entry["icons"].values())

    return PoiCatalog(
        pois=tuple(poi for poi in [*merged, *lights, *highway_gates, *custom]
                   if included(poi)),
        kinds=tuple(direct),
        scope=params.scope,
        params_fingerprint=params.fingerprint,
        issues=tuple(facility_catalog.issues) + tuple(
            f"功能区 {poi.source.id} 缺少图标映射（{poi.icon_key or '主建筑原生条目 ID 缺失'}），未生成图标。"
            for poi in parks if poi.icon_key not in direct
        ) + ((
            "建筑缺少原生条目 ID，请使用新版导出器重新导出地图。",)
            if any(not _value_text(getattr(b, "category_id", ""))
                   for b in getattr(mapdata, "buildings", ())) else ()),
        facility_groups=tuple(facility_catalog.groups),
    )
