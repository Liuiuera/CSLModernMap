"""建筑图层：按功能区渲染建筑，带描边。

对应 SPEC F005（建筑渲染）。

核心规则（反编译 BuildingBuilder / BuildingGrouping 确认）：
1. 按建筑 footprint 的完整多边形闭合绘制
2. 按 service 归一化为 7 类：Residential/Commercial/Industrial/Office/
   PublicTransport/Beautification(Monument)/Public
3. 子建筑组（含 sub_building_id 的主建筑 + 其子建筑）合并绘制，边框加粗 ×2
4. 使用 aggdraw（AGG 矢量渲染）→ 抗锯齿，边缘清晰
5. POI 建筑着色（buildings.color_mode=poi）：POI 及同街区关联建筑
   使用映射图标实际主色的淡化版本；无法解析时使用 buildings.default
"""
from __future__ import annotations

from ..style import hex_to_rgb

import aggdraw

from ..spatial import building_bbox, intersects
from .base import Layer, RenderContext
from ..detail import classify_building_poi
from ..poi_color import (
    get_poi_color_resolver, mix_white, poi_tint_mix_white,
)
from ..cmm import SemanticLevel
from ..services.poi.facility import (
    FacilityGroupCatalog,
    build_facility_groups,
    facility_container_building_ids,
)

# 渲染类别 → YAML 颜色 key（对应 amap.cmvstyle 的 Building 色板）
CATEGORIES = [
    "residential",
    "commercial",
    "industrial",
    "office",
    "publictransport",
    "beautification",
    "public",
]

# 缺省颜色（与 amap.cmvstyle 一致）
DEFAULT_COLORS = {
    "residential": "#EBEDF2",
    "commercial": "#E9EBF0",
    "industrial": "#E6E8EE",
    "office": "#EDEFF4",
    "publictransport": "#E8EAF0",
    "beautification": "#D2EBC8",
    "public": "#EEEBEE",
}


def _service_category(service: str) -> str:
    """服务类别 → 渲染类别（对应 BuildingRenderingManager.InternalServiceCategoryName）。"""
    if service == "Residential":
        return "residential"
    if service == "Commercial":
        return "commercial"
    if service in ("Industrial", "PlayerIndustry"):
        return "industrial"
    if service == "Office":
        return "office"
    if service == "PublicTransport":
        return "publictransport"
    if service in ("Beautification", "Monument"):
        return "beautification"
    return "public"


def _resolved_service(b) -> str:
    """建筑服务名（与兼容视图同口径：源值优先，回退角色小写）。"""
    return b.service or str(getattr(b.role, "value", b.role)).lower()


def _resolved_icls(b) -> str:
    """建筑 item class（与兼容视图同口径：icls 优先，回退源值）。"""
    return b.icls or b.source_raw


class BuildingLayer(Layer):
    """建筑渲染器。

    transit_colored=True（公共交通图）：把公共交通建筑（PublicTransport*
    服务，地铁站/火车站/码头等站房）从普通建筑中抽离出来，填充淡线路色 +
    边框压深线路色；其余建筑照常。
    """

    name = "building"

    def __init__(self, transit_colored: bool = False, *, fill_only: bool = False):
        self.transit_colored = transit_colored
        self.fill_only = fill_only

    def render(self, ctx: RenderContext, document) -> None:
        """按样式和 POI 语义绘制建筑占地。"""
        if not document.buildings:
            return
        # 瓦片裁剪：只保留与当前渲染区域（含外扩）相交的建筑。
        # 建筑数量（10 万+）是超大存档最大数据源，且 _group_buildings
        # 分组为 O(N²)，不裁剪时每瓦片重复全量分组 → 建筑层是最大卡点。
        wx0, wz0, wx1, wz1 = (ctx.world_x0, ctx.world_z0,
                              ctx.world_x1, ctx.world_z1)
        buildings = [b for b in document.buildings
                     if intersects(building_bbox(b), wx0, wz0, wx1, wz1)]
        if not buildings:
            return
        self.ctx = ctx
        # 放大倍数（相对 4096 全图基准），建筑描边随特写等比加粗
        self.render_scale = ctx.zoom
        # 抽象分级按最终输出尺寸（抗锯齿下采样后）
        self._lvl = ctx.detail_policy.abstract_level
        # 强抽象（如 512 全图）：建筑退化为噪声，直接跳过
        if ctx.detail_policy.building_min_area is None:
            return
        cfg = ctx.style.get("buildings", {})
        bw = cfg.get("border_width", {"fixed": 0.25, "scaled": 0.1})
        if isinstance(bw, dict):
            self.border_px = bw.get("fixed", 0.0) + bw.get("scaled", 0.0) * self.render_scale
        else:
            self.border_px = float(bw)
        self.border_color = hex_to_rgb(cfg.get("border", "#D7DAE1"))
        default_color = hex_to_rgb(cfg.get("default", "#D8D2C6"))
        self.colors = {cat: default_color for cat in CATEGORIES}
        # 建筑着色直接使用映射图标的颜色，不受图标可见性影响。
        self._poi_tint_enabled = str(cfg.get("color_mode", "poi")).casefold() != "uniform"
        self._poi_mix_white = poi_tint_mix_white(cfg)
        self._poi_building_colors = {}
        if self._poi_tint_enabled:
            resolver = get_poi_color_resolver(ctx)
            shared_colors = ctx.shared.setdefault("poi_building_colors", {})
            for building in buildings:
                building_id = int(building.id)
                resolved = resolver.building_color(building, document.metadata.origin.game.lower())
                if resolved is None:
                    shared_colors.pop(building_id, None)
                    continue
                rgb = tuple(resolved[1])
                shared_colors[building_id] = rgb
                self._poi_building_colors[building_id] = rgb
        # 公共交通图：公共交通建筑 → 线路色映射（按渲染上下文缓存）。
        # 线路色推导经 MapDocumentView 从 CMM 惰性取数。
        self.line_colors = {}
        if self.transit_colored:
            from .. import transit
            cache_key = "transit_building_colors"
            cached = ctx.shared.get(cache_key)
            if cached is None:
                cached = transit.building_line_colors(
                    ctx.session._legacy_view())
                ctx.shared[cache_key] = cached
            self.line_colors = cached
        self._agg = aggdraw.Draw(ctx.image)

        # 1. 子建筑分组（对应 BuildingGrouping.GroupBySubBuilding）。
        # 语义目录属于整个渲染上下文，分块不能把被裁剪的子对象误认成独立 POI。
        facility_catalog = ctx.shared.get("facility_group_catalog")
        if facility_catalog is None:
            facility_catalog = build_facility_groups(document)
            ctx.shared["facility_group_catalog"] = facility_catalog
        container_ids = facility_container_building_ids(document)
        buildings = [item for item in buildings if int(item.id) not in container_ids]
        groups = self._group_buildings(buildings, facility_catalog)

        # 2. 普通建筑按类别收集，子建筑组立即绘制
        normals = {cat: [] for cat in CATEGORIES}
        for group in groups:
            main = group[0]
            min_area = float(ctx.detail_policy.building_min_area or 0.0)
            priority_floor = ctx.detail_policy.important_building_priority
            priority, _category = classify_building_poi(
                _resolved_icls(main), _resolved_service(main), main.sub_service)
            if (self._footprint_area(main) < min_area and
                    (priority_floor is None or priority < priority_floor)):
                continue
            if len(group) > 1:
                self._draw_group(group)
            else:
                normals[_service_category(_resolved_service(main))].append(main)

        # 3. 普通建筑逐类绘制（公共交通建筑 transit_colored 时按线路色）
        for cat in CATEGORIES:
            fill = self.colors[cat]
            bw_n = self._border_width(multi=False)
            for b in normals[cat]:
                if self.transit_colored and b.id in self.line_colors:
                    self._draw_building_line(b, self.line_colors[b.id], bw_n)
                else:
                    tint = self._poi_fill(b)
                    self._draw_building(
                        b, tint if tint is not None else fill, bw_n)

        self._agg.flush()

    # ---- POI 淡色足迹 ----

    def _poi_fill(self, b):
        """存在关联 POI 图标时返回其柔和化颜色。"""
        if not getattr(self, "_poi_tint_enabled", False):
            return None
        resolved = getattr(self, "_poi_building_colors", {}).get(int(b.id))
        if resolved is not None:
            rgb = resolved[1] if isinstance(resolved, (tuple, list)) and len(resolved) == 2 else resolved
            return mix_white(rgb, getattr(self, "_poi_mix_white", 0.70))
        return None

    # ---- 分组 ----

    def _group_buildings(
        self, buildings: list,
        facility_catalog: FacilityGroupCatalog | None = None,
    ) -> list:
        """按 SubBuildingId / ParentBuildingId 分组（简化 GroupBySubBuilding）。

        子建筑父索引按 parent_building_id 一次性建立（O(N)），主建筑沿索引
        递归收集子建筑，替换旧版在 while 循环内对全量 buildings 的线性扫描
        （O(N²)，超大存档子建筑多时是建筑层卡死主因）。
        """
        by_id = {int(b.id): b for b in buildings}
        groups = []
        used = set()

        # 优先使用世界坐标语义组件，只有子对象占地进入分块时也适用；设施分组不在此合并。
        if facility_catalog is not None:
            semantic_groups = {}
            for building in sorted(buildings, key=lambda item: int(item.id)):
                group = facility_catalog.group_for_building(int(building.id))
                if (group is None
                        or group.semantic_level is not SemanticLevel.COMPOSITE_BUILDING
                        or group.id in semantic_groups):
                    continue
                members = [by_id[item] for item in group.member_building_ids
                           if item in by_id]
                if not members:
                    continue
                roots = [item for item in members
                         if int(item.id) in group.root_building_ids]
                main = roots[0] if roots else members[0]
                ordered = [main] + [item for item in members if item is not main]
                semantic_groups[group.id] = ordered
                used.update(int(item.id) for item in ordered)
                groups.append(ordered)

        children_by_parent: dict[str, list] = {}
        for b in buildings:
            parent_id = (b.parent_building_id or getattr(b, "parent_id", None))
            if parent_id:
                children_by_parent.setdefault(
                    str(parent_id), []).append(b)
        for b in buildings:
            if b.id in used:
                continue
            child_id = b.sub_building_id or (
                str(b.child_ids[0]) if getattr(b, "child_ids", None) else "")
            parent_id = b.parent_building_id or getattr(b, "parent_id", None)
            if child_id and not parent_id:
                # 主建筑：沿父索引收集其所有子建筑（含递归孙建筑）
                group = [b]
                used.add(b.id)
                stack = [str(b.id)]
                while stack:
                    pid = stack.pop()
                    for c in children_by_parent.get(str(pid), ()):
                        if c.id in used:
                            continue
                        used.add(c.id)
                        group.append(c)
                        if (c.sub_building_id or getattr(c, "child_ids", None)):
                            stack.append(str(c.id))
                groups.append(group)
            else:
                # 无子建筑关系的普通建筑 / 父建筑未纳入当前瓦片的孤儿 → 单组
                used.add(b.id)
                groups.append([b])
        return groups

    # ---- 绘制 ----

    def _footprint_area(self, b) -> float:
        """建筑占地面积（世界单位²，完整轮廓的鞋带公式）。"""
        pts = b.footprint.points
        if len(pts) < 3:
            return 0.0
        s = 0.0
        for i in range(len(pts)):
            p1, p2 = pts[i], pts[(i + 1) % len(pts)]
            s += p1.x * p2.z - p2.x * p1.z
        return abs(s) / 2.0

    def _poly_pts(self, b):
        pts = [self.ctx.transform(p.x, p.z) for p in b.footprint.points]
        return pts if len(pts) >= 3 else None

    def _border_width(self, multi: bool) -> float:
        """描边宽度（像素，aggdraw 支持亚像素线宽）。"""
        if self.fill_only:
            return 0.0
        bw = self.border_px * (2 if multi else 1)
        return bw if bw > 0 else 0.0

    def _draw_building(self, b, fill, border_w: float) -> None:
        pts = self._poly_pts(b)
        if pts is None:
            return
        coords = tuple(v for xy in pts for v in xy)
        pen = aggdraw.Pen(self.border_color, border_w) if border_w > 0 else None
        brush = aggdraw.Brush(fill) if fill else None
        self._agg.polygon(coords, pen, brush)

    def _draw_building_line(self, b, line_rgb, border_w: float) -> None:
        """公共交通建筑按线路色绘制：填充淡线路色 + 边框压深线路色。"""
        pts = self._poly_pts(b)
        if pts is None:
            return
        coords = tuple(v for xy in pts for v in xy)
        fill = tuple(int(c * 0.85 + 255 * 0.15) for c in line_rgb)
        pen_c = tuple(int(c * 0.72) for c in line_rgb)
        pen = aggdraw.Pen(pen_c, border_w) if border_w > 0 else None
        brush = aggdraw.Brush(fill) if fill else None
        self._agg.polygon(coords, pen, brush)

    def _draw_group(self, group: list) -> None:
        """子建筑组：合并绘制，边框加粗（对应 BuildingWithSubBuildingBorderPen ×2）。"""
        main = group[0]
        if self.transit_colored and main.id in self.line_colors:
            fill = tuple(int(c * 0.85 + 255 * 0.15)
                         for c in self.line_colors[main.id])
            pen_c = tuple(int(c * 0.72) for c in self.line_colors[main.id])
            bw = self._border_width(multi=True)
            pen = aggdraw.Pen(pen_c, bw) if bw > 0 else None
        else:
            tint = next((value for b in group
                         if (value := self._poi_fill(b)) is not None), None)
            fill = (tint if tint is not None
                    else self.colors[_service_category(_resolved_service(main))])
            bw = self._border_width(multi=True)
            pen = aggdraw.Pen(self.border_color, bw) if bw > 0 else None
        brush = aggdraw.Brush(fill) if fill else None
        for b in group:
            pts = self._poly_pts(b)
            if pts is None:
                continue
            self._agg.polygon(
                tuple(v for xy in pts for v in xy), pen, brush
            )
