"""水域图层：矢量等值线填充水面，边界平滑抗锯齿（aggdraw）。

对应 SPEC F003（水域渲染）。

原实现把水面作为网格像素判色（方块状）。本图层改为：
1. 水域深度网格放大后，用 contourpy 提取 0.2 深度等值线（闭合轮廓）
2. 按轮廓包含关系分层（偶数层=水域，奇数层=岛）
3. Chaikin 角点切割平滑轮廓
4. aggdraw（AGG 矢量）填充：水域用水色，岛用质心地形色
→ 水陆边界为平滑曲线，无方块锯齿（对标 CSLMapView 的矢量等高线方案）
"""
from __future__ import annotations

from ..style import hex_to_rgb

import math
import threading
from collections import OrderedDict
from dataclasses import dataclass

import numpy as np
import aggdraw
import contourpy
from PIL import Image
from .base import Layer, RenderContext
from ..i18n import tr

# CSL 标准 9 格地图尺寸（单位：游戏内米）
WORLD_SIZE = 17280.0
WORLD_MIN = -8640.0

# 水域判定阈值（CSLMapView: WaterArray > 0.2）
WATER_THRESHOLD = 0.2
# 等值线提取前的网格放大倍数（提高轮廓精度 → 边界更平滑）
CONTOUR_UPSCALE = 4
# Chaikin 平滑迭代次数（越大岸线越圆润）
SMOOTH_ITERS = 3
# RDP 轮廓简化容差（放大后网格单位，忽略岸边小锯齿）
RDP_EPSILON = 0.8
# 忽略面积小于该值的多边形（放大后网格²，过滤小水洼/小岛）
MIN_POLY_AREA = 12.0
_WATER_GEOMETRY_CACHE_MAX = 4
_WATER_GEOMETRY_CACHE = OrderedDict()
_WATER_GEOMETRY_CACHE_LOCK = threading.RLock()


@dataclass(frozen=True)
class _WaterGeometry:
    polygons: tuple
    is_water: tuple
    bounds: np.ndarray
    upscale: int
    origin_x: float
    origin_z: float
    cell: float
    width: int
    height: int


def _point_in_poly(pt, poly) -> bool:
    """射线法判断点是否在多边形内。"""
    points = np.asarray(poly, dtype=np.float64)
    if len(points) < 3:
        return False
    x, y = float(pt[0]), float(pt[1])
    xi, yi = points[:, 0], points[:, 1]
    xj, yj = np.roll(xi, 1), np.roll(yi, 1)
    dy = np.where(yj == yi, 1e-12, yj - yi)
    crosses = ((yi > y) != (yj > y)) & (
        x < (xj - xi) * (y - yi) / dy + xi
    )
    return bool(np.count_nonzero(crosses) & 1)


def _poly_area(poly) -> float:
    points = np.asarray(poly, dtype=np.float64)
    if len(points) < 3:
        return 0.0
    x, y = points[:, 0], points[:, 1]
    return float(abs(np.dot(x, np.roll(y, -1))
                     - np.dot(y, np.roll(x, -1))) * 0.5)


def _poly_centroid(poly):
    points = np.asarray(poly, dtype=np.float64)
    center = points.mean(axis=0)
    return float(center[0]), float(center[1])


def _rdp(points, epsilon):
    """Douglas-Peucker 折线简化（忽略小于容差的细节）。"""
    pts = np.asarray(points, dtype=np.float64)
    if len(pts) < 3:
        return pts
    keep = np.zeros(len(pts), dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    epsilon = float(epsilon)
    while stack:
        start, end = stack.pop()
        if end - start <= 1:
            continue
        segment = pts[start + 1:end]
        a, b = pts[start], pts[end]
        dx, dy = b[0] - a[0], b[1] - a[1]
        if abs(dx) < 1e-12 and abs(dy) < 1e-12:
            distances = np.hypot(
                segment[:, 0] - a[0], segment[:, 1] - a[1])
        else:
            distances = np.abs(
                dy * segment[:, 0] - dx * segment[:, 1]
                + b[0] * a[1] - b[1] * a[0]
            ) / math.hypot(dx, dy)
        relative = int(np.argmax(distances))
        if float(distances[relative]) > epsilon:
            index = start + 1 + relative
            keep[index] = True
            stack.append((start, index))
            stack.append((index, end))
    return pts[keep]


def _chaikin(points, iterations=SMOOTH_ITERS):
    """Chaikin 角点切割平滑（闭合多边形）。"""
    pts = np.asarray(points, dtype=np.float64)
    if len(pts) < 2:
        return pts
    for _ in range(iterations):
        following = np.roll(pts, -1, axis=0)
        new = np.empty((len(pts) * 2, 2), dtype=np.float64)
        new[0::2] = 0.75 * pts + 0.25 * following
        new[1::2] = 0.25 * pts + 0.75 * following
        pts = new
    return pts


def _water_geometry_key(document, water, *, upscale, rdp_epsilon, min_area):
    """内容级缓存键：同网格 + 同参数必然得到相同几何，不再依赖 id()。"""
    import hashlib

    terrain = document.terrain
    # 标准地图的形状、原点和单元尺寸只描述坐标系；缓存身份还须包含真实水深网格。
    content = hashlib.blake2b(
        np.ascontiguousarray(water, dtype=np.float32).view(np.uint8),
        digest_size=20,
    ).digest()
    return (
        content,
        tuple(int(v) for v in water.shape),
        float(terrain.origin.x), float(terrain.origin.z), float(terrain.cell),
        int(upscale), round(float(rdp_epsilon), 6), round(float(min_area), 6),
    )


def _classify_polygons(polys):
    """按嵌套深度返回排序后的多边形及水面/岛屿奇偶性。"""
    entries = [(_poly_area(poly), poly) for poly in polys]
    entries.sort(key=lambda item: item[0], reverse=True)
    ordered = [poly for _area, poly in entries]
    if len(ordered) <= 1:
        return ordered, [True] * len(ordered)

    bounds = np.asarray([
        (float(np.min(poly[:, 0])), float(np.min(poly[:, 1])),
         float(np.max(poly[:, 0])), float(np.max(poly[:, 1])))
        for poly in ordered
    ], dtype=np.float64)
    min_x, min_y = np.min(bounds[:, :2], axis=0)
    max_x, max_y = np.max(bounds[:, 2:], axis=0)
    grid_n = max(8, min(64, int(math.sqrt(len(ordered))) + 1))
    cell_w = max((max_x - min_x) / grid_n, 1e-12)
    cell_h = max((max_y - min_y) / grid_n, 1e-12)
    bins = [[] for _ in range(grid_n * grid_n)]
    large = []

    def cell_index(x: float, y: float) -> tuple[int, int]:
        ix = min(grid_n - 1, max(0, int((x - min_x) / cell_w)))
        iy = min(grid_n - 1, max(0, int((y - min_y) / cell_h)))
        return ix, iy

    def covered_cells(box) -> list[tuple[int, int]]:
        ix0, iy0 = cell_index(box[0], box[1])
        ix1, iy1 = cell_index(box[2], box[3])
        return [
            (ix, iy)
            for iy in range(iy0, iy1 + 1)
            for ix in range(ix0, ix1 + 1)
        ]

    is_water = []
    for index, (poly, box) in enumerate(zip(ordered, bounds)):
        center = _poly_centroid(poly)
        ix, iy = cell_index(center[0], center[1])
        candidates = list(large)
        candidates.extend(bins[iy * grid_n + ix])
        depth = 0
        for candidate in candidates:
            candidate_box = bounds[candidate]
            if not (
                candidate_box[0] <= center[0] <= candidate_box[2]
                and candidate_box[1] <= center[1] <= candidate_box[3]
            ):
                continue
            if _point_in_poly(center, ordered[candidate]):
                depth += 1
        is_water.append(depth % 2 == 0)

        cells = covered_cells(box)
        if len(cells) > (grid_n * grid_n) // 4:
            large.append(index)
        else:
            for cell_x, cell_y in cells:
                bins[cell_y * grid_n + cell_x].append(index)
    return ordered, is_water


def _extract_filled_contours(up):
    """基于完整水域网格串行提取轮廓，禁止内部切块闭合。"""
    # contourpy 的 threaded/ChunkCombined 路径会在内部块边界拆开连续
    # 水面并生成额外闭合边。水面属于连续底图，必须保留单次、全网格的
    # filled() 结果；性能换取几何正确性，避免高分辨率出现固定列接缝。
    return contourpy.contour_generator(z=up).filled(
        WATER_THRESHOLD, 1e9), 1


def build_water_geometry(water, terrain, *, upscale, min_area):
    """提取水域轮廓几何。``terrain`` 为 CMM ``Terrain``（origin/cell）。"""
    gh, gw = water.shape
    if gh < 2 or gw < 2:
        return None
    if upscale == 1:
        up = np.asarray(water, dtype=np.float64)
    else:
        up = np.asarray(
            Image.fromarray(water).resize(
                (gw * upscale, gh * upscale), Image.BILINEAR
            ),
            dtype=np.float64,
        )
    res, _contour_threads = _extract_filled_contours(up)
    del up
    lines = res[0] if isinstance(res[0], (list, tuple)) else (res[0],)
    pointss = res[1] if isinstance(res[1], (list, tuple)) else (res[1],)
    polys = []
    for ln, offs in zip(lines, pointss):
        offs = np.asarray(offs)
        for i in range(len(offs) - 1):
            poly = np.asarray(ln[offs[i]:offs[i + 1]], dtype=np.float64)
            if len(poly) >= 3 and _poly_area(poly) >= min_area:
                polys.append(poly)
    if not polys:
        return None
    polys, is_water = _classify_polygons(polys)
    bounds = np.asarray([
        (float(np.min(poly[:, 0])), float(np.min(poly[:, 1])),
         float(np.max(poly[:, 0])), float(np.max(poly[:, 1])))
        for poly in polys
    ], dtype=np.float64)
    return _WaterGeometry(
        polygons=tuple(polys),
        is_water=tuple(is_water),
        bounds=bounds,
        upscale=int(upscale),
        origin_x=float(terrain.origin.x),
        origin_z=float(terrain.origin.z),
        cell=float(terrain.cell),
        width=int(gw),
        height=int(gh),
    )


def water_geometry_for_document(document, *, upscale=CONTOUR_UPSCALE,
                                min_area=MIN_POLY_AREA, **_ignored):
    """会话级入口：按参数从 CMM 文档构建（或返回缓存的）水域几何。"""
    water = document.terrain.water_depth.values
    if water is None:
        return None
    water = np.asarray(water, dtype=np.float32)
    return build_water_geometry(
        water, document.terrain,
        upscale=max(1, int(upscale)), min_area=float(min_area))


def _get_water_geometry(document, water, *, upscale,
                        rdp_epsilon, min_area):
    key = _water_geometry_key(
        document, water, upscale=upscale,
        rdp_epsilon=rdp_epsilon, min_area=min_area,
    )
    with _WATER_GEOMETRY_CACHE_LOCK:
        geometry = _WATER_GEOMETRY_CACHE.get(key)
        if geometry is not None:
            _WATER_GEOMETRY_CACHE.move_to_end(key)
            return geometry
    geometry = build_water_geometry(
        water, document.terrain, upscale=upscale, min_area=min_area)
    if geometry is not None:
        with _WATER_GEOMETRY_CACHE_LOCK:
            _WATER_GEOMETRY_CACHE[key] = geometry
            _WATER_GEOMETRY_CACHE.move_to_end(key)
            while len(_WATER_GEOMETRY_CACHE) > _WATER_GEOMETRY_CACHE_MAX:
                _WATER_GEOMETRY_CACHE.popitem(last=False)
    return geometry


class WaterLayer(Layer):
    """水域渲染器（矢量平滑水面）。

    平滑参数可通过构造传入（GUI 可调）；缺省使用模块常量。
    """

    name = "water"
    full_canvas = True  # 整图连续场：分块时作为底图整图渲染，等值线闭合无缝

    def __init__(
        self,
        upscale: int | None = None,
        smooth_iters: int | None = None,
        rdp_epsilon: float | None = None,
        min_area: float | None = None,
    ):
        self._upscale = upscale if upscale is not None else CONTOUR_UPSCALE
        self._smooth_iters = (
            smooth_iters if smooth_iters is not None else SMOOTH_ITERS
        )
        self._rdp_epsilon = (
            rdp_epsilon if rdp_epsilon is not None else RDP_EPSILON
        )
        self._min_area = min_area if min_area is not None else MIN_POLY_AREA

    @staticmethod
    def _raster_mask(ctx, water, origin_x, origin_z, cell):
        """在与道路及地形一致的世界到像素坐标系中采样水域，并反转变换供 PIL 使用。"""
        anchor = np.asarray(ctx.transform(origin_x, origin_z), dtype=np.float64)
        x_axis = np.asarray(ctx.transform(origin_x + cell, origin_z)) - anchor
        z_axis = np.asarray(ctx.transform(origin_x, origin_z + cell)) - anchor
        inverse = np.linalg.inv(np.column_stack((x_axis, z_axis)))
        offset = -inverse @ anchor
        source = Image.fromarray(
            np.where(water > WATER_THRESHOLD, 255, 0).astype(np.uint8),
            mode="L",
        )
        return source.transform(
            ctx.image.size, Image.Transform.AFFINE,
            (inverse[0, 0], inverse[0, 1], offset[0],
             inverse[1, 0], inverse[1, 1], offset[1]),
            resample=Image.Resampling.BILINEAR, fillcolor=0,
        )

    def render(self, ctx: RenderContext, document) -> None:
        """按输出变换绘制水体轮廓和主题填充。"""
        wm = document.terrain.water_depth.values
        if wm is None:
            return
        water = np.asarray(wm, dtype=np.float32)
        gh_all, gw_all = water.shape

        bounds = getattr(ctx.transform, "region_tile", None)
        if bounds is None:
            bounds = getattr(ctx.transform, "region", (
                ctx.world_x0, ctx.world_z0, ctx.world_x1, ctx.world_z1))
        x0, z0, x1, z1 = bounds
        _ter = document.terrain
        _ox, _oz, _cell = _ter.origin.x, _ter.origin.z, _ter.cell
        if _cell <= 0 or water.size == 0:
            return
        gh, gw = water.shape
        full_world = (
            abs(x0 - _ox) <= _cell * 1.5
            and abs(z0 - _oz) <= _cell * 1.5
            and abs(x1 - (_ox + gw_all * _cell)) <= _cell * 1.5
            and abs(z1 - (_oz + gh_all * _cell)) <= _cell * 1.5
        )
        # 整张低分辨率底图直接使用栅格遮罩。它只覆盖水面像素，
        # 不会抹掉岛屿下方已经绘制的地形，因此不会改变 6.5.3 的遮罩语义。
        # 仅在连续整图路径使用，避免把快路径带入分块接缝区域。
        if (full_world and int(getattr(ctx, "rotation", 0) or 0) % 360 == 0
                and ctx.image.width <= 4096):
            mask = self._raster_mask(ctx, water, _ox, _oz, _cell)
            fill = water_color = hex_to_rgb(
                ctx.style.get("water.color", "#80D4FE"))
            if ctx.image.mode == "RGBA":
                fill = (*water_color, 255)
            ctx.image.paste(fill, (0, 0), mask)
            ctx.report(1.0, tr("gui.progress.water.mask"))
            return

        # 局部图仍使用完整质量地图的平滑轮廓；放大二值栅格会绕过平滑并模糊岸线。
        requested_upscale = max(1, int(self._upscale))
        pixels_per_cell = abs(float(getattr(ctx.transform, "scale", 0.0))) * _cell
        if pixels_per_cell <= 0.0:
            pixels_per_cell = min(
                ctx.image.width / max(gw, 1),
                ctx.image.height / max(gh, 1),
            )
        effective_upscale = min(
            requested_upscale,
            max(1, int(math.ceil(pixels_per_cell / 1.5))),
        )
        scale_ratio = effective_upscale / requested_upscale
        effective_rdp = float(self._rdp_epsilon) * scale_ratio
        effective_min_area = float(self._min_area) * scale_ratio ** 2
        geometry = _get_water_geometry(
            document, water, upscale=effective_upscale,
            rdp_epsilon=effective_rdp,
            min_area=effective_min_area,
        )
        if geometry is None:
            return
        margin = 2.0 * geometry.upscale
        gx0 = (x0 - geometry.origin_x) / geometry.cell * geometry.upscale - margin
        gy0 = (z0 - geometry.origin_z) / geometry.cell * geometry.upscale - margin
        gx1 = (x1 - geometry.origin_x) / geometry.cell * geometry.upscale + margin
        gy1 = (z1 - geometry.origin_z) / geometry.cell * geometry.upscale + margin
        selected = [
            (poly, is_w)
            for poly, is_w, bounds in zip(
                geometry.polygons, geometry.is_water, geometry.bounds)
            if bounds[2] >= gx0 and bounds[0] <= gx1
            and bounds[3] >= gy0 and bounds[1] <= gy1
        ]
        if not selected:
            return
        polys = [poly for poly, _is_w in selected]
        is_water = [is_w for _poly, is_w in selected]
        off_u_x = off_u_y = 0.0
        up_scale = geometry.upscale
        ctx.report(0.25, tr("gui.progress.water.contours"))

        # 4. 颜色准备
        water_color = hex_to_rgb(ctx.style.get("water.color", "#80D4FE"))

        def grid_to_world(gx: float, gy: float):
            # 局部放大网格坐标 → 全局世界坐标（裁剪后需加全局偏移）
            x = _ox + (off_u_x + gx + 0.5) / up_scale * _cell
            z = _oz + (off_u_y + gy + 0.5) / up_scale * _cell
            return x, z

        # 5. 只生成水面遮罩，再覆盖到已有画布。
        #    地形层在本层之前绘制；若直接把内陆/岛屿多边形填成质心颜色，
        #    会抹掉山地的逐像素高度分层。水面遮罩按包含层级绘制：水域白色、
        #    岛屿黑色，从而保留岛屿下方已有的地形/建成区图像。
        water_mask = Image.new("L", ctx.image.size, 0)
        agg = aggdraw.Draw(water_mask)
        _n_poly = max(len(polys), 1)
        for _pi, (poly, is_w) in enumerate(zip(polys, is_water)):
            if _pi % 8 == 0 or _pi == _n_poly - 1:
                ctx.report(0.3 + 0.65 * (_pi + 1) / _n_poly,
                           tr("gui.progress.water.fill", i=_pi + 1, n=_n_poly))
            # RDP 简化（网格坐标，忽略岸边小锯齿）→ 像素 → Chaikin 圆润
            poly = _rdp(poly, effective_rdp)
            pts = []
            for gx, gy in poly:
                wx, wz = grid_to_world(gx, gy)
                px, py = ctx.transform(wx, wz)
                pts.append((px, py))
            pts = _chaikin(pts, iterations=self._smooth_iters)
            coords = tuple(v for xy in pts for v in xy)
            agg.polygon(coords, None, aggdraw.Brush(255 if is_w else 0))
        agg.flush()
        ctx.image.paste(water_color, mask=water_mask)
        ctx.report(1.0, tr("gui.progress.water.done"))
