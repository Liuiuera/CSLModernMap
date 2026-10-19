"""生成保护隐私的 CMM 数据子集。"""
from __future__ import annotations

from typing import Iterable

from .model import (
    DisplayName,
    Grid,
    MapDocument,
    ObjectRef,
    Polygon,
    Polyline,
    Vec2,
    Vec3,
)


_ANONYMOUS_SOURCE = "<anonymized>"
_ANONYMOUS_CITY = "<anonymized-city>"
_AREA_PATTERN_SOURCES = {"area_pattern", "area-pattern", "area pattern"}
_SMALL_BUILDING_AREA = 25.0


def _area_pattern(name: DisplayName) -> bool:
    return str(name.source or "").strip().casefold() in _AREA_PATTERN_SOURCES


def _anonymous_name(name: DisplayName) -> DisplayName:
    return name if _area_pattern(name) else DisplayName("", "empty")


def _round_grid(value: float, quantum: float | None) -> float:
    if quantum is None or quantum <= 0:
        return float(value)
    return round(float(value) / quantum) * quantum


def _v2(value: Vec2, dx: float, dz: float, quantum: float | None) -> Vec2:
    return Vec2(_round_grid(value.x - dx, quantum),
                _round_grid(value.z - dz, quantum))


def _v3(value: Vec3, dx: float, dz: float, quantum: float | None) -> Vec3:
    return Vec3(_round_grid(value.x - dx, quantum),
                _round_grid(value.y, quantum),
                _round_grid(value.z - dz, quantum))


def _polyline(value: Polyline, dx: float, dz: float,
              quantum: float | None, keep_geometry: bool) -> Polyline:
    if not keep_geometry:
        return Polyline()
    return Polyline(tuple(_v3(point, dx, dz, quantum) for point in value.points))


def _polygon(value: Polygon | None, dx: float, dz: float,
             quantum: float | None, keep_geometry: bool) -> Polygon | None:
    if value is None:
        return None
    if not keep_geometry:
        return Polygon()
    return Polygon(tuple(_v2(point, dx, dz, quantum) for point in value.points))


def _polygon_area(value: Polygon) -> float:
    points = value.points
    if len(points) < 3:
        return 0.0
    return abs(sum(
        points[index - 1].x * point.z - point.x * points[index - 1].z
        for index, point in enumerate(points)
    )) / 2.0


def _all_coordinates(document: MapDocument) -> Iterable[tuple[float, float]]:
    for node in document.nodes:
        yield node.pos.x, node.pos.z
    for segment in document.networks:
        for point in segment.geometry.points:
            yield point.x, point.z
    for building in document.buildings:
        for point in building.footprint.points:
            yield point.x, point.z
    for district in document.districts:
        for point in district.grid_points:
            yield point.x, point.z
        if district.boundary:
            for point in district.boundary.points:
                yield point.x, point.z
        if district.position:
            yield district.position.x, district.position.z
    for stop in document.stops:
        yield stop.position.x, stop.position.z
    for station in document.derived.stations:
        yield station.position.x, station.position.z
    for poi in document.derived.pois:
        yield poi.anchor.x, poi.anchor.z
    for body in document.derived.water_bodies:
        for point in body.polygon.points:
            yield point.x, point.z
    yield document.terrain.origin.x, document.terrain.origin.z


def _sample_grid(grid: Grid, factor: int) -> Grid:
    if factor <= 1 or grid.values is None or not grid.rows or not grid.cols:
        return grid
    row_indices = list(range(0, grid.rows, factor))
    col_indices = list(range(0, grid.cols, factor))
    values = grid.values[row_indices][:, col_indices]
    rows = len(row_indices)
    cols = len(col_indices)
    return Grid(rows=rows, cols=cols, origin=grid.origin,
                cell=grid.cell * factor, _values=values, axis=grid.axis)


def _mark_derived_refs(document: MapDocument, removed_buildings: set[int]) -> None:
    core_ids = {
        "node": {item.id for item in document.nodes},
        "network": {item.id for item in document.networks},
        "building": {item.id for item in document.buildings},
        "district": {item.id for item in document.districts},
        "stop": {item.id for item in document.stops},
        "line": {item.id for item in document.transit_lines},
        "route": {item.id for item in document.routes},
    }
    for poi in document.derived.pois:
        ref = poi.primary_ref
        if ref.kind in core_ids and ref.id not in core_ids[ref.kind]:
            poi.primary_ref = ObjectRef(ref.kind, ref.id, dropped=True)
        elif ref.kind == "building" and ref.id in removed_buildings:
            poi.primary_ref = ObjectRef(ref.kind, ref.id, dropped=True)
    for label in document.derived.labels:
        ref = label.ref
        if ref.kind in core_ids and ref.id not in core_ids[ref.kind]:
            label.ref_dropped = True
        elif ref.kind == "building" and ref.id in removed_buildings:
            label.ref_dropped = True


def anonymize(
    document: MapDocument,
    *,
    keep_geometry: bool = True,
    drop_small_buildings: bool = False,
    quantize: float | None = 0.5,
    terrain_subsample: int = 1,
) -> MapDocument:
    """返回匿名化且可渲染的文档副本；保留标识、枚举含义和相对几何，清除个人名称与源诊断信息。"""
    if not isinstance(document, MapDocument):
        raise TypeError("anonymize() expects a MapDocument")
    if quantize is not None and float(quantize) < 0:
        raise ValueError("quantize must be non-negative or None")
    factor = max(1, int(terrain_subsample))
    result = MapDocument.from_dict(document.to_dict())

    coords = list(_all_coordinates(result))
    dx = min((item[0] for item in coords), default=0.0)
    dz = min((item[1] for item in coords), default=0.0)

    result.metadata.anonymized = True
    result.metadata.origin.source_name = _ANONYMOUS_CITY
    result.metadata.counters = {
        "nodes": len(result.nodes),
        "networks": len(result.networks),
        "buildings": len(result.buildings),
        "districts": len(result.districts),
        "stops": len(result.stops),
        "transit_lines": len(result.transit_lines),
        "routes": len(result.routes),
    }
    result.issues = []
    # 交通快照和程序化对象含绝对坐标及身份信息，匿名文档中予以丢弃。
    result.traffic = None
    result.procedural_objects = []

    for node in result.nodes:
        node.pos = _v3(node.pos, dx, dz, quantize)
        node.source_raw = ""
    for segment in result.networks:
        segment.geometry = _polyline(segment.geometry, dx, dz, quantize, keep_geometry)
        segment.name = ""
        segment.display_name = ""
        segment.game_name = ""
        segment.source_raw = ""
    for line in result.transit_lines:
        line.name = ""
        line.branch = ""
        line.source_raw = ""
    for route in result.routes:
        route.source_raw = ""
    for stop in result.stops:
        stop.position = _v3(stop.position, dx, dz, quantize)
        stop.name = _anonymous_name(stop.name)
        stop.source_raw = ""
    for building in result.buildings:
        building.footprint = _polygon(
            building.footprint, dx, dz, quantize, keep_geometry)
        building.name = ""
        building.display_name = _anonymous_name(building.display_name)
        building.source_raw = ""
    for district in result.districts:
        district.grid_points = [_v2(point, dx, dz, quantize)
                                for point in district.grid_points]
        district.boundary = _polygon(
            district.boundary, dx, dz, quantize, keep_geometry)
        district.position = (_v2(district.position, dx, dz, quantize)
                             if district.position else None)
        district.name = _anonymous_name(district.name)
        district.source_raw = ""

    removed_buildings: set[int] = set()
    if drop_small_buildings:
        kept = []
        for building in result.buildings:
            if _polygon_area(building.footprint) < _SMALL_BUILDING_AREA:
                removed_buildings.add(building.id)
            else:
                kept.append(building)
        result.buildings = kept
        kept_ids = {item.id for item in kept}
        for building in result.buildings:
            if building.parent_id in removed_buildings:
                building.parent_id = None
            building.child_ids = [item for item in building.child_ids if item in kept_ids]

    for station in result.derived.stations:
        station.position = _v3(station.position, dx, dz, quantize)
        station.name = _anonymous_name(station.name)
        station.member_stop_ids = [item for item in station.member_stop_ids
                                   if result.stop(item) is not None]
        station.member_stop_keys = [item for item in station.member_stop_keys
                                    if any(stop.stop_key == item for stop in result.stops)]
        station.serving_line_ids = [item for item in station.serving_line_ids
                                    if result.line(item) is not None]
        if station.building_id in removed_buildings:
            station.building_id = None
        for facility in station.facilities:
            facility.name = _anonymous_name(facility.name)
            facility.building_ids = [item for item in facility.building_ids
                                     if item not in removed_buildings]
    for poi in result.derived.pois:
        poi.anchor = _v3(poi.anchor, dx, dz, quantize)
        poi.name = _anonymous_name(poi.name)
        poi.name_pool_key = ""
        poi.pool_key = ""
    for body in result.derived.water_bodies:
        body.polygon = _polygon(body.polygon, dx, dz, quantize, keep_geometry)
    for label in result.derived.labels:
        if label.source.lower() != "area_pattern":
            label.text = ""

    terrain = result.terrain
    terrain.origin = _v2(terrain.origin, dx, dz, quantize)
    terrain.height = _sample_grid(terrain.height, factor)
    terrain.water_depth = _sample_grid(terrain.water_depth, factor)
    terrain.height = Grid(
        rows=terrain.height.rows, cols=terrain.height.cols,
        origin=terrain.origin, cell=terrain.height.cell,
        _values=terrain.height.values, axis=terrain.height.axis)
    terrain.water_depth = Grid(
        rows=terrain.water_depth.rows, cols=terrain.water_depth.cols,
        origin=terrain.origin, cell=terrain.water_depth.cell,
        _values=terrain.water_depth.values, axis=terrain.water_depth.axis)
    terrain.rows = terrain.height.rows or terrain.rows
    terrain.cols = terrain.height.cols or terrain.cols
    terrain.source_subsampled = max(int(terrain.source_subsampled), factor)

    _mark_derived_refs(result, removed_buildings)
    result.metadata.counters.update({
        "nodes": len(result.nodes),
        "networks": len(result.networks),
        "buildings": len(result.buildings),
        "districts": len(result.districts),
        "stops": len(result.stops),
        "transit_lines": len(result.transit_lines),
        "routes": len(result.routes),
    })
    result.rebuild_index()
    result.validate()
    return result
