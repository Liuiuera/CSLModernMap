"""交通线路图层：地铁线路用线路颜色细线标识 + 线路名徽章。

对应 SPEC F007（交通线路渲染）。

实现说明：
- v6.4 起线路沿真实轨道绘制：用导出数据里的地铁轨道段（SegmentType.metro）
  构建轨道邻接图，相邻站点间 BFS 寻路并取路段形状点，与灰色轨道完全重合；
  某段无通路时回退为站点直连（原示意图画法）。
- v6.4.2 地铁线路改为「站点停靠线段 Path 链」重建（CSLMapView 同源）：
  每个 Metro Line 停靠线段携带该站段实际行驶经过的轨道段有序链，
  站点→停靠线段→链 = 精确行驶路线（非 BFS 最短路径近似），且闭环遍历
  天然闭合环线（内环/外环）；链缺失的站段回退 BFS（MapData.metro_line_geometry）。
- 用 aggdraw（AGG 抗锯齿）画一条细线，颜色取线路 XML <color>。
- 不绘制铁路符号/枕木，仅一条细线标识。
- 线路名徽章：白底圆角矩形 + 线路色文字/边框，沿线每 repeat_interval
  米重复放置（参考高德地铁线路名样式）。
"""
from __future__ import annotations

from ..path_model import normalize_angle, move_towards

from ..style import hex_to_rgb

import math
import re
from dataclasses import replace
from datetime import datetime
from statistics import median

import aggdraw
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .. import transit
from .._paths import resource_dir
from ..cmm import GroupingParams, JobParams, StationMergeParams, TransitKind
from ..data import MapData, Point
from ..fonts import load_pillow_font
from ..services.transit.color import hex_palette
from ..schematic_water import draw_water_surface, layout_and_water
from ..services.transit import (
    build_transit_model,
    match_facilities,
    resolve_route_geometry,
)

# TransitKind → TYPE_REGISTRY key 映射（替代旧的 transit.normalize_type）
_TYPE_KIND_TO_KEY: dict[TransitKind, str] = {
    TransitKind.METRO: "Metro",
    TransitKind.TRAIN: "Train",
    TransitKind.MONORAIL: "Monorail",
    TransitKind.TRAM: "Tram",
    TransitKind.CABLECAR: "CableCar",
    TransitKind.BUS: "Bus",
    TransitKind.TROLLEYBUS: "Trolleybus",
    TransitKind.SHIP: "Ship",
    TransitKind.FERRY: "Ferry",
    TransitKind.AIRPLANE: "Airplane",
    TransitKind.HELICOPTER: "Helicopter",
}
from ..i18n import tr as i18n_tr
from ..schematic import edge_key
from ..path_model import OpenPath, from_point_list
from .base import Layer, RenderContext

# 中文字体候选（与 label_layer 一致）

# 粗体候选（换乘站名加粗用，优先微软雅黑粗体）

def _natural_text_key(value: str) -> tuple:
    """Sort labels as users read them: 1号线, 2号线, 10号线."""
    return tuple(
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", str(value or ""))
    )


#: ``render_scale`` 中对应「长边 1024px 成图」的取值（= 1024 / 4096）。
#: 线路标签的视觉规范以该成图尺寸为基准书写，换算见 ``styles/amap.yaml``
#: 的 ``transports.transit_view`` 段。
_BASELINE_1024_SCALE = 0.25


def _resolve_size(val, render_scale: float) -> float:
    if isinstance(val, dict):
        return val.get("fixed", 0.0) + val.get("scaled", 0.0) * render_scale
    return float(val)


def _minimum_station_font_size(width: int, height: int,
                               pixels_per_1k: float = 10.0) -> int:
    """按成图长边给站名字号设置可读下限，避免高分辨率小字发虚。"""
    resolution_scale = max(int(width), int(height), 1) / 1024.0
    return max(int(math.ceil(max(float(pixels_per_1k), 1.0)
                             * resolution_scale)), 8)


def _resolved_station_font_size(base_size: int, candidate_scale: float,
                                readable_minimum: int, is_transfer: bool,
                                transfer_scale: float = 1.25) -> int:
    """解析单站字号；换乘站始终大于普通站的完整基准字号。"""
    base_size = max(int(base_size), 1)
    readable_minimum = max(int(readable_minimum), 1)
    if is_transfer:
        transfer_scale = max(float(transfer_scale), 1.01)
        transfer_base = max(base_size + 1,
                            int(math.ceil(base_size * transfer_scale)))
        transfer_minimum = max(
            transfer_base,
            int(math.ceil(readable_minimum * transfer_scale)),
        )
        return max(int(round(transfer_base * float(candidate_scale))),
                   transfer_minimum)
    return max(int(round(base_size * float(candidate_scale))),
               readable_minimum)


def _rgba_to_hex(color) -> str:
    """把 ARGB 或 RGB 元组转成十六进制颜色。"""
    if len(color) >= 3:
        r, g, b = int(color[-3]), int(color[-2]), int(color[-1])
    else:
        r, g, b = 255, 255, 255
    return "#%02X%02X%02X" % (r, g, b)


# 同色判定阈值：RGB 欧氏距离 < 该值的线路视为同线不同方向（共边重叠绘制）
_COLOR_CLOSE_DIST = 60.0

_RMP_ICON_CACHE = {}
# 预栅格化 PNG 母版（512px，由 tools/diagnostics/rasterize_rmp_icons.py 生成）
_RMP_PNG_CACHE = {}


def _is_light_color(color: str, threshold: float = 170.0) -> bool:
    """判断线路色是否需要在白底站点图标上增加灰色外描边。"""
    try:
        r, g, b = hex_to_rgb(color)
    except (TypeError, ValueError, IndexError):
        return False
    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return luminance >= threshold


def _outlined_icon(icon: Image.Image, color: str = "#737980",
                   width: int = 1) -> Image.Image:
    """扩展透明画布后沿 SVG alpha 轮廓铺灰边，避免外描边被裁切。"""
    width = max(int(width), 1)
    # ChongqingRTBasicStation 的圆环外沿正好贴住 SVG viewBox；如果直接
    # 在原尺寸 alpha 上膨胀，四个方向都会被位图边界削平。除描边宽度
    # 外再留 2px 透明安全区，让抗锯齿边缘也完整保留。
    padding = width + 2
    padded = Image.new(
        "RGBA",
        (icon.width + padding * 2, icon.height + padding * 2),
        (0, 0, 0, 0),
    )
    padded.alpha_composite(icon, (padding, padding))
    alpha = padded.getchannel("A")
    expanded = alpha.filter(ImageFilter.MaxFilter(width * 2 + 1))
    outline = Image.new("RGBA", padded.size, hex_to_rgb(color) + (255,))
    outline.putalpha(expanded)
    result = Image.new("RGBA", padded.size, (0, 0, 0, 0))
    result.alpha_composite(outline)
    result.alpha_composite(padded)
    return result


def _rmp_png_master(name: str) -> Image.Image:
    """加载并缓存预栅格化图标 PNG 母版（512px，透明底）。"""
    master = _RMP_PNG_CACHE.get(name)
    if master is None:
        path = resource_dir("resources") / "icons" / "rmp" / f"{name}.png"
        master = Image.open(path).convert("RGBA")
        _RMP_PNG_CACHE[name] = master
    return master


def _tint_dark_pixels(master: Image.Image, color: str) -> Image.Image:
    """把母版中不透明的深色像素（currentColor 笔画）重着为目标色。

    半透明的抗锯齿边缘像素也是深灰，一并着色以保持平滑边缘。
    """
    import numpy as np

    arr = np.array(master)
    dark = (
        (arr[..., 3] > 0)
        & (arr[..., 0] < 80)
        & (arr[..., 1] < 80)
        & (arr[..., 2] < 80)
    )
    r, g, b = hex_to_rgb(color)
    arr[dark, 0] = r
    arr[dark, 1] = g
    arr[dark, 2] = b
    return Image.fromarray(arr, "RGBA")


def _rmp_icon_bitmap(name: str, size: int, color: str | None = None) -> Image.Image:
    """缩放 PNG 母版，并按需重着深色图形。"""
    size = max(int(round(size)), 1)
    cache_key = (name, size, color or "")
    cached = _RMP_ICON_CACHE.get(cache_key)
    if cached is not None:
        return cached.copy()
    master = _rmp_png_master(name)
    if color:
        master = _tint_dark_pixels(master, color)
    # 母版 512px 直接 LANCZOS 缩到目标尺寸（下采样自带抗锯齿）。
    icon = master.resize((size, size), Image.Resampling.LANCZOS)
    _RMP_ICON_CACHE[cache_key] = icon
    return icon.copy()


def _paste_centered_icon(draw, icon: Image.Image, px: float, py: float) -> None:
    """把带 alpha 的 SVG 位图贴到局部 ImageDraw 画布中心。"""
    image = getattr(draw, "_image", None)
    if image is None:
        raise RuntimeError("ImageDraw 画布不支持 SVG 图标合成")
    image.paste(
        icon,
        (int(round(px - icon.width / 2)),
         int(round(py - icon.height / 2))),
        icon,
    )


def _railmapgen_facility_icon(kind: str, size: int) -> Image.Image:
    """使用 Rail Map Painter 仓库中的原始设施 SVG 图标。"""
    return _rmp_icon_bitmap(kind, size)


def _color_close(c1: str, c2: str) -> bool:
    """两 hex 颜色是否接近（RGB 欧氏距离 < 阈值）。"""
    a, b = hex_to_rgb(c1), hex_to_rgb(c2)
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2
            + (a[2] - b[2]) ** 2) ** 0.5 < _COLOR_CLOSE_DIST


def _visual_corridor_slots(line_ids, tr_by_id: dict, group_of: dict,
                           color_of, *, allow_color_close: bool = True
                           ) -> tuple[int, dict]:
    """共线对象折叠为视觉槽：同逻辑线或近似色共槽，异色稳定并排。"""
    groups = []
    slot_of = {}
    for tid in line_ids:
        tr = tr_by_id.get(tid)
        if tr is None:
            continue
        color = color_of(tr)
        group = group_of.get(tid) if group_of else None
        group_key = ("g", id(group)) if group is not None else None
        slot = next((i for i, item in enumerate(groups)
                     if ((group_key is not None and item[0] == group_key)
                         or (allow_color_close and _color_close(item[1], color)))), None)
        if slot is None:
            groups.append((group_key, color))
            slot = len(groups) - 1
        slot_of[tid] = slot
    return max(len(groups), 1), slot_of


def _luminance(rgb) -> float:
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]


def _luminance709(rgb) -> float:
    """Rec.709 加权亮度（sRGB 通道权重）。

    线路标签的「深字/白字」判定专用，必须与预览侧
    ``viewer/src/main.ts::perceivedLuminance`` 同式同阈值，否则中间亮度的
    线路色会在两端判出相反的字色（详见 01-线路标签视觉统一-20260914.md）。
    """
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def _clip_segment_to_box(p0, p1, box: tuple[float, float, float, float]):
    """返回线段进入/离开矩形的参数；无交集返回 None。"""
    x0, z0 = p0.x, p0.z
    dx, dz = p1.x - x0, p1.z - z0
    xmin, zmin, xmax, zmax = box
    t_enter, t_exit = 0.0, 1.0
    for p, q in (
        (-dx, x0 - xmin),
        (dx, xmax - x0),
        (-dz, z0 - zmin),
        (dz, zmax - z0),
    ):
        if abs(p) < 1e-12:
            if q < 0.0:
                return None
            continue
        t = q / p
        if p < 0.0:
            if t > t_exit:
                return None
            t_enter = max(t_enter, t)
        else:
            if t < t_enter:
                return None
            t_exit = min(t_exit, t)
    if t_enter > t_exit:
        return None
    return max(0.0, t_enter), min(1.0, t_exit)


def _lerp_point(p0: Point, p1: Point, t: float) -> Point:
    return Point(
        p0.x + (p1.x - p0.x) * t,
        p0.y + (p1.y - p0.y) * t,
        p0.z + (p1.z - p0.z) * t,
    )


def _clip_world_polyline(
    pts: list[Point],
    box: tuple[float, float, float, float] | None,
    source_box: tuple[float, float, float, float] | None = None,
) -> list[tuple[list[Point], float]]:
    """按世界坐标裁剪线路，返回连续子路径及其原始弧长起点。"""
    if len(pts) < 2:
        return []
    if box is None:
        return [(pts, 0.0)]
    if source_box is not None and (
        source_box[2] < box[0] or source_box[0] > box[2]
        or source_box[3] < box[1] or source_box[1] > box[3]
    ):
        return []
    if all(
        box[0] <= p.x <= box[2] and box[1] <= p.z <= box[3]
        for p in pts
    ):
        return [(pts, 0.0)]

    cumulative = [0.0]
    for p0, p1 in zip(pts, pts[1:]):
        cumulative.append(
            cumulative[-1] + math.hypot(p1.x - p0.x, p1.z - p0.z))

    result: list[tuple[list[Point], float]] = []
    current: list[Point] = []
    current_start = 0.0

    def append_point(point: Point) -> None:
        if not current:
            current.append(point)
            return
        last = current[-1]
        if abs(last.x - point.x) > 1e-9 or abs(last.z - point.z) > 1e-9:
            current.append(point)

    def finish_current() -> None:
        nonlocal current
        if len(current) >= 2:
            result.append((current, current_start))
        current = []

    for index, (p0, p1) in enumerate(zip(pts, pts[1:])):
        seg_len = cumulative[index + 1] - cumulative[index]
        if seg_len <= 1e-12:
            continue
        clipped = _clip_segment_to_box(p0, p1, box)
        if clipped is None:
            finish_current()
            continue
        t_enter, t_exit = clipped
        enter_point = _lerp_point(p0, p1, t_enter)
        exit_point = _lerp_point(p0, p1, t_exit)
        if not current:
            current_start = cumulative[index] + seg_len * t_enter
        elif (abs(current[-1].x - enter_point.x) > 1e-9
              or abs(current[-1].z - enter_point.z) > 1e-9):
            finish_current()
            current_start = cumulative[index] + seg_len * t_enter
        append_point(enter_point)
        append_point(exit_point)
        if t_exit < 1.0 - 1e-12:
            finish_current()
    finish_current()
    return result


def _catmull_rom(points, samples: int):
    """Catmull-Rom 样条：曲线经过全部控制点，段间圆滑过渡。

    points: [(x, z), ...] 世界/像素坐标控制点。
    samples: 每个控制点区间插值的采样数。
    返回平滑后的点列（含首尾控制点）。
    """
    if len(points) < 3:
        return list(points)
    out = [points[0]]
    n = len(points)
    for i in range(n - 1):
        p0 = points[i - 1] if i > 0 else points[i]
        p1 = points[i]
        p2 = points[i + 1]
        p3 = points[i + 2] if i + 2 < n else points[i + 1]
        for s in range(1, samples + 1):
            t = s / samples
            t2, t3 = t * t, t * t * t
            x = (
                0.5
                * (2 * p1[0] + (-p0[0] + p2[0]) * t
                   + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                   + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)
            )
            z = (
                0.5
                * (2 * p1[1] + (-p0[1] + p2[1]) * t
                   + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                   + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
            )
            out.append((x, z))
    if out[-1] != points[-1]:
        out[-1] = points[-1]
    return out


def _line_intersect(p, u, q, v):
    """两直线交点：p+t·u = q+s·v；平行返回 None。"""
    det = u[0] * v[1] - u[1] * v[0]
    if abs(det) < 1e-12:
        return None
    t = ((q[0] - p[0]) * v[1] - (q[1] - p[1]) * v[0]) / det
    return (p[0] + u[0] * t, p[1] + u[1] * t)


def _smooth_corners(pts, round_factor: float = 3.0,
                     use_fractional: bool = False) -> list:
    """SVG 路径圆角处理（移植自 railmapgen pathRounding.js）。

    在每个 L-L 拐点处用三次 Bézier 曲线替代尖角，生成 M L C L 结构。
    直线段保持平直，仅拐点被圆滑。

    Args:
        pts: 折线点列 [(x, y), ...]
        round_factor: 圆角半径（像素），use_fractional=True 时为 0~1 的比例
        use_fractional: True=按段长比例计算圆角，False=固定像素半径

    Returns:
        圆滑后的点列（含 Bézier 曲线控制点）
    """
    if len(pts) < 3 or round_factor <= 0:
        return list(pts)


    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        prev_point = pts[i - 1]
        cur_point = pts[i]
        next_point = pts[i + 1]

        v1x, v1y = cur_point[0] - prev_point[0], cur_point[1] - prev_point[1]
        v2x, v2y = next_point[0] - cur_point[0], next_point[1] - cur_point[1]

        L1 = math.hypot(v1x, v1y)
        L2 = math.hypot(v2x, v2y)

        if L1 < 1e-6 or L2 < 1e-6:
            continue

        cross = (v1x / L1) * (v2y / L2) - (v1y / L1) * (v2x / L2)
        if abs(cross) < 1e-6:
            out.append(cur_point)
            continue

        if use_fractional:
            d1 = round_factor * L1 * 0.5
            d2 = round_factor * L2 * 0.5
        else:
            d1 = min(round_factor, L1 * 0.5)
            d2 = min(round_factor, L2 * 0.5)

        curve_start = move_towards(cur_point, prev_point, d1)
        curve_end = move_towards(cur_point, next_point, d2)

        # 控制点距离：使用更精确的圆弧近似公式
        # 计算转角角度
        dot = (v1x / L1) * (v2x / L2) + (v1y / L1) * (v2y / L2)
        dot = max(-1.0, min(1.0, dot))
        turn_angle = math.pi - math.acos(dot)  # 偏转角
        if turn_angle < 1e-6:
            out.append(cur_point)
            continue
        # 通用公式：控制点距离 = d * 4/3 * tan(angle/4)
        # 90° 拐角时 ≈ d * 0.5522847498
        cp_dist_1 = d1 * (4.0 / 3.0) * math.tan(turn_angle / 4.0)
        cp_dist_2 = d2 * (4.0 / 3.0) * math.tan(turn_angle / 4.0)
        # 控制点方向：沿入射/出射方向
        cp1 = (curve_start[0] + (cur_point[0] - prev_point[0]) / L1 * cp_dist_1,
               curve_start[1] + (cur_point[1] - prev_point[1]) / L1 * cp_dist_1)
        cp2 = (curve_end[0] + (cur_point[0] - next_point[0]) / L2 * cp_dist_2,
               curve_end[1] + (cur_point[1] - next_point[1]) / L2 * cp_dist_2)

        out.append(curve_start)

        # 三次 Bézier 曲线采样
        n_samples = max(4, int(max(d1, d2) / 2))
        for k in range(1, n_samples + 1):
            t = k / (n_samples + 1)
            t2, t3 = t * t, t * t * t
            mt = 1 - t
            x = (mt ** 3) * curve_start[0] + 3 * mt * mt * t * cp1[0] \
                + 3 * mt * t2 * cp2[0] + t3 * curve_end[0]
            y = (mt ** 3) * curve_start[1] + 3 * mt * mt * t * cp1[1] \
                + 3 * mt * t2 * cp2[1] + t3 * curve_end[1]
            out.append((x, y))

    out.append(pts[-1])
    return out


def _bezier_offset(pts, d: float) -> list:
    """Bézier 等距偏移（移植自 railmapgen bezier-parallel.ts）。

    对折线进行法向偏移，拐点处使用直线交点算法（类似 getLineIntersection），
    避免传统 miter join 产生的尖刺。支持 C1 连续的平滑过渡。

    Args:
        pts: 折线点列 [(x, y), ...]
        d: 偏移距离（正=左侧，负=右侧）

    Returns:
        偏移后的点列
    """
    if len(pts) < 2 or abs(d) < 1e-6:
        return list(pts)

    segs = []
    for i in range(len(pts) - 1):
        ax, ay = pts[i]
        bx, by = pts[i + 1]
        dx, dy = bx - ax, by - ay
        L = math.hypot(dx, dy)
        if L < 1e-9:
            continue
        ux, uy = dx / L, dy / L
        nx, ny = -uy, ux
        segs.append((
            (ax + nx * d, ay + ny * d),
            (bx + nx * d, by + ny * d),
            (ux, uy)
        ))

    if not segs:
        return list(pts)

    out = [segs[0][0]]
    for i in range(len(segs) - 1):
        a1, b1, u1 = segs[i]
        _a2, b2, u2 = segs[i + 1]

        ip = _line_intersect(a1, u1, b2, u2)

        dot = u1[0] * u2[0] + u1[1] * u2[1]
        angle = math.acos(max(-1.0, min(1.0, dot)))

        # 大角度（>144°）直接拼接，避免过远的交点
        if angle > math.pi * 0.80:
            out.append(b1)
        elif ip is not None:
            out.append(ip)
        else:
            out.append(b1)

    out.append(segs[-1][1])
    return out


def _parallel_endpoint_connectors(base_pts, parallel_pts) -> list[list[tuple]]:
    """返回严格平行线两端到站芯的短连接段。"""
    if len(base_pts) < 2 or len(parallel_pts) < 2:
        return []
    connectors = []
    for base, parallel in ((base_pts[0], parallel_pts[0]),
                           (base_pts[-1], parallel_pts[-1])):
        if math.hypot(base[0] - parallel[0],
                      base[1] - parallel[1]) > 0.25:
            connectors.append([base, parallel])
    return connectors


def _corridor_edge_key(a, b, quantum: float = 0.25) -> tuple:
    """像素线段无向键；同一实际路段正反方向归入同一走廊。"""
    quantum = max(float(quantum), 1e-6)

    def quantize(point):
        return (int(round(point[0] / quantum)),
                int(round(point[1] / quantum)))

    qa, qb = quantize(a), quantize(b)
    return (qa, qb) if qa <= qb else (qb, qa)


def _stable_corridor_line_ids(line_ids, tr_by_id: dict) -> list[int]:
    """按交通 mode 注册优先级和线路 id 给走廊线路稳定排序。"""
    mode_priority = {mode: rank
                     for rank, mode in enumerate(transit.TYPE_REGISTRY)}

    def sort_key(tid):
        tr = tr_by_id.get(tid)
        mode = ((transit.normalize_type(tr.type) or tr.type)
                if tr is not None else "")
        return (
            mode_priority.get(mode, len(mode_priority)),
            int(getattr(tr, "id", 0) or 0) if tr is not None else 0,
            str(getattr(tr, "name", "") or "") if tr is not None else "",
        )

    return sorted(dict.fromkeys(line_ids), key=sort_key)


def _resample_px(pts, step: float):
    """像素折线按弧长 step 加密（保持形状，供渐变采样）。"""
    if len(pts) < 2 or step <= 0:
        return list(pts)
    out = [pts[0]]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        L = math.hypot(x1 - x0, y1 - y0)
        if L < 1e-9:
            continue
        n = int(L / step)
        for i in range(1, n + 1):
            t = (i * step) / L
            out.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
        if out[-1] != (x1, y1):
            out.append((x1, y1))
    return out


def _bounded_offset(pts, distance: float,
                    miter_limit: float = 1.8) -> list:
    """对复合色带做有 miter 上限的小幅法向偏移。

    `_bezier_offset` 适合间距较大的平行线，但在发卡弯中交点可能
    超出局部曲率半径。复合色带只需很小的横向分色，因此对角
    平分偏移显式限幅，避免尖刺和自交。
    """
    if len(pts) < 2 or abs(distance) < 1e-9:
        return list(pts)
    normals = []
    for a, b in zip(pts, pts[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        if length < 1e-9:
            normals.append((0.0, 1.0))
        else:
            ux, uy = dx / length, dy / length
            normals.append((-uy, ux))

    out = []
    max_shift = abs(distance) * max(float(miter_limit), 1.0)
    for index, point in enumerate(pts):
        if index == 0:
            nx, ny = normals[0]
            shift = distance
        elif index == len(pts) - 1:
            nx, ny = normals[-1]
            shift = distance
        else:
            n0, n1 = normals[index - 1], normals[index]
            nx, ny = n0[0] + n1[0], n0[1] + n1[1]
            magnitude = math.hypot(nx, ny)
            if magnitude < 1e-6:
                nx, ny = n1
                shift = distance
            else:
                nx, ny = nx / magnitude, ny / magnitude
                denominator = nx * n1[0] + ny * n1[1]
                shift = (distance / denominator
                         if abs(denominator) > 0.2 else distance)
                shift = max(-max_shift, min(max_shift, shift))
        out.append((point[0] + nx * shift, point[1] + ny * shift))
    return out


def _connected_segment_chains(mapdata: MapData,
                              segment_ids) -> list[tuple[list[int], int]]:
    """把同样式的实体线段追踪为最长无分支链。"""
    segments = {
        int(segment_id): mapdata.segment(int(segment_id))
        for segment_id in dict.fromkeys(segment_ids)
        if mapdata.segment(int(segment_id)) is not None
    }
    adjacency: dict[int, list[int]] = {}
    isolated = []
    for segment_id, segment in segments.items():
        if segment.start_node_id == segment.end_node_id:
            isolated.append(([segment_id], segment.start_node_id))
            continue
        adjacency.setdefault(segment.start_node_id, []).append(segment_id)
        adjacency.setdefault(segment.end_node_id, []).append(segment_id)
    for values in adjacency.values():
        values.sort()

    unused = set(segments) - {chain[0] for chain, _node in isolated}
    chains = list(isolated)

    def follow(start_node: int, first_segment: int):
        chain = []
        node = start_node
        segment_id = first_segment
        while segment_id in unused:
            unused.remove(segment_id)
            chain.append(segment_id)
            segment = segments[segment_id]
            next_node = (segment.end_node_id
                         if node == segment.start_node_id
                         else segment.start_node_id)
            candidates = [candidate
                          for candidate in adjacency.get(next_node, ())
                          if candidate in unused]
            if len(adjacency.get(next_node, ())) != 2 or not candidates:
                break
            node = next_node
            segment_id = candidates[0]
        return chain

    for node in sorted(adjacency):
        if len(adjacency[node]) == 2:
            continue
        for segment_id in adjacency[node]:
            if segment_id in unused:
                chain = follow(node, segment_id)
                if chain:
                    chains.append((chain, node))
    while unused:
        first_segment = min(unused)
        segment = segments[first_segment]
        chain = follow(segment.start_node_id, first_segment)
        if chain:
            chains.append((chain, segment.start_node_id))
    return chains


def _connected_grid_chains(edge_keys) -> list[list[tuple[int, int]]]:
    """把同色带集合的八方向网格边追踪为无分支点链。"""
    edges = {}
    adjacency: dict[tuple[int, int], list[tuple]] = {}
    for raw_key in dict.fromkeys(edge_keys):
        key = tuple(raw_key)
        if len(key) != 4:
            continue
        a, b = (key[0], key[1]), (key[2], key[3])
        edges[key] = (a, b)
        adjacency.setdefault(a, []).append(key)
        adjacency.setdefault(b, []).append(key)
    for values in adjacency.values():
        values.sort()
    unused = set(edges)
    chains = []

    def follow(start, first_edge):
        points = [start]
        node = start
        edge = first_edge
        while edge in unused:
            unused.remove(edge)
            a, b = edges[edge]
            next_node = b if node == a else a
            points.append(next_node)
            candidates = [candidate
                          for candidate in adjacency.get(next_node, ())
                          if candidate in unused]
            if len(adjacency.get(next_node, ())) != 2 or not candidates:
                break
            node = next_node
            edge = candidates[0]
        return points

    for node in sorted(adjacency):
        if len(adjacency[node]) == 2:
            continue
        for edge in adjacency[node]:
            if edge in unused:
                points = follow(node, edge)
                if len(points) >= 2:
                    chains.append(points)
    while unused:
        edge = min(unused)
        points = follow(edges[edge][0], edge)
        if len(points) >= 2:
            chains.append(points)
    return chains


def _capsule_points(px, py, half_len, r, angle_deg, n=12):
    """旋转胶囊轮廓顶点（两端半圆），供 polygon 绘制。"""
    a = math.radians(angle_deg)
    ux, uy = math.cos(a), math.sin(a)
    p1 = (px - ux * half_len, py - uy * half_len)
    p2 = (px + ux * half_len, py + uy * half_len)
    pts = []
    # 顶点必须沿外轮廓同向行走。旧实现把两端半圆朝内画，
    # polygon 因此自交成“工字/沙漏”，而不是胶囊。
    for i in range(n + 1):       # p2 右端：-90°→+90°（经外侧）
        t = a - math.pi / 2 + math.pi * i / n
        pts.append((p2[0] + math.cos(t) * r, p2[1] + math.sin(t) * r))
    for i in range(n + 1):       # p1 左端：+90°→+270°（经外侧）
        t = a + math.pi / 2 + math.pi * i / n
        pts.append((p1[0] + math.cos(t) * r, p1[1] + math.sin(t) * r))
    return pts


def _dashed_segments(pts: list, pattern: tuple, dash_offset: float = 0.0) -> list:
    """按虚实 pattern（on, off, ...）把像素点列切成若干实线段。

    aggdraw 的 Pen 不支持虚线，此处按弧长累加切段、逐段实线绘制，
    模拟虚线观感。pattern 单位 = 像素（分辨率自适应）。
    """
    pattern = tuple(float(value) for value in pattern if float(value) > 0)
    if len(pts) < 2 or not pattern:
        return []
    cum = [0.0]
    for i in range(1, len(pts)):
        cum.append(cum[-1] + math.hypot(pts[i][0] - pts[i - 1][0],
                                        pts[i][1] - pts[i - 1][1]))
    total = cum[-1]
    if total <= 0:
        return []
    segs = []
    pos = 0.0
    pi = 0
    on = True
    pattern_total = sum(pattern)
    phase = float(dash_offset) % pattern_total
    while phase > 1e-12:
        span = pattern[pi % len(pattern)]
        if phase < span:
            span -= phase
            phase = 0.0
            break
        phase -= span
        pi += 1
        on = not on
    else:
        span = pattern[pi % len(pattern)]
    start_index = 0
    end_index = 0
    while pos < total - 1e-9:
        span = float(span)
        if span <= 0:
            pi += 1
            on = not on
            span = pattern[pi % len(pattern)]
            continue
        end = min(pos + span, total)
        if on:
            start_index = _advance_index(cum, pos, start_index)
            end_index = _advance_index(cum, end, end_index)
            segs.append(_subpolyline(
                pts, cum, pos, end,
                start_index=start_index, end_index=end_index,
            ))
        pos = end
        on = not on
        pi += 1
        span = pattern[pi % len(pattern)]
    return segs


def _advance_index(cum: list, t: float, index: int) -> int:
    """把累计长度索引单调推进到 ``t`` 所在的线段。"""
    index = max(0, min(index, len(cum) - 2))
    while index < len(cum) - 2 and cum[index + 1] < t:
        index += 1
    return index


def _subpolyline(
    pts: list,
    cum: list,
    t0: float,
    t1: float,
    *,
    start_index: int | None = None,
    end_index: int | None = None,
) -> list:
    """取点列中弧长 [t0, t1] 的子折线（两端插值）。"""
    if t1 - t0 < 1e-9:
        return []
    i = (_advance_index(cum, t0, 0)
         if start_index is None else start_index)
    j = (_advance_index(cum, t1, i)
         if end_index is None else end_index)
    out = [_point_at_index(pts, cum, t0, i)]
    for k in range(i + 1, j + 1):
        out.append(pts[k])
    out.append(_point_at_index(pts, cum, t1, j))
    # 去重相邻重复点
    dedup = [out[0]]
    for p in out[1:]:
        if abs(p[0] - dedup[-1][0]) > 0.5 or abs(p[1] - dedup[-1][1]) > 0.5:
            dedup.append(p)
    return dedup


def _point_at_index(pts: list, cum: list, t: float, index: int) -> tuple:
    """使用已定位索引按弧长插值，避免再次从线路起点扫描。"""
    i = max(0, min(index, len(cum) - 2))
    span = max(cum[i + 1] - cum[i], 1e-9)
    tt = (t - cum[i]) / span
    p0, p1 = pts[i], pts[i + 1]
    return (p0[0] + (p1[0] - p0[0]) * tt,
            p0[1] + (p1[1] - p0[1]) * tt)


class TransportLayer(Layer):
    """交通线路渲染器（6.6：接入 transit 单元，支持多类型线路）。

    类型能力由 csmodernmap/transit.py 的类型注册表提供：
    - 轨道型（Metro/Train/Monorail/Tram/CableCar）：Path 链重建 → 轨道 BFS 回退
    - 地面示意型（Bus/Trolleybus/Ship/Ferry）：Path 链（实际道路/航线）→ 站点直连
    - 空中型（Airplane/Helicopter）：站点直连 + Catmull-Rom
    本层只负责：按配置解析启用类型 → 调 transit 出世界坐标 → 像素变换 →
    类型级样式（线宽/虚实线/颜色）绘制 + 线路名徽章。

    transit_view=True（公共交通图叠加层）：线路加粗、标签更密更大更清晰，
    重合线路标签横向拼接（参考逆向 MergedLineNumbering）。常规渲染默认 False，
    保持既有观感完全不变。
    v6.4：线路沿真实轨道（WayType.Metro 轨道段的形状点）寻路绘制。
    v6.4.2：地铁改为「站点停靠线段 Path 链」重建（精确行驶路线 + 环线闭合）。
    """

    name = "transport"
    # 后期图层：线路与徽章在合并后的最终画布上一次绘制（大小与最终输出一致），
    # 避免分块渲染时跨瓦片边界被裁切（与 label 文字层相同的处理）。
    late = True

    def __init__(self, transit_view: bool = False, metro: bool = False):
        super().__init__()
        self.transit_view = transit_view
        # metro=True（地铁示意图纯图）：启用逻辑线路分组（line_groups）
        # 与 RMG 风格参数，配置来自独立的 metro_map.yaml
        self.metro = metro
        self._transit_model = None
        self._render_lines = ()
        self._raw_line_by_id = {}
        self._line_by_id = {}
        self._geometry_by_line_id = {}
        self._schematic_pos_by_stop_key = {}
        self._station_fixes_active = False
        self._groups = None
        self._group_of = {}
        # 同向重叠别名 id 集合（共享度归并的交路线：同几何同色重叠绘制）
        self._overlay_ids: set = set()
        # 地铁示意图站名只保存在图层，不回写 mapdata；无名站点不生成文字。
        self._rmp_labels = {}
        self._rmp_facilities = []
        self._metro_station_names: dict[int, str] = {}
        self._metro_station_name_custom: dict[int, bool] = {}
        self._schematic_offset = (0, 0)
        self._font_family = None
        self._station_facilities = {}
        self._station_type_overrides = {}
        self._legend_rect = None
        self._title_rect = None
        # 地铁图图例先生成独立面板，待所有 late 图层完成后再扩展成品画布。
        self._legend_panel = None
        self._legend_panel_margin = 0

    def _build_transit_render_context(self, mapdata: MapData, cfg: dict,
                                      enabled: list):
        """构建当前渲染共用的交通视图；源 MapData 保持兼容输入，后续消费只读取 T02 派生模型。"""
        st_cfg = cfg.get("station") or {}
        gcfg = cfg.get("grouping") or {}
        station_params = StationMergeParams(
            merge_enabled=bool(st_cfg.get("merge_enabled", True)),
            merge_range=float(st_cfg.get("merge_range", 48.0)),
            merge_bus_tram=bool(st_cfg.get("merge_bus_tram", True)),
            merge_train_metro=bool(st_cfg.get("merge_train_metro", False)),
            overrides=st_cfg.get("merge_overrides") or {},
        )
        grouping_params = GroupingParams(
            by_name=bool(gcfg.get("by_name", True)),
            sequence_fallback=bool(gcfg.get("sequence_fallback", True)),
            share_ratio=float(gcfg.get("share_ratio", 0.0)),
            merge_partial_overlap=bool(
                gcfg.get("merge_partial_overlap", True)),
            share_corridors=bool(gcfg.get("share_corridors", False)),
        )
        palette = tuple(hex_palette(cfg.get("palette") or ()))
        line_selection = cfg.get(
            "enabled_line_ids", cfg.get("line_ids"))
        params = JobParams(
            enabled_types=tuple(enabled),
            enabled_line_ids=(
                None if line_selection is None
                else tuple(line_selection or ())),
            view_all=False,
            auto_color=bool(cfg.get("auto_color", False)),
            palette=palette,
            station_merge=station_params,
            grouping=grouping_params,
        )
        # None 启用地图旁自动发现，显式路径原样传递，False 则关闭。
        station_fix = st_cfg.get("sidecar", st_cfg.get("station_fix"))
        model = build_transit_model(
            mapdata, params, station_fix=station_fix,
        )
        # 设施匹配属于站点上下文，服务按 T02 站点标识返回关联而不修改源地图。
        if self.metro:
            facility_cfg = st_cfg.get("facilities") or {}
            if bool(facility_cfg.get("enabled", True)):
                facility_map = match_facilities(
                    mapdata, model.stations,
                    distances=facility_cfg.get("distances") or {},
                    merge_distances=facility_cfg.get("merge_distances") or {},
                )
                model = replace(model, stations=tuple(
                    replace(station, facilities=[facility_map[str(station.id)]])
                    if str(station.id) in facility_map else station
                    for station in model.stations
                ))
            else:
                # 标准交通模型可能已有设施关联；关闭该视图时也必须清空这些关联。
                model = replace(model, stations=tuple(
                    replace(station, facilities=[]) for station in model.stations))

        raw_by_id = model.source_lines
        render_lines = tuple(
            raw_by_id[line.id] for line in model.lines if line.id in raw_by_id
        )
        geometry_cache = {}
        geometries = {
            line.id: resolve_route_geometry(
                mapdata, raw_by_id[line.id], line.kind, cache=geometry_cache)
            for line in model.lines if line.id in raw_by_id
        }
        return model, render_lines, geometries


    def _station_lines(self, station) -> list:
        """通过 T02 线路索引解析站点所属的原始线路。"""
        if self._transit_model is not None:
            return [self._raw_line_by_id[line_id]
                    for line_id in getattr(station, "serving_line_ids", ())
                    if line_id in self._raw_line_by_id]
        return list(getattr(station, "lines", ()) or ())

    def _station_group_ids(self, station, lines=None) -> set[int]:
        """返回语义服务分组，不以颜色作为换乘身份。"""
        if self._transit_model is not None:
            return {int(group_id) for group_id in
                    getattr(station, "serving_group_ids", ())}
        groups = set()
        for line in lines if lines is not None else self._station_lines(station):
            group = self._group_of.get(id(line))
            if group is not None:
                groups.add(int(getattr(group, "id", id(group))))
        return groups

    def _is_transfer_station(self, station, lines=None) -> bool:
        manual_type = getattr(self, "_station_type_overrides", {}).get(
            station.id)
        if manual_type == "interchange":
            return True
        if manual_type in {"normal", "terminal"}:
            return False
        return len(self._station_group_ids(station, lines)) >= 2

    def _stop_key_for_line(self, line, node_id: int) -> str | None:
        if self._transit_model is None:
            return None
        for stop in self._transit_model.stops:
            if (stop.key.node_id == int(node_id)
                    and line.id in stop.serving_line_ids):
                # 反向查找必须经由模型，渲染器不能自行重建站点归属。
                if self._transit_model.station_of_stop(stop.stop_key) is not None:
                    return stop.stop_key
        return None

    def render(self, ctx: RenderContext, document) -> None:
        # transit/poi/labeling 深度管线经 MapDocumentView 从 CMM 惰性取数。
        """从标准交通模型绘制线路、站点及视图装饰。"""
        mapdata = (ctx.session._legacy_view() if ctx.session is not None
                   else document)
        cfg = ctx.style.get("transports", {})
        # 普通模式默认覆盖注册表中的全部公共交通类型；主题仍可通过
        # transports.enabled 显式缩小范围，示意图模式则由上层对话框覆盖。
        enabled = transit.resolve_enabled(
            cfg.get("enabled", list(transit.TYPE_REGISTRY)))
        # 常规地图按内容密度过滤线路；公共交通专用视图的叠加层必须完整。
        if not self.transit_view and ctx.detail_policy.transit_types is not None:
            enabled = [
                key for key in enabled
                if key in ctx.detail_policy.transit_types
            ]
        if not enabled:
            return
        self.ctx = ctx
        self._cfg = cfg
        self._legend_panel = None
        self._legend_panel_margin = 0
        self._legend_rect = None
        # 交通线路标签统一读取主题 transports.label.font；旧主题没有该字段
        # 时默认使用黑体（SimHei）。
        self._font_family = (cfg.get("label", {}) or {}).get("font") or "SimHei"
        # 公共交通图模式的线路样式/标签覆盖（YAML transports.transit_view 可调）
        self._tv = cfg.get("transit_view", {}) if self.transit_view else {}
        # 放大倍数（相对 4096 全图基准），线宽/站点徽章随特写等比放大
        self.render_scale = ctx.zoom
        self._font_cache = {}
        # 抽象化开关：true=示意图（站点直连），false=沿真实路线（默认）
        abstract = bool(cfg.get("abstract", False))
        # 八方向走线（抽象模式下默认启用；显式 octilinear=false 时退化为曲线）
        self._octilinear = bool(cfg.get("octilinear", True)) if abstract else False
        st_cfg = cfg.get("station") or {}
        # 标准交通模型在几何、布局和标注前只构建一次，之后所有消费方只读共享结果。
        shared = getattr(ctx, "shared", None)
        context_key = (
            "transport.context", id(mapdata), id(cfg),
            tuple(enabled), self.transit_view, self.metro,
        )
        cached_context = (shared or {}).get(context_key)
        if cached_context is None:
            cached_context = self._build_transit_render_context(
                mapdata, cfg, enabled)
            if shared is not None:
                shared[context_key] = cached_context
        (self._transit_model, self._render_lines,
         self._geometry_by_line_id) = cached_context
        self._station_fixes_active = bool(
            getattr(self._transit_model, "station_fix_applied", False))
        self._raw_line_by_id = {
            int(item.id): item for item in self._render_lines
        }
        self._line_by_id = {
            int(item.id): item for item in self._transit_model.lines
        }
        self._merged = self._transit_model.stations
        self._groups = self._transit_model.groups
        self._group_of = {
            id(self._raw_line_by_id[line_id]): group
            for line_id, group in self._transit_model.group_of.items()
            if line_id in self._raw_line_by_id
        }
        self._overlay_ids = {
            id(self._raw_line_by_id[line_id])
            for group in self._groups
            for line_id in group.overlay_ids
            if line_id in self._raw_line_by_id
        }
        self._metro_station_names = (
            self._build_metro_station_names(
                mapdata, self._merged, st_cfg)
            if self.metro else {}
        )
        self._metro_station_name_custom = getattr(
            self, "_metro_station_name_custom", {})
        # 线路绘制参数（YAML transports.line；缺省值 = 旧硬编码行为）
        line_cfg = cfg.get("line") or {}
        self._halo_color = str(line_cfg.get("halo_color", "#FFFFFF"))
        self._halo_ratio = float(line_cfg.get("halo_ratio", 1.6))
        self._round_factor = float(line_cfg.get("round_factor", 3.0))
        light_cfg = line_cfg.get("light_outline") or {}
        self._light_outline_enabled = bool(
            self.metro and light_cfg.get("enabled", True))
        self._light_outline_threshold = float(
            light_cfg.get("luminance_threshold", 170.0))
        self._light_outline_color = str(
            light_cfg.get("color", "#344054"))
        self._light_outline_width = _resolve_size(
            light_cfg.get("width", {"fixed": 1.2, "scaled": 1.2}),
            self.render_scale,
        )
        # 网格 A* 布局结果（abstract + octilinear 时生效）：
        # 站点合并组 → 八方向网格路由（拐弯惩罚 + 拥堵分离）→ fit 画布。
        # 固定网格布局失败直接抛错；动态比例尺失败时在下方回退到固定网格。
        self._schematic = None
        self._schematic_pos = {}
        self._schematic_pos_by_stop_key = {}
        self._station_facilities = {}
        self._rmp_labels = {}
        self._rmp_facilities = []
        self._legend_rect = None
        self._title_rect = None
        self._schematic_offset = (
            int(getattr(ctx, "tile_offset_x", 0)),
            int(getattr(ctx, "tile_offset_y", 0)),
        )
        if abstract and self._octilinear:
            from ..schematic import schematic_layout_with_fallback
            lay_cfg = cfg.get("schematic", {})
            spacing = float(lay_cfg.get("spacing", 400.0))
            adaptive_cfg = lay_cfg.get("adaptive") or {}
            scale_mode = str(lay_cfg.get("scale_mode", "fixed")).strip().lower()
            adaptive_scale = scale_mode in {
                "adaptive", "dynamic", "nonlinear",
            }
            # 共线走线束并行间距（× 线宽；>1 防叠，越大越松散）
            self._parallel_gap = max(
                float(lay_cfg.get("parallel_gap", 1.55)), 1.05)
            # 共享段几何复用（地铁图可经 grouping.share_corridors 关闭）
            share_corr = True
            if self.metro:
                share_corr = bool((cfg.get("grouping") or {}).get(
                    "share_corridors", True))
            # 地铁图：直接消费外部分组结果（不在布局层内重新猜测）
            alias_map = {
                id(self._raw_line_by_id[alias.id]):
                id(self._raw_line_by_id[group.primary.id])
                for group in self._groups
                for alias in group.aliases
                if alias.id in self._raw_line_by_id
                and group.primary.id in self._raw_line_by_id
            }
            from ..metro_edit import (
                CompiledEdits, EditIssue, MetroEditDocument,
                build_editor_scene,
            )
            compiled_edits = CompiledEdits()
            raw_edits = lay_cfg.get("edits")
            if isinstance(raw_edits, dict):
                try:
                    edit_document = MetroEditDocument.from_dict(raw_edits).for_map(mapdata)
                    compiled_edits = edit_document.compile(
                        self._merged, self._render_lines)
                except (KeyError, TypeError, ValueError) as exc:
                    # 可选覆盖层过期或损坏不能抑制基础渲染，应在编辑器中显示诊断。
                    compiled_edits.issues.append(EditIssue(
                        "edit_document_invalid", str(exc), severity="error"))
            self._station_type_overrides = dict(compiled_edits.station_types)
            layout_kwargs = dict(
                canvas=(
                    int(getattr(ctx, "canvas_width", 0) or ctx.image.width),
                    int(getattr(ctx, "canvas_height", 0) or ctx.image.height),
                ),
                rotation=int(getattr(ctx, "rotation", 0) or 0),
                spacing=spacing, alias=alias_map,
                share_corridors=share_corr,
                station_merge_distance=float(
                    st_cfg.get("merge_range", 48.0)),
                merge_nearby_stations=bool(
                    st_cfg.get("merge_enabled", True)),
                adaptive_scale=adaptive_scale,
                adaptive_strength=float(adaptive_cfg.get("strength", 0.40)),
                adaptive_max_compress=float(
                    adaptive_cfg.get("max_compress", 0.60)),
                adaptive_max_expand=float(
                    adaptive_cfg.get("max_expand", 2.0)),
                adaptive_dense_gap=int(
                    adaptive_cfg.get("dense_gap", 1)),
                adaptive_dense_threshold=float(
                    adaptive_cfg.get("dense_threshold", 0.45)),
                transit_model=self._transit_model,
                station_grid_overrides=compiled_edits.station_cells,
                line_loop_overrides=compiled_edits.line_loops,
                shared_corridor_overrides=compiled_edits.shared_segments,
            )
            projected, water_report = None, {}
            if self.metro:
                self._schematic, projected, water_report = layout_and_water(
                    mapdata, list(self._render_lines), self._merged, enabled,
                    session=ctx.session,
                    water_smoothing=(cfg.get("schematic_water") or {}).get("smoothing", 8),
                    line_width_pixels=median([
                        self._resolve_width(transit.type_def(line.type))
                        for line in self._render_lines]) if self._render_lines else None,
                    **layout_kwargs)
                if water_report["status"] == "unverified":
                    message = i18n_tr("gui.transit.water_unverified")
                    ctx.report(.4, message)
            else:
                self._schematic = schematic_layout_with_fallback(
                    mapdata, list(self._render_lines), self._merged, enabled, **layout_kwargs)
            ctx.shared["transit_layout_report"] = {
                "mode": self._schematic.fallback_strategy,
                "failures": list(self._schematic.layout_failures),
                "model_issues": list(self._transit_model.issues),
                "placement": dict(self._schematic.placement_metrics),
                "source_chains": self._schematic.source_chains,
                "station_positions": dict(self._schematic.node_grid),
                "station_types": dict(compiled_edits.station_types),
                "shared_segments": list(compiled_edits.shared_segments),
                "edit_issues": [issue.to_dict()
                                for issue in compiled_edits.issues],
                "editor_scene": build_editor_scene(
                    self._schematic, self._merged, self._render_lines,
                    compiled_edits.issues),
            }
            if self._schematic.fallback_strategy != "requested":
                key = ("gui.transit.layout_draft"
                       if self._schematic.fallback_strategy == "geographic"
                       else "gui.transit.layout_fallback")
                ctx.report(0.4, i18n_tr(key))
            ox, oy = self._schematic_offset
            self._schematic_pos = {
                node_id: (px - ox, py - oy)
                for node_id, (px, py) in self._schematic.positions.items()
            }
            self._schematic_pos_by_stop_key = {
                key: (px - ox, py - oy)
                for key, (px, py) in getattr(
                    self._schematic, "stop_positions", {}).items()
            }
            if self.metro and self._merged is not None:
                self._station_facilities = {
                    str(station.id): station.facilities[0]
                    for station in self._transit_model.stations
                    if station.facilities
                }
                water_cfg = cfg.get("schematic_water") or {}
                if projected is not None:
                    draw_water_surface(ctx.image, projected, water_cfg, offset=self._schematic_offset)
                ctx.shared["transit_layout_report"]["water"] = {
                    **water_report, "displayed": projected is not None and not projected.is_empty
                    and bool(water_cfg.get("enabled", True))}
        # 线路像素缓存仅限当前 render context；每个整图/瓦片调用都会重置，
        # 避免把前一个 tile 的像素坐标带入下一个 tile。
        self._line_cache = {}
        samples = max(int(cfg.get("smooth", 10) * self.render_scale), 6)

        agg = aggdraw.Draw(ctx.image)
        line_list = list(self._render_lines) if cfg.get("lines_visible", True) else []
        # 线路 id → 对象（共边束颜色分组判定用）
        self._tr_by_id = {id(tr): tr for tr in line_list}
        total_lines = max(len(line_list), 1)
        use_grid = abstract and self._octilinear and self._schematic is not None
        # 非抽象公共交通图：用存档实体 segment ID 组成稳定走廊，
        # 同廊线路合成“单外框 + 多色色带”。不再依赖像素点对
        # 完全相等，也不在每个短片段上反复渐收束。
        bundle_plans = None
        composite_corridors = bool(self._tv.get(
            "composite_corridors", True))
        if line_list and self.transit_view and not abstract and composite_corridors:
            bundle_plans = self._prepare_nonabstract_bundle_plans(
                line_list, mapdata, samples)
            self._draw_nonabstract_bundles(agg, bundle_plans)
        grid_bundle_plans = None
        if (self.metro and use_grid and composite_corridors
                and self._schematic.fallback_strategy != "geographic"):
            grid_bundle_plans = self._prepare_grid_bundle_plans()
            self._draw_nonabstract_bundles(agg, grid_bundle_plans)
        for idx, tr in enumerate(line_list):
            td = transit.type_def(tr.type)
            self.ctx.report(idx / total_lines, tr.name or tr.type)
            if grid_bundle_plans is not None:
                continue
            if use_grid:
                # 网格布局：检查是否为对向线的主线
                alias_ids = self._schematic.alias_pairs.get(id(tr))
                # 重叠别名（同向交路线）不参与 ±gap 对称偏移，按普通
                # 线同几何重叠绘制
                pair_ids = ([a for a in alias_ids
                             if a not in self._overlay_ids]
                            if alias_ids else [])
                if pair_ids:
                    # 双向对象已在逻辑线路层归成一个视觉槽。直接走统一
                    # 共廊槽位算法：同线路双向保持一条居中线；若同一段另有
                    # 异色线路，则按全部视觉线路数稳定并排。旧的 ±gap 特例
                    # 只看一对线路，会与第三条共线线路抢占同一侧槽位。
                    self._draw_line_grid(agg, tr, td)
                    continue
                if (id(tr) in self._schematic.route_alias
                        and id(tr) not in self._overlay_ids):
                    # 对向线的副线：跳过（由主线统一绘制）
                    continue
                # 普通线路（重叠别名：强制 order=0 同几何覆盖）
                self._draw_line_grid(agg, tr, td,
                                     overlay=id(tr) in self._overlay_ids)
                continue
            if bundle_plans is not None:
                continue
            color = self._resolve_color(tr, td)
            width = self._resolve_width(td)
            dash = self._resolve_dash(td)
            for pts, dash_offset in self._line_pixel_paths(
                    tr, td, mapdata, abstract, samples):
                if len(pts) < 2:
                    continue
                # 使用结构化路径模型
                path = from_point_list(
                    pts, round_factor=max(width * self._round_factor, 8.0))
                draw_pts = path.to_sampled_points(max(10, int(width * 2.0)))
                if dash:
                    self._draw_dashed(
                        agg, draw_pts, color, width, dash,
                        dash_offset=dash_offset)
                elif len(draw_pts) >= 2:
                    self._draw_stroked_line(agg, draw_pts, color, width)
                    # 步骤2：线路端点圆帽（模拟 strokeLinecap="round"）
                    for ep in (draw_pts[0], draw_pts[-1]):
                        self._draw_round_cap(agg, ep, color, width)
        agg.flush()

        # 地铁示意图标题先于图例绘制，让图例四角避让时把标题也纳入占用评估。
        if self.metro and use_grid:
            self._draw_metro_title(mapdata, cfg)

        # 线路名徽章（放在细线之上）
        label_cfg = cfg.get("label", {})
        # 低分辨率（如 1024 全图）隐藏徽标，避免小图过于拥挤；
        # min_zoom 阈值：渲染缩放 < 该值时不画徽标（0.5 = 2048 全图）。
        # 抽象模式例外：布局 fit 画布自适应分辨率，任意尺寸可画
        min_zoom = float(label_cfg.get("min_zoom", 0.5))
        if abstract and self._octilinear:
            min_zoom = 0.0
        if label_cfg.get("enabled", True) and self.render_scale >= min_zoom:
            if use_grid:
                # 抽象地铁图：只预生成独立图例面板；最终画布扩展与合成
                # 延迟到 Renderer 完成所有 late 图层之后。
                # transports.legend.enabled 缺省 true = 保持既有绘制。
                if bool((cfg.get("legend") or {}).get("enabled", True)):
                    self._draw_legend(line_list, label_cfg)
            elif self.transit_view:
                self._draw_line_labels_view(
                    mapdata, enabled, label_cfg, abstract, samples)
            else:
                placed = []
                for tr in self._render_lines:
                    td = transit.type_def(tr.type)
                    if (td is None
                            or (transit.normalize_type(tr.type) or tr.type)
                            not in enabled
                            or not tr.name):
                        continue
                    pts = self._line_pixels(tr, td, mapdata, abstract, samples)
                    if len(pts) < 2:
                        continue
                    self._draw_line_labels(tr, pts, label_cfg, placed)

        # 站点（合并后白底圆点，公共交通视图用；常规图默认关闭）
        scfg = cfg.get("station") or {}
        st_min_zoom = float(scfg.get("min_zoom", 0.5))
        if abstract and self._octilinear:
            st_min_zoom = 0.0
        stations_visible = (scfg.get("enabled", False)
                            and self.render_scale >= st_min_zoom)
        if stations_visible:
            self._draw_stations(mapdata, enabled, scfg)
            if use_grid:
                # 全站名标注（抽象地铁图专用；8 方向避让 + 网格防重叠）
                self._draw_station_names(scfg)
        if not use_grid and (self.transit_view or (scfg.get("names") or {}).get("enabled", False)):
            self._draw_transit_station_names(scfg)
        if self.metro and use_grid:
            self._draw_annotations(mapdata, cfg)
            # 设施符号最后绘制，确保不会被线路、站点和站名覆盖。
            if stations_visible and self._station_facilities:
                self._draw_facility_icons(scfg)
        if use_grid and self._schematic.fallback_strategy == "geographic":
            # 导出图像也须显示这一限制，不能只写入进度信息。
            notice = i18n_tr("gui.transit.layout_draft")
            font = load_pillow_font(max(12, round(min(ctx.image.size) / 90)))
            draw = ImageDraw.Draw(ctx.image)
            box = draw.textbbox((8, 8), notice, font=font)
            draw.rectangle((box[0] - 4, box[1] - 4, box[2] + 4, box[3] + 4),
                           fill="#FFF4CE")
            draw.text((8, 8), notice, font=font, fill="#614700")

        if self.metro and use_grid and ctx.shared.get("capture_rmp"):
            from ..rmp_snapshot import capture_layer
            ctx.shared["rmp_snapshot"] = capture_layer(self, projected,
                cfg.get("schematic_water") or {}, scfg)

    # ---- 线路像素（统一入口：transit 单元出世界坐标，本层做像素变换） ----

    def _line_pixels(self, tr, td, mapdata, abstract: bool, samples: int) -> list:
        """线路 → 像素点列（每次 render 周期内缓存）。

        - abstract=True + octilinear=True：站点序列 → 八方向折线（0°/45°/90°/135°）
        - abstract=True + octilinear=False：站点直连 + Catmull-Rom 平滑（旧示意图）
        - 轨道型（chain/track）：沿真实路线（Path 链 / 轨道 BFS），不平滑
        - 示意型（Bus/Ferry 等）：站点直连 + Catmull-Rom 平滑（观感与旧 abstract 一致）
        """
        # 线路像素坐标依赖当前 tile 的 transform/image；即使调用方复用
        # TransportLayer 实例，也不能跨 tile 复用旧坐标。
        key = (id(tr), id(self.ctx.transform), id(self.ctx.image))
        cached = self._line_cache.get(key)
        if cached is not None:
            return cached
        pts = self._compute_line_pixels(tr, td, mapdata, abstract, samples)
        self._line_cache[key] = pts
        return pts

    def _line_pixel_paths(
        self, tr, td, mapdata, abstract: bool, samples: int
    ) -> list[tuple[list, float]]:
        """返回当前可见的像素子路径及其世界弧长起点。

        普通地图/公共交通图在世界坐标中先裁剪，避免把不可见线路的
        全部几何与虚线段带入当前 tile；示意图路径已经是画布布局坐标，
        保持原有拓扑和布局，不走世界裁剪。
        """
        if abstract:
            pts = self._line_pixels(tr, td, mapdata, abstract, samples)
            return [(pts, 0.0)] if len(pts) >= 2 else []
        geometry = self._geometry_by_line_id.get(int(tr.id))
        world_pts = geometry.points if geometry is not None else ()
        if len(world_pts) < 2:
            return []
        visible = []
        for part in geometry.parts or (world_pts,):
            source_box = (min(point.x for point in part), min(point.z for point in part),
                          max(point.x for point in part), max(point.z for point in part))
            visible.extend(_clip_world_polyline(
                part, (self.ctx.world_x0, self.ctx.world_z0, self.ctx.world_x1, self.ctx.world_z1),
                source_box=source_box))
        scale = abs(float(getattr(self.ctx.transform, "scale", 0.0)))
        if scale <= 0.0:
            scale = 1.0
        paths = []
        for segment, start_world in visible:
            pixels = []
            for point in segment:
                px, py = self.ctx.transform(point.x, point.z)
                if (pixels and abs(px - pixels[-1][0]) < 0.5
                        and abs(py - pixels[-1][1]) < 0.5):
                    continue
                pixels.append((px, py))
            if len(pixels) >= 2:
                paths.append((pixels, start_world * scale))
        return paths


    def _bundle_lanes(self, occupants) -> list[dict]:
        """把同一实体走廊上的去返线折叠为稳定视觉色带。"""
        occupants = _stable_corridor_line_ids(
            occupants, self._tr_by_id)
        _count, slot_of = _visual_corridor_slots(
            occupants, self._tr_by_id, self._group_of,
            lambda other: self._resolve_color(
                other, transit.type_def(other.type)),
            allow_color_close=False,
        )
        lanes_by_slot = {}
        for transport_id in occupants:
            slot = slot_of.get(transport_id)
            if slot is None or slot in lanes_by_slot:
                continue
            tr = self._tr_by_id.get(transport_id)
            td = transit.type_def(tr.type) if tr is not None else None
            if tr is None or td is None:
                continue
            lanes_by_slot[slot] = {
                "color": self._resolve_color(tr, td),
                "width": self._resolve_width(td),
                "dash": self._resolve_dash(td),
                "transport_id": transport_id,
            }
        return [lanes_by_slot[index] for index in sorted(lanes_by_slot)]

    def _bundle_pixels_from_segments(self, mapdata, segment_ids,
                                     start_node: int,
                                     width: float) -> list[list]:
        node = mapdata.node(start_node)
        if node is None:
            return []
        world_pts = mapdata._chain_to_points(segment_ids, node.pos)
        if len(world_pts) < 2:
            return []
        source_box = (
            min(point.x for point in world_pts),
            min(point.z for point in world_pts),
            max(point.x for point in world_pts),
            max(point.z for point in world_pts),
        )
        visible = _clip_world_polyline(
            world_pts,
            (self.ctx.world_x0, self.ctx.world_z0,
             self.ctx.world_x1, self.ctx.world_z1),
            source_box=source_box,
        )
        paths = []
        for segment, _start_world in visible:
            pixels = []
            for point in segment:
                px, py = self.ctx.transform(point.x, point.z)
                if (pixels and abs(px - pixels[-1][0]) < 0.5
                        and abs(py - pixels[-1][1]) < 0.5):
                    continue
                pixels.append((px, py))
            if len(pixels) < 2:
                continue
            path = from_point_list(
                pixels, round_factor=max(width * self._round_factor, 8.0))
            sampled = path.to_sampled_points(max(10, int(width * 2.0)))
            if len(sampled) >= 2:
                paths.append(sampled)
        return paths

    def _prepare_nonabstract_bundle_plans(self, line_list, mapdata,
                                          samples: int) -> list[dict]:
        """以实体 segment ID 聚合走廊，生成单外框复合色带计划。"""
        segment_lines: dict[int, list[int]] = {}
        traced_lines = set()
        for tr in line_list:
            td = transit.type_def(tr.type)
            if td is None or td.airborne:
                continue
            if not (td.chain_preferred or td.schematic_fallback):
                continue
            stops = [stop.node_id for stop in tr.stops]
            geometry = self._geometry_by_line_id.get(int(tr.id))
            if geometry is None or str(geometry.kind) != "path_chain":
                continue
            if geometry.parts:
                continue
            segment_ids = list(geometry.path_segment_ids)
            valid_ids = [segment_id for segment_id in segment_ids
                         if mapdata.segment(segment_id) is not None]
            if not valid_ids:
                continue
            first_node = next(
                (mapdata.node(stop_id) for stop_id in stops
                 if mapdata.node(stop_id) is not None), None)
            if first_node is None or len(mapdata._chain_to_points(
                    valid_ids, first_node.pos)) < 2:
                continue
            traced_lines.add(id(tr))
            for segment_id in dict.fromkeys(valid_ids):
                users = segment_lines.setdefault(segment_id, [])
                if id(tr) not in users:
                    users.append(id(tr))

        style_segments: dict[tuple, list[int]] = {}
        style_lanes: dict[tuple, list[dict]] = {}
        for segment_id, occupants in segment_lines.items():
            lanes = self._bundle_lanes(occupants)
            if not lanes:
                continue
            key = tuple((
                lane["color"].upper(),
                round(float(lane["width"]), 3),
                tuple(round(float(value), 3)
                      for value in lane["dash"]),
            ) for lane in lanes)
            style_segments.setdefault(key, []).append(segment_id)
            style_lanes[key] = lanes

        plans = []
        for key in sorted(style_segments, key=str):
            lanes = style_lanes[key]
            width = max((lane["width"] for lane in lanes), default=1.0)
            for segment_ids, start_node in _connected_segment_chains(
                    mapdata, style_segments[key]):
                for pixels in self._bundle_pixels_from_segments(
                        mapdata, segment_ids, start_node, width):
                    plans.append({
                        "points": pixels,
                        "lanes": lanes,
                        "dash_offset": 0.0,
                    })
        # 缺失 Path 链的线路不猜测共廊：保留中心线几何，
        # 但仍纳入统一的“先外框、后色芯”绘制，避免白边相互切割。
        for tr in line_list:
            if id(tr) in traced_lines:
                continue
            td = transit.type_def(tr.type)
            if td is None:
                continue
            lane = {
                "color": self._resolve_color(tr, td),
                "width": self._resolve_width(td),
                "dash": self._resolve_dash(td),
                "transport_id": id(tr),
            }
            for pixels, dash_offset in self._line_pixel_paths(
                    tr, td, mapdata, abstract=False, samples=samples):
                if len(pixels) < 2:
                    continue
                path = from_point_list(
                    pixels,
                    round_factor=max(lane["width"] * self._round_factor,
                                     8.0),
                )
                sampled = path.to_sampled_points(
                    max(10, int(lane["width"] * 2.0)))
                if len(sampled) >= 2:
                    plans.append({
                        "points": sampled,
                        "lanes": [lane],
                        "dash_offset": dash_offset,
                    })
        return plans

    def _prepare_grid_bundle_plans(self) -> list[dict]:
        """将地铁示意图共享网格边合成单外框多色走廊。"""
        if self._schematic is None:
            return []
        style_edges: dict[tuple, list[tuple]] = {}
        style_lanes: dict[tuple, list[dict]] = {}
        for grid_edge, occupants in self._schematic.edge_lines.items():
            lanes = self._bundle_lanes(occupants)
            if not lanes:
                continue
            key = tuple((
                lane["color"].upper(),
                round(float(lane["width"]), 3),
                tuple(round(float(value), 3)
                      for value in lane["dash"]),
            ) for lane in lanes)
            style_edges.setdefault(key, []).append(grid_edge)
            style_lanes[key] = lanes

        plans = []
        for key in sorted(style_edges, key=str):
            lanes = style_lanes[key]
            width = max((lane["width"] for lane in lanes), default=1.0)
            for grid_points in _connected_grid_chains(style_edges[key]):
                pixels = [self._schematic_to_px(x, y)
                          for x, y in grid_points]
                path = self._generate_path_for_segment(
                    pixels,
                    len(lanes) > 1,
                    round_corner_factor=max(
                        width * self._round_factor, 8.0),
                )
                sampled = path.to_sampled_points(
                    max(12, int(width * 2.0)))
                if len(sampled) >= 2:
                    plans.append({
                        "points": sampled,
                        "lanes": lanes,
                        "dash_offset": 0.0,
                    })
        return plans

    def _bundle_metrics(self, lanes) -> tuple[float, float, float]:
        base_width = max((float(lane["width"]) for lane in lanes),
                         default=1.0)
        count = max(len(lanes), 1)
        if count == 1:
            stripe_width = base_width
            core_width = base_width
        else:
            stripe_ratio = max(float(self._tv.get(
                "composite_stripe_ratio", 0.46)), 0.2)
            min_stripe = max(float(self._tv.get(
                "composite_min_stripe", 1.15)), 0.5)
            stripe_width = max(base_width * stripe_ratio, min_stripe)
            core_width = stripe_width * count
        casing_width = core_width + base_width * max(
            self._halo_ratio - 1.0, 0.35)
        return stripe_width, core_width, casing_width

    def _station_radius_for_lines(self, base_radius: float, lines,
                                  transfer_is_circle: bool, station=None) -> float:
        """让圆形换乘站完整盖住相接的复合走廊端面。

        多条共线线路合成一根多色宽线后，线芯宽度会随颜色数增长；
        若换乘图标仍保持单线尺寸，外侧色带会从站点两侧露出，看起来
        像线路没有真正接入车站。这里复用复合走廊自身的宽度计算，保证
        站点半径至少覆盖其白色外框，而非另设一套缩放规则。
        """
        radius = max(float(base_radius), 1.0)
        count = (self._station_group_count(station, lines)
                 if station is not None else self._line_color_count(lines))
        if not transfer_is_circle or count < 2:
            return radius
        line_ids = [id(line) for line in (lines or ())
                    if id(line) in self._tr_by_id]
        lanes = self._bundle_lanes(line_ids)
        if len(lanes) < 2:
            return radius
        _stripe_width, _core_width, casing_width = self._bundle_metrics(lanes)
        edge_clearance = max(1.0, float(self._light_outline_width) * 0.5)
        return max(radius, casing_width * 0.5 + edge_clearance)

    @staticmethod
    def _agg_line(agg, points, color: str, width: float) -> None:
        if len(points) < 2 or width <= 0:
            return
        agg.line([coordinate for point in points for coordinate in point],
                 aggdraw.Pen(color, width))

    @staticmethod
    def _agg_cap(agg, point, color: str, width: float) -> None:
        radius = max(float(width), 0.0) / 2.0
        if radius <= 0:
            return
        agg.ellipse((point[0] - radius, point[1] - radius,
                     point[0] + radius, point[1] + radius),
                    None, aggdraw.Brush(color))

    def _draw_bundle_casing(self, agg, plan) -> None:
        points, lanes = plan["points"], plan["lanes"]
        _stripe_width, _core_width, casing_width = self._bundle_metrics(lanes)
        dash = lanes[0]["dash"] if len(lanes) == 1 else ()
        pieces = (_dashed_segments(points, dash, plan["dash_offset"])
                  if dash else [points])
        for piece in pieces:
            self._agg_line(agg, piece, self._halo_color, casing_width)
            self._agg_cap(agg, piece[0], self._halo_color, casing_width)
            self._agg_cap(agg, piece[-1], self._halo_color, casing_width)

    def _draw_bundle_core(self, agg, plan) -> None:
        points, lanes = plan["points"], plan["lanes"]
        stripe_width, core_width, casing_width = self._bundle_metrics(lanes)
        if any(self._contrast_width(
                lane["color"], stripe_width) is not None for lane in lanes):
            contrast_width = min(
                casing_width - 0.35,
                core_width + 2.0 * max(self._light_outline_width, 0.5),
            )
            self._agg_line(
                agg, points, self._light_outline_color, contrast_width)
            self._agg_cap(
                agg, points[0], self._light_outline_color, contrast_width)
            self._agg_cap(
                agg, points[-1], self._light_outline_color, contrast_width)

        count = len(lanes)
        for index, lane in enumerate(lanes):
            distance = (index - (count - 1) / 2.0) * stripe_width
            stripe_points = _bounded_offset(points, distance)
            dash = lane["dash"]
            pieces = (_dashed_segments(
                stripe_points, dash, plan["dash_offset"])
                if dash else [stripe_points])
            # 色带略微重叠，避免抗锯齿取样在带与带之间留白缝。
            draw_width = stripe_width + (0.32 if count > 1 else 0.0)
            for piece in pieces:
                self._agg_line(
                    agg, piece, lane["color"], draw_width)
                self._agg_cap(
                    agg, piece[0], lane["color"], draw_width)
                self._agg_cap(
                    agg, piece[-1], lane["color"], draw_width)

    def _draw_nonabstract_bundles(self, agg, plans) -> None:
        """全部走廊先画一次外框，再画复合色芯，杜绝白边覆盖。"""
        for plan in plans:
            self._draw_bundle_casing(agg, plan)
        for plan in plans:
            self._draw_bundle_core(agg, plan)

    def _compute_line_pixels(self, tr, td, mapdata, abstract: bool, samples: int) -> list:
        if abstract:
            if self._octilinear and self._schematic is not None:
                # 网格 A* 路径（天然八方向，无需再规整）
                route = self._schematic.routes.get(id(tr))
                if route is None:
                    return []
                pts = []
                for g in route:
                    px, py = self._schematic_to_px(*g)
                    if pts and abs(px - pts[-1][0]) < 0.5 and \
                            abs(py - pts[-1][1]) < 0.5:
                        continue
                    pts.append((px, py))
                return pts
            schematic = self._line_pixels_schematic(tr, mapdata)
            return _catmull_rom(schematic, samples)
        geometry = self._geometry_by_line_id.get(int(tr.id))
        pts_world = geometry.points if geometry is not None else ()
        pts = []
        for p in pts_world:
            px, py = self.ctx.transform(p.x, p.z)
            if pts and abs(px - pts[-1][0]) < 0.5 and abs(py - pts[-1][1]) < 0.5:
                continue
            pts.append((px, py))
        if td.schematic_fallback and not td.track_flag:
            # 示意型线路（无轨道图）：Catmull-Rom 平滑（非抽象模式不走八方向）
            return _catmull_rom(pts, samples)
        return pts

    # ---- 样式解析（类型级，YAML types → 兜底 cfg → transit 注册表默认） ----


    def _type_cfg(self, key: str) -> dict:
        return (self._cfg.get("types") or {}).get(key) or {}

    def _resolve_width(self, td) -> float:
        tcfg = self._type_cfg(td.key)
        if "width" in tcfg:
            w = _resolve_size(tcfg["width"], self.render_scale)
        elif "width" in self._cfg:
            w = _resolve_size(self._cfg["width"], self.render_scale)
        else:
            w = _resolve_size(td.style.get("width", 1.0), self.render_scale)
        # 公共交通图只保留固定的视图加粗倍率。分辨率增长
        # 已由线路 width 的 fixed + scaled * render_scale 处理；
        # 若此处再随 render_scale 增长，区域特写会变成平方级放大。
        if self.transit_view:
            w *= float(self._tv.get("width_scale", 1.7))
        # 仅公共交通专用视图保留可读性钳位；普通地图按主题值原样绘制。
        # 这样不会把普通渲染中误放大的交通线宽再次“托底”到粗线。
        if self.transit_view:
            min_w = 2.0 if getattr(td, 'track_flag', None) else 1.5
            w = max(w, min_w)
        return w

    def _resolve_dash(self, td) -> tuple:
        tcfg = self._type_cfg(td.key)
        d = tcfg.get("dash")
        if d is None:
            d = td.style.get("dash")
        if not d:
            return ()
        return tuple(float(x) for x in d if float(x) > 0)

    def _resolve_color(self, tr, td) -> str:
        # 优先级：T02 用户色 → T02 服务自动色 → 类型默认色 → 兜底灰。
        line = self._line_by_id.get(int(tr.id))
        if line is not None and line.user_color:
            r, g, b = line.user_color[:3]
            return "#%02X%02X%02X" % (r, g, b)
        # 私有几何和布局辅助函数可单独测试，此时未构建模型，但显式源颜色仍是有效输入。
        if self._transit_model is None:
            raw_color = tuple(getattr(tr, "color", ()) or ())
            if raw_color and tuple(raw_color[-3:]) != (255, 255, 255):
                return _rgba_to_hex(raw_color)
        auto_color = (self._transit_model.auto_colors.get(int(tr.id))
                      if self._transit_model is not None else None)
        if auto_color:
            return _rgba_to_hex(auto_color)
        c = self._type_cfg(td.key).get("color") or td.style.get("color")
        if c:
            return c
        return "#8A8F99"

    # ---- 线路名徽章 ----

    def _schematic_to_px(self, gx: int, gy: int) -> tuple[float, float]:
        """把全图示意布局坐标平移到当前 tile 的局部像素坐标。"""
        px, py = self._schematic.to_px(gx, gy)
        return (
            px - self._schematic_offset[0],
            py - self._schematic_offset[1],
        )

    def _line_pixels_schematic(self, tr, mapdata) -> list:
        """抽象示意图：站点序列 → 像素点列（地铁图风格，不沿轨道）。

        布局坐标已是画布像素（schematic_layout fit 输出），直接使用；
        未被布局的站点（数据异常）回退真实坐标 transform。
        """
        pts = []
        for stop in tr.stops:
            stop_key = self._stop_key_for_line(tr, stop.node_id)
            if (stop_key is not None
                    and stop_key in self._schematic_pos_by_stop_key):
                px, py = self._schematic_pos_by_stop_key[stop_key]
            elif self._schematic_pos and stop.node_id in self._schematic_pos:
                px, py = self._schematic_pos[stop.node_id]
            else:
                semantic = self._transit_model.stop(stop_key) if self._transit_model is not None and stop_key else None
                semantic = semantic if semantic is not None and semantic.key.namespace else None
                n = mapdata.node(stop.node_id) if semantic is None else None
                position = semantic.position if semantic is not None else n.pos if n is not None else None
                if position is None:
                    continue
                px, py = self.ctx.transform(position.x, position.z)
            if pts and abs(px - pts[-1][0]) < 0.5 and abs(py - pts[-1][1]) < 0.5:
                continue
            pts.append((px, py))
        return pts

    def _draw_dashed(
        self, agg, pts: list, color: str, width: float, dash: tuple,
        dash_offset: float = 0.0,
    ) -> None:
        """按虚实模式分段绘制（aggdraw 不支持虚线，手工切段）。"""
        segments = _dashed_segments(pts, dash, dash_offset=dash_offset)
        for sub in segments:
            if len(sub) < 2:
                continue
            self._draw_stroked_line(agg, sub, color, width)

    def _contrast_width(self, color: str, width: float) -> float | None:
        """浅色线路需要的深色外框宽度；深色线返回 None。"""
        if (not self._light_outline_enabled
                or _luminance(hex_to_rgb(color))
                < self._light_outline_threshold):
            return None
        return width + 2.0 * max(self._light_outline_width, 0.5)

    def _contrast_color(self, color: str, fallback: str | None = None) -> str:
        """返回站点圆圈使用的可读描边色，与线路浅色对比框同源。"""
        if not color:
            return fallback or self._light_outline_color
        try:
            light = (_luminance(hex_to_rgb(color))
                     >= self._light_outline_threshold)
        except (TypeError, ValueError, IndexError):
            light = False
        if self._light_outline_enabled and light:
            return self._light_outline_color
        return color

    def _draw_stroked_line(self, agg, pts: list, color: str,
                           width: float) -> None:
        """统一线路描边：白色隔离带 → 浅色对比框 → 线路色。"""
        if len(pts) < 2:
            return
        coords = [c for p in pts for c in p]
        agg.line(coords, aggdraw.Pen(
            self._halo_color, width * self._halo_ratio))
        contrast_width = self._contrast_width(color, width)
        if contrast_width is not None:
            agg.line(coords, aggdraw.Pen(
                self._light_outline_color, contrast_width))
        agg.line(coords, aggdraw.Pen(color, width))

    def _draw_round_cap(self, agg, point, color: str, width: float) -> None:
        """线端与主线使用相同的三层描边，避免浅色端点消失。"""
        if point is None or width < 1.0:
            return
        x, y = point

        def circle(diameter: float, fill: str) -> None:
            radius = diameter / 2.0
            agg.ellipse((x - radius, y - radius, x + radius, y + radius),
                        None, aggdraw.Brush(fill))

        circle(width * self._halo_ratio, self._halo_color)
        contrast_width = self._contrast_width(color, width)
        if contrast_width is not None:
            circle(contrast_width, self._light_outline_color)
        circle(width, color)

    def _draw_parallel_connectors(self, agg, base_pts, parallel_pts,
                                  color: str, width: float) -> None:
        """先画站芯连接，再覆盖严格平行主线；站点图标稍后遮住接缝。"""
        for connector in _parallel_endpoint_connectors(base_pts,
                                                       parallel_pts):
            self._draw_stroked_line(agg, connector, color, width)

    # ---- 网格布局绘制（抽象地铁图） ----

    def _draw_line_grid(self, agg, tr, td, overlay: bool = False,
                        route_override=None, station_idx_override=None,
                        draw_parts: bool = True) -> None:
        """网格布局线路：路径生成 + 恒定并行偏移。

        使用结构化路径模型（OpenPath）：
        1. 同一站间的完整折线先合并共线点
        2. 所有内部拐点统一生成连续 R 角
        3. 并行线按固定法向距离严格平移
        4. 路径采样点列绘制

        共线多线（占用同一网格边的线路组）按布局序号横向错开
        gap（略大于线宽）——经典地铁图走线束。整段偏移量恒定，
        不再在站点附近斜向收束；线路分合接缝由后绘制的站点标记覆盖。

        开放环线路（out/back 共边）：同条线路的去程和回程段
        复用相同网格边，通过 geometry 缓存复用同一 base geometry
        而非跳过——既消除路径独立生成导致的平行四边形/锯齿，
        又保证回程站点仍有连线。
        """
        res = self._schematic
        if res.fallback_strategy == "geographic":
            # 源几何可能交叉或重访站点，绘制完整路段链，不套用严格网格平滑或并行槽位。
            parts = [res.routes.get(id(tr), ()), *res.route_parts.get(id(tr), ())]
            color, width = self._resolve_color(tr, td), self._resolve_width(td)
            dash = self._resolve_dash(td)
            for part in parts:
                points = [self._schematic_to_px(*point) for point in part]
                if len(points) < 2:
                    continue
                if dash:
                    self._draw_dashed(agg, points, color, width, dash)
                else:
                    self._draw_stroked_line(agg, points, color, width)
            return
        route = (route_override if route_override is not None
                 else res.routes.get(id(tr)))
        if route is None or len(route) < 2:
            return
        s_idx = (station_idx_override if station_idx_override is not None
                 else res.station_indices.get(id(tr))) \
            or [0, len(route) - 1]
        color = self._resolve_color(tr, td)
        width = self._resolve_width(td)
        gap = width * self._parallel_gap
        # 段边界：站点索引 + 线尾（环线含闭合段）
        bounds = list(s_idx)
        if bounds[-1] != len(route) - 1:
            bounds.append(len(route) - 1)
        # 几何缓存：边集 frozenset → (base_pts, draw_pts)
        # open-loop 回程段共享同边时复用，保证 out/back 完全重合
        geom_cache: dict = {}
        # 端点圆帽：跟踪整条线路的首尾绘制点
        line_first_pt = None
        line_last_pt = None
        for k in range(len(bounds) - 1):
            i0, i1 = bounds[k], bounds[k + 1]
            seg = route[i0:i1 + 1]
            if len(seg) < 2:
                continue
            # 计算本段网格边集合
            seg_edges: set = set()
            for j in range(len(seg) - 1):
                seg_edges.add(edge_key(seg[j], seg[j + 1]))
            edge_key_fs = frozenset(seg_edges)

            # 缓存命中：复用已绘制的几何（open-loop 回程段）
            cached = geom_cache.get(edge_key_fs)
            if cached is not None:
                base_pts, draw_pts = cached
                # 同色或近似色：直接重叠绘制（视觉合并为一条线）
                # 异色：仍重叠绘制（同一路径但颜色不同，显示并行双线）
                if len(draw_pts) >= 2:
                    self._draw_parallel_connectors(
                        agg, base_pts, draw_pts, color, width)
                    self._draw_stroked_line(
                        agg, draw_pts, color, width)
                continue

            # 共享情况：段内任一网格边的最大占用数与本线序号。
            n_share, order, lst = 1, 0, []
            for j in range(len(seg) - 1):
                e_lst = res.edge_lines.get(
                    edge_key(seg[j], seg[j + 1]), [])
                if len(e_lst) > len(lst) and id(tr) in e_lst:
                    lst = e_lst
            if lst:
                n_share, slot_of = _visual_corridor_slots(
                    lst, self._tr_by_id, self._group_of,
                    lambda tr2: self._resolve_color(
                        tr2, transit.type_def(tr2.type)),
                    allow_color_close=False)
                # 重叠别名（同向交路线）：强制同槽重叠，同色覆盖即视觉一条线
                if overlay:
                    order = 0
                else:
                    order = slot_of.get(id(tr), 0)
            # 非共站但同方向、长距离近邻的视觉走廊：布局后处理已为
            # 整段分配稳定槽位。仅在本段没有共享物理边时接管，避免
            # 覆盖已有的共廊/对向线对称偏移规则。
            # 兼容回退到 f28ad34 的原始布局算法：旧 SchematicResult 没有
            # directional_corridor_slots，因而自然跳过本轮新增的视觉吸附。
            directional_slot = getattr(res, "directional_corridor_slots", {}).get(
                (id(tr), i0, i1))
            if n_share <= 1 and directional_slot is not None:
                n_share = directional_slot.count
                order = directional_slot.slot
            pts = [self._schematic_to_px(gx, gy) for gx, gy in seg]
            # === 路径生成：用结构化路径模型 ===
            rc_factor = max(width * self._round_factor, 8.0)
            path = self._generate_path_for_segment(pts, n_share > 1,
                                                    round_corner_factor=rc_factor)
            # 采样为密集点列用于渲染
            if n_share > 1:
                d = (order - (n_share - 1) / 2.0) * gap
                base = path.to_sampled_points(max(12, int(width * 2.0)))
                # 按弧长补点后统一计算法向，整段保持恒定槽位偏移。
                base = _resample_px(base, max(width * 0.75, 3.0))
                draw_pts = _bezier_offset(base, d)
            else:
                draw_pts = path.to_sampled_points(max(12, int(width * 2.0)))
                base = draw_pts
            # 缓存本段几何（以边集为 key），供 open-loop 回程段复用
            geom_cache[edge_key_fs] = (base, draw_pts)
            if len(draw_pts) >= 2:
                self._draw_parallel_connectors(
                    agg, base, draw_pts, color, width)
                self._draw_stroked_line(agg, draw_pts, color, width)
                # 记录首尾点（用于端点圆帽）
                if line_first_pt is None:
                    line_first_pt = draw_pts[0]
                line_last_pt = draw_pts[-1]
        # 端点圆帽（模拟 strokeLinecap="round"）
        for ep in (line_first_pt, line_last_pt):
            self._draw_round_cap(agg, ep, color, width)
        if draw_parts:
            parts = res.route_parts.get(id(tr), ())
            indices = res.part_station_indices.get(id(tr), ())
            for part, p_idx in zip(parts, indices):
                self._draw_line_grid(
                    agg, tr, td, overlay=overlay,
                    route_override=part,
                    station_idx_override=p_idx,
                    draw_parts=False,
                )

    def _generate_path_for_segment(self, pts: list[tuple],
                                    has_parallel: bool = False,
                                    round_corner_factor: float = 0.0) -> OpenPath:
        """把完整站间折线转为连续、无尖角的 R 角路径。"""
        del has_parallel  # 槽位偏移在路径生成后统一处理
        if len(pts) < 2:
            return OpenPath('empty-open', [])

        # 去重并合并同方向共线格点，使圆角半径由真实折线段长
        # 决定，而不是被单个网格步长限制。
        unique = []
        for point in pts:
            if not unique or point != unique[-1]:
                unique.append(point)
        if len(unique) < 3:
            return from_point_list(unique, round_factor=0)

        compact = [unique[0]]
        for i in range(1, len(unique) - 1):
            a, b, c = compact[-1], unique[i], unique[i + 1]
            ab = (b[0] - a[0], b[1] - a[1])
            bc = (c[0] - b[0], c[1] - b[1])
            cross = ab[0] * bc[1] - ab[1] * bc[0]
            dot = ab[0] * bc[0] + ab[1] * bc[1]
            if abs(cross) < 1e-9 and dot > 0:
                continue
            compact.append(b)
        compact.append(unique[-1])

        rounded = _smooth_corners(compact, round_corner_factor)
        return from_point_list(rounded, round_factor=0)

    def _station_angle(self, gid) -> float:
        """站点走线主方向（度，45° 吸附）：扫描线路路由中该站的前后方向。

        多线经过时取向量平均（换乘车站在主导方向上拉长）。
        """
        res = self._schematic
        cell = res.node_grid.get(gid) if res else None
        if cell is None:
            return 0.0
        angles = []
        routes = list(res.routes.values())
        routes.extend(
            part
            for parts in res.route_parts.values()
            for part in parts
        )
        for route in routes:
            if len(route) < 2:
                continue
            for i, p in enumerate(route):
                if p != cell:
                    continue
                if i == 0:
                    a = math.atan2(route[1][1] - route[0][1],
                                   route[1][0] - route[0][0])
                elif i == len(route) - 1:
                    a = math.atan2(route[i][1] - route[i - 1][1],
                                   route[i][0] - route[i - 1][0])
                else:
                    a = math.atan2(route[i + 1][1] - route[i - 1][1],
                                   route[i + 1][0] - route[i - 1][0])
                angles.append(a)
                break
        if not angles:
            return 0.0
        vx = sum(math.cos(a) for a in angles)
        vy = sum(math.sin(a) for a in angles)
        if abs(vx) < 1e-9 and abs(vy) < 1e-9:
            return 0.0
        deg = math.degrees(math.atan2(vy, vx))
        return round(deg / 45.0) * 45.0

    def _decoration_size(self, value, _zoom=None):
        """先按 1024 像素确定页面装饰，再等比缩放全部尺寸。"""
        width = getattr(self.ctx, "canvas_width", 0) or self.ctx.image.width
        height = getattr(self.ctx, "canvas_height", 0) or self.ctx.image.height
        return _resolve_size(value, 0.25) * min(width, height) / 1024.0

    def _draw_legend(self, line_list: list, cfg: dict) -> None:
        """生成抽象地铁图的独立图例面板。

        面板不直接放到主图中；它会在所有 late 图层完成后由
        :meth:`finalize_output` 贴到新扩展出的右侧画布中。
        """
        res = self._schematic
        if res is None:
            return
        h = int(getattr(self.ctx, "canvas_height", 0) or self.ctx.image.height)
        # 收集有名字的线路，去重（双向合并线只显示一次）
        legend_lines = []
        seen_names = set()
        if self._groups:
            # 地铁图：按逻辑组列图例（一组一条，主方向代表）
            for g in self._groups:
                if self._transit_model is not None:
                    tr = self._raw_line_by_id.get(g.primary.id)
                    if tr is None:
                        continue
                else:
                    tr = g.primary
                if not tr.name:
                    continue
                name_key = tr.name.strip()
                if name_key in seen_names:
                    continue
                seen_names.add(name_key)
                td = transit.type_def(tr.type)
                color = self._resolve_color(tr, td)
                legend_lines.append((tr.name, color, hex_to_rgb(color),
                                     self._resolve_dash(td)))
        else:
            for tr in line_list:
                if not tr.name:
                    continue
                # 对向合并线跳过（主线已包含该名）
                pid = res.route_alias.get(id(tr))
                if pid is not None:
                    prim = self._tr_by_id.get(pid)
                    if prim is not None and prim.name == tr.name:
                        continue
                name_key = tr.name.strip()
                if name_key in seen_names:
                    continue
                seen_names.add(name_key)
                td = transit.type_def(tr.type)
                color = self._resolve_color(tr, td)
                rgb = hex_to_rgb(color)
                legend_lines.append((tr.name, color, rgb,
                                     self._resolve_dash(td)))

        if not legend_lines:
            return
        legend_lines.sort(key=lambda item: (_natural_text_key(item[0]), item[0]))
        facility_symbols = [kind for kind in ("airport", "rail", "port")
                            if any(
                                str(getattr(item.kind, "value", item.kind)).lower()
                                == kind
                                for item in self._station_facilities.values())]

        # 图例卡片尺寸按 fixed + scaled*zoom 解析；宽度由实际文本决定，
        # 供成品画布向右扩展，而不是把名称压缩到主图比例预算内。
        font_size = max(int(round(self._decoration_size(
            cfg.get("size", {"fixed": 15, "scaled": 11}),
            self.render_scale))), 8)
        title_size = max(int(round(self._decoration_size(
            cfg.get("title_size", {"fixed": 16, "scaled": 12}),
            self.render_scale))), font_size)
        pad = max(int(round(self._decoration_size(
            cfg.get("padding", {"fixed": 10, "scaled": 8}),
            self.render_scale))), 6)
        gap = max(int(round(self._decoration_size(
            cfg.get("gap", {"fixed": 6, "scaled": 4}),
            self.render_scale))), 3)
        column_gap = max(int(round(self._decoration_size(
            cfg.get("column_gap", {"fixed": 18, "scaled": 12}),
            self.render_scale))), gap * 2)
        swatch_w = max(int(round(self._decoration_size(
            cfg.get("swatch_width", {"fixed": 28, "scaled": 20}),
            self.render_scale))), 14)
        swatch_h = max(int(round(self._decoration_size(
            cfg.get("swatch_height", {"fixed": 10, "scaled": 8}),
            self.render_scale))), 6)
        border_w = max(int(round(self._decoration_size(
            cfg.get("border_width", {"fixed": 0.8, "scaled": 0.8}),
            self.render_scale))), 1)
        radius = max(int(round(self._decoration_size(
            cfg.get("radius", {"fixed": 6, "scaled": 3}),
            self.render_scale))), 4)
        margin = max(int(round(self._decoration_size(
            cfg.get("margin", {"fixed": 14, "scaled": 14}),
            self.render_scale))), 10)
        row_h = max(
            # 标签本体高 1.75× 字号；图例行高额外留出约 0.45× 字号，
            # 避免连续色块贴得过紧。
            int(round(font_size * 2.2)),
            swatch_h + gap * 2,
        )
        title_key = str(cfg.get("title_key", "")).strip()
        title = (i18n_tr(title_key) if title_key else str(cfg.get("title", "线路")))
        title_gap = max(gap, int(round(font_size * 0.45)))
        header_h = title_size + title_gap if title else 0
        max_h = max(int(h * float(cfg.get("max_height_ratio", 0.42))), 1)

        font = self._get_font(font_size)
        title_font = self._get_font(title_size, bold=True)
        probe = ImageDraw.Draw(Image.new("RGBA", (4, 4), (0, 0, 0, 0)))

        def text_width(value: str, fnt) -> int:
            bb = probe.textbbox((0, 0), value, font=fnt)
            return bb[2] - bb[0]

        available_rows = max(
            int((max_h - pad * 2 - header_h) / max(row_h, 1)), 1)
        # 右侧栏可以水平扩展，因此优先保证图例卡片在垂直方向完整；
        # 线路很多时自动增加列数，不再把卡片裁到主图高度之外。
        columns = max(1, math.ceil(len(legend_lines) / available_rows))
        rows = math.ceil(len(legend_lines) / columns)

        # 极端超长名称可通过配置主动截断；默认 0 表示完整显示并让右侧栏扩展。
        max_name_w = max(int(cfg.get("max_name_width", 0)), 0)

        def fit_name(value: str) -> str:
            if max_name_w <= 0 or text_width(value, font) <= max_name_w:
                return value
            suffix = "…"
            lo, hi = 0, len(value)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if text_width(value[:mid] + suffix, font) <= max_name_w:
                    lo = mid
                else:
                    hi = mid - 1
            return value[:lo] + suffix

        # 彩色条目直接复用 PNG 公共交通图的线路标签绘制器。这样胶囊形状、
        # Rec.709 自适应字色、半透明细轮廓和抗锯齿策略只保留一份实现；
        # 图例仍使用自己的字号与间距配置，不会被地图内标签的密度参数影响。
        color_chip = cfg.get("legend_color_chip", True)
        badge_cfg = {
            "size": float(font_size),
            "padding": float(cfg.get("chip_padding", font_size * 0.6)),
            "border_width": float(cfg.get(
                "chip_border_width", max(font_size * 2.0 / 15.0, 1.0))),
            # PNG 标签绘制器默认是全圆胶囊；图例改用较小圆角的矩形。
            "corner_radius": float(cfg.get(
                "chip_radius", max(font_size * 0.35, 3.0))),
            "outer_border_color": cfg.get("chip_border_color", "#101828"),
            "outer_border_alpha": float(cfg.get("chip_border_alpha", 0.55)),
            "text_on_light": cfg.get("chip_text_on_light", "#101828"),
            "text_on_dark": cfg.get("chip_text_on_dark", "#FFFFFF"),
            "luminance_threshold": float(cfg.get(
                "chip_luminance_threshold", 165.0)),
        }
        display_lines = []
        for name, color, rgb, dash in legend_lines:
            display_name = fit_name(name)
            badge = (self._make_view_badge([(display_name, rgb)], badge_cfg)
                     if color_chip else None)
            display_lines.append((display_name, color, rgb, dash, badge))
        column_widths = []
        for col in range(columns):
            start = col * rows
            entries = display_lines[start:start + rows]
            if color_chip:
                column_widths.append(max(
                    (item[4].width for item in entries if item[4] is not None),
                    default=0,
                ))
            else:
                name_w = max((text_width(item[0], font) for item in entries),
                             default=0)
                column_widths.append(swatch_w + gap + name_w)

        card_w = (pad * 2 + sum(column_widths)
                  + column_gap * (columns - 1))
        symbol_h = (len(facility_symbols) * row_h + (gap if facility_symbols else 0))
        card_h = pad * 2 + header_h + rows * row_h + symbol_h
        if facility_symbols:
            symbol_size = max(20, row_h - 2)
            labels = {
                "airport": i18n_tr("metro.legend.airport"),
                "rail": i18n_tr("metro.legend.rail"),
                "port": i18n_tr("metro.legend.port"),
            }
            symbol_w = symbol_size + gap + max(
                (text_width(labels[kind], font) for kind in facility_symbols),
                default=0,
            )
            card_w = max(card_w, pad * 2 + symbol_w)

        # 圆角只承担轻微卡片修饰，避免在小图例上形成“胶囊”外观。
        radius = min(radius, max(4, min(card_w, card_h) // 5))

        shadow_enabled = bool(cfg.get("shadow", False))
        shadow_blur = max(int(round(self._decoration_size(
            cfg.get("shadow_blur", {"fixed": 4, "scaled": 5}),
            self.render_scale))), 2)
        shadow_offset = max(int(round(self._decoration_size(
            cfg.get("shadow_offset", {"fixed": 2, "scaled": 2}),
            self.render_scale))), 1)
        shadow_pad = shadow_blur * 2 + shadow_offset if shadow_enabled else 0
        legend_img = Image.new(
            "RGBA",
            (card_w + shadow_pad * 2, card_h + shadow_pad * 2),
            (0, 0, 0, 0),
        )
        card_box = (
            shadow_pad, shadow_pad,
            shadow_pad + card_w - 1, shadow_pad + card_h - 1,
        )

        if shadow_enabled:
            mask = Image.new("L", legend_img.size, 0)
            md = ImageDraw.Draw(mask)
            shadow_box = (
                card_box[0] + shadow_offset,
                card_box[1] + shadow_offset,
                card_box[2] + shadow_offset,
                card_box[3] + shadow_offset,
            )
            md.rounded_rectangle(shadow_box, radius=radius, fill=90)
            mask = mask.filter(ImageFilter.GaussianBlur(shadow_blur))
            shadow_layer = Image.new(
                "RGBA", legend_img.size,
                hex_to_rgb(cfg.get("shadow_color", "#101828")) + (0,))
            shadow_layer.putalpha(mask)
            legend_img.alpha_composite(shadow_layer)

        ld = ImageDraw.Draw(legend_img)
        bg_rgb = hex_to_rgb(cfg.get("bg", "#FFFFFF"))
        bc_rgb = hex_to_rgb(cfg.get("border_color", "#D0D5DD"))
        ld.rounded_rectangle(
            card_box,
            radius=radius,
            fill=bg_rgb + (int(cfg.get("background_alpha", 246)),),
            outline=bc_rgb + (220,),
            width=border_w,
        )

        x0, y0 = shadow_pad + pad, shadow_pad + pad
        if title:
            title_text = f"{title}  {len(legend_lines)}"
            ld.text(
                (x0, y0), title_text, font=title_font,
                fill=hex_to_rgb(cfg.get("title_color", "#101828")) + (255,),
            )
            y0 += header_h

        # ``legend_color_chip=false`` 保留旧式「白底 + 色块 + 深字」回退。
        text_rgb = hex_to_rgb(cfg.get("text_color", "#344054"))
        col_x = x0
        for col in range(columns):
            start = col * rows
            entries = display_lines[start:start + rows]
            for row, (name, color, rgb, dash, badge) in enumerate(entries):
                cy = y0 + row * row_h + row_h / 2
                sx0, sx1 = col_x, col_x + swatch_w
                if color_chip and badge is not None:
                    legend_img.alpha_composite(
                        badge,
                        (int(round(col_x)), int(round(cy - badge.height / 2))),
                    )
                    continue
                else:
                    # 旧式画法：白底 + 色块 + 深字
                    contrast = (
                        self._light_outline_color
                        if (_luminance(rgb) >= self._light_outline_threshold)
                        else cfg.get("swatch_border_color", "#FFFFFF")
                    )
                    sample_pts = [(sx0, cy), (sx1, cy)]
                    core_w = max(2, int(round(swatch_h * .52)))
                    def draw_sample(points, fill, line_width):
                        if dash:
                            scaled = tuple(max(2.0, value * core_w / 4.0)
                                           for value in dash)
                            for fragment in _dashed_segments(points, scaled):
                                if len(fragment) >= 2:
                                    ld.line(fragment, fill=fill, width=line_width)
                        else:
                            ld.line(points, fill=fill, width=line_width)
                    draw_sample(sample_pts, hex_to_rgb(self._halo_color) + (255,),
                                max(core_w + 2, int(core_w * self._halo_ratio)))
                    if _luminance(rgb) >= self._light_outline_threshold:
                        draw_sample(sample_pts, hex_to_rgb(str(contrast)) + (255,),
                                    core_w + 2)
                    draw_sample(sample_pts, rgb + (255,), core_w)
                    row_text_rgb = text_rgb
                    tx = col_x + swatch_w + gap
                bb = ld.textbbox((0, 0), name, font=font)
                ty = int(round(cy - (bb[3] - bb[1]) / 2 - bb[1]))
                ld.text((tx, ty), name, font=font, fill=row_text_rgb + (255,))
            col_x += column_widths[col] + column_gap

        # 设施符号说明只在本图实际识别到相应设施时出现，避免空图例。
        if facility_symbols:
            symbol_y = y0 + rows * row_h + gap
            labels = {
                "airport": i18n_tr("metro.legend.airport"),
                "rail": i18n_tr("metro.legend.rail"),
                "port": i18n_tr("metro.legend.port"),
            }
            for index, kind in enumerate(facility_symbols):
                cy = symbol_y + index * row_h + row_h / 2
                icon = _railmapgen_facility_icon(kind, max(20, row_h - 2))
                legend_img.alpha_composite(
                    icon, (int(x0), int(round(cy - icon.height / 2)))
                )
                ld.text((x0 + icon.width + gap, int(cy - font_size * .55)), labels[kind],
                        font=font, fill=text_rgb + (255,))

        # 图例成为独立右侧栏：margin 同时作为面板的左右/上下留白。
        # 不再扫描主图像素，也不再修改主图中的任何布局。
        self._legend_panel = legend_img
        self._legend_panel_margin = margin

    def finalize_output(self, image: Image.Image, document,
                        session=None) -> Image.Image:
        """主图完成后按图例位置扩展画布并合成地铁图例面板。

        transports.legend.position 缺省/非法时回退 label.position；
        right_top = 历史默认（向右扩展、面板靠上），输出与旧版一致。
        """
        del document, session
        panel = self._legend_panel
        if panel is None or image.width <= 0 or image.height <= 0:
            return image
        margin = max(int(self._legend_panel_margin), 0)
        panel_width = panel.width + margin * 2
        bg = self.ctx.style.get("canvas.background", "#FAFAF8")
        bg_rgb = hex_to_rgb(str(bg))
        legend_cfg = (self._cfg or {}).get("legend") or {}
        position = str(legend_cfg.get("position")
                       or (self._cfg or {}).get("label", {}).get(
                           "position", "right_top"))
        side_left = position.startswith("left")
        bottom = position.endswith("bottom")
        panel_y = ((image.height - margin - panel.height) if bottom
                   else margin)
        if image.mode == "RGBA":
            result = Image.new(
                "RGBA", (image.width + panel_width, image.height),
                (0, 0, 0, 0),
            )
            image_x = panel_width if side_left else 0
            result.alpha_composite(image.convert("RGBA"), (image_x, 0))
            panel_bg = Image.new(
                "RGBA", (panel_width, image.height), bg_rgb + (255,)
            )
            panel_x = 0 if side_left else image.width
            result.alpha_composite(panel_bg, (panel_x, 0))
            panel_anchor = margin if side_left else image.width + margin
            result.alpha_composite(panel, (panel_anchor, panel_y))
        else:
            result = Image.new(
                image.mode, (image.width + panel_width, image.height), bg_rgb
            )
            image_x = panel_width if side_left else 0
            result.paste(image, (image_x, 0))
            panel_anchor = margin if side_left else image.width + margin
            result.paste(panel, (panel_anchor, panel_y), panel)
        self._legend_panel = None
        return result

    @staticmethod
    def _legend_occupancy(image, x: int, y: int,
                          width: int, height: int) -> float:
        """估算图例候选矩形的已有内容占用率，数值越低越空。"""
        x0 = max(int(x), 0)
        y0 = max(int(y), 0)
        x1 = min(int(x + width), image.width)
        y1 = min(int(y + height), image.height)
        if x1 <= x0 or y1 <= y0:
            return 1.0
        crop = image.crop((x0, y0, x1, y1))
        if "A" in crop.getbands():
            signal = crop.getchannel("A")
        else:
            # RGB 画布的后备判定：以左上角像素作为背景样本。
            from PIL import ImageChops
            bg = Image.new(crop.mode, crop.size, crop.getpixel((0, 0)))
            signal = ImageChops.difference(crop, bg).convert("L")
        sample_w = max(1, min(64, crop.width // 8 or crop.width))
        sample_h = max(1, min(64, crop.height // 8 or crop.height))
        signal = signal.resize((sample_w, sample_h), Image.Resampling.BILINEAR)
        pixels = list(signal.get_flattened_data()
                      if hasattr(signal, "get_flattened_data") else signal.getdata())
        return sum(pixels) / max(255.0 * len(pixels), 1.0)

    def _draw_metro_title(self, mapdata, cfg: dict) -> None:
        """绘制地铁示意图标题；双语时以中文主标题 + 拼音副标题呈现。"""
        title_cfg = cfg.get("title") or {}
        if not bool(title_cfg.get("enabled", True)):
            return
        city = str(getattr(mapdata, "city_name", "") or "").strip()
        template = str(title_cfg.get("template", "{cityname}地铁图"))
        title = template.replace("{cityname}", city)
        if not city and title == template:
            title = "地铁图"
        if not title:
            return
        size = max(int(round(self._decoration_size(
            title_cfg.get("size", {"fixed": 24, "scaled": 12}),
            self.render_scale))), 8)
        secondary_size = max(int(round(self._decoration_size(
            title_cfg.get("secondary_size", {"fixed": 16, "scaled": 8}),
            self.render_scale))), 7)
        margin = max(int(round(self._decoration_size(
            title_cfg.get("margin", {"fixed": 18, "scaled": 12}),
            self.render_scale))), 4)
        stroke_width = max(int(round(self._decoration_size(
            title_cfg.get("stroke_width", {"fixed": 3, "scaled": 1}),
            self.render_scale))), 0)
        font = self._get_font(size, bold=True)
        secondary_font = self._get_font(secondary_size)
        draw = ImageDraw.Draw(self.ctx.image)
        bbox = draw.textbbox((0, 0), title, font=font,
                             stroke_width=stroke_width)
        text_h = bbox[3] - bbox[1]
        secondary = None
        if str(title_cfg.get("language", "zh")) == "bilingual":
            from ..romanize import chinese_pinyin
            city_pinyin = chinese_pinyin(city)
            if city_pinyin:
                secondary = f"{city_pinyin} Metro Map"
        secondary_h = 0
        if secondary:
            secondary_h = (draw.textbbox((0, 0), secondary,
                                         font=secondary_font)[3]
                           - draw.textbbox((0, 0), secondary,
                                             font=secondary_font)[1])
        canvas_w = int(
            getattr(self.ctx, "canvas_width", 0) or self.ctx.image.width)
        ox, oy = self._schematic_offset
        x = canvas_w / 2 - ox
        total_h = text_h + (secondary_h + max(4, secondary_size // 3)
                            if secondary else 0)
        y = margin + total_h / 2 - oy
        draw.text(
            (x, y), title, font=font,
            anchor="mm",
            fill=hex_to_rgb(title_cfg.get("color", "#101828")) + (255,),
            stroke_width=stroke_width,
            stroke_fill=hex_to_rgb(
                title_cfg.get("stroke_fill", "#FAFAF8")) + (255,),
        )
        if secondary:
            draw.text(
                (x, y + text_h / 2 + max(4, secondary_size // 3)), secondary,
                font=secondary_font, anchor="ma",
                fill=hex_to_rgb(title_cfg.get("color", "#101828")) + (255,),
            )
        self._title_rect = (x - (bbox[2] - bbox[0]) / 2 - 4,
                            y - total_h / 2 - 4,
                            x + (bbox[2] - bbox[0]) / 2 + 4,
                            y + total_h / 2 + 4)

    def _draw_facility_icons(self, scfg: dict) -> None:
        """在站点旁绘制设施语义符号，不替代换乘胶囊。"""
        res = self._schematic
        if res is None:
            return
        fcfg = scfg.get("facilities") or {}
        if not bool(fcfg.get("enabled", True)):
            return
        # 设施图标属于最终成图的视觉标记。以 1024px 预览为设计基准，
        # 随画布分辨率线性缩放，使 2048px 正式输出保持相同视觉占比。
        canvas_scale = max(min(self.ctx.image.size) / 1024.0, 0.5)
        size = max(int(round(_resolve_size(
            fcfg.get("size", {"fixed": 12, "scaled": 8}),
            self.render_scale) * canvas_scale)), 9)
        offset = max(int(round(_resolve_size(
            fcfg.get("offset", {"fixed": 10, "scaled": 7}),
            self.render_scale) * canvas_scale)), 7)
        seen = set()
        for gid, facility in self._station_facilities.items():
            if gid in seen or gid not in res.node_grid:
                continue
            seen.add(gid)
            px, py = self._schematic_to_px(*res.node_grid[gid])
            # 统一使用右上角邻近标识；图形采用 Rail Map Painter 的
            # 灰底圆 + 白色设施轮廓，保留站芯和换乘线色信息。
            cx, cy = int(px + offset), int(py - offset)
            facility_kind = getattr(facility, "kind", "other")
            facility_kind = getattr(facility_kind, "value", facility_kind)
            icon = _railmapgen_facility_icon(
                str(facility_kind).lower(), size)
            if self.ctx.shared.get("capture_rmp"):
                raw_name = getattr(facility.name, "text", str(facility.name))
                self._rmp_facilities.append({"station_id": str(gid), "kind": str(facility_kind).lower(),
                    "x": cx, "y": cy, "size": size, "name": raw_name,
                    "name_visible": False})
            self.ctx.image.paste(
                icon,
                (int(round(cx - icon.width / 2)),
                 int(round(cy - icon.height / 2))),
                icon,
            )


    def _draw_annotations(self, mapdata, cfg: dict) -> None:
        """信息附注卡：与标题/图例共用四角避让，不遮盖主要内容。"""
        acfg = cfg.get("annotations") or {}
        if not bool(acfg.get("enabled", True)):
            return
        lines = []
        if bool(acfg.get("disclaimer", True)):
            lines.append(i18n_tr("metro.annotation.disclaimer"))
        if (bool(acfg.get("scale_info", True)) and self._schematic is not None
                and self._schematic.fallback_strategy != "geographic"):
            res = self._schematic
            spacing = res.spacing_m or float(
                ((cfg.get("schematic") or {}).get("spacing", 400.0)))
            if res.adaptive_x is not None and res.adaptive_y is not None:
                low, high = res.adaptive_scale_range
                lines.append(i18n_tr(
                    "metro.annotation.scale_adaptive",
                    spacing=f"{spacing:g}",
                    low=f"{low:.2f}",
                    high=f"{high:.2f}",
                    strength=f"{res.adaptive_strength * 100:.0f}",
                    zoom=f"{self.render_scale:.2f}",
                ))
            else:
                lines.append(i18n_tr(
                    "metro.annotation.scale_fixed",
                    spacing=f"{spacing:g}",
                    zoom=f"{self.render_scale:.2f}",
                ))
        if bool(acfg.get("data_notice", True)):
            lines.append(i18n_tr("metro.annotation.data_notice"))
        if bool(acfg.get("generated_at", True)):
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            lines.append(i18n_tr("metro.annotation.generated", time=stamp))
        if bool(acfg.get("north_arrow", False)):
            # 示意图默认关闭，用户显式打开时仅提示画布上方为北；不附比例尺。
            lines.append(i18n_tr("metro.annotation.north"))
        credit_raw = acfg.get("credit", "")
        # credit: false 为 GUI 显式关闭；空串/缺省仍回退资源翻译表文案。
        if credit_raw is not False:
            credit = str(credit_raw or i18n_tr(
                "metro.annotation.credit")).strip()
            if credit:
                lines.append(credit)
        if not lines:
            return
        size = max(int(round(_resolve_size(
            acfg.get("size", {"fixed": 12, "scaled": 8}), self.render_scale))), 7)
        pad = max(int(round(_resolve_size(
            acfg.get("padding", {"fixed": 8, "scaled": 6}), self.render_scale))), 5)
        margin = max(int(round(_resolve_size(
            acfg.get("margin", {"fixed": 16, "scaled": 12}), self.render_scale))), 8)
        font = self._get_font(size)
        probe = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
        max_width = max(probe.textbbox((0, 0), value, font=font)[2] for value in lines)
        line_h = max(size + 3, int(size * 1.35))
        card_w, card_h = max_width + pad * 2, line_h * len(lines) + pad * 2
        w, h = self.ctx.image.size
        positions = {
            "left_bottom": (margin, h - card_h - margin),
            "right_bottom": (w - card_w - margin, h - card_h - margin),
            "right_top": (w - card_w - margin, margin),
            "left_top": (margin, margin),
        }
        preferred = str(acfg.get("position", "left_bottom"))
        candidates = [positions.get(preferred, positions["left_bottom"])] + [
            point for key, point in positions.items() if key != preferred]
        def overlaps(box, other):
            return other and not (box[2] < other[0] or box[0] > other[2]
                                  or box[3] < other[1] or box[1] > other[3])
        scored = []
        for x, y in candidates:
            box = (x, y, x + card_w, y + card_h)
            if overlaps(box, self._legend_rect) or overlaps(box, self._title_rect):
                continue
            occupancy = self._legend_occupancy(
                self.ctx.image, x, y, card_w, card_h)
            scored.append((occupancy, len(scored), x, y))
        # 候选顺序只作为同占用率时的偏好；优先选择线路、站名和站点
        # 实际像素最少的角落，避免附注卡盖住底部站名。
        if scored:
            _score, _order, x, y = min(scored)
        else:
            x, y = candidates[0]
        d = ImageDraw.Draw(self.ctx.image, "RGBA")
        d.rounded_rectangle((x, y, x + card_w, y + card_h), radius=5,
                            fill=(255, 255, 255, int(acfg.get("alpha", 222))),
                            outline=(152, 162, 179, 210), width=1)
        for i, value in enumerate(lines):
            d.text((x + pad, y + pad + i * line_h), value, font=font,
                   fill=(52, 64, 84, 255))


    def _line_color_count(self, lines) -> int:
        """返回逻辑服务线路数量；常规路径仅以 T02 分组标识判定，旧版颜色近似只用于私有渲染比较。"""
        if self._transit_model is not None:
            group_ids = {
                int(self._group_of[id(line)].id)
                for line in lines or () if id(line) in self._group_of
            }
            return max(len(group_ids), 1)
        colors: list = []
        for t in lines or ():
            td2 = transit.type_def(t.type)
            c2 = self._resolve_color(t, td2)
            if not any(_color_close(gc, c2) for gc in colors):
                colors.append(c2)
        return max(len(colors), 1)

    def _station_group_count(self, station, lines=None) -> int:
        """返回单个站点的语义分组数量；旧版路径可用 lines 回退，不凭颜色或视觉聚类推断换乘。"""
        if (self._transit_model is not None
                and self._station_fixes_active):
            return max(len(set(getattr(station, "serving_group_ids", ()))), 1)
        return self._line_color_count(lines if lines is not None
                                      else self._station_lines(station))

    def _cluster_lines(self, merged) -> dict:
        """簇代表组 → 途经线路列表（簇内全部成员线路并集，保序去重）。

        布局域近距站合并（P4）后，胶囊/站名大小按簇总逻辑线路数；
        未合并组不在返回中。
        """
        res = self._schematic
        if res is None or not res.cluster_of:
            return {}
        ms_by_id = {m.id: m for m in merged}
        members: dict = {}
        for g, r in res.cluster_of.items():
            members.setdefault(r, []).append(g)
        out: dict = {}
        for r, gids in members.items():
            lines: dict = {}      # id(line) → line（保序去重）
            for g in [r] + gids:
                m = ms_by_id.get(g)
                if m is not None:
                    for l in self._station_lines(m):
                        lines[id(l)] = l
            if lines:
                out[r] = list(lines.values())
        return out

    @staticmethod
    def _transfer_aspect(n_lines: int, scfg: dict) -> float:
        """兼容旧胶囊样式的长宽比（半长 / 半高）。

        参考 railmapgen 的做法：胶囊宽度随换乘线数温和增长，
        但设上限，避免复杂枢纽把站间走廊整段遮住。
        """
        base = max(float(scfg.get("transfer_scale", 2.4)), 1.4)
        per_line = max(float(scfg.get("transfer_per_line", 0.4)), 0.0)
        limit = max(float(scfg.get("transfer_max_scale", 4.0)), base)
        return min(base + per_line * max(n_lines - 2, 0), limit)

    @staticmethod
    def _transfer_is_circle(scfg: dict) -> bool:
        """是否使用重庆轨道交通式圆形换乘标记。"""
        design = str(scfg.get("transfer_design", "capsule")).strip().lower()
        return design in {"chongqing", "circle", "circular"}

    def _uses_chongqing_transfer_icon(self, scfg: dict) -> bool:
        """公共交通视图与地铁示意图统一使用当前圆形换乘图标。"""
        return self.transit_view or self._transfer_is_circle(scfg)

    def _build_metro_station_names(self, mapdata, merged, scfg: dict) -> dict:
        """只返回存档中真实存在的站名；无名站点保留结构但不生成文字。"""
        custom_flags: dict[int, bool] = {}

        def real_name(ms) -> str:
            display_name = getattr(ms, "name", "")
            value = getattr(display_name, "text", display_name) or ""
            value = str(value).strip()
            if not value and self._transit_model is None:
                value = next((str(getattr(stop, "name", "") or "").strip()
                              for stop in getattr(ms, "stops", ())
                              if str(getattr(stop, "name", "") or "").strip()),
                             "")
            custom_flags[ms.id] = bool(value)
            return value

        names = {ms.id: name for ms in merged if (name := real_name(ms))}
        self._metro_station_name_custom = custom_flags
        return names

    def _draw_station_names(self, scfg: dict) -> None:
        """全站名标注：换乘站优先，8 方向候选避让 + bbox 防重叠。"""
        ncfg = scfg.get("names") or {}
        if not bool(ncfg.get("enabled", True)):
            return
        merged = self._merged
        res = self._schematic
        if merged is None or res is None:
            return
        fsize = max(int(_resolve_size(
            ncfg.get("size", {"fixed": 13, "scaled": 13}),
            self.render_scale)), 8)
        if "font_size_per_1k" in ncfg:
            fsize = max(1, round(float(ncfg["font_size_per_1k"]) * min(self.ctx.image.size) / 1024))
        color = ncfg.get("color", "#4A4F5A")
        tcolor = ncfg.get("transfer_color", "#1A1D24")
        # 站名 halo（白描边压线可读）与换乘站名加粗（RMG 风格，YAML 可配）
        halo_on = bool(ncfg.get("halo", False))
        stroke_w = float(ncfg.get("stroke_width", 2.0))
        if "stroke_per_1k" in ncfg:
            stroke_w = float(ncfg["stroke_per_1k"]) * min(self.ctx.image.size) / 1024
        stroke_rgb = hex_to_rgb(ncfg.get("stroke_fill", "#FFFFFF"))
        bold_on = bool(ncfg.get("transfer_bold", False))
        adaptive_font = bool(ncfg.get("adaptive_font", True))
        try:
            min_font_scale = float(ncfg.get("adaptive_min_scale", 0.60))
        except (TypeError, ValueError):
            min_font_scale = 0.60
        min_font_scale = max(0.50, min(min_font_scale, 1.0))
        font_scales = (1.0,)
        if adaptive_font and min_font_scale < 0.999:
            font_scales = tuple(dict.fromkeys((
                1.0, max(0.86, min_font_scale),
                max(0.74, min_font_scale),
                max(0.64, min_font_scale), min_font_scale,
            )))
        probe = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
        d = ImageDraw.Draw(self.ctx.image)
        w, h = self.ctx.image.width, self.ctx.image.height
        try:
            readable_per_1k = float(
                ncfg.get("min_readable_size_per_1k", 10.0))
        except (TypeError, ValueError):
            readable_per_1k = 10.0
        readable_minimum = (max(1, round(fsize * .6)) if "font_size_per_1k" in ncfg else
                            _minimum_station_font_size(w, h, readable_per_1k))
        try:
            transfer_size_scale = float(
                ncfg.get("transfer_size_scale", 1.25))
        except (TypeError, ValueError):
            transfer_size_scale = 1.25
        transfer_size_scale = max(transfer_size_scale, 1.01)
        # 换乘站优先标注（信息量最大），后标普通站；
        # 布局域近距站簇（P4）：非代表组跳过（代表统一画并列名）
        # 颜色去重计数：同线双向不抬高优先级（普通站不被推迟）
        ordered = sorted(
            merged,
            key=lambda m: (
                -self._station_group_count(m, self._station_lines(m)),
                not self._metro_station_name_custom.get(m.id, False),
            ),
        )
        # 站芯也作为标签障碍物参与检测。旧逻辑只记录已放置文字，
        # 因而标签虽然彼此不重叠，却可能压在邻站圆/胶囊上。
        cl_lines = self._cluster_lines(merged)
        station_obstacles = []
        station_r = max(_resolve_size(
            scfg.get("size", {"fixed": 2.5, "scaled": 5.0}),
            self.render_scale), 1.0)
        station_hscale = max(
            float(scfg.get("transfer_height_scale", 0.78)), 0.5)
        transfer_is_circle = self._uses_chongqing_transfer_icon(scfg)
        for other in merged:
            other_cell = res.node_grid.get(other.id)
            if other_cell is None:
                continue
            other_rep = res.cluster_of.get(other.id)
            if other_rep is not None and other_rep != other.id:
                continue
            opx, opy = self._schematic_to_px(*other_cell)
            other_cl = cl_lines.get(other.id)
            other_lines = (other_cl if other_cl is not None
                           else self._station_lines(other))
            other_n = self._station_group_count(other, other_lines)
            other_r = self._station_radius_for_lines(
                station_r, other_lines, transfer_is_circle, other)
            if other_n >= 2:
                half_w = (other_r if transfer_is_circle else
                          other_r * self._transfer_aspect(other_n, scfg))
                half_h = (other_r if transfer_is_circle else
                          other_r * station_hscale)
            else:
                half_w = half_h = other_r
            station_obstacles.append((
                opx - half_w - 4.0, opy - half_h - 4.0,
                opx + half_w + 4.0, opy + half_h + 4.0))
        placed: list = list(station_obstacles)
        if self._title_rect is not None:
            placed.append(self._title_rect)

        # 设施标记固定在站点右上方并最后绘制；先预留它们的实际占位，
        # 防止站名先占住同一位置后再被机场/铁路/港口图标盖住。
        fcfg = scfg.get("facilities") or {}
        canvas_scale = max(min(self.ctx.image.size) / 1024.0, 0.5)
        facility_size = max(int(round(_resolve_size(
            fcfg.get("size", {"fixed": 12, "scaled": 8}),
            self.render_scale) * canvas_scale)), 9)
        facility_offset = max(int(round(_resolve_size(
            fcfg.get("offset", {"fixed": 10, "scaled": 7}),
            self.render_scale) * canvas_scale)), 7)
        for gid in self._station_facilities:
            cell = res.node_grid.get(gid)
            if cell is None:
                continue
            fpx, fpy = self._schematic_to_px(*cell)
            cx, cy = fpx + facility_offset, fpy - facility_offset
            half = facility_size / 2.0 + 2.0
            placed.append((cx - half, cy - half, cx + half, cy + half))

        # 线路也参与避让。文字仍可贴近线路，但候选框不能穿过线路色带；
        # 这样 halo 是可读性兜底，而不再承担“文字直接压线”的常态。
        route_obstacles = []
        seen_edges = set()
        line_pad = max(
            max((self._resolve_width(transit.type_def(tr.type))
                 for tr in self._tr_by_id.values()
                 if transit.type_def(tr.type) is not None), default=2.0)
            * self._halo_ratio / 2.0,
            2.0,
        )
        routes = list(res.routes.values())
        routes.extend(part for parts in res.route_parts.values() for part in parts)
        for route in routes:
            for a, b in zip(route, route[1:]):
                edge = edge_key(a, b)
                if edge in seen_edges:
                    continue
                seen_edges.add(edge)
                route_obstacles.append((self._schematic_to_px(*a),
                                        self._schematic_to_px(*b)))
        route_grid = self._segment_grid(route_obstacles)
        # 候选偏移：先在站点外留出可读间距，再尝试斜向/垂直位置；
        # 仅靠 6px 偏移会让密集站区的文字重新贴回站芯和线路。
        label_gap = max(float(ncfg.get("gap", 4.0)), 3.0)
        offs = [(1, 0, "lm"), (1, -1, "lm"), (1, 1, "lm"),
                (-1, 0, "rm"), (-1, -1, "rm"), (-1, 1, "rm"),
                (0, -1, "ms"), (0, 1, "mt")]
        preferred_position = str(ncfg.get("position", "auto"))
        if preferred_position == "left":
            offs.sort(key=lambda item: item[0] >= 0)
        elif preferred_position == "right":
            offs.sort(key=lambda item: item[0] <= 0)
        # 簇线路集（代表 → set(线路)）：胶囊大小按簇总线路数
        for ms in ordered:
            cell_ms = res.node_grid.get(ms.id)
            if cell_ms is None:
                continue
            rep = res.cluster_of.get(ms.id)
            if rep is not None and rep != ms.id:
                continue          # 簇成员：由代表统一标注
            names = res.cluster_names.get(ms.id)
            if names:
                name = "·".join(names[:3]) + ("…" if len(names) > 3 else "")
            else:
                name = self._metro_station_names.get(ms.id)
                if not name:
                    display_name = getattr(ms, "name", "")
                    name = getattr(display_name, "text", display_name) or None
            if not name:
                continue
            from ..romanize import bilingual_text, clean_station_name
            if bool(ncfg.get("strip_player_station_suffix", True)):
                name = clean_station_name(name)
            name = bilingual_text(name, str(ncfg.get("language", "zh")))
            px, py = self._schematic_to_px(*cell_ms)
            if not (0 <= px <= w and 0 <= py <= h):
                continue
            # 视觉线路数（分组/颜色去重：同线双向计 1；簇并集优先）
            cl = cl_lines.get(ms.id)
            n_lines = self._station_group_count(
                ms, cl if cl is not None else self._station_lines(ms))
            manual_type = self._station_type_overrides.get(ms.id)
            is_transfer = (manual_type == "interchange" or (
                manual_type not in {"normal", "terminal"}
                and n_lines >= 2))
            base_r = max(_resolve_size(
                scfg.get("size", {"fixed": 2.5, "scaled": 2.5}),
                self.render_scale), 1.0)
            angle = self._station_angle(ms.id) if is_transfer else 0.0
            aspect = (
                1.0 if self._transfer_is_circle(scfg)
                else self._transfer_aspect(n_lines, scfg)
            ) if is_transfer else 1.0
            height_scale = max(
                float(scfg.get("transfer_height_scale", 0.78)), 0.5)
            selected = None
            station_font_scales = (1.0,) if is_transfer else font_scales
            # 先用用户设定字号找空位；只有全部候选均冲突时才逐级缩小。
            # 最小字号仍无空位时，沿用冲突最小方向兜底，保证站名不消失。
            for scale_index, font_scale in enumerate(station_font_scales):
                current_size = _resolved_station_font_size(
                    fsize, font_scale, readable_minimum, is_transfer,
                    transfer_size_scale)
                secondary_size = max(int(round(
                    current_size * float(ncfg.get(
                        "bilingual_english_scale", 0.72)))),
                    int(math.ceil(readable_minimum * float(ncfg.get(
                        "bilingual_english_scale", 0.72)))), 7)
                use_bold = bold_on and is_transfer
                f = self._get_font(current_size, bold=use_bold)
                sf = self._get_font(secondary_size, bold=use_bold)
                # Pillow 的 text anchor 不支持多行文本；先在原点量出实际
                # 文本框，再按锚点语义换算左上角。
                if "\n" in name:
                    primary, secondary = name.split("\n", 1)
                    primary_bb = probe.textbbox((0, 0), primary, font=f)
                    secondary_bb = probe.textbbox((0, 0), secondary, font=sf)
                    line_spacing = max(2, secondary_size // 5)
                    primary_h = primary_bb[3] - primary_bb[1]
                    secondary_h = secondary_bb[3] - secondary_bb[1]
                    text_w = max(primary_bb[2] - primary_bb[0],
                                 secondary_bb[2] - secondary_bb[0])
                    text_h = primary_h + line_spacing + secondary_h
                    metrics = (primary, secondary, primary_bb, secondary_bb,
                               primary_h, line_spacing)
                else:
                    text_bb = probe.textbbox((0, 0), name, font=f)
                    text_w = text_bb[2] - text_bb[0]
                    text_h = text_bb[3] - text_bb[1]
                    metrics = (text_bb,)
                candidates = []
                for distance_scale in (1.0, 1.65, 2.3, 3.1):
                    for direction_rank, (dx, dy, anchor) in enumerate(offs):
                        norm = math.hypot(dx, dy) or 1.0
                        ux, uy = dx / norm, dy / norm
                        ar = math.radians(angle)
                        major = (math.cos(ar), math.sin(ar))
                        minor = (-major[1], major[0])
                        half_w = base_r * aspect
                        half_h = (base_r * height_scale
                                  if is_transfer else base_r)
                        support = (
                            abs(ux * major[0] + uy * major[1]) * half_w
                            + abs(ux * minor[0] + uy * minor[1]) * half_h
                        )
                        distance = support + label_gap * distance_scale
                        tx = px + ux * distance
                        ty = py + uy * distance
                        ax, ay = anchor[0], anchor[1]
                        left = tx if ax == "l" else (
                            tx - text_w if ax == "r" else tx - text_w / 2)
                        top = ty - text_h / 2 if ay == "m" else (
                            ty - text_h if ay == "s" else ty)
                        # 边缘站点仍参与方向评分，但先把文字框收回画布内。
                        if text_w <= w - 4:
                            left = min(max(left, 2.0), w - 2.0 - text_w)
                        if text_h <= h - 4:
                            top = min(max(top, 2.0), h - 2.0 - text_h)
                        bb = (left, top, left + text_w, top + text_h)
                        candidates.append((left, top, bb, distance_scale,
                                           direction_rank))
                chosen = self._choose_label_candidate(
                    candidates, placed, route_grid, line_pad, (w, h),
                    allow_collision=(
                        scale_index == len(station_font_scales) - 1))
                if chosen is not None:
                    selected = (chosen, f, sf, metrics)
                    break
            if selected is None:
                continue  # 仅在无法生成任何候选框时发生
            chosen, f, sf, metrics = selected
            tx, ty, bb = chosen
            if self.ctx.shared.get("capture_rmp"):
                self._rmp_labels[str(ms.id)] = {"x": tx, "y": ty, "font_size": int(f.size), "text": name}
            placed.append((bb[0] - 2, bb[1] - 1, bb[2] + 2, bb[3] + 1))
            kw = ({"stroke_width": stroke_w,
                   "stroke_fill": stroke_rgb + (255,)} if halo_on else {})
            fill = tcolor if is_transfer else color
            if "\n" in name:
                (primary, secondary, primary_bb, secondary_bb,
                 primary_h, line_spacing) = metrics
                d.text((tx - primary_bb[0], ty - primary_bb[1]),
                       primary, font=f, fill=fill, **kw)
                d.text((tx - secondary_bb[0],
                        ty + primary_h + line_spacing - secondary_bb[1]), secondary,
                       font=sf, fill=fill, **kw)
            else:
                text_bb = metrics[0]
                d.text((tx - text_bb[0], ty - text_bb[1]),
                       name, font=f, fill=fill, **kw)

    @staticmethod
    def _bbox_hit(bb, placed, pad: float = 2.0) -> bool:
        """bbox 与已放标签是否相交。"""
        for p in placed:
            if not (bb[2] + pad < p[0] or p[2] + pad < bb[0]
                    or bb[3] + pad < p[1] or p[3] + pad < bb[1]):
                return True
        return False

    @staticmethod
    def _bbox_overlap_area(a, b) -> float:
        """两个矩形的重叠面积。"""
        width = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
        height = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
        return width * height

    @staticmethod
    def _bbox_distance(a, b) -> float:
        """两个轴对齐矩形之间的最短距离（相交时为 0）。"""
        dx = max(b[0] - a[2], a[0] - b[2], 0.0)
        dy = max(b[1] - a[3], a[1] - b[3], 0.0)
        return math.hypot(dx, dy)

    @staticmethod
    def _point_bbox_distance(point, bb) -> float:
        """点到轴对齐矩形的最短距离。"""
        x, y = point
        dx = max(bb[0] - x, x - bb[2], 0.0)
        dy = max(bb[1] - y, y - bb[3], 0.0)
        return math.hypot(dx, dy)

    @staticmethod
    def _point_segment_distance(point, a, b) -> float:
        """点到线段的最短距离。"""
        vx, vy = b[0] - a[0], b[1] - a[1]
        length_sq = vx * vx + vy * vy
        if length_sq <= 1e-12:
            return math.hypot(point[0] - a[0], point[1] - a[1])
        t = ((point[0] - a[0]) * vx + (point[1] - a[1]) * vy) / length_sq
        t = min(max(t, 0.0), 1.0)
        return math.hypot(point[0] - (a[0] + t * vx),
                          point[1] - (a[1] + t * vy))

    @classmethod
    def _bbox_segment_distance(cls, bb, a, b, pad: float = 0.0) -> float:
        """文字框到线路色带的近似净空；相交时为 0。"""
        expanded = (bb[0] - pad, bb[1] - pad,
                    bb[2] + pad, bb[3] + pad)
        if cls._bbox_segment_hit(bb, a, b, pad):
            return 0.0
        corners = ((expanded[0], expanded[1]), (expanded[2], expanded[1]),
                   (expanded[2], expanded[3]), (expanded[0], expanded[3]))
        return min(
            cls._point_bbox_distance(a, expanded),
            cls._point_bbox_distance(b, expanded),
            *(cls._point_segment_distance(corner, a, b)
              for corner in corners),
        )

    @classmethod
    def _choose_label_candidate(cls, candidates, placed, route_grid,
                                line_pad: float, canvas_size,
                                allow_collision: bool = True):
        """按周围净空选站名方向；全被占用时返回冲突最小的位置。"""
        if not candidates:
            return None
        canvas_w, canvas_h = canvas_size
        evaluated = []
        for left, top, bb, distance_scale, direction_rank in candidates:
            text_area = max((bb[2] - bb[0]) * (bb[3] - bb[1]), 1.0)
            obstacle_cost = 0.0
            clearances = [max(0.0, min(
                bb[0], bb[1], canvas_w - bb[2], canvas_h - bb[3]))]
            for obstacle in placed:
                expanded = (obstacle[0] - 2.0, obstacle[1] - 2.0,
                            obstacle[2] + 2.0, obstacle[3] + 2.0)
                if cls._bbox_hit(bb, (obstacle,)):
                    # 即使只是边缘接触，也保留一个非零代价，避免误判为空位。
                    obstacle_cost += max(
                        cls._bbox_overlap_area(bb, expanded), 1.0)
                clearances.append(cls._bbox_distance(bb, expanded))
            line_hits = 0
            for a, b in cls._segments_near(route_grid, bb, line_pad):
                if cls._bbox_segment_hit(bb, a, b, line_pad):
                    line_hits += 1
                clearances.append(
                    cls._bbox_segment_distance(bb, a, b, line_pad))
            collision_cost = obstacle_cost + line_hits * text_area * 1.25
            clearance = min(clearances) if clearances else 0.0
            evaluated.append((collision_cost, clearance, distance_scale,
                              direction_rank, left, top, bb))

        free = [item for item in evaluated if item[0] <= 1e-9]
        if free:
            # 每个方向只保留最近的空位，避免为了多一点净空把站名推得过远；
            # 再在这些方向中选择四周最空旷的一个。
            nearest_by_direction = {}
            for item in free:
                direction_rank = item[3]
                previous = nearest_by_direction.get(direction_rank)
                if previous is None or item[2] < previous[2]:
                    nearest_by_direction[direction_rank] = item
            best = max(nearest_by_direction.values(),
                       key=lambda item: (item[1], -item[2], -item[3]))
        else:
            if not allow_collision:
                return None
            # 密集区没有绝对空位时不再隐藏文字：优先减少遮挡面积和穿线，
            # 同等冲突下选择净空更大、离站点更近的方向。
            best = min(evaluated,
                       key=lambda item: (item[0], -item[1], item[2], item[3]))
        return best[4], best[5], best[6]

    @staticmethod
    def _segment_grid(segments, cell_size: float = 64.0):
        grid = {}
        for segment in segments:
            (x0, y0), (x1, y1) = segment
            for cx in range(math.floor(min(x0, x1) / cell_size),
                            math.floor(max(x0, x1) / cell_size) + 1):
                for cy in range(math.floor(min(y0, y1) / cell_size),
                                math.floor(max(y0, y1) / cell_size) + 1):
                    grid.setdefault((cx, cy), []).append(segment)
        return cell_size, grid

    @staticmethod
    def _segments_near(route_grid, bb, pad: float):
        cell_size, grid = route_grid
        result = set()
        for cx in range(math.floor((bb[0] - pad) / cell_size),
                        math.floor((bb[2] + pad) / cell_size) + 1):
            for cy in range(math.floor((bb[1] - pad) / cell_size),
                            math.floor((bb[3] + pad) / cell_size) + 1):
                result.update(grid.get((cx, cy), ()))
        return result

    @staticmethod
    def _bbox_segment_hit(bb, a, b, pad: float = 0.0) -> bool:
        """线段是否穿过扩张后的文字框（Liang-Barsky 裁剪判定）。"""
        left, top, right, bottom = (
            bb[0] - pad, bb[1] - pad, bb[2] + pad, bb[3] + pad)
        x0, y0 = a
        dx, dy = b[0] - x0, b[1] - y0
        t0, t1 = 0.0, 1.0
        for p, q in ((-dx, x0 - left), (dx, right - x0),
                     (-dy, y0 - top), (dy, bottom - y0)):
            if abs(p) < 1e-12:
                if q < 0:
                    return False
                continue
            ratio = q / p
            if p < 0:
                if ratio > t1:
                    return False
                t0 = max(t0, ratio)
            else:
                if ratio < t0:
                    return False
                t1 = min(t1, ratio)
        return t0 <= t1

    def _draw_transit_station_names(self, scfg: dict) -> None:
        """从标准模型绘制普通交通站名，并按确定性四方向规则避让。"""
        ncfg = scfg.get("names") or scfg.get("station_names") or {}
        if not bool(ncfg.get("enabled", True)) or self._merged is None:
            return
        try:
            size = max(int(round(_resolve_size(
                ncfg.get("size", ncfg.get("font_size", 11)),
                self.render_scale))), 6 if self.transit_view else 8)
        except (TypeError, ValueError):
            size = 11
        font = self._get_font(size, bold=bool(ncfg.get("bold", False)))
        color = str(ncfg.get("color", "#344054"))
        stroke_width = max(0, int(round(float(ncfg.get(
            "stroke_width", 1 if ncfg.get("halo", True) else 0)))))
        stroke_fill = str(ncfg.get("stroke_fill", "#FFFFFF"))
        draw = ImageDraw.Draw(self.ctx.image)
        base_radius = _resolve_size(
            scfg.get("size", {"fixed": 2.5, "scaled": 5.0}),
            self.render_scale)
        gap = max(float(ncfg.get("gap", 5.0)), 1.0)
        placed = []
        candidates = ((1.0, 0.0), (-1.0, 0.0), (0.0, -1.0), (0.0, 1.0))
        stations = sorted(
            self._merged,
            key=lambda item: (
                round(float(getattr(item.position, "z", 0.0)), 4)
                if item.position is not None else float("inf"),
                round(float(getattr(item.position, "x", 0.0)), 4)
                if item.position is not None else float("inf"),
                str(item.id),
            ),
        )
        for station in stations:
            position = getattr(station, "position", None)
            name_obj = getattr(station, "name", "")
            text = str(getattr(name_obj, "text", name_obj) or "").strip()
            if position is None or not text:
                continue
            px, py = self.ctx.transform(position.x, position.z)
            if not (-size <= px <= self.ctx.image.width + size
                    and -size <= py <= self.ctx.image.height + size):
                continue
            is_transfer = self._is_transfer_station(station)
            label_font = font if not is_transfer else self._get_font(
                max(size, int(round(size * float(ncfg.get(
                    "transfer_size_scale", 1.0))))),
                bold=bool(ncfg.get("transfer_bold", False)),
            )
            label_color = str(ncfg.get(
                "transfer_color", color)) if is_transfer else color
            for dx, dy in candidates:
                anchor_x = px + dx * (base_radius + gap)
                anchor_y = py + dy * (base_radius + gap)
                bbox = draw.textbbox(
                    (anchor_x, anchor_y), text, font=label_font,
                    anchor="lm", stroke_width=stroke_width,
                )
                if not self._bbox_hit(bbox, placed, pad=2.0):
                    draw.text(
                        (anchor_x, anchor_y), text, font=label_font,
                        fill=hex_to_rgb(label_color) + (255,), anchor="lm",
                        stroke_width=stroke_width,
                        stroke_fill=hex_to_rgb(stroke_fill) + (255,),
                    )
                    placed.append(bbox)
                    break

    def _draw_stations(self, mapdata, enabled: list, scfg: dict) -> None:
        """合并站点渲染：白底图形 + halo描边 + 换乘站多色tick。

        参考 railmapgen 站点渲染模式：
        - 普通站：白色圆 + halo白描边 + 深色描边
        - 换乘站：重庆式白底圆形标记 + 双向换乘箭头
        - 抽象模式下站点位置直接用布局像素（与线路几何同源）

        注意：aggdraw.flush() 会覆盖 ImageDraw 操作，因此必须先画
        aggdraw 内容、flush 后再画 ImageDraw。
        """
        trs = [t for t in self._render_lines
               if (transit.normalize_type(t.type) or t.type) in enabled]
        if not trs:
            return
        merged = self._merged
        if merged is None:
            return
        radius = _resolve_size(scfg.get("size", {"fixed": 2.5, "scaled": 5.0}),
                               self.render_scale)
        if "diameter_per_1k" in scfg:
            radius = float(scfg["diameter_per_1k"]) * min(self.ctx.image.size) / 2048
        outline = scfg.get("outline", "#3A3F4A")
        fill = scfg.get("fill", "#FFFFFF")
        # 站点设计：rmg = railmapgen 经典式（白底 + 线路色描边，无中心色点）
        design = str(scfg.get("design", ""))
        # 换乘站描边色（缺省沿用普通描边色）；旧胶囊样式仍支持分色刻度
        t_outline = scfg.get("transfer_outline", outline)
        # 公共交通视图不再使用主题里的旧胶囊；与地铁示意图复用同一
        # ChongqingRTIntStation SVG。普通地图仍尊重主题配置。
        transfer_is_circle = self._uses_chongqing_transfer_icon(scfg)
        ticks_on = bool(scfg.get("ticks", True))
        border = max(
            int(_resolve_size(scfg.get("border_width", 1.0),
                              self.render_scale) * 2), 1)
        transfer_halo = max(float(scfg.get("transfer_halo", 2.0)), 0.0)
        transfer_height_scale = max(
            float(scfg.get("transfer_height_scale", 0.78)), 0.5)
        tick_height = max(int(_resolve_size(
            scfg.get("tick_height", {"fixed": 4, "scaled": 4}),
            self.render_scale)), 2)
        w, h = self.ctx.image.width, self.ctx.image.height
        grid = self._schematic

        # 第一阶段：收集所有站点信息
        st_info = []  # 站点绘制参数依次为位置、半径、线路数、颜色、换乘状态、角度和偏移。
        cl_lines_st = self._cluster_lines(merged) \
            if grid is not None else None
        for ms in merged:
            if grid is not None and ms.id in grid.node_grid:
                px, py = self._schematic_to_px(*grid.node_grid[ms.id])
            elif ms.position is not None:
                px, py = self.ctx.transform(ms.position.x, ms.position.z)
            else:
                continue
            if not (0 <= px <= w and 0 <= py <= h):
                continue
            if grid is not None and grid.cluster_of.get(ms.id, ms.id) != ms.id:
                continue
            if cl_lines_st is not None:
                cl = cl_lines_st.get(ms.id)
                lines_for_st = (cl if cl is not None
                                else self._station_lines(ms))
            else:
                lines_for_st = self._station_lines(ms)
            r = self._station_radius_for_lines(
                radius, lines_for_st, transfer_is_circle, ms)
            n_lines = self._station_group_count(ms, lines_for_st)
            line_colors = []
            seen_c = set()
            for l in lines_for_st:
                td2 = transit.type_def(l.type)
                c = self._resolve_color(l, td2)
                if c not in seen_c:
                    seen_c.add(c)
                    line_colors.append(c)
            if self._transit_model is not None and self._station_fixes_active:
                is_interchange = self._is_transfer_station(ms)
            elif cl_lines_st is None or ms.id not in cl_lines_st:
                is_interchange = self._is_transfer_station(ms)
            else:
                is_interchange = self._line_color_count(lines_for_st) >= 2
            angle = 0.0
            sx = 1.0
            station_type = self._station_type_overrides.get(ms.id, "auto")
            if is_interchange:
                sx = (1.0 if transfer_is_circle
                      else self._transfer_aspect(n_lines, scfg))
                angle = (self._station_angle(ms.id)
                         if grid is not None else 0.0)
            elif station_type == "terminal" and grid is not None:
                angle = self._station_angle(ms.id)
            # 步骤3：站点形状差异化——取首条线路的类型 key 作为主类型
            primary_type = ""
            has_track = False
            if lines_for_st:
                first_td = transit.type_def(lines_for_st[0].type)
                if first_td is not None:
                    primary_type = first_td.key
                    has_track = bool(first_td.track_flag)
            st_info.append((px, py, r, n_lines, line_colors,
                            is_interchange, angle, sx,
                            primary_type, has_track, station_type))

        # 第二阶段：旧胶囊样式继续用 aggdraw；重庆式圆形标记在下一阶段
        # 用 ImageDraw 绘制，以便叠加清晰的双向箭头。
        if not transfer_is_circle and any(s[5] for s in st_info):
            agg = aggdraw.Draw(self.ctx.image)
            halo_brush = aggdraw.Brush("#FFFFFF")
            outline_brush = aggdraw.Brush(t_outline)
            fill_brush = aggdraw.Brush(fill)
            for info in st_info:
                px, py, r, n_lines, line_colors, is_int, angle, sx = info[:8]
                if not is_int:
                    continue
                ry = r * transfer_height_scale
                pts_halo = _capsule_points(
                    px, py, r * sx + transfer_halo,
                    ry + transfer_halo, angle)
                agg.polygon([c for p in pts_halo for c in p],
                            None, halo_brush)
                pts_outer = _capsule_points(px, py, r * sx, ry, angle)
                agg.polygon([c for p in pts_outer for c in p],
                            None, outline_brush)
                inner_r = max(ry - border, 1.0)
                inner_half_len = max(r * sx - border, inner_r)
                pts_inner = _capsule_points(
                    px, py, inner_half_len, inner_r, angle)
                agg.polygon([c for p in pts_inner for c in p],
                            None, fill_brush)
            agg.flush()

        # 第三阶段：图标单独以高分辨率局部画布绘制，再缩回原尺寸。
        # 不能为整张 4K 图创建 4 倍画布，否则会造成不必要的内存峰值。
        icon_aa = max(int(scfg.get("icon_antialias", 4)), 1)
        image_w, image_h = self.ctx.image.size

        def composite_icon(bounds, painter):
            x0 = max(0, int(math.floor(bounds[0])))
            y0 = max(0, int(math.floor(bounds[1])))
            x1 = min(image_w, int(math.ceil(bounds[2])))
            y1 = min(image_h, int(math.ceil(bounds[3])))
            if x1 <= x0 or y1 <= y0:
                return
            patch = Image.new(
                "RGBA", ((x1 - x0) * icon_aa, (y1 - y0) * icon_aa),
                (0, 0, 0, 0),
            )
            painter(ImageDraw.Draw(patch), x0, y0, icon_aa)
            if icon_aa > 1:
                patch = patch.resize((x1 - x0, y1 - y0),
                                     Image.Resampling.LANCZOS)
            self.ctx.image.alpha_composite(patch, (x0, y0))

        for info in st_info:
            px, py, r, n_lines, line_colors, is_int, angle, sx = info[:8]
            primary_type = info[8] if len(info) > 8 else ""
            has_track = info[9] if len(info) > 9 else True
            station_type = info[10] if len(info) > 10 else "auto"
            if is_int:
                if transfer_is_circle:
                    extent = r + transfer_halo + 2

                    def paint_transfer(d, x0, y0, aa):
                        self._draw_chongqing_interchange(
                            d, (px - x0) * aa, (py - y0) * aa,
                            r * aa, fill, t_outline, transfer_halo * aa,
                            max(int(round(border * aa)), 1),
                            scfg.get("transfer_arrow", t_outline))

                    composite_icon(
                        (px - extent, py - extent, px + extent, py + extent),
                        paint_transfer)
                elif ticks_on:
                    extent = r * sx + tick_height + 2

                    def paint_ticks(d, x0, y0, aa):
                        self._draw_interchange_ticks(
                            d, (px - x0) * aa, (py - y0) * aa,
                            r * aa, sx, angle, line_colors,
                            tick_height * aa,
                            max(int(round(border * aa)), 1))

                    composite_icon(
                        (px - extent, py - extent, px + extent, py + extent),
                        paint_ticks)
            else:
                extent = r + 4

                def paint_station(d, x0, y0, aa):
                    self._draw_station_marker(
                        d, (px - x0) * aa, (py - y0) * aa, r * aa,
                        fill, outline, max(int(round(border * aa)), 1),
                        line_colors, design, has_track, 2 * aa)
                    if station_type == "terminal":
                        cx, cy = (px - x0) * aa, (py - y0) * aa
                        length = max(r * 0.62 * aa, 2.0)
                        rad = math.radians(angle + 90.0)
                        dx, dy = math.cos(rad) * length, math.sin(rad) * length
                        d.line((cx - dx, cy - dy, cx + dx, cy + dy),
                               fill=outline,
                               width=max(int(round(border * aa)), 2))

                composite_icon(
                    (px - extent, py - extent, px + extent, py + extent),
                    paint_station)

    def _draw_station_marker(self, d, px, py, r, fill, outline, border,
                             line_colors, design, has_track, halo) -> None:
        """在给定分辨率的局部画布上绘制普通站点图标。"""
        if design == "rmg":
            # 直接使用仓库中的 ChongqingRTBasicStation 几何；仅替换
            # currentColor 为主线路色，保持原始 SVG 形状不变。
            marker_color = line_colors[0] if line_colors else outline
            icon = _rmp_icon_bitmap(
                "chongqingrt-basic", max(int(round(2 * r)), 1),
                color=marker_color,
            )
            if _is_light_color(marker_color,
                               self._light_outline_threshold):
                icon = _outlined_icon(
                    icon, self._light_outline_color,
                    width=max(int(round(r * 0.12)), 1))
            _paste_centered_icon(d, icon, px, py)
            return
        elif has_track:
            # Metro/Train：标准圆形
            d.ellipse((px - r - halo, py - r - halo,
                       px + r + halo, py + r + halo),
                      fill="#FFFFFF", outline=None)
            d.ellipse((px - r, py - r, px + r, py + r),
                      fill=fill, outline=outline, width=border)
            if line_colors:
                dot_r = max(int(r * 0.35), max(int(halo * 0.5), 1))
                dot_outline = (
                    self._light_outline_color
                    if _is_light_color(line_colors[0],
                                       self._light_outline_threshold)
                    else None)
                d.ellipse((px - dot_r, py - dot_r,
                           px + dot_r, py + dot_r),
                          fill=line_colors[0], outline=dot_outline,
                          width=1)
        else:
            # Tram/Monorail/Bus：圆角方形外框
            sr = r * 0.75
            d.rounded_rectangle(
                (px - sr - halo, py - sr - halo,
                 px + sr + halo, py + sr + halo),
                radius=max(int(halo), int(sr * 0.4)),
                fill="#FFFFFF", outline=None)
            d.rounded_rectangle(
                (px - sr, py - sr, px + sr, py + sr),
                radius=max(int(halo * 0.5), int(sr * 0.4)),
                fill=fill, outline=outline, width=border)
            if line_colors:
                dot_r = max(int(sr * 0.35), max(int(halo * 0.35), 1))
                dot_outline = (
                    self._light_outline_color
                    if _is_light_color(line_colors[0],
                                       self._light_outline_threshold)
                    else None)
                d.ellipse((px - dot_r, py - dot_r,
                           px + dot_r, py + dot_r),
                          fill=line_colors[0], outline=dot_outline,
                          width=1)

    @staticmethod
    def _draw_chongqing_interchange(d, px, py, r, fill, outline,
                                    halo, border, arrow_color) -> None:
        """贴入仓库中的 ChongqingRTIntStation 原始 SVG 图标。"""
        del fill, outline, halo, border, arrow_color
        icon = _rmp_icon_bitmap("chongqingrt-int", max(int(round(r * 2)), 1))
        _paste_centered_icon(d, icon, px, py)

    def _draw_interchange_ticks(self, d, px, py, r, sx, angle_deg,
                                 line_colors, tick_height, border) -> None:
        """换乘站分色tick标记：沿胶囊全长均匀分布，每线一色。

        参考 railmapgen interchange-tick 设计，将 N 条线路的刻度
        沿胶囊长轴均匀分布，每个刻度用对应线路颜色，方向垂直于胶囊。
        """
        n = len(line_colors)
        if n == 0:
            return
        a = math.radians(angle_deg)
        # 垂直于线路方向的单位向量（tick 绘制方向）
        nx, ny = -math.sin(a), math.cos(a)
        # 沿线路方向的单位向量（tick 分布方向）
        tx, ty = math.cos(a), math.sin(a)
        half_len = r * sx  # 胶囊半长
        # 将 N 个 tick 均匀分布在胶囊全长上
        capsule_len = half_len * 2  # 胶囊全长
        if n == 1:
            positions = [0.0]  # 单线居中
        else:
            # 均匀分布：首尾距边缘半个间距
            step = capsule_len / n
            positions = [-half_len + step * (i + 0.5) for i in range(n)]
        for i, color in enumerate(line_colors[:n]):
            # tick 中心在胶囊长轴上的位置
            offset_along = positions[i]
            cx = px + tx * offset_along
            cy = py + ty * offset_along
            # tick 两端沿法向延伸
            x1 = cx + nx * tick_height * 0.5
            y1 = cy + ny * tick_height * 0.5
            x2 = cx - nx * tick_height * 0.5
            y2 = cy - ny * tick_height * 0.5
            tick_rgb = hex_to_rgb(color)
            tick_width = max(2, border)
            if _is_light_color(color, self._light_outline_threshold):
                d.line([(x1, y1), (x2, y2)],
                       fill=hex_to_rgb(self._light_outline_color) + (255,),
                       width=tick_width + 2)
            d.line([(x1, y1), (x2, y2)],
                   fill=tick_rgb + (255,),
                   width=tick_width)

    def _draw_line_labels(self, tr, smooth: list, cfg: dict, placed: list) -> None:
        if len(smooth) < 2:
            return
        # 采样间距（像素，分辨率自适应）：与徽章字号同比例随 render_scale 缩放，
        # 保持"每条线路固定数量徽章、徽章间屏幕间距恒定"——否则高分辨率下
        # 线路像素变长而间隔不变，沿线徽章数量线性暴增、市中心挤成一团。
        # 与 label_layer._draw_road_names 的 spacing*render_scale 处理一致。
        interval_px = float(cfg.get("repeat_interval", 600)) * self.render_scale
        min_gap = float(cfg.get("min_gap", 350)) * self.render_scale
        max_per_line = int(cfg.get("max_per_line", 4))
        line_color = self._resolve_color(tr, transit.type_def(tr.type))
        rgb = hex_to_rgb(line_color)

        # 累计弧长
        cum = [0.0]
        for i in range(1, len(smooth)):
            cum.append(
                cum[-1] + math.hypot(smooth[i][0] - smooth[i - 1][0], smooth[i][1] - smooth[i - 1][1])
            )
        total = cum[-1]
        if total <= 0:
            return

        targets = [cum[0]]
        while targets[-1] + interval_px <= total - 1:
            targets.append(targets[-1] + interval_px)
        # 线路中点也放一个
        mid = total / 2.0
        if all(abs(t - mid) > interval_px * 0.4 for t in targets):
            targets.append(mid)
        targets.sort()

        count = 0
        for t in targets:
            if count >= max_per_line:
                break
            i = 0
            while i < len(cum) - 2 and cum[i + 1] < t:
                i += 1
            p0, p1 = smooth[i], smooth[i + 1]
            tt = (t - cum[i]) / max(cum[i + 1] - cum[i], 1e-9)
            px = p0[0] + (p1[0] - p0[0]) * tt
            py = p0[1] + (p1[1] - p0[1]) * tt
            if not (0 <= px <= self.ctx.image.width and 0 <= py <= self.ctx.image.height):
                continue
            # 与已放置的同名徽章间距检查
            if any(math.hypot(px - qx, py - qy) < min_gap for qx, qy in placed):
                continue
            # 该点切线方向（smooth 为 transform 旋转后的像素点列，
            # angle 已是屏幕视觉方向，无需再补偿输出旋转量）
            i0, i1 = max(0, i - 1), min(len(smooth) - 1, i + 2)
            angle = math.degrees(
                math.atan2(smooth[i1][1] - smooth[i0][1], smooth[i1][0] - smooth[i0][0])
            )
            badge = self._make_badge(tr.name, rgb, cfg)
            if badge is None:
                continue
            # 徽章文字沿线路视觉方向保持正立可读（normalize_angle 归一化防倒置）
            img = badge.rotate(
                -normalize_angle(angle),
                expand=True,
                resample=Image.BICUBIC,
            )
            x = int(round(px - img.width / 2))
            y = int(round(py - img.height / 2))
            self.ctx.image.paste(img, (x, y), img)
            placed.append((px, py))
            count += 1

    # ---- 公共交通图：重合线路拼接标签（参考逆向 MergedLineNumbering） ----

    def _label_candidates(self, tr, smooth: list, cfg: dict) -> list:
        """单线路的沿线标签候选点 [(px, py, name, rgb), ...]（公共交通图用）。"""
        if len(smooth) < 2 or not tr.name:
            return []
        interval_px = float(cfg.get("repeat_interval", 600)) * self.render_scale
        max_per_line = int(cfg.get("max_per_line", 4))
        candidate_interval = min(
            interval_px,
            max(float(cfg.get("candidate_interval", 120.0))
                * float(cfg.get("layout_scale", self.render_scale)), 24.0),
        )
        candidate_limit = max(
            int(cfg.get("candidate_limit", max_per_line * 6)),
            max_per_line,
        )
        line_color = self._resolve_color(tr, transit.type_def(tr.type))
        rgb = hex_to_rgb(line_color)
        # 累计弧长
        cum = [0.0]
        for i in range(1, len(smooth)):
            cum.append(cum[-1] + math.hypot(
                smooth[i][0] - smooth[i - 1][0], smooth[i][1] - smooth[i - 1][1]))
        total = cum[-1]
        if total <= 0:
            return []
        # 候选采样比最终重复间隔更密；最终数量与间距在全局净空评分后决定。
        # 从半个步长起采样，避免默认把超长徽章压在线路端点/站点上。
        targets = []
        if total <= candidate_interval:
            targets.append(total / 2.0)
        else:
            target = candidate_interval * 0.5
            while target < total and len(targets) < candidate_limit:
                targets.append(target)
                target += candidate_interval
        mid = total / 2.0
        if all(abs(t - mid) > candidate_interval * 0.35 for t in targets):
            targets.append(mid)
        targets.sort()
        out = []
        for t in targets[:candidate_limit]:
            i = 0
            while i < len(cum) - 2 and cum[i + 1] < t:
                i += 1
            p0, p1 = smooth[i], smooth[i + 1]
            tt = (t - cum[i]) / max(cum[i + 1] - cum[i], 1e-9)
            px = p0[0] + (p1[0] - p0[0]) * tt
            py = p0[1] + (p1[1] - p0[1]) * tt
            if not (0 <= px <= self.ctx.image.width and 0 <= py <= self.ctx.image.height):
                continue
            out.append((px, py, tr.name, rgb))
        return out

    @staticmethod
    def _line_label_obstacle_score(bb, obstacle_points, canvas_size,
                                   clearance_padding: float = 12.0):
        """标签框对站点/交通 POI 与画布边缘的冲突和净空评分。"""
        width, height = canvas_size
        outside = (
            max(0.0, -bb[0]) + max(0.0, -bb[1])
            + max(0.0, bb[2] - width) + max(0.0, bb[3] - height)
        )
        safe = max(float(clearance_padding), (bb[3] - bb[1]) * 0.75, 4.0)
        cost = 0.0
        nearest = max(width, height, 1)
        for px, py in obstacle_points:
            dx = max(bb[0] - px, 0.0, px - bb[2])
            dy = max(bb[1] - py, 0.0, py - bb[3])
            distance = math.hypot(dx, dy)
            nearest = min(nearest, distance)
            if distance < safe:
                cost += (safe - distance) / safe
        edge_clearance = min(
            bb[0], bb[1], width - bb[2], height - bb[3])
        nearest = min(nearest, max(edge_clearance, 0.0))
        return outside, cost, nearest

    def _draw_line_labels_view(self, mapdata, enabled: list, label_cfg: dict,
                               abstract: bool, samples: int) -> None:
        """公共交通图：标签更密更大更清晰；重合线路同一处横向拼接徽章。

        统一视觉规范（两端共用）见
        线路色实心胶囊；文字色按 Rec.709 亮度自适应；无文字描边；
        一圈半透明细轮廓；重合线路各色块直接并排、共用同一圈轮廓。
        """
        tv = self._tv
        # 标签按成品长边缩放，不随选区的地理放大倍数增长。
        # ctx.zoom 含选区放大倍率，直接用于字号会让局部导出的胶囊遮住地图。
        # 使用完整画布尺寸，矩形、旋转和分块均保持相同比例；AA 画布同比放大。
        width = getattr(self.ctx, "canvas_width", 0) or self.ctx.image.width
        height = getattr(self.ctx, "canvas_height", 0) or self.ctx.image.height
        output_scale = max(width, height) / 1024.0
        layout_scale = output_scale * _BASELINE_1024_SCALE
        # YAML 的 {fixed, scaled} 先在 1024px 基准解析，再按输出分辨率缩放。
        lv = {
            "repeat_interval": float(label_cfg.get("repeat_interval", 300)),
            "min_gap": float(label_cfg.get("min_gap", 180)),
            "max_per_line": int(label_cfg.get("max_per_line", 12)),
            "size": {"fixed": 0.0, "scaled": 60.0},
            "outer_border_color": "#101828",
            "outer_border_alpha": 0.55,
            "text_on_light": "#101828",
            "text_on_dark": "#FFFFFF",
            "luminance_threshold": 165.0,
            "merge_dist": 40.0,
            "candidate_interval": 110.0,
            "candidate_limit": 72,
            "clearance_padding": 14.0,
        }
        for k, v in tv.items():
            if k.startswith("label_"):
                lv[k[len("label_"):]] = v
        # 内边距 / 轮廓 / 缝一律由**字号**等比例派生（规范基准：字号 15px@1024
        # → 内边距 9、轮廓 2、缝 3，即 0.60 / 2⁄15 / 0.20）。侧栏把「名称字号」
        # 拉大时整个胶囊等比放大，而不是字变大、边距不变。
        # 主题若显式写了 ``transit_view.label_padding`` 等，仍以主题为准。
        _size_1024 = _resolve_size(lv["size"], _BASELINE_1024_SCALE)
        for _key, _ratio in (("padding", 0.60),
                             ("border_width", 2.0 / 15.0),
                             ("seam_width", 3.0 / 15.0)):
            if _key not in lv:
                lv[_key] = {
                    "fixed": 0.0,
                    "scaled": _size_1024 * _ratio / _BASELINE_1024_SCALE,
                }
        for key in ("size", "padding", "border_width", "seam_width", "corner_radius"):
            if key in lv and lv[key] is not None:
                lv[key] = _resolve_size(lv[key], _BASELINE_1024_SCALE) * output_scale
        min_gap = float(lv["min_gap"]) * layout_scale
        merge_dist = float(lv["merge_dist"]) * layout_scale

        # 收集所有启用线路的沿线候选点
        cands: list = []
        for tr in self._render_lines:
            td = transit.type_def(tr.type)
            if (td is None
                    or (transit.normalize_type(tr.type) or tr.type)
                    not in enabled
                    or not tr.name):
                continue
            pts = self._line_pixels(tr, td, mapdata, abstract, samples)
            if len(pts) < 2:
                continue
            cands.extend(self._label_candidates(tr, pts, {
                "repeat_interval": float(lv["repeat_interval"]),
                "max_per_line": int(lv["max_per_line"]),
                "candidate_interval": float(lv["candidate_interval"]),
                "candidate_limit": int(lv["candidate_limit"]),
                "layout_scale": layout_scale,
            }))

        # 重合合并：距离阈值内拼进同一徽章（组心 = 首个候选位置）
        groups: list = []
        for c in cands:
            found = None
            for g in groups:
                if math.hypot(g[0] - c[0], g[1] - c[1]) < merge_dist:
                    found = g
                    break
            if found is None:
                groups.append([c[0], c[1], [c]])
            else:
                found[2].append(c)

        # 站点与交通 POI 共用 transport stop 位置；POI 图层稍后绘制，
        # 因此在这里先把这些位置作为障碍物纳入净空评分。
        obstacle_points = []
        seen_obstacles = set()
        for tr in self._render_lines:
            if ((transit.normalize_type(tr.type) or tr.type) not in enabled):
                continue
            for stop in tr.stops:
                key = self._stop_key_for_line(tr, stop.node_id)
                semantic = self._transit_model.stop(key) if self._transit_model is not None and key else None
                semantic = semantic if semantic is not None and semantic.key.namespace else None
                node = mapdata.node(stop.node_id) if semantic is None else None
                position = semantic.position if semantic is not None else node.pos if node is not None else None
                if position is None:
                    continue
                px, py = self.ctx.transform(position.x, position.z)
                key = (int(round(px)), int(round(py)))
                if key not in seen_obstacles:
                    seen_obstacles.add(key)
                    obstacle_points.append((px, py))

        # 先生成所有实际尺寸的徽章，再综合评价：画布外溢、站点/POI
        # 冲突、当前交通图像素占用、最近净空。评分最低者优先放置。
        prepared = []
        for gx, gy, items in groups:
            seen = set()
            uniq: list = []
            for _, _, name, rgb in items:
                key = (name, rgb)
                if key in seen:
                    continue
                seen.add(key)
                uniq.append((name, rgb))
            if not uniq:
                continue
            badge = self._make_view_badge(uniq, lv)
            if badge is None:
                continue
            x = int(round(gx - badge.width / 2))
            y = int(round(gy - badge.height / 2))
            bb = (x, y, x + badge.width, y + badge.height)
            outside, obstacle_cost, clearance = (
                self._line_label_obstacle_score(
                    bb, obstacle_points, self.ctx.image.size,
                    float(lv.get("clearance_padding", 14.0))
                    * layout_scale,
                ))
            visual_pad = max(int(round(
                float(lv.get("clearance_padding", 14.0))
                * layout_scale)), 2)
            visual_occupancy = self._legend_occupancy(
                self.ctx.image,
                x - visual_pad, y - visual_pad,
                badge.width + visual_pad * 2,
                badge.height + visual_pad * 2,
            )
            prepared.append((
                outside, obstacle_cost, visual_occupancy, -clearance,
                gx, gy, bb, badge, uniq,
            ))

        prepared.sort(key=lambda item: item[:4])
        placed: list = []
        placed_boxes = []
        label_counts = {}
        max_per_line = max(int(lv.get("max_per_line", 12)), 1)
        for (_outside, _obstacle_cost, _occupancy, _neg_clearance,
             gx, gy, bb, badge, uniq) in prepared:
            keys = [(name, rgb) for name, rgb in uniq]
            if all(label_counts.get(key, 0) >= max_per_line for key in keys):
                continue
            if any(math.hypot(gx - px, gy - py) < min_gap
                   for px, py in placed):
                continue
            if self._bbox_hit(bb, placed_boxes, pad=4.0):
                continue
            x, y = int(bb[0]), int(bb[1])
            self.ctx.image.paste(badge, (x, y), badge)
            placed.append((gx, gy))
            placed_boxes.append(bb)
            for key in keys:
                label_counts[key] = label_counts.get(key, 0) + 1

    def _make_view_badge(self, items: list, lv: dict):
        """拼接徽章（2x 超采样抗锯齿）。

        统一视觉规范（导出与预览共用）见

        - 底色：线路色实心胶囊（单条）或各线路色块直接并排（多条）；
        - 字色：按 Rec.709 亮度自适应，≥ ``luminance_threshold`` 用深字，
          否则白字；**不再画文字描边**，可读性由字重承担；
        - 轮廓：整条／整组共用一圈半透明细边（``outer_border_color`` ×
          ``outer_border_alpha``），取代旧的 3px 实心黑框与白底组框；
        - 多条重合：色块之间留 ``seam_width`` 的透明缝（透出底图底色），
          两端圆角、内部直角，视觉上仍是「一条胶囊被切成几段」。

        与 ``viewer/src/main.ts`` 的 ``.map-transit-badge`` 使用同一组值。
        """
        threshold = float(lv.get("luminance_threshold", 165.0))
        on_light = hex_to_rgb(str(lv.get("text_on_light", "#101828")))
        on_dark = hex_to_rgb(str(lv.get("text_on_dark", "#FFFFFF")))

        def adaptive_rgba(rgb) -> tuple:
            return ((on_light + (255,)) if _luminance709(rgb) >= threshold
                    else (on_dark + (255,)))

        # 主题参数可能来自用户编辑或旧版本配置；绘制前统一限制为合法值，
        # 避免极小缩放/负边距把 Pillow 的矩形高度截断为 0 或负数。
        size = max(float(_resolve_size(lv.get("size", 12), self.render_scale)), 1.0)
        pad = max(float(_resolve_size(lv.get("padding", 6), self.render_scale)), 0.0)
        border_w = max(
            float(_resolve_size(lv.get("border_width", 2.0), self.render_scale)),
            0.0,
        )
        seam = max(
            float(_resolve_size(lv.get("seam_width", 3.0), self.render_scale)),
            0.0,
        )
        outer_alpha = max(0.0, min(float(
            lv.get("outer_border_alpha", 0.55)), 1.0))
        outer_rgba = (hex_to_rgb(str(lv.get("outer_border_color", "#101828")))
                      + (int(round(outer_alpha * 255)),))
        configured_radius = lv.get("corner_radius")
        radius2 = (None if configured_radius is None else max(int(round(
            _resolve_size(configured_radius, self.render_scale) * 2)), 0))
        # 公共交通图徽章一律加粗：取消文字描边后，可读性由字重承担。
        font = self._get_font(max(int(size * 2), 6), bold=True)
        probe = ImageDraw.Draw(Image.new("RGBA", (4, 4), (0, 0, 0, 0)))
        meas = []
        for name, rgb in items:
            bbox = probe.textbbox((0, 0), name, font=font)
            meas.append((bbox, bbox[2] - bbox[0], bbox[3] - bbox[1]))
        th = max(m[2] for m in meas)
        # 文本高度非正时（空/纯空白线路名、字体退化）badge 无意义且会让
        # rounded_rectangle 出现 y1<y0 崩溃，直接跳过该组（与 _make_badge 一致）。
        if th <= 0:
            return None
        blocks = []
        # 胶囊高固定为 1.75 × 字号（与预览侧 .map-transit-badge 同式），
        # 不跟着文字 bbox 高度漂移，两端高度才能对上。
        badge_h = max(int(round(size * 3.5)), 2)
        for (bbox, tw, thh), (name, rgb) in zip(meas, items):
            # pad 是 1x 域的每侧水平内边距；这里在 2x 域作画，故两侧共 pad * 4
            bw2 = max(int(math.ceil(tw + pad * 4)), 2)
            blocks.append((name, rgb, bbox, bw2, badge_h))

        def draw_label(d, x0, y0, bw2, bh2, bbox, name, rgb) -> None:
            """按该色块自身底色亮度取字色（描边已取消）。"""
            d.text((x0 + (bw2 - (bbox[2] - bbox[0])) / 2 - bbox[0],
                    y0 + (bh2 - (bbox[3] - bbox[1])) / 2 - bbox[1]),
                   name, font=font, fill=adaptive_rgba(rgb))

        if len(blocks) == 1:
            name, rgb, bbox, bw2, bh2 = blocks[0]
            img2 = Image.new("RGBA", (bw2, bh2), (0, 0, 0, 0))
            d = ImageDraw.Draw(img2)
            radius = (min(bw2, bh2) // 2 if radius2 is None
                      else min(radius2, min(bw2, bh2) // 2))
            d.rounded_rectangle((0, 0, bw2 - 1, bh2 - 1),
                                radius=radius,
                                fill=rgb + (255,))
            d.rounded_rectangle((0, 0, bw2 - 1, bh2 - 1),
                                radius=radius,
                                outline=outer_rgba,
                                width=max(int(round(border_w * 2)), 1))
            draw_label(d, 0, 0, bw2, bh2, bbox, name, rgb)
        else:
            n = len(blocks)
            inner_h = max(b[4] for b in blocks)
            seam2 = seam * 2.0
            total_w = max(int(math.ceil(
                sum(b[3] for b in blocks) + seam2 * (n - 1))), 2)
            total_h = max(int(math.ceil(inner_h)), inner_h, 2)
            img2 = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
            d = ImageDraw.Draw(img2)
            x = 0.0
            cy = max((total_h - inner_h) // 2, 0)
            for idx, (name, rgb, bbox, bw2, bh2) in enumerate(blocks):
                # 圆角：仅整组两端的左/右侧为胶囊，内部拼接处为竖直直线
                if idx == 0:
                    corners = (True, False, False, True)   # 右接拼线，仅左端圆
                elif idx == n - 1:
                    corners = (False, True, True, False)   # 左接拼线，仅右端圆
                else:
                    corners = (False, False, False, False)  # 中间段直线
                x0 = int(round(x))
                y0 = int(cy)
                x1 = max(x0 + bw2 - 1, x0)
                y1 = max(y0 + bh2 - 1, y0)
                # Pillow 在仅启用部分 corners 时会继续绘制侧边矩形；
                # 徽标过小时，半径过大会让内部侧边的 y1 小于 y0。
                block_radius = max(min(x1 - x0 + 1, y1 - y0 + 1) - 3, 0) // 2
                d.rounded_rectangle((x0, y0, x1, y1),
                                    radius=block_radius,
                                    fill=rgb + (255,),
                                    corners=corners)
                draw_label(d, x0, y0, bw2, bh2, bbox, name, rgb)
                x += bw2 + seam2
            # 整组共用一圈细轮廓（不再有白底组框）
            d.rounded_rectangle((0, 0, total_w - 1, total_h - 1),
                                radius=min(total_w, total_h) // 2,
                                outline=outer_rgba,
                                width=max(int(round(border_w * 2)), 1))
        w1 = max(int(round(img2.width / 2)), 1)
        h1 = max(int(round(img2.height / 2)), 1)
        return img2.resize((w1, h1), Image.LANCZOS)

    def _make_badge(self, text: str, line_rgb, cfg: dict):
        """绘制线路名徽章（白底圆角矩形 + 线路色文字/边框），2x 超采样抗锯齿。"""
        size = max(float(_resolve_size(cfg.get("size", 9), self.render_scale)), 1.0)
        pad = max(float(_resolve_size(cfg.get("padding", 6), self.render_scale)), 0.0)
        border_w = max(
            float(_resolve_size(cfg.get("border_width", 1.2), self.render_scale)),
            0.0,
        )
        bg = cfg.get("bg", "#FFFFFF")
        # color_fill：线路色底白字胶囊（RMG 式）；缺省白底彩边
        badge_style = cfg.get("badge_style")

        # 线路名徽章统一黑体加粗，辨识度更高
        font = self._get_font(max(size * 2, 6), bold=True)
        # 文字颜色：线路色过亮时自动转深色保证可读
        text_color = cfg.get("text_color")
        if not text_color:
            text_color = "#3A3F4A" if _luminance(line_rgb) > 170 else _rgba_to_hex(line_rgb)
        text_rgb = hex_to_rgb(text_color)
        bg_rgb = hex_to_rgb(bg)

        # 量尺寸（2x 超采样）
        probe = ImageDraw.Draw(Image.new("RGBA", (4, 4), (0, 0, 0, 0)))
        bbox = probe.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if tw <= 0 or th <= 0:
            return None
        bw2 = max(int(math.ceil(tw + (pad + border_w + 3) * 2)), 2)
        bh2 = max(int(math.ceil(th + (pad + border_w + 3) * 2)), 2)
        radius = min(bw2, bh2) // 2
        img = Image.new("RGBA", (bw2, bh2), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        if badge_style == "color_fill":
            d.rounded_rectangle(
                (0, 0, bw2 - 1, bh2 - 1),
                radius=radius,
                fill=line_rgb + (255,),
            )
            d.text(
                ((bw2 - tw) / 2 - bbox[0], (bh2 - th) / 2 - bbox[1]),
                text,
                font=font,
                fill=(255, 255, 255, 255),
            )
        else:
            d.rounded_rectangle(
                (0, 0, bw2 - 1, bh2 - 1),
                radius=radius,
                fill=bg_rgb + (255,),
                outline=line_rgb + (255,),
                width=max(int(border_w * 2), 1),
            )
            d.text(
                ((bw2 - tw) / 2 - bbox[0], (bh2 - th) / 2 - bbox[1]),
                text,
                font=font,
                fill=text_rgb + (255,),
            )
        # 缩小到 1x
        w1 = max(int(round(bw2 / 2)), 1)
        h1 = max(int(round(bh2 / 2)), 1)
        return img.resize((w1, h1), Image.LANCZOS)

    def _get_font(self, size_px: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        key = (size_px, bold)
        if key not in self._font_cache:
            self._font_cache[key] = load_pillow_font(
                size_px, font_name=self._font_family, bold=bold)
        return self._font_cache[key]
