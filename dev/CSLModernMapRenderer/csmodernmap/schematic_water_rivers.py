"""局部细长水域的 Voronoi 中心线和八方向等宽河带。"""
from collections import Counter
from math import ceil, floor

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import nearest_points, substring

from .schematic import SchematicLayoutError, _astar
from .schematic_water_corners import round_river_centerlines
from .schematic_water_vector import same_water_topology


def _lines(geometry):
    return [line for line in shapely.get_parts(geometry)
            if line.geom_type == "LineString" and line.length > 0]


def river_centerlines(surface, spacing, minimum_branch):
    """加密边界的内 Voronoi 边；反复删除短终端支枝，保留连接和环。"""
    count = sum(np.ceil(np.linalg.norm(np.diff(np.asarray(ring.coords), axis=0), axis=1)/spacing).sum()
                for ring in (surface.exterior, *surface.interiors))
    if count > 100_000:
        raise ValueError("河流中心线边界采样超过 100000 点")
    sampled = shapely.segmentize(surface, spacing)
    edges = shapely.get_parts(shapely.voronoi_polygons(sampled, only_edges=True))
    shapely.prepare(surface)
    edges = edges[shapely.contains(surface, edges)]
    # GEOS 的共圆顶点可能产生约 1e-13 px 的边；固定精度只合并数值噪声。
    edges = shapely.set_precision(edges, spacing*1e-7)
    lines = _lines(shapely.line_merge(shapely.union_all(edges)))
    while lines:
        degree = Counter(end for line in lines for end in (line.coords[0], line.coords[-1]))
        kept = [line for line in lines if line.length >= minimum_branch
                or all(degree[end] > 1 for end in (line.coords[0], line.coords[-1]))]
        if len(kept) == len(lines):
            break
        lines = _lines(shapely.line_merge(shapely.union_all(kept)))
    return lines


def _attach_mouths(lines, core, distance):
    if core.is_empty:
        return lines
    degree = Counter(end for line in lines for end in (line.coords[0], line.coords[-1]))
    result = []
    for line in lines:
        coords = list(line.coords)
        for index in (0, -1):
            point = Point(coords[index])
            if degree[coords[index]] == 1 and point.distance(core) <= distance:
                mouth = nearest_points(point, core)[1]
                if mouth.distance(point) > 0:
                    if index == 0:
                        coords.insert(0, mouth.coords[0])
                    else:
                        coords.append(mouth.coords[0])
        result.append(LineString(coords))
    return result


def route_river_centerline(line, grid):
    """复用线网 A*，仅以该中心线走廊为硬边界，不传站点或线路占用。"""
    if line.is_ring:
        first = route_river_centerline(substring(line, 0, line.length/2), grid)
        last = route_river_centerline(substring(line, line.length/2, line.length), grid)
        return LineString([*first.coords, *list(last.coords)[1:]])
    # 简化不改变支流共享端点；长段分段限制 A* 搜索窗口。
    guide = shapely.segmentize(line.simplify(2*grid), 64*grid)
    anchors = [(round(x/grid), round(y/grid)) for x, y in guide.coords]
    routed = [anchors[0]]
    direction = None
    for first, last in zip(anchors, anchors[1:]):
        if first == last:
            continue
        a, b = (first[0]*grid, first[1]*grid), (last[0]*grid, last[1]*grid)
        # 每一段的走廊跟随原折线；简化误差已限定为两格。
        start = line.project(Point(a))
        end = line.project(Point(b))
        segment = substring(line, min(start, end), max(start, end))
        corridor = shapely.union_all([segment.buffer(3*grid), LineString((a, b)).buffer(grid)])
        x0, y0, x1, y1 = corridor.bounds
        bounds = (floor(x0/grid), floor(y0/grid), ceil(x1/grid), ceil(y1/grid))
        xs, ys = np.meshgrid(np.arange(bounds[0], bounds[2]+1), np.arange(bounds[1], bounds[3]+1))
        inside = shapely.intersects_xy(corridor, xs*grid, ys*grid)
        blocked = set(zip(xs[~inside].tolist(), ys[~inside].tolist()))
        path, direction = _astar(first, {last}, direction, set(), {}, bounds, hard_block_cells=blocked)
        routed.extend(path[1:])
    compact = [routed[0]]
    for index in range(1, len(routed)-1):
        before, point, after = routed[index-1:index+2]
        if (point[0]-before[0], point[1]-before[1]) != (after[0]-point[0], after[1]-point[1]):
            compact.append(point)
    compact.append(routed[-1])
    if len(set(compact)) < 2:
        # 小于一格的内部连接收缩到共享节点；整体水域拓扑仍在替换时检查。
        return LineString()
    return LineString([(x*grid, y*grid) for x, y in compact])


def _replacement_reason(before, after, clip, minimum_area):
    if (not same_water_topology(before, after)
            or not same_water_topology(before.intersection(clip), after.intersection(clip))):
        return "river_band_changed_water_topology"
    if any(part.area < minimum_area or any(Polygon(hole).area < minimum_area for hole in part.interiors)
           for part in shapely.get_parts(after.intersection(clip)) if part.geom_type == "Polygon"):
        return "river_band_below_minimum_area"
    return None


def schematize_rivers(surface, bounds, *, scale, width, minimum_area, protected_areas=None):
    """按局部宽度分开细长河段；逐段验证，受限段保留已有合法岸线。"""
    radius, grid = 24*scale, 2*scale
    clip = box(*bounds)
    parts = list(shapely.get_parts(surface)) if not surface.is_empty else []
    source_parts = list(parts)
    operations, regions = [], []
    for component, original in enumerate(parts):
        core = original.buffer(-radius).buffer(radius)
        residuals = sorted(shapely.get_parts(original.difference(core)), key=lambda part: -part.area)
        for residual in residuals:
            if residual.geom_type != "Polygon" or residual.area < minimum_area:
                continue
            lines = river_centerlines(residual, grid, max(3*width, 2*radius))
            length = sum(line.length for line in lines)
            average_width = residual.area/length if length else 0
            if length < max(80*scale, 6*width) or length < 6*average_width:
                continue
            operation = {"component": component, "stage": "river"}
            try:
                routed = [route_river_centerline(line, grid)
                          for line in _attach_mouths(lines, core, radius)]
            except SchematicLayoutError as error:
                operations.append({**operation, "reason": "centerline_routing_failed",
                                   "detail": str(error)})
                continue
            routed = [line for line in routed if not line.is_empty]
            if not routed:
                operations.append({**operation, "reason": "centerline_collapsed_to_grid"})
                continue
            centerline = shapely.union_all(routed)
            band = centerline.buffer(width/2, cap_style="round", join_style="round")
            candidate = parts[component].difference(residual).union(band)
            combined = shapely.union_all(parts[:component]+[candidate]+parts[component+1:])
            reason = _replacement_reason(surface, combined, clip, minimum_area)
            if reason is None:
                parts[component] = candidate
                regions.append((component, residual, centerline, band))
                continue
            operations.append({**operation, "reason": reason})
    result = shapely.union_all(parts) if parts else Polygon()
    sharp_surface = result
    rounded_bands = {}

    def replace_bands(replacements):
        changed = list(source_parts)
        for index, (component, residual, _, band) in enumerate(regions):
            changed[component] = changed[component].difference(residual).union(replacements.get(index, band))
        return shapely.union_all(changed)

    for index, (_, _, centerline, _) in enumerate(regions):
        def accept(candidate_line):
            band = candidate_line.buffer(width/2, cap_style="round", join_style="round")
            combined = replace_bands({**rounded_bands, index: band})
            return (_replacement_reason(sharp_surface, combined, clip, minimum_area)
                    or _replacement_reason(surface, combined, clip, minimum_area))
        rounded, adjustments = round_river_centerlines(centerline, radius=2*width, width=width,
                                                       tolerance=.1*scale, accept=accept)
        if rounded is not centerline:
            reason = accept(rounded)
            if reason is None:
                rounded_bands[index] = rounded.buffer(width/2, cap_style="round", join_style="round")
            else:
                adjustments = [{"reason": "final_"+reason, "used_radius_pixels": 0.0}]
        operations.extend({"stage": "river_rounding", "region": index, **item} for item in adjustments)
    if rounded_bands:
        result = replace_bands(rounded_bands)
    if protected_areas is not None:
        for index, (_, residual, _, band) in enumerate(regions):
            protected_areas.extend((residual, rounded_bands.get(index, band)))
    return result, operations
