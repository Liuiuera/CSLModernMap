"""水域路径的相切圆弧；不改变固定节点或线网几何。"""
from collections import Counter
import math

import numpy as np
import shapely
from shapely.geometry import LineString


def _chain_points(line, fixed=()):
    points = np.asarray(line.coords)
    closed = line.is_closed
    if closed:
        points = points[:-1]
    keep = []
    for index, point in enumerate(points):
        if tuple(point) in fixed or (not closed and index in (0, len(points)-1)):
            keep.append(point)
            continue
        before, after = point-points[index-1], points[(index+1) % len(points)]-point
        cross = before[0]*after[1]-before[1]*after[0]
        if abs(cross) > 1e-10*np.linalg.norm(before)*np.linalg.norm(after) or np.dot(before, after) <= 0:
            keep.append(point)
    return np.asarray(keep), closed


def _arc(point, incoming, outgoing, angle, radius, tolerance):
    distance = radius*math.tan(abs(angle)/2)
    first, last = point-incoming*distance, point+outgoing*distance
    side = 1 if angle > 0 else -1
    center = first+side*radius*np.array([-incoming[1], incoming[0]])
    start = math.atan2(*(first-center)[::-1])
    maximum_angle = 2*math.acos(max(-1.0, 1-tolerance/radius))
    count = max(2, math.ceil(abs(angle)/maximum_angle))
    angles = start+np.linspace(0, angle, count+1)
    sampled = center+radius*np.column_stack((np.cos(angles), np.sin(angles)))
    sampled[0], sampled[-1] = first, last
    return sampled


def _plans(points, closed, fixed, radius, minimum_radius, tolerance, small_reason):
    count = len(points)
    plans, skipped = {}, []
    for index in range(count):
        if (not closed and index in (0, count-1)) or tuple(points[index]) in fixed:
            continue
        incoming, outgoing = points[index]-points[index-1], points[(index+1) % count]-points[index]
        incoming, outgoing = incoming/np.linalg.norm(incoming), outgoing/np.linalg.norm(outgoing)
        angle = math.atan2(incoming[0]*outgoing[1]-incoming[1]*outgoing[0], np.dot(incoming, outgoing))
        if abs(angle) < 1e-10:
            continue
        if abs(angle) >= math.pi-1e-6:
            skipped.append({"vertex": index, "reason": "near_reversal", "used_radius_pixels": 0.0})
            continue
        plans[index] = {"incoming": incoming, "outgoing": outgoing, "angle": angle,
                        "factor": math.tan(abs(angle)/2)}
    # 两端圆角共同使用同一条边，按较紧的邻边同比缩小各角，避免相交。
    while plans:
        radii = {index: radius for index in plans}
        for index in range(count if closed else count-1):
            following = (index+1) % count
            length = np.linalg.norm(points[following]-points[index])
            requested = sum(radius*plans[i]["factor"] for i in (index, following) if i in plans)
            ratio = min(1.0, length/requested) if requested else 1.0
            for i in (index, following):
                if i in plans:
                    radii[i] = min(radii[i], radius*ratio)
        small = [index for index in plans if radii[index] <= minimum_radius+tolerance]
        if not small:
            for index in plans:
                plans[index]["radius"] = radii[index]
            break
        for index in small:
            skipped.append({"vertex": index, "reason": small_reason, "used_radius_pixels": 0.0})
            del plans[index]
    return plans, skipped


def round_river_centerlines(centerline, *, radius, width, tolerance, accept,
                           fixed_points=(), minimum_radius=None, small_radius_reason="insufficient_inner_radius"):
    """逐角尝试圆弧；accept 返回拒绝原因或 None，错误交由既有水域边界报告。"""
    pieces = list(shapely.get_parts(centerline))
    degree = Counter(end for line in pieces for end in (line.coords[0], line.coords[-1]))
    fixed = {point for point, value in degree.items() if value != 2} | set(fixed_points)
    minimum = width/2 if minimum_radius is None else minimum_radius
    chains = list(shapely.get_parts(shapely.line_merge(centerline)))
    controls = [_chain_points(line, fixed) for line in chains]
    arcs, operations = {}, []

    def build(candidate_arcs):
        lines = []
        for chain, (points, closed) in enumerate(controls):
            samples = []
            for vertex, point in enumerate(points):
                for sampled in candidate_arcs.get((chain, vertex), [point]):
                    if not samples or np.linalg.norm(sampled-samples[-1]) > tolerance*1e-7:
                        samples.append(sampled)
            if closed:
                samples.append(samples[0])
            lines.append(LineString(samples))
        return shapely.union_all(lines), lines

    if radius > 0:
        for chain, (points, closed) in enumerate(controls):
            plans, skipped = _plans(points, closed, fixed, radius, minimum, tolerance, small_radius_reason)
            operations.extend({"chain": chain, "requested_radius_pixels": radius, **item} for item in skipped)
            for vertex, plan in plans.items():
                reason = None
                for attempt in range(7):
                    used = plan["radius"]*.75**attempt
                    if used <= minimum+tolerance:
                        break
                    sampled = _arc(points[vertex], plan["incoming"], plan["outgoing"],
                                   plan["angle"], used, tolerance)
                    candidate_arcs = {**arcs, (chain, vertex): sampled}
                    candidate, lines = build(candidate_arcs)
                    reason = ("rounded_centerline_self_intersection" if any(not line.is_simple for line in lines)
                              else accept(candidate))
                    if reason is None:
                        arcs = candidate_arcs
                        if used < radius:
                            operations.append({"chain": chain, "vertex": vertex,
                                "requested_radius_pixels": radius, "used_radius_pixels": used,
                                "reason": previous_reason if attempt else "neighboring_short_edges"})
                        break
                    previous_reason = reason
                else:
                    used = 0.0
                if reason is not None or used <= minimum+tolerance:
                    operations.append({"chain": chain, "vertex": vertex, "requested_radius_pixels": radius,
                        "used_radius_pixels": 0.0, "reason": reason or small_radius_reason})
    result = build(arcs)[0] if arcs else centerline
    return result, operations
