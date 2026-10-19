"""将道路局部高度约束编译为预览绘制层级。

预览和 PNG 共用本模块。绘制等级作用于完整路面而非单次描边；通过交叉点之间的分段消除整条道路的循环约束。人为切分边不会成为道路边界。"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from math import hypot, floor

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QPainterPath, QPainterPathStroker

from .road_geometry import (HEIGHT_EPSILON, clip_polyline_span,
                            build_junction_mesh, junction_primary)
from .road_hierarchy import road_level_rank

VERSION = 4
_EPS = 1e-6


def _path(points, closed=True):
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.WindingFill)
    if points:
        path.moveTo(*points[0])
        for point in points[1:]:
            path.lineTo(*point)
        if closed:
            path.closeSubpath()
    return path


def _rings(path):
    result = []
    # 查找物理边界前先移除环绕规则产生的重叠和回绕边，避免描边连接内的三角形内环。
    for polygon in path.simplified().toFillPolygons():
        ring = tuple((p.x(), p.y()) for p in polygon)
        area = sum(a[0]*b[1]-b[0]*a[1] for a, b in zip(ring, ring[1:]))
        if len(ring) >= 4 and abs(area) > _EPS:
            result.append(ring)
    return result


class _ProjectionIndex:
    """在短连续中心线段上执行精确最近路段查询；单次编译时构建，并在等距时保持源路段顺序。"""
    def __init__(self, points):
        segments = []
        distance = 0.0
        for index, (a, b) in enumerate(zip(points, points[1:])):
            dx, dz = b.x-a.x, b.z-a.z
            length = hypot(dx, dz)
            if length <= _EPS:
                continue
            segments.append((index, a, dx, dz, b.y-a.y, length,
                             length*length, distance, b))
            distance += length
        self.blocks = []
        for offset in range(0, len(segments), 8):
            chunk = segments[offset:offset+8]
            self.blocks.append((min(min(s[1].x, s[8].x) for s in chunk),
                                min(min(s[1].z, s[8].z) for s in chunk),
                                max(max(s[1].x, s[8].x) for s in chunk),
                                max(max(s[1].z, s[8].z) for s in chunk), chunk))

    def project(self, x, z):
        candidates = []
        for x0, z0, x1, z1, segments in self.blocks:
            dx, dz = max(x0-x, 0.0, x-x1), max(z0-z, 0.0, z-z1)
            candidates.append((dx*dx+dz*dz, segments))
        candidates.sort(key=lambda item: item[0])
        best_error, best_index = float('inf'), float('inf')
        best_y = best_distance = 0.0
        for lower, segments in candidates:
            # 保守余量只影响剪枝，不改变查询结果。
            if lower > best_error + 1e-12 * max(1.0, best_error):
                break
            for index, a, dx, dz, dy, length, squared, distance, _ in segments:
                t = max(0.0, min(1.0, ((x-a.x)*dx+(z-a.z)*dz)/squared))
                error = (x-a.x-t*dx)**2 + (z-a.z-t*dz)**2
                if error < best_error or (error == best_error and index < best_index):
                    best_error, best_index = error, index
                    best_y, best_distance = a.y+dy*t, distance+t*length
        return best_y, best_distance


def _projection(road, x, z, indexes=None):
    """返回局部中心线高度和弧长；零也是有效高度。"""
    if indexes is not None:
        index = indexes.get(road.id)
        if index is None:
            index = indexes[road.id] = _ProjectionIndex(road.points)
        return index.project(x, z)
    best = (float('inf'), 0.0, 0.0)
    distance = 0.0
    for a, b in zip(road.points, road.points[1:]):
        dx, dz = b.x - a.x, b.z - a.z
        length = hypot(dx, dz)
        if length <= _EPS:
            continue
        t = max(0.0, min(1.0, ((x-a.x)*dx + (z-a.z)*dz)/(length*length)))
        error = (x-a.x-t*dx)**2 + (z-a.z-t*dz)**2
        if error < best[0]:
            best = error, a.y+(b.y-a.y)*t, distance+t*length
        distance += length
    return best[1:]


def _cut(road, distance):
    points = clip_polyline_span(road.points, distance, .02)
    a, b = points[0], points[-1]
    return ((a.x+b.x)*.5, (a.z+b.z)*.5), (b.x-a.x, b.z-a.z)


def _split_surface(path, road, start, end):
    """沿最近的横向边界弦切分，而非无限直线，以免切到发夹弯的另一支。"""
    point, normal = _cut(road, end)
    rings = _rings(path)
    hits = []
    for ring_index, ring in enumerate(rings):
        for index, (a, b) in enumerate(zip(ring, ring[1:])):
            da = (a[0]-point[0])*normal[0] + (a[1]-point[1])*normal[1]
            db = (b[0]-point[0])*normal[0] + (b[1]-point[1])*normal[1]
            if abs(da-db) < 1e-12 or (da < 0) == (db < 0):
                continue
            t = da/(da-db)
            p = a[0]+t*(b[0]-a[0]), a[1]+t*(b[1]-a[1])
            side = -(p[0]-point[0])*normal[1] + (p[1]-point[1])*normal[0]
            hits.append((side, ring_index, index+t, p))
    negative = [hit for hit in hits if hit[0] < 0]
    positive = [hit for hit in hits if hit[0] >= 0]
    if not negative or not positive:
        raise ValueError(f'Cannot locate a local road cut on segment {road.id}')
    left, right = max(negative), min(positive)
    if left[1] != right[1]:
        raise ValueError(f'Road cut crosses separate surface rings on segment {road.id}')
    ring = rings[left[1]][:-1]
    count = len(ring)

    def arc(a, b):
        stop = b[2] if b[2] > a[2] else b[2]+count
        return [a[3], *[ring[i % count] for i in range(floor(a[2])+1, floor(stop)+1)], b[3]]

    first, second = _path(arc(left, right)), _path(arc(right, left))
    anchor = clip_polyline_span(road.points, max(start, end-.05), .001)[0]
    if second.contains(QPointF(anchor.x, anchor.z)):
        first, second = second, first
    elif not first.contains(QPointF(anchor.x, anchor.z)):
        raise ValueError(f'Road centreline is outside its surface at cut on segment {road.id}')
    for index, other in enumerate(rings):
        if index == left[1]:
            continue
        # 不连通的占地区块仍保持各自的局部弧长归属。
        distance = _projection(road, *other[0])[1]
        if distance < end:
            first = first.united(_path(other))
        else:
            second = second.united(_path(other))
    return first, second


def _clip_lines(lines, region, rings=None):
    """只裁剪源边，不为人为切分边描边。"""
    boundary = [p for ring in (_rings(region) if rings is None else rings)
                for p in zip(ring, ring[1:])]
    index = _BoundaryIndex(boundary)
    result = []
    for line in lines:
        run = []
        for a, b in zip(line, line[1:]):
            dx, dz = b[0]-a[0], b[1]-a[1]
            values = [0.0, 1.0]
            for c, d in index.candidates(a, b):
                ex, ez = d[0]-c[0], d[1]-c[1]
                determinant = dx*ez-dz*ex
                if abs(determinant) <= _EPS:
                    continue
                cx, cz = c[0]-a[0], c[1]-a[1]
                t, u = (cx*ez-cz*ex)/determinant, (cx*dz-cz*dx)/determinant
                if 0 < t < 1 and -_EPS <= u <= 1+_EPS:
                    values.append(t)
            values = sorted(set(values))
            for t0, t1 in zip(values, values[1:]):
                t = (t0+t1)*.5
                p = QPointF(a[0]+t*dx, a[1]+t*dz)
                # 保留与边界重合的源边；Qt contains 为半开判断，因此向各方向微探测。
                inside = any(region.contains(p+QPointF(ox, oz)) for ox, oz in
                             ((0, 0), (_EPS, 0), (-_EPS, 0), (0, _EPS), (0, -_EPS)))
                if inside:
                    start = (a[0]+t0*dx, a[1]+t0*dz)
                    end = (a[0]+t1*dx, a[1]+t1*dz)
                    if run and hypot(run[-1][0]-start[0], run[-1][1]-start[1]) > _EPS:
                        result.append(tuple(run)); run = []
                    if not run:
                        run.append(start)
                    run.append(end)
                elif run:
                    result.append(tuple(run)); run = []
        if run:
            result.append(tuple(run))
    return tuple(result)


@dataclass
class RoadPaint:
    road: object
    rings: tuple
    boundaries: tuple
    polygons: tuple = ()


@dataclass
class RoadPiece:
    road: object
    index: int
    start: float
    end: float
    rank: int = 0
    rings: tuple = ()
    boundaries: tuple = ()
    paints: tuple = ()

    @property
    def paint_regions(self):
        return self.paints or (RoadPaint(self.road, self.rings, self.boundaries),)

    @property
    def points(self):
        return clip_polyline_span(self.road.points, (self.start+self.end)*.5,
                                  self.end-self.start)


@dataclass
class RoadOrder:
    pieces: dict[int, list[RoadPiece]] = field(default_factory=dict)
    diagnostics: dict = field(default_factory=dict)


def _polygons(path):
    """为 GeoJSON 显式保留孔洞；PNG 使用 Qt 重排方向的填充环。"""
    rings = [tuple((p.x(), p.y()) for p in polygon)
             for polygon in path.simplified().toSubpathPolygons()]
    rings = [ring for ring in rings if len(ring) >= 4]
    rings.sort(key=lambda ring: abs(sum(a[0]*b[1]-b[0]*a[1]
                                     for a, b in zip(ring, ring[1:]))), reverse=True)
    polygons = []
    for ring in rings:
        point = QPointF(*ring[0])
        containing = [index for index, polygon in enumerate(polygons)
                      if _path(polygon[0]).contains(point)]
        if containing and not any(_path(hole).contains(point)
                                  for index in containing for hole in polygons[index][1:]):
            polygons[containing[-1]].append(ring)
        else:
            polygons.append([ring])
    return tuple(tuple(polygon) for polygon in polygons)


def _junction_paint_masks(scene, paths, height_ranges):
    masks = defaultdict(list)
    seams = defaultdict(list)
    for junction in sorted(scene.junctions, key=lambda item: item.node_id):
        modes = defaultdict(list)
        for sid in junction.segment_ids:
            road = scene.roads_by_id[sid]
            if road.footprint is not None:
                endpoint = road.points[0] if road.start_node_id == junction.node_id else road.points[-1]
                modes[road.mode].append((endpoint.y, road))
        for candidates in modes.values():
            groups = []
            for height, road in sorted(candidates, key=lambda item: (item[0], item[1].id)):
                if not groups or (scene.height_reliable and height-groups[-1][0][0] > HEIGHT_EPSILON):
                    groups.append([])
                groups[-1].append((height, road))
            for group in groups:
                if len(group) < 2:
                    continue
                roads = [road for _, road in group]
                same_style = all(road.level is roads[0].level for road in roads)
                same_grade = (max(height_ranges[road.id][1] for road in roads)
                              - min(height_ranges[road.id][0] for road in roads) < HEIGHT_EPSILON)
                if same_style and same_grade:
                    continue
                active = type(junction)(junction.node_id, tuple(road.id for road in roads),
                                        junction.x, junction.z, junction.mean_y, junction.bbox)
                primary = junction_primary(scene, active)
                mesh = build_junction_mesh(scene, active, lambda road: road.width, lambda road: 0)
                mask, surface = QPainterPath(), QPainterPath()
                for ring in mesh.inner:
                    mask = mask.united(_path(ring))
                for road in roads:
                    surface = surface.united(paths[road.id])
                mask = mask.intersected(surface)
                for index, road in enumerate(roads):
                    for other in roads[index+1:]:
                        seams[min(road.id, other.id), max(road.id, other.id)].append(mask)
                for road in roads:
                    if road.level is not primary.level:
                        masks[road.id].append((primary, mask))
    for entries in masks.values():
        entries.sort(key=lambda item: (road_level_rank(item[0].level),
                                      item[0].width, item[0].id), reverse=True)
    return masks, seams


def _paint_regions(road, region, boundaries, masks):
    remaining = region
    result = []
    for primary, mask in masks:
        painted = remaining.intersected(mask)
        if painted.isEmpty():
            continue
        # 只转移样式，各片段保持局部深度等级，包括坡道和桥梁落地点。
        rings = tuple(_rings(painted))
        result.append(RoadPaint(primary, rings,
                                _clip_lines(boundaries, painted, rings), _polygons(painted)))
        remaining = remaining.subtracted(painted)
    if result:
        if not remaining.isEmpty():
            rings = tuple(_rings(remaining))
            result.insert(0, RoadPaint(road, rings,
                                      _clip_lines(boundaries, remaining, rings), _polygons(remaining)))
        return tuple(result)
    return ()


def _ranks(nodes, constraints):
    outgoing = defaultdict(set)
    degree = {node: 0 for node in nodes}
    ranks = dict.fromkeys(nodes, 0)
    for a, b in constraints:
        if a != b and b not in outgoing[a]:
            outgoing[a].add(b); degree[b] += 1
    ready = deque(sorted(node for node, value in degree.items() if value == 0))
    while ready:
        a = ready.popleft()
        for b in sorted(outgoing[a]):
            ranks[b] = max(ranks[b], ranks[a]+1)
            degree[b] -= 1
            if degree[b] == 0:
                ready.append(b)
    return ranks, {node for node, value in degree.items() if value}


def _edge_spans(a, b, boundaries):
    """在相邻占地进入或离开源边的位置切分。"""
    dx, dz = b[0]-a[0], b[1]-a[1]
    values = {0.0, 1.0}
    for c, d in boundaries:
        ex, ez = d[0]-c[0], d[1]-c[1]
        determinant = dx*ez-dz*ex
        if abs(determinant) <= _EPS:
            continue
        cx, cz = c[0]-a[0], c[1]-a[1]
        t, u = (cx*ez-cz*ex)/determinant, (cx*dz-cz*dx)/determinant
        if 0 < t < 1 and -_EPS <= u <= 1+_EPS:
            values.add(t)
    values = sorted(values)
    return [((a[0]+t0*dx, a[1]+t0*dz), (a[0]+t1*dx, a[1]+t1*dz))
            for t0, t1 in zip(values, values[1:]) if t1-t0 > _EPS]


class _BoundaryIndex:
    """只测试邻近外边界线段，并限制长斜线索引范围。"""
    cell = 32.0

    def __init__(self, edges):
        self.edges = edges
        self.grid = defaultdict(list)
        self.large = []
        for index, (a, b) in enumerate(edges):
            # _edge_spans 接受略超出端点的参数，索引也须覆盖这一扩展。
            pad_x = abs(b[0]-a[0])*_EPS + 1e-9
            pad_z = abs(b[1]-a[1])*_EPS + 1e-9
            x0, x1 = floor((min(a[0], b[0])-pad_x)/self.cell), floor((max(a[0], b[0])+pad_x)/self.cell)
            z0, z1 = floor((min(a[1], b[1])-pad_z)/self.cell), floor((max(a[1], b[1])+pad_z)/self.cell)
            if (x1-x0+1)*(z1-z0+1) > 64:
                self.large.append(index)
                continue
            for x in range(x0, x1+1):
                for z in range(z0, z1+1):
                    self.grid[x, z].append(index)

    def candidates(self, a, b):
        x0, x1 = floor(min(a[0], b[0])/self.cell), floor(max(a[0], b[0])/self.cell)
        z0, z1 = floor(min(a[1], b[1])/self.cell), floor(max(a[1], b[1])/self.cell)
        if (x1-x0+1)*(z1-z0+1) > 64:
            return self.edges
        indexes = set(self.large)
        for x in range(x0, x1+1):
            for z in range(z0, z1+1):
                indexes.update(self.grid.get((x, z), ()))
        return [self.edges[index] for index in sorted(indexes)]


def _outer_boundaries(road, original, scene, paths, incidence, neighbor_cache=None,
                      projection_indexes=None, height_ranges=None, junction_seams=None):
    """消除横向内部接缝，包括桥梁落地点；依据拓扑和实际覆盖判断，而非固定中点半径。"""
    lines, run = [], []
    if neighbor_cache is None:
        neighbor_cache = {}
    neighbors = []
    neighbor_ids = set(incidence[road.start_node_id]) | set(incidence[road.end_node_id])
    for other_id in sorted(neighbor_ids - {road.id}):
        other = scene.roads_by_id[other_id]
        if other.footprint is None:
            continue
        if other_id not in neighbor_cache:
            stroke = QPainterPathStroker()
            stroke.setWidth(.1)  # 保留 Qt 精确的圆角并集归属结果。
            region = paths[other_id].united(stroke.createStroke(paths[other_id]))
            # 在真实外轮廓上切分；按扩张容差掩码切分会缩短露出的路肩并留下细小外沿缺口。
            edges = [edge for ring in _rings(paths[other_id]) for edge in zip(ring, ring[1:])]
            bbox = region.boundingRect()
            neighbor_cache[other_id] = (region, _BoundaryIndex(edges),
                                       (bbox.left(), bbox.top(), bbox.right(), bbox.bottom()))
        region, edges, bbox = neighbor_cache[other_id]
        pair = min(road.id, other.id), max(road.id, other.id)
        same_grade = False
        if height_ranges is not None:
            low_a, high_a = height_ranges[road.id]
            low_b, high_b = height_ranges[other.id]
            same_grade = max(high_a, high_b)-min(low_a, low_b) < HEIGHT_EPSILON-1e-9
        neighbors.append((other, region, edges, bbox, same_grade,
                          (junction_seams or {}).get(pair, ())))
    own = paths[road.id]
    for a, b in zip(original, original[1:]):
        length = hypot(b[0]-a[0], b[1]-a[1])
        if length <= _EPS:
            continue
        x0, x1 = min(a[0], b[0])-.1, max(a[0], b[0])+.1
        z0, z1 = min(a[1], b[1])-.1, max(a[1], b[1])+.1
        nearby = [entry for entry in neighbors
                  if entry[3][0] < x1 and entry[3][2] > x0
                  and entry[3][1] < z1 and entry[3][3] > z0]
        if not nearby:
            if not run:
                run.append(a)
            run.append(b)
            continue
        boundaries = [edge for _, _, edges, _, _, _ in nearby for edge in edges.candidates(a, b)]
        # 探测距离须超过圆角扩张量，避免把重合的外边界误判为内部接缝。
        normal = QPointF(-(b[1]-a[1])*.1/length, (b[0]-a[0])*.1/length)
        for start, end in _edge_spans(a, b, boundaries):
            middle = QPointF((start[0]+end[0])*.5, (start[1]+end[1])*.5)
            left, right = middle+normal, middle-normal
            left_inside, right_inside = own.contains(left), own.contains(right)
            outward = ([right] if left_inside else [left]) if left_inside != right_inside else []
            internal = False
            own_heights = {}
            for other, region, _, bbox, pair_grade, seams in nearby:
                if not (bbox[0] <= middle.x() <= bbox[2] and bbox[1] <= middle.y() <= bbox[3]):
                    continue
                if not region.contains(middle) or not any(region.contains(p) for p in outward):
                    continue
                # 所有插值高度都落在端点高度范围内。
                # 若整对地面道路已证明处于同一高度容差内，无须最近路段查询。
                # 兼容的共用节点即使位于坡上仍拥有原生核心，不能投影到两条分离中心线后判断高度。
                same_grade = pair_grade or any(mask.contains(middle) for mask in seams)
                if scene.height_reliable and not same_grade:
                    separated = False
                    for p in (start, (middle.x(), middle.y()), end):
                        if p not in own_heights:
                            own_heights[p] = _projection(road, *p, projection_indexes)[0]
                        if abs(own_heights[p]-_projection(other, *p, projection_indexes)[0]) > HEIGHT_EPSILON:
                            separated = True
                            break
                    if separated:
                        continue
                internal = True
                break
            if internal:
                if run:
                    lines.append(tuple(run)); run = []
            else:
                if not run:
                    run.append(start)
                run.append(end)
    if run:
        lines.append(tuple(run))
    return tuple(lines)


def build_road_order(scene, *, accurate=True):
    """返回完全覆盖的片段及其局部物理顺序；占地相交也贡献约束，缺失或相等高度不虚构上下关系。"""
    roads = scene.roads_by_id
    events = [(e.lower_segment_id, e.lower_distance,
               e.upper_segment_id, e.upper_distance) for e in scene.crossings
              if e.upper_y-e.lower_y > HEIGHT_EPSILON and scene.height_reliable]
    paths = {}
    grid = defaultdict(list)
    overlap_samples = 0
    projection_indexes = {}
    height_ranges = {road.id: (min(p.y for p in road.points), max(p.y for p in road.points))
                     for road in scene.roads}
    for road in scene.roads:
        if road.footprint is not None and len(road.footprint.points) >= 3:
            path = _path([(p.x, p.z) for p in road.footprint.points])
        else:
            stroke = QPainterPathStroker()
            stroke.setWidth(max(road.width, .1))
            stroke.setCapStyle(Qt.PenCapStyle.RoundCap)
            stroke.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            path = stroke.createStroke(_path([(p.x, p.z) for p in road.points], False))
        paths[road.id] = path
        if accurate:
            bbox = path.boundingRect()
            for gx in range(floor(bbox.left()/256), floor(bbox.right()/256)+1):
                for gz in range(floor(bbox.top()/256), floor(bbox.bottom()/256)+1):
                    grid[gx, gz].append(road.id)
    seen = set()
    if accurate and scene.height_reliable:
        for bucket in grid.values():
            for i, aid in enumerate(bucket):
                for bid in bucket[i+1:]:
                    pair = min(aid, bid), max(aid, bid)
                    if pair in seen:
                        continue
                    seen.add(pair)
                    a, b = roads[aid], roads[bid]
                    if not paths[aid].boundingRect().intersects(paths[bid].boundingRect()):
                        continue
                    low_a, high_a = height_ranges[aid]
                    low_b, high_b = height_ranges[bid]
                    if max(high_a, high_b)-min(low_a, low_b) < HEIGHT_EPSILON-1e-9:
                        continue
                    overlap = paths[aid].intersected(paths[bid])
                    for ring in _rings(overlap):
                        # 顶点加内部边采样能发现宽重叠区域中的高度反转，而不只看中心。
                        samples = list(ring[:-1])
                        samples += [((p[0]+q[0])*.5, (p[1]+q[1])*.5)
                                    for p, q in zip(ring, ring[1:])]
                        for x, z in samples:
                            ay, ad = _projection(a, x, z, projection_indexes)
                            by, bd = _projection(b, x, z, projection_indexes)
                            if abs(ay-by) <= HEIGHT_EPSILON:
                                continue
                            events.append((aid, ad, bid, bd) if ay < by else (bid, bd, aid, ad))
                            overlap_samples += 1
    # 事件位置只为去重而量化，不参与高度排序。
    events = sorted(set((a, round(ad, 5), b, round(bd, 5)) for a, ad, b, bd in events))
    cuts = {road.id: [] for road in scene.roads}
    positions = defaultdict(set)
    roles = defaultdict(set)
    for a, ad, b, bd in events:
        positions[a].add(ad); positions[b].add(bd)
        roles[a].add((ad, 0)); roles[b].add((bd, 1))

    def key(road_id, distance):
        from bisect import bisect_right
        return road_id, bisect_right(cuts[road_id], distance)

    nodes = [(road.id, 0) for road in scene.roads]
    constraints = {(key(a, ad), key(b, bd)) for a, ad, b, bd in events}
    ranks, cyclic = _ranks(nodes, constraints)
    if cyclic:
        # 只在连续冲突位置之间切分受阻道路，以消除空间上的循环顺序而不制造虚假高度。
        for road_id, _ in cyclic:
            samples = sorted(roles[road_id])
            cuts[road_id] = sorted(set((a+b)*.5 for (a, ar), (b, br)
                                       in zip(samples, samples[1:])
                                       if ar != br and b-a > .01))
        nodes = [(road.id, i) for road in scene.roads for i in range(len(cuts[road.id])+1)]
        constraints = {(key(a, ad), key(b, bd)) for a, ad, b, bd in events}
        ranks, cyclic = _ranks(nodes, constraints)
    if cyclic:
        for road_id, _ in cyclic:
            samples = sorted(positions[road_id])
            cuts[road_id] = [(a+b)*.5 for a, b in zip(samples, samples[1:]) if b-a > .01]
        nodes = [(road.id, i) for road in scene.roads for i in range(len(cuts[road.id])+1)]
        constraints = {(key(a, ad), key(b, bd)) for a, ad, b, bd in events}
        ranks, cyclic = _ranks(nodes, constraints)
    if cyclic:
        # 物理循环不能靠源标识或平均高度悄悄打破。
        raise ValueError(f"Preview road ordering has {len(cyclic)} unresolved fragments")

    result = RoadOrder()
    incidence = defaultdict(list)
    neighbor_cache = {}
    for road in scene.roads:
        incidence[road.start_node_id].append(road.id)
        incidence[road.end_node_id].append(road.id)
    paint_masks, junction_seams = _junction_paint_masks(scene, paths, height_ranges)
    for road in scene.roads:
        bounds = [0.0, *cuts[road.id], road.length]
        remainder = paths[road.id]
        original = tuple((p.x, p.z) for p in road.footprint.points) if road.footprint else ()
        if original and original[0] != original[-1]:
            original += (original[0],)
        boundaries = ()
        if original:
            boundaries = (_outer_boundaries(road, original, scene, paths, incidence,
                           neighbor_cache, projection_indexes, height_ranges, junction_seams)
                          if accurate else (original,))
        pieces = []
        for index, (start, end) in enumerate(zip(bounds, bounds[1:])):
            region = remainder
            if original and index < len(bounds)-2:
                region, remainder = _split_surface(remainder, road, start, end)
            piece = RoadPiece(road, index, start, end, ranks[road.id, index])
            if original:
                piece.rings = tuple(_rings(region))
                piece.boundaries = _clip_lines(boundaries, region, piece.rings) if cuts[road.id] else boundaries
                if road.id in paint_masks:
                    piece.paints = _paint_regions(road, region, piece.boundaries,
                        paint_masks[road.id])
            pieces.append(piece)
        result.pieces[road.id] = pieces
    result.diagnostics = {
        'version': VERSION, 'accurateIntersections': accurate, 'constraints': len(constraints),
        'overlapSamples': overlap_samples,
        'splitRoads': sum(bool(value) for value in cuts.values()),
        'fragments': len(nodes), 'ranks': max(ranks.values(), default=0)+1,
        'paintRegions': sum(len(piece.paints) for pieces in result.pieces.values() for piece in pieces),
    }
    return result
