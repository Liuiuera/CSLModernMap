"""面状水岸线与岛洞的圆弧过渡，保护既有河带和画布交点。"""
import numpy as np
import shapely
from shapely.geometry import LineString, Polygon, box

from .schematic_water_corners import round_river_centerlines
from .schematic_water_vector import _on_bounds, _split_at_bounds, same_water_topology


def round_water_shores(reference, surface, bounds, *, radius, minimum_radius, scale,
                       minimum_area, protected=()):
    """在八方向基准上构造圆弧，仅将其局部增减应用到当前水面。"""
    clip = box(*bounds)
    tolerance = .1*scale
    protection = shapely.union_all(protected).buffer(tolerance) if protected else Polygon()
    result, operations = surface, []
    for component, part in enumerate(shapely.get_parts(reference)):
        if part.is_empty:
            continue
        for ring_index, ring in enumerate((part.exterior, *part.interiors)):
            points = _split_at_bounds(np.asarray(ring.coords)[:-1], bounds)
            fixed = {tuple(point) for point in points[_on_bounds(points, bounds)]}
            original = Polygon(points)
            line = LineString([*points, points[0]])
            baseline = result

            def replace(candidate_line):
                candidate_ring = Polygon(candidate_line.coords)
                removed, added = original.difference(candidate_ring), candidate_ring.difference(original)
                if ring_index:
                    removed, added = added, removed
                return removed, added

            def accept(candidate_line):
                removed, added = replace(candidate_line)
                if not protection.is_empty and (removed.intersects(protection) or added.intersects(protection)):
                    return "protected_river_or_mouth"
                candidate = baseline.difference(removed).union(added)
                if (not same_water_topology(surface, candidate)
                        or not same_water_topology(surface.intersection(clip), candidate.intersection(clip))):
                    return "shore_arc_changed_water_topology"
                if any(p.area < minimum_area or any(Polygon(h).area < minimum_area for h in p.interiors)
                       for p in shapely.get_parts(candidate.intersection(clip)) if p.geom_type == "Polygon"):
                    return "shore_arc_below_minimum_area"
                return None

            rounded, adjustments = round_river_centerlines(line, radius=radius, width=0,
                tolerance=tolerance, accept=accept, fixed_points=fixed, minimum_radius=minimum_radius,
                small_radius_reason="insufficient_shore_radius")
            if rounded is not line:
                reason = accept(rounded)
                if reason is None:
                    removed, added = replace(rounded)
                    result = baseline.difference(removed).union(added)
                else:
                    adjustments = [{"reason": "final_"+reason, "used_radius_pixels": 0.0}]
            operations.extend({"component": component, "ring": ring_index,
                               "ring_type": "island" if ring_index else "shore", **operation}
                              for operation in adjustments)
    return result, [{"stage": "shore_rounding", **operation} for operation in operations]
