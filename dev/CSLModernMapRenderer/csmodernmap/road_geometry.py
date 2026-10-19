"""统一处理道路拓扑、路口网格和局部高低顺序。

本模块不依赖具体渲染器，依据导出的三维道路中心线生成世界坐标图元，再由栅格和 SVG 后端分别投影与着色。"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Iterable

from .cmm.model import (
    MapDocument,
    NetworkKind,
    NetworkSegment,
    Polygon,
    RoadLevel as CmmRoadLevel,
)
from .data import MapData, Point, Segment, SegmentType
from .road_hierarchy import (
    RoadLevel,
    classify_segment,
    road_level_rank,
    segment_physical_width,
)


GRID_SIZE = 32.0
HEIGHT_EPSILON = 1.5
_EPS = 1e-7

# CMM RoadLevel → 渲染道路层级（同名枚举，逐成员映射）。
_CMM_LEVEL_MAP = {
    CmmRoadLevel.HIGHWAY: RoadLevel.HIGHWAY,
    CmmRoadLevel.MAIN: RoadLevel.MAIN,
    CmmRoadLevel.BRANCH: RoadLevel.BRANCH,
    CmmRoadLevel.SPECIAL: RoadLevel.SPECIAL,
    CmmRoadLevel.BEAUTIFICATION: RoadLevel.BEAUTIFICATION,
}


@dataclass(frozen=True)
class RoadRecord:
    segment: Segment
    segment_type: SegmentType
    mode: str
    points: tuple[Point, ...]
    bbox: tuple[float, float, float, float]
    mean_y: float
    length: float
    height_valid: bool
    level: RoadLevel

    @property
    def id(self) -> int:
        return self.segment.id

    @property
    def width(self) -> float:
        return segment_physical_width(self.segment, self.segment_type)

    @property
    def footprint(self) -> Polygon | None:
        if isinstance(self.segment, NetworkSegment):
            return self.segment.footprint
        return None

    @property
    def start_node_id(self) -> int:
        """端点节点 ID（兼容 legacy Segment 与 CMM NetworkSegment）。"""
        value = getattr(self.segment, "start_node_id", None)
        return value if value is not None else self.segment.start_id

    @property
    def end_node_id(self) -> int:
        value = getattr(self.segment, "end_node_id", None)
        return value if value is not None else self.segment.end_id


@dataclass(frozen=True)
class CrossingEvent:
    lower_segment_id: int
    upper_segment_id: int
    x: float
    z: float
    lower_y: float
    upper_y: float
    lower_distance: float
    upper_distance: float
    span: float

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        r = self.span * 0.5
        return (self.x - r, self.z - r, self.x + r, self.z + r)


@dataclass(frozen=True)
class Junction:
    node_id: int
    segment_ids: tuple[int, ...]
    x: float
    z: float
    mean_y: float
    bbox: tuple[float, float, float, float]


@dataclass
class RoadScene:
    roads: list[RoadRecord] = field(default_factory=list)
    roads_by_id: dict[int, RoadRecord] = field(default_factory=dict)
    junctions: list[Junction] = field(default_factory=list)
    crossings: list[CrossingEvent] = field(default_factory=list)
    height_reliable: bool = False

    def roads_in(self, bbox) -> list[RoadRecord]:
        return [r for r in self.roads if _bbox_intersects(r.bbox, bbox)]

    def junctions_in(self, bbox) -> list[Junction]:
        return [j for j in self.junctions if _bbox_intersects(j.bbox, bbox)]

@dataclass(frozen=True)
class JunctionMesh:
    outer: tuple[tuple[tuple[float, float], ...], ...]
    inner: tuple[tuple[tuple[float, float], ...], ...]


@dataclass(frozen=True)
class _Edge:
    road_id: int
    edge_index: int
    a: Point
    b: Point
    start_distance: float
    length: float
    bbox: tuple[float, float, float, float]


def segment_mode(mapdata: MapData, seg: Segment) -> str:
    """按导出节点状态区分隧道、高架和地面；og 表示接地，局部上下关系仍由高度决定。"""
    n0 = mapdata.node(seg.start_node_id)
    n1 = mapdata.node(seg.end_node_id)
    underground = bool((n0 and n0.ug) or (n1 and n1.ug))
    on_ground = bool((n0.og if n0 else True) and (n1.og if n1 else True))
    if underground:
        return "tunnel"
    if not on_ground:
        return "elevated"
    return "ground"


def build_road_scene(source) -> RoadScene:
    """为一次渲染或导出构建拓扑和局部高度事件，支持标准 MapDocument 与过渡期 MapData 视图。"""
    if isinstance(source, MapDocument):
        return _build_scene_from_document(source)
    return _build_scene_from_mapdata(source)


def _cmm_classify(network) -> RoadLevel:
    return _CMM_LEVEL_MAP[network.level]


def _build_scene_from_document(document: MapDocument) -> RoadScene:
    """从 CMM 文档直接构建道路场景（不经旧模型往返）。"""
    nodes_by_id = {node.id: node for node in document.nodes}
    roads: list[RoadRecord] = []
    roads_by_id: dict[int, RoadRecord] = {}
    incidence: dict[int, list[int]] = {}
    any_height = False
    edges: list[_Edge] = []

    for network in document.networks:
        if network.kind not in (NetworkKind.ROAD, NetworkKind.BEAUTIFICATION):
            continue
        pts = tuple(network.geometry.points)
        if len(pts) < 2:
            start = nodes_by_id.get(network.start_id)
            end = nodes_by_id.get(network.end_id)
            pts = tuple(item.pos for item in (start, end) if item is not None)
        if len(pts) < 2:
            continue
        xs = [p.x for p in pts]
        zs = [p.z for p in pts]
        if network.footprint is not None:
            xs.extend(point.x for point in network.footprint.points)
            zs.extend(point.z for point in network.footprint.points)
        lengths = [math.hypot(b.x - a.x, b.z - a.z)
                   for a, b in zip(pts, pts[1:])]
        total = sum(lengths)
        if total <= _EPS:
            continue
        weighted_y = sum(
            ((a.y + b.y) * 0.5) * length
            for a, b, length in zip(pts, pts[1:], lengths)
        ) / total
        height_valid = any(abs(p.y) > 1e-4 for p in pts)
        any_height = any_height or height_valid
        record = RoadRecord(
            segment=network,
            segment_type=None,
            mode=network.mode.value.lower(),
            points=pts,
            bbox=(min(xs), min(zs), max(xs), max(zs)),
            mean_y=weighted_y,
            length=total,
            height_valid=height_valid,
            level=_cmm_classify(network),
        )
        roads.append(record)
        roads_by_id[network.id] = record
        incidence.setdefault(network.start_id, []).append(network.id)
        incidence.setdefault(network.end_id, []).append(network.id)
        distance = 0.0
        for idx, (a, b, length) in enumerate(zip(pts, pts[1:], lengths)):
            if length <= _EPS:
                continue
            edges.append(_Edge(
                road_id=network.id,
                edge_index=idx,
                a=a,
                b=b,
                start_distance=distance,
                length=length,
                bbox=(min(a.x, b.x), min(a.z, b.z),
                      max(a.x, b.x), max(a.z, b.z)),
            ))
            distance += length

    roads.sort(key=lambda r: r.id)
    height_reliable = any_height
    crossings = _find_crossings(edges, roads_by_id, height_reliable)

    junctions: list[Junction] = []
    for node_id, ids in incidence.items():
        unique_ids = tuple(sorted(set(ids)))
        if len(unique_ids) < 2:
            continue
        node = nodes_by_id.get(node_id)
        if node is None:
            continue
        widths = [roads_by_id[i].width for i in unique_ids if i in roads_by_id]
        if len(widths) < 2:
            continue
        max_width = max(widths)
        transition = _clamp(1.5 * max_width, 12.0, 30.0)
        endpoint_heights = []
        for segment_id in unique_ids:
            record = roads_by_id.get(segment_id)
            if record is None:
                continue
            if record.start_node_id == node_id:
                endpoint_heights.append(record.points[0].y)
            elif record.end_node_id == node_id:
                endpoint_heights.append(record.points[-1].y)
        mean_y = max(endpoint_heights) if endpoint_heights else node.pos.y
        junctions.append(Junction(
            node_id=node_id,
            segment_ids=unique_ids,
            x=node.pos.x,
            z=node.pos.z,
            mean_y=mean_y,
            bbox=(node.pos.x - transition, node.pos.z - transition,
                  node.pos.x + transition, node.pos.z + transition),
        ))
    junctions.sort(key=lambda j: (j.mean_y, j.node_id))
    return RoadScene(
        roads=roads,
        roads_by_id=roads_by_id,
        junctions=junctions,
        crossings=crossings,
        height_reliable=height_reliable,
    )


def _build_scene_from_mapdata(mapdata: MapData) -> RoadScene:
    """供尚未迁移到 MapDocument 的 SVG 和城市卡片使用的旧版路径。"""
    roads: list[RoadRecord] = []
    roads_by_id: dict[int, RoadRecord] = {}
    incidence: dict[int, list[int]] = {}
    any_height = False
    edges: list[_Edge] = []

    for seg in mapdata.segments:
        st = mapdata.segment_types.get(seg.name) or mapdata.segment_types.get(seg.icls)
        if st is None or not (st.road or st.highway or st.beautification):
            continue
        pts = tuple(seg.points)
        if len(pts) < 2:
            continue
        xs = [p.x for p in pts]
        zs = [p.z for p in pts]
        lengths = [math.hypot(b.x - a.x, b.z - a.z) for a, b in zip(pts, pts[1:])]
        total = sum(lengths)
        if total <= _EPS:
            continue
        weighted_y = sum(
            ((a.y + b.y) * 0.5) * length
            for a, b, length in zip(pts, pts[1:], lengths)
        ) / total
        height_valid = any(abs(p.y) > 1e-4 for p in pts)
        any_height = any_height or height_valid
        record = RoadRecord(
            segment=seg,
            segment_type=st,
            mode=segment_mode(mapdata, seg),
            points=pts,
            bbox=(min(xs), min(zs), max(xs), max(zs)),
            mean_y=weighted_y,
            length=total,
            height_valid=height_valid,
            level=classify_segment(seg, st),
        )
        roads.append(record)
        roads_by_id[seg.id] = record
        incidence.setdefault(seg.start_node_id, []).append(seg.id)
        incidence.setdefault(seg.end_node_id, []).append(seg.id)
        distance = 0.0
        for idx, (a, b, length) in enumerate(zip(pts, pts[1:], lengths)):
            if length <= _EPS:
                continue
            edges.append(_Edge(
                road_id=seg.id,
                edge_index=idx,
                a=a,
                b=b,
                start_distance=distance,
                length=length,
                bbox=(min(a.x, b.x), min(a.z, b.z), max(a.x, b.x), max(a.z, b.z)),
            ))
            distance += length

    roads.sort(key=lambda r: r.id)
    height_reliable = any_height
    crossings = _find_crossings(edges, roads_by_id, height_reliable)

    junctions: list[Junction] = []
    for node_id, ids in incidence.items():
        unique_ids = tuple(sorted(set(ids)))
        if len(unique_ids) < 2:
            continue
        node = mapdata.node(node_id)
        if node is None:
            continue
        widths = [roads_by_id[i].width for i in unique_ids if i in roads_by_id]
        if len(widths) < 2:
            continue
        max_width = max(widths)
        transition = _clamp(1.5 * max_width, 12.0, 30.0)
        # 高架和坡道的节点位置可能偏离末端曲线点；路口是共享路面，应取入射端最高表面避免外沿穿过路口。
        endpoint_heights = []
        for segment_id in unique_ids:
            record = roads_by_id.get(segment_id)
            if record is None:
                continue
            if record.segment.start_node_id == node_id:
                endpoint_heights.append(record.points[0].y)
            elif record.segment.end_node_id == node_id:
                endpoint_heights.append(record.points[-1].y)
        mean_y = max(endpoint_heights) if endpoint_heights else node.pos.y
        junctions.append(Junction(
            node_id=node_id,
            segment_ids=unique_ids,
            x=node.pos.x,
            z=node.pos.z,
            mean_y=mean_y,
            bbox=(node.pos.x - transition, node.pos.z - transition,
                  node.pos.x + transition, node.pos.z + transition),
        ))
    junctions.sort(key=lambda j: (j.mean_y, j.node_id))
    return RoadScene(
        roads=roads,
        roads_by_id=roads_by_id,
        junctions=junctions,
        crossings=crossings,
        height_reliable=height_reliable,
    )


def get_road_scene(mapdata: MapData, cache=None) -> RoadScene:
    """从调用方显式缓存中构建或获取道路场景；直接调用方得到全新且无副作用的结果。"""
    key = (len(mapdata.segments), len(mapdata.nodes),
           id(mapdata.segments), id(mapdata.segment_types))
    if cache is None:
        return build_road_scene(mapdata)
    cache_key = ("road_scene", key)
    scene = cache.get(cache_key)
    if scene is None:
        scene = build_road_scene(mapdata)
        cache[cache_key] = scene
    return scene


def road_sort_key(record: RoadRecord, height_reliable: bool = True):
    mode_rank = {"tunnel": 0, "ground": 1, "elevated": 2}.get(record.mode, 1)
    level_rank = road_level_rank(record.level)
    if height_reliable:
        return (record.mean_y, mode_rank, level_rank, record.id)
    return (mode_rank, level_rank, record.id)


def junction_primary(scene: RoadScene, junction: Junction) -> RoadRecord:
    """为路口核心选择确定性的样式归属。"""
    mode_rank = {"tunnel": 0, "ground": 1, "elevated": 2}
    candidates = [scene.roads_by_id[i] for i in junction.segment_ids
                  if i in scene.roads_by_id]
    return max(candidates, key=lambda r: (
        mode_rank.get(r.mode, 1), road_level_rank(r.level), r.width, r.id,
    ))


def build_junction_mesh(
    scene: RoadScene,
    junction: Junction,
    fill_width: Callable[[RoadRecord], float],
    border_width: Callable[[RoadRecord], float],
) -> JunctionMesh:
    """生成世界坐标中的渐缩分支和有界凸形节点核心，栅格与 SVG 共用拓扑。"""
    records = [scene.roads_by_id[i] for i in junction.segment_ids
               if i in scene.roads_by_id]
    if len(records) < 2:
        return JunctionMesh((), ())
    max_fill = max(max(fill_width(r), 0.1) for r in records)
    max_outer = max(max(fill_width(r) + border_width(r), 0.1) for r in records)
    transition = _clamp(1.5 * max(r.width for r in records), 12.0, 30.0)
    core_distance = min(max(max_fill * 0.5, 1.0), transition * 0.65)
    overlap = min(max(0.75, max_outer * 0.05), 2.0)

    inner_arms: list[tuple[tuple[float, float], ...]] = []
    outer_arms: list[tuple[tuple[float, float], ...]] = []
    inner_core_pts: list[tuple[float, float]] = []
    outer_core_pts: list[tuple[float, float]] = []

    for record in records:
        oriented = _points_away_from_node(record, junction.node_id)
        if len(oriented) < 2:
            continue
        core_center = _point_at_distance(oriented, core_distance)
        inner_center = _point_at_distance(oriented, max(core_distance - overlap, 0.0))
        outer_center = _point_at_distance(oriented, transition + overlap)
        ix, iz, itx, itz = inner_center
        ox, oz, otx, otz = outer_center
        cx, cz, ctx, ctz = core_center
        inner_fill_half = max_fill * 0.5
        outer_fill_half = max(fill_width(record), 0.1) * 0.5
        inner_outer_half = max_outer * 0.5
        outer_outer_half = max(fill_width(record) + border_width(record), 0.1) * 0.5
        inner_fill_section = _section(ix, iz, itx, itz, inner_fill_half)
        outer_fill_section = _section(ox, oz, otx, otz, outer_fill_half)
        inner_outer_section = _section(ix, iz, itx, itz, inner_outer_half)
        outer_outer_section = _section(ox, oz, otx, otz, outer_outer_half)
        core_fill_section = _section(cx, cz, ctx, ctz, inner_fill_half)
        core_outer_section = _section(cx, cz, ctx, ctz, inner_outer_half)
        inner_arms.append((inner_fill_section[0], outer_fill_section[0],
                           outer_fill_section[1], inner_fill_section[1]))
        outer_arms.append((inner_outer_section[0], outer_outer_section[0],
                           outer_outer_section[1], inner_outer_section[1]))
        inner_core_pts.extend(core_fill_section)
        outer_core_pts.extend(core_outer_section)

    if len(inner_core_pts) >= 3:
        inner_arms.append(tuple(_convex_hull(inner_core_pts)))
    if len(outer_core_pts) >= 3:
        outer_arms.append(tuple(_convex_hull(outer_core_pts)))
    return JunctionMesh(tuple(outer_arms), tuple(inner_arms))


def clip_road_span(
    record: RoadRecord,
    center_distance: float,
    span: float,
    *,
    endpoint_extension: float = 0.0,
) -> tuple[Point, ...]:
    points = clip_polyline_span(record.points, center_distance, span)
    if len(points) < 2 or endpoint_extension <= _EPS:
        return points

    start = center_distance - span * 0.5
    end = center_distance + span * 0.5
    out = list(points)
    if start <= _EPS:
        out.insert(0, _extrapolate(out[0], out[1], endpoint_extension))
    if end >= record.length - _EPS:
        out.append(_extrapolate(out[-1], out[-2], endpoint_extension))
    return tuple(out)


def clip_polyline_span(points: Iterable, center_distance: float, span: float):
    """截取三维折线区间，并对边界点精确插值。"""
    pts = tuple(points)
    if len(pts) < 2:
        return pts
    lengths = [math.hypot(b.x - a.x, b.z - a.z) for a, b in zip(pts, pts[1:])]
    total = sum(lengths)
    start = _clamp(center_distance - span * 0.5, 0.0, total)
    end = _clamp(center_distance + span * 0.5, 0.0, total)
    if end - start <= _EPS:
        return ()
    out = [_sample_polyline(pts, lengths, start)]
    distance = 0.0
    for point, length in zip(pts[1:], lengths):
        distance += length
        if start + _EPS < distance < end - _EPS:
            out.append(point)
    out.append(_sample_polyline(pts, lengths, end))
    return tuple(out)


def _crossing_grid_size(edges):
    """限制密集 CS2 曲线的局部索引大小，避免长边索引膨胀。"""
    size = GRID_SIZE
    budget = max(65536, len(edges) * 8)
    while True:
        entries = 0
        for edge in edges:
            x0, z0, x1, z1 = edge.bbox
            entries += ((math.floor(x1 / size) - math.floor(x0 / size) + 1)
                        * (math.floor(z1 / size) - math.floor(z0 / size) + 1))
            if entries > budget:
                break
        if entries <= budget:
            return size
        size *= 2


def _find_crossings(edges, roads_by_id, height_reliable):
    grid: dict[tuple[int, int], list[int]] = {}
    grid_size = _crossing_grid_size(edges)
    for idx, edge in enumerate(edges):
        x0, z0, x1, z1 = edge.bbox
        for gx in range(math.floor(x0 / grid_size), math.floor(x1 / grid_size) + 1):
            for gz in range(math.floor(z0 / grid_size), math.floor(z1 / grid_size) + 1):
                grid.setdefault((gx, gz), []).append(idx)

    shared_nodes: dict[tuple[int, int], bool] = {}
    events: list[CrossingEvent] = []
    seen_crossings: set[tuple[int, int, int, int]] = set()
    for cell in sorted(grid):
        bucket = grid[cell]
        for pos, ai in enumerate(bucket):
            for bi in bucket[pos + 1:]:
                if ai == bi:
                    continue
                a, b = edges[ai], edges[bi]
                if a.road_id == b.road_id or not _bbox_intersects(a.bbox, b.bbox):
                    continue
                pair = (min(a.road_id, b.road_id), max(a.road_id, b.road_id))
                shares = shared_nodes.get(pair)
                if shares is None:
                    ra, rb = roads_by_id[a.road_id], roads_by_id[b.road_id]
                    nodes_a = {ra.start_node_id, ra.end_node_id}
                    nodes_b = {rb.start_node_id, rb.end_node_id}
                    shares = bool(nodes_a & nodes_b)
                    shared_nodes[pair] = shares
                if shares:
                    continue
                hit = _line_intersection(a, b)
                if hit is None:
                    continue
                t, u, x, z = hit
                if (math.floor(x / grid_size), math.floor(z / grid_size)) != cell:
                    continue
                dedupe = (pair[0], pair[1], round(x * 10), round(z * 10))
                if dedupe in seen_crossings:
                    continue
                seen_crossings.add(dedupe)
                ay = a.a.y + (a.b.y - a.a.y) * t
                by = b.a.y + (b.b.y - b.a.y) * u
                ra, rb = roads_by_id[a.road_id], roads_by_id[b.road_id]
                # 地面道路可有零高度；只要场景证明三维高度存在，零仍是有效高程。
                if height_reliable and abs(ay - by) > HEIGHT_EPSILON:
                    upper_a = ay > by
                else:
                    upper_a = road_sort_key(ra, False) > road_sort_key(rb, False)
                upper, lower = (a, b) if upper_a else (b, a)
                ur, lr = roads_by_id[upper.road_id], roads_by_id[lower.road_id]
                uy, ly = (ay, by) if upper_a else (by, ay)
                ud = upper.start_distance + (t if upper is a else u) * upper.length
                ld = lower.start_distance + (u if upper is a else t) * lower.length
                # 斜交时下层道路在上层占地中的投影会变长，覆盖跨度须随交角延长并设上下界。
                cross_sine = abs(
                    ((a.b.x - a.a.x) * (b.b.z - b.a.z)
                     - (a.b.z - a.a.z) * (b.b.x - b.a.x))
                    / max(a.length * b.length, _EPS)
                )
                projected_lower = lr.width / max(cross_sine, 0.2)
                base_span = _clamp(
                    1.25 * (ur.width + lr.width), 12.0, 40.0)
                span = _clamp(
                    max(base_span, 1.25 * projected_lower), 12.0, 120.0)
                events.append(CrossingEvent(
                    lower_segment_id=lower.road_id,
                    upper_segment_id=upper.road_id,
                    x=x, z=z, lower_y=ly, upper_y=uy,
                    lower_distance=ld, upper_distance=ud, span=span,
                ))
    events.sort(key=lambda e: (e.upper_y, e.upper_segment_id, e.x, e.z))
    return events


def _line_intersection(a: _Edge, b: _Edge):
    rx, rz = a.b.x - a.a.x, a.b.z - a.a.z
    sx, sz = b.b.x - b.a.x, b.b.z - b.a.z
    denom = rx * sz - rz * sx
    if abs(denom) <= _EPS:
        return None
    qx, qz = b.a.x - a.a.x, b.a.z - a.a.z
    t = (qx * sz - qz * sx) / denom
    u = (qx * rz - qz * rx) / denom
    # 形状点端点未必是拓扑连接；游戏常在另一条道路正上方切分高架道路，不能排除端点交叉。
    if t < -_EPS or t > 1.0 + _EPS or u < -_EPS or u > 1.0 + _EPS:
        return None
    t = _clamp(t, 0.0, 1.0)
    u = _clamp(u, 0.0, 1.0)
    return (t, u, a.a.x + t * rx, a.a.z + t * rz)


def _points_away_from_node(record: RoadRecord, node_id: int):
    if record.start_node_id == node_id:
        return record.points
    if record.end_node_id == node_id:
        return tuple(reversed(record.points))
    return ()


def _point_at_distance(points, distance):
    remaining = max(distance, 0.0)
    for a, b in zip(points, points[1:]):
        dx, dz = b.x - a.x, b.z - a.z
        length = math.hypot(dx, dz)
        if length <= _EPS:
            continue
        if remaining <= length:
            t = remaining / length
            return (a.x + dx * t, a.z + dz * t, dx / length, dz / length)
        remaining -= length
    a, b = points[-2], points[-1]
    dx, dz = b.x - a.x, b.z - a.z
    length = max(math.hypot(dx, dz), _EPS)
    return (b.x, b.z, dx / length, dz / length)


def _section(x, z, tx, tz, half_width):
    nx, nz = -tz, tx
    return ((x + nx * half_width, z + nz * half_width),
            (x - nx * half_width, z - nz * half_width))


def _convex_hull(points):
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _sample_polyline(points, lengths, distance):
    remaining = max(distance, 0.0)
    for a, b, length in zip(points, points[1:], lengths):
        if length <= _EPS:
            continue
        if remaining <= length:
            t = remaining / length
            cls = type(a)
            return cls(
                a.x + (b.x - a.x) * t,
                a.y + (b.y - a.y) * t,
                a.z + (b.z - a.z) * t,
            )
        remaining -= length
    return points[-1]


def _extrapolate(endpoint, inward, distance):
    """在 X/Z 平面上将端点沿远离邻点的方向移动。"""
    dx, dz = endpoint.x - inward.x, endpoint.z - inward.z
    length = math.hypot(dx, dz)
    if length <= _EPS:
        return endpoint
    scale = distance / length
    cls = type(endpoint)
    return cls(
        endpoint.x + dx * scale,
        endpoint.y + (endpoint.y - inward.y) * scale,
        endpoint.z + dz * scale,
    )


def _bbox_intersects(a, b):
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def _clamp(value, low, high):
    return max(low, min(high, value))
