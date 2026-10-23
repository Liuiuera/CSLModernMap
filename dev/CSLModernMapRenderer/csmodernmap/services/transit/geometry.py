"""从路径、轨道和站点信息解析线路几何。"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from ...cmm import GeomKind, TransitKind, normalize_transit_kind
from ...data import MapData, Point, Segment, Transport


@dataclass(frozen=True)
class WorldPoint:
    """保留用于等价诊断的精确世界坐标；CMM Vec3 为文档稳定性会量化数值，此处必须保留源浮点精度。"""

    x: float
    y: float
    z: float

    def as_2d(self) -> tuple[float, float]:
        return self.x, self.z


@dataclass(frozen=True)
class GeometryResult:
    kind: GeomKind
    path_segment_ids: tuple[int, ...] = ()
    points: tuple[WorldPoint, ...] = ()
    parts: tuple[tuple[WorldPoint, ...], ...] = ()


def _same_xy(a: Point, b: Point) -> bool:
    return abs(a.x - b.x) < 1e-3 and abs(a.z - b.z) < 1e-3


def track_path_index(mapdata: MapData, keyword: str, cache=None) -> dict[int, list[tuple[int, int, list[int]]]]:
    key = ("path_index", str(keyword or "").casefold())
    if cache is not None and key in cache:
        return cache[key]
    result: dict[int, list[tuple[int, int, list[int]]]] = {}
    wanted = (keyword or "").lower()
    for segment in mapdata.segments:
        if wanted not in (segment.name or "").lower() or not segment.path_seg_ids:
            continue
        chain: list[int] = []
        for segment_id in segment.path_seg_ids:
            if not chain or chain[-1] != segment_id:
                chain.append(segment_id)
        result.setdefault(segment.start_node_id, []).append(
            (segment.id, segment.end_node_id, chain)
        )
    for values in result.values():
        values.sort(key=lambda item: (item[1], item[0], tuple(item[2])))
    if cache is not None:
        cache[key] = result
    return result


def path_chain_segment_ids(mapdata: MapData, stop_node_ids, keyword: str,
                           cache=None) -> tuple[int, ...]:
    """提供不带缓存的线路路段链解析逻辑。"""
    stops = [int(item) for item in stop_node_ids if mapdata.node(int(item)) is not None]
    if len(stops) < 2:
        return ()
    if stops[-1] == stops[0]:
        stops = stops[:-1]
        if len(stops) < 2:
            return ()
    index = track_path_index(mapdata, keyword, cache)
    chains: list[list[int]] = []
    closed = False
    for i, start in enumerate(stops):
        end = stops[(i + 1) % len(stops)]
        if i == len(stops) - 1:
            has_forward = any(item[1] == end for item in index.get(start, ()))
            has_backward = any(item[1] == start for item in index.get(end, ()))
            if not has_forward and not has_backward:
                break
            closed = True
        candidates = [item for item in index.get(start, ()) if item[1] == end]
        chain = candidates[0][2] if candidates else None
        if chain is None:
            candidates = [item for item in index.get(end, ()) if item[1] == start]
            chain = list(reversed(candidates[0][2])) if candidates else None
        if chain is None:
            return ()
        chains.append(list(chain))
    full: list[int] = []
    for chain in chains:
        if not chain:
            continue
        overlap = 0
        while overlap < len(chain) and overlap < len(full) and chain[overlap] == full[-1 - overlap]:
            overlap += 1
        full.extend(chain[1:] if overlap == 1 else chain)
    if not full:
        return ()
    if closed and len(full) > 1 and full[0] == full[-1]:
        full.pop()
    return tuple(full)


def track_chain_points(mapdata: MapData, chain: tuple[int, ...], start_pos: Point) -> tuple[WorldPoint, ...]:
    points: list[Point] = []
    previous_after_node: int | None = None
    segments: list[Segment | None] = [mapdata.segment(segment_id) for segment_id in chain]
    for index, segment in enumerate(segments):
        if segment is None:
            continue
        shape = list(segment.points)
        if not shape:
            start = mapdata.node(segment.start_node_id)
            end = mapdata.node(segment.end_node_id)
            if start is None or end is None:
                continue
            shape = [start.pos, end.pos]
        reverse: bool | None = None
        if index < len(segments) - 1 and segments[index + 1] is not None:
            following = segments[index + 1]
            assert following is not None
            if segment.end_node_id in (following.start_node_id, following.end_node_id):
                reverse = False
            elif segment.start_node_id in (following.start_node_id, following.end_node_id):
                reverse = True
        if reverse is None and previous_after_node is not None:
            if segment.start_node_id == previous_after_node:
                reverse = False
            elif segment.end_node_id == previous_after_node:
                reverse = True
        if reverse is None:
            anchor = points[-1] if points else start_pos
            reverse = ((shape[0].x - anchor.x) ** 2 + (shape[0].z - anchor.z) ** 2
                       > (shape[-1].x - anchor.x) ** 2 + (shape[-1].z - anchor.z) ** 2)
        if reverse:
            shape.reverse()
            previous_after_node = segment.start_node_id
        else:
            previous_after_node = segment.end_node_id
        if points and shape and _same_xy(points[-1], shape[0]):
            points.extend(shape[1:])
        else:
            points.extend(shape)
    return tuple(WorldPoint(item.x, item.y, item.z) for item in points)


def _track_graph(mapdata: MapData, flag: str, cache=None) -> dict[int, list[tuple[int, int]]]:
    key = ("track_graph", str(flag))
    if cache is not None and key in cache:
        return cache[key]
    graph: dict[int, list[tuple[int, int]]] = {}
    for segment in mapdata.segments:
        definition = mapdata.segment_types.get(segment.name) or mapdata.segment_types.get(segment.icls)
        if definition is None or not getattr(definition, flag, False):
            continue
        if segment.start_node_id == segment.end_node_id:
            continue
        graph.setdefault(segment.start_node_id, []).append((segment.end_node_id, segment.id))
        graph.setdefault(segment.end_node_id, []).append((segment.start_node_id, segment.id))
    for values in graph.values():
        values.sort(key=lambda item: (item[0], item[1]))
    if cache is not None:
        cache[key] = graph
    return graph


def _nearest_track_node(mapdata: MapData, graph: dict[int, list[tuple[int, int]]],
                        node_id: int, cache=None) -> int | None:
    key = ("nearest_track", id(graph), int(node_id))
    if cache is not None and key in cache:
        return cache[key]
    source = mapdata.node(node_id)
    if source is None:
        return None
    candidates = []
    for candidate_id in graph:
        candidate = mapdata.node(candidate_id)
        if candidate is not None:
            candidates.append(((candidate.pos.x - source.pos.x) ** 2
                               + (candidate.pos.z - source.pos.z) ** 2, candidate_id))
    result = min(candidates)[1] if candidates else None
    if cache is not None:
        cache[key] = result
    return result


def _bfs(graph: dict[int, list[tuple[int, int]]], start: int, end: int, cache=None):
    key = ("bfs", id(graph), int(start), int(end))
    if cache is not None and key in cache:
        return cache[key]
    if start == end:
        result = ((start,), ())
        if cache is not None:
            cache[key] = result
        return result
    previous: dict[int, tuple[int, int] | None] = {start: None}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        if current == end:
            break
        for nxt, segment_id in graph.get(current, ()):
            if nxt not in previous:
                previous[nxt] = (current, segment_id)
                queue.append(nxt)
    if end not in previous:
        if cache is not None:
            cache[key] = None
        return None
    nodes: list[int] = []
    segments: list[int] = []
    current: int | None = end
    while current is not None:
        nodes.append(current)
        edge = previous[current]
        if edge is not None:
            segments.append(edge[1])
            current = edge[0]
        else:
            current = None
    result = (tuple(reversed(nodes)), tuple(reversed(segments)))
    if cache is not None:
        cache[key] = result
    return result


def _bfs_points(mapdata: MapData, graph, stop_node_ids, cache=None) -> tuple[WorldPoint, ...]:
    valid = [int(item) for item in stop_node_ids if mapdata.node(int(item)) is not None]
    if len(valid) < 2:
        return ()
    projected = [_nearest_track_node(mapdata, graph, item, cache) for item in valid]
    result: list[Point] = []
    first = mapdata.node(valid[0])
    if first is not None:
        result.append(first.pos)
    for start, end in zip(projected, projected[1:]):
        if start is None or end is None or start == end:
            continue
        path = _bfs(graph, start, end, cache) or ((start, end), ())
        nodes, segment_ids = path
        for index, segment_id in enumerate(segment_ids):
            segment = mapdata.segment(segment_id)
            if segment is None or not segment.points:
                for node_id in (nodes[index], nodes[index + 1]):
                    map_node = mapdata.node(node_id)
                    if map_node is not None:
                        result.append(map_node.pos)
                continue
            shape = segment.points
            oriented = shape if segment.start_node_id == nodes[index] else list(reversed(shape))
            result.extend(oriented if index == 0 else oriented[1:])
        if not segment_ids:
            for node_id in nodes:
                map_node = mapdata.node(node_id)
                if map_node is not None:
                    result.append(map_node.pos)
    last = mapdata.node(valid[-1])
    if last is not None:
        result.append(last.pos)
    deduped: list[Point] = []
    for point in result:
        if not deduped or not _same_xy(deduped[-1], point):
            deduped.append(point)
    return tuple(WorldPoint(point.x, point.y, point.z) for point in deduped)


def _direct_points(mapdata: MapData, transport: Transport) -> tuple[WorldPoint, ...]:
    result: list[WorldPoint] = []
    for source_stop in transport.stops:
        node = mapdata.node(source_stop.node_id)
        if node is None:
            continue
        point = WorldPoint(node.pos.x, node.pos.y, node.pos.z)
        if result and result[-1].as_2d() == point.as_2d():
            continue
        result.append(point)
    return tuple(result)


def resolve_route_geometry(mapdata: MapData, transport: Transport,
                           kind: TransitKind | str | None = None,
                           *, cache=None) -> GeometryResult:
    from .native import cs2_document
    document = cs2_document(mapdata)
    if document is not None:
        parts, ids = [], []
        for route in sorted(document.routes_of_line(transport.id), key=lambda item: item.id):
            part = []
            for ident in route.path_segment_ids:
                leaf = document.network(ident)
                if leaf is None or not leaf.geometry.points:
                    if part:
                        parts.append(tuple(part))
                        part = []
                    continue
                part.extend(WorldPoint(p.x, p.y, p.z) for p in leaf.geometry.points)
                ids.append(ident)
            if part:
                parts.append(tuple(part))
        return GeometryResult(GeomKind.PATH_CHAIN if parts else GeomKind.STOP_DIRECT,
                              tuple(ids), parts[0] if parts else (), tuple(parts))
    canonical = normalize_transit_kind(kind or transport.type)
    track_flags = {
        TransitKind.METRO: "metro", TransitKind.TRAIN: "rail",
        TransitKind.MONORAIL: "monorail", TransitKind.TRAM: "tram",
        TransitKind.CABLECAR: "cablecar",
    }
    track_flag = track_flags.get(canonical)
    airborne = canonical in {TransitKind.AIRPLANE, TransitKind.HELICOPTER}
    line_name = canonical.value.casefold().replace("cablecar", "cable car") + " line"
    stops = [item.node_id for item in transport.stops]
    if not airborne:
        chain = path_chain_segment_ids(mapdata, stops, line_name, cache)
        if chain:
            start_node = mapdata.node(next((item for item in stops if mapdata.node(item)), stops[0]))
            if start_node is not None:
                points = track_chain_points(mapdata, chain, start_node.pos)
                if len(points) >= 2:
                    return GeometryResult(GeomKind.PATH_CHAIN, chain, points)
    if track_flag:
        graph = _track_graph(mapdata, track_flag, cache)
        points = _bfs_points(mapdata, graph, stops, cache)
        if len(points) >= 2:
            segments: list[int] = []
            projected = [_nearest_track_node(mapdata, graph, item, cache) for item in stops]
            for start, end in zip(projected, projected[1:]):
                if start is not None and end is not None:
                    path = _bfs(graph, start, end, cache)
                    if path:
                        segments.extend(path[1])
            return GeometryResult(GeomKind.TRACK_BFS, tuple(segments), points)
    return GeometryResult(GeomKind.STOP_DIRECT, points=_direct_points(mapdata, transport))


def line_geometry_world(mapdata: MapData, transport: Transport,
                        kind: TransitKind | str | None = None) -> tuple[WorldPoint, ...]:
    return resolve_route_geometry(mapdata, transport, kind).points


class RouteGeometryService:
    resolve = staticmethod(resolve_route_geometry)
    path_chain = staticmethod(path_chain_segment_ids)
