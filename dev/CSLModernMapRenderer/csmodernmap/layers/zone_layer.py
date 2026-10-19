"""功能区图层：渲染游戏"公园区/产业区"（DistrictPark）的边界与分类色块。

对应游戏的"公园区/产业区"工具（森林、城市公园、动物园、游乐场、自然保护区、
校园、产业区、机场、大学等）。与"区划"图层（行政区）相互独立：本图层只画
功能区，区划图层只画行政区，两者可分别勾选、互不叠加。

数据来源：一代 Mod v6.5+ 导出的 <ParkGrid>，或二代 .cmm 直接导出的
SubArea 边界。前者经掩膜平滑，后者按游戏提供的精确多边形直接绘制。

算法（对齐 district_layer 平滑思路）：
1. 把各功能区的采样格点栅格化到统一掩膜（每格画 cell 大小方块，值=序号+1）
2. 逐功能区 GaussianBlur + contourpy 提取 50% 等值线（外环 + 孔洞环）
3. 像素坐标 Chaikin 平滑 + aggdraw 抗锯齿填充/描边
"""
from __future__ import annotations

from ..zone_geometry import grid_rings
from ..path_model import chaikin

from ..style import hex_to_rgb

import aggdraw
import numpy as np

from ..built_area import WORLD_MIN, WORLD_SIZE
from .base import Layer, RenderContext

# Chaikin 平滑迭代次数（越大轮廓越圆润）
SMOOTH_ITERS = 3

# 掩膜 BILINEAR 放大倍数：放大后提取 50% 过渡带等值线，轮廓采样更密
CONTOUR_UPSCALE = 2

# 功能区基础填充色。各主题可通过 zone.fill 覆盖。
DEFAULT_FILL = "#C8C8C8"


class ZoneLayer(Layer):
    """功能区渲染器（公园区/产业区边界 + 基础填充）。

    show_ids: 可选白名单（功能区 id 集合）。None=显示全部；
    传入集合时只渲染集合内的功能区（GUI"区划显示"单独开关用）。
    """

    name = "zone"

    def __init__(self, show_ids=None):
        super().__init__()
        self.show_ids = show_ids

    def render(self, ctx: RenderContext, document) -> None:
        """绘制功能区范围并保留源区域的边界语义。"""
        cfg = ctx.style.get("zone", {})
        if not cfg or not cfg.get("enabled", True):
            return
        # 跳过背景"非功能区"（游戏 AREA_PATTERN[None]，type=None，覆盖全图）：
        # 它不是真实功能区，填充会让整图蒙上默认色、淹没真实功能区色块。
        parks = [d for d in document.park_zones
                 if (d.grid_points or self._boundary_points(d))
                 and (d.type_label or "").strip()
                 and (d.type_label or "").lower() not in ("none", "0")]
        if self.show_ids is not None:
            parks = [p for p in parks if p.id in self.show_ids]
        if not parks:
            return

        grid_parks = [park for park in parks if park.grid_points]
        boundary_parks = [park for park in parks
                          if not park.grid_points and self._boundary_points(park)]

        # 网格单元大小：优先用 Mod 导出的 'cell=' 值；旧存档回退按格点间距
        # 推断（中位数，避免相邻区域格点互相污染最小值）
        smooth_blur = float(cfg.get("smooth_blur", 1.0) or 0.0)
        upscale = max(1, int(cfg.get("contour_upscale", CONTOUR_UPSCALE) or 1))
        smooth_iters = int(cfg.get("smooth_iters", SMOOTH_ITERS) or 0)
        border_color = hex_to_rgb(cfg.get("border", "#B08A5C"))
        border_w = self._border_px(cfg.get("border_width", 1), ctx.zoom)
        fill_alpha = int(cfg.get("fill_alpha", 100))
        fill = hex_to_rgb(cfg.get("fill", DEFAULT_FILL))

        agg = aggdraw.Draw(ctx.image)
        from ..zone_geometry import functional_zone_geometries
        for _park, polygons, _source_ids in functional_zone_geometries(boundary_parks):
            for rings in polygons:
                path = aggdraw.Path()
                for ring_index, ring in enumerate(rings):
                    pixels = [ctx.transform(x, z) for x, z in ring]
                    # 非零环绕规则要求孔洞方向与外边界相反。
                    signed = sum(a[0]*b[1] - b[0]*a[1] for a, b in zip(pixels, pixels[1:]))
                    if (signed > 0) != (ring_index == 0):
                        pixels.reverse()
                    path.moveto(*pixels[0])
                    for point in pixels[1:]:
                        path.lineto(*point)
                    path.close()
                agg.path(path,
                         aggdraw.Pen(border_color, border_w) if border_w > 0 else None,
                         aggdraw.Brush(fill, opacity=fill_alpha) if fill_alpha > 0 else None)

        if not grid_parks:
            agg.flush()
            return

        cell = float(document.park_cell or 0.0)
        if cell <= 0:
            cell = self._infer_cell(grid_parks)
        if cell <= 0:
            agg.flush()
            return

        # 1. 统一掩膜：每格 = 功能区序号 + 1（0 = 无功能区）
        mask, ox, oz = self._build_mask(grid_parks, cell, ctx)
        if mask is None:
            agg.flush()
            return
        arr = np.asarray(mask, dtype=np.int32)

        # 2. 先画全部填充（所有功能区共用基础色）
        if fill_alpha > 0:
            for idx, _p in enumerate(grid_parks):
                for pts in grid_rings(arr, idx + 1, cell, ox, oz,
                                           upscale, smooth_blur):
                    if len(pts) < 3:
                        continue
                    pix = [ctx.transform(float(p[0]), float(p[1])) for p in pts]
                    pix = chaikin(pix, smooth_iters)
                    if len(pix) >= 3:
                        coords = tuple(v for xy in pix for v in xy)
                        agg.polygon(coords, None,
                                    aggdraw.Brush(fill, opacity=fill_alpha))

        # 3. 再画边界线（盖在填充之上，相邻区域接缝清晰）
        if border_w > 0:
            for idx, p in enumerate(grid_parks):
                for pts in grid_rings(arr, idx + 1, cell, ox, oz,
                                           upscale, smooth_blur):
                    if len(pts) < 3:
                        continue
                    pix = [ctx.transform(float(p[0]), float(p[1])) for p in pts]
                    pix = chaikin(pix, smooth_iters)
                    if len(pix) >= 3:
                        coords = tuple(v for xy in pix for v in xy)
                        agg.line(coords, aggdraw.Pen(border_color, border_w))

        agg.flush()

    @staticmethod
    def _boundary_points(park):
        boundary = getattr(park, "boundary", None)
        points = getattr(boundary, "points", ()) if boundary is not None else ()
        return tuple(points or ()) if len(points or ()) >= 3 else ()

    # ---- 掩膜构建 ----

    def _infer_cell(self, parks) -> float:
        """从格线分布推断网格单元大小（米）。

        取全部唯一 x 格线相邻间距的均值（跨度/数量）：取整坐标（F0）下
        最近邻中位数会偏小（19.2 取整为 19/20 交替，中位数 19.0 ≠ 19.2），
        均值能精确还原真实单元大小。
        """
        all_pts = []
        for p in parks:
            all_pts.extend(p.grid_points_xz)
        if len(all_pts) < 2:
            return 0.0
        pts = np.asarray(all_pts, dtype=np.float64)
        ux = np.unique(np.round(pts[:, 0], 1))
        if len(ux) < 2:
            return 0.0
        return float((ux[-1] - ux[0]) / (len(ux) - 1))

    def _build_mask(self, parks, cell: float, ctx):
        """构建功能区掩膜（int32 图像，像素值 = 功能区序号 + 1）。

        只覆盖可见世界范围，返回 (mask_img, origin_x, origin_z) 或 None。

        格点定位用**绝对格线索引**：采样格线位于 WORLD_MIN + (i+0.5)*cell
        （见 Mod ExportZoneGrid），因此 idx = round((x-WORLD_MIN)/cell - 0.5)。
        - 直接用 round((x-WORLD_MIN)/cell)：取整坐标（F0 格式）下相邻格线每
          5 格出现像素重叠/空隙，掩膜碎成格点（"行政区图格点状"）；
        - 改用"数据内唯一格线 → 索引"：局部数据会让掩膜整体偏移（功能区/
          机场漂移），因为重建世界坐标时假设索引 0 = 世界原点。
        绝对索引同时避免这两类问题，对 F0/F1 存档都成立。
        """
        grid_n = int(round(WORLD_SIZE / cell))
        wx0 = max(ctx.world_x0, WORLD_MIN)
        wz0 = max(ctx.world_z0, WORLD_MIN)
        wx1 = min(ctx.world_x1, WORLD_MIN + WORLD_SIZE)
        wz1 = min(ctx.world_z1, WORLD_MIN + WORLD_SIZE)
        if wx1 <= wx0 or wz1 <= wz0:
            return None
        gx0 = max(0, min(grid_n - 1, int(np.floor((wx0 - WORLD_MIN) / cell - 0.5))))
        gz0 = max(0, min(grid_n - 1, int(np.floor((wz0 - WORLD_MIN) / cell - 0.5))))
        gx1 = max(0, min(grid_n - 1, int(np.ceil((wx1 - WORLD_MIN) / cell - 0.5))))
        gz1 = max(0, min(grid_n - 1, int(np.ceil((wz1 - WORLD_MIN) / cell - 0.5))))
        if gx1 <= gx0 or gz1 <= gz0:
            return None
        gw, gh = gx1 - gx0 + 1, gz1 - gz0 + 1
        mask = np.zeros((gh, gw), dtype=np.int32)
        # 逐功能区写入：绝对格线索引，一次向量化写入
        for idx, p in enumerate(parks):
            if not p.grid_points:
                continue
            pts = np.asarray(p.grid_points_xz, dtype=np.float64)
            gx = np.round((pts[:, 0] - WORLD_MIN) / cell - 0.5).astype(int)
            gz = np.round((pts[:, 1] - WORLD_MIN) / cell - 0.5).astype(int)
            vis = (gx >= gx0) & (gx <= gx1) & (gz >= gz0) & (gz <= gz1)
            if not vis.any():
                continue
            mask[gz[vis] - gz0, gx[vis] - gx0] = idx + 1
        return mask, WORLD_MIN + gx0 * cell, WORLD_MIN + gz0 * cell


    # ---- 工具 ----

    def _border_px(self, val, zoom: float) -> float:
        """边界线宽（像素）：数字=固定像素；dict {fixed, scaled}=fixed+scaled*缩放。"""
        if isinstance(val, dict):
            return val.get("fixed", 0.0) + val.get("scaled", 0.0) * zoom
        return float(val)
