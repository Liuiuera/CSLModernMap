"""根据建筑和区域几何推导设施语义。"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from functools import cached_property
from math import ceil, floor
from typing import Any, Iterable, Mapping

from ...built_area import WORLD_MIN
from ...cmm import DisplayName, FacilityGroup, Polygon, SemanticLevel, Vec2, Vec3
from .classifier import functional_area_kind
from .mapping_registry import PoiMappingRegistry, load_mapping_registry

FACILITY_KINDS = frozenset({
    "airport", "campus", "university", "park", "zoo", "amusement",
    "nature_reserve", "ship", "cargo_harbor",
})
PRECINCT_KINDS = frozenset({"industry", "pedestrian"})
KIND_PRIORITY = {
    "airport": 0,
    "campus": 1,
    "university": 1,
    "park": 2,
    "zoo": 2,
    "amusement": 2,
    "nature_reserve": 2,
    "ship": 3,
    "cargo_harbor": 3,
    "industry": 3,
    "pedestrian": 4,
}


@dataclass(frozen=True)
class FacilityGroupCatalog:
    """保存确定性且只读的设施归属结果。"""

    groups: tuple[FacilityGroup, ...] = ()
    building_to_group: Mapping[int, str] = field(default_factory=dict)
    zone_to_group: Mapping[int, str] = field(default_factory=dict)
    building_zone_kind: Mapping[int, str] = field(default_factory=dict)
    building_zone_id: Mapping[int, int] = field(default_factory=dict)
    zone_cells: Mapping[tuple[int, int], tuple[int, ...]] = field(default_factory=dict)
    zone_cell_sizes: Mapping[int, float] = field(default_factory=dict)
    zone_boundaries: Mapping[int, Polygon] = field(default_factory=dict)
    issues: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "groups", tuple(sorted(
            self.groups, key=lambda item: str(item.id))))
        object.__setattr__(self, "building_to_group", {
            int(key): str(value) for key, value in self.building_to_group.items()
        })
        object.__setattr__(self, "zone_to_group", {
            int(key): str(value) for key, value in self.zone_to_group.items()
        })
        object.__setattr__(self, "building_zone_kind", {
            int(key): str(value) for key, value in self.building_zone_kind.items()
        })
        object.__setattr__(self, "building_zone_id", {
            int(key): int(value) for key, value in self.building_zone_id.items()
        })
        object.__setattr__(self, "zone_cells", {
            (int(key[0]), int(key[1])): tuple(sorted(set(int(item) for item in value)))
            for key, value in self.zone_cells.items()
        })
        object.__setattr__(self, "zone_cell_sizes", {
            int(key): float(value) for key, value in self.zone_cell_sizes.items()
        })
        object.__setattr__(self, "zone_boundaries", {
            int(key): value if isinstance(value, Polygon) else Polygon.from_dict(value)
            for key, value in self.zone_boundaries.items()
        })
        object.__setattr__(self, "issues", tuple(str(item) for item in self.issues))

    @cached_property
    def by_id(self) -> dict[str, FacilityGroup]:
        return {str(item.id): item for item in self.groups}

    @cached_property
    def _zone_lookup(self):
        return _ZoneLookup(self.zone_boundaries, self.zone_cells, self.zone_cell_sizes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "groups": [item.to_dict() for item in self.groups],
            "building_to_group": {str(key): value
                                  for key, value in self.building_to_group.items()},
            "zone_to_group": {str(key): value
                              for key, value in self.zone_to_group.items()},
            "building_zone_kind": {str(key): value
                                    for key, value in self.building_zone_kind.items()},
            "building_zone_id": {str(key): value
                                  for key, value in self.building_zone_id.items()},
            "zone_cells": {
                f"{key[0]},{key[1]}": list(value)
                for key, value in self.zone_cells.items()
            },
            "zone_cell_sizes": {str(key): value
                                  for key, value in self.zone_cell_sizes.items()},
            "zone_boundaries": {str(key): value.to_dict()
                                for key, value in self.zone_boundaries.items()},
            "issues": list(self.issues),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "FacilityGroupCatalog":
        value = value or {}
        cells = {}
        for raw_key, cell_values in (value.get("zone_cells", {}) or {}).items():
            try:
                x, z = (int(item) for item in str(raw_key).split(",", 1))
            except (TypeError, ValueError):
                continue
            cells[(x, z)] = tuple(cell_values or ())
        return cls(
            groups=tuple(FacilityGroup.from_dict(item)
                         for item in value.get("groups", ())),
            building_to_group=value.get("building_to_group", {}),
            zone_to_group=value.get("zone_to_group", {}),
            building_zone_kind=value.get("building_zone_kind", {}),
            building_zone_id=value.get("building_zone_id", {}),
            zone_cells=cells,
            zone_cell_sizes=value.get("zone_cell_sizes", {}),
            zone_boundaries=value.get("zone_boundaries", {}),
            issues=tuple(value.get("issues", ())),
        )

    def group_for_building(self, building_id: int) -> FacilityGroup | None:
        group_id = self.building_to_group.get(int(building_id))
        return self.by_id.get(group_id) if group_id else None

    def group_for_point(self, x: float, z: float) -> FacilityGroup | None:
        """根据导出网格或精确区域边界判断点的归属。"""
        candidates: list[FacilityGroup] = []
        by_id = self.by_id
        for zone_id in self._zone_lookup.containing(float(x), float(z)):
            group = by_id.get(self.zone_to_group.get(zone_id))
            if group is None or group.semantic_level is not SemanticLevel.FACILITY:
                continue
            candidates.append(group)
        candidates = list({item.id: item for item in candidates}.values())
        return min(candidates, key=lambda item: (
            KIND_PRIORITY.get(item.kind, 99), int(item.source_zone_id or 0))) if candidates else None


@dataclass(frozen=True)
class _Zone:
    id: int
    kind: str
    name: str
    anchor: Vec3
    boundary: Polygon | None
    grid_points: tuple[tuple[float, float], ...]
    cell: float
    owner_building_id: int | None
    name_source: str = "empty"


def _text(value: Any) -> str:
    return str(getattr(value, "text", value) or "").strip()


def _compact(value: Any) -> str:
    return "".join(ch for ch in _text(value).casefold() if ch.isalnum())


def _id(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _xz(value: Any) -> tuple[float, float] | None:
    try:
        return float(getattr(value, "x")), float(getattr(value, "z"))
    except (AttributeError, TypeError, ValueError):
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            try:
                return float(value[0]), float(value[-1])
            except (TypeError, ValueError):
                return None
    return None


def _polygon(points: Iterable[Any]) -> Polygon | None:
    values = [_xz(item) for item in points or ()]
    values = [item for item in values if item is not None]
    if len(values) < 3:
        return None
    from ...cmm import Vec2
    return Polygon(tuple(Vec2(x, z) for x, z in values))


def _point_in_polygon(x: float, z: float, polygon: Polygon) -> bool:
    points = tuple((_xz(point) for point in polygon.points))
    points = tuple(point for point in points if point is not None)
    return _point_in_ring(x, z, points)


def _point_in_ring(x, z, points):
    if len(points) < 3:
        return False
    inside = False
    previous = points[-1]
    for current in points:
        x1, z1 = previous
        x2, z2 = current
        cross = (x - x1) * (z2 - z1) - (z - z1) * (x2 - x1)
        if abs(cross) <= 1e-6 and min(x1, x2) - 1e-6 <= x <= max(x1, x2) + 1e-6 \
                and min(z1, z2) - 1e-6 <= z <= max(z1, z2) + 1e-6:
            return True
        if (z1 > z) != (z2 > z):
            crossing_x = x1 + (z - z1) * (x2 - x1) / (z2 - z1)
            if crossing_x >= x:
                inside = not inside
        previous = current
    return inside


class _ZoneLookup:
    """只索引一次导出边界和网格；多数对象空间上不相交，避免建筑与全部多边形逐一比较。"""

    CELL = 256.0
    MAX_CELLS = 256

    def __init__(self, boundaries, grid_cells, cell_sizes):
        self.rings = {}
        self.buckets = defaultdict(list)
        self.large = []
        self.grid_cells = grid_cells
        self.cell_sizes = cell_sizes
        self.grid_sizes = sorted({size for size in cell_sizes.values() if size > 0})
        for zone_id, boundary in boundaries.items():
            points = tuple(p for point in boundary.points if (p := _xz(point)) is not None)
            if len(points) < 3:
                continue
            xs, zs = zip(*points)
            # 匹配包含边界的判断规则所用的取整容差。
            bounds = min(xs)-1e-6, min(zs)-1e-6, max(xs)+1e-6, max(zs)+1e-6
            self.rings[zone_id] = points, bounds
            x0, z0, x1, z1 = (floor(v/self.CELL) for v in bounds)
            if (x1-x0+1)*(z1-z0+1) > self.MAX_CELLS:
                self.large.append(zone_id)
            else:
                for gx in range(x0, x1+1):
                    for gz in range(z0, z1+1):
                        self.buckets[gx, gz].append(zone_id)

    def containing(self, x, z):
        found = set()
        for cell in self.grid_sizes:
            key = _grid_key(x, z, cell)
            found.update(zone_id for zone_id in self.grid_cells.get(key, ())
                         if self.cell_sizes.get(zone_id) == cell)
        candidates = self.buckets.get((floor(x/self.CELL), floor(z/self.CELL)), ())
        for zone_id in (*candidates, *self.large):
            if zone_id in found:
                continue
            points, (x0, z0, x1, z1) = self.rings[zone_id]
            if x0 <= x <= x1 and z0 <= z <= z1 and _point_in_ring(x, z, points):
                found.add(zone_id)
        return found


def _polygon_area(polygon: Polygon | None) -> float:
    if polygon is None or len(polygon.points) < 3:
        return 0.0
    points = [(_xz(point) or (0.0, 0.0)) for point in polygon.points]
    return abs(sum(
        points[index][0] * points[(index + 1) % len(points)][1]
        - points[(index + 1) % len(points)][0] * points[index][1]
        for index in range(len(points))) * 0.5)


def _centre(points: Iterable[Any], fallback: Any | None = None) -> Vec3:
    values = [_xz(item) for item in points or ()]
    values = [item for item in values if item is not None][:4]
    if values:
        return Vec3(sum(x for x, _ in values) / len(values), 0.0,
                    sum(z for _, z in values) / len(values))
    value = _xz(fallback)
    return Vec3(value[0], 0.0, value[1]) if value else Vec3(0, 0, 0)


def _zone_values(mapdata: Any) -> Iterable[Any]:
    document = getattr(mapdata, "_document", mapdata)
    if hasattr(document, "park_zones"):
        yield from (zone for zone in document.park_zones if not zone.is_background)
        return
    parks = getattr(mapdata, "parks", None)
    if parks:
        yield from parks
        return
    for item in getattr(mapdata, "districts", ()) or ():
        if _compact(getattr(item, "kind", "")) == "parkzone":
            yield item


def _zone_from(value: Any, default_cell: float) -> _Zone | None:
    raw_kind = (getattr(value, "ptype", "") or getattr(value, "source_raw", "")
                or getattr(value, "zone_kind", "") or getattr(value, "type_label", ""))
    kind = functional_area_kind(raw_kind)
    if not kind:
        kind = functional_area_kind(getattr(value, "zone_kind", ""))
    # CS1 原生 CMM 用负标识保留功能区。
    zone_id = int(value.id)
    if zone_id == 0:
        return None
    boundary_value = getattr(value, "boundary", None)
    points = getattr(value, "points", None)
    if points is None and boundary_value is not None:
        points = getattr(boundary_value, "points", ())
    boundary = _polygon(points or ())
    grid_values = []
    for point in getattr(value, "grid_points", ()) or ():
        pair = _xz(point)
        if pair is not None:
            grid_values.append(pair)
    fallback = getattr(value, "position", None)
    anchor = (_centre((), fallback=fallback) if fallback is not None
              else _centre(points or grid_values))
    cell = float(getattr(value, "grid_cell", 0.0) or default_cell or 0.0)
    raw_name = getattr(value, "name", "")
    name = _text(raw_name)
    if name.casefold().startswith("area_pattern"):
        name = ""
    name_source = (_text(getattr(raw_name, "source", ""))
                   or _text(getattr(value, "name_source", "")))
    if not name:
        name_source = "empty"
    elif name_source not in {"custom", "game"}:
        # 旧 MapData 的名称只是普通文本；维持旧版游戏名称解释，同时尊重 CMM 显式来源。
        name_source = "game"
    return _Zone(
        zone_id, kind, name, anchor, boundary, tuple(grid_values), cell,
        _id(getattr(value, "owner_building_id", None)), name_source)


def _building_points(building: Any) -> tuple[Any, ...]:
    points = getattr(building, "points", None)
    if points is not None:
        return tuple(points)[:4]
    footprint = getattr(building, "footprint", None)
    return tuple(getattr(footprint, "points", ()) or ())[:4]


def facility_container_building_ids(
    mapdata: Any,
    catalog: FacilityGroupCatalog | None = None,
) -> set[int]:
    """只隐藏明确导出且有替代结构的容器；旧文件缺少几何来源，不能猜测后删除占地。"""
    buildings = {int(item.id): item
                 for item in (getattr(mapdata, "buildings", ()) or ())}
    return {
        building_id for building_id, building in buildings.items()
        if getattr(building, "geometry_source", "") == "cs2:structure-container"
        and any(
            getattr(buildings.get(int(child)), "geometry_source", "")
            == "cs2:mesh-bounds"
            and _polygon_area(_polygon(_building_points(buildings[int(child)]))) > 0
            for child in getattr(building, "child_ids", ()))
    }


def _building_display_name(building: Any) -> DisplayName:
    custom = _text(getattr(building, "custom_name", ""))
    if custom:
        return DisplayName(custom, "custom")
    display = getattr(building, "display_name", None)
    text = _text(display)
    if not text:
        return DisplayName()
    source = _text(getattr(display, "source", "")) or _text(
        getattr(building, "name_source", ""))
    return DisplayName(text, source if source in {"custom", "game"} else "game")


def _building_name(building: Any) -> str:
    return _building_display_name(building).text


def _samples(buildings: Iterable[Any]) -> tuple[tuple[float, float], ...]:
    result = []
    for building in buildings:
        points = _building_points(building)
        values = [_xz(point) for point in points]
        values = [item for item in values if item is not None]
        if values:
            result.append((sum(x for x, _ in values) / len(values),
                           sum(z for _, z in values) / len(values)))
            result.extend(values)
    return tuple(result)


def _grid_key(x: float, z: float, cell: float) -> tuple[int, int] | None:
    if cell <= 0:
        return None
    return (int(round((x - WORLD_MIN) / cell - 0.5)),
            int(round((z - WORLD_MIN) / cell - 0.5)))


def _parent_graph(buildings: list[Any]) -> tuple[dict[int, int], list[str]]:
    ids = {_id(getattr(item, "id", 0)) for item in buildings}
    ids.discard(None)
    parents: dict[int, int] = {}
    issues: list[str] = []
    for building in buildings:
        bid = _id(getattr(building, "id", 0))
        if bid is None:
            continue
        raw_parent = (getattr(building, "parent_building_id", "")
                      or getattr(building, "parent_id", None))
        parent = _id(raw_parent)
        if raw_parent and parent not in ids:
            issues.append(f"ORPHAN_PARENT:{bid}:{raw_parent}")
        elif parent is not None and parent != bid:
            parents[bid] = parent
        raw_child = getattr(building, "sub_building_id", "")
        child_values = [_id(raw_child)] if raw_child else []
        child_values.extend(_id(item) for item in (getattr(building, "child_ids", ()) or ()))
        for child in child_values:
            if child is None:
                continue
            if child not in ids:
                issues.append(f"ORPHAN_CHILD:{bid}:{child}")
            elif child != bid and child not in parents:
                parents[child] = bid
    return parents, issues


def _components(buildings: list[Any]) -> tuple[dict[int, list[Any]], list[str]]:
    by_id = {int(item.id): item for item in buildings if _id(getattr(item, "id", 0)) is not None}
    parents, issues = _parent_graph(buildings)
    roots: dict[int, list[Any]] = defaultdict(list)
    for bid, building in sorted(by_id.items()):
        path: list[int] = []
        current = bid
        while current in parents:
            if current in path:
                cycle = path[path.index(current):]
                root = min(cycle)
                issues.append("PARENT_CYCLE:" + ":".join(str(item) for item in sorted(cycle)))
                break
            path.append(current)
            current = parents[current]
        else:
            root = current
        roots[root].append(building)
    return {root: sorted(items, key=lambda item: int(item.id))
            for root, items in roots.items()}, issues


def _make_group_id(level: SemanticLevel, source_id: int) -> str:
    prefix = "composite" if level is SemanticLevel.COMPOSITE_BUILDING else (
        "facility" if level is SemanticLevel.FACILITY else "area")
    return f"{prefix}:park:{int(source_id)}"


def build_facility_groups(
    mapdata: Any,
    *,
    mapping: PoiMappingRegistry | None = None,
) -> FacilityGroupCatalog:
    """根据导出网格、边界和所有者构建设施归属。"""
    buildings = [item for item in (getattr(mapdata, "buildings", ()) or ())
                 if _id(getattr(item, "id", 0)) is not None]
    by_id = {int(item.id): item for item in buildings}
    default_cell = float(getattr(mapdata, "park_cell", 0.0) or 0.0)
    zones = [zone for zone in (_zone_from(item, default_cell)
                               for item in _zone_values(mapdata)) if zone is not None]
    zones = sorted({zone.id: zone for zone in zones}.values(),
                   key=lambda item: (KIND_PRIORITY.get(item.kind, 99), item.id))
    issues: list[str] = []
    has_grid = bool(zones and any(zone.grid_points and zone.cell > 0 for zone in zones))
    has_zone_geometry = bool(zones and any(
        (zone.grid_points and zone.cell > 0) or zone.boundary for zone in zones))
    if not has_zone_geometry:
        issues.append("PARK_GRID_MISSING")
    elif has_grid:
        issues.extend(
            f"PARK_GRID_PARTIAL:{zone.id}"
            for zone in zones
            if ((not zone.grid_points or zone.cell <= 0) and not zone.boundary)
        )

    by_cell: dict[tuple[int, int], list[int]] = defaultdict(list)
    zone_cell_sizes: dict[int, float] = {}
    zone_by_id = {zone.id: zone for zone in zones}
    for zone in zones:
        zone_cell_sizes[zone.id] = zone.cell
        for x, z in zone.grid_points:
            key = _grid_key(x, z, zone.cell)
            if key is not None:
                by_cell[key].append(zone.id)
    for key in by_cell:
        by_cell[key] = sorted(set(by_cell[key]))

    lookup = _ZoneLookup({zone.id: zone.boundary for zone in zones if zone.boundary},
                         by_cell, zone_cell_sizes)
    by_owner = defaultdict(list)
    for zone in zones:
        if zone.owner_building_id is not None:
            by_owner[zone.owner_building_id].append(zone)

    components, component_issues = _components(buildings)
    issues.extend(component_issues)
    component_zone: dict[int, _Zone] = {}
    owned_components: set[int] = set()
    for root, members in components.items():
        member_ids = {int(item.id) for item in members}
        # 结构组件根节点拥有设施，升级对象较小的区域不能替换港口或机场主体。
        owned = by_owner.get(root, ()) or [
            zone for member_id in member_ids for zone in by_owner.get(member_id, ())]
        if not owned:
            owned = [zone_by_id[area_id] for item in members
                     if (area_id := getattr(item, "area_id", None)) in zone_by_id]
        if owned:
            owned_components.add(root)
            component_zone[root] = min(
                owned, key=lambda zone: (KIND_PRIORITY.get(zone.kind, 99), zone.id))
            continue
        samples = _samples(members)
        scores: dict[int, int] = defaultdict(int)
        for x, z in samples:
            for zone_id in lookup.containing(x, z):
                scores[zone_id] += 1
        if scores and samples:
            threshold = ceil(len(samples) / 2)
            winners = [zone_by_id[zid] for zid, score in scores.items()
                       if score >= threshold]
            if winners:
                component_zone[root] = min(
                    winners, key=lambda zone: (KIND_PRIORITY.get(zone.kind, 99), zone.id))

    groups: list[FacilityGroup] = []
    building_to_group: dict[int, str] = {}
    zone_to_group: dict[int, str] = {}
    building_zone_kind: dict[int, str] = {}
    building_zone_id: dict[int, int] = {}

    grouped_zones: dict[tuple[str, int], list[_Zone]] = defaultdict(list)
    for zone in zones:
        key = (("owner", zone.owner_building_id)
               if zone.owner_building_id is not None else ("zone", zone.id))
        grouped_zones[key].append(zone)

    # 一个 CS2 设施可有多个不连通的子区域；各源区域保留可寻址性，同时归于同一语义父级。
    for key, owned_zones in sorted(grouped_zones.items(), key=lambda item: item[0]):
        zone = min(owned_zones, key=lambda item: (
            KIND_PRIORITY.get(item.kind, 99), item.id))
        level = (SemanticLevel.FACILITY if zone.kind in FACILITY_KINDS
                  else SemanticLevel.AREA)
        gid = (_make_group_id(level, zone.id) if key[0] == "zone" else
               f"{'facility' if level is SemanticLevel.FACILITY else 'area'}:building:{key[1]}")
        for owned_zone in owned_zones:
            zone_to_group[owned_zone.id] = gid
        boundary_zone = max(owned_zones, key=lambda item: _polygon_area(item.boundary))
        named_zone = min(owned_zones, key=lambda item: (
            item.name_source != "custom", not bool(item.name), item.id))
        groups.append(FacilityGroup(
            id=gid,
            semantic_level=level,
            kind=("university" if zone.kind == "campus" else zone.kind),
            name=DisplayName(named_zone.name, named_zone.name_source),
            anchor=zone.anchor,
            boundary=boundary_zone.boundary,
            source_zone_id=zone.id,
            source_zone_ids=[item.id for item in owned_zones],
        ))

    group_by_id = {item.id: item for item in groups}
    members_by_group: dict[str, list[int]] = defaultdict(list)
    roots_by_group: dict[str, list[int]] = defaultdict(list)
    for root, members in components.items():
        zone = component_zone.get(root)
        if zone is None:
            continue
        building_zone_kind.update({int(item.id): zone.kind for item in members})
        building_zone_id.update({int(item.id): zone.id for item in members})
        gid = zone_to_group.get(zone.id)
        if gid and root in owned_components:
            members_by_group[gid].extend(int(item.id) for item in members)
            roots_by_group[gid].append(root)

    for gid, group in group_by_id.items():
        group.member_building_ids = sorted(set(members_by_group.get(gid, ())))
        group.root_building_ids = sorted(set(roots_by_group.get(gid, ())))
        if group.semantic_level is SemanticLevel.FACILITY and not group.name.text:
            owner_id = zone_by_id[group.source_zone_id].owner_building_id
            owner = by_id.get(owner_id)
            if owner is not None:
                group.name = _building_display_name(owner)

    for root, members in components.items():
        if len(members) <= 1:
            zone = component_zone.get(root)
            gid = zone_to_group.get(zone.id) if zone is not None else None
            if zone is not None and root in owned_components and gid:
                building_to_group[int(members[0].id)] = gid
            continue
        zone = component_zone.get(root)
        parent_gid = (zone_to_group.get(zone.id, "") if zone is not None
                      and root in owned_components else "")
        gid = f"composite:building:{int(root)}"
        root_name = _building_display_name(by_id.get(root, members[0]))
        groups.append(FacilityGroup(
            id=gid,
            semantic_level=SemanticLevel.COMPOSITE_BUILDING,
            kind="composite_building",
            name=root_name,
            anchor=_centre(_samples(members)),
            source_zone_id=(zone.id if zone is not None else None),
            member_building_ids=[int(item.id) for item in members],
            root_building_ids=[int(root)],
            parent_group_id=parent_gid,
        ))
        for item in members:
            building_to_group[int(item.id)] = gid

    # 导出归属优先；按结构组件解析后重建全部索引，重叠空间成员也只能有唯一 POI 父级。
    derived = getattr(mapdata, "derived", None)
    exported_groups = [FacilityGroup.from_dict(item.to_dict())
                       if isinstance(item, FacilityGroup) else FacilityGroup.from_dict(item)
                       for item in (getattr(derived, "facility_groups", ()) or ())]
    if exported_groups:
        authoritative = {item.id: item for item in exported_groups}
        local = {item.id: item for item in groups}
        aliases = {}
        for group in exported_groups:
            previous = local.get(group.id) or next((item for item in groups
                if item.semantic_level is not SemanticLevel.COMPOSITE_BUILDING
                and set(item.source_zone_ids).intersection(group.source_zone_ids)), None)
            if previous:
                if previous.name.source == "custom" and group.name.source != "custom":
                    group.name = previous.name
                aliases[previous.id] = group.id
                group.source_zone_ids = sorted(set(group.source_zone_ids)
                                               | set(previous.source_zone_ids))
        zone_to_group = {zid: aliases.get(gid, gid) for zid, gid in zone_to_group.items()}
        building_to_group = {bid: aliases.get(gid, gid) for bid, gid in building_to_group.items()}
        for group in groups:
            group.parent_group_id = aliases.get(group.parent_group_id, group.parent_group_id)
        # 相同源区域的旧版推断分组也需替换。
        exported_zone_ids = {zid for group in exported_groups for zid in group.source_zone_ids}
        groups = [item for item in groups
                  if item.id not in authoritative
                  and (item.semantic_level is SemanticLevel.COMPOSITE_BUILDING
                       or not exported_zone_ids.intersection(item.source_zone_ids))]
        groups.extend(exported_groups)
        group_by_id = {item.id: item for item in groups}
        candidates: dict[int, list[FacilityGroup]] = defaultdict(list)
        for group in exported_groups:
            for zone_id in group.source_zone_ids:
                zone_to_group[zone_id] = group.id
            for building_id in set(group.member_building_ids) | set(group.root_building_ids):
                candidates[building_id].append(group)
        for root, members in components.items():
            choices = {g.id: g for b in members for g in candidates.get(int(b.id), ())}
            if not choices:
                continue
            winner = min(choices.values(), key=lambda g: (
                0 if root in g.root_building_ids else 1,
                0 if g.semantic_level is SemanticLevel.FACILITY else 1,
                KIND_PRIORITY.get(g.kind, 99), g.id))
            composite = group_by_id.get(f"composite:building:{root}")
            parent_id = winner.id if winner.source_zone_ids or winner.semantic_level is SemanticLevel.FACILITY else ""
            if composite:
                composite.parent_group_id = parent_id
                composite.source_zone_id = winner.source_zone_id
                composite.source_zone_ids = list(winner.source_zone_ids)
            for building in members:
                bid = int(building.id)
                building_zone_kind[bid] = winner.kind
                if winner.source_zone_id is not None:
                    building_zone_id[bid] = winner.source_zone_id
                building_to_group.pop(bid, None)
                if composite or parent_id:
                    building_to_group[bid] = composite.id if composite else parent_id
        # 升级对象自身分组从属于结构根节点。
        component_roots = {int(b.id): root for root, members in components.items() for b in members}
        for group in exported_groups:
            for bid in group.root_building_ids:
                root = component_roots.get(bid, bid)
                if root == bid:
                    continue
                composite = group_by_id.get(f"composite:building:{root}")
                if composite and composite.parent_group_id != group.id:
                    group.parent_group_id = composite.parent_group_id
                    break

    return FacilityGroupCatalog(
        groups=tuple(groups),
        building_to_group=building_to_group,
        zone_to_group=zone_to_group,
        building_zone_kind=building_zone_kind,
        building_zone_id=building_zone_id,
        zone_cells=by_cell,
        zone_cell_sizes=zone_cell_sizes,
        zone_boundaries={zone.id: zone.boundary for zone in zones
                         if zone.boundary is not None},
        issues=tuple(dict.fromkeys(issues)),
    )


def scoped_override(
    building: Any,
    catalog: FacilityGroupCatalog,
    mapping: PoiMappingRegistry,
) -> Any | None:
    """存在时返回受区域归属限制的建筑映射覆盖。"""
    bid = _id(getattr(building, "id", 0))
    if bid is None:
        return None
    area_kind = catalog.building_zone_kind.get(bid, "")
    if not area_kind:
        return None
    return mapping.lookup_area_override(area_kind, _building_name(building))
