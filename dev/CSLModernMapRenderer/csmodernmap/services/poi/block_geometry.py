"""计算街区聚合所需的建筑占地间隙。"""
import math


def _cross(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])


def _intersects(a, b, c, d):
    if max(a[0], b[0]) < min(c[0], d[0]) or max(c[0], d[0]) < min(a[0], b[0]):
        return False
    if max(a[1], b[1]) < min(c[1], d[1]) or max(c[1], d[1]) < min(a[1], b[1]):
        return False
    return _cross(a, b, c)*_cross(a, b, d) <= 0 and _cross(c, d, a)*_cross(c, d, b) <= 0


def _point_distance(p, a, b):
    dx, dz = b[0]-a[0], b[1]-a[1]
    length = dx*dx + dz*dz
    t = max(0, min(1, ((p[0]-a[0])*dx+(p[1]-a[1])*dz)/length)) if length else 0
    return math.hypot(p[0]-a[0]-t*dx, p[1]-a[1]-t*dz)


class BlockGeometry:
    def __init__(self, mapdata, candidates):
        wanted = {
            p.source.id for p in candidates
            if getattr(getattr(p, "source", None), "kind", "") == "building"
        }
        self.polygons = {}
        self.bounds = {}
        self.max_extent = 0.0
        for b in getattr(mapdata, "buildings", ()):
            if b.id not in wanted:
                continue
            points = getattr(b, "points", None)
            if points is None:
                points = getattr(getattr(b, "footprint", None), "points", ())
            polygon = [(p.x, p.z) for p in points]
            if not polygon:
                continue
            self.polygons[b.id] = polygon
            bounds = (min(p[0] for p in polygon), min(p[1] for p in polygon),
                      max(p[0] for p in polygon), max(p[1] for p in polygon))
            self.bounds[b.id] = bounds
            # 搜索半径保守涵盖较大或旋转的建筑地块。
            self.max_extent = max(self.max_extent, math.hypot(
                bounds[2] - bounds[0], bounds[3] - bounds[1]))

    def near(self, left, right, distance):
        a, b = (left.anchor.x, left.anchor.z), (right.anchor.x, right.anchor.z)
        lp = self.polygons.get(left.source.id, [a])
        rp = self.polygons.get(right.source.id, [b])
        # 在检查全部矩形边之前先快速排除不相交边界框。
        lb = self.bounds.get(left.source.id, (a[0], a[1], a[0], a[1]))
        rb = self.bounds.get(right.source.id, (b[0], b[1], b[0], b[1]))
        dx = max(0, lb[0] - rb[2], rb[0] - lb[2])
        dz = max(0, lb[1] - rb[3], rb[1] - lb[3])
        if math.hypot(dx, dz) > distance:
            return False
        for p, q in zip(lp, lp[1:]+lp[:1]):
            for r, s in zip(rp, rp[1:]+rp[:1]):
                if _intersects(p, q, r, s) or min(
                    _point_distance(p, r, s), _point_distance(q, r, s),
                    _point_distance(r, p, q), _point_distance(s, p, q)) <= distance:
                    return True
        return False
