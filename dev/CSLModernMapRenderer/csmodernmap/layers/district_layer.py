"""区划边界图层：由区划网格重建边界，绘制边界线 + 半透明填充。

对应 SPEC F006（区划渲染）。数据来源为 Mod v6.5+ 导出的 <DistrictGrid>
（每个区划的采样格点，世界坐标，见 parser._parse_district_grid）。
旧存档无网格数据时本图层自动跳过（区划名称仍由 LabelLayer 负责标注）。

独立功能：默认关闭（与"用地类型"图层互不叠加），由 GUI/CLI 显式启用。

算法（对齐 built_area 平滑思路）：
1. 把各区划的采样格点栅格化到统一掩膜（每格画 cell 大小方块，值=区划序号）
2. 逐区划 GaussianBlur + contourpy 提取 50% 等值线（外环 + 孔洞环）
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

# 区划填充默认调色板（每区划按序取色，循环使用）
DEFAULT_FILL_COLORS = [
    "#E8F0E0", "#F0E8E0", "#E0E8F0", "#F0E8F0",
    "#F0F0E0", "#E0F0F0", "#F0E0E0", "#E8E0F0",
]

def is_base_district(district, cell: float) -> bool:
    """判定是否为游戏自带的全图基础区划（如"贝尔蒙特 高地"）。

    CS1 每个存档都有一块划分区划前覆盖整图的基础区划，它没有真实边界，
    渲染会把整图铺成一块底色遮罩（用户反馈的"整图被图层遮罩覆盖"）。
    游戏保留区划的稳定 id 为 0；玩家划出区划后，它会变成剩余区域，
    此时面积可能低于 90%，所以 id 判定必须优先。面积规则兼容旧导出格式。
    cell<=0 时无法判定返回 False（无网格数据存档本就不参与区划渲染）。
    """
    if int(getattr(district, "id", -1)) == 0:
        return True
    if cell <= 0 or not district.grid_points:
        return False
    n_world = round(WORLD_SIZE / cell) ** 2
    return len(district.grid_points) >= 0.9 * n_world


def visible_administrative_districts(source) -> list:
    """返回玩家创建的行政区，不包含游戏默认区。"""
    districts = getattr(source, "administrative_districts", None)
    if districts is None:
        districts = getattr(source, "districts", ()) or ()
    cell = float(getattr(source, "district_cell", 0.0) or 0.0)
    return [district for district in districts
            if not is_base_district(district, cell)]


def _poly_area(poly) -> float:
    """多边形面积（世界坐标 m²）。"""
    s = 0.0
    n = len(poly)
    for i in range(n):
        x1, z1 = poly[i][0], poly[i][1]
        x2, z2 = poly[(i + 1) % n][0], poly[(i + 1) % n][1]
        s += x1 * z2 - x2 * z1
    return abs(s) / 2.0


def _poly_centroid(poly):
    return (float(np.mean(poly[:, 0])), float(np.mean(poly[:, 1])))


def _point_in_poly(px: float, pz: float, poly) -> bool:
    """射线法判断点 (px,pz) 是否在多边形内。poly = (N,2) 的 (x,z)。"""
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, zi = poly[i][0], poly[i][1]
        xj, zj = poly[j][0], poly[j][1]
        if (zi > pz) != (zj > pz) and px < (xj - xi) * (pz - zi) / (
            (zj - zi) or 1e-12
        ) + xi:
            inside = not inside
        j = i
    return inside


class DistrictLayer(Layer):
    """区划边界渲染器（边界线 + 半透明填充）。

    show_ids: 可选白名单（区划 id 集合）。None=显示全部；
    传入集合时只渲染集合内的区划（GUI"区划显示"单独开关用）。
    """

    name = "district"

    def __init__(self, show_ids=None):
        super().__init__()
        self.show_ids = show_ids

    def render(self, ctx: RenderContext, document) -> None:
        """绘制行政区填充与边界，并处理当前视图范围。"""
        cfg = ctx.style.get("district", {})
        if not cfg or not cfg.get("enabled", True):
            return
        districts = [d for d in visible_administrative_districts(document)
                     if d.grid_points]
        if self.show_ids is not None:
            districts = [d for d in districts if d.id in self.show_ids]
        if not districts:
            return  # 无区划网格数据（旧存档）或白名单过滤为空 → 本图层无输出

        # 网格单元大小：优先用 Mod 导出的 'cell=' 值；旧存档回退按格点
        # 间距推断（中位数，避免相邻区划格点互相污染最小值）
        cell = float(document.district_cell or 0.0)
        if cell <= 0:
            cell = self._infer_cell(districts)
        if cell <= 0:
            return
        smooth_blur = float(cfg.get("smooth_blur", 1.0) or 0.0)
        upscale = max(1, int(cfg.get("contour_upscale", CONTOUR_UPSCALE) or 1))
        smooth_iters = int(cfg.get("smooth_iters", SMOOTH_ITERS) or 0)
        border_color = hex_to_rgb(cfg.get("border", "#B0B0B0"))
        border_w = self._border_px(cfg.get("border_width", 1), ctx.zoom)
        fill_alpha = int(cfg.get("fill_alpha", 64))
        fill_colors = cfg.get("fill_colors") or DEFAULT_FILL_COLORS
        # 1. 统一掩膜：每格 = 区划序号 + 1（0 = 无区划）
        mask_result = self._build_mask(districts, cell, ctx)
        if mask_result is None:
            return
        mask, ox, oz = mask_result
        arr = np.asarray(mask, dtype=np.int32)

        agg = aggdraw.Draw(ctx.image)

        # 2. 先画全部填充（各区划互不重叠，顺序无关）
        # 注意：aggdraw 的 Brush 不支持 rgba 4 元组，半透明须用
        # Brush(颜色3元组, opacity=透明度)（Polygon 需扁平坐标）。
        if fill_alpha > 0:
            for idx, d in enumerate(districts):
                fill = hex_to_rgb(fill_colors[idx % len(fill_colors)])
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

        # 3. 再画边界线（盖在填充之上，相邻区划接缝清晰）
        if border_w > 0:
            for idx, d in enumerate(districts):
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

    # ---- 掩膜构建 ----

    def _infer_cell(self, districts) -> float:
        """从格线分布推断网格单元大小（米）。

        取全部唯一 x 格线相邻间距的均值（跨度/数量）：取整坐标（F0）下
        最近邻中位数会偏小（19.2 取整为 19/20 交替，中位数 19.0 ≠ 19.2），
        均值能精确还原真实单元大小。
        """
        all_pts = []
        for d in districts:
            all_pts.extend(d.grid_points_xz)
        if len(all_pts) < 2:
            return 0.0
        pts = np.asarray(all_pts, dtype=np.float64)
        ux = np.unique(np.round(pts[:, 0], 1))
        if len(ux) < 2:
            return 0.0
        return float((ux[-1] - ux[0]) / (len(ux) - 1))

    def _build_mask(self, districts, cell: float, ctx):
        """构建区划掩膜（int32 图像，像素值 = 区划序号 + 1）。

        只覆盖可见世界范围（对齐 built_area 的裁剪思路），
        返回 (mask_img, origin_x, origin_z) 或 None。

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
        # 逐区划写入：绝对格线索引，一次向量化写入
        for idx, d in enumerate(districts):
            if not d.grid_points:
                continue
            pts = np.asarray(d.grid_points_xz, dtype=np.float64)
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
