"""构建跨输出共享的道路连通链和屏幕文字路径。

连通规则参考采用 MIT 许可证的 Vellum 实现；文字先由 Pillow 完整排版，再沿路径绘制，以保留字距和连字效果。"""
from dataclasses import dataclass
from math import atan2, degrees, hypot

from ..road_hierarchy import classify_segment


@dataclass(frozen=True)
class RoadChain:
    id: int
    text: str
    source: str
    level: str
    points: tuple
    members: tuple[int, ...]


def street_chains(mapdata, *, road_ids=None):
    """将同名同类道路连接为可沿线排布文字的稳定链。"""
    segments = {}
    point_sets = {}
    groups = {}
    types = mapdata.segment_types
    for segment in mapdata.segments:
        if road_ids is not None and segment.id not in road_ids:
            continue
        custom = str(segment.custom_name or "").strip()
        text = custom or str(getattr(segment, "game_name", "") or "").strip()
        definition = types.get(segment.name) or types.get(segment.icls)
        if not text or definition is None:
            continue
        points = []
        for point in segment.points:
            if not points or (point.x, point.z) != (points[-1].x, points[-1].z):
                points.append(point)
        if len(points) < 2:
            continue
        level = classify_segment(segment, definition).value
        key = (text, "CUSTOM" if custom else "GAME", level)
        groups.setdefault(key, []).append(segment.id)
        segments[segment.id] = segment
        point_sets[segment.id] = points
    result = []
    for (text, source, level), ids in sorted(groups.items()):
        links = {}
        for id_ in ids:
            segment = segments[id_]
            for node in (segment.start_node_id, segment.end_node_id):
                links.setdefault(node, []).append(id_)
        unused = set(ids)
        while unused:
            seed = min(unused)
            unused.remove(seed)
            segment = segments[seed]
            points = list(point_sets[seed])
            members = [seed]
            ends = [segment.start_node_id, segment.end_node_id]
            for side in (0, 1):
                while True:
                    best = None
                    # 在路口继续连接名称和等级相同的最直未用路段，转角不超过 60 度。
                    for next_id in sorted(id_ for id_ in links[ends[side]] if id_ in unused):
                        other = segments[next_id]
                        extension = list(point_sets[next_id])
                        if side == 0:
                            if other.start_node_id == ends[0]:
                                extension.reverse()
                            a, b, c = extension[-2], points[0], points[1]
                            next_node = other.start_node_id if other.end_node_id == ends[0] else other.end_node_id
                            joint = extension[-1]
                        else:
                            if other.end_node_id == ends[1]:
                                extension.reverse()
                            a, b, c = points[-2], points[-1], extension[1]
                            next_node = other.end_node_id if other.start_node_id == ends[1] else other.start_node_id
                            joint = extension[0]
                        if hypot(joint.x-b.x, joint.z-b.z) > .01:
                            continue
                        incoming = atan2(b.z-a.z, b.x-a.x)
                        outgoing = atan2(c.z-b.z, c.x-b.x)
                        turn = abs((degrees(outgoing-incoming)+180) % 360-180)
                        if turn <= 60 and (best is None or turn < best[0]):
                            best = (turn, next_id, extension, next_node)
                    if best is None:
                        break
                    _, next_id, extension, next_node = best
                    unused.remove(next_id)
                    members.append(next_id)
                    points = extension[:-1] + points if side == 0 else points + extension[1:]
                    ends[side] = next_node
            result.append(RoadChain(min(members), text, source, level, tuple(points), tuple(sorted(members))))
    return result


def text_path(points, width, spacing, stroke, height):
    """为排版后的整段文字生成清晰的栅格条带位置。"""
    points = [tuple(point) for point in points]
    lengths = [hypot(b[0]-a[0], b[1]-a[1]) for a, b in zip(points, points[1:])]
    total = sum(lengths)
    margin = width / 2 + stroke + 4
    if total < 2 * margin:
        return []

    def sample(distance):
        acc = 0
        for a, b, length in zip(points, points[1:], lengths):
            if length > 0 and distance <= acc + length + 1e-8:
                t = (distance-acc) / length
                return a[0]+(b[0]-a[0])*t, a[1]+(b[1]-a[1])*t, degrees(atan2(b[1]-a[1], b[0]-a[0]))
            acc += length
        return *points[-1], 0

    centers = [total / 2] if total < spacing * 1.5 else []
    center = max(spacing / 2, margin)
    while not centers and center <= total-margin:
        centers.append(center)
        center += spacing
    if centers and len(centers) == 1 and total >= spacing * 1.5:
        while center <= total-margin:
            centers.append(center)
            center += spacing
    result = []
    for center in centers:
        middle = sample(center)
        reverse = not -90-1e-7 <= middle[2] <= 90+1e-7
        strips = []
        for u in range(0, int(width), 2):
            span = min(2, width-u)
            offset = u+span/2-width/2
            x, y, angle = sample(center-offset if reverse else center+offset)
            if reverse:
                angle = (angle+360) % 360-180
            strips.append((x, y, angle, u, span))
        angles = [strip[2] for strip in strips]
        if max(angles)-min(angles) > 60 or any(abs(b-a) > 30 for a, b in zip(angles, angles[1:])):
            continue
        extent = height / 2 + stroke + 4
        box = (min(strip[0] for strip in strips)-extent, min(strip[1] for strip in strips)-extent,
               max(strip[0] for strip in strips)+extent, max(strip[1] for strip in strips)+extent)
        result.append((middle, strips, box))
    return result
