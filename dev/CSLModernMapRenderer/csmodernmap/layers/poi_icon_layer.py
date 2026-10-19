"""POI 图标图层：用 POI 图标标记重要设施，带聚合控制避免过密。

渲染内容：
- 特殊建筑（消防局、医院、学校、交通枢纽等）
- 交通站点（地铁站、公交站、火车站、机场、码头）
- 公园/园区
- 红绿灯（交通快照 lights 节点，traffic_light 图标）
- 高速出入口（识别高速匝道与普通道路的连接点，highway_exit / highway_entrance）
- POI 名字：保留存档实际显示名；无名设施只绘制图标

聚合策略（实体与显示两阶段）：
1. 实体合并发生在世界坐标的 POI 服务（building/transit 两个独立开关）
2. 建筑、交通子图层共用语义身份，各自控制显示
3. 两轮显示网格先按类别选代表，再限制单元总量；之后处理图标/名称碰撞
4. 显示筛选不能删除服务目录，实体合并不能代替显示密度控制
"""
from __future__ import annotations

from ..style import hex_to_rgb

import math
import logging
import os
import sys
from dataclasses import dataclass
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from ..data import MapData
from ..cmm import normalize_transit_kind
from .._paths import cn_icons_dir, icons_library_dir
from ..fonts import load_pillow_font, resolve_text_font
from ..poi_color import dominant_icon_rgb
from ..services.poi import POIJobParams, produce as produce_poi_catalog
from ..services.poi.ownership import poi_group_for, poi_visibility_group_for, sublayer_for
from ..services.poi.merge import strong_name_key
from ..services.transit import resolve_route_geometry
from .base import Layer, RenderContext
from .poi_density import filter_pois_by_distance, poi_density_params, select_pois

LOGGER = logging.getLogger(__name__)

# 图标工具模块路径
_ICON_UTILS_DIR = os.path.normpath(cn_icons_dir())
if _ICON_UTILS_DIR not in sys.path:
    sys.path.insert(0, _ICON_UTILS_DIR)

# 中文字体候选（与 label_layer 一致）
_FONT_CANDIDATES = [
    "simhei.ttf",
    "msyh.ttc",
    "msyhbd.ttc",
    "simsun.ttc",
]


@dataclass
class POIItem:
    """一个 POI 条目。"""
    px: float
    py: float
    icon_id: str
    label: str
    category: str      # 用于聚合去重（同类只留一个）
    priority: int
    source: str        # 来源：building / transport / park / node
    pool_key: str = ""        # 历史 CMM 字段，仅用于兼容读取
    draw_name: bool = True    # 是否绘制名字文字（红绿灯/出入口等不画）
    avoid_dir: Optional[Tuple[float, float]] = None  # 站台圆点避让方向（像素单位向量）
    name_source: str = "empty"  # 自定义名称、游戏名称或空名称。
    semantic_level: str = "BUILDING"
    group_id: str = ""
    parent_poi_id: str = ""
    poi_id: str = ""
    poi_group: str = "ordinary"
    kind: str = ""
    world_x: float | None = None
    world_z: float | None = None
    strong_name: str = ""


class _Occupancy:
    """供两个 POI 子图层共用的像素空间碰撞索引。"""
    def __init__(self):
        self.cells = {}

    def _keys(self, box):
        x0, y0, x1, y1 = box
        for x in range(math.floor(x0 / 64), math.floor(x1 / 64) + 1):
            for y in range(math.floor(y0 / 64), math.floor(y1 / 64) + 1):
                yield x, y

    def add(self, box):
        for key in self._keys(box):
            self.cells.setdefault(key, []).append(box)

    def overlaps(self, box):
        return any(box[0] < b[2] and b[0] < box[2] and box[1] < b[3] and b[1] < box[3]
                   for key in self._keys(box) for b in self.cells.get(key, ()))


def _resolve_size(val, render_scale: float) -> float:
    if isinstance(val, dict):
        return val.get("fixed", 0.0) + val.get("scaled", 0.0) * render_scale
    return float(val)


def _active_profile(cfg: dict) -> dict:
    value = cfg.get("active_profile", {}) if isinstance(cfg, dict) else {}
    return value if isinstance(value, dict) else {}


def _is_static_png(cfg: dict) -> bool:
    return _active_profile(cfg).get("target") in {"normal_png", "transit_png"}


class POIIconLayer(Layer):
    """POI 图标渲染器（带网格聚合）。

    transit_only=True（公共交通图叠加层）：只收集交通站点 POI
    （地铁站/火车站/码头/公交站/机场等，source=transport），把公共交通
    相关设施从普通 POI 中抽离出来单独标注；不画建筑/公园/红绿灯/出入口。
    """

    name = "poi_icons"

    def __init__(self, icon_map=None, transit_only: bool = False,
                 *, force_detail: bool = False, sublayer: str | None = None):
        self.icon_map = icon_map
        self.transit_only = transit_only
        # 公共交通 POI 必须在线路/站点之后绘制。TransportLayer 本身是 late
        # 图层，因此这里也进入 late 队列，并依靠注册顺序保持在它之上。
        # 常规 POI 仍沿用原有 early 行为，不改变普通地图层级。
        self.late = bool(transit_only)
        self.force_detail = bool(force_detail)
        self.sublayer = sublayer
        self._children = None
        self._sprite_img = None
        self._icon_cache = {}
        self._icon_sources = {}
        self._color_cache = {}         # icon_id -> 主色 #RRGGBB
        self._font_cache = {}          # 字体尺寸到 ImageFont 的缓存。
        # 站台圆点避让开关/距离（仅公共交通图叠加层启用，常规渲染恒为关闭，
        # 保证正常模式 POI 完全不受影响）
        self._station_avoid = False
        self._station_avoid_dist = None
        self._transit_view_scale = 1.0
        self._prepared_catalogs = {}
        self._poi_line_geometry = {}
        self._poi_geometry_cache = {}

    def supports(self, document) -> bool:
        return True

    def _poi_scope(self) -> str:
        """返回位图图层使用的 POI 目录范围。"""
        return "transit" if self.transit_only else "normal"

    def prepare(self, session, job) -> None:
        """启动分块工作进程前只推导一次有效目录。"""
        self._prepared_catalogs = {}
        style = getattr(session, "_prepare_style", None)
        if style is None:
            return
        policy = getattr(session, "_prepare_detail_policy", None)
        base = style.get("poi_icons", {}) or {}
        has_snapshot = isinstance(base.get("visibility_snapshot"), dict)
        if (not base.get("png_export") and not _is_static_png(base)
                and not self.force_detail
                and policy is not None and policy.poi_max_per_cell == 0):
            return
        override_path = style.get("_poi_mapping_override") or None
        if not has_snapshot and not base.get("enabled", True):
            return
        owners = ([self.sublayer] if self.sublayer else
                  (["transit"] if self.transit_only else ["transit", "building"]))
        for owner in owners:
            cfg = {**base, **base.get(owner, {})}
            if not has_snapshot and not cfg.get("enabled", True):
                continue
            params = POIJobParams.from_style(
                {"poi_icons": cfg, "transports": style.get("transports", {})},
                scope=self._poi_scope())
            key = ("poi.t05.catalog", self._poi_scope(), params.fingerprint,
                   str(override_path or ""))
            if key not in self._prepared_catalogs:
                def build_catalog():
                    return session.poi_catalog(params, override_path=override_path)
                self._prepared_catalogs[key] = session.service_cache.get_or_compute(
                    "poi.bitmap_catalog", {"params": params.fingerprint, "override": override_path},
                    build_catalog)

    def _poi_catalog(self, mapdata, cfg: dict):
        """为本次渲染构建或读取世界坐标 POI 目录；每个分块单独执行像素变换和密度筛选。"""
        shared = getattr(self.ctx, "shared", None)
        config = {"poi_icons": cfg, "transports": self.ctx.style.get("transports", {})}
        override_path = self.ctx.style.get("_poi_mapping_override") or None
        params = POIJobParams.from_style(config, scope=self._poi_scope())
        key = ("poi.t05.catalog", self._poi_scope(), params.fingerprint,
               str(override_path or ""))
        catalog = self._prepared_catalogs.get(key)
        if catalog is None:
            catalog = (shared or {}).get(key)
        if catalog is None:
            if override_path:
                catalog = produce_poi_catalog(
                    mapdata, params, override_path=override_path)
            else:
                catalog = produce_poi_catalog(mapdata, params)
            if shared is not None:
                shared[key] = catalog
        return catalog


    def _service_stop_avoid_dir(self, mapdata: MapData, poi) -> Optional[Tuple[float, float]]:
        """目录构建后在像素空间计算交通站点避让。"""
        if not self._station_avoid or getattr(poi.source, "kind", "") != "stop":
            return None
        source_id = int(getattr(poi.source, "id", 0))
        node_id = source_id
        # 原生 CMM 站点标识是源实体标识，线路几何另按网络节点标识；旧投影则使用节点标识作为回退。
        for source_stop in getattr(mapdata, "stops", ()) or ():
            if int(getattr(source_stop, "id", -1)) == source_id:
                node_id = int(getattr(source_stop, "network_node_id", source_id))
                break
        node = mapdata.node(node_id)
        if node is None:
            return None

        line_candidates = []
        wanted_kind = str(getattr(poi, "kind", "")).casefold()
        for tr in mapdata.transports:
            if not any(int(stop.node_id) == node_id for stop in tr.stops):
                continue
            if normalize_transit_kind(tr.type).value.casefold() == wanted_kind:
                line_candidates.append(tr)
        if not line_candidates:
            line_candidates = [
                tr for tr in mapdata.transports
                if any(int(stop.node_id) == node_id for stop in tr.stops)
            ]

        for tr in line_candidates:
            points = self._poi_line_geometry.get(tr.id)
            if points is None:
                points = resolve_route_geometry(
                    mapdata, tr, normalize_transit_kind(tr.type),
                    cache=self._poi_geometry_cache,
                ).points
                self._poi_line_geometry[tr.id] = points
            if not points:
                continue
            nx, nz = node.pos.x, node.pos.z
            best, best_distance = 0, float("inf")
            for index, point in enumerate(points):
                distance = ((point.x - nx) ** 2 + (point.z - nz) ** 2)
                if distance < best_distance:
                    best_distance, best = distance, index
            i0, i1 = max(0, best - 1), min(len(points) - 1, best + 1)
            if i0 == i1:
                continue
            a = self.ctx.transform(points[i0].x, points[i0].z)
            b = self.ctx.transform(points[i1].x, points[i1].z)
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = math.hypot(dx, dy)
            if length >= 1e-9:
                return (-dy / length, dx / length)
        return None

    def _service_poi_items(
        self, mapdata: MapData, cfg: dict, min_priority: int,
        show_categories: set,
    ) -> List[POIItem]:
        """把世界坐标 POI 适配为图层使用的像素域对象。"""
        result: List[POIItem] = []
        catalog = self._poi_catalog(mapdata, cfg)
        from ..building_names import BuildingNames
        names = BuildingNames(mapdata.buildings, cfg.get("use_game_names", False))
        groups = {str(group.id): group for group in catalog.facility_groups}
        hidden_categories = set(cfg.get("hidden_categories", ()))
        name_filters = cfg.get("name_filters", {}) or {}
        show_player_names = bool(name_filters.get("player", True))
        show_important_names = bool(name_filters.get("important", True))
        png_policy = cfg.get("png_export")
        active_profile = _active_profile(cfg)
        static_profile = _is_static_png(cfg)
        visibility = cfg.get("visibility_snapshot")
        has_visibility_snapshot = isinstance(visibility, dict)
        name_visibility = (visibility.get("names", {})
                           if has_visibility_snapshot else {})
        from ..services.poi.visibility import visible_pois
        for poi in visible_pois(catalog, cfg,
                                visibility_snapshot=visibility):
            category = str(getattr(poi, "kind", getattr(poi, "category", "")))
            poi_group = poi_group_for(poi)
            owner = sublayer_for(poi)
            if self.sublayer is not None and owner != self.sublayer:
                continue
            if (not has_visibility_snapshot
                    and not cfg.get(owner, {}).get("enabled", True)):
                continue
            if static_profile:
                if (getattr(poi, "parent_poi_id", "")
                        and not bool(active_profile.get(
                            "png_internal_buildings", False))):
                    continue
            elif png_policy:
                if ((owner == "building" or getattr(poi.source, "kind", "") == "building")
                        and getattr(poi, "parent_poi_id", "")
                        and not png_policy["internal_buildings"]):
                    continue
            elif float(getattr(self.ctx, "final_zoom", 1.0)) < float(getattr(poi, "min_zoom", 0.0)):
                continue
            if not (has_visibility_snapshot or static_profile) and category in hidden_categories:
                continue
            if not (has_visibility_snapshot or static_profile) and int(getattr(poi, "priority", 0)) < min_priority:
                continue
            if not (has_visibility_snapshot or static_profile) and show_categories and category not in show_categories:
                continue
            anchor = poi.anchor
            name_obj = names.poi(poi, groups)
            name_source = str(getattr(name_obj, "source", "") or "").casefold()
            label = str(getattr(name_obj, "text", "") or "").strip()
            if has_visibility_snapshot:
                if not bool(name_visibility.get(poi_visibility_group_for(poi), True)):
                    label = ""
            elif not static_profile:
                is_player_name = name_source in {
                    "custom", "custom_user_name", "player", "user",
                }
                if ((is_player_name and not show_player_names)
                        or (not is_player_name and (
                            not show_important_names
                            or (poi_group != "area"
                                and int(getattr(poi, "priority", 0)) < 70)))):
                    label = ""
            if (active_profile.get("target") == "preview"
                    and not has_visibility_snapshot
                    and not cfg.get("show_name", True)):
                label = ""
            # 布局使用完整目录，避免分块边界改变标注胜出结果；PIL 在当前画布裁剪绘制。
            px, py = self.ctx.transform(anchor.x, anchor.z)
            source_kind = str(getattr(poi.source, "kind", ""))
            source = "transport" if source_kind == "stop" else source_kind
            result.append(POIItem(
                px=px, py=py,
                icon_id=str(getattr(poi, "icon_key", "") or category),
                label=label,
                category=category,
                priority=int(getattr(poi, "priority", 0)),
                source=source,
                pool_key=str(getattr(poi, "pool_key", "") or ""),
                draw_name=bool(getattr(poi, "draw_name", True) or name_source == "category")
                and bool(label),
                avoid_dir=self._service_stop_avoid_dir(mapdata, poi),
                name_source=name_source,
                semantic_level=str(getattr(poi, "semantic_level", "BUILDING")),
                group_id=str(getattr(poi, "group_id", "") or ""),
                parent_poi_id=str(getattr(poi, "parent_poi_id", "") or ""),
                poi_id=str(getattr(poi, "id", "")),
                poi_group=poi_group,
                kind=category,
                world_x=float(anchor.x),
                world_z=float(anchor.z),
                strong_name=strong_name_key(poi) or "",
            ))
        return result

    def render(self, ctx: RenderContext, document) -> None:
        """筛选并布局当前画布中的 POI 图标与名称。"""
        if self.sublayer is None:
            if self._children is None:
                self._children = [TransitPOILayer(transit_only=self.transit_only,
                                                  force_detail=self.force_detail)]
                if not self.transit_only:
                    self._children.append(BuildingPOILayer(force_detail=self.force_detail))
            occupancy = _Occupancy()
            icon_occupancy = _Occupancy()
            # 子图层先预留图标位置，再共享标注占位空间；交通标注优先。
            for child in self._children:
                child._prepared_catalogs = self._prepared_catalogs
                child._occupancy = occupancy
                child._icon_occupancy = icon_occupancy
                child._defer_names = True
                child._pending_names = []
                child.render(ctx, document)
            for child in self._children:
                for args in child._pending_names:
                    child._draw_icon_name(*args)
            return
        # POI 目录服务经 MapDocumentView 从 CMM 惰性取数。
        mapdata = (ctx.session._legacy_view() if ctx.session is not None
                   else document)
        base_cfg = ctx.style.get("poi_icons", {})
        cfg = {**base_cfg, **base_cfg.get(self.sublayer, {})}
        active_profile = _active_profile(cfg)
        static_profile = _is_static_png(cfg)
        has_visibility_snapshot = isinstance(
            cfg.get("visibility_snapshot"), dict)
        if not has_visibility_snapshot and not base_cfg.get("enabled", True):
            return
        if not has_visibility_snapshot and not cfg.get("enabled", True):
            return
        policy = ctx.detail_policy
        # 内部强简化档完全关闭 POI；公共交通专用叠加层强制保留。
        png_policy = cfg.get("png_export")
        if (not png_policy and not static_profile
                and active_profile.get("target") != "preview"
                and not self.force_detail and policy.poi_max_per_cell == 0):
            return

        # 懒加载图标工具
        if (self.icon_map is None
                or getattr(self.icon_map, "appearance", "light") != cfg.get("appearance", "light")):
            self._init_icon_tools(cfg)

        self.ctx = ctx
        self._poi_line_geometry = {}
        self._poi_geometry_cache = {}
        self._poi_cfg = cfg
        self._active_profile = active_profile
        self._static_png_profile = static_profile
        self._overlap_avoidance = bool(active_profile.get(
            "overlap_avoidance", bool(png_policy)))
        self._raster_scale = ctx.zoom / max(float(ctx.final_zoom), 1e-9)
        self._collision_scale = self._raster_scale if static_profile else 1.0
        defer_to_parent = getattr(self, "_defer_names", False)
        if not defer_to_parent:
            self._occupancy = _Occupancy()
            self._icon_occupancy = _Occupancy()
            self._pending_names = []
        self._defer_names = True
        # 放大倍数（相对 4096 全图基准），图标/簇网格随特写等比放大
        self.render_scale = ctx.zoom

        transit_only = self.transit_only
        if transit_only:
            try:
                transit_scale = float(cfg.get("transit_view_scale", 1.15))
            except (TypeError, ValueError):
                transit_scale = 1.15
            # 公共交通视图相对常规地图至少略大一档，同时限制异常主题值。
            self._transit_view_scale = max(1.05, min(transit_scale, 2.0))
        else:
            self._transit_view_scale = 1.0

        size_zoom = ctx.final_zoom if static_profile else self.render_scale
        raster_scale = self._raster_scale if static_profile else 1.0
        icon_size_value = (_resolve_size(cfg.get("icon_size", 22), size_zoom)
                           * self._transit_view_scale)
        icon_max_size = cfg.get("icon_max_size")
        if isinstance(icon_max_size, (int, float)):
            icon_size_value = min(icon_size_value,
                                  float(icon_max_size) * self._transit_view_scale)
        self._logical_icon_size = icon_size_value if static_profile else None
        icon_size = max(int(round(icon_size_value * raster_scale)), 1)
        bg_circle = cfg.get("bg_circle", True)
        bg_circle_size_value = (_resolve_size(cfg.get("bg_circle_size", 30), size_zoom)
                                * self._transit_view_scale)
        self._logical_bg_circle_size = bg_circle_size_value if static_profile else None
        bg_circle_size = max(int(round(bg_circle_size_value * raster_scale)), 1)
        min_priority = int(cfg.get("min_priority", 15))
        if static_profile:
            min_priority = 0
        elif png_policy:
            min_priority = 35 if png_policy["mode"] == "major" else 0
        elif not self.force_detail and policy.poi_min_priority is not None:
            min_priority = max(min_priority, policy.poi_min_priority)
        show_categories = set(cfg.get("categories", []))

        # 站台圆点避让（公共交通图叠加层）：交通站点 POI 图标让出站台圆点——
        # "让位置/偏移距离"，而非靠图层顺序压住。仅公共交通图启用（transit_only
        # 为 False 时 _station_avoid 恒为 False），常规渲染 POI 不受影响。
        # 方向：与该站点所在线路线条垂直（切线法向）；距离：默认按
        # 图标半宽+圆点半径+间隙随 zoom 等比自适应，可用 station_avoid_dist 覆盖。
        self._station_avoid = transit_only and bool(cfg.get("station_avoid", True))
        _avd = cfg.get("station_avoid_dist")
        if isinstance(_avd, (int, float)) or isinstance(_avd, dict):
            self._station_avoid_dist = (
                _resolve_size(_avd, size_zoom) * raster_scale)
        else:
            self._station_avoid_dist = None  # None = 自适应（随 zoom 等比）

        # 从统一世界坐标目录适配全部候选对象，源识别在目录阶段完成。
        pois = self._collect_pois(mapdata, min_priority, show_categories)
        # 空白图标表示明确不绘制，聚类前移除以免抑制邻近符号。
        pois = [poi for poi in pois if poi.poi_group == "area"
                or not self.icon_map.is_hidden(poi.icon_id)]
        if not pois:
            self._defer_names = defer_to_parent
            return

        # 实体身份判定与屏幕密度判定分开进行。
        icon_candidates = [poi for poi in pois if not self.icon_map.is_hidden(poi.icon_id)]
        selected = self._visual_cluster(icon_candidates, cfg)

        # 3. 加载精灵图
        self._load_sprite(cfg)

        # 4. 渲染图标（按优先级从低到高，保证高优先级在上层）
        if png_policy or static_profile:
            selected.sort(key=lambda p: (bool(p.parent_poi_id),
                                        p.name_source != "custom", -p.priority,
                                        p.category, p.px, p.py, p.label))
        else:
            selected.sort(key=lambda p: (-p.priority, p.category, p.px, p.py, p.label))
        selected_ids = {poi.poi_id for poi in selected}
        for poi in selected:
            self._draw_icon(poi, icon_size, bg_circle, bg_circle_size)
        for poi in pois:
            if poi.poi_id not in selected_ids:
                self._queue_unpainted_name(poi, icon_size)
        if not defer_to_parent:
            for args in self._pending_names:
                self._draw_icon_name(*args)
        self._defer_names = defer_to_parent

    # ---- 初始化 ----

    def _visual_cluster(self, pois, cfg):
        png_policy = cfg.get("png_export")
        active_profile = _active_profile(cfg)
        target = active_profile.get("target")
        if active_profile.get("view") in {"normal", "transit", "metro"}:
            if not active_profile["density_enabled"]:
                return list(pois)
            grid, limit = poi_density_params(active_profile["density_tier"])
            aa = self.ctx.zoom / self.ctx.final_zoom
            return select_pois(pois, grid_size=grid, max_per_cell=limit,
                               origin=self.ctx.transform(0, 0), pixel_scale=aa)
        if target in {"normal_png", "transit_png"}:
            selected = filter_pois_by_distance(
                pois, active_profile,
                allow_different_station_names=bool(
                    active_profile.get("allow_different_station_names", False)),
            )
            aa = self.ctx.zoom / self.ctx.final_zoom
            width = self.ctx.canvas_width or self.ctx.image.width
            height = self.ctx.canvas_height or self.ctx.image.height
            output_scale = max(width, height) / aa / 4096.0
            # 跨输出尺寸与分块保持主题的 4K 显示配额；世界距离和名称筛选不改变身份。
            view_zoom = self.ctx.final_zoom / output_scale
            grid = _resolve_size(cfg.get("grid_size", 90), view_zoom) * output_scale
            limit = int(cfg.get("max_per_cell", 2))
            if self.sublayer == "transit":
                grid *= 0.5
                transit_cfg = self.ctx.style.get("poi_icons", {}).get("transit", {})
                limit = int(transit_cfg.get("max_per_cell", cfg.get("transit_max_per_cell", 3)))
            return select_pois(
                selected, grid_size=grid, max_per_cell=limit,
                origin=self.ctx.transform(0, 0), pixel_scale=aa,
            )
        if target == "preview":
            if not bool(active_profile.get("density_enabled", True)):
                return list(pois)
            grid, limit = poi_density_params(
                active_profile.get("density_tier", "standard"))
            final_zoom = float(self.ctx.final_zoom)
            aa = self.ctx.zoom / final_zoom if final_zoom > 0 else 1.0
            if self.sublayer == "transit":
                grid *= 0.5
                transit_cfg = self.ctx.style.get("poi_icons", {}).get("transit", {})
                limit = int(transit_cfg.get("max_per_cell", limit))
            if not self.force_detail:
                grid *= self.ctx.detail_policy.poi_grid_multiplier
                cap = self.ctx.detail_policy.poi_max_per_cell
                if cap is not None:
                    limit = min(limit, cap)
            return select_pois(
                pois, grid_size=grid, max_per_cell=limit,
                origin=self.ctx.transform(0, 0), pixel_scale=aa,
                # 预览密度统一作用于候选对象；设施子对象的可见性独立，但不免除同格配额。
            )
        if png_policy:
            if self.sublayer == "building" and not png_policy["merge_same_kind"]:
                return list(pois)
            final_zoom = float(self.ctx.final_zoom)
            aa = self.ctx.zoom / final_zoom if final_zoom > 0 else 1.0
            # 使用完整输出画布，而非当前分块或抗锯齿尺寸；仅提高分辨率不应增加 POI 数量。
            width = getattr(self.ctx, "canvas_width", 0) or self.ctx.image.width
            height = getattr(self.ctx, "canvas_height", 0) or self.ctx.image.height
            layout_scale = max(width, height) / aa / 4096.0
            major = png_policy["mode"] == "major"
            grid = (110.0 if major else 70.0) * layout_scale
            if self.sublayer == "transit":
                grid *= 0.5
            return select_pois(
                pois, grid_size=grid, max_per_cell=2 if major else 4,
                origin=self.ctx.transform(0, 0), pixel_scale=aa,
            )
        owner_key = "transit_merge_enabled" if self.sublayer == "transit" else "building_merge_enabled"
        if not cfg.get("visual_merge_enabled", cfg.get(owner_key, cfg.get("merge_enabled", True))):
            return list(pois)
        final_zoom = float(self.ctx.final_zoom)
        aa = self.ctx.zoom / final_zoom if final_zoom > 0 else 1.0
        grid = _resolve_size(cfg.get("grid_size", 90), final_zoom)
        limit = int(cfg.get("max_per_cell", 2))
        if self.sublayer == "transit":
            grid *= 0.5
            transit_cfg = self.ctx.style.get("poi_icons", {}).get("transit", {})
            limit = int(transit_cfg.get("max_per_cell", cfg.get("transit_max_per_cell", 3)))
        if not self.force_detail:
            grid *= self.ctx.detail_policy.poi_grid_multiplier
            cap = self.ctx.detail_policy.poi_max_per_cell
            if cap is not None:
                limit = min(limit, cap)
        origin = self.ctx.transform(0, 0)
        unit = self.ctx.transform(1, 0)
        ppm = math.hypot(unit[0]-origin[0], unit[1]-origin[1]) / aa
        distance_key = "hub_core_m" if self.sublayer == "transit" else "block_gap_m"
        distance = float(cfg.get("visual_merge_distance", cfg.get(distance_key, cfg.get("merge_distance", 0))))
        return select_pois(pois, grid_size=grid, max_per_cell=limit,
                           origin=origin, pixel_scale=aa, distance_px=max(0, distance)*ppm,
                           pixels_per_world=ppm,
                           overrides=cfg.get("visual_merge_overrides", cfg.get("merge_overrides", {})))

    @staticmethod
    def _resolve_icons_dir(icons_dir) -> str:
        """所有主题共用 CSLModernMap 素材库。"""
        return os.path.normpath(str(icons_library_dir("cn_icons")))

    def _init_icon_tools(self, cfg: dict) -> None:
        from poi_icon_utils import POIIconMap
        icons_dir = self._resolve_icons_dir(cfg.get("icons_dir"))
        self._icons_dir = icons_dir
        json_path = os.path.join(icons_dir, "icons_map.json")
        style = getattr(getattr(self, "ctx", None), "style", None)
        mapping_path = (style.get("_poi_mapping_override") if style is not None
                        else cfg.get("_poi_mapping_override"))
        self.icon_map = POIIconMap(json_path, mapping_path=mapping_path,
                                   appearance=cfg.get("appearance", "light"))
        self._sprite_img = None
        self._icon_cache.clear()
        self._icon_sources.clear()
        self._color_cache.clear()

    def _load_sprite(self, cfg: dict) -> None:
        if self._sprite_img is not None:
            return
        self._sprite_img = self.icon_map.load_sprite(cfg.get("sprite"))

    # ---- POI 收集 ----

    def _collect_pois(
        self,
        mapdata: MapData,
        min_priority: int,
        show_categories: set,
        buildings: list | None = None,
    ) -> List[POIItem]:
        """从统一世界坐标 POI 目录收集全部候选对象。"""
        del buildings
        return self._service_poi_items(
            mapdata, getattr(self, "_poi_cfg", {}),
            min_priority, show_categories,
        )

    # ---- 绘制 ----

    def _draw_icon(
        self,
        poi: POIItem,
        icon_size: int,
        bg_circle: bool,
        bg_circle_size: int,
    ) -> None:
        # 按实际图片宽度缩放，两种存储模式使用同一显示尺寸。
        cache_key = (poi.icon_id, icon_size)
        if cache_key not in self._icon_cache:
            try:
                source_img = self.icon_map.render_icon(
                    poi.icon_id, sprite_image=self._sprite_img)
            except FileNotFoundError:
                if poi.poi_group != "area":
                    raise
                LOGGER.warning("Missing area icon asset: %s", poi.icon_id)
                source_img = None
            if source_img is None:
                LOGGER.warning("Missing POI icon asset: %s", poi.icon_id)
                self._icon_cache[cache_key] = None
                self._queue_unpainted_name(poi, icon_size)
                return
            self._icon_sources[poi.icon_id] = source_img
            scale = icon_size / source_img.width
            icon_img = source_img.resize((max(1, int(source_img.width * scale)),
                                         max(1, int(source_img.height * scale))), Image.LANCZOS)
            self._icon_cache[cache_key] = icon_img

        icon_img = self._icon_cache[cache_key]
        if icon_img is None:
            self._queue_unpainted_name(poi, icon_size)
            return
        source_img = self._icon_sources.get(poi.icon_id)
        bg_circle = bg_circle and not self.icon_map.get_icon(poi.icon_id).get("borderless", False)

        # 站台圆点避让（仅公共交通图叠加层）：沿线路垂直方向让出距离，
        # 露出站点圆点。距离默认按 图标半宽+圆点半径+间隙 随 zoom 等比自适应，
        # 可用 poi_icons.station_avoid_dist 覆盖；方向不可算时兜底斜上方。
        ox = oy = 0.0
        if self._station_avoid and poi.source == "transport":
            d = self._station_avoid_dist
            if d is None:
                st_cfg = (self.ctx.style.get("transports") or {}).get("station") or {}
                dot_r = _resolve_size(
                    st_cfg.get("size", {"fixed": 2.5, "scaled": 2.5}),
                    self.render_scale)
                gap = (1.5 * self._raster_scale if self._static_png_profile
                       else max(1.5, 1.5 * self.render_scale))
                d = icon_size / 2.0 + dot_r + gap
            if poi.avoid_dir is not None:
                ox = poi.avoid_dir[0] * d
                oy = poi.avoid_dir[1] * d
            else:
                ox = d * 0.70710678
                oy = -d * 0.70710678

        x = int(round(poi.px + ox - icon_img.width / 2))
        y = int(round(poi.py + oy - icon_img.height / 2))
        visible = icon_img.getbbox() or (0, 0, icon_img.width, icon_img.height)
        png_policy = self._poi_cfg.get("png_export")
        png_output = bool(png_policy) or bool(
            getattr(self, "_static_png_profile", False))
        pad = (2 * self._raster_scale if self._static_png_profile
               else 2 * self.ctx.zoom if png_output else 2)
        box = (x + visible[0] - pad, y + visible[1] - pad,
               x + visible[2] + pad, y + visible[3] + pad)
        icon_occupancy = getattr(self, "_icon_occupancy", None)
        if png_output and bg_circle:
            radius = bg_circle_size / 2
            box = (min(box[0], poi.px + ox - radius - pad),
                   min(box[1], poi.py + oy - radius - pad),
                   max(box[2], poi.px + ox + radius + pad),
                   max(box[3], poi.py + oy + radius + pad))
        static_layout_box = None
        if (getattr(self, "_static_png_profile", False)
                and source_img is not None):
            scale = max(float(self._raster_scale), 1e-9)
            base_icon = max(float(getattr(self, "_logical_icon_size", icon_size)), 0.0)
            source_factor = base_icon / max(float(source_img.width), 1.0)
            logical_width = float(source_img.width) * source_factor
            logical_height = float(source_img.height) * source_factor
            anchor_x = (poi.px + ox) / scale
            anchor_y = (poi.py + oy) / scale
            logical_x = anchor_x - logical_width / 2.0
            logical_y = anchor_y - logical_height / 2.0
            source_visible = source_img.getbbox() or (
                0, 0, source_img.width, source_img.height)
            static_layout_box = (
                logical_x + source_visible[0] * source_factor - 2.0,
                logical_y + source_visible[1] * source_factor - 2.0,
                logical_x + source_visible[2] * source_factor + 2.0,
                logical_y + source_visible[3] * source_factor + 2.0,
            )
            if bg_circle:
                radius = float(getattr(self, "_logical_bg_circle_size", bg_circle_size)) / 2.0
                static_layout_box = (
                    min(static_layout_box[0], anchor_x - radius - 2.0),
                    min(static_layout_box[1], anchor_y - radius - 2.0),
                    max(static_layout_box[2], anchor_x + radius + 2.0),
                    max(static_layout_box[3], anchor_y + radius + 2.0),
                )
            # 标注从完整符号边界外开始，包括可选背景圆和抗锯齿留白。
            static_name_box = (
                static_layout_box[0],
                static_layout_box[1],
                static_layout_box[2] - static_layout_box[0],
                static_layout_box[3] - static_layout_box[1],
            )
        else:
            static_name_box = None
        if getattr(self, "_active_profile", {}).get("target") in {
                "preview", "normal_png", "transit_png"}:
            declutter = bool(getattr(self, "_overlap_avoidance", True))
        else:
            declutter = (bool(png_policy) or (
                self._poi_cfg.get("declutter", True)
                and poi.category in {"residential", "commercial", "office", "industry"}
                and poi.name_source == "fallback"))
        collision_box = (static_layout_box if static_layout_box is not None
                         else self._collision_box(box))
        if (declutter and icon_occupancy is not None
                and icon_occupancy.overlaps(collision_box)):
            self._queue_unpainted_name(poi, icon_size)
            return
        if declutter and icon_occupancy is not None:
            icon_occupancy.add(collision_box)

        # 白底圆点
        if bg_circle:
            from PIL import ImageDraw
            # 预生成圆点模板缓存
            circle_key = ("bg_circle", bg_circle_size, poi.priority)
            if circle_key not in self._icon_cache:
                size = bg_circle_size * 2  # 2x 超采样
                circle = Image.new("RGBA", (size, size), (0, 0, 0, 0))
                d = ImageDraw.Draw(circle)
                # 白底 + 浅灰边
                d.ellipse(
                    (0, 0, size - 1, size - 1),
                    fill=(255, 255, 255, 240),
                    outline=(200, 200, 200, 180),
                    width=max(1, size // 32),
                )
                circle = circle.resize(
                    (bg_circle_size, bg_circle_size), Image.LANCZOS
                )
                self._icon_cache[circle_key] = circle

            circle = self._icon_cache[circle_key]
            cx = int(round(poi.px + ox - circle.width / 2))
            cy = int(round(poi.py + oy - circle.height / 2))
            self.ctx.image.paste(circle, (cx, cy), circle)

        self.ctx.image.paste(icon_img, (x, y), icon_img)
        if declutter and hasattr(self, "_occupancy"):
            self._occupancy.add(collision_box)

        # 名称放在完整符号边界外，避免候选位置与自己的图标碰撞而丢失。
        if poi.draw_name and poi.label:
            args = (poi, icon_size, box[0], box[1],
                    box[2] - box[0], box[3] - box[1], 0.0, 0.0,
                    static_name_box)
            if getattr(self, "_defer_names", False):
                self._pending_names.append(args)
            else:
                self._draw_icon_name(*args)

    def _queue_unpainted_name(self, poi: POIItem, icon_size: int) -> None:
        if not (poi.draw_name and poi.label
                and (poi.poi_group == "area" or poi.name_source == "custom")):
            return
        args = (poi, icon_size, poi.px, poi.py, 0, 0, 0.0, 0.0, None)
        if getattr(self, "_defer_names", False):
            self._pending_names.append(args)
        else:
            self._draw_icon_name(*args)

    def _collision_box(self, box):
        scale = float(getattr(self, "_collision_scale", 1.0) or 1.0)
        if scale <= 0 or abs(scale - 1.0) < 1e-9:
            return box
        return tuple(float(value) / scale for value in box)

    # ---- 名字绘制 ----

    def _icon_color(self, icon_id: str) -> str:
        """计算图标主色（排除近白底/透明），返回 #RRGGBB。"""
        if icon_id in self._color_cache:
            return self._color_cache[icon_id]
        try:
            raw = self.icon_map.crop_icon(icon_id, sprite_image=self._sprite_img)
        except FileNotFoundError:
            LOGGER.warning("Missing POI icon asset: %s", icon_id)
            raw = None
        if raw is None:
            self._color_cache[icon_id] = "#505564"
            return self._color_cache[icon_id]
        rgb = dominant_icon_rgb(raw)
        # 文字可读性：过亮则压深
        lum = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
        if lum > 200:
            rgb = tuple(max(0, int(v * 0.7)) for v in rgb)
        color = "#%02X%02X%02X" % rgb
        self._color_cache[icon_id] = color
        return color

    def _get_font(self, size: float, text: str = "") -> ImageFont.FreeTypeFont:
        cfg = getattr(self, "_poi_cfg", {})
        path = resolve_text_font(cfg.get("font"), text=text)
        key = (round(size, 1), str(path or ""))
        if key not in self._font_cache:
            size_px = max(int(round(size)), 6)
            try:
                self._font_cache[key] = (ImageFont.truetype(str(path), size_px)
                                         if path else load_pillow_font(size_px))
            except OSError:
                self._font_cache[key] = load_pillow_font(size_px)
        return self._font_cache[key]

    def _draw_icon_name(self, poi: POIItem, icon_size: int,
                        ix: int, iy: int, iw: int, ih: int,
                        tox: float = 0.0, toy: float = 0.0,
                        layout_box=None) -> None:
        """在图标右侧绘制 POI 名字（白色描边，颜色取图标主色）。

        tox/toy：文字自身的额外偏移；ix/iy 已包含图标的站点避让位移。

        输出旋转时：锚点位置已随地图 transform 旋转，文字保持水平正立可读，
        始终贴合屏幕阅读方向（图标居中、名字在右侧，均不旋转）。
        """
        cfg = getattr(self, "_poi_cfg", self.ctx.style.get("poi_icons", {}))
        has_snapshot = isinstance(cfg.get("visibility_snapshot"), dict)
        if not has_snapshot and not cfg.get("show_name", True):
            return
        static_layout = bool(getattr(self, "_static_png_profile", False))
        scale = max(float(self._raster_scale), 1e-9)
        size_zoom = self.ctx.final_zoom if static_layout else self.render_scale
        if self.transit_only and "transit_name_size" in cfg:
            size = max(6, _resolve_size(cfg["transit_name_size"], size_zoom))
        else:
            size = _resolve_size(cfg.get("name_size", {"fixed": 11, "scaled": 2}),
                                 size_zoom) * self._transit_view_scale
            max_size = cfg.get("name_max_size")
            if isinstance(max_size, (int, float)):
                size = min(size, float(max_size) * self._transit_view_scale)
        if size < 6:
            return
        offset = (_resolve_size(cfg.get("name_offset", 3), size_zoom)
                  * self._transit_view_scale)
        font = self._get_font(size, poi.label)
        # 描边颜色与宽度比例从主题读：预览侧 halos['poi'] 用同一组键判断要不要画
        # 描边（preview_scene._poi_name_halo），两端必须同源。缺省保持内置的
        # 「白色描边 + 字号 × 0.12」，即本层原来的硬编码行为。
        stroke_hex = str(cfg.get("name_stroke", "#FFFFFF") or "")
        stroke_ratio = float(cfg.get("name_stroke_ratio", 0.12) or 0.0)
        stroke = (max(int(round(size * stroke_ratio)), 1)
                  if stroke_hex and stroke_ratio > 0 else 0)
        fill = hex_to_rgb(self._icon_color(poi.icon_id)) + (255,)
        stroke_rgb = (hex_to_rgb(stroke_hex) + (255,)) if stroke_hex else fill

        probe = ImageDraw.Draw(Image.new("RGBA", (4, 4), (0, 0, 0, 0)))
        bbox = probe.textbbox((0, 0), poi.label, font=font, stroke_width=stroke)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if tw <= 0 or th <= 0:
            return
        pad = stroke + 4
        logical_width = tw + pad * 2
        logical_height = th + pad * 2
        if static_layout:
            # 按最终像素测量，只在字形抗锯齿时进行超采样。
            raster_font = self._get_font(size * scale, poi.label)
            raster_stroke = (
                max(int(round(stroke * scale)), 1) if stroke > 0 else 0)
            raster_bbox = probe.textbbox(
                (0, 0), poi.label, font=raster_font,
                stroke_width=raster_stroke)
            raster_pad = max(1, int(round(pad * scale)))
            raster_size = (
                max(1, int(round(logical_width * scale))),
                max(1, int(round(logical_height * scale))),
            )
            img = Image.new("RGBA", raster_size, (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            d.text((raster_pad - raster_bbox[0],
                    raster_pad - raster_bbox[1]), poi.label,
                   font=raster_font, fill=fill,
                   stroke_width=raster_stroke, stroke_fill=stroke_rgb)
        else:
            img = Image.new("RGBA", (logical_width, logical_height),
                            (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            d.text((pad - bbox[0], pad - bbox[1]), poi.label, font=font,
                   fill=fill, stroke_width=stroke, stroke_fill=stroke_rgb)
        # 文字垂直居中于图标，水平位于图标右侧（始终水平正立）
        if static_layout:
            if layout_box is None:
                ix, iy, iw, ih = ix / scale, iy / scale, iw / scale, ih / scale
            else:
                ix, iy, iw, ih = layout_box
            cx = ix + iw + offset + tox
            ty = iy + ih / 2 + toy
            x = int(round(cx))
            y = int(round(ty - logical_height / 2))
        else:
            cx = ix + iw + offset + tox
            ty = iy + ih / 2 + toy
            x = int(round(cx))
            y = int(round(ty - img.height / 2))
        occupancy = getattr(self, "_occupancy", None)
        candidates = [(x, y), (int(round(ix - offset - logical_width)), y),
                      (int(round(ix + iw / 2 - logical_width / 2)),
                       int(round(iy - offset - logical_height))),
                      (int(round(ix + iw / 2 - logical_width / 2)),
                       int(round(iy + ih + offset)))]
        profile_layout = getattr(self, "_active_profile", {}).get("target") in {
            "preview", "normal_png", "transit_png"}
        avoid = (bool(getattr(self, "_overlap_avoidance", True))
                 if profile_layout else True)
        for x, y in candidates:
            box = (x, y, x + logical_width, y + logical_height)
            collision_box = box if static_layout else self._collision_box(box)
            if avoid and occupancy is not None and occupancy.overlaps(collision_box):
                continue
            if avoid and occupancy is not None:
                occupancy.add(collision_box)
            paste_x = int(round(x * scale)) if static_layout else x
            paste_y = int(round(y * scale)) if static_layout else y
            self.ctx.image.paste(img, (paste_x, paste_y), img)
            self.ctx.shared.setdefault("painted_poi_name_groups", set()).add(poi.group_id)
            break


class BuildingPOILayer(POIIconLayer):
    """返回非交通地点，显示由 poi_icons.building 控制。"""
    name = "poi_building"

    def __init__(self, **kwargs):
        super().__init__(sublayer="building", **kwargs)


class TransitPOILayer(POIIconLayer):
    """返回所有站点和交通设施，不以源对象类型筛选。"""
    name = "poi_transit"

    def __init__(self, **kwargs):
        super().__init__(sublayer="transit", **kwargs)
