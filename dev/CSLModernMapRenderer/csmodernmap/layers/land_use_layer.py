"""用地类型图层：按建筑功能区在地图底色上铺分类色块。

对应 SPEC F005 扩展：把城市建成区按用地类型（住宅/商业/工业/办公/
绿地/公共服务/交通枢纽…）分区上色，得到"用地类型图"（类似城市规划
用地图）。独立功能：默认关闭（与"区划"图层互不叠加），由 GUI/CLI 显式启用。

算法（复用 built_area 掩膜管线）：
1. 建筑轮廓按 service（或细分 sub_service）归一化为类别，栅格化到统一掩膜，
   类别按优先级从低到高绘制（高优先级覆盖低优先级）
2. 每类掩膜 GaussianBlur + contourpy 提取 50% 等值线
3. 像素坐标 Chaikin 平滑 + aggdraw 抗锯齿半透明填充
"""
from __future__ import annotations

from ..zone_geometry import grid_rings
from ..path_model import chaikin

from ..style import hex_to_rgb

import aggdraw
import numpy as np
from PIL import Image, ImageDraw

from ..built_area import WORLD_MIN, WORLD_SIZE
from ..spatial import building_bbox, intersects
from .base import Layer, RenderContext

# 大类绘制顺序（从低到高，高优先级盖在低优先级上面）
DRAW_ORDER = [
    "green",            # 绿地/公园/景观
    "public",           # 公共服务（学校/医院/市政设施等）
    "residential",      # 住宅
    "industrial",       # 工业/产业
    "office",           # 办公
    "publictransport",  # 公共交通（车站/机场等）
    "commercial",       # 商业
]

# service → 大类；未列出的 service 归入 "public"
SERVICE_MAP = {
    "Residential": "residential",
    "Commercial": "commercial",
    "Industrial": "industrial",
    "PlayerIndustry": "industrial",
    "Office": "office",
    "PublicTransport": "publictransport",
    "Beautification": "green",
    "Monument": "green",
    "Education": "public",
    "HealthCare": "public",
    "FireDepartment": "public",
    "PoliceDepartment": "public",
    "Electricity": "public",
    "Water": "public",
    "Hotel": "public",
}

# 忽略的 service（道路设施等非用地实体，不构成用地色块）
EXCLUDE_SERVICES = ("Road",)

# 细分模式（detail=sub）：(service, sub_service) → 细分类别 key
SUB_MAP = {
    ("Residential", "ResidentialLow"): "res_low",
    ("Residential", "ResidentialHigh"): "res_high",
    ("Residential", "ResidentialWallToWall"): "res_wall",
    ("Commercial", "CommercialLow"): "com_low",
    ("Commercial", "CommercialHigh"): "com_high",
    ("Commercial", "CommercialWallToWall"): "com_wall",
    ("Office", "OfficeGeneric"): "off_generic",
    ("Office", "OfficeHightech"): "off_hightech",
    ("Office", "OfficeFinancial"): "off_financial",
    ("Industrial", "IndustrialGeneric"): "ind_generic",
    ("Industrial", "IndustrialOre"): "ind_ore",
    ("Industrial", "IndustrialForestry"): "ind_forestry",
    ("Industrial", "IndustrialFarming"): "ind_farming",
    ("Industrial", "IndustrialOil"): "ind_oil",
    ("PlayerIndustry", "PlayerIndustryFarming"): "ind_farming",
    ("PlayerIndustry", "PlayerIndustryForestry"): "ind_forestry",
    ("PlayerIndustry", "PlayerIndustryOre"): "ind_ore",
    ("PlayerIndustry", "PlayerIndustryOil"): "ind_oil",
}

# 默认大类配色（参考城市规划用地分类色系）
DEFAULT_COLORS = {
    "green": "#B8DCA0",
    "public": "#D8D0C8",
    "residential": "#F2C97E",
    "industrial": "#C9A7CC",
    "office": "#8FB8E0",
    "publictransport": "#A0D4B0",
    "commercial": "#F2A07E",
}

# 细分配色（未列出时回退到所属大类颜色）
DEFAULT_SUB_COLORS = {
    "res_low": "#F5D9A8",
    "res_high": "#E8B65C",
    "res_wall": "#DBA54A",
    "com_low": "#F5B08C",
    "com_high": "#E88A5E",
    "com_wall": "#D96B4A",
    "off_generic": "#8FB8E0",
    "off_hightech": "#6FA0D0",
    "off_financial": "#5C8CC8",
    "ind_generic": "#C9A7CC",
    "ind_ore": "#B89BC4",
    "ind_forestry": "#A8C49B",
    "ind_farming": "#C4D89B",
    "ind_oil": "#8FA8C4",
}


class LandUseLayer(Layer):
    """用地类型渲染器（按建筑功能区铺分类色块）。"""

    name = "land_use"

    def render(self, ctx: RenderContext, document) -> None:
        cfg = ctx.style.get("land_use", {})
        if not cfg or not cfg.get("enabled", True):
            return
        if not document.buildings:
            return

        detail = str(cfg.get("detail", "base"))
        cell = float(cfg.get("cell_meters", 16.0))
        smooth_blur = float(cfg.get("smooth_blur", 1.5) or 0.0)
        upscale = max(1, int(cfg.get("contour_upscale", 2) or 1))
        smooth_iters = int(cfg.get("smooth_iters", 3) or 0)
        alpha = int(cfg.get("alpha", 110))
        colors_cfg = cfg.get("colors", {})

        # 1. 建筑按类别分组（类别顺序即绘制优先级，从低到高）
        cats = self._categorize(document, detail, ctx=ctx)
        if not cats:
            return
        keys = list(cats.keys())

        # 2. 统一掩膜：像素值 = 类别序号 + 1（0 = 无建筑），后画类别覆盖先画
        mask, ox, oz = self._build_mask(document, cats, keys, cell, ctx)
        if mask is None:
            return
        arr = np.asarray(mask, dtype=np.int32)

        agg = aggdraw.Draw(ctx.image)
        # 注意：aggdraw 的 Brush 不支持 rgba 4 元组，半透明须用
        # Brush(颜色3元组, opacity=透明度)（Polygon 需扁平坐标）。
        for idx, key in enumerate(keys):
            fill = hex_to_rgb(self._color_for(key, colors_cfg))
            for pts in grid_rings(arr, idx + 1, cell, ox, oz,
                                       upscale, smooth_blur):
                if len(pts) < 3:
                    continue
                pix = [ctx.transform(float(p[0]), float(p[1])) for p in pts]
                pix = chaikin(pix, smooth_iters)
                if len(pix) >= 3:
                    coords = tuple(v for xy in pix for v in xy)
                    agg.polygon(coords, None,
                                aggdraw.Brush(fill, opacity=alpha))
        agg.flush()

    # ---- 分类 ----

    def _categorize(self, document, detail: str, ctx=None) -> dict:
        """建筑按类别分组。返回 {类别 key: [Building, ...]}，顺序=DRAW_ORDER。"""
        cats: dict = {}
        # 瓦片裁剪：只处理与当前渲染区域（含外扩）相交的建筑，避免分块时
        # 每瓦片重复遍历全量建筑（独立图层 land_use 同样随瓦片重复调用）。
        buildings = document.buildings
        if ctx is not None:
            wx0, wz0, wx1, wz1 = (ctx.world_x0, ctx.world_z0,
                                  ctx.world_x1, ctx.world_z1)
            buildings = [b for b in buildings
                         if intersects(building_bbox(b), wx0, wz0, wx1, wz1)]
        for b in buildings:
            service = b.service or str(getattr(b.role, "value", b.role)).lower()
            pts = b.footprint.points
            if service in EXCLUDE_SERVICES or len(pts) < 3:
                continue
            if detail == "sub":
                key = SUB_MAP.get((service, b.sub_service))
            else:
                key = None
            if key is None:
                key = SERVICE_MAP.get(service, "public")
            cats.setdefault(key, []).append(b)
        # 保证绘制顺序稳定：先大类顺序，再细分类别（按首次出现顺序）
        ordered: dict = {}
        for k in DRAW_ORDER:
            if k in cats:
                ordered[k] = cats[k]
        for k in cats:
            if k not in ordered:
                ordered[k] = cats[k]
        return ordered

    def _color_for(self, key: str, colors_cfg: dict) -> str:
        """取类别颜色：样式配置优先，其次细分默认色，最后大类默认色。"""
        if colors_cfg and isinstance(colors_cfg, dict):
            c = colors_cfg.get(key)
            if c:
                return str(c)
            sub = colors_cfg.get("sub", {})
            if isinstance(sub, dict):
                c = sub.get(key)
                if c:
                    return str(c)
        if key in DEFAULT_SUB_COLORS:
            return DEFAULT_SUB_COLORS[key]
        if key in DEFAULT_COLORS:
            return DEFAULT_COLORS[key]
        return DEFAULT_COLORS["public"]

    # ---- 掩膜构建 ----

    def _build_mask(self, document, cats, keys, cell: float, ctx):
        """构建类别掩膜（int32 图像，像素值 = 类别序号 + 1）。

        只覆盖可见世界范围（对齐 built_area 的裁剪思路），
        返回 (mask_img, origin_x, origin_z) 或 None。
        """
        grid_n = int(round(WORLD_SIZE / cell))
        wx0 = max(ctx.world_x0, WORLD_MIN)
        wz0 = max(ctx.world_z0, WORLD_MIN)
        wx1 = min(ctx.world_x1, WORLD_MIN + WORLD_SIZE)
        wz1 = min(ctx.world_z1, WORLD_MIN + WORLD_SIZE)
        if wx1 <= wx0 or wz1 <= wz0:
            return None
        gx0 = max(0, min(grid_n - 1, int(np.floor((wx0 - WORLD_MIN) / cell))))
        gz0 = max(0, min(grid_n - 1, int(np.floor((wz0 - WORLD_MIN) / cell))))
        gx1 = max(0, min(grid_n - 1, int(np.ceil((wx1 - WORLD_MIN) / cell))))
        gz1 = max(0, min(grid_n - 1, int(np.ceil((wz1 - WORLD_MIN) / cell))))
        if gx1 <= gx0 or gz1 <= gz0:
            return None
        gw, gh = gx1 - gx0 + 1, gz1 - gz0 + 1
        mask = Image.new("I", (gw, gh), 0)
        d = ImageDraw.Draw(mask)
        # 按类别顺序（低→高）绘制：高优先级类别覆盖低优先级
        for idx, key in enumerate(keys):
            for b in cats[key]:
                pts = [
                    ((p.x - WORLD_MIN) / cell - gx0,
                     (p.z - WORLD_MIN) / cell - gz0)
                    for p in b.footprint.points
                ]
                if len(pts) >= 3:
                    d.polygon(pts, fill=idx + 1)
        return mask, WORLD_MIN + gx0 * cell, WORLD_MIN + gz0 * cell
