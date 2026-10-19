"""三层地表地面模型（Apple Maps 风格）。

把非建成区地表概括为三层：
1. 建成区：白色硬边（由 BuiltAreaLayer 负责，本模块只提供距离场）；
2. 郊区：黄绿 → 浅绿，按"离建成区边缘的距离"线性渐变（只有这一处渐变）；
3. 山地：显示海拔超过阈值后**硬切换**到更深的绿色带，带内部再随海拔渐变。

供 TerrainLayer（整图逐像素设色）、BuiltAreaLayer（建成区孔洞取质心地色）
与 SVG 导出（降级对齐）共用。距离场在渲染准备阶段全图只计算一次，
经 ctx.shared / renderer shared 缓存（键 GROUND_DIST_KEY）。
"""
from __future__ import annotations

import math

import numpy as np

from .built_area import DEFAULT_EXCLUDE_SERVICES, WORLD_MIN, WORLD_SIZE, _build_mask

# 距离场在渲染共享缓存中的键（与山影缓存 "terrain.hillshade" 同机制）
GROUND_DIST_KEY = "terrain.ground_dist"

# 距离场网格默认精度（米）：渐变带通常上千米，32m 足够
DIST_CELL_METERS = 32.0

# 山地带缺省色阶（相对 mountain_threshold 的海拔偏移）：
# 主题未配置 mountain_colors 时使用，保证硬切换后仍有"更深一档绿"。
_DEFAULT_MOUNTAIN_RAMP = (
    (0.0, "#9FC37F"),
    (380.0, "#82AA65"),
    (980.0, "#6F995B"),
)


def _hex_to_rgb(color: str):
    """解析 #RRGGBB / #RGB 为 (r,g,b)。"""
    c = str(color).strip().lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))


def parse_ground_cfg(cfg) -> dict | None:
    """解析主题中的 ``terrain.ground`` 配置；未启用返回 None。

    返回 {"near", "far", "ramp", "mt", "m_thr", "m_lut"}：
    - near/far: 郊区渐变两端色 (r,g,b)；ramp: 渐变带长度（米）；
    - mt: 山地硬切换阈值（显示海拔，相对海平面）；
    - m_thr/m_lut: 山地带阈值/颜色表（带内连续插值，与地形设色同逻辑）。
    """
    if not isinstance(cfg, dict) or not bool(cfg.get("enabled", False)):
        return None
    try:
        near = _hex_to_rgb(cfg.get("near_green", "#E2ECB6"))
        far = _hex_to_rgb(cfg.get("far_green", "#CBE2A6"))
        ramp = max(1.0, float(cfg.get("ramp_meters", 2400.0)))
        mt = float(cfg.get("mountain_threshold", 220.0))
    except (TypeError, ValueError):
        return None

    entries = cfg.get("mountain_colors") or []
    entries = [e for e in entries if isinstance(e, dict)
               and isinstance(e.get("threshold"), (int, float))
               and isinstance(e.get("color"), str)]
    if not entries:
        entries = [{"threshold": mt + off, "color": color}
                   for off, color in _DEFAULT_MOUNTAIN_RAMP]
    entries.sort(key=lambda e: float(e["threshold"]))
    m_thr = np.asarray([float(e["threshold"]) for e in entries],
                       dtype=np.float32)
    try:
        m_lut = np.asarray([_hex_to_rgb(e["color"]) for e in entries],
                           dtype=np.uint8)
    except (ValueError, IndexError):
        m_lut = np.asarray([_hex_to_rgb(c) for _, c in _DEFAULT_MOUNTAIN_RAMP],
                           dtype=np.uint8)
        m_thr = np.asarray([mt + off for off, _ in _DEFAULT_MOUNTAIN_RAMP],
                           dtype=np.float32)
    return {"near": near, "far": far, "ramp": ramp,
            "mt": mt, "m_thr": m_thr, "m_lut": m_lut}


# ---------------------------------------------------------------- 距离场


def _chamfer_distance(inside: np.ndarray, cell: float,
                      clamp: float) -> np.ndarray:
    """两遍 chamfer（3-4 距离）求各格到最近建成区格的距离（米）。

    行内传播用 ``np.minimum.accumulate`` 精确等价顺序扫描
    （min-plus 与 [0,1,2,...] 核的卷积 = j + min_{k<=j}(base[k]-k)），
    行间只依赖上一完整行，与标准两遍扫描语义一致。
    """
    sq2 = math.sqrt(2.0)
    inf = np.float32(clamp / max(cell, 1e-9) + 1e6)
    d = np.where(inside, np.float32(0.0), inf).astype(np.float32)
    h, w = d.shape
    col_idx = np.arange(w, dtype=np.float32)

    def relax_row(i: int, nb) -> None:
        """用相邻行 nb 松弛第 i 行，再做行内双向距离传播。"""
        row = d[i]
        if nb is not None:
            row = np.minimum(row, nb + 1.0)
            cand = np.empty_like(row)
            cand[1:] = nb[:-1] + sq2
            cand[0] = inf
            row = np.minimum(row, cand)
            cand = np.empty_like(row)
            cand[:-1] = nb[1:] + sq2
            cand[-1] = inf
            row = np.minimum(row, cand)
        # 左→右：d[j] = min_k<=j(base[k] + (j-k))
        left = np.minimum.accumulate(row - col_idx) + col_idx
        # 右→左（翻转同法）
        rr = row[::-1]
        right = (np.minimum.accumulate(rr - col_idx) + col_idx)[::-1]
        d[i] = np.minimum(left, right)

    for i in range(h):
        relax_row(i, d[i - 1] if i > 0 else None)
    for i in range(h - 1, -1, -1):
        relax_row(i, d[i + 1] if i < h - 1 else None)

    return np.minimum(d * np.float32(cell), np.float32(clamp))


def builtup_distance_field(
    source,
    cell_meters: float = DIST_CELL_METERS,
    buffer_meters: float = 60.0,
    clamp_meters: float = 4000.0,
) -> dict | None:
    """全图建成区距离场（到建成区边缘的近似欧氏距离，单位米）。

    source: :class:`MapDocument`（首选）或 legacy ``MapData``（过渡）。
    返回 {"dist": float32 数组（行=世界 Z，列=世界 X，与高度图原始网格
    同轴向）, "origin_x", "origin_z", "cell"}，或无建成区时 None。
    掩膜复用 built_area._build_mask（排除非房屋设施 + 膨胀弥合间隙），
    与建成区底色的"城市范围"口径一致。
    """
    if not getattr(source, "buildings", None):
        return None
    cell_meters = max(float(cell_meters), 4.0)
    got = _build_mask(
        source, cell_meters, DEFAULT_EXCLUDE_SERVICES,
        WORLD_MIN, WORLD_MIN, WORLD_MIN + WORLD_SIZE, WORLD_MIN + WORLD_SIZE,
    )
    if got is None:
        return None
    mask_img, ox, oz = got
    arr = np.asarray(mask_img, dtype=np.uint8)
    if not (arr > 0).any():
        return None
    dist = _chamfer_distance(arr > 127, cell_meters, clamp_meters)
    dist = np.maximum(dist - max(float(buffer_meters), 0.0), 0.0)
    return {"dist": dist, "origin_x": ox, "origin_z": oz,
            "cell": cell_meters}


def distance_at(field: dict | None, wx: float, wz: float) -> float:
    """取世界坐标处的距离场值（米）；无距离场返回无穷大（→ 远端色）。"""
    if not field:
        return math.inf
    dist = field["dist"]
    cell = field["cell"]
    gh, gw = dist.shape
    col = int((wx - field["origin_x"]) / cell)
    row = int((wz - field["origin_z"]) / cell)
    col = min(max(col, 0), gw - 1)
    row = min(max(row, 0), gh - 1)
    return float(dist[row, col])


# ---------------------------------------------------------------- 判色


def shade_ground(values: np.ndarray, dist: np.ndarray,
                 p: dict) -> np.ndarray:
    """逐像素地面设色（向量化）。

    values: 显示海拔（相对海平面），(h, w) float；dist: 距离（米），同形。
    分类按阈值硬判断：values >= mt 走山地带（带内连续插值），否则低地
    按距离在 near→far 间线性插值——两处带间均为突变，无跨带插值。
    """
    near = np.asarray(p["near"], dtype=np.float32)
    far = np.asarray(p["far"], dtype=np.float32)
    t = np.clip(dist.astype(np.float32) / float(p["ramp"]), 0.0, 1.0)
    out = np.clip(near + (far - near) * t[..., None], 0, 255).astype(np.uint8)

    mtn = values >= float(p["mt"])
    if mtn.any():
        v = values[mtn].astype(np.float32)
        thr, lut = p["m_thr"], p["m_lut"]
        hi = np.searchsorted(thr, v, side="right")
        lo = np.clip(hi - 1, 0, len(lut) - 1)
        hi = np.clip(hi, 0, len(lut) - 1)
        span = thr[hi] - thr[lo]
        frac = np.divide(v - thr[lo], span,
                         out=np.zeros_like(v, dtype=np.float32),
                         where=span > 0)
        blended = (lut[lo].astype(np.float32)
                   + (lut[hi].astype(np.float32)
                      - lut[lo].astype(np.float32)) * frac[:, None])
        out[mtn] = np.clip(blended, 0, 255).astype(np.uint8)
    return out


def ground_color_at(elev: float, dist: float, p: dict):
    """单点地面色（标量版，供建成区孔洞取质心地色）。判色规则同
    shade_ground：阈值硬边 + 带内线性插值。"""
    if elev >= float(p["mt"]):
        thr, lut = p["m_thr"], p["m_lut"]
        idx = int(np.searchsorted(thr, float(elev), side="right")) - 1
        idx = min(max(idx, 0), len(lut) - 1)
        hi = min(idx + 1, len(lut) - 1)
        span = float(thr[hi] - thr[idx])
        frac = (float(elev) - float(thr[idx])) / span if span > 0 else 0.0
        rgb = (lut[idx].astype(np.float32)
               + (lut[hi].astype(np.float32)
                  - lut[idx].astype(np.float32)) * float(np.clip(frac, 0, 1)))
        return tuple(int(v) for v in np.clip(rgb, 0, 255))
    t = min(max(float(dist) / float(p["ramp"]), 0.0), 1.0)
    near, far = p["near"], p["far"]
    return tuple(int(near[i] + (far[i] - near[i]) * t) for i in range(3))


def effective_elevation_colors(p: dict) -> list[dict]:
    """把地面模型合成等价的海拔设色表（供 SVG 导出等矢量路径降级使用）。

    低地统一用远端浅绿（距离渐变无法矢量化表达），山地保留分档色；
    阈值处重复条目制造硬边。
    """
    far_hex = "#{:02X}{:02X}{:02X}".format(*p["far"])
    mt = float(p["mt"])
    entries = [{"threshold": 0.0, "color": far_hex},
               {"threshold": mt, "color": far_hex}]
    for thr, rgb in zip(p["m_thr"], p["m_lut"]):
        entries.append({
            "threshold": float(thr),
            "color": "#{:02X}{:02X}{:02X}".format(*rgb),
        })
    return entries
