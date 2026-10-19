"""闭合岸线的方向限制与配对边移动；不参与交通布局。

采用 Buchin 等 (2016, doi:10.1145/2818373) 的方向限制、扫过区域检查和
面积补偿思想。这里只处理多边形环，不实现平面分区的共享边与交汇节点。
"""
from itertools import product
from bisect import bisect_left
import math

import numpy as np
import shapely
from shapely.affinity import translate
from shapely.geometry import LineString, Polygon
from shapely.strtree import STRtree


_DIRECTIONS = np.array([(1, 0), (1, 1), (0, 1), (-1, 1),
                        (-1, 0), (-1, -1), (0, -1), (1, -1)], dtype=float)


def _parts(surface):
    return [] if surface.is_empty else [surface] if surface.geom_type == "Polygon" else list(surface.geoms)


def _bijection(before, after):
    if len(before) != len(after):
        return None
    tree = STRtree(after)
    mapping = []
    for polygon in before:
        matches = [int(i) for i in tree.query(polygon, predicate="intersects")
                   if polygon.relate_pattern(after[i], "T********")]
        if len(matches) != 1:
            return None
        mapping.append(matches[0])
    return mapping if len(set(mapping)) == len(mapping) else None


def same_water_topology(before, after, *, ordered=False):
    """已知环身份时比较包含树；集合运算后用保守的正面积对应匹配身份。"""
    if not after.is_valid or after.geom_type not in ("Polygon", "MultiPolygon"):
        return False
    old, new = _parts(before), _parts(after)
    if ordered:
        if len(old) != len(new) or any(len(a.interiors) != len(b.interiors) for a, b in zip(old, new)):
            return False
        def parents(parts):
            rings = [Polygon(ring) for part in parts for ring in (part.exterior, *part.interiors)]
            tree = STRtree(rings)
            result = []
            for i, ring in enumerate(rings):
                containing = [int(j) for j in tree.query(ring.representative_point(), predicate="within")
                              if j != i and rings[j].area > ring.area]
                result.append(min(containing, key=lambda j: rings[j].area) if containing else None)
            return result
        return parents(old) == parents(new)
    mapping = _bijection(old, new)
    return mapping is not None and all(
        _bijection([Polygon(ring) for ring in part.interiors],
                   [Polygon(ring) for ring in new[mapping[i]].interiors]) is not None
        for i, part in enumerate(old))


def _on_bounds(points, bounds):
    if bounds is None:
        return np.zeros(len(points), dtype=bool)
    x0, y0, x1, y1 = bounds
    return ((abs(points[:, 0]-x0) < 1e-8) | (abs(points[:, 0]-x1) < 1e-8) |
            (abs(points[:, 1]-y0) < 1e-8) | (abs(points[:, 1]-y1) < 1e-8))


def _clean(points, bounds=None):
    points = np.asarray(points, dtype=float)
    delta = np.roll(points, -1, axis=0)-points
    points = points[np.linalg.norm(delta, axis=1) > 1e-9]
    if len(points) < 3:
        return points
    incoming = points-np.roll(points, 1, axis=0)
    outgoing = np.roll(points, -1, axis=0)-points
    cross = incoming[:, 0]*outgoing[:, 1]-incoming[:, 1]*outgoing[:, 0]
    straight = (abs(cross) <= 1e-10*np.linalg.norm(incoming, axis=1)*np.linalg.norm(outgoing, axis=1))
    return points[~(straight & (np.sum(incoming*outgoing, axis=1) > 0) & ~_on_bounds(points, bounds))]


def _rings(surface, bounds=None):
    return [[_clean(np.asarray(ring.coords)[:-1], bounds) for ring in (part.exterior, *part.interiors)]
            for part in _parts(surface)]


def _split_at_bounds(points, bounds):
    if bounds is None:
        return points
    result = []
    for i, start in enumerate(points):
        vector = points[(i+1) % len(points)]-start
        cuts = [(0.0, start)]
        for axis, value in ((0, bounds[0]), (0, bounds[2]), (1, bounds[1]), (1, bounds[3])):
            if abs(vector[axis]) < 1e-12:
                continue
            t = (value-start[axis])/vector[axis]
            if 1e-10 < t < 1-1e-10:
                point = start+t*vector
                point[axis] = value
                cuts.append((t, point))
        result.extend(point for _, point in sorted(cuts, key=lambda cut: cut[0]))
    return _clean(result, bounds)


def _surface(groups):
    polygons = [Polygon(rings[0], rings[1:]) for rings in groups]
    return polygons[0] if len(polygons) == 1 else shapely.MultiPolygon(polygons)


def _sector(vector):
    angle = math.atan2(vector[1], vector[0]) % (2*math.pi)
    direction = angle/(math.pi/4)
    nearest = round(direction)
    return [nearest % 8] if abs(direction-nearest) < 1e-10 else [int(direction), (int(direction)+1) % 8]


def _endpoint_directions(points, bounds=None):
    result = []
    for i, point in enumerate(points):
        vectors = [points[i-1]-point, points[(i+1) % len(points)]-point]
        angles = [math.atan2(v[1], v[0]) for v in vectors]
        original_turn = math.sin(angles[1]-angles[0])
        choices = []
        sectors = [_sector(vector) for vector in vectors]
        if bounds is not None:
            for axis, value in ((0, bounds[0]), (0, bounds[2]), (1, bounds[1]), (1, bounds[3])):
                if abs(point[axis]-value) < 1e-8 and vectors[0][axis]*vectors[1][axis] < -1e-16:
                    # 横穿画布边界的边必须立即离开边界，不能先沿边界滑行。
                    sectors = [[direction for direction in options
                                if abs(vector[axis]) < 1e-10 or _DIRECTIONS[direction, axis] != 0]
                               for options, vector in zip(sectors, vectors)]
        for a, b in product(*sectors):
            if a == b or math.sin((b-a)*math.pi/4)*original_turn < -1e-10:
                continue
            cost = sum(math.remainder(d*math.pi/4-angle, 2*math.pi)**2
                       for d, angle in zip((a, b), angles))
            choices.append((cost, a, b))
        if not choices:
            raise ValueError("岸线节点不能分配互异的八方向")
        _, incoming, outgoing = min(choices)
        result.append((incoming, outgoing))
    return result


def _staircase(start, end, first, last, count):
    vector = end-start
    sectors = _sector(vector)
    if len(sectors) == 1:
        return np.array([start, end])
    lower, upper = sectors
    u, v = _DIRECTIONS[[lower, upper]]
    det = u[0]*v[1]-u[1]*v[0]
    multiple = 2 if first == last else 4
    count = max(multiple, math.ceil(count/multiple)*multiple)
    du = u*((vector[0]*v[1]-vector[1]*v[0])/det/count)
    dv = v*((u[0]*vector[1]-u[1]*vector[0])/det/count)
    a, b = (du, dv) if first == lower else (dv, du)
    # 同侧的 L 连续排列，在远离端点处换侧，避免尖角处两条岸线折线相穿。
    # 两种顺序的格数相同，故有符号面积抵消；加密后横向偏离随 1/count 缩小。
    if first == last:
        forward = np.arange(count) < count//2
    else:
        forward = (np.arange(count) < count//4) | (np.arange(count) >= 3*count//4)
    origins = start+np.arange(count)[:, None]*(vector/count)
    elbows = origins+np.where(forward[:, None], a, b)
    ends = start+np.arange(1, count+1)[:, None]*(vector/count)
    path = np.concatenate(([start], np.stack((elbows, ends), axis=1).reshape(-1, 2)))
    path = np.clip(path, np.minimum(start, end), np.maximum(start, end))
    path[-1] = end
    return path


def restrict_water_directions(surface, step, *, bounds=None):
    """端点固定、局部加密的八方向折线；无可用结果时明确报错。"""
    if surface.is_empty:
        return surface
    groups = _rings(surface)
    groups = [[_split_at_bounds(points, bounds) for points in rings] for rings in groups]
    paths = []
    for rings in groups:
        for points in rings:
            assignments = _endpoint_directions(points, bounds)
            for i, start in enumerate(points):
                end = points[(i+1) % len(points)]
                paths.append((start, end, assignments[i][1],
                              (assignments[(i+1) % len(points)][0]+4) % 8))
    segments = shapely.linestrings(np.array([[a, b] for a, b, *_ in paths]))
    tree = STRtree(segments)
    # 共享端点属于同一节点，预先索引；不要对每个邻边重复做四次数组比较。
    endpoints = [(tuple(start), tuple(end)) for start, end, *_ in paths]
    incident = {}
    for i, pair in enumerate(endpoints):
        for point in pair:
            incident.setdefault(point, set()).add(i)
    counts = []
    for i, (start, end, *_directions) in enumerate(paths):
        if len(_sector(end-start)) == 1:
            counts.append(1)
            continue
        nearby = tree.query(segments[i], predicate="dwithin", distance=step)
        # 相邻边共享端点；节点方向分配处理其局部次序。
        touching = incident[endpoints[i][0]] | incident[endpoints[i][1]]
        separate = [int(j) for j in nearby if j not in touching]
        clearance = float(np.min(shapely.distance(segments[i], segments[separate]))) if separate else step
        tolerance = min(step/2, clearance/3)
        if tolerance <= 1e-9:
            raise ValueError("保留岸线存在非相邻接触，不能安全限制方向")
        counts.append(max(1, math.ceil(np.linalg.norm(end-start)/(4*tolerance))))
    for attempt in range(7):
        if sum(counts)*6 > 200_000:
            raise ValueError("八方向岸线超过局部细分上限")
        converted = [_staircase(*path, count) for path, count in zip(paths, counts)]
        result_groups, offset = [], 0
        for rings in groups:
            result_rings = []
            for points in rings:
                result_rings.append(_clean(np.concatenate([path[:-1] for path in converted[offset:offset+len(points)]]), bounds))
                offset += len(points)
            result_groups.append(result_rings)
        candidate = _surface(result_groups)
        if same_water_topology(surface, candidate, ordered=True):
            return candidate
        counts = [2*count for count in counts]
    raise ValueError("八方向岸线不能保持水体与岛洞关系")


def _cross(a, b):
    return float(a[0]*b[1]-a[1]*b[0])


def _fit_chain(points):
    """固定端点和有符号面积，用最多三条八方向边拟合岸段。"""
    local = points-points[0]
    end = local[-1]
    area = float(np.sum(local[:-1, 0]*local[1:, 1]-local[:-1, 1]*local[1:, 0]))
    candidates = []
    if len(_sector(end)) == 1 and abs(area) <= 1e-9:
        candidates.append(points[[0, -1]])
    for direction in range(8):
        for turn in (1, 2):
            u, v = _DIRECTIONS[[direction, (direction+turn) % 8]]
            det = _cross(u, v)
            a, b = _cross(end, v)/det, _cross(u, end)/det
            if a <= 1e-9 or b <= 1e-9:
                continue
            # u,v,u 三段的两倍有符号面积为 b*(2*x-a)*det。
            for first, middle, length, width, cross in ((u, v, a, b, det), (v, u, b, a, -det)):
                x = (length+area/(width*cross))/2
                if x < -1e-9 or x > length+1e-9:
                    continue
                x = min(length, max(0.0, x))
                candidate = np.array([local[0], x*first, x*first+width*middle, end])+points[0]
                candidate[[0, -1]] = points[[0, -1]]
                candidate = candidate[np.r_[True, np.linalg.norm(np.diff(candidate, axis=0), axis=1) > 1e-9]]
                candidates.append(candidate)
    return sorted(candidates, key=lambda path: (len(path), float(np.linalg.norm(np.diff(path, axis=0), axis=1).sum())))


def fit_water_chains(surface, tolerance, *, bounds=None, origin=(0, 0)):
    """先合并长岸段，保留细颈附近无法安全替换的短边；每环至多两轮。"""
    if surface.is_empty:
        return surface
    groups = _rings(surface, bounds)
    baseline_area = surface.area
    for group_index, rings in enumerate(groups):
        for ring_index in range(len(rings)):
            points = rings[ring_index]
            obstacles = [LineString(np.vstack((r, r[0]))) for gi, group in enumerate(groups)
                         for ri, r in enumerate(group) if (gi, ri) != (group_index, ring_index)]
            if bounds is not None:
                left, bottom, right, top = surface.bounds
                obstacles.extend([LineString([(x, bottom-tolerance), (x, top+tolerance)]) for x in (bounds[0], bounds[2])])
                obstacles.extend([LineString([(left-tolerance, y), (right+tolerance, y)]) for y in (bounds[1], bounds[3])])
            others = STRtree(obstacles)
            for _pass in range(2):
                n = len(points)
                if n <= 4:
                    break
                closed = np.vstack((points, points[0]))
                segments = shapely.linestrings(np.stack((points, np.roll(points, -1, axis=0)), axis=1))
                tree = STRtree(segments)
                pinned = _on_bounds(closed, bounds)
                pieces, swept_regions, batch = [], [], 0
                index = 0
                while index < n:
                    replacement, stop = None, index+1
                    for span in sorted({min(size, n-index) for size in (32, 16, 8, 4)}, reverse=True):
                        if span < 4 or np.any(pinned[index:index+span+1]):
                            continue
                        chain = closed[index:index+span+1]
                        old_line = LineString(chain)
                        for fitted in _fit_chain(chain):
                            if len(fitted) >= len(chain):
                                continue
                            line = LineString(fitted)
                            if line.length > old_line.length+1e-8 or old_line.hausdorff_distance(line) > tolerance:
                                continue
                            # 端点附近不能沿相邻原边折返；它们不属于下方的非邻边碰撞检查。
                            if not (line.intersection(segments[(index-1) % n]).equals(shapely.Point(chain[0])) and
                                    line.intersection(segments[(index+span) % n]).equals(shapely.Point(chain[-1]))):
                                continue
                            # 双向缓冲包含检查约束整条线，而不只约束输入顶点。
                            if not (old_line.buffer(tolerance).covers(line) and line.buffer(tolerance).covers(old_line)):
                                continue
                            swept = shapely.MultiLineString([chain, fitted]).convex_hull
                            exempt = {(j % n) for j in range(index-1, index+span+1)}
                            if (any(int(j) not in exempt for j in tree.query(swept, predicate="intersects")) or
                                    len(others.query(swept, predicate="intersects")) or
                                    any(swept.intersects(region) for region in swept_regions)):
                                continue
                            replacement, stop = fitted[:-1], index+span
                            swept_regions.append(swept)
                            batch += 1
                            break
                        if replacement is not None:
                            break
                    pieces.append(points[index:index+1] if replacement is None else replacement)
                    index = stop
                if not batch:
                    break
                candidate_points = _clean(np.concatenate(pieces), bounds)
                rings[ring_index] = candidate_points
                candidate = _surface(groups)
                if (not same_water_topology(surface, candidate, ordered=True) or
                        not translate(candidate, xoff=origin[0], yoff=origin[1]).is_valid or
                        abs(candidate.area-baseline_area) > max(1e-6, baseline_area*1e-9)):
                    rings[ring_index] = points
                    break
                points = candidate_points
            rings[ring_index] = points
    result = _surface(groups)
    return result


def _move(points, index, side):
    """平移一条边，直到本边或相邻边首次退化。"""
    size = len(points)
    a, b, c, d = (points[j % size] for j in (index-1, index, index+1, index+2))
    edge = c-b
    normal = side*np.array([-edge[1], edge[0]])/np.linalg.norm(edge)
    previous, following = b-a, d-c
    p, q = float(previous@normal), float(following@normal)
    if abs(p) < 1e-10 or abs(q) < 1e-10:
        return None
    vb, vc = previous/p, following/q
    limits = [-p] if p < 0 else []
    if q > 0:
        limits.append(q)
    rate = float((vc-vb)@edge/(edge@edge))
    if rate < -1e-10:
        limits.append(-1/rate)
    if not limits:
        return None
    distance = min(limits)
    if distance <= 1e-8:
        return None
    swept = Polygon([b, c, c+distance*vc, b+distance*vb]).convex_hull
    if swept.geom_type != "Polygon":
        return None
    # 环有符号面积的变化为 linear*t + quadratic*t²。
    linear = (_cross(a, vb)+_cross(vb, c)+_cross(b, vc)+_cross(vc, d))/2
    quadratic = _cross(vb, vc)/2
    change = linear*distance+quadratic*distance**2
    return index, vb, vc, distance, linear, quadratic, change, swept


def _apply(points, move, distance):
    result = points.copy()
    i, vb, vc = move[:3]
    result[i] += distance*vb
    result[(i+1) % len(points)] += distance*vc
    # 退化边的两个端点必须完全相同，避免平移回画布后舍入产生极小自交。
    for vertex, neighbor in ((i, (i-1) % len(points)), ((i+1) % len(points), (i+2) % len(points))):
        if np.linalg.norm(result[vertex]-points[neighbor]) < 1e-8:
            result[vertex] = points[neighbor]
    if np.linalg.norm(result[i]-result[(i+1) % len(points)]) < 1e-8:
        result[(i+1) % len(points)] = result[i]
    return result


def _blocked(move, points, tree, segments, others):
    i, *_, swept = move
    n = len(points)
    for j in tree.query(swept, predicate="intersects"):
        if j in {(i-1) % n, i, (i+1) % n}:
            continue
        hit = swept.intersection(segments[j])
        if hit.geom_type == "Point" and any(
                np.linalg.norm(np.array(hit.coords[0])-points[k % n]) < 1e-8 for k in (i-1, i+2)):
            continue
        return True
    return bool(len(others.query(swept, predicate="intersects")))


def simplify_water_edges(surface, tolerance, *, bounds=None, origin=(0, 0)):
    """闭合环上的配对平移；安全操作耗尽即可停止，不强求指定顶点数。"""
    if surface.is_empty:
        return surface
    groups = _rings(surface, bounds)
    baseline_area = surface.area
    # 扫过区域代价有显示尺度上限，防止为了顶点目标过度改变形状。
    for group_index, rings in enumerate(groups):
        for ring_index in range(len(rings)):
            points = rings[ring_index]
            obstacles = [LineString(np.vstack((r, r[0]))) for gi, group in enumerate(groups)
                         for ri, r in enumerate(group) if (gi, ri) != (group_index, ring_index)]
            if bounds is not None:
                x0, y0, x1, y1 = bounds
                left, bottom, right, top = surface.bounds
                obstacles.extend([LineString([(x, bottom-tolerance), (x, top+tolerance)]) for x in (x0, x1)])
                obstacles.extend([LineString([(left-tolerance, y), (right+tolerance, y)]) for y in (y0, y1)])
            others = STRtree(obstacles)
            # 批量执行互不干扰的操作，限定八轮，避免预览耗时随化简次数平方增长。
            for _pass in range(8):
                if len(points) <= 4:
                    break
                segments = shapely.linestrings(np.stack((points, np.roll(points, -1, axis=0)), axis=1))
                tree = STRtree(segments)
                pinned = _on_bounds(points, bounds)
                # 补偿留在同一段未跨越画布边界的岸线上，避免向画布外转移面积。
                labels = np.zeros(len(points), dtype=int)
                starts = np.flatnonzero(pinned)
                if len(starts):
                    order = (np.arange(len(points))+starts[0]) % len(points)
                    labels[order] = np.cumsum(pinned[order])
                moves = []
                for i in range(len(points)):
                    if pinned[i] or pinned[(i+1) % len(points)]:
                        continue
                    for side in (-1, 1):
                        move = _move(points, i, side)
                        if (move is not None and abs(move[6]) > 1e-10 and
                                move[3] <= tolerance and not _blocked(move, points, tree, segments, others)):
                            moves.append(move)
                moves.sort(key=lambda move: abs(move[6]))
                positive = [move for move in moves if move[6] > 0]
                negative = [move for move in moves if move[6] < 0]
                costs = {1: [abs(move[6]) for move in positive], -1: [abs(move[6]) for move in negative]}
                used, swept_regions, batch = set(), [], 0
                swept_tree, pending = STRtree([]), []
                candidate_points = points.copy()
                for move in moves:
                    if move[0] in used:
                        continue
                    opposites = negative if move[6] > 0 else positive
                    start = bisect_left(costs[-1 if move[6] > 0 else 1], abs(move[6]))
                    for compensate in opposites[start:start+64]:
                        if compensate[0] in used:
                            continue
                        i, j = move[0], compensate[0]
                        if labels[i] != labels[j]:
                            continue
                        if min((i-j) % len(points), (j-i) % len(points)) < 3:
                            continue
                        if move[7].intersects(compensate[7]):
                            continue
                        if (len(swept_tree.query(move[7], predicate="intersects")) or
                                len(swept_tree.query(compensate[7], predicate="intersects")) or
                                (pending and (np.any(shapely.intersects(pending, move[7])) or
                                              np.any(shapely.intersects(pending, compensate[7]))))):
                            continue
                        # 单调扫过面积在 [0, limit] 内求补偿位移。
                        lo, hi = 0.0, compensate[3]
                        for _ in range(40):
                            mid = (lo+hi)/2
                            area = compensate[4]*mid+compensate[5]*mid*mid
                            if abs(area) < abs(move[6]):
                                lo = mid
                            else:
                                hi = mid
                        candidate_points = _apply(_apply(candidate_points, move, move[3]), compensate, (lo+hi)/2)
                        used.update((index+offset) % len(points) for index in (i, j) for offset in range(-2, 3))
                        swept_regions.extend((move[7], compensate[7]))
                        pending.extend((move[7], compensate[7]))
                        if len(pending) >= 32:
                            swept_tree = STRtree(swept_regions)
                            pending.clear()
                        batch += 1
                        break
                candidate_points = _clean(candidate_points, bounds)
                if not batch or len(candidate_points) >= len(points) or len(candidate_points) < 3:
                    break
                rings[ring_index] = candidate_points
                candidate = _surface(groups)
                # 环身份与父子关系由 groups 固定；扫过区域已阻止跨越其他边界。
                # 此处检查重建几何与面积，完整对应检查留在阶段出口。
                if (not candidate.is_valid or not translate(candidate, xoff=origin[0], yoff=origin[1]).is_valid or
                        abs(candidate.area-baseline_area) > max(1e-6, baseline_area*1e-9)):
                    rings[ring_index] = points
                    break
                points = candidate_points
            rings[ring_index] = points
    result = _surface(groups)
    return result
