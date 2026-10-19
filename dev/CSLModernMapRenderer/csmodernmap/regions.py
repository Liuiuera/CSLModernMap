"""随机区域采样：在城市中按场景特征加权随机选取特写区域。

基础算法（random_region）：
1. 把世界划分为 grid×grid 网格，统计每格内的建筑数量 → 密度场
2. 以高于阈值（或最高密度）的格为候选，按密度加权随机选一格
3. 格内抖动取中心，跨度随机，钳制在世界范围内

场景加权（closeup_region）：同样的网格加权框架，但特征场按场景键切换 ——
铁路设置特写轨道密集区、水域设置特写水面、地形设置特写山地起伏……
用于首次运行向导 / 主题编辑器的"随机特写"预览取景。
"""
from __future__ import annotations

import math
import random
from operator import attrgetter

import numpy as np

from .data import MapData
from .layers.district_layer import visible_administrative_districts

WORLD_MIN = -8640.0
WORLD_SIZE = 17280.0

# CSL 标准区块网格：9×9 块，每块 1920×1920 单位
TILE_METERS = 1920.0
TILE_GRID = 9


def most_prosperous_window(
    mapdata: MapData,
    tiles: int = 3,
) -> tuple[float, float, float, float]:
    """在 9×9 区块网格上滑动 tiles×tiles 窗口，返回建筑最密集窗口的世界区域。

    复用 _density_grid(mapdata, 9)：grid=9 时每格恰好对应一个 1920×1920 区块，
    即按区块统计建筑数量；随后滑动窗口求总和最大（平手时优先靠近地图中心），
    返回该窗口覆盖的游戏坐标 (x0, z0, x1, z1)。

    无建筑时回落到中心窗口，保证始终有可用区域。
    """
    n = TILE_GRID
    if not 1 <= tiles <= n:
        raise ValueError(f"tiles 需在 1~{n} 之间，当前 {tiles}")
    counts = _density_grid(mapdata, n)  # 9×9 每区块建筑数（行=z，列=x）
    wins = n - tiles + 1
    center = (n - tiles) / 2.0
    best_sum = -1
    best_d = float("inf")
    best_ix = best_iy = 0
    for iy in range(wins):
        for ix in range(wins):
            s = int(counts[iy:iy + tiles, ix:ix + tiles].sum())
            # 平手时优先取最靠近地图中心的窗口（曼哈顿距离最小）
            d = abs(ix - center) + abs(iy - center)
            if s > best_sum or (s == best_sum and d < best_d):
                best_sum = s
                best_d = d
                best_ix, best_iy = ix, iy
    x0 = WORLD_MIN + best_ix * TILE_METERS
    z0 = WORLD_MIN + best_iy * TILE_METERS
    x1 = x0 + tiles * TILE_METERS
    z1 = z0 + tiles * TILE_METERS
    return (x0, z0, x1, z1)


def random_region(
    mapdata: MapData,
    rng: random.Random | None = None,
    grid: int = 32,
    min_count: int = 20,
    span_range: tuple[float, float] = (1500.0, 2600.0),
) -> tuple[float, float, float, float]:
    """返回随机城市建成区区域 (x0, z0, x1, z1)（游戏坐标）。

    按建筑密度加权随机选一格作为中心，跨度在 span_range 内随机，
    并保证区域完整落在世界范围内。
    """
    rng = rng or random.Random()
    counts = _density_grid(mapdata, grid)
    if counts.sum() == 0:
        cy = cx = grid // 2
    else:
        cand = np.argwhere(counts >= min_count)
        if len(cand) == 0:
            top = int(counts.max())
            cand = np.argwhere(counts == top)
        weights = counts[cand[:, 0], cand[:, 1]].astype(float)
        pick = rng.choices(range(len(cand)), weights=weights, k=1)[0]
        cy, cx = int(cand[pick, 0]), int(cand[pick, 1])

    cell = WORLD_SIZE / grid
    cx_w = WORLD_MIN + (cx + rng.uniform(0.3, 0.7)) * cell
    cz_w = WORLD_MIN + (cy + rng.uniform(0.3, 0.7)) * cell
    half = rng.uniform(*span_range) / 2.0
    # 中心向世界内收，保证区域不越界
    cx_w = min(max(cx_w, WORLD_MIN + half), WORLD_MIN + WORLD_SIZE - half)
    cz_w = min(max(cz_w, WORLD_MIN + half), WORLD_MIN + WORLD_SIZE - half)
    return (cx_w - half, cz_w - half, cx_w + half, cz_w + half)


def _density_grid(mapdata: MapData, grid: int,
                  bounds: tuple[float, float, float, float] | None = None
                  ) -> np.ndarray:
    """建筑质心密度网格（行=z，列=x）。

    ``bounds`` 缺省按 CS1 全图 ``[-8640, 8640]²`` 归一化；给定时按该世界范围
    归一化。**必须跟随实际幅面**：CS2 地图只有 14.336 km，若仍按 17280 归一
    化，建筑质心会全部挤进中间约 83% 的格子，「最繁华」窗口就会选偏。

    使用 ``map`` + ``attrgetter`` 将内层顶点求和推到 C 层执行，
    避免 Python 生成器表达式开销。
    """
    if bounds is None:
        x0 = z0 = WORLD_MIN
        span_x = span_z = WORLD_SIZE
    else:
        x0, z0, x1, z1 = (float(v) for v in bounds)
        span_x, span_z = x1 - x0, z1 - z0
    counts = np.zeros((grid, grid), dtype=int)
    if span_x <= 0 or span_z <= 0:
        return counts
    _get_x = attrgetter("x")
    _get_z = attrgetter("z")
    xs: list[float] = []
    zs: list[float] = []
    for b in mapdata.buildings:
        pts = b.points
        if not pts:
            continue
        n = len(pts)
        xs.append(sum(map(_get_x, pts)) / n)
        zs.append(sum(map(_get_z, pts)) / n)
    if not xs:
        return counts
    gx = np.clip(
        ((np.asarray(xs) - x0) / span_x * grid).astype(int), 0, grid - 1
    )
    gz = np.clip(
        ((np.asarray(zs) - z0) / span_z * grid).astype(int), 0, grid - 1
    )
    np.add.at(counts, (gz, gx), 1)
    return counts


def most_prosperous_window_by_size(
    mapdata: MapData,
    side_m: float,
    grid: int = 32,
) -> tuple[float, float, float, float]:
    """返回建筑最密集的 ``side_m`` 见方区域（世界坐标）。

    与 :func:`most_prosperous_window` 的区别在**窗口怎么量**：那个数的是
    CS1 的 1920m 区块格子，地图幅面一变（CS2 是 14.336 km）格子就不再对应
    任何真实地块；这里直接在实测幅面上按 ``grid × grid`` 统计，窗口边长由
    调用方按实际地块或旧范围设置给出，与代数、地图尺寸都不耦合。

    无建筑时回落到地图中心，保证始终有可用区域。
    """
    x0, z0, x1, z1 = _world_bounds(mapdata)
    span = min(x1 - x0, z1 - z0)
    if span <= 0:
        return (x0, z0, x0, z0)
    side = max(min(float(side_m), span), 1.0)
    counts = _density_grid(mapdata, grid, (x0, z0, x0 + span, z0 + span))
    if not counts.any():
        cx, cz = (x0 + x1) / 2, (z0 + z1) / 2
        return (cx - side / 2, cz - side / 2, cx + side / 2, cz + side / 2)
    cell = span / grid
    k = max(1, min(grid, int(round(side / cell))))
    wins = grid - k + 1
    # 2D 前缀和：每个 k×k 窗口的和都是 O(1)。
    integral = np.zeros((grid + 1, grid + 1), dtype=np.int64)
    integral[1:, 1:] = counts.cumsum(axis=0).cumsum(axis=1)
    best_sum, best_d = -1, float("inf")
    best_ix = best_iy = 0
    center = (wins - 1) / 2.0
    for iy in range(wins):
        for ix in range(wins):
            total = int(integral[iy + k, ix + k] - integral[iy, ix + k]
                        - integral[iy + k, ix] + integral[iy, ix])
            # 平手时优先取最靠近地图中心的窗口（曼哈顿距离最小）
            distance = abs(ix - center) + abs(iy - center)
            if total > best_sum or (total == best_sum and distance < best_d):
                best_sum, best_d = total, distance
                best_ix, best_iy = ix, iy
    # 密度网格只提供近似范围，最终窗口仍须保持在地图内部。
    wx0 = min(x0 + best_ix * cell, x1 - side)
    wz0 = min(z0 + best_iy * cell, z1 - side)
    return (wx0, wz0, wx0 + side, wz0 + side)


# ---------------------------------------------------------------------------
# 场景加权随机特写（首次运行向导 / 主题编辑器预览取景）
# ---------------------------------------------------------------------------

# 场景键 → 特征场（与主题编辑器场景下拉 / 向导页面语义一致，poi 为向导专用）
SCENE_FEATURES = (
    "overall", "terrain", "water", "roads", "railway",
    "transports", "buildings", "zones", "poi",
)


def _world_bounds(mapdata: MapData):
    """地图世界范围 (x0, z0, x1, z1)：terrain 网格优先，兜底实体包围盒外扩。

    完整存档覆盖 -8640~8640；裁剪小存档按自身地形网格范围，保证特写窗口
    始终落在有效数据内。
    """
    t = mapdata.terrain
    if t.width > 0 and t.height > 0 and t.cell > 0:
        return (t.origin_x, t.origin_z,
                t.origin_x + t.width * t.cell, t.origin_z + t.height * t.cell)
    x0, z0, x1, z1 = mapdata.bounds_2d()
    pad = 100.0
    return (x0 - pad, z0 - pad, x1 + pad, z1 + pad)


def _block_reduce(arr, origin_x: float, origin_z: float, cell: float,
                  x0: float, z0: float, x1: float, z1: float, grid: int,
                  func) -> np.ndarray:
    """把世界范围映射到 grid×grid，每格对 arr 块执行 func（mean/std 等）。"""
    rows, cols = arr.shape
    out = np.zeros((grid, grid), dtype=float)
    if rows == 0 or cols == 0 or cell <= 0:
        return out
    for gz in range(grid):
        za = int(np.floor((z0 + (z1 - z0) * gz / grid - origin_z) / cell))
        zb = int(np.ceil((z0 + (z1 - z0) * (gz + 1) / grid - origin_z) / cell))
        za = max(0, min(rows, za))
        zb = max(0, min(rows, zb))
        if zb <= za:
            continue
        for gx in range(grid):
            xa = int(np.floor((x0 + (x1 - x0) * gx / grid - origin_x) / cell))
            xb = int(np.ceil((x0 + (x1 - x0) * (gx + 1) / grid - origin_x) / cell))
            xa = max(0, min(cols, xa))
            xb = max(0, min(cols, xb))
            if xb <= xa:
                continue
            out[gz, gx] = func(arr[za:zb, xa:xb])
    return out


def _point_grid(xs, zs, grid: int, x0: float, z0: float,
                x1: float, z1: float) -> np.ndarray:
    """点集 → grid×grid 计数网格（行=z，列=x）。"""
    out = np.zeros((grid, grid), dtype=float)
    if not xs:
        return out
    gx = np.clip(((np.asarray(xs) - x0) / (x1 - x0) * grid).astype(int), 0, grid - 1)
    gz = np.clip(((np.asarray(zs) - z0) / (z1 - z0) * grid).astype(int), 0, grid - 1)
    np.add.at(out, (gz, gx), 1)
    return out


def _seg_len_grid(mapdata: MapData, grid: int, x0: float, z0: float,
                  x1: float, z1: float, pred) -> np.ndarray:
    """线段折线长度网格：类型满足 pred 的段按中点归格、长度累加。"""
    acc = np.zeros((grid, grid), dtype=float)
    span_x, span_z = x1 - x0, z1 - z0
    if span_x <= 0 or span_z <= 0:
        return acc
    for seg in mapdata.segments:
        st = (mapdata.segment_types.get(seg.name)
              or mapdata.segment_types.get(seg.icls))
        if st is None or not pred(st):
            continue
        pts = seg.points
        if len(pts) < 2:
            continue
        length = 0.0
        mx = mz = 0.0
        px, pz = pts[0].x, pts[0].z
        for p in pts[1:]:
            length += math.hypot(p.x - px, p.z - pz)
            px, pz = p.x, p.z
        for p in pts:
            mx += p.x
            mz += p.z
        n = len(pts)
        if length <= 0:
            continue
        gx = int((mx / n - x0) / span_x * grid)
        gz = int((mz / n - z0) / span_z * grid)
        if 0 <= gx < grid and 0 <= gz < grid:
            acc[gz, gx] += length
    return acc


def _feature_grid(mapdata: MapData, scene: str, grid: int,
                  x0: float, z0: float, x1: float, z1: float) -> np.ndarray:
    """场景键 → 特征网格（值越大越可能被特写）。"""
    t = mapdata.terrain
    if scene == "water":
        if t.watermap is not None and t.watermap.size:
            return _block_reduce(t.watermap, t.origin_x, t.origin_z, t.cell,
                                 x0, z0, x1, z1, grid, lambda a: (a > 0.2).mean())
        return np.zeros((grid, grid))
    if scene == "terrain":
        if t.heightmap is not None and t.heightmap.size:
            return _block_reduce(t.heightmap, t.origin_x, t.origin_z, t.cell,
                                 x0, z0, x1, z1, grid, np.std)
        return np.zeros((grid, grid))
    if scene == "roads":
        return _seg_len_grid(mapdata, grid, x0, z0, x1, z1,
                             lambda st: bool(st.road or st.highway))
    if scene == "railway":
        return _seg_len_grid(mapdata, grid, x0, z0, x1, z1,
                             lambda st: bool(st.is_rail))
    if scene == "transports":
        xs, zs = [], []
        for tr in mapdata.transports:
            for stop in tr.stops:
                no = mapdata.node(stop.node_id)
                if no is not None:
                    xs.append(no.pos.x)
                    zs.append(no.pos.z)
        return _point_grid(xs, zs, grid, x0, z0, x1, z1)
    if scene == "buildings":
        xs, zs = [], []
        for b in mapdata.buildings:
            pts = b.points
            if not pts:
                continue
            n = len(pts)
            xs.append(sum(p.x for p in pts) / n)
            zs.append(sum(p.z for p in pts) / n)
        return _point_grid(xs, zs, grid, x0, z0, x1, z1)
    if scene == "zones":
        xs, zs = [], []
        for d in visible_administrative_districts(mapdata):
            for gx_, gz_ in d.grid_points:
                xs.append(gx_)
                zs.append(gz_)
        for pk in mapdata.parks:
            for gx_, gz_ in pk.grid_points:
                xs.append(gx_)
                zs.append(gz_)
        return _point_grid(xs, zs, grid, x0, z0, x1, z1)
    if scene == "poi":
        xs, zs = [], []
        for po in mapdata.procedural_objects:
            xs.append(po.pos.x)
            zs.append(po.pos.z)
        for tr in mapdata.transports:
            if tr.type == "Bus":
                for stop in tr.stops:
                    no = mapdata.node(stop.node_id)
                    if no is not None:
                        xs.append(no.pos.x)
                        zs.append(no.pos.z)
        return _point_grid(xs, zs, grid, x0, z0, x1, z1)
    # overall：建筑为主、道路/轨道为辅的混合场（各特征归一后加权）
    b = _feature_grid(mapdata, "buildings", grid, x0, z0, x1, z1)
    out = b.astype(float)
    for scene_key, weight in (("roads", 0.6), ("railway", 0.4)):
        f = _feature_grid(mapdata, scene_key, grid, x0, z0, x1, z1)
        if f.max() > 0:
            out += weight * f / f.max()
    return out


def closeup_region(
    mapdata: MapData,
    scene: str = "overall",
    rng: random.Random | None = None,
    grid: int = 24,
    span_frac: tuple[float, float] = (0.15, 0.30),
    span_range: tuple[float, float] = (400.0, 2500.0),
) -> tuple[float, float, float, float]:
    """按场景特征加权随机选取特写区域 (x0, z0, x1, z1)（游戏坐标）。

    scene 见 SCENE_FEATURES：铁路设置特写轨道密集区、水域特写水面、
    地形特写起伏山地……特征为 0 的地图回退到中心区域。跨度按地图尺寸
    自适应（span_frac 比例），并钳制在 span_range 内。
    """
    rng = rng or random.Random()
    if scene not in SCENE_FEATURES:
        scene = "overall"
    x0, z0, x1, z1 = _world_bounds(mapdata)
    w, h = x1 - x0, z1 - z0
    if w <= 0 or h <= 0:
        return (x0, z0, x1, z1)
    feat = _feature_grid(mapdata, scene, grid, x0, z0, x1, z1)
    if feat.sum() <= 0:
        gy = gx = grid // 2
    else:
        cand = np.argwhere(feat > 0)
        weights = feat[cand[:, 0], cand[:, 1]].astype(float)
        pick = rng.choices(range(len(cand)), weights=weights, k=1)[0]
        gy, gx = int(cand[pick, 0]), int(cand[pick, 1])
    cell_w = w / grid
    cx = x0 + (gx + rng.uniform(0.25, 0.75)) * cell_w
    cz = z0 + (gy + rng.uniform(0.25, 0.75)) * cell_w
    # 跨度：地图尺寸比例自适应，钳制在 [span_range] 且不越界
    half = rng.uniform(span_frac[0], span_frac[1]) * min(w, h) / 2.0
    half = min(max(half, span_range[0] / 2.0), span_range[1] / 2.0)
    half = min(half, min(w, h) / 2.0 - 1.0)
    if half <= 0:
        half = min(w, h) / 4.0
    cx = min(max(cx, x0 + half), x1 - half)
    cz = min(max(cz, z0 + half), z1 - half)
    return (cx - half, cz - half, cx + half, cz + half)
