"""立交识别与拼图渲染（功能 3）。

识别思路：
1. 高速汇聚节点：≥3 段高速（highway）相连的节点
2. 空间聚类：贪心（eps=300 游戏单位），每个簇视为一处立交候选
3. 强度评估：方向数（8 分桶）、簇内高速总长、高架占比、汇聚点数
4. 重叠合并：包围盒相交的相邻簇合并为同一立交（如苜蓿叶被拆成两簇）
5. 筛选：方向数 >= min_dirs（过滤普通十字路口），返回按强度降序的方形区域

拼图：逐簇区域渲染 → 网格合成一张图。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, List, Mapping, Optional

from PIL import Image

from .data import MapData, Node, Segment
from .i18n import tr

EPS = 300.0      # 聚类半径（游戏单位）
MIN_PTS = 3      # 簇最少候选点数
MARGIN = 250.0   # 包围盒外扩（单位）
ANGLE_BINS = 8   # 方向分桶数
MIN_DIRS = 4     # 方向数下限（过滤普通十字路口）
WORLD = 8640.0   # 世界半边长（游戏单位）
MAX_COUNT = 16   # 拼图最多容纳的立交数


@dataclass(frozen=True)
class InterchangeRequest:
    """定义可序列化的交通枢纽识别与拼图参数。"""

    top_count: int = MAX_COUNT
    tile_size: int = 1024
    cols: int | None = None
    eps: float = EPS
    min_pts: int = MIN_PTS
    margin: float = MARGIN
    min_dirs: int = MIN_DIRS

    def __post_init__(self) -> None:
        object.__setattr__(self, "top_count", max(1, int(self.top_count)))
        object.__setattr__(self, "tile_size", max(64, int(self.tile_size)))
        object.__setattr__(self, "cols", (
            None if self.cols is None else max(1, int(self.cols))))
        object.__setattr__(self, "eps", max(0.0, float(self.eps)))
        object.__setattr__(self, "min_pts", max(1, int(self.min_pts)))
        object.__setattr__(self, "margin", max(0.0, float(self.margin)))
        object.__setattr__(self, "min_dirs", max(1, int(self.min_dirs)))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None = None, **kwargs):
        raw = dict(value or {})
        raw.update(kwargs)
        return cls(
            top_count=raw.get("top_count", raw.get("max_count", MAX_COUNT)),
            tile_size=raw.get("tile_size", raw.get("size", 1024)),
            cols=raw.get("cols"), eps=raw.get("eps", EPS),
            min_pts=raw.get("min_pts", MIN_PTS),
            margin=raw.get("margin", MARGIN),
            min_dirs=raw.get("min_dirs", MIN_DIRS),
        )

    def to_dict(self) -> dict:
        return {
            "top_count": self.top_count, "tile_size": self.tile_size,
            "cols": self.cols, "eps": self.eps, "min_pts": self.min_pts,
            "margin": self.margin, "min_dirs": self.min_dirs,
        }


# ---- 识别 ----

def _seg_type(mapdata: MapData, seg: Segment):
    return mapdata.segment_types.get(seg.name) or mapdata.segment_types.get(seg.icls)


def highway_junction_nodes(mapdata: MapData) -> List[Node]:
    """高速汇聚节点：≥3 段高速相连。"""
    counts: dict = {}
    for s in mapdata.segments:
        st = _seg_type(mapdata, s)
        if not st or not st.highway:
            continue
        counts[s.start_node_id] = counts.get(s.start_node_id, 0) + 1
        counts[s.end_node_id] = counts.get(s.end_node_id, 0) + 1
    return [n for nid, cnt in counts.items() if cnt >= 3
            for n in [mapdata.node(nid)] if n]


def _cluster_nodes(nodes: List[Node], eps: float = EPS) -> List[List[Node]]:
    """贪心聚类：与任一已有簇中心距离 <= eps 则并入。"""
    clusters: List[List[Node]] = []
    centers: List[tuple] = []
    for n in nodes:
        best = -1
        for i, (cx, cz) in enumerate(centers):
            if math.hypot(n.pos.x - cx, n.pos.z - cz) <= eps:
                best = i
                break
        if best < 0:
            clusters.append([])
            centers.append((n.pos.x, n.pos.z))
            best = len(clusters) - 1
        clusters[best].append(n)
        k = len(clusters[best])
        clusters[best] = clusters[best]  # 占位
        centers[best] = (
            sum(q.pos.x for q in clusters[best]) / k,
            sum(q.pos.z for q in clusters[best]) / k,
        )
    return clusters


def _seg_len(seg: Segment) -> float:
    pts = seg.points
    if len(pts) < 2:
        return 0.0
    return sum(math.hypot(b.x - a.x, b.z - a.z) for a, b in zip(pts, pts[1:]))


def _seg_mode(mapdata: MapData, seg: Segment) -> str:
    """tunnel / elevated / ground（同 road_layer._mode）。"""
    n0 = mapdata.node(seg.start_node_id)
    n1 = mapdata.node(seg.end_node_id)
    ug = (n0.ug if n0 else False) or (n1.ug if n1 else False)
    og = (n0.og if n0 else True) and (n1.og if n1 else True)
    if ug:
        return "tunnel"
    if not og:
        return "elevated"
    return "ground"


def _cluster_strength(mapdata: MapData, nodes: List[Node],
                      margin: float = MARGIN) -> dict:
    xs = [n.pos.x for n in nodes]
    zs = [n.pos.z for n in nodes]
    rx0 = max(min(xs) - margin, -WORLD)
    rx1 = min(max(xs) + margin, WORLD)
    rz0 = max(min(zs) - margin, -WORLD)
    rz1 = min(max(zs) + margin, WORLD)

    hw_len = el_len = 0.0
    hw_cnt = el_cnt = 0
    for s in mapdata.segments:
        st = _seg_type(mapdata, s)
        if not st or not st.highway or not s.points:
            continue
        pts = s.points
        mx = sum(p.x for p in pts) / len(pts)
        mz = sum(p.z for p in pts) / len(pts)
        if not (rx0 <= mx <= rx1 and rz0 <= mz <= rz1):
            continue
        length = _seg_len(s)
        hw_len += length
        hw_cnt += 1
        if _seg_mode(mapdata, s) == "elevated":
            el_len += length
            el_cnt += 1

    # 方向数：汇聚节点相连高速段的方位角分桶
    angles = set()
    for n in nodes:
        for s in mapdata.segments:
            st = _seg_type(mapdata, s)
            if not st or not st.highway:
                continue
            other = None
            if s.start_node_id == n.id:
                other = mapdata.node(s.end_node_id)
            elif s.end_node_id == n.id:
                other = mapdata.node(s.start_node_id)
            if other is None:
                continue
            ang = math.degrees(math.atan2(other.pos.z - n.pos.z,
                                          other.pos.x - n.pos.x)) % 360
            angles.add(int(ang // (360 / ANGLE_BINS)))

    # 扩为正方形（_build_transform 会按中心扩方，这里直接输出方形区域）
    span = max(rx1 - rx0, rz1 - rz0)
    cx, cz = (rx0 + rx1) / 2, (rz0 + rz1) / 2
    half = span / 2
    region = (
        max(cx - half, -WORLD), max(cz - half, -WORLD),
        min(cx + half, WORLD), min(cz + half, WORLD),
    )

    return {
        "region": region,
        "cx": cx, "cz": cz,
        "npts": len(nodes),
        "hw_len": hw_len, "el_len": el_len,
        "hw_cnt": hw_cnt, "el_cnt": el_cnt,
        "dirs": len(angles),
    }


def _merge_overlapping(items: List[dict], margin: float = MARGIN) -> List[dict]:
    """包围盒相交的相邻立交合并为同一处（取并集）。"""
    merged: List[dict] = []
    for it in items:
        r0 = it["region"]
        hit = None
        for m in merged:
            r1 = m["region"]
            if not (r0[2] <= r1[0] or r0[0] >= r1[2]
                    or r0[3] <= r1[1] or r0[1] >= r1[3]):
                hit = m
                break
        if hit is None:
            merged.append(dict(it))
        else:
            r1 = hit["region"]
            x0, z0 = min(r0[0], r1[0]), min(r0[1], r1[1])
            x1, z1 = max(r0[2], r1[2]), max(r0[3], r1[3])
            span = max(x1 - x0, z1 - z0)
            cx, cz = (x0 + x1) / 2, (z0 + z1) / 2
            half = span / 2
            hit["region"] = (
                max(cx - half, -WORLD), max(cz - half, -WORLD),
                min(cx + half, WORLD), min(cz + half, WORLD),
            )
            hit["npts"] += it["npts"]
            hit["hw_len"] += it["hw_len"]
            hit["el_len"] += it["el_len"]
            hit["hw_cnt"] += it["hw_cnt"]
            hit["el_cnt"] += it["el_cnt"]
            hit["dirs"] = max(hit["dirs"], it["dirs"])
            hit["cx"], hit["cz"] = cx, cz
    return merged


def detect_interchanges(
    mapdata: MapData,
    eps: float = EPS,
    min_pts: int = MIN_PTS,
    margin: float = MARGIN,
    min_dirs: int = MIN_DIRS,
) -> List[dict]:
    """识别立交候选，返回按强度降序的字典列表。

    每项含：region(方形游戏坐标)、cx/cz(中心)、npts、dirs、
    hw_len、el_len、hw_cnt、el_cnt。
    """
    nodes = highway_junction_nodes(mapdata)
    candidates = []
    for cluster in _cluster_nodes(nodes, eps):
        if len(cluster) < min_pts:
            continue
        candidates.append(_cluster_strength(mapdata, cluster, margin))

    merged = _merge_overlapping(candidates, margin)
    merged = [c for c in merged if c["dirs"] >= min_dirs]
    merged.sort(
        key=lambda c: (c["dirs"], c["hw_len"], c["npts"]), reverse=True
    )
    return merged


# ---- 拼图 ----

def compose_mosaic(
    tiles: List[object],
    cols: Optional[int] = None,
    gap: int = 6,
    bg: tuple = (32, 32, 32),
):
    """将等尺寸图块网格拼成一张大图。"""
    if not tiles:
        raise ValueError(tr("gui.error.no_mosaic_tiles"))
    n = len(tiles)
    cols = cols or math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    tw, th = tiles[0].size
    out = Image.new("RGB", (cols * tw + (cols + 1) * gap,
                            rows * th + (rows + 1) * gap), bg)
    for i, tile in enumerate(tiles):
        r, c = divmod(i, cols)
        out.paste(tile, (gap + c * (tw + gap), gap + r * (th + gap)))
    return out


def render_interchange_mosaic(
    mapdata: MapData,
    render_region: Callable[[tuple, int], object],
    tile_size: int = 1024,
    max_count: int = MAX_COUNT,
    cols: Optional[int] = None,
    progress: Optional[Callable[[float, str], None]] = None,
    eps: float = EPS,
    min_pts: int = MIN_PTS,
    margin: float = MARGIN,
    min_dirs: int = MIN_DIRS,
):
    """识别立交 → 逐簇区域渲染 → 拼图。

    render_region(region, size) 返回该区域的 PIL Image（size×size）。
    返回 (mosaic, interchanges)。
    """
    interchanges = detect_interchanges(
        mapdata, eps=eps, min_pts=min_pts, margin=margin, min_dirs=min_dirs
    )[:max_count]
    if not interchanges:
        return None, []
    tiles = []
    total = len(interchanges)
    for i, ic in enumerate(interchanges):
        if progress:
            progress(i / total, tr("gui.progress.interchange",
                                   i=i + 1, n=total))
        tiles.append(render_region(ic["region"], tile_size))
    if progress:
        progress(0.99, tr("gui.progress.mosaic"))
    mosaic = compose_mosaic(tiles, cols=cols)
    return mosaic, interchanges


def render_interchange_request(
    mapdata: MapData,
    render_region: Callable[[tuple, int], object],
    request: InterchangeRequest | Mapping[str, Any] | object | None = None,
    *,
    progress: Optional[Callable[[float, str], None]] = None,
):
    """在保持命令行渲染接口不变的同时适配界面工作进程。"""
    if not isinstance(request, InterchangeRequest):
        # 界面负责公开请求协议，此处接受字典形式以避免领域适配层依赖 Qt。
        if request is not None and not isinstance(request, Mapping):
            to_dict = getattr(request, "to_dict", None)
            if not callable(to_dict):
                raise TypeError("interchange request must be a mapping or serialisable request")
            request = to_dict()
        request = InterchangeRequest.from_mapping(request)
    return render_interchange_mosaic(
        mapdata, render_region,
        tile_size=request.tile_size,
        max_count=request.top_count,
        cols=request.cols,
        progress=progress,
        eps=request.eps,
        min_pts=request.min_pts,
        margin=request.margin,
        min_dirs=request.min_dirs,
    )
