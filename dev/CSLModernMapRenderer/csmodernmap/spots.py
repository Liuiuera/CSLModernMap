"""特写取景与拼图框架：随机城市特写 / 堵车特写 / 堵车 Top 榜单。

取景主题（mode）：
1. random      按建筑密度加权随机取景（多块区域互不重叠）
2. congestion  按拥堵值加权随机取景（落在最堵的路段附近，需交通快照）
3. top         聚类最堵路段簇，按累计拥堵强度排序取 Top N（榜单模式，需交通快照）

拼图：逐区域渲染 → 网格合成一张大图，每格可画标注
（随机特写标编号，堵车/榜单标「路名 + 拥堵指数」）。
对比：同一区域渲染「有交通 vs 无交通」两张并排（仅单区域时）。
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .data import MapData
from .interchanges import compose_mosaic
from .i18n import tr
from .fonts import load_pillow_font
from .regions import (_density_grid, most_prosperous_window,
                      most_prosperous_window_by_size, random_region)

WORLD_MIN = -8640.0
WORLD_SIZE = 17280.0
WORLD_MAX = WORLD_MIN + WORLD_SIZE

# 相邻取景中心的最近间距（游戏单位），避免拼图格子内容重复
MIN_SEP = 300.0

# 中文字体候选（按优先级，同 label_layer）

# 取景主题中文名（GUI / CLI 共用）
MODE_LABELS = {
    "random": "随机城市特写",
    "congestion": "堵车特写",
    "top": "堵车Top榜单",
}


@dataclass(frozen=True)
class SpotRequest:
    """保存可序列化的局部地图选择请求，便于界面跨工作进程传递并复用原命令行采样函数。"""

    mode: str = "random"
    count: int = 1
    seed: int | None = None
    grid: int = 32
    min_count: int = 20
    min_congestion: float = 0.3
    span_range: tuple[float, float] = (1000.0, 1800.0)
    max_attempts: int = 6
    tiles: int = 3
    avoid_centers: tuple[tuple[float, float], ...] = ()

    def __post_init__(self) -> None:
        mode = str(self.mode or "random").strip().casefold()
        mode = {"random_spot": "random", "traffic": "congestion"}.get(
            mode, mode)
        if mode not in {"random", "congestion", "top"}:
            raise ValueError(f"unknown spot request mode: {self.mode!r}")
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "count", max(1, int(self.count)))
        object.__setattr__(self, "grid", max(1, int(self.grid)))
        object.__setattr__(self, "min_count", max(0, int(self.min_count)))
        object.__setattr__(self, "min_congestion", float(self.min_congestion))
        low, high = (float(value) for value in self.span_range)
        object.__setattr__(self, "span_range", (min(low, high), max(low, high)))
        object.__setattr__(self, "max_attempts", max(1, int(self.max_attempts)))
        object.__setattr__(self, "tiles", max(1, int(self.tiles)))
        object.__setattr__(self, "avoid_centers", tuple(
            (float(x), float(z)) for x, z in (self.avoid_centers or ())))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None = None, **kwargs) -> "SpotRequest":
        raw = dict(value or {})
        raw.update(kwargs)
        span = raw.get("span_range", raw.get("span", (1000.0, 1800.0)))
        if isinstance(span, (int, float)):
            span = (float(span), float(span))
        return cls(
            mode=raw.get("mode", raw.get("kind", "random")),
            count=raw.get("count", 1), seed=raw.get("seed"),
            grid=raw.get("grid", raw.get("grid_size", 32)),
            min_count=raw.get("min_count", 20),
            min_congestion=raw.get("min_congestion", 0.3),
            span_range=tuple(span),
            max_attempts=raw.get("max_attempts", 6),
            tiles=raw.get("tiles", 3),
            avoid_centers=tuple(raw.get("avoid_centers") or ()),
        )

    def to_dict(self) -> dict:
        return {
            "mode": self.mode, "count": self.count, "seed": self.seed,
            "grid": self.grid, "min_count": self.min_count,
            "min_congestion": self.min_congestion,
            "span_range": list(self.span_range),
            "max_attempts": self.max_attempts, "tiles": self.tiles,
            "avoid_centers": [list(item) for item in self.avoid_centers],
        }

    def to_region_policy(self):
        """把随机和拥堵偏好转换为工作进程的 RegionPolicy。"""
        from .render_jobs import RegionPolicy

        kind = {
            "random": "building_density",
            "congestion": "congestion",
        }.get(self.mode)
        if kind is None:
            raise ValueError("top spot requests do not map to one region policy")
        return RegionPolicy(
            kind=kind, seed=self.seed, avoid_centers=self.avoid_centers,
            max_attempts=self.max_attempts, tiles=self.tiles,
        )


# ---- 字体（拼图标注用） ----

_font_cache: dict = {}


def _get_font(size: float) -> ImageFont.FreeTypeFont:
    key = round(size, 1)
    if key not in _font_cache:
        size_px = max(int(round(size)), 6)
        _font_cache[key] = load_pillow_font(size_px)
    return _font_cache[key]


def draw_tile_label(tile: Image.Image, text: str, font_ratio: float = 0.045) -> Image.Image:
    """在图像左上角画半透明圆角底条 + 白色文字（用于拼图每格标注）。"""
    img = tile.convert("RGBA")
    size = max(int(round(min(img.size) * font_ratio)), 12)
    font = _get_font(size)
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    bbox = d.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad = max(int(round(size * 0.7)), 10)
    box_w, box_h = tw + pad * 2, th + pad
    d.rounded_rectangle(
        (pad, pad, pad + box_w, pad + box_h), radius=int(box_h / 2),
        fill=(0, 0, 0, 150),
    )
    d.text(
        (pad * 2 - bbox[0], pad + (box_h - th) / 2 - bbox[1]),
        text, font=font, fill=(255, 255, 255),
    )
    return Image.alpha_composite(img, overlay).convert("RGB")


# ---- 权重网格采样（通用） ----

def _pick_weighted(grid: np.ndarray, rng: random.Random, mask=None):
    """按网格值加权随机选一格 (row, col)；无可选时返回 None。"""
    w = grid.copy() if mask is not None else grid
    if mask is not None:
        w[mask] = 0.0
    cand = np.argwhere(w > 0)
    if len(cand) == 0:
        return None
    weights = w[cand[:, 0], cand[:, 1]].astype(float)
    pick = rng.choices(range(len(cand)), weights=weights, k=1)[0]
    return int(cand[pick, 0]), int(cand[pick, 1])


def _candidate_grid(counts: np.ndarray, min_count: int) -> np.ndarray:
    """城市检测：只保留建筑数 >= min_count 的格作候选（同 regions.random_region）。

    不足时回落到最高密度的格；整图无建筑时返回原网格（由调用方兜底）。
    """
    cand = np.argwhere(counts >= min_count)
    if len(cand) == 0:
        top = int(counts.max())
        if top <= 0:
            return counts
        cand = np.argwhere(counts == top)
    grid = np.zeros_like(counts, dtype=float)
    grid[cand[:, 0], cand[:, 1]] = counts[cand[:, 0], cand[:, 1]]
    return grid


def _region_from_cell(rng: random.Random, cy: int, cx: int,
                      grid: int, span_range: tuple) -> tuple:
    """由格心抖动取中心 + 随机跨度，返回 (region, cx_w, cz_w, half)。"""
    cell = WORLD_SIZE / grid
    cx_w = WORLD_MIN + (cx + rng.uniform(0.3, 0.7)) * cell
    cz_w = WORLD_MIN + (cy + rng.uniform(0.3, 0.7)) * cell
    half = rng.uniform(*span_range) / 2.0
    cx_w = min(max(cx_w, WORLD_MIN + half), WORLD_MAX - half)
    cz_w = min(max(cz_w, WORLD_MIN + half), WORLD_MAX - half)
    return (cx_w - half, cz_w - half, cx_w + half, cz_w + half), cx_w, cz_w, half


def _sample_from_grid(grid: np.ndarray, count: int, rng: random.Random,
                      grid_size: int, span_range: tuple) -> list:
    """从权重网格加权随机取 count 块区域（取景中心尽量分散、避开空地）。"""
    spots = []
    used = np.zeros_like(grid, dtype=bool)
    cell = WORLD_SIZE / grid_size
    for _ in range(count):
        pick = _pick_weighted(grid, rng, used)
        if pick is None:
            break
        cy, cx = pick
        region, cx_w, cz_w, half = _region_from_cell(rng, cy, cx, grid_size, span_range)
        # 屏蔽该格周围已占用的格：只按本块实际半径 + 最小间距，
        # 避免按最大跨度屏蔽导致 12 宫格排不下（轻微重叠可接受，内容仍不同）
        r_cells = int(math.ceil((half + MIN_SEP) / cell))
        y0, y1 = max(0, cy - r_cells), min(grid_size, cy + r_cells + 1)
        x0, x1 = max(0, cx - r_cells), min(grid_size, cx + r_cells + 1)
        used[y0:y1, x0:x1] = True
        spots.append({
            "region": region, "cx": cx_w, "cz": cz_w,
            "span": half * 2, "label": None, "score": None,
        })
    return spots


# ---- 交通拥堵辅助 ----

def _seg_congestion(seg) -> float:
    """路段有效拥堵值 [0,1]，无有效数据返回 -1（路段级优先，回落车道级）。"""
    st = getattr(seg, "seg_stats", None)
    c = st.weighted_avg_congestion if st else -1.0
    if c is None or c < 0.0:
        c = -1.0
        for lane in getattr(seg, "lanes", None) or []:
            if lane.has_congestion and lane.congestion > c:
                c = lane.congestion
    return c if (c is not None and c >= 0.0) else -1.0


def has_traffic(mapdata: MapData) -> bool:
    """是否拥有可用的交通快照。"""
    snap = getattr(mapdata, "traffic", None)
    return bool(snap and getattr(snap, "segments", None))


def _region_policy_from_value(value):
    from .render_jobs import RegionPolicy

    if isinstance(value, RegionPolicy):
        return value
    if isinstance(value, SpotRequest):
        return value.to_region_policy()
    if isinstance(value, Mapping):
        raw = dict(value)
        kind = raw.get("kind", raw.get("mode", "building_density"))
        kind = {"random": "building_density", "random_spot": "building_density",
                "traffic": "congestion"}.get(str(kind).casefold(), kind)
        return RegionPolicy.from_dict({**raw, "kind": kind})
    # 兼容旧工作进程测试及调用方传入的小型鸭子类型策略对象。
    if hasattr(value, "kind"):
        return RegionPolicy(
            kind=getattr(value, "kind"), seed=getattr(value, "seed", None),
            avoid_centers=getattr(value, "avoid_centers", ()),
            max_attempts=getattr(value, "max_attempts", 6),
            tiles=getattr(value, "tiles", 3),
        )
    raise TypeError(f"unsupported region policy: {type(value).__name__}")


def pick_region_for_request(mapdata, request) -> tuple[tuple, tuple | None]:
    """从结构化 SpotRequest 或策略映射选择一个区域。"""
    if isinstance(request, SpotRequest):
        if request.mode == "top":
            spots = top_congestion_spots(
                mapdata, count=1, min_congestion=request.min_congestion)
            if not spots:
                raise RuntimeError(tr("gui.msg.no_congestion"))
            item = spots[0]
            return item["region"], (item.get("cx"), item.get("cz"))
        policy = request.to_region_policy()
    else:
        policy = _region_policy_from_value(request)
    return pick_region_for_policy(mapdata, policy)


def select_spots(mapdata, request: SpotRequest | Mapping[str, Any]) -> list:
    """把随机、拥堵或排名请求解析为局部地图记录。"""
    request = (request if isinstance(request, SpotRequest)
               else SpotRequest.from_mapping(request))
    rng = random.Random(request.seed) if request.seed is not None else None
    if request.mode == "random":
        return sample_random_spots(
            mapdata, count=request.count, rng=rng, grid=request.grid,
            min_count=request.min_count, span_range=request.span_range,
        )
    if request.mode == "congestion":
        if not has_traffic(mapdata):
            raise RuntimeError(tr("gui.msg.no_traffic_spot"))
        return sample_congestion_spots(
            mapdata, count=request.count, rng=rng, grid=request.grid,
            min_congestion=request.min_congestion,
            span_range=request.span_range,
        )
    return top_congestion_spots(
        mapdata, count=request.count, min_congestion=request.min_congestion)


# 为使用操作名称作为动作标识的调用方提供描述性别名。
select_spots_for_request = select_spots


def pick_region_for_policy(mapdata, policy) -> tuple[tuple, tuple | None]:
    """按可序列化区域策略选区，返回 ``(region, center)``。

    该函数是渲染作业进程边界内的唯一动态选区入口：GUI 不再传递
    Python 回调，只传 :class:`render_jobs.RegionPolicy` 的序列化形态。
    center 供调用方维护"最近选区"避让列表；无法确定时返回 None。
    """
    policy = _region_policy_from_value(policy)
    rng = random.Random(policy.seed) if policy.seed is not None else None
    kind = policy.kind
    if kind == "building_density":
        region = None
        cx = cz = 0.0
        for _ in range(max(1, policy.max_attempts)):
            region = random_region(mapdata, rng=rng)
            span = max(abs(region[2] - region[0]), abs(region[3] - region[1]))
            cx = (region[0] + region[2]) / 2
            cz = (region[1] + region[3]) / 2
            if all(math.hypot(cx - rx, cz - rz) >= span
                   for rx, rz in policy.avoid_centers):
                break
        return region, (cx, cz)
    if kind == "most_prosperous":
        # side_m 优先：调用方已按游戏地块或旧范围设置算好窗口边长。
        # tiles 那套 1920m 格子语言只留给没给 side_m 的旧调用方。
        region = (most_prosperous_window_by_size(mapdata, policy.side_m)
                  if policy.side_m > 0
                  else most_prosperous_window(mapdata, tiles=policy.tiles))
        return region, ((region[0] + region[2]) / 2,
                        (region[1] + region[3]) / 2)
    if kind == "congestion":
        if not has_traffic(mapdata):
            raise RuntimeError(tr("gui.msg.no_traffic_spot"))
        sampled = sample_congestion_spots(mapdata, count=1, rng=rng)
        if not sampled:
            raise RuntimeError(tr("gui.msg.no_congestion"))
        spot = sampled[0]
        return spot["region"], (spot.get("cx"), spot.get("cz"))
    raise ValueError(f"unknown region policy kind: {kind!r}")


def worst_segment_in_region(mapdata: MapData, region: tuple):
    """区域内拥堵最高的路段 → (road_name, congestion)；无则 ("", -1)。"""
    snap = getattr(mapdata, "traffic", None)
    if not snap:
        return "", -1.0
    best, best_c = None, -1.0
    for seg in snap.segments:
        c = _seg_congestion(seg)
        if c < 0:
            continue
        wp = seg.world_points()
        if not wp:
            continue
        mx = sum(p.x for p in wp) / len(wp)
        mz = sum(p.z for p in wp) / len(wp)
        if region[0] <= mx <= region[2] and region[1] <= mz <= region[3]:
            if c > best_c:
                best, best_c = seg, c
    if best is None:
        return "", -1.0
    name = (best.road_name or "").strip() or "拥堵路段"
    return name, best_c


def _congestion_grid(mapdata: MapData, grid: int = 32,
                     min_congestion: float = 0.3) -> np.ndarray:
    """拥堵权重网格：每格累计经过该格的路段拥堵值。"""
    grid_arr = np.zeros((grid, grid), dtype=float)
    snap = getattr(mapdata, "traffic", None)
    if not snap or not getattr(snap, "segments", None):
        return grid_arr
    xs, zs, vals = [], [], []
    for seg in snap.segments:
        c = _seg_congestion(seg)
        if c < min_congestion:
            continue
        wp = seg.world_points()
        if not wp:
            continue
        xs.append(sum(p.x for p in wp) / len(wp))
        zs.append(sum(p.z for p in wp) / len(wp))
        vals.append(c)
    if not xs:
        return grid_arr
    gx = np.clip(((np.asarray(xs) - WORLD_MIN) / WORLD_SIZE * grid).astype(int),
                 0, grid - 1)
    gz = np.clip(((np.asarray(zs) - WORLD_MIN) / WORLD_SIZE * grid).astype(int),
                 0, grid - 1)
    np.add.at(grid_arr, (gz, gx), np.asarray(vals))
    return grid_arr


# ---- 取景主题 ----

def sample_random_spots(
    mapdata: MapData,
    count: int = 1,
    rng: Optional[random.Random] = None,
    grid: int = 32,
    min_count: int = 20,
    span_range: tuple = (1000.0, 1800.0),
) -> list:
    """按建筑密度加权随机取 count 块城市建成区（互不重叠，避开空地）。

    候选格沿用 regions.random_region 的城市检测：只取建筑数 >= min_count 的格，
    不足时回落到最高密度格，确保取景中心落在已开发区域。
    返回 [{region, cx, cz, span, label, score}, ...]。
    """
    density = _density_grid(mapdata, grid)
    if density.sum() == 0:
        density[grid // 2, grid // 2] = 1  # 兜底：无建筑时取世界中心
    rng = rng or random.Random()
    weights = _candidate_grid(density, min_count)
    spots = _sample_from_grid(weights, count, rng, grid, span_range)
    # 补全标注：随机特写标编号
    for i, s in enumerate(spots):
        s["label"] = str(i + 1)
    return spots


def sample_congestion_spots(
    mapdata: MapData,
    count: int = 1,
    rng: Optional[random.Random] = None,
    grid: int = 32,
    min_congestion: float = 0.3,
    span_range: tuple = (900.0, 1600.0),
) -> list:
    """按拥堵值加权随机取 count 块区域（需交通快照）。

    标注取区域内拥堵最高路段的「路名 + 拥堵指数」。
    """
    grid_arr = _congestion_grid(mapdata, grid, min_congestion)
    if grid_arr.sum() == 0:
        raise ValueError(
            tr("gui.error.no_congestion", pct=f"{min_congestion:.0%}")
        )
    spots = _sample_from_grid(grid_arr, count, rng or random.Random(), grid, span_range)
    for s in spots:
        name, c = worst_segment_in_region(mapdata, s["region"])
        s["label"] = f"{name} {c * 100:.0f}%" if c >= 0 else None
        s["score"] = c
    return spots


def top_congestion_spots(
    mapdata: MapData,
    count: int = 12,
    eps: float = 300.0,
    min_congestion: float = 0.4,
    margin: float = 250.0,
    span_cap: float = 2400.0,
) -> list:
    """聚类最堵路段簇，按累计拥堵强度排序取 Top N（榜单模式）。

    返回按 score（累计拥堵）降序的 [{region, cx, cz, span, label, score}, ...]。
    """
    snap = getattr(mapdata, "traffic", None)
    if not snap or not getattr(snap, "segments", None):
        return []
    hot = []
    for seg in snap.segments:
        c = _seg_congestion(seg)
        if c < min_congestion:
            continue
        wp = seg.world_points()
        if not wp:
            continue
        hot.append({
            "x": sum(p.x for p in wp) / len(wp),
            "z": sum(p.z for p in wp) / len(wp),
            "c": c, "name": seg.road_name,
        })
    if not hot:
        return []

    # 贪心聚类（同 interchanges._cluster_nodes）
    clusters, centers = [], []
    for h in hot:
        best = -1
        for i, (cx, cz) in enumerate(centers):
            if math.hypot(h["x"] - cx, h["z"] - cz) <= eps:
                best = i
                break
        if best < 0:
            clusters.append([])
            centers.append((h["x"], h["z"]))
            best = len(clusters) - 1
        clusters[best].append(h)
        k = len(clusters[best])
        centers[best] = (
            sum(q["x"] for q in clusters[best]) / k,
            sum(q["z"] for q in clusters[best]) / k,
        )

    spots = []
    for cl in clusters:
        score = sum(q["c"] for q in cl)
        worst = max(cl, key=lambda q: q["c"])
        xs = [q["x"] for q in cl]
        zs = [q["z"] for q in cl]
        span = min(max(max(xs) - min(xs), max(zs) - min(zs)) + margin * 2, span_cap)
        cx = (min(xs) + max(xs)) / 2
        cz = (min(zs) + max(zs)) / 2
        half = span / 2
        region = (
            max(cx - half, WORLD_MIN), max(cz - half, WORLD_MIN),
            min(cx + half, WORLD_MAX), min(cz + half, WORLD_MAX),
        )
        name = (worst["name"] or "").strip() or "拥堵路段"
        spots.append({
            "region": region, "cx": cx, "cz": cz, "span": span,
            "label": f"{name} {worst['c'] * 100:.0f}%", "score": score,
        })
    spots.sort(key=lambda s: s["score"], reverse=True)
    return spots[:count]


# ---- 拼图 / 对比 ----

def render_spot_mosaic(
    regions: list,
    render_region: Callable[[tuple, int], object],
    tile_size: int = 1024,
    cols: Optional[int] = None,
    progress: Optional[Callable[[float, str], None]] = None,
):
    """逐区域渲染 → 网格拼图（带标注）。

    regions 每项含 region（游戏坐标方形区域）与可选 label；
    render_region(region, size) 返回 size×size 的 PIL Image。
    """
    if not regions:
        raise ValueError(tr("gui.error.no_spot_region"))
    tiles = []
    total = len(regions)
    for i, r in enumerate(regions):
        if progress:
            progress(i / total, tr("gui.progress.spot", i=i + 1, n=total))
        tile = render_region(r["region"], tile_size)
        if r.get("label"):
            tile = draw_tile_label(tile, str(r["label"]))
        tiles.append(tile)
    if progress:
        progress(0.99, tr("gui.progress.mosaic"))
    return compose_mosaic(tiles, cols=cols)


def compose_compare(
    a: Image.Image,
    b: Image.Image,
    caption_left: str = "有交通",
    caption_right: str = "无交通",
    gap: int = 6,
    bg: tuple = (32, 32, 32),
):
    """两张同尺寸图并排成「交通对比」图，每半左上角画标题。"""
    if a.size != b.size:
        b = b.resize(a.size, Image.LANCZOS)
    a2 = draw_tile_label(a.convert("RGB"), caption_left) if caption_left else a
    b2 = draw_tile_label(b.convert("RGB"), caption_right) if caption_right else b
    w, h = a2.size
    out = Image.new("RGB", (w * 2 + gap * 3, h + gap * 2), bg)
    out.paste(a2, (gap, gap))
    out.paste(b2, (w + gap * 2, gap))
    return out
