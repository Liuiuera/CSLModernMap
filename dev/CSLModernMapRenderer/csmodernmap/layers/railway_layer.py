"""铁路/轨道图层：渲染火车铁路、有轨电车、地铁轨道、缆车、单轨。

对应 SPEC F102（铁路渲染），复刻 CSLMapView RailWayBuilder：
- 分类：train（WayType.Rail）、tram、metro、monorail、cablecar
- 轨道统一样式：主线（实线）+ 白色虚线 overlay（火车/电车/地铁），
  不区分地面/高架/隧道（用户指定统一渲染）
- 连续相位虚线：先把端点相连的轨道段贪心合并成链，再沿整条链
  按弧长连续交替画虚线（复刻 CSLMapView GroupDirectRailway 合并 +
  WPF DashStyle 连续相位行为），避免段间相位错位出现黑段不均/纯白段
- 虚线间隔按「笔宽倍数」计算（WPF DashStyle 语义：dash 值 = 笔宽倍数）
- 缆车/单轨：实线 + 沿线等距装饰横线
- 站台（段名含 Station/Stop）：画加宽粗线，不参与链合并
- 绘制顺序（自底向上）：metro → tram → cablecar → monorail → rail
  （与 CSLMapView BuildRailShape 一致）
"""
from __future__ import annotations

import math

import aggdraw

from ..cmm.model import NetworkKind
from ..spatial import intersects
from .base import Layer, RenderContext

# 轨道类型 → CMM NetworkKind（绘制语义对应旧 SegmentType 轨道标志位）
_KIND_BY_NETWORK = {
    NetworkKind.RAIL: "train",
    NetworkKind.TRAM: "tram",
    NetworkKind.METRO: "metro",
    NetworkKind.MONORAIL: "monorail",
    NetworkKind.CABLECAR: "cablecar",
}
_KINDS = ("train", "tram", "metro", "monorail", "cablecar")
# 绘制顺序（自底向上，同 CSLMapView）
_DRAW_ORDER = ("metro", "tram", "cablecar", "monorail", "train")

# 抽象级别对应的最小轨道像素（最终输出像素），防止小图下发丝化
_ABS_MIN_PIX = (0.5, 0.6, 0.8, 1.0)  # 按 abstract_level 索引

# 链合并端点匹配容差（像素，round(x*2)/2 = 0.5 精度）
_CHAIN_TOL = 0.5


def _size_parts(value, default_fixed: float, default_scaled: float) -> dict:
    if value is None:
        return {"fixed": default_fixed, "scaled": default_scaled}
    if isinstance(value, dict):
        return {"fixed": float(value.get("fixed", default_fixed)),
                "scaled": float(value.get("scaled", default_scaled))}
    return {"fixed": float(value), "scaled": 0.0}


def _color_parts(value, default: str, inherited_alpha=None) -> tuple[str, float]:
    if isinstance(value, dict):
        color = str(value.get("color", default))
        alpha = value.get("alpha", inherited_alpha)
    else:
        color = str(value or default)
        alpha = inherited_alpha
    opacity = 1.0 if alpha is None else max(0.0, min(1.0, float(alpha) / 255.0))
    return color, opacity


def railway_kind_config(raw, kind: str) -> dict:
    """按 PNG 图层的规则精确解析铁路类型。"""
    root = raw if isinstance(raw, dict) else {}
    cfg = root.get(kind) or {}
    if not isinstance(cfg, dict):
        cfg = {}
    if not cfg and kind == "train":
        cfg = {"line": root.get("line", "#C36E69"),
               "dash": root.get("dash", "#FFFFFF")}
    return cfg


def resolve_railway_style(raw) -> dict:
    """返回 PNG 和预览共享的铁路分类绘制参数。"""
    result = {}
    for kind in _KINDS:
        cfg = railway_kind_config(raw, kind)
        base_color, base_opacity = _color_parts(
            cfg.get("line"), "#C36E69", cfg.get("alpha"))
        dash_color, dash_opacity = _color_parts(
            cfg.get("dash"), "#FFFFFF", cfg.get("alpha"))
        result[kind] = {
            "baseColor": base_color,
            "baseOpacity": base_opacity,
            "width": _size_parts(cfg.get("width"), 2.0, 1.2),
            "stationWidth": _size_parts(
                cfg.get("station_width") or cfg.get("width"), 4.5, 2.7),
            "dashEnabled": bool(cfg.get("dash")),
            "dashColor": dash_color,
            "dashOpacity": dash_opacity,
            "dashWidth": _size_parts(cfg.get("dash_width"), 0.8, 1.2),
            "dashPattern": [float(value) for value in
                            (cfg.get("pattern") or [2, 4])],
        }
    return {"types": result, "drawOrder": list(_DRAW_ORDER)}


def _hex_to_rgba(color: str, alpha: float | None = None):
    """解析 #RRGGBB/#RGB（可选 alpha 0-255）为 (r,g,b[,a])。"""
    c = str(color).strip().lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    r, g, b = (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))
    if alpha is None:
        return (r, g, b)
    return (r, g, b, int(alpha))


def _chain_bbox(pts_world) -> tuple:
    """世界坐标点列的 AABB (x0, z0, x1, z1)。"""
    xs = [p.x for p in pts_world]
    zs = [p.z for p in pts_world]
    return (min(xs), min(zs), max(xs), max(zs))


def _wkey(pt) -> tuple:
    """端点世界坐标量化（0.1 米容差内视为同一端点）。"""
    return (round(pt.x * 10) / 10, round(pt.z * 10) / 10)


class RailwayLayer(Layer):
    """铁路/轨道渲染器。"""

    name = "railway"

    def render(self, ctx: RenderContext, document) -> None:
        if not document.networks:
            return
        self.ctx = ctx
        # 放大倍数（相对 4096 全图基准），线宽随特写等比放大
        self.render_scale = ctx.zoom
        # 抽象分级按最终输出尺寸
        self._lvl = ctx.detail_policy.abstract_level
        # 线宽下限换算到渲染分辨率（AA 渲染线宽/2=最终线宽）
        self._min_pix = _ABS_MIN_PIX[self._lvl] * (ctx.zoom / ctx.final_zoom)
        self.rail_cfg = ctx.style.get("railway", {})
        if self.rail_cfg.get("enabled", True) is False:
            return
        self._agg = aggdraw.Draw(ctx.image)

        # 0. 全图轨道链合并（世界坐标，缓存一次）：
        #    链合并必须在完整段集上做（跨瓦片相位连续），结果与区域无关。
        #    瓦片渲染按区域只绘制相交的完整链 → 避免每瓦片重复全量遍历
        #    与重复 O(N²) 链合并（"卡在道路下一层"），同时虚线相位不重置。
        chains = self._chains_world(ctx, document)
        wx0, wz0, wx1, wz1 = (ctx.world_x0, ctx.world_z0,
                              ctx.world_x1, ctx.world_z1)

        # 3. 按序绘制（不区分地面/高架/隧道，统一轨道样式）
        for kind in _DRAW_ORDER:
            items = chains.get(kind)
            if not items:
                continue
            cfg = self._cfg(kind)
            for pts_world, station in items:
                if not intersects(_chain_bbox(pts_world), wx0, wz0, wx1, wz1):
                    continue
                pts = [self.ctx.transform(p.x, p.z) for p in pts_world]
                if len(pts) < 2:
                    continue
                self._draw_chain(pts, cfg, kind, station=station)
        self._agg.flush()

    # ---- 全图链合并（世界坐标，会话级缓存） ----

    def _chains_world(self, ctx, document) -> dict:
        """全图轨道分类 + 端点链合并（世界坐标）。

        返回 {kind: [(pts_world, is_station), ...]}，pts_world 为世界坐标
        点列。结果与瓦片/区域无关，按 (文档会话, 抽象级) 缓存一次；
        分块渲染每瓦片复用，虚线相位沿整条链连续。
        """
        if ctx.session is not None:
            return ctx.session.service_cache.get_or_compute(
                "railway_chains", {"lvl": self._lvl},
                lambda: self._build_chains(document))
        cached = ctx.shared.get(("railway_chains", self._lvl))
        if cached is None:
            cached = self._build_chains(document)
            ctx.shared[("railway_chains", self._lvl)] = cached
        return cached

    def _build_chains(self, document) -> dict:
        # 1. 分类收集（世界坐标点列）
        buckets: dict = {k: [] for k in _KINDS}
        for network in document.networks:
            kind = _KIND_BY_NETWORK.get(network.kind)
            if kind is None:
                continue
            pts = list(network.geometry.points)
            if len(pts) < 2:
                start = document.node(network.start_id)
                end = document.node(network.end_id)
                pts = [n.pos for n in (start, end) if n is not None]
            if len(pts) < 2:
                continue
            buckets[kind].append((network, pts))

        # 2. 小图抽象：最低档只保留火车/地铁轨道
        if self._lvl >= 3:
            for k in ("tram", "cablecar", "monorail"):
                buckets[k] = []

        # 3. 站台段单独宽线；其余段端点贪心合并成链（世界坐标）
        result: dict = {k: [] for k in _KINDS}
        for kind in _DRAW_ORDER:
            items = buckets.get(kind)
            if not items:
                continue
            stations = [(network, pts) for network, pts in items
                        if self._is_station(network, kind)]
            rails = [(network, pts) for network, pts in items
                     if not self._is_station(network, kind)]
            for _network, pts in stations:
                result[kind].append((pts, True))
            for pts in self._merge_chains_world(rails):
                result[kind].append((pts, False))
        return result

    @staticmethod
    def _is_station(network, kind: str) -> bool:
        """站台段：缆车站段名含 Stop，其余含 Station（同 CSLMapView）。"""
        name = str(network.name or network.kind.value or "").lower()
        return ("stop" in name) if kind == "cablecar" else ("station" in name)

    # ---- 链合并（世界坐标） ----

    @staticmethod
    def _merge_chains_world(items) -> list:
        """端点贪心链合并（世界坐标点列）：端点（0.1m 容差）相连的轨道段
        合并为一条连续折线，使虚线相位能沿整条轨道连续推进。

        结果与渲染区域无关（可在全图一次合并后缓存），瓦片按区域只取
        相交链绘制，避免分块渲染重复 O(N²) 链合并且相位跨瓦片不重置。
        """
        if not items:
            return []
        segs = []
        for _network, pts in items:
            segs.append((_wkey(pts[0]), _wkey(pts[-1]), pts))
        remaining = list(range(len(segs)))
        chains: list = []
        while remaining:
            idx = remaining.pop(0)
            _, _, pts = segs[idx]
            chain_pts = list(pts)
            changed = True
            while changed:
                changed = False
                c_end = _wkey(chain_pts[-1])
                c_start = _wkey(chain_pts[0])
                for j in range(len(remaining) - 1, -1, -1):
                    s2, e2, p2 = segs[remaining[j]]
                    if s2 == c_end:      # 正向接尾
                        chain_pts.extend(p2[1:])
                    elif e2 == c_end:    # 反向接尾
                        chain_pts.extend(p2[-2::-1])
                    elif e2 == c_start:  # 反向接头
                        chain_pts = p2[:-1] + chain_pts
                    elif s2 == c_start:  # 正向接头
                        chain_pts = p2[::-1][:-1] + chain_pts
                    else:
                        continue
                    del remaining[j]
                    changed = True
                    break
            chains.append(chain_pts)
        return chains

    # ---- 样式辅助 ----

    def _cfg(self, kind: str) -> dict:
        return railway_kind_config(self.rail_cfg, kind)

    def _size(self, val, def_fixed: float, def_scaled: float) -> float:
        """取尺寸：数值 或 {fixed, scaled}，按 render_scale 线性放大。"""
        if val is None:
            v = def_fixed + def_scaled * self.render_scale
        elif isinstance(val, dict):
            v = float(val.get("fixed", def_fixed)) + float(
                val.get("scaled", def_scaled)
            ) * self.render_scale
        else:
            v = float(val)
        return max(v, self._min_pix)

    def _color(self, cfg: dict, key: str, default: str):
        val = cfg.get(key, default)
        if isinstance(val, dict):
            alpha = val.get("alpha")
            color = val.get("color", default)
        else:
            color = val
            alpha = cfg.get("alpha")
        if not isinstance(color, str):
            return color  # 已是 (r,g,b[,a]) 元组
        return _hex_to_rgba(color, alpha)

    # ---- 绘制 ----

    def _draw_chain(self, pts: list, cfg: dict, kind: str,
                    station: bool = False) -> None:
        coords = tuple(v for xy in pts for v in xy)
        # 主线（实线；站台用加宽粗线）
        if station:
            w = self._size(cfg.get("station_width") or cfg.get("width"), 4.5, 2.7)
        else:
            w = self._size(cfg.get("width"), 2.0, 1.2)
        self._agg.line(coords, aggdraw.Pen(self._color(cfg, "line", "#C36E69"), w))

        if station:
            return
        # 白色虚线 overlay（火车/电车/地铁）
        if cfg.get("dash"):
            dw = max(self._size(cfg.get("dash_width"), 0.8, 1.2), 0.5)
            pattern = cfg.get("pattern") or [2, 4]
            self._draw_dashed_chain(
                pts, self._color(cfg, "dash", "#FFFFFF"), dw,
                [float(d) for d in pattern],
            )
        # 装饰横线（缆车/单轨）
        if cfg.get("decoration_span"):
            self._draw_decorations(pts, cfg)

    def _draw_dashed_chain(self, pts: list, color, width: float, dash: list) -> None:
        """沿整条折线按弧长连续交替画虚线（相位跨段连续）。

        间隔 = dash 值 × 笔宽（WPF DashStyle 语义：dash 数组元素为笔宽倍数，
        与 CSLMapView RailDashPen.DashStyle[2,4] 一致），随缩放等比变化。
        """
        dash_len = max(dash[0] * width, 0.5)
        gap_len = max(dash[1] * width, 0.5)
        pen = aggdraw.Pen(color, width)
        on = True
        remain = dash_len
        px, py = pts[0]
        for i in range(len(pts) - 1):
            qx, qy = pts[i + 1]
            dx, dy = qx - px, qy - py
            dist = math.hypot(dx, dy)
            if dist < 1e-6:
                px, py = qx, qy
                continue
            ux, uy = dx / dist, dy / dist
            t = 0.0
            while t < dist - 1e-6:
                step = min(remain, dist - t)
                if on:
                    self._agg.line(
                        (px + ux * t, py + uy * t,
                         px + ux * (t + step), py + uy * (t + step)),
                        pen,
                    )
                t += step
                remain -= step
                if remain <= 1e-6:
                    on = not on
                    remain = dash_len if on else gap_len
            px, py = qx, qy

    def _draw_decorations(self, pts: list, cfg: dict) -> None:
        """沿折线每隔 span 像素画一条垂直横线（单轨/缆车装饰）。

        算法复刻 CSLMapView BuildCableCars/BuildMonorails：
        从线段起点按 span 步进采样，跨越线段时把剩余步长累积到下一段。

        注意（6.5.x 卡死修复）：旧版在"绘制"分支只重置 offset=span 而不消耗
        dist、不推进 idx，导致任何像素长度 >= span 的线段都在同一位置无限重绘
        （高德风格单轨/缆车卡死 30 分钟+）。改为在段内循环推进 offset，
        直到超过段长再扣减并前进到下一段。
        """
        span = self._size(cfg.get("decoration_span"), 2.0, 25.0)
        length = self._size(cfg.get("decoration_length"), 1.0, 1.0)
        w = max(self._size(cfg.get("decoration_width"), 0.6, 0.6), 0.3)
        pen = aggdraw.Pen(self._color(cfg, "line", "#C36E69"), w)
        if len(pts) < 2:
            return
        idx = 0
        offset = span / 2.0
        while idx < len(pts) - 1:
            ax, ay = pts[idx]
            bx, by = pts[idx + 1]
            dx, dy = bx - ax, by - ay
            dist = math.hypot(dx, dy)
            if dist < 1e-6:
                idx += 1
                continue
            ux, uy = dx / dist, dy / dist
            # 沿当前段按 span 步进绘制，直到剩余长度不足一个 span
            while offset <= dist:
                px, py = ax + ux * offset, ay + uy * offset
                # 垂直横线（法向 ± length）
                nx, ny = -uy, ux
                sx, sy = px + nx * length, py + ny * length
                ex, ey = px - nx * length, py - ny * length
                self._agg.line((sx, sy, ex, ey), pen)
                offset += span
            offset -= dist
            idx += 1
