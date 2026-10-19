"""Procedural Objects（魔物 MOD）图层：按 OBB 渲染玩家自定义对象。

对应 SPEC F010（Procedural Objects 渲染）。

核心规则：
1. 每个 PO 对象由 AABB (center_x,center_z)+(extent_x,extent_z) 表示，
   四角 = (cx±ex, cz±ez)。若 rotation_y ≠ 0，则 AABB 绕中心旋转 rotation_y
   弧度 → 变为 OBB（有向包围盒）。
2. 颜色优先级：
   a. 若玩家已染色（color_rgb 非 None），优先使用；
   b. 否则按 prefab_name 的前缀/关键字走 PO 专属分类色板（来自 YAML）。
3. 使用 aggdraw（AGG 矢量渲染）保证抗锯齿、边缘清晰；与建筑层保持一致观感。

OBB 旋转公式（绕原点旋转 θ，z 轴向上 → 屏幕 Y 朝 -z，因此需要负号修正，
详见 Point.transform_x/transform_z 的坐标系约定）：
    X' =  x*cos θ + z*sin θ + cx
    Z' = -x*sin θ + z*cos θ + cz
   等价于：先把 (±ex, ±ez) 作为 AABB 的局部坐标，再旋转到世界坐标，再加上 (cx, cz)。
"""
from __future__ import annotations

from ..style import hex_to_rgb

import math
from typing import List, Tuple

import aggdraw

from ..spatial import intersects, po_bbox
from .base import Layer, RenderContext


# ---- PO 分类：按 basePrefabName（pfn）关键词匹配 ----
#
# 常见来源模式：
#   1094334744.Cube_Data        → 自定义方块（几何体）
#   1094334744.Sphere_Data
#   ...Prop...                  → 小装饰
#   ...Building...              → 建筑改造
#   ...Tree/...                 → 植物
#   以 "BuildingInfo" 结尾      → 建筑资产
#   以 "PropInfo" 结尾          → 摆件/灯/招牌 等
#
# 分类顺序由长到短命中，避免前缀误匹配。
# 缺省颜色（与 amap.yaml procedural_objects 配置同步，若 YAML 缺省则走此兜底）
DEFAULT_COLORS = {
    "geometry":     "#C9D1DD",   # 几何形状（立方体/球体等）
    "building":     "#E4E6EC",   # 建筑改造
    "prop":         "#D5D9E1",   # 摆件/装饰
    "tree":         "#D2EBC8",   # 植物类
    "street_furn":  "#CFD3DB",   # 街道设施
    "sign":         "#F5D58B",   # 招牌/文字
    "unknown":      "#D0D4DD",   # 兜底
}

CATEGORY_KEYS = (
    "geometry",
    "building",
    "prop",
    "tree",
    "street_furn",
    "sign",
    "unknown",
)


def _po_category(prefab_name: str, source_type: str) -> str:
    """PO 分类（pfn → 类别 key），供渲染色板使用。"""
    p = prefab_name.lower()
    if not p:
        # 无 prefab 名 → 按 source_type 兜底
        if source_type and "BUILDING" in source_type:
            return "building"
        return "unknown"
    # 几何体（PO 自带 Cube/Sphere/Cylinder 等）
    if any(tok in p for tok in ("cube", "sphere", "cylinder", "plane", "torus", "cone", "capsule")):
        return "geometry"
    # 招牌/文字
    if any(tok in p for tok in ("sign", "billboard", "text", "letter", "neon")):
        return "sign"
    # 街道设施
    if any(tok in p for tok in ("street_lamp", "bench", "trash", "bin", "bollard", "pylon")):
        return "street_furn"
    # 树/植物
    if any(tok in p for tok in ("tree", "bush", "plant", "pine", "oak", "palm", "hedge")):
        return "tree"
    # 建筑（含 BuildingInfo 结尾或显式 Building 标记）
    if "buildinginfo" in p or "building" in p or (source_type and "BUILDING" in source_type):
        return "building"
    # Prop（含 PropInfo 结尾或 prop 关键字）
    if "propinfo" in p or "prop" in p:
        return "prop"
    # 兜底
    return "unknown"


class POLayer(Layer):
    """Procedural Objects 渲染层（魔物 MOD 专属）。"""

    name = "procedural_objects"

    def render(self, ctx: RenderContext, document) -> None:
        if not document.procedural_objects:
            return
        self.ctx = ctx
        self.render_scale = ctx.zoom
        self._lvl = ctx.detail_policy.abstract_level
        # 强抽象：PO 退化为噪声，直接跳过（PO 本就是特写细节）
        if ctx.detail_policy.procedural_object_min_area is None:
            return
        cfg = ctx.style.get("procedural_objects", {})
        # 色板
        colors_map = cfg.get("colors", {}) or {}
        self.colors: dict = {
            cat: hex_to_rgb(colors_map.get(cat, DEFAULT_COLORS[cat]))
            for cat in CATEGORY_KEYS
        }
        # 边框
        bw = cfg.get("border_width", {"fixed": 0.20, "scaled": 0.08})
        if isinstance(bw, dict):
            self.border_px = bw.get("fixed", 0.0) + bw.get("scaled", 0.0) * self.render_scale
        else:
            self.border_px = float(bw)
        self.border_color = hex_to_rgb(cfg.get("border", "#C6CAD3"))
        # 用户染色 alpha（区分原生分类色和玩家主动染色，增加饱和度微降效果）
        self.custom_alpha = float(cfg.get("custom_color_alpha", 1.0))
        self._agg = aggdraw.Draw(ctx.image)

        # 中度抽象：过滤掉微小 PO（小于 1m²），避免远处斑点
        self.min_area = float(ctx.detail_policy.procedural_object_min_area)

        # 瓦片裁剪：只保留与当前渲染区域（含外扩）相交的 PO（O(1) AABB）。
        wx0, wz0, wx1, wz1 = (ctx.world_x0, ctx.world_z0,
                              ctx.world_x1, ctx.world_z1)
        po_items = [po for po in document.procedural_objects
                    if intersects(po_bbox(po), wx0, wz0, wx1, wz1)]

        # 单趟：逐个 PO 绘制（数量一般 < 几万，性能可接受；若 > 10w 可后续按类别批处理）
        for po in po_items:
            if (4 * po.extent_x * po.extent_z) < self.min_area:
                continue
            self._draw_po(po)
        self._agg.flush()

    # ---- 单个 PO 绘制 ----

    def _draw_po(self, po) -> None:
        # 1. 颜色
        fill = self._resolve_color(po)
        # 2. OBB 四角（世界坐标）
        corners = self._obb_corners(po)
        # 3. 变换到屏幕坐标
        pts: List[Tuple[float, float]] = [
            self.ctx.transform(cx_world, cz_world)
            for (cx_world, cz_world) in corners
        ]
        xy = [v for p in pts for v in p]  # [x1,y1,x2,y2,x3,y3,x4,y4]
        # 4. 多边形填充 + 描边
        pen = aggdraw.Pen(self.border_color, max(self.border_px, 0.0))
        br = aggdraw.Brush(fill)
        self._agg.polygon(xy, pen, br)

    def _resolve_color(self, po) -> Tuple[int, int, int]:
        """决定 PO 的填充色：用户染色优先，否则按 prefab 分类走色板。"""
        if po.color_rgb is not None:
            cr, cg, cb = po.color_rgb
            # custom_alpha 微降饱和度，避免用户染色过于刺眼；alpha=1.0 即原样输出
            if self.custom_alpha < 1.0:
                a = self.custom_alpha
                # 与对应分类默认色线性插值（相当于柔和的"融入画面"）
                def_clr = self.colors[_po_category(po.prefab_name, po.source_type)]
                cr = int(cr * a + def_clr[0] * (1 - a))
                cg = int(cg * a + def_clr[1] * (1 - a))
                cb = int(cb * a + def_clr[2] * (1 - a))
            return (max(0, min(255, cr)), max(0, min(255, cg)), max(0, min(255, cb)))
        return self.colors[_po_category(po.prefab_name, po.source_type)]

    def _obb_corners(self, po) -> List[Tuple[float, float]]:
        """AABB(±ex,±ez) → 按 rotation_y 绕 (cx,cz) 旋转 → 返回世界坐标四角列表。

        返回顺序：(+ex,+ez) → (-ex,+ez) → (-ex,-ez) → (+ex,-ez)（闭合循环）。
        注意坐标系约定（与 transform_x/transform_z 一致）：
            世界 X → 屏幕 X
            世界 Z → 屏幕 -Y
        所以绕 Y 轴（从上往下看）旋转 θ 的 2D 旋转矩阵为：
            x' = x cos θ + z sin θ
            z' = -x sin θ + z cos θ
        """
        cx, cz = po.center_x, po.center_z
        ex, ez = po.extent_x, po.extent_z
        local = [(ex, ez), (-ex, ez), (-ex, -ez), (ex, -ez)]
        theta = po.rotation_y
        if abs(theta) < 1e-4:
            return [(cx + lx, cz + lz) for (lx, lz) in local]
        cos_t = math.cos(theta)
        sin_t = math.sin(theta)
        return [
            (cx + lx * cos_t + lz * sin_t,
             cz - lx * sin_t + lz * cos_t)
            for (lx, lz) in local
        ]
