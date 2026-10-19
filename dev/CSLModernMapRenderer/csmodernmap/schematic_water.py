"""独立提取、投影并概括示意水面。"""

import numpy as np
import shapely
from shapely.affinity import affine_transform, translate
from shapely.geometry import Polygon, box
from shapely.ops import unary_union
from shapely.strtree import STRtree
from shapely.errors import GEOSException

from .schematic_water_vector import fit_water_chains, restrict_water_directions, same_water_topology, simplify_water_edges
from .schematic_water_rivers import schematize_rivers
from .schematic_water_shores import round_water_shores


class SchematicWaterError(ValueError):
    def __init__(self, reason):
        super().__init__(f"示意水域生成失败：{reason}。线网已保留。")


def polygon_parts(geometry):
    if geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        return [geometry]
    return [part for item in getattr(geometry, "geoms", ()) for part in polygon_parts(item)]


def water_surface(geometry):
    """按水面减岛屿的规则构造多边形。"""
    rings = []
    for ring, wet in zip(geometry.polygons, geometry.is_water):
        polygon = Polygon([(geometry.origin_x+float(x)*geometry.cell/geometry.upscale,
                            geometry.origin_z+float(z)*geometry.cell/geometry.upscale) for x, z in ring])
        if not polygon.is_valid:
            raise SchematicWaterError("源水域多边形无效")
        rings.append((polygon, wet))
    wet = [polygon for polygon, filled in rings if filled]
    tree = STRtree(wet)
    areas = [polygon.area for polygon in wet]
    holes = [[] for _polygon in wet]
    for polygon, filled in rings:
        if filled:
            continue
        # 填充轮廓按嵌套奇偶分类；每个岛属于包围它的最小水面。
        candidates = [int(i) for i in tree.query(polygon.representative_point(), predicate="within")
                      if areas[i] > polygon.area]
        if not candidates:
            raise SchematicWaterError("岛屿轮廓没有对应水面")
        holes[min(candidates, key=lambda i: areas[i])].append(polygon)
    return unary_union([polygon.difference(unary_union(children)) if children else polygon
                        for polygon, children in zip(wet, holes)])


def source_water(session, mapdata):
    if session is not None:
        geometry = session.water_geometry()
        return None if geometry is None else water_surface(geometry)
    if mapdata.terrain.water_array is None:
        return None
    from .layers.water_layer import build_water_geometry, CONTOUR_UPSCALE, MIN_POLY_AREA
    from .cmm import Terrain, Vec2
    terrain = Terrain(origin=Vec2(mapdata.terrain.origin_x, mapdata.terrain.origin_z), cell=mapdata.terrain.cell)
    geometry = build_water_geometry(mapdata.terrain.water_array, terrain,
        upscale=CONTOUR_UPSCALE, min_area=MIN_POLY_AREA)
    return None if geometry is None else water_surface(geometry)


def draw_water_surface(image, geometry, cfg, *, offset=(0, 0)):
    if not bool(cfg.get("enabled", True)) or geometry.is_empty:
        return
    import aggdraw
    from PIL import Image
    from shapely.geometry.polygon import orient
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = aggdraw.Draw(overlay)
    fill = tuple(cfg.get("color_rgb", (116, 190, 222)))
    alpha = max(72, min(int(cfg.get("alpha", 84)), 255))
    for part in polygon_parts(geometry):
        part = orient(part, sign=1)
        path = aggdraw.Path()
        for ring in (part.exterior, *part.interiors):
            points = list(ring.coords)
            path.moveto(points[0][0]-offset[0], points[0][1]-offset[1])
            for x, y in points[1:]:
                path.lineto(x-offset[0], y-offset[1])
            path.close()
        draw.path(path, aggdraw.Brush(fill+(alpha,)))
    draw.flush()
    image.alpha_composite(overlay)


def _pixel_inverse(value, axis, scale, offset):
    if axis is None:
        return (value-offset)/scale
    keys = sorted(axis)
    pixels = [axis[key] for key in keys]
    if value <= pixels[0]:
        return keys[0]+(value-pixels[0])/scale
    if value >= pixels[-1]:
        return keys[-1]+(value-pixels[-1])/scale
    return float(np.interp(value, pixels, keys))


def project_native_water(water, layout, bounds):
    """单调坐标轴在每个网格矩形内仿射映射，不产生翻折。"""
    from .schematic import orient_world
    if water.is_empty:
        return water
    spacing = layout.spacing_m
    ax, ay = orient_world(1, 0, layout.rotation)
    bx, by = orient_world(0, 1, layout.rotation)
    source = affine_transform(water.simplify(0, preserve_topology=True),
                              (ax/spacing, bx/spacing, ay/spacing, by/spacing, 0, 0))
    scale, ox, oy = layout.transform
    x0, y0, x1, y1 = bounds
    clipped = source.intersection(box(
        _pixel_inverse(x0, layout.adaptive_x, scale, ox),
        _pixel_inverse(y0, layout.adaptive_y, scale, oy),
        _pixel_inverse(x1, layout.adaptive_x, scale, ox),
        _pixel_inverse(y1, layout.adaptive_y, scale, oy)))
    if clipped.is_empty:
        return Polygon()
    if layout.adaptive_x is None:
        return affine_transform(clipped, (scale, 0, 0, scale, ox, oy))
    xknots, yknots = sorted(layout.adaptive_x), sorted(layout.adaptive_y)
    if any(axis[b] <= axis[a] for axis, knots in ((layout.adaptive_x, xknots), (layout.adaptive_y, yknots))
           for a, b in zip(knots, knots[1:])):
        raise SchematicWaterError("非均匀坐标轴不单调")

    def pixels(values, axis, knots):
        mapped = np.interp(values, knots, [axis[key] for key in knots])
        low, high = values < knots[0], values > knots[-1]
        mapped[low] = axis[knots[0]]+(values[low]-knots[0])*scale
        mapped[high] = axis[knots[-1]]+(values[high]-knots[-1])*scale
        return mapped

    def ring(points):
        coords = np.asarray(points)
        starts, ends = coords[:-1], coords[1:]
        delta = ends-starts
        extras = {}
        # 大部分轮廓边不越过轴节点；批量筛选后只切分少数跨格边。
        for axis, knots in ((0, xknots), (1, yknots)):
            lower = np.searchsorted(knots, np.minimum(starts[:, axis], ends[:, axis]), side="right")
            upper = np.searchsorted(knots, np.maximum(starts[:, axis], ends[:, axis]), side="left")
            for i in np.flatnonzero(upper > lower):
                for knot in knots[lower[i]:upper[i]]:
                    extras.setdefault(i, set()).add((knot-starts[i, axis])/delta[i, axis])
        indices = list(range(len(starts)))
        fractions = [0.0]*len(starts)
        for i, values in extras.items():
            indices.extend([i]*len(values))
            fractions.extend(values)
        indices, fractions = np.asarray(indices), np.asarray(fractions)
        order = np.lexsort((fractions, indices))
        values = starts[indices[order]]+delta[indices[order]]*fractions[order, None]
        values = np.vstack((values, values[0]))
        return np.column_stack((pixels(values[:, 0], layout.adaptive_x, xknots),
                                pixels(values[:, 1], layout.adaptive_y, yknots)))

    result = unary_union([Polygon(ring(part.exterior.coords), [ring(hole.coords) for hole in part.interiors])
                          for part in polygon_parts(clipped)])
    if not result.is_valid or _topology(result) != _topology(clipped):
        raise SchematicWaterError("坐标轴映射改变了可见水域拓扑")
    return result


def _topology(geometry):
    parts = polygon_parts(geometry)
    return len(parts), sum(len(part.interiors) for part in parts)


def _filter_small_water(geometry, area, counts):
    parts = []
    for part in polygon_parts(geometry):
        if part.area < area:
            counts["removed_components"] += 1
            continue
        holes = []
        for hole in part.interiors:
            if Polygon(hole).area < area:
                counts["filled_holes"] += 1
            else:
                holes.append(hole)
        parts.append(Polygon(part.exterior, holes))
    # 填岛后可能包住原先的岛内湖；合并成同一份水面。
    return unary_union(parts) if parts else Polygon()


def _check_display_surface(geometry):
    if not geometry.is_valid or (not geometry.is_empty and geometry.geom_type not in ("Polygon", "MultiPolygon")):
        raise SchematicWaterError("概括后的水面不是有效多边形")


def _water_grid_step(layout, scale):
    gaps = []
    for axis in (layout.adaptive_x, layout.adaptive_y):
        keys = sorted(axis) if axis is not None else []
        if len(keys) < 2:
            gaps.append(layout.transform[0])
        else:
            gaps.extend(np.diff([axis[key] for key in keys])/np.diff(keys))
    return max(float(np.median(gaps))/2, 6*scale)


def _visible_water(surface, area, clip):
    return _filter_small_water(unary_union(polygon_parts(surface.intersection(clip))), area,
                               {"removed_components": 0, "filled_holes": 0})


def _simplify_display_water(filtered, area, epsilon, clip):
    baseline = _visible_water(filtered, area, clip)
    for attempt in range(7):
        used = epsilon/2**attempt
        simplified = filtered.simplify(used, preserve_topology=True)
        protected, skipped = [], []
        for index, (original, candidate) in enumerate(zip(polygon_parts(filtered), polygon_parts(simplified))):
            below_area = any(Polygon(ring).area < area for ring in candidate.interiors)
            holes = [old if Polygon(new).area < area else new
                     for old, new in zip(original.interiors, candidate.interiors)]
            candidate = Polygon(candidate.exterior, holes)
            if not candidate.is_valid or candidate.area < area:
                candidate = original
                skipped.append({"component": index, "stage": "simplification", "reason": "retained_component_changed"})
            elif below_area:
                skipped.append({"component": index, "stage": "simplification", "reason": "retained_island_below_area"})
            protected.append(candidate)
        simplified = unary_union(protected) if protected else Polygon()
        if same_water_topology(filtered, simplified) and same_water_topology(baseline, _visible_water(simplified, area, clip)):
            if attempt:
                skipped.append({"stage": "simplification", "reason": "visible_topology_reduced_tolerance",
                                "requested_pixels": epsilon, "used_pixels": used})
            return simplified, skipped
    return filtered, [{"stage": "simplification", "reason": "water_topology_changed"}]


def octilinear_water(geometry, bounds, step):
    """矢量岸线的局部八方向限制；step 控制初始细分尺度。"""
    _check_display_surface(geometry)
    if not np.isfinite(step) or step <= 0:
        raise SchematicWaterError("八方向岸线细分尺度必须是有限正数")
    if geometry.is_empty:
        return geometry
    origin_x, origin_y = geometry.bounds[:2]
    local = translate(geometry, xoff=-origin_x, yoff=-origin_y)
    local_bounds = (bounds[0]-origin_x, bounds[1]-origin_y, bounds[2]-origin_x, bounds[3]-origin_y)
    try:
        restricted = restrict_water_directions(local, step, bounds=local_bounds)
    except ValueError as error:
        raise SchematicWaterError(str(error)) from error
    fitted = fit_water_chains(restricted, step/2, bounds=local_bounds, origin=(origin_x, origin_y))
    result = simplify_water_edges(fitted, step/2, bounds=local_bounds, origin=(origin_x, origin_y))
    # 配对移动会释放原先受局部边阻挡的长岸段，再合并一次即可。
    result = fit_water_chains(result, step/2, bounds=local_bounds, origin=(origin_x, origin_y))
    result = translate(result, xoff=origin_x, yoff=origin_y)
    _check_display_surface(result)
    if not same_water_topology(translate(restricted, xoff=origin_x, yoff=origin_y), result, ordered=True):
        raise SchematicWaterError("八方向处理改变了水体或岛洞关系")
    clip = box(*bounds)
    for before, after in zip(polygon_parts(geometry), polygon_parts(result)):
        a = unary_union(polygon_parts(before.intersection(clip)))
        b = unary_union(polygon_parts(after.intersection(clip)))
        if _topology(a) != _topology(b) or abs(a.area-b.area) > max(1e-6, a.area*1e-8):
            raise SchematicWaterError("八方向处理改变了画布内的水域关系或面积")
    return result


def _chaikin_ring(ring, distance):
    points = np.asarray(ring.coords)[:-1, :2]
    # 限制每次切角的距离，避免长边在小强度下也被大幅削去。
    for _ in range(3):
        previous, following = np.roll(points, 1, axis=0), np.roll(points, -1, axis=0)
        left, right = previous-points, following-points
        left_length, right_length = np.linalg.norm(left, axis=1), np.linalg.norm(right, axis=1)
        left_fraction = np.minimum(.25, np.divide(distance/3, left_length,
            out=np.zeros_like(left_length), where=left_length > 0))
        right_fraction = np.minimum(.25, np.divide(distance/3, right_length,
            out=np.zeros_like(right_length), where=right_length > 0))
        points = np.stack((points+left*left_fraction[:, None],
                           points+right*right_fraction[:, None]), axis=1).reshape(-1, 2)
    return points


def _smooth_display_water(surface, distance, area, clip):
    """Chaikin 切角；逐组件降低强度，保留水体、岛洞和画布内关系。"""
    parts = polygon_parts(surface)
    visible = _visible_water(surface, area, clip)
    operations = []
    if distance == 0:
        return surface, operations
    for index, original in enumerate(parts):
        reason = None
        for attempt in range(7):
            used = distance/2**attempt
            candidate = Polygon(_chaikin_ring(original.exterior, used),
                [_chaikin_ring(ring, used) for ring in original.interiors])
            if not candidate.is_valid:
                reason = "invalid_smoothed_geometry"
            elif candidate.area < area or any(Polygon(ring).area < area for ring in candidate.interiors):
                reason = "smoothing_below_minimum_area"
            else:
                combined = unary_union(parts[:index]+[candidate]+parts[index+1:])
                if (same_water_topology(surface, combined) and
                        same_water_topology(surface.intersection(clip), combined.intersection(clip)) and
                        same_water_topology(visible, _visible_water(combined, area, clip))):
                    parts[index] = candidate
                    break
                reason = "smoothing_changed_topology"
        else:
            used = 0.0
        if attempt or used == 0:
            operations.append({"component": index, "stage": "smoothing", "reason": reason,
                "requested_pixels": distance, "used_pixels": used})
    return unary_union(parts) if parts else Polygon(), operations


def generalize_water(water, layout, bounds, *, line_width_pixels=None, water_smoothing=0):
    """面积筛选、八方向岸线和倒圆只依赖画布尺度，不约束线网。"""
    x0, y0, x1, y1 = bounds
    scale = min(x1-x0, y1-y0)/1024
    width = 3*scale if line_width_pixels is None else float(line_width_pixels)
    if not np.isfinite(width) or width <= 0:
        raise SchematicWaterError("示意水域线宽必须是有限正数")
    if isinstance(water_smoothing, bool) or not np.isfinite(water_smoothing) or not 0 <= water_smoothing <= 16:
        raise SchematicWaterError("岸线平滑强度必须是 0 到 16 的有限数值")
    smoothing = float(water_smoothing)*scale
    radius, epsilon, area = width/2, max(1.5*scale, smoothing), 150*scale*scale
    step = _water_grid_step(layout, scale)
    padding = 2*radius+epsilon+smoothing+2*step
    projected = unary_union(polygon_parts(project_native_water(water, layout,
        (x0-padding, y0-padding, x1+padding, y1+padding))))
    _check_display_surface(projected)
    clip = box(*bounds)
    counts = {"removed_components": 0, "filled_holes": 0}
    filtered = _filter_small_water(projected, area, counts)
    simplified, skipped = _simplify_display_water(filtered, area, epsilon, clip)
    smoothed, smoothing_operations = _smooth_display_water(simplified, smoothing, area, clip)
    skipped.extend(smoothing_operations)
    # 切角增加的是曲线采样点；按亚像素误差压缩后再做方向限制。
    sampling_tolerance = min(.25*scale, smoothing/8)
    sampled = smoothed
    if smoothing > 0:
        sampled, sampling_skips = _simplify_display_water(smoothed, area, sampling_tolerance, clip)
        skipped.extend({**item, "stage": "smoothing_sampling"} for item in sampling_skips)
    skeleton = octilinear_water(sampled, bounds, step)
    skeleton = _filter_small_water(skeleton, area, counts)
    visible_skeleton = _visible_water(skeleton, area, clip)
    rounded = polygon_parts(skeleton)
    for index, part in enumerate(rounded):
        reason = None
        for attempt in range(7):
            local_radius = radius/2**attempt
            opened = part.buffer(-local_radius, join_style="round")
            candidate = opened.buffer(2*local_radius, join_style="round").buffer(-local_radius, join_style="round")
            if candidate.is_empty:
                reason = "buffer_erased_component"
            elif not same_water_topology(part, candidate):
                reason = "buffer_changed_topology"
            elif candidate.area < area or any(Polygon(ring).area < area for ring in candidate.interiors):
                reason = "buffer_below_minimum_area"
            else:
                combined = unary_union(rounded[:index]+[candidate]+rounded[index+1:])
                if (same_water_topology(skeleton, combined) and
                        same_water_topology(skeleton.intersection(clip), combined.intersection(clip)) and
                        same_water_topology(visible_skeleton, _visible_water(combined, area, clip))):
                    rounded[index] = candidate
                    break
                reason = "buffer_changed_topology"
        else:
            local_radius = 0.0
        if attempt or local_radius == 0:
            skipped.append({"component": index, "stage": "rounding", "reason": reason,
                            "requested_radius_pixels": radius, "used_radius_pixels": local_radius})
    pre_river = unary_union(rounded)
    protected_areas = []
    try:
        result, river_adjustments = schematize_rivers(pre_river, bounds, scale=scale, width=8*scale,
            minimum_area=area, protected_areas=protected_areas)
    except ValueError as error:
        raise SchematicWaterError(str(error)) from error
    skipped.extend(river_adjustments)
    result, shore_adjustments = round_water_shores(skeleton, result, bounds, radius=16*scale,
        minimum_radius=max(radius, .25*scale), scale=scale, minimum_area=area, protected=protected_areas)
    skipped.extend(shore_adjustments)
    result = unary_union(polygon_parts(result.intersection(clip)))
    result = _filter_small_water(result, area, counts)
    _check_display_surface(result)
    report = {"status": "generated", "verification_scope": "geometry_only",
              **counts, "skipped_operations": skipped}
    return result, report


def layout_and_water(mapdata, lines, stations, enabled, *, session=None, water=None,
                     line_width_pixels=None, water_smoothing=0, **options):
    """先完成无水域布局；水面处理失败只隐藏水面，不重排线网。"""
    from .schematic import schematic_layout_with_fallback
    layout = schematic_layout_with_fallback(mapdata, lines, stations, enabled, **options)
    if not stations or not lines:
        return layout, None, {"status": "not_applicable", "displayed": False}
    try:
        if water is None:
            water = source_water(session, mapdata)
        if water is None or water.is_empty:
            return layout, None, {"status": "unavailable", "displayed": False}
        _check_display_surface(water)
        width, height = options.get("canvas", (1024, 1024))
        projected, report = generalize_water(water, layout, (0, 0, width, height),
            line_width_pixels=line_width_pixels, water_smoothing=water_smoothing)
        return layout, projected, report
    except (SchematicWaterError, GEOSException) as exc:
        report = {"status": "unverified", "displayed": False, "failures": [str(exc)],
                  "verification_scope": "geometry_only"}
    return layout, None, report
