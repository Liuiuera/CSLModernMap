"""MapV41Loader — 从 .mmap XML 直接构造 MapDocument。

目标数据流的唯一解析入口：

    .mmap XML → MapV41Loader → MapDocument

不经过 ``data.MapData``，不生成旧模型的 Segment/Building 对象；
类型识别（道路类别、宽度、交通方式）在读取时一次完成。
地形直接写入 ``numpy.ndarray(dtype=float32)``，逐项 Python float
转换被彻底移除。
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Mapping

import numpy as np

from .. import __version__
from ..parser import (
    TERRAIN_SIZE,
    _load_element_tree,
    _parse_bool,
    _parse_pos,
    _parse_points,
    _parse_zone_grid,
)
from ..road_hierarchy import classify_segment, segment_physical_width
from .model import (
    Building,
    DisplayName,
    District,
    Forest,
    GeomKind,
    Grid,
    LaneSummary,
    MapDocument,
    MapIssue,
    Metadata,
    NetworkKind,
    NetworkNode,
    NetworkSegment,
    Origin,
    Polygon,
    Polyline,
    ProceduralObject,
    RoadLevel,
    Route,
    SegmentMode,
    Stop,
    StopKind,
    Terrain,
    TransitLine,
    Vec2,
    Vec3,
)
from .map_v41_common import (
    AdapterCapabilities,
    _building_role,
    _display_name,
    _network_kind,
    _rgba_or_none,
    _stop_kind,
    _transit_kind,
    _zone_kind,
)
from ..i18n import tr


# CMM 文档的家族后缀。三个都要认：
#   .cmm.gz   —— 游戏内 Mod 现在的正式产物（恒 gzip 压缩）
#   .cmm      —— 手工解压后的形态，排障时有用
#   .cmm.json —— 向后兼容，早期导出的明文文件
CMM_FILE_SUFFIXES = (".cmm.json", ".cmm.gz", ".cmm")


class _SegmentTypeInfo:
    """轻量道路类型记录：只承载分类所需标志，不是旧模型对象。"""

    __slots__ = (
        "name", "highway", "road", "beautification", "width",
        "car_lane_count", "lane_dir", "rail", "tram", "metro",
        "monorail", "cablecar",
    )

    def __init__(self, name: str) -> None:
        self.name = name
        self.highway = False
        self.road = False
        self.beautification = False
        self.width = 0.0
        self.car_lane_count = 0
        self.lane_dir = 0
        self.rail = False
        self.tram = False
        self.metro = False
        self.monorail = False
        self.cablecar = False


class _SegmentRaw:
    """轻量路段记录：解析与分类之间的最小接口。"""

    __slots__ = (
        "id", "start_node_id", "end_node_id", "icls", "width",
        "name", "custom_name", "points", "path_seg_ids",
    )

    def __init__(self) -> None:
        self.id = 0
        self.start_node_id = 0
        self.end_node_id = 0
        self.icls = ""
        self.width = 0.0
        self.name = ""
        self.custom_name = ""
        self.points: list = []
        self.path_seg_ids: list[int] = []


# --------------------------------------------------------------------------
# XML 分段读取
# --------------------------------------------------------------------------


def _read_metadata(root) -> tuple[str, str, float]:
    city_name = ""
    generated = ""
    sea_level = 0.0
    city_elem = root.find("City")
    if city_elem is not None:
        city_name = (city_elem.text or "").strip()
    gen_elem = root.find("Generated")
    if gen_elem is not None:
        generated = (gen_elem.text or "").strip()
    sea_elem = root.find("SeaLevel")
    if sea_elem is not None:
        try:
            sea_level = float((sea_elem.text or "0").strip())
        except ValueError:
            sea_level = 0.0
    return city_name, generated, sea_level


def _read_segment_types(root) -> dict[str, _SegmentTypeInfo]:
    """解析 SegmentTypes/ST，逻辑与 parser.parse_segment_types 完全一致。"""
    result: dict[str, _SegmentTypeInfo] = {}
    sts_elem = root.find("SegmentTypes")
    if sts_elem is None:
        return result
    for st in sts_elem.findall("ST"):
        name = st.get("name", "")
        info = _SegmentTypeInfo(name)
        info.highway = st.get("highway", "false").strip().lower() == "true"
        has_ferry = False
        has_dir_f = False
        has_dir_b = False
        min_edge = float("inf")
        max_edge = float("-inf")
        for lane in st.findall("Lanes/Lane"):
            ltype = (lane.get("type") or "").strip()
            vtypes = [(v or "").strip() for v in (lane.get("vType") or "").split(",")]
            ldir = (lane.get("dir") or "").strip()
            try:
                pos = float(lane.get("pos", 0))
                width = float(lane.get("width", 0))
            except ValueError:
                continue
            if ltype == "Vehicle" and ("Car" in vtypes or "Train" in vtypes):
                if ldir == "Forward":
                    has_dir_f = True
                elif ldir == "Backward":
                    has_dir_b = True
            min_edge = min(min_edge, pos - width / 2)
            max_edge = max(max_edge, pos + width / 2)
            if ltype == "Vehicle":
                for vt in vtypes:
                    if vt in ("Car", "Plane"):
                        info.road = True
                        if vt == "Car":
                            info.car_lane_count += 1
                    elif vt == "Train":
                        info.rail = True
                    elif vt == "Tram":
                        info.tram = True
                    elif vt == "Metro":
                        info.metro = True
                    elif vt == "Monorail":
                        info.monorail = True
                    elif vt == "CableCar":
                        info.cablecar = True
                    elif vt == "Bicycle":
                        info.beautification = True
                    elif vt in ("Blimp", "Ferry"):
                        has_ferry = True
            elif ltype == "Pedestrian":
                info.beautification = True
        if has_ferry:
            continue
        if info.road or info.highway or info.rail or info.tram \
                or info.metro or info.monorail or info.cablecar:
            info.beautification = False
        info.lane_dir = 1 if (has_dir_f and not has_dir_b) else (
            -1 if (has_dir_b and not has_dir_f) else 0)
        info.width = max(int(max_edge - min_edge), 3) if max_edge > min_edge else 0.0
        result[name] = info
    return result


def _read_nodes(root) -> tuple[list[NetworkNode], dict[int, SegmentMode]]:
    """直接构造 NetworkNode，并记录节点级地上/地下模式用于路段判定。"""
    nodes: list[NetworkNode] = []
    mode_by_node: dict[int, SegmentMode] = {}
    nodes_elem = root.find("Nodes")
    if nodes_elem is None:
        return nodes, mode_by_node
    for ne in nodes_elem.findall("Node"):
        node_id = int(ne.get("id", 0))
        pe = ne.find("Pos")
        pos = _parse_pos(pe) if pe is not None else None
        ug = _parse_bool(ne.get("ug", "false"))
        og = _parse_bool(ne.get("og", "false"))
        mode = SegmentMode.TUNNEL if ug else (SegmentMode.GROUND if og else SegmentMode.ELEVATED)
        mode_by_node[node_id] = mode
        subsrv = ne.get("subsrv", "")
        nodes.append(NetworkNode(
            id=node_id,
            pos=Vec3(pos.x, pos.y, pos.z) if pos is not None else Vec3(0, 0, 0),
            mode_hint=mode,
            station_service=(_stop_kind(subsrv) if subsrv else None),
            source_raw=subsrv or "",
        ))
    return nodes, mode_by_node


def _read_segments(
    root,
    segment_types: Mapping[str, _SegmentTypeInfo],
    mode_by_node: Mapping[int, SegmentMode],
    pos_by_node: Mapping[int, Vec3],
) -> list[NetworkSegment]:
    """直接构造 NetworkSegment：类型识别在读取时一次完成。"""
    networks: list[NetworkSegment] = []
    segs_elem = root.find("Segments")
    if segs_elem is None:
        return networks
    for se in segs_elem.findall("Seg"):
        raw = _SegmentRaw()
        raw.id = int(se.get("id", 0))
        raw.start_node_id = int(se.get("sn", 0))
        raw.end_node_id = int(se.get("en", 0))
        raw.icls = se.get("icls", "")
        raw.width = float(se.get("width", 0))
        raw.custom_name = se.get("CustomName", se.get("customName", ""))
        name_elem = se.find("Name")
        if name_elem is not None:
            raw.name = (name_elem.text or "").strip()
        custom_elem = se.find("CustomName")
        if custom_elem is not None and (custom_elem.text or "").strip():
            raw.custom_name = custom_elem.text.strip()
        points_elem = se.find("Points")
        if points_elem is not None:
            raw.points = _parse_points(points_elem)
        path_elem = se.find("Path")
        if path_elem is not None:
            path_segs = path_elem.find("Segs")
            if path_segs is not None:
                raw.path_seg_ids = [
                    int(sg.text) for sg in path_segs.findall("Sg") if sg.text]

        seg_type = segment_types.get(raw.name) or segment_types.get(raw.icls)
        kind, transit_for = _network_kind(raw, seg_type)
        geometry_points = raw.points
        if not geometry_points:
            geometry_points = []
            for node_id in (raw.start_node_id, raw.end_node_id):
                point = pos_by_node.get(node_id)
                if point is not None:
                    geometry_points.append(point)
        level = None
        if kind in (NetworkKind.ROAD, NetworkKind.BEAUTIFICATION):
            legacy_level = classify_segment(raw, seg_type)
            level = RoadLevel[legacy_level.name]
        width = segment_physical_width(raw, seg_type)
        start_mode = mode_by_node.get(raw.start_node_id, SegmentMode.GROUND)
        end_mode = mode_by_node.get(raw.end_node_id, SegmentMode.GROUND)
        if start_mode is SegmentMode.TUNNEL or end_mode is SegmentMode.TUNNEL:
            mode = SegmentMode.TUNNEL
        elif start_mode is SegmentMode.ELEVATED or end_mode is SegmentMode.ELEVATED:
            mode = SegmentMode.ELEVATED
        else:
            mode = SegmentMode.GROUND
        networks.append(NetworkSegment(
            id=raw.id,
            kind=kind,
            start_id=raw.start_node_id,
            end_id=raw.end_node_id,
            geometry=Polyline(tuple(
                Vec3(p.x, p.y, p.z) for p in geometry_points)),
            mode=mode,
            width=width,
            level=level,
            lane_summary=(None if seg_type is None else LaneSummary(
                seg_type.car_lane_count, seg_type.lane_dir)),
            transit_for=transit_for,
            path_segment_ids=(list(raw.path_seg_ids)
                              if kind is NetworkKind.STOP_LINE else []),
            platform="platform" in (raw.name or "").casefold(),
            name=raw.name or "",
            display_name=raw.custom_name or "",
            game_name=se.findtext("GameName") or se.get("game_name", ""),
            source_raw=raw.name or "",
        ))
    return networks


def _read_buildings(root) -> list[Building]:
    buildings: list[Building] = []
    child_by_parent: dict[int, list[int]] = {}
    blds_elem = root.find("Buildings")
    if blds_elem is None:
        return buildings
    for be in blds_elem.findall("Buil"):
        building_id = int(be.get("id", 0))
        service = be.get("srv", "")
        sub_service = be.get("subsrv", "")
        icls = be.get("icls", "")
        parent_raw = be.get("prntb", be.get("ParentBuildingId", be.get("parentbld", "")))
        child_raw = be.get("subb", be.get("SubBuildingId", be.get("subbld", "")))
        parent_id = _as_int(parent_raw)
        child_id = _as_int(child_raw)
        if parent_id is not None:
            child_by_parent.setdefault(parent_id, []).append(building_id)
        if child_id is not None:
            child_by_parent.setdefault(building_id, []).append(child_id)
        points_elem = be.find("Points")
        points = _parse_points(points_elem) if points_elem is not None else []
        custom_name = be.get("myname", "")
        game_name = be.get("displayname", "")
        display_name = custom_name or game_name
        buildings.append(Building(
            id=building_id,
            name=be.get("name", ""),
            display_name=DisplayName(
                display_name,
                "custom" if custom_name else "game" if game_name else "empty"),
            role=_building_role(service, sub_service, icls),
            footprint=Polygon(tuple(Vec2(p.x, p.z) for p in points)),
            parent_id=parent_id,
            child_ids=[],
            source_raw=" ".join(item for item in (service, sub_service, icls) if item),
            service=service or "",
            sub_service=sub_service or "",
            icls=icls or "",
            sub_building_id=str(child_raw or ""),
            parent_building_id=str(parent_raw or ""),
            area_id=(-(int(be.get("park_id")) + 1) if be.get("park_id") not in (None, "0") else None),
            category_id=be.get("category_id", ""),
            category_name=be.get("category_name", ""),
            category_locale=be.get("category_locale", ""),
            growth_kind=be.get("growth_kind", "unknown"),
            growth_use=be.get("growth_use", ""),
        ))
    for building in buildings:
        building.child_ids = sorted(set(child_by_parent.get(building.id, [])))
    return buildings


def _read_districts(root) -> tuple[list[District], float, float]:
    districts: list[District] = []
    district_cell = 0.0
    park_cell = 0.0
    dists_elem = root.find("Districts")
    grid_by_id: dict[int, list[tuple[float, float]]] = {}
    grid_elem = root.find("DistrictGrid")
    if grid_elem is not None:
        grid_by_id, district_cell = _parse_zone_grid((grid_elem.text or "").strip())
    if dists_elem is not None:
        for de in dists_elem.findall("Dist"):
            district_id = int(de.get("id", 0))
            dtype = de.get("type", "")
            boundary_points = _parse_points(de)
            grid_points = [Vec2(x, z) for x, z in grid_by_id.get(district_id, ())]
            districts.append(District(
                id=district_id,
                name=_display_name(de.get("name", "")),
                kind="DISTRICT",
                zone_kind=_zone_kind(dtype),
                grid_points=grid_points,
                grid_cell=float(district_cell),
                boundary=(Polygon(tuple(Vec2(p.x, p.z) for p in boundary_points))
                          if boundary_points else None),
                position=(Vec2(boundary_points[0].x, boundary_points[0].z)
                          if boundary_points else (grid_points[0] if grid_points else None)),
                source_raw=dtype or "",
            ))
    parks_elem = root.find("Parks")
    park_grid_by_id: dict[int, list[tuple[float, float]]] = {}
    park_grid_elem = root.find("ParkGrid")
    if park_grid_elem is not None:
        park_grid_by_id, park_cell = _parse_zone_grid((park_grid_elem.text or "").strip())
    if parks_elem is not None:
        for pe in parks_elem.findall("Park"):
            park_id = int(pe.get("id", 0))
            ptype = ""
            te = pe.find("type")
            if te is not None:
                ptype = (te.text or "").strip()
            boundary_points = _parse_points(pe)
            grid_points = [Vec2(x, z) for x, z in park_grid_by_id.get(park_id, ())]
            districts.append(District(
                id=-(park_id + 1),
                name=_display_name(pe.get("name", "")),
                kind="PARK_ZONE",
                zone_kind=_zone_kind(ptype),
                grid_points=grid_points,
                grid_cell=float(park_cell),
                boundary=(Polygon(tuple(Vec2(p.x, p.z) for p in boundary_points))
                          if boundary_points else None),
                position=(Vec2(boundary_points[0].x, boundary_points[0].z)
                          if boundary_points else (grid_points[0] if grid_points else None)),
                owner_building_id=(int(pe.get("main_building_id")) if pe.get("main_building_id") not in (None, "0") else None),
                source_raw=ptype or "",
            ))
    return districts, float(district_cell), float(park_cell)


def _read_transports(
    root,
    mode_by_node: Mapping[int, SegmentMode],
    pos_by_node: Mapping[int, Vec3],
    service_by_node: Mapping[int, str],
) -> tuple[list[TransitLine], list[Stop], list[Route]]:
    lines: list[TransitLine] = []
    stops: list[Stop] = []
    routes: list[Route] = []
    trans_elem = root.find("Transports")
    if trans_elem is None:
        return lines, stops, routes
    stop_by_key: dict[tuple[int, StopKind], Stop] = {}
    used_stop_ids: set[int] = set()
    for te in trans_elem.findall("Trans"):
        color = (255, 255, 255, 255)
        color_elem = te.find("color")
        if color_elem is not None:
            color = (
                int(color_elem.get("a", 255)),
                int(color_elem.get("r", 255)),
                int(color_elem.get("g", 255)),
                int(color_elem.get("b", 255)),
            )
        transport_id = int(te.get("id", 0))
        transport_type = te.get("type", "")
        transit_kind = _transit_kind(transport_type)
        lines.append(TransitLine(
            id=transport_id,
            name=te.get("name", ""),
            kind=transit_kind,
            user_color=_rgba_or_none(color),
            bidirectional=None,
            branch="",
            source_raw=transport_type or "",
        ))
        route_stop_ids: list[int] = []
        stops_elem = te.find("Stops")
        if stops_elem is not None:
            for stop_elem in stops_elem.findall("Stop"):
                node_id = int(stop_elem.get("node", 0))
                node_service = service_by_node.get(node_id, "")
                stop_kind = _stop_kind(node_service, transit_kind)
                key = (node_id, stop_kind)
                stop = stop_by_key.get(key)
                if stop is None:
                    stop_id = node_id if node_id not in used_stop_ids else None
                    if stop_id is None:
                        candidate = node_id * 100 + list(StopKind).index(stop_kind) + 1
                        while candidate in used_stop_ids:
                            candidate += 1
                        stop_id = candidate
                    used_stop_ids.add(stop_id)
                    position = pos_by_node.get(node_id, Vec3(0, 0, 0))
                    stop = Stop(
                        id=stop_id,
                        network_node_id=node_id,
                        kind=stop_kind,
                        name=_display_name(stop_elem.get("name", "")),
                        position=position,
                        source_raw=node_service,
                    )
                    stop_by_key[key] = stop
                    stops.append(stop)
                elif not stop.name.text and stop_elem.get("name", ""):
                    stop.name = _display_name(stop_elem.get("name", ""))
                route_stop_ids.append(stop.id)
        is_closed = len(route_stop_ids) > 1 and route_stop_ids[0] == route_stop_ids[-1]
        routes.append(Route(
            id=transport_id * 2 + 1,
            line_id=transport_id,
            direction="loop" if is_closed else "outbound",
            stop_ids=route_stop_ids,
            is_closed=is_closed,
            geometry_kind=GeomKind.STOP_DIRECT,
            source_raw=transport_type or "",
        ))
    return lines, stops, routes


def _read_terrain(root) -> Terrain:
    """地形直接写入 numpy float32 数组，不产生百万级 Python float tuple。"""
    origin = Vec2(-8640.0, -8640.0)
    cell = 17280.0 / 1081.0
    rows = TERRAIN_SIZE
    cols = TERRAIN_SIZE
    terrains_root = root.find("Terrains")
    ter_elem = root.find("Terrains/Ter")
    if ter_elem is None:
        return Terrain(origin=origin, cell=cell, rows=rows, cols=cols)
    try:
        origin = Vec2(float(terrains_root.get("originX", "-8640")),
                      float(terrains_root.get("originZ", "-8640")))
        cell = float(terrains_root.get("cell", str(cell)))
        cols = int(terrains_root.get("cols", str(cols)))
        rows = int(terrains_root.get("rows", str(rows)))
        if rows <= 0 or cols <= 0 or cell <= 0:
            raise ValueError("rows, cols and cell must be positive")

        def components():
            for pair in (ter_elem.text or "").strip().split(","):
                height, depth = pair.split(":")
                yield int(height)
                yield int(depth)

        values = np.fromiter(components(), dtype=np.int32)
        total = rows * cols
        if values.size != total * 2:
            raise ValueError(f"expected {total} height:depth pairs, got {values.size // 2}")
        pairs = values.reshape(total, 2)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"Invalid Terrains/Ter: {error}") from error
    land = (pairs[:, 0] / 64.0).astype(np.float32)
    water = (pairs[:, 1] / 64.0).astype(np.float32)
    return Terrain(
        height=Grid(rows=rows, cols=cols, origin=origin, cell=cell,
                    _values=land.reshape(rows, cols)),
        water_depth=Grid(rows=rows, cols=cols, origin=origin, cell=cell,
                         _values=water.reshape(rows, cols)),
        origin=origin,
        cell=cell,
        rows=rows,
        cols=cols,
    )


def _read_forests(root) -> list[Forest]:
    forests: list[Forest] = []
    forests_elem = root.find("Forests")
    if forests_elem is None:
        return forests
    for fe in forests_elem.findall("Forest"):
        text = (fe.text or "").strip()
        data = [int(v) for v in text.split(",") if v.strip()]
        forests.append(Forest(data=data))
    return forests


def _read_procedural_objects(root) -> list[ProceduralObject]:
    items: list[ProceduralObject] = []
    pos_elem = root.find("ProceduralObjects")
    if pos_elem is None:
        return items
    for pe in pos_elem.findall("PO"):
        x = float(pe.get("x", 0))
        y = float(pe.get("y", 0))
        z = float(pe.get("z", 0))
        cx = pe.get("cx")
        cz = pe.get("cz")
        ex = pe.get("ex")
        ez = pe.get("ez")
        ry = float(pe.get("ry", "0"))
        scale = float(pe.get("s", "1"))
        if scale < 0.01:
            scale = 1.0
        cr_str = pe.get("cr")
        cg_str = pe.get("cg")
        cb_str = pe.get("cb")
        color = None
        if cr_str is not None and cg_str is not None and cb_str is not None:
            try:
                color = (int(cr_str) & 0xFF, int(cg_str) & 0xFF, int(cb_str) & 0xFF)
            except ValueError:
                color = None
        items.append(ProceduralObject(
            id=int(pe.get("id", 0)),
            pos=Vec3(x, y, z),
            center_x=float(cx) if cx else x,
            center_z=float(cz) if cz else z,
            extent_x=max(float(ex) if ex else 0.5, 0.1),
            extent_z=max(float(ez) if ez else 0.5, 0.1),
            rotation_y=ry,
            scale=scale,
            prefab_name=pe.get("pfn", "") or "",
            source_type=pe.get("src", "") or "",
            color_rgb=color,
        ))
    return items


def _as_int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# 加载器
# --------------------------------------------------------------------------


class MapV41Loader:
    """XML → MapDocument 直接构造器。"""

    def __init__(self) -> None:
        self._capabilities = AdapterCapabilities()

    def capabilities(self) -> AdapterCapabilities:
        return self._capabilities

    def load(
        self,
        source: str | Path,
        *,
        validate: bool = True,
        progress: Callable[[float, str], None] | None = None,
    ) -> MapDocument:
        """直接从源 XML 读取并构建标准地图文档。"""
        def report(pct: float, msg: str) -> None:
            if progress:
                progress(pct, msg)

        source_path = Path(source)
        issues: list[MapIssue] = []

        report(0.02, tr("gui.progress.load.xml"))
        root = _load_element_tree(str(source_path))
        version = (root.get("version") or "").strip()
        if version and version != "4.1":
            issues.append(MapIssue(
                "error", "VERSION_UNKNOWN",
                f"expected .mmap version 4.1, got {version}",
            ))
        elif not version:
            version = "4.1"
            issues.append(MapIssue(
                "info", "VERSION_DEFAULTED",
                "source has no version attribute; interpreted as v4.1",
            ))

        report(0.08, tr("gui.progress.load.meta"))
        city_name, generated, sea_level = _read_metadata(root)

        report(0.15, tr("gui.progress.load.road_types"))
        segment_types = _read_segment_types(root)

        report(0.25, tr("gui.progress.load.nodes"))
        nodes, mode_by_node = _read_nodes(root)
        pos_by_node = {node.id: node.pos for node in nodes}
        service_by_node = {node.id: node.source_raw for node in nodes}

        report(0.40, tr("gui.progress.load.segments"))
        networks = _read_segments(root, segment_types, mode_by_node, pos_by_node)

        report(0.55, tr("gui.progress.load.buildings"))
        buildings = _read_buildings(root)

        report(0.65, tr("gui.progress.load.zones"))
        districts, district_cell, park_cell = _read_districts(root)

        report(0.75, tr("gui.progress.load.transit"))
        lines, stops, routes = _read_transports(
            root, mode_by_node, pos_by_node, service_by_node)

        report(0.82, tr("gui.progress.load.terrain"))
        try:
            terrain = _read_terrain(root)
        except ValueError as error:
            raise ValueError(f"{source_path}: {error}") from error

        report(0.90, tr("gui.progress.load.props"))
        forests = _read_forests(root)
        procedural_objects = _read_procedural_objects(root)

        traffic = None
        traffic_elem = root.find("TrafficSnapshotJson")
        if traffic_elem is not None:
            json_text = (traffic_elem.text or "").strip()
            if json_text:
                try:
                    from ..traffic import TrafficSnapshot
                    traffic = TrafficSnapshot.load_from_string(json_text)
                    report(0.94, tr("gui.progress.load.traffic", count=len(traffic.segments)))
                except Exception as exc:
                    issues.append(MapIssue(
                        "warn", "TRAFFIC_PARSE",
                        f"embedded traffic snapshot parse failed: {exc}",
                    ))

        counters = {
            "nodes": len(nodes),
            "networks": len(networks),
            "buildings": len(buildings),
            "districts": len(districts),
            "stops": len(stops),
            "transit_lines": len(lines),
            "routes": len(routes),
            "district_cell": district_cell,
            "park_cell": park_cell,
        }
        report(0.96, tr("gui.progress.load.document"))
        extensions = {}
        tile_grid = root.find("MapTileGrid")
        if tile_grid is not None:
            try:
                extensions["map_tile_grid"] = {
                    "columns": int(tile_grid.get("columns", 0)),
                    "rows": int(tile_grid.get("rows", 0)),
                    "tile_size_m": float(tile_grid.get("tileSize", 0)),
                    "origin": [float(tile_grid.get("originX", 0)),
                               float(tile_grid.get("originZ", 0))],
                    "source": "game",
                }
            except (TypeError, ValueError):
                pass  # 旧版或第三方 XML 仍可能使用标准游戏网格。
        document = MapDocument(
            schema_version="cmm-v1",
            metadata=Metadata(
                origin=Origin("cs1", f"CSLModernMap {__version__}",
                              city_name or source_path.name),
                exported_at=generated,
                counters=counters,
                source_path=str(source_path.resolve()),
            ),
            sea_level=sea_level,
            nodes=nodes,
            networks=networks,
            buildings=buildings,
            districts=districts,
            stops=stops,
            transit_lines=lines,
            routes=routes,
            terrain=terrain,
            forests=forests,
            procedural_objects=procedural_objects,
            traffic=traffic,
            issues=issues,
            extensions=extensions,
        )
        if validate:
            document.validate()
        report(1.0, tr("gui.progress.load.done"))
        return document


def load_document(
    path: str | Path,
    progress: Callable[[float, str], None] | None = None,
    *,
    validate: bool = True,
) -> MapDocument:
    """统一解析入口：所有 GUI/CLI/SVG/Web 前端都调用此函数。"""
    source = Path(path)
    if source.name.casefold().endswith(CMM_FILE_SUFFIXES):
        if progress is not None:
            progress(0.1, tr("gui.progress.load.document"))
        document = MapDocument.from_json(source)
        document.metadata.source_path = str(source)
        for network in document.networks:
            if network.kind in (NetworkKind.ROAD, NetworkKind.BEAUTIFICATION) and network.level is None:
                raise ValueError(f"{source}: road {network.id} has no valid road level")
        if validate:
            document.validate()
        if progress is not None:
            progress(1.0, tr("gui.progress.load.done"))
        return document
    return MapV41Loader().load(path, validate=validate, progress=progress)
