"""交通图层：把 CSLModernMap 导出的交通快照按车道渲染到地图上。

渲染逻辑（消费 schema v1.1，分析软件直接可用）：
1. 每条车道 = 一条与道路中心线平行的色带：
   - 中心线 = nodeA → 贝塞尔中间点(0.25/0.5/0.75) → nodeB（世界坐标米）
   - 车道中心线 = 中心线沿右法线横向平移 lane.position 米
   - 色带宽度 = lane.width 米（换算为像素 = width × scale）
2. 颜色由 lane.congestion 决定：
   - Vehicle 且 dataQuality==0：绿(0) → 黄(0.3) → 橙(0.6) → 红(0.85) → 深红(1.0)
   - 非机动车道 / dataQuality==1 / congestion<0：浅灰（不参与拥堵色）
3. 先画整段浅灰"路面轮廓"（所有车道），再叠加拥堵色带，
   保证独立渲染（无底图）时道路结构依然可见。

与道路图层一致使用 aggdraw 抗锯齿矢量渲染。
"""
from __future__ import annotations

from ..style import hex_to_rgb

import math

import aggdraw

from ..road_hierarchy import (
    RoadLevel,
    classify_traffic_segment,
    visible_at_abstract_level,
)
from ..spatial import intersects, traffic_bbox
from .base import Layer, RenderContext

# 默认拥堵色阶：(congestion, #RRGGBB)。可在 style["traffic"]["ramp"] 覆盖。
DEFAULT_TRAFFIC_RAMP = [
    (0.00, "#2AA96D"),  # 畅通 绿
    (0.30, "#F2C14E"),  # 轻度 黄
    (0.60, "#EF733C"),  # 中度 橙
    (0.85, "#C1322B"),  # 重度 红
    (1.00, "#921519"),  # 堵死 深红
]
# 非机动车道 / 无数据车道底色
DEFAULT_TRAFFIC_NO_DATA = "#E9EBF0"
# 车道色带宽度缩放：1.0=占满整条车道，越小线越细（默认 0.5，露出灰色路面底）
DEFAULT_TRAFFIC_WIDTH_SCALE = 0.5
# 色带最细下限（像素）
DEFAULT_TRAFFIC_MIN_LANE_PX = 0.5


def _lerp(a, b, t):
    return a + (b - a) * t


def congestion_color(c: float, ramp: list | None = None) -> str:
    """按拥堵值返回色带颜色（线性插值，c 越靠近 1 越红）。"""
    stops = ramp or DEFAULT_TRAFFIC_RAMP
    if not stops:
        return "#C1322B"
    c = max(0.0, min(1.0, c))
    if c <= stops[0][0]:
        return stops[0][1]
    if c >= stops[-1][0]:
        return stops[-1][1]
    for (t0, h0), (t1, h1) in zip(stops, stops[1:]):
        if t0 <= c <= t1:
            r0, g0, b0 = hex_to_rgb(h0)
            r1, g1, b1 = hex_to_rgb(h1)
            t = 0.0 if t1 == t0 else (c - t0) / (t1 - t0)
            return "#%02X%02X%02X" % (
                int(round(_lerp(r0, r1, t))),
                int(round(_lerp(g0, g1, t))),
                int(round(_lerp(b0, b1, t))),
            )
    return stops[-1][1]


def parse_traffic_ramp(raw) -> list | None:
    """解析 style 配置里的 ramp（列表 [(阈值, 色)] 或 [{value,color}]）。"""
    if not isinstance(raw, list) or not raw:
        return None
    parsed = []
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            parsed.append((float(item[0]), str(item[1])))
        elif isinstance(item, dict):
            parsed.append(
                (float(item.get("value", 0.0)), str(item.get("color", "#C1322B")))
            )
    return sorted(parsed, key=lambda kv: kv[0]) if parsed else None


def resolve_traffic_style(raw) -> dict:
    """返回 PNG 和预览共享的交通绘制参数。"""
    cfg = raw if isinstance(raw, dict) else {}
    return {
        "ramp": parse_traffic_ramp(cfg.get("ramp")) or list(DEFAULT_TRAFFIC_RAMP),
        "no_data_color": cfg.get("no_data_color", DEFAULT_TRAFFIC_NO_DATA),
        "width_scale": float(cfg.get(
            "width_scale", DEFAULT_TRAFFIC_WIDTH_SCALE)),
        "min_lane_px": max(float(cfg.get(
            "min_lane_px", DEFAULT_TRAFFIC_MIN_LANE_PX)), 0.5),
    }


def _round_cap_and_join(agg, coords, color, width: float) -> None:
    """用填充圆补齐 aggdraw 折线的端点和内部拐角。"""
    if len(coords) < 4:
        return
    radius = max(width / 2.0, 0.25)
    brush = aggdraw.Brush(color)
    for index in range(2, len(coords) - 2, 2):
        x, y = coords[index], coords[index + 1]
        agg.ellipse(
            (x - radius, y - radius, x + radius, y + radius), brush)
    for index in (0, len(coords) - 2):
        x, y = coords[index], coords[index + 1]
        agg.ellipse(
            (x - radius, y - radius, x + radius, y + radius), brush)


class TrafficLayer(Layer):
    """交通拥堵图层（按行驶方向渲染色带）。

    渲染粒度 = 路段 × 行驶方向（Forward / Backward）：
    - 同一路段、同一方向的所有机动车道合并为一条色带
      · 中心位置 = 该方向车道 position 的宽度加权平均
      · 色带宽度 = 该方向车道宽度之和
      · 颜色     = 该方向有效车道 congestion 的宽度加权平均
    - 路面轮廓画整段浅灰带（独立渲染无底图时道路结构可见）
    - 非机动车道 / 无 TMPE 统计的车道不参与方向色带（仍显示在浅灰路面内）
    """

    name = "traffic"
    late = False

    def supports(self, document) -> bool:
        return bool(getattr(document, "traffic", None))

    def render(self, ctx: RenderContext, document) -> None:
        """按有效交通快照绘制道路流量和方向信息。"""
        if ctx.session is not None:
            snap = ctx.session.traffic_snapshot()
        else:
            from ..traffic import ensure_snapshot
            snap = ensure_snapshot(getattr(document, "traffic", None))
        if not snap or not snap.segments:
            return
        scale = ctx.transform.scale  # 像素/米
        cfg = ctx.style.get("traffic", {}) if ctx.style else {}
        paint = resolve_traffic_style(cfg)
        ramp = paint["ramp"]
        no_data_color = paint["no_data_color"]
        width_scale = paint["width_scale"]
        min_w = paint["min_lane_px"]

        agg = aggdraw.Draw(ctx.image)
        agg.setantialias(True)

        level = ctx.detail_policy.abstract_level
        has_highways = any(
            classify_traffic_segment(seg) is RoadLevel.HIGHWAY
            for seg in snap.segments
        )

        # 瓦片裁剪：只保留与当前渲染区域（含外扩）相交的交通路段。
        wx0, wz0, wx1, wz1 = (ctx.world_x0, ctx.world_z0,
                              ctx.world_x1, ctx.world_z1)
        tsegs = [
            s for s in snap.segments
            if visible_at_abstract_level(
                classify_traffic_segment(s), level,
                has_highways=has_highways,
            )
            and intersects(traffic_bbox(s), wx0, wz0, wx1, wz1)
        ]

        footprints = []
        direction_bands = []
        for seg in tsegs:
            footprint, bands = self._direction_bands(
                seg, scale, ctx.transform)
            footprints.append(footprint)
            direction_bands.extend(bands)

        no_data_rgb = hex_to_rgb(no_data_color)
        for coords, width_m in footprints:
            width_px = max(width_m * scale, 0.0)
            if width_px <= 0:
                continue
            pen_width = max(width_px, min_w)
            agg.line(coords, aggdraw.Pen(no_data_color, pen_width))
            _round_cap_and_join(agg, coords, no_data_rgb, pen_width)

        for coords, _center_m, width_m, congestion in direction_bands:
            width_px = max(width_m * scale * width_scale, min_w)
            color = congestion_color(congestion, ramp)
            agg.line(coords, aggdraw.Pen(color, width_px))
            _round_cap_and_join(
                agg, coords, hex_to_rgb(color), width_px)
        agg.flush()

    def _direction_bands(self, seg, scale, transform):
        """返回 (路面轮廓(coords, 带宽米), 方向色带列表)。

        方向色带元素：(coords, 中心偏移米, 带宽米, congestion)。
        """
        wpts = seg.world_points()
        if len(wpts) < 2:
            return ((), 0.0), []
        norms = self._normals(wpts)

        def line_at(offset: float):
            px = []
            for p, n in zip(wpts, norms):
                qx = p.x + offset * n[0]
                qz = p.z + offset * n[1]
                x, y = transform(qx, qz)
                px.append((x, y))
            return tuple(v for xy in px for v in xy)

        # 路面轮廓：整段宽度（含人行道等非机动车道）
        road_w = seg.width if seg.width > 0 else sum(
            l.width for l in seg.lanes
        )
        foot = (line_at(0.0), road_w)

        # 与网页快照渲染器共享有效性及方向判断规则。
        groups = seg.directional_lanes()
        if not groups:
            congestion = seg.seg_stats.weighted_avg_congestion
            if math.isfinite(congestion) and 0 <= congestion <= 1 and road_w > 0:
                return foot, [(line_at(0.0), 0.0, road_w, congestion)]
            return foot, []

        bands = []
        for _d, lanes in groups.items():
            total_w = sum(l.width for l in lanes)
            if total_w <= 0:
                continue
            # 该方向车道组的中心（宽度加权平均 position）
            center = seg.direction_offset(_d, lanes)
            # 该方向有效拥堵车道（Vehicle + dataQuality==0 + congestion>=0）
            valid = [l for l in lanes if l.has_congestion]
            if not valid:
                continue
            cong = sum(l.congestion * l.width for l in valid) / sum(
                l.width for l in valid
            )
            bands.append((line_at(center), center, total_w, cong))
        return foot, bands

    @staticmethod
    def _normals(wpts):
        """逐点计算中心线的右法线 (-dz, dx)（世界 xz 平面，正=前进方向右侧）。"""
        n = len(wpts)
        norms = []
        for i in range(n):
            if i == 0:
                d = (wpts[1].x - wpts[0].x, wpts[1].z - wpts[0].z)
            elif i == n - 1:
                d = (wpts[i].x - wpts[i - 1].x, wpts[i].z - wpts[i - 1].z)
            else:
                d = (
                    wpts[i + 1].x - wpts[i - 1].x,
                    wpts[i + 1].z - wpts[i - 1].z,
                )
            length = math.hypot(d[0], d[1])
            if length < 1e-6:
                norms.append((0.0, 1.0))
            else:
                norms.append((-d[1] / length, d[0] / length))
        return norms
