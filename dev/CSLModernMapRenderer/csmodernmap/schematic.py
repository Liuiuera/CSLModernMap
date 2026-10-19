"""地铁示意图布局 v3：Bast 网格 A* 引擎（EuroVis 2020 近似算法）。

算法：《Metro Maps on Octilinear Grid Graphs》
(Bast, Brosi, Storandt) 的实用近似版本：

1. 站点合并组（逻辑站）snap 到站距网格（spacing 米/格）
2. 同格冲突消解（不同逻辑组错开到最近空格）
3. 线路按站点数降序逐线 A* 路由，代价：
   - 步长（直 1 / 斜 √2）
   - 拐弯惩罚（直行 0 / 45° 1 / 90° 2.5 / 135° 4 / 折返 8）
     —— 拐弯全局最少，消除锯齿
   - 拥堵惩罚（网格边已被 k 条线占用 → +CONGESTION*k）
     —— 后布局的线自动绕开繁忙走廊，共线重叠自然分离
   - 穿站惩罚（路径经过其他逻辑站格点 → 大惩罚，避免穿站）
   对向线路（站点序列相同/互逆/环双向旋转等价）只路由一次，
   其余复制主线路由——双向线独立寻路会互相抢道，被拥堵惩罚
   推到邻边产生平行四边形伪影；绘制层按颜色分组共槽/偏移
4. 换乘 = 逻辑组（同组多线强制同格点，天然对齐闭合）
5. 未放置站的落点在真实位置候选邻域内由 A* 全局择优
   —— 保持相对拓扑，无 v2 的锚点拉扯
6. 站间段简化：路由完成后每段收敛为 ≤1 拐点的 L 折线
   （对齐则直线；两候选均被其他站阻挡时保留 A* 原路径），
   消除多拐锯齿（三角形/平行四边形伪影）；随后重建网格边占用
7. 可选的自适应比例尺先为密集站区预留网格间距并重跑 A*，再构建
   非均匀坐标轴；长区段压缩、密集站区舒展，线路与站点锚点保持一致
8. 固定模式在网格域完成后等比缩放 + 居中 fit 画布；动态模式额外
   校验变形后的斜边角度，超限时降低强度或回退固定模式

严格入口保留拓扑校验；交互入口重试耗尽后可生成地理草图，保留连接供编辑。
"""
from __future__ import annotations

import heapq
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .data import MapData, Transport
from .line_topology import normalize_station_sequence

# 八方向：(dx, dy)，索引 0-7 顺时针
_DIRS = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]
_DIAG = {1, 3, 5, 7}
_SQRT2 = math.sqrt(2.0)

# 拐弯惩罚表（方向索引差 → 代价）：直行 0 / 45° 1 / 90° 2.5 / 135° 4 / 折返 8
_BEND_COST = (0.0, 1.0, 2.5, 4.0, 8.0)
# 真拥堵（穿越）：每条与当前步垂直/斜穿的已有占用追加的代价
_CONGESTION = 3.0
# 走廊共享（手挽手）：边占用与本步同向（含反向平行）时每线折扣的代价。
# 必须 < 1.0/(_CORRIDOR_CAP-1) 保证步代价恒正
_CORRIDOR_BONUS = 0.22
# 走廊容量：同向并行超过该线数后恢复惩罚（防全部线坍缩成一束）
_CORRIDOR_CAP = 4
# 骨架走廊：宽度（格，骨架两侧 BFS 深度）与出走廊每步惩罚。
# 中间站路由被约束贴骨架走，不再满图乱跑（P2）
_CORRIDOR_WIDTH = 3
_CORRIDOR_PENALTY = 2.0
# 布局域近距站合并：snap 后格距离 ≤ _STATION_MERGE_GRID 的逻辑组
# 聚成一簇共用一格（近距站视觉合并为大站，名字并列显示）
_STATION_MERGE_GRID = 0.8
# 邻边平行吸引：相隔 1 格的平行邻边同向占用折扣系数（× _CORRIDOR_BONUS）。
# 近距平行线被吸到相邻边（视觉平行束），同边共享折扣为主、邻边为辅
_PARALLEL_EDGE_FACTOR = 0.5
# 路径经过其他逻辑站格点的惩罚
_STATION_BLOCK = 12.0
# 换乘站不再使用第二套 snap 比例尺。视觉留白由冲突消解和线路几何
# 处理负责；改变比例尺会把共享锚点移动到另一坐标系，造成线路折返。
# A* 搜索格子数上限（防失控；超限 = 布局失败）
_ASTAR_LIMIT = 60000
# 普通线路局部 A* 失败时，回滚最近的已定稿段并禁止其旧边，
# 为后续站点重新寻找整线可行解。
_LINE_REPLAN_LIMIT = 16
# 后置非均匀坐标轴允许的最大八方向角度偏差。超过此值时，调用层会
# 降低动态比例尺强度，仍不满足则回退到固定网格布局。
# 非均匀轴会让局部斜线偏离 45°；适度放宽阈值，避免把“2 倍基线”
# 的有效舒展力度过早降级为弱效果。线路拓扑和直角/折线路由仍保持不变。
_ADAPTIVE_ANGLE_TOLERANCE_DEG = 19.0


def orient_world(x: float, z: float, rotation: int = 0) -> Tuple[float, float]:
    """按主图同源方向把世界 ``(x, z)`` 转成屏幕布局坐标。

    主图的基础方向为 ``(-z, -x)``，再施加用户选择的顺时针旋转。
    示意图必须在进入 snap/A* 前使用同一矩阵；若直接使用 ``(x, z)``，
    90° 时会得到 ``(x, z)`` 对 ``(x, -z)`` 的垂直镜像。
    """
    rot = int(rotation or 0) % 360
    if rot == 0:
        return (-z, -x)
    if rot == 90:
        return (x, -z)
    if rot == 180:
        return (z, x)
    if rot == 270:
        return (-x, z)
    raise ValueError(f"rotation must be 0/90/180/270, got {rotation!r}")


class SchematicLayoutError(Exception):
    """一次网格布局不可行（数据异常 / 搜索超限）。"""


def _station_order_ok(current, candidate, positions, world, neighbors):
    for other in neighbors.get(current, ()):
        if other not in positions:
            continue
        for axis in (0, 1):
            delta = world[current][axis]-world[other][axis]
            if abs(delta) > 50 and delta*(candidate[axis]-positions[other][axis]) < 0:
                return False
    return True






def _place_station_grid(world, neighbors, line_counts, pressure, spacing, manual, dense_gap):
    """站点布局受限于原候选区域，局部重分配不会扩张候选域。"""
    ideal = {ident: (p[0]/spacing, p[1]/spacing) for ident, p in world.items()}
    domains = {}
    for ident, (x, y) in ideal.items():
        domains[ident] = [(gx, gy) for gx in range(math.floor(x-2), math.ceil(x+2)+1)
                         for gy in range(math.floor(y-2), math.ceil(y+2)+1)
                         if math.hypot(gx-x, gy-y) <= 2+1e-9]
    positions = dict(manual)
    if len(set(positions.values())) != len(positions):
        raise SchematicLayoutError("manual stations occupy the same grid cell")

    def score(ident, cell):
        displacement = math.dist(ideal[ident], cell)
        clearance = sum(max(abs(cell[0]-point[0]), abs(cell[1]-point[1])) <= dense_gap
                        for other, point in positions.items() if other != ident)
        return (displacement, clearance*pressure.get(ident, 0), cell)

    def place(ident, stack, budget):
        if budget[0] <= 0 or len(stack) >= 4:
            return False
        for cell in sorted(domains[ident], key=lambda c: score(ident, c)):
            if budget[0] <= 0:
                break
            budget[0] -= 1
            conflicts = {other for other, point in positions.items()
                         if point == cell or (other in neighbors.get(ident, ()) and
                             not _station_order_ok(ident, cell, {other: point}, world, neighbors))}
            if conflicts.intersection(manual) or conflicts.intersection(stack) or len(conflicts) > 2:
                continue
            saved = dict(positions)
            for other in conflicts:
                del positions[other]
            positions[ident] = cell
            if all(place(other, stack | {ident}, budget) for other in sorted(conflicts)):
                # 不要让邻近的未放置站点失去位置。
                future = [other for other in neighbors.get(ident, ()) if other not in positions]
                occupied = set(positions.values())
                if all(any(c not in occupied and _station_order_ok(other, c, positions, world, neighbors)
                           for c in domains[other]) for other in future):
                    return True
            positions.clear()
            positions.update(saved)
        return False

    order = sorted(world, key=lambda ident: (-line_counts.get(ident, 0), -pressure.get(ident, 0), ident))
    for ident in order:
        if ident not in manual and not place(ident, set(), [256]):
            raise SchematicLayoutError(f"station {ident}: no placement within two grid cells")
    for ident, cell in positions.items():
        if not _station_order_ok(ident, cell, positions, world, neighbors):
            raise SchematicLayoutError(f"station {ident}: manual/automatic station order conflict")
    return positions


def schematic_layout_with_fallback(mapdata, transports, merged, enabled,
                                   *, allow_geographic=True, **kwargs):
    """先尝试严格布局，失败时保留源连接关系并标记为草稿。"""
    fixed = {**kwargs, "adaptive_scale": False}
    spacing = float(kwargs.get("spacing", 400.0))
    attempts = [("requested", kwargs)]
    if kwargs.get("adaptive_scale", False):
        attempts.append(("fixed", fixed))
    for factor in (0.5, 0.25):
        attempts.append((f"fine_{factor}", {
            **fixed, "spacing": max(spacing * factor, 1.0),
            "merge_nearby_stations": False, "share_corridors": False,
        }))
    failures = []
    for strategy, options in attempts:
        try:
            result = schematic_layout(mapdata, transports, merged, enabled, **options)
        except SchematicLayoutError as exc:
            failures.append(f"{strategy}: {exc}")
            continue
        result.fallback_strategy = strategy
        result.layout_failures = tuple(failures)
        return result
    if allow_geographic and transports:
        result = schematic_layout(mapdata, transports, merged, enabled,
                                  **{**fixed, "geographic": True})
        result.fallback_strategy = "geographic"
        result.layout_failures = tuple(failures)
        return result
    raise SchematicLayoutError("; ".join(failures))


@dataclass
class SchematicResult:
    """布局输出（网格域 + 像素变换参数）。

    严格模式 routes / edge_lines 在网格域；地理草图的 routes / node_grid
    保留定向后的世界坐标，不经过网格走线。绘制层经 ``to_px`` 转像素。
    positions 已是像素（站点绘制直接用）。
    """

    positions: Dict[int, Tuple[float, float]] = field(default_factory=dict)
    # 新渲染使用站点语义键到像素位置的映射；旧数字节点视图仅用于兼容，避免不同交通模式的同址站点合并。
    stop_positions: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    # id(transport) → 网格点列（含站点与拐点，环线首尾闭合）
    routes: Dict[int, List[Tuple[float, float]]] = field(default_factory=dict)
    # id(transport) → 站点顶点索引（routes 下标，有序）
    station_indices: Dict[int, List[int]] = field(default_factory=dict)
    # 分支线路除主链外的独立几何部件；每个部件拥有自己的站点索引。
    route_parts: Dict[int, List[List[Tuple[float, float]]]] = field(default_factory=dict)
    part_station_indices: Dict[int, List[List[int]]] = field(default_factory=dict)
    # 归一化网格边 (ax,ay,bx,by) a<b → [id(transport)]（稳定线序）
    edge_lines: Dict[Tuple[int, int, int, int], List[int]] = field(default_factory=dict)
    # 物理走廊：无向站对 (min_gid,max_gid) → 经过的线路 id（稳定线序）
    corridor_lines: Dict[Tuple[int, int], List[int]] = field(default_factory=dict)
    # 逻辑组 id → 网格格点
    node_grid: Dict[int, Tuple[float, float]] = field(default_factory=dict)
    # 布局域近距站合并（P4）：组 id → 簇代表组 id（未合并组无条目）
    cluster_of: Dict[int, int] = field(default_factory=dict)
    # 簇代表组 id → 成员站名（有序去重，绘制并列显示）
    cluster_names: Dict[int, List[str]] = field(default_factory=dict)
    # 对向线路合并（P6）：id(transport) → 主线 id(transport)
    # （主线无条目；绘制层徽章去重 / 方向区分用）
    route_alias: Dict[int, int] = field(default_factory=dict)
    # 对向线路双向映射（P6+）：主线 id(transport) → [对向线 id(transport), ...]
    # 绘制层用此信息做方向感知的对称并行偏移
    alias_pairs: Dict[int, List[int]] = field(default_factory=dict)
    # id(transport) → (拓扑类型, 原始腿数, 折叠腿数)
    topology_stats: Dict[int, Tuple[str, int, int]] = field(default_factory=dict)
    # 网格 → 像素：px = gx*scale+ox, py = gy*scale+oz
    transform: Tuple[float, float, float] = (1.0, 0.0, 0.0)
    spacing_px: float = 0.0   # fit 后站距像素（绘制 stub/偏移参考）
    # 动态比例尺的非均匀网格坐标（整数网格坐标 → 像素）。保留 transform
    # 作为旧调用方的线性回退；动态坐标只在示意图布局内部使用。
    adaptive_x: Optional[Dict[int, float]] = None
    adaptive_y: Optional[Dict[int, float]] = None
    spacing_m: float = 0.0
    adaptive_strength: float = 0.0
    # 实际动态轴局部间距 / 固定网格间距，便于图例和调试信息展示。
    adaptive_scale_range: Tuple[float, float] = (1.0, 1.0)
    fallback_strategy: str = "requested"
    layout_failures: Tuple[str, ...] = ()
    # 保留稳定的源线路和逻辑站点标识，供后续编辑使用。
    source_chains: Dict[int, Tuple[Tuple[int, ...], ...]] = field(default_factory=dict)
    placement_metrics: dict = field(default_factory=dict)
    rotation: int = 0
    def to_px(self, gx: float, gy: float) -> Tuple[float, float]:
        s, ox, oz = self.transform
        if self.adaptive_x is None or self.adaptive_y is None:
            return (gx * s + ox, gy * s + oz)

        def value(mapping: Dict[int, float], coord: int) -> float:
            if coord in mapping:
                return mapping[coord]
            keys = sorted(mapping)
            if not keys:
                return coord * s + (ox if mapping is self.adaptive_x else oz)
            if coord <= keys[0]:
                return mapping[keys[0]] + (coord - keys[0]) * s
            if coord >= keys[-1]:
                return mapping[keys[-1]] + (coord - keys[-1]) * s
            lo = max(k for k in keys if k < coord)
            hi = min(k for k in keys if k > coord)
            t = (coord - lo) / (hi - lo)
            return mapping[lo] + (mapping[hi] - mapping[lo]) * t

        return (value(self.adaptive_x, gx), value(self.adaptive_y, gy))


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def edge_key(a: Tuple[int, int], b: Tuple[int, int]) -> Tuple[int, int, int, int]:
    """无向网格边归一化 key（小端在前）。布局与绘制层共用。"""
    if a <= b:
        return (a[0], a[1], b[0], b[1])
    return (b[0], b[1], a[0], a[1])


_edge_key = edge_key


def _bend(d1: int, d2: int) -> float:
    """方向索引间的拐弯代价。"""
    diff = (d1 - d2) % 8
    if diff > 4:
        diff = 8 - diff
    return _BEND_COST[diff]


def _octile(x: int, y: int, gx: int, gy: int) -> float:
    """八方向启发距离（可采纳）。"""
    dx = abs(x - gx)
    dy = abs(y - gy)
    mx, mn = max(dx, dy), min(dx, dy)
    return mx + (_SQRT2 - 1.0) * mn


# ---------------------------------------------------------------------------
# A* 路由（状态 = 格点 + 入方向）
# ---------------------------------------------------------------------------

def _dir_of(a: Tuple[int, int], b: Tuple[int, int]) -> int:
    """格步 a→b 的方向索引（八方向保证存在）。"""
    return _DIRS.index((b[0] - a[0], b[1] - a[1]))


def _dir_of_safe(a: Tuple[int, int], b: Tuple[int, int]) -> Optional[int]:
    """格步方向索引；非八方向步（走廊对齐移站后的越界步/零步）返回 None。"""
    step = (b[0] - a[0], b[1] - a[1])
    try:
        return _DIRS.index(step)
    except ValueError:
        return None


def _record_edges(route: List[Tuple[int, int]], tr_id: int,
                  edge_use: Dict[Tuple[int, int, int, int],
                                 List[Tuple[int, int]]]) -> None:
    """记录路径的边占用：每边追加 (线路id, 规范方向)。

    规范方向 = 边 key 小端 → 大端的方向索引，使同边的所有"沿边"
    占用（无论行进方向）方向一致，走廊判定 O(1)。
    """
    for j in range(len(route) - 1):
        a, b = route[j], route[j + 1]
        nd = _dir_of(a, b)
        od = nd if a <= b else (nd + 4) % 8
        edge_use.setdefault(_edge_key(a, b), []).append((tr_id, od))


def _segments_intersect(a: Tuple[int, int], b: Tuple[int, int],
                        c: Tuple[int, int], d: Tuple[int, int]) -> bool:
    """整数网格线段相交判定，包含端点接触和共线重叠。"""
    def orient(p: Tuple[int, int], q: Tuple[int, int],
               r: Tuple[int, int]) -> int:
        value = (q[0] - p[0]) * (r[1] - p[1]) \
            - (q[1] - p[1]) * (r[0] - p[0])
        return (value > 0) - (value < 0)

    def on_segment(p: Tuple[int, int], q: Tuple[int, int],
                   r: Tuple[int, int]) -> bool:
        return (min(p[0], r[0]) <= q[0] <= max(p[0], r[0])
                and min(p[1], r[1]) <= q[1] <= max(p[1], r[1]))

    o1 = orient(a, b, c)
    o2 = orient(a, b, d)
    o3 = orient(c, d, a)
    o4 = orient(c, d, b)
    if o1 == 0 and on_segment(a, c, b):
        return True
    if o2 == 0 and on_segment(a, d, b):
        return True
    if o3 == 0 and on_segment(c, a, d):
        return True
    if o4 == 0 and on_segment(c, b, d):
        return True
    return o1 != o2 and o3 != o4


def _segments_properly_cross(a: Tuple[float, float], b: Tuple[float, float],
                             c: Tuple[float, float], d: Tuple[float, float]
                             ) -> bool:
    """只识别两条线段内部的横穿，不包含端点接触或共线重叠。"""
    def orient(p: Tuple[float, float], q: Tuple[float, float],
               r: Tuple[float, float]) -> float:
        return ((q[0] - p[0]) * (r[1] - p[1])
                - (q[1] - p[1]) * (r[0] - p[0]))

    o1, o2 = orient(a, b, c), orient(a, b, d)
    o3, o4 = orient(c, d, a), orient(c, d, b)
    eps = 1e-9
    return ((o1 > eps and o2 < -eps) or (o1 < -eps and o2 > eps)) \
        and ((o3 > eps and o4 < -eps) or (o3 < -eps and o4 > eps))


def _leg_pair(a: int, b: int) -> Tuple[int, int]:
    return (a, b) if a < b else (b, a)


def _route_edge_owners(route: List[Tuple[int, int]],
                       station_idx: List[int]) -> List[int]:
    """把最终路由的每条网格边映射回其来源站间腿。"""
    owners = [-1] * max(len(route) - 1, 0)
    for leg, (start, end) in enumerate(zip(station_idx, station_idx[1:])):
        for edge_i in range(start, end):
            owners[edge_i] = leg
    if station_idx:
        for edge_i in range(station_idx[-1], len(route) - 1):
            owners[edge_i] = len(station_idx) - 1
    return owners


def _source_leg_crossings(
    core: List[int],
    group_pos: Dict[int, Tuple[float, float]],
    closed: bool,
) -> Set[Tuple[int, int]]:
    """从原始站序位置提取有来源证据的合法跨越站间腿对。"""
    legs = list(zip(core, core[1:]))
    if closed and len(core) >= 3:
        legs.append((core[-1], core[0]))
    allowed: Set[Tuple[int, int]] = set()
    for i, (a, b) in enumerate(legs):
        for j in range(i + 1, len(legs)):
            c, d = legs[j]
            if len({a, b, c, d}) < 4:
                continue
            if _segments_properly_cross(
                    group_pos[a], group_pos[b], group_pos[c], group_pos[d]):
                allowed.add((i, j))
    return allowed


def _validate_line_route(
    route: List[Tuple[int, int]],
    station_idx: Optional[List[int]] = None,
    *,
    closed: Optional[bool] = None,
    label: str = "line",
    require_station_order: bool = True,
    allowed_leg_crossings: Optional[Set[Tuple[int, int]]] = None,
) -> None:
    """验证一条线路的整线拓扑，而不是只验证每个站间段。

    站间局部路径都合法，并不代表拼起来没有折返、自交或重复走边。
    这里把这些条件提升为线路级硬不变量。环线允许首格在尾部重复一次，
    但不允许其它格点或边重复；对向别名线可以复用主线路由，因此调用方
    可以关闭站序单调检查，但仍会检查几何本身。
    """
    if not route:
        raise SchematicLayoutError(f"{label}: empty route")
    is_closed = (len(route) >= 2 and route[0] == route[-1]) \
        if closed is None else closed
    body = route[:-1] if is_closed and route[-1] == route[0] else route
    if len(set(body)) != len(body):
        raise SchematicLayoutError(
            f"{label}: line topology conflict: repeated grid cell")

    edge_list = [_edge_key(a, b) for a, b in zip(route, route[1:])]
    if len(set(edge_list)) != len(edge_list):
        raise SchematicLayoutError(
            f"{label}: line topology conflict: repeated grid edge")

    # 单位八方向边只需要检查非相邻边；环线首尾边也是相邻的。
    edge_count = len(route) - 1
    edge_owners = (_route_edge_owners(route, station_idx)
                   if station_idx is not None else [])
    for i, (a, b) in enumerate(zip(route, route[1:])):
        for j in range(i + 1, edge_count):
            if j == i + 1:
                continue
            if is_closed and i == 0 and j == edge_count - 1:
                continue
            c, d = route[j], route[j + 1]
            if _segments_intersect(a, b, c, d):
                if (allowed_leg_crossings and edge_owners
                        and _segments_properly_cross(a, b, c, d)
                        and _leg_pair(edge_owners[i], edge_owners[j])
                        in allowed_leg_crossings):
                    continue
                raise SchematicLayoutError(
                    f"{label}: line topology conflict: self intersection")

    if station_idx is None:
        return
    if any(i < 0 or i >= len(route) for i in station_idx):
        raise SchematicLayoutError(
            f"{label}: station index outside route")
    if require_station_order and any(b < a for a, b in
                                     zip(station_idx, station_idx[1:])):
        raise SchematicLayoutError(
            f"{label}: station order conflict")
    station_cells = [route[i] for i in station_idx]
    # 相邻手动站点可共用网格格点，此时形成零长度连接；不相邻的重复格点仍是拓扑错误。
    for i, cell in enumerate(station_cells):
        for j in range(i + 1, len(station_cells)):
            if cell == station_cells[j] and station_idx[i] != station_idx[j]:
                raise SchematicLayoutError(
                    f"{label}: different stations revisit a route grid cell")


def _path_conflicts_with_line(
    path: List[Tuple[int, int]],
    line_cells: Set[Tuple[int, int]],
    line_edges: Set[Tuple[int, int, int, int]],
    start: Tuple[int, int],
    goals: Set[Tuple[int, int]],
    *,
    current_line_edge_owners: Optional[
        Dict[Tuple[int, int, int, int], int]] = None,
    current_leg: Optional[int] = None,
    allowed_leg_crossings: Optional[Set[Tuple[int, int]]] = None,
) -> bool:
    """判断一段候选路径是否撞上当前线路既有几何。"""
    for cell in path[1:]:
        if cell in line_cells and cell not in goals:
            return True
    for a, b in zip(path, path[1:]):
        edge = _edge_key(a, b)
        if edge in line_edges:
            return True
        for old_edge in line_edges:
            oa = (old_edge[0], old_edge[1])
            ob = (old_edge[2], old_edge[3])
            if not _segments_intersect(a, b, oa, ob):
                continue
            old_leg = ((current_line_edge_owners or {}).get(old_edge))
            if (allowed_leg_crossings and current_leg is not None
                    and old_leg is not None
                    and _leg_pair(current_leg, old_leg)
                    in allowed_leg_crossings
                    and _segments_properly_cross(a, b, oa, ob)):
                continue
            shared = {a, b} & {oa, ob}
            if ((a == start and a in shared)
                    or (b in goals and b in shared)):
                continue
            return True
    return False


def _astar(start: Tuple[int, int], goals: Set[Tuple[int, int]],
           start_dir: Optional[int],
           station_cells: Set[Tuple[int, int]],
           edge_use: Dict[Tuple[int, int, int, int],
                          List[Tuple[int, int]]],
           bounds: Tuple[int, int, int, int],
           corridor: Optional[Dict[Tuple[int, int], int]] = None,
           *,
           current_line_cells: Optional[Set[Tuple[int, int]]] = None,
           current_line_edges: Optional[Set[Tuple[int, int, int, int]]] = None,
           current_line_edge_owners: Optional[
               Dict[Tuple[int, int, int, int], int]] = None,
           current_leg: Optional[int] = None,
           allowed_leg_crossings: Optional[Set[Tuple[int, int]]] = None,
           hard_block_cells: Optional[Set[Tuple[int, int]]] = None,
           forbidden_edges: Optional[Set[Tuple[int, int, int, int]]] = None,
           ) -> Tuple[List[Tuple[int, int]], int]:
    """八方向网格 A*：start → goals 中任一点。

    代价 = 步长 + 拐弯惩罚 + 方向感知占用 + 穿站惩罚
    + 出骨架走廊惩罚（corridor 给定时：格不在骨架 ±_CORRIDOR_WIDTH
    内 → 每步 +_CORRIDOR_PENALTY，软约束——中间站贴骨架走但
    理想格在走廊外时仍可达，不引入布局失败）。
    占用按方向分诊：同向（含反向平行）= 走廊共享折扣（手挽手并行），
    垂直/斜穿 = 真拥堵惩罚；同向超容量恢复惩罚（防坍缩一束）。
    返回 (路径格点列, 末段方向)；不可达 / 超限抛 SchematicLayoutError。

    current_line_cells/current_line_edges 是当前 Transport 已经确认的
    几何，只允许从起点进入目标格，不允许本线折返复用。hard_block_cells
    用于把其它站点和当前线路尚未到达的未来站点变成硬障碍；目标格例外。
    """
    bx0, by0, bx1, by1 = bounds
    h0 = min(_octile(*start, *g) for g in goals)
    # 起点方向键统一（start_dir 与 best/came 键必须一致：若堆项用 start_dir
    # 而 best 记 -1，起点状态可被后续松弛写入 came → 回溯链成环死循环）
    sd = start_dir if start_dir is not None else -1
    # 堆项: (f, g, tie, x, y, dir)；tie 递增保证 heapq 稳定比较
    open_heap = [(h0, 0.0, 0, start[0], start[1], sd)]
    counter = 1
    # best[(x,y,dir)] = 最小 g
    best = {(start[0], start[1], sd): 0.0}
    came: Dict[Tuple[int, int, int], Tuple[Tuple[int, int, int], Tuple[int, int]]] = {}
    expanded = 0

    while open_heap:
        f, g, _, x, y, d = heapq.heappop(open_heap)
        if g > best.get((x, y, d), float("inf")):
            continue  # 过期堆项
        if (x, y) in goals:
            # 回溯路径（步数超限 = came 链成环，直接失败不兜底）
            path: List[Tuple[int, int]] = [(x, y)]
            cur = (x, y, d)
            for _ in range(_ASTAR_LIMIT):
                if cur not in came:
                    break
                cur, pt = came[cur]
                path.append(pt)
            else:
                raise SchematicLayoutError("A* backtrack chain cycle detected")
            path.reverse()
            return path, d
        expanded += 1
        if expanded > _ASTAR_LIMIT:
            raise SchematicLayoutError(
                f"A* search exceeded limit ({_ASTAR_LIMIT} cells)")
        for nd in range(8):
            dx, dy = _DIRS[nd]
            nx, ny = x + dx, y + dy
            if not (bx0 <= nx <= bx1 and by0 <= ny <= by1):
                continue
            nd_key = (nx, ny, nd)
            next_cell = (nx, ny)
            # 当前线路不重复经过自己的格点/边。目标站可以是已布局的
            # 换乘锚点，允许作为终点进入，但不能复用同一条边。
            if (current_line_cells is not None
                    and next_cell in current_line_cells
                    and next_cell not in goals):
                continue
            step = _SQRT2 if nd in _DIAG else 1.0
            cost = step
            if d >= 0:
                cost += _bend(d, nd)
            ek = _edge_key((x, y), (nx, ny))
            if current_line_edges is not None and ek in current_line_edges:
                continue
            if forbidden_edges is not None and ek in forbidden_edges:
                continue
            # 已有本线边的交叉也禁止。允许从当前段起点离开、或进入
            # 当前目标站时与旧线段共享端点；其余相交均是自交。
            if current_line_edges:
                crosses_own_edge = False
                for old_edge in current_line_edges:
                    oa = (old_edge[0], old_edge[1])
                    ob = (old_edge[2], old_edge[3])
                    if not _segments_intersect((x, y), next_cell, oa, ob):
                        continue
                    old_leg = ((current_line_edge_owners or {}).get(old_edge))
                    if (allowed_leg_crossings and current_leg is not None
                            and old_leg is not None
                            and _leg_pair(current_leg, old_leg)
                            in allowed_leg_crossings
                            and _segments_properly_cross(
                                (x, y), next_cell, oa, ob)):
                        continue
                    shared = { (x, y), next_cell } & {oa, ob}
                    if ((x, y) == start and (x, y) in shared) \
                            or (next_cell in goals and next_cell in shared):
                        continue
                    crosses_own_edge = True
                    break
                if crosses_own_edge:
                    continue
            if (hard_block_cells is not None
                    and next_cell in hard_block_cells
                    and next_cell not in goals):
                continue
            occ = edge_use.get(ek)
            bonus = 0.0
            if occ:
                same = sum(1 for _tid, od in occ
                           if od == nd or od == (nd + 4) % 8)
                if same:
                    # 走廊共享：同向并行折扣；超容量恢复惩罚
                    if same < _CORRIDOR_CAP:
                        bonus += _CORRIDOR_BONUS * same
                    else:
                        cost += _CONGESTION * (same - _CORRIDOR_CAP + 1)
                cross = len(occ) - same
                if cross:
                    cost += _CONGESTION * cross
            # 邻边平行吸引：左右邻格同向占用（相隔 1 格的平行线）
            # → 辅助折扣，把近距平行线吸成相邻平行束
            for side in ((nd + 2) % 8, (nd - 2) % 8):
                px_, py_ = _DIRS[side]
                pocc = edge_use.get(_edge_key((x + px_, y + py_),
                                              (nx + px_, ny + py_)))
                if pocc:
                    psame = sum(1 for _t, od in pocc
                                if od == nd or od == (nd + 4) % 8)
                    if 0 < psame < _CORRIDOR_CAP:
                        bonus += _CORRIDOR_BONUS * _PARALLEL_EDGE_FACTOR * psame
            # 总折扣不超过步长一半（保证代价恒正、启发可采纳）
            cost -= min(bonus, step * 0.5)
            if (nx, ny) in station_cells and (nx, ny) not in goals:
                cost += _STATION_BLOCK
            if corridor is not None and (nx, ny) not in corridor:
                cost += _CORRIDOR_PENALTY
            ng = g + cost
            if ng < best.get(nd_key, float("inf")) - 1e-9:
                best[nd_key] = ng
                came[nd_key] = ((x, y, d), (x, y))
                h = min(_octile(nx, ny, *gg) for gg in goals)
                heapq.heappush(open_heap, (ng + h, ng, counter, nx, ny, nd))
                counter += 1
    raise SchematicLayoutError("A* failed: goal unreachable on grid")


def _candidates(cell: Tuple[int, int], blocked: Set[Tuple[int, int]],
                radius: int = 1) -> List[Tuple[int, int]]:
    """cell 邻域内未被占用的候选格（按距离排序）。"""
    out = []
    for dx in range(-radius, radius + 1):
        for dy in range(-radius, radius + 1):
            c = (cell[0] + dx, cell[1] + dy)
            if c not in blocked:
                out.append(c)
    out.sort(key=lambda c: (abs(c[0] - cell[0]) + abs(c[1] - cell[1]),
                            c[0], c[1]))
    return out


def _snap(pos: Tuple[float, float], spacing: float) -> Tuple[int, int]:
    return (int(round(pos[0] / spacing)), int(round(pos[1] / spacing)))


def _adaptive_axis_map(
    axis_min: int,
    axis_max: int,
    samples: List[Tuple[int, float]],
    compressed_spans: List[Tuple[int, int, float]],
    start_px: float,
    end_px: float,
    strength: float,
    max_compress: float = 0.60,
    max_expand: float = 2.0,
) -> Dict[int, float]:
    """构造一条保持端点不变的非均匀网格轴。

    每个整数网格区间先获得一个权重：站点压力会提高局部权重，长区段
    会降低其权重。最后把累计权重归一化到原来的像素范围，因此密集区
    获得更多像素、长而稀疏的区段自动让出空间。线路路由本身不变，
    共享格点也始终映射到同一个像素位置。
    """
    if axis_max <= axis_min:
        return {axis_min: start_px}

    strength = max(0.0, min(float(strength), 1.0))
    max_compress = max(0.25, min(float(max_compress), 1.0))
    max_expand = max(1.0, min(float(max_expand), 4.0))
    span = axis_max - axis_min
    weights = [1.0] * span
    for i in range(span):
        mid = axis_min + i + 0.5
        pressure = 0.0
        for coord, value in samples:
            distance = abs(mid - coord)
            if distance <= 3.5:
                # 3.5 格内的密度才影响本地轴段，避免整个城市被一个
                # 中心枢纽均匀拉伸。
                pressure += max(0.0, float(value)) * math.exp(
                    -(distance * distance) / 3.2)
        pressure = min(pressure, 2.5)
        # max_expand 直接表示相对基线的上限；不再额外乘折扣，
        # 这样 max_expand=2.0 才真正允许密集区达到约 2 倍基线。
        weight = 1.0 + strength * (max_expand - 1.0) * pressure

        longness = 0.0
        for a, b, span_longness in compressed_spans:
            lo, hi = min(a, b), max(a, b)
            if lo <= mid <= hi:
                longness = max(longness, max(0.0, min(span_longness, 1.0)))
        if longness > 0.0:
            weight *= 1.0 - strength * (1.0 - max_compress) \
                * 0.55 * longness
        weights[i] = max(weight, 0.20)

    total = sum(weights)
    if total <= 1e-9:
        return {
            axis_min + i: start_px + (end_px - start_px) * i / span
            for i in range(span + 1)
        }

    result = {axis_min: start_px}
    acc = 0.0
    for i, weight in enumerate(weights, 1):
        acc += weight
        result[axis_min + i] = start_px + (end_px - start_px) * acc / total
    result[axis_max] = end_px
    return result


def _adaptive_routes_keep_octilinear(
    routes: Dict[int, List[Tuple[int, int]]],
    adaptive_x: Dict[int, float],
    adaptive_y: Dict[int, float],
    linear_scale: float,
    tolerance_deg: float = _ADAPTIVE_ANGLE_TOLERANCE_DEG,
) -> bool:
    """检查轴变形后的原网格斜边是否仍接近 45°。

    单调坐标轴不会破坏连通性，但可能把网格斜边拉成任意角度。动态比例尺
    只作为视觉增强，因此几何质量不满足阈值时应由调用层降低强度或回退。
    """
    def value(mapping: Dict[int, float], coord: int) -> float:
        if coord in mapping:
            return mapping[coord]
        keys = sorted(mapping)
        if not keys:
            return coord * linear_scale
        if coord <= keys[0]:
            return mapping[keys[0]] + (coord - keys[0]) * linear_scale
        if coord >= keys[-1]:
            return mapping[keys[-1]] + (coord - keys[-1]) * linear_scale
        lo = max(k for k in keys if k < coord)
        hi = min(k for k in keys if k > coord)
        return mapping[lo] + (mapping[hi] - mapping[lo]) \
            * (coord - lo) / (hi - lo)

    for route in routes.values():
        for (x0, y0), (x1, y1) in zip(route, route[1:]):
            if x0 == x1 or y0 == y1:
                continue
            px0, px1 = value(adaptive_x, x0), value(adaptive_x, x1)
            py0, py1 = value(adaptive_y, y0), value(adaptive_y, y1)
            dx = abs(px1 - px0)
            dy = abs(py1 - py0)
            if min(dx, dy) <= 1e-6:
                return False
            angle = math.degrees(math.atan2(dy, dx))
            if abs(angle - 45.0) > tolerance_deg:
                return False
    return True


def _l_cells(a: Tuple[int, int], b: Tuple[int, int],
             diag_first: bool) -> List[Tuple[int, int]]:
    """a→b 的 ≤1 拐点八方向 L 格点列（对齐时为直线）。

    diag_first=True 先走斜线再走直线；False 相反（对齐时无差别）。
    """
    x, y = a
    cells = [(x, y)]
    dx, dy = b[0] - x, b[1] - y
    adx, ady = abs(dx), abs(dy)
    sx = 1 if dx > 0 else -1
    sy = 1 if dy > 0 else -1
    d = min(adx, ady)
    rx, ry = adx - d, ady - d          # 恰一个非零（对齐时均为 0）
    if not diag_first:
        for _ in range(rx):
            x += sx
            cells.append((x, y))
        for _ in range(ry):
            y += sy
            cells.append((x, y))
    for _ in range(d):
        x += sx
        y += sy
        cells.append((x, y))
    if diag_first:
        for _ in range(rx):
            x += sx
            cells.append((x, y))
        for _ in range(ry):
            y += sy
            cells.append((x, y))
    return cells


def _z_cells(a: Tuple[int, int], b: Tuple[int, int], frac: float
             ) -> List[Tuple[int, int]]:
    """a→b 的 Z 型（≤2 拐点）八方向格点列：直 rem1 → 斜 d → 直 rem2。

    L 型被站阻挡时的次级候选（提前/延后转向绕开障碍）；
    rem=0 或 d=0 退化为 L/直线。
    """
    dx, dy = b[0] - a[0], b[1] - a[1]
    adx, ady = abs(dx), abs(dy)
    d = min(adx, ady)
    rem = max(adx, ady) - d
    if d == 0 or rem == 0:
        return _l_cells(a, b, True)
    sx = 1 if dx > 0 else -1
    sy = 1 if dy > 0 else -1
    horiz = adx > ady
    k = max(0, min(rem, int(round(rem * frac))))
    x, y = a
    cells = [(x, y)]

    def _straight(n: int) -> None:
        nonlocal x, y
        for _ in range(n):
            if horiz:
                x += sx
            else:
                y += sy
            cells.append((x, y))

    _straight(k)
    for _ in range(d):
        x += sx
        y += sy
        cells.append((x, y))
    _straight(rem - k)
    return cells


def _simple_seg(a: Tuple[int, int], b: Tuple[int, int],
                orig: List[Tuple[int, int]],
                blocked: Set[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """站间段简化：L（≤1 拐点）→ Z（≤2 拐点）→ 原路径。

    1. 取不穿站的 L 候选中与原路径重叠最多者；
    2. L 全被挡 → Z 型候选（转向点前移/后移绕开障碍，frac 1/3、1/2、2/3）；
    3. Z 也被挡才保留 A* 原路径（仍八方向；极少触发）。
    """
    if a == b:
        return [a]
    adx, ady = abs(b[0] - a[0]), abs(b[1] - a[1])
    cands: List[List[Tuple[int, int]]] = []
    if adx == ady or adx == 0 or ady == 0:
        cands.append(_l_cells(a, b, True))
    else:
        cands.extend([_l_cells(a, b, True), _l_cells(a, b, False)])
    orig_set = set(orig)
    best: Optional[List[Tuple[int, int]]] = None
    best_score = -1
    for c in cands:
        if any(m in blocked for m in c[1:-1]):
            continue
        score = len(orig_set.intersection(c))
        if score > best_score:
            best, best_score = c, score
    if best is not None:
        return best
    # Z 型候选（L 全被挡：提前/延后转向绕开障碍站）
    for frac in (0.5, 0.34, 0.66):
        c = _z_cells(a, b, frac)
        if any(m in blocked for m in c[1:-1]):
            continue
        score = len(orig_set.intersection(c))
        if score > best_score:
            best, best_score = c, score
    return best if best is not None else list(orig)


# 正交方向索引（E/NE/N/W/SW/S/SE/W 中的 E/N/W/S）
_ORTHO = {0, 2, 4, 6}


def _straighten(route: List[Tuple[int, int]],
                keep_cells: Set[Tuple[int, int]]
                ) -> List[Tuple[int, int]]:
    """staircase 拉直（P1+P5b）：通用三段偏移直线删中点 / L 替换。

    P1→P2→P3→P4 中 d1==d3 且 d2!=d1 时（平行四边形/锯齿本质）：
    - 90° 差（正交-正交 或 斜-斜）：删非站点中点（剩余三点成 L，
      对角步替两正交步 / 正交步替两斜步，仍八方向）
    - 45°/135° 差（正交-斜）：删中点会越界（非八方向步），改为
      L 替换——用 _l_cells 生成 p1→p4 的八方向 L 折线替换中间两点，
      保持八方向性、消除锯齿
    反复扫描至不动点；站点格（keep_cells）永不删除/替换。
    """
    r = list(route)
    # 两种等价 L 折线可能被相邻窗口交替改写，导致 changed 永远为 True。
    # 后处理只是视觉优化；一旦路径状态重复，保留当前合法路径即可，不能
    # 让预览线程永久占用 CPU。状态数同时构成与输入规模相关的自然上限。
    seen_states: Set[Tuple[Tuple[int, int], ...]] = set()
    changed = True
    while changed:
        state = tuple(r)
        if state in seen_states:
            break
        seen_states.add(state)
        changed = False
        i = 0
        while i + 3 < len(r):
            p1, p2, p3 = r[i], r[i + 1], r[i + 2]
            p4 = r[i + 3]
            d1 = _dir_of_safe(p1, p2)
            d2 = _dir_of_safe(p2, p3)
            d3 = _dir_of_safe(p3, p4)
            # 非八方向步（走廊对齐移站后可能出现）：不参与拉直
            if d1 is None or d2 is None or d3 is None:
                i += 1
                continue
            if d1 == d3 and d2 != d1:
                diff = (d1 - d2) % 8
                if diff > 4:
                    diff = 8 - diff
                if diff == 2:
                    # 90° 差：删中点（剩余三点成 L，对角/正交步替两步）
                    if p2 not in keep_cells:
                        del r[i + 1]
                        changed = True
                        continue
                    if p3 not in keep_cells:
                        del r[i + 2]
                        changed = True
                        continue
                elif diff == 1 or diff == 3:
                    # 45°/135° 差：删中点越界，L 替换中间两点
                    if p2 not in keep_cells and p3 not in keep_cells:
                        replaced = False
                        for diag_first in (True, False):
                            lcells = _l_cells(p1, p4, diag_first)
                            # 中间格不撞站点格才替换
                            if all(c not in keep_cells
                                   for c in lcells[1:-1]):
                                replacement = lcells[1:-1]
                                if r[i + 1:i + 3] == replacement:
                                    continue
                                r[i + 1:i + 3] = replacement
                                changed = True
                                replaced = True
                                break
                        if replaced:
                            continue
            i += 1
    return r


def _rebuild_s(route: List[Tuple[int, int]],
               cells_seq: List[Tuple[int, int]]) -> List[int]:
    """按站点格顺序在路由中贪心重建站点索引（拉直/对齐后调用）。"""
    s: List[int] = []
    pos = 0
    previous_cell: Optional[Tuple[int, int]] = None
    for c in cells_seq:
        if s and c == previous_cell:
            s.append(s[-1])
            continue
        j = pos
        while j < len(route) and route[j] != c:
            j += 1
        if j >= len(route):
            j = len(route) - 1   # 防御（正常不会发生：keep 保护站点格）
        s.append(j)
        pos = j + 1
        previous_cell = c
    return s


def _repair_octolinear(route: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """修复路由为严格八方向单位步序列。

    走廊对齐移站后可能出现少量越界步（如 (2,1)）或零步：零步剔除，
    越界步用 ≤1 拐点 L 折线格列补回，恢复八方向性（边占用登记/
    并行偏移均基于八方向边）。原有格点全部保留，站点索引可重建。
    """
    if len(route) < 2:
        return list(route)
    out: List[Tuple[int, int]] = [route[0]]
    for b in route[1:]:
        a = out[-1]
        if b == a:
            continue
        if (b[0] - a[0], b[1] - a[1]) in _DIRS:
            out.append(b)
            continue
        out.extend(_l_cells(a, b, True)[1:])
    return out


def _build_skeleton(waypoints: List[Tuple[int, int]],
                    placed_cells: Set[Tuple[int, int]],
                    edge_use: Dict[Tuple[int, int, int, int],
                                   List[Tuple[int, int]]],
                    ) -> List[Tuple[int, int]]:
    """宏观骨架：按完整有序站点锚点串联 A*。

    骨架是参考形态（不记录边占用）；任段不可达 → 布局失败（不兜底）。
    每个站点重置入射方向，使拐弯惩罚只约束站间腿内部。
    """
    skel: List[Tuple[int, int]] = []
    for a, b in zip(waypoints, waypoints[1:]):
        if a == b:
            continue
        bounds = (min(a[0], b[0]) - 8, min(a[1], b[1]) - 8,
                  max(a[0], b[0]) + 8, max(a[1], b[1]) + 8)
        path, _end_dir = _astar(a, {b}, None, placed_cells,
                                edge_use, bounds)
        skel.extend(path if not skel else path[1:])
    return skel


def _skeleton_corridor(skel: List[Tuple[int, int]]
                       ) -> Dict[Tuple[int, int], int]:
    """骨架多源 BFS：格 → 离骨架距离（≤ _CORRIDOR_WIDTH）。

    中间站路由的软约束场——贴骨架 0 惩罚，出走廊每步 +_CORRIDOR_PENALTY。
    """
    dist: Dict[Tuple[int, int], int] = {c: 0 for c in skel}
    dq = deque(dist.keys())
    while dq:
        c = dq.popleft()
        d = dist[c]
        if d >= _CORRIDOR_WIDTH:
            continue
        for dx, dy in _DIRS:
            n = (c[0] + dx, c[1] + dy)
            if n not in dist:
                dist[n] = d + 1
                dq.append(n)
    return dist


def _dense_line_fallback(
    tr: Transport,
    core: List[int],
    grid: Dict[int, Tuple[int, int]],
    placed: Dict[int, Tuple[int, int]],
    placed_cells: Set[Tuple[int, int]],
    edge_use: Dict[Tuple[int, int, int, int], List[Tuple[int, int]]],
) -> Optional[Tuple[List[Tuple[int, int]], List[int]]]:
    """为无锚点的极端密集线路预留一条可行的拓扑容量。"""
    if len(core) < 8 or any(g in placed for g in core[1:]):
        return None
    source = [grid[g] for g in core]
    sx = [c[0] for c in source]
    sy = [c[1] for c in source]
    compact_area = (max(sx) - min(sx) + 1) * (max(sy) - min(sy) + 1)
    if compact_area > len(core) * 1.5:
        return None

    taken = set(placed_cells) | set(grid.values())
    row = max(sy) + 2
    start_x = min(sx)
    while any((start_x + i, row) in taken for i in range(len(core))):
        row += 1
    cells = [(start_x + i, row) for i in range(len(core))]
    label = f"Transport {tr.id} {tr.name or ''}".strip()
    _validate_line_route(cells, list(range(len(cells))), label=label)
    for g, cell in zip(core, cells):
        grid[g] = cell
        placed[g] = cell
        placed_cells.add(cell)
    _record_edges(cells, id(tr), edge_use)
    return cells, list(range(len(cells)))


def _route_line_with_constraints(
    tr: Transport,
    core: List[int],
    is_loop: bool,
    grid: Dict[int, Tuple[int, int]],
    placed: Dict[int, Tuple[int, int]],
    placed_cells: Set[Tuple[int, int]],
    edge_use: Dict[Tuple[int, int, int, int], List[Tuple[int, int]]],
    corridor: Optional[Dict[Tuple[int, int], int]],
    skel_box: Optional[Tuple[int, int, int, int]],
    allowed_leg_crossings: Optional[Set[Tuple[int, int]]] = None,
) -> Tuple[List[Tuple[int, int]], List[int]]:
    """按整条线路回滚重试的普通线路路由器。

    每段仍使用 A*，但一旦后面的站点证明当前局部选择会把线路
    困在自己的走廊里，就禁止最近一段的旧边并从线路起点重算。
    这样不会用“允许自交”的低质量路径作为兜底，同时保留现有
    逐段寻路对普通线路和共享锚点的兼容性。
    """
    if len(core) < 2:
        return [grid[core[0]]], [0]

    dense_fallback = _dense_line_fallback(tr, core, grid, placed, placed_cells, edge_use)
    if dense_fallback is not None:
        return dense_fallback

    g0 = core[0]
    if g0 not in placed:
        placed[g0] = grid[g0]
        placed_cells.add(grid[g0])

    banned_edges: Dict[int, Set[Tuple[int, int, int, int]]] = {}
    last_error: Optional[Exception] = None

    for _attempt in range(_LINE_REPLAN_LIMIT):
        work_placed = dict(placed)
        path_full: List[Tuple[int, int]] = [placed[g0]]
        s_idx: List[int] = [0]
        line_cells: Set[Tuple[int, int]] = {placed[g0]}
        line_edges: Set[Tuple[int, int, int, int]] = set()
        line_edge_owners: Dict[Tuple[int, int, int, int], int] = {}
        segment_paths: List[List[Tuple[int, int]]] = []
        prev_dir: Optional[int] = None
        fail_i: Optional[int] = None

        for i in range(1, len(core)):
            u, v = core[i - 1], core[i]
            su = work_placed[u]
            goals = {work_placed[v]} if v in work_placed else {grid[v]}
            future_cells = {
                work_placed.get(g, grid[g]) for g in core[i:]
            }
            hard_block = set(placed_cells) | future_cells
            gxs = [g[0] for g in goals]
            gys = [g[1] for g in goals]
            if skel_box is not None:
                bounds = (min(skel_box[0], min(gxs), su[0]) - 8,
                          min(skel_box[1], min(gys), su[1]) - 8,
                          max(skel_box[2], max(gxs), su[0]) + 8,
                          max(skel_box[3], max(gys), su[1]) + 8)
            else:
                bounds = (min(su[0], min(gxs)) - 8,
                          min(su[1], min(gys)) - 8,
                          max(su[0], max(gxs)) + 8,
                          max(su[1], max(gys)) + 8)
            try:
                path, prev_dir = _astar(
                    su, goals, prev_dir, placed_cells, edge_use, bounds,
                    corridor,
                    current_line_cells=line_cells,
                    current_line_edges=line_edges,
                    current_line_edge_owners=line_edge_owners,
                    current_leg=i - 1,
                    allowed_leg_crossings=allowed_leg_crossings,
                    hard_block_cells=hard_block,
                    forbidden_edges=banned_edges.get(i - 1),
                )
            except SchematicLayoutError as exc:
                last_error = exc
                fail_i = i
                break
            if _path_conflicts_with_line(
                    path, line_cells, line_edges, su, goals,
                    current_line_edge_owners=line_edge_owners,
                    current_leg=i - 1,
                    allowed_leg_crossings=allowed_leg_crossings):
                last_error = SchematicLayoutError(
                    "line topology conflict: candidate self intersection")
                fail_i = i
                break
            segment_paths.append(path)
            work_placed[v] = path[-1]
            path_full.extend(path[1:])
            s_idx.append(len(path_full) - 1)
            line_cells.update(path)
            for a, b in zip(path, path[1:]):
                edge = _edge_key(a, b)
                line_edges.add(edge)
                line_edge_owners[edge] = i - 1

        # 环线有锚点时，闭合段也必须遵守整线约束；无锚点的八边形
        # 已经在调用方的专用分支中完成。
        if fail_i is None and is_loop:
            sv, goal = work_placed[core[-1]], work_placed[core[0]]
            bounds = (min(sv[0], goal[0]) - 8,
                      min(sv[1], goal[1]) - 8,
                      max(sv[0], goal[0]) + 8,
                      max(sv[1], goal[1]) + 8)
            try:
                path, prev_dir = _astar(
                    sv, {goal}, prev_dir, placed_cells, edge_use, bounds,
                    current_line_cells=line_cells,
                    current_line_edges=line_edges,
                    current_line_edge_owners=line_edge_owners,
                    current_leg=len(core) - 1,
                    allowed_leg_crossings=allowed_leg_crossings,
                    hard_block_cells=set(placed_cells),
                    forbidden_edges=banned_edges.get(len(core) - 1),
                )
            except SchematicLayoutError as exc:
                last_error = exc
                fail_i = len(core)
            else:
                if _path_conflicts_with_line(
                        path, line_cells, line_edges, sv, {goal},
                        current_line_edge_owners=line_edge_owners,
                        current_leg=len(core) - 1,
                        allowed_leg_crossings=allowed_leg_crossings):
                    last_error = SchematicLayoutError(
                        "line topology conflict: candidate self intersection")
                    fail_i = len(core)
                else:
                    path_full.extend(path[1:])
                    line_cells.update(path)
                    for a, b in zip(path, path[1:]):
                        edge = _edge_key(a, b)
                        line_edges.add(edge)
                        line_edge_owners[edge] = len(core) - 1

        if fail_i is None:
            label = f"Transport {tr.id} {tr.name or ''}".strip()
            _validate_line_route(path_full, s_idx, closed=is_loop,
                                 label=label,
                                 allowed_leg_crossings=allowed_leg_crossings)
            for i, g in enumerate(core):
                if g not in placed:
                    placed[g] = grid[g]
                    placed_cells.add(grid[g])
            for path in segment_paths:
                _record_edges(path, id(tr), edge_use)
            if is_loop:
                # segment_paths 不含闭合段，最后一段由 path_full 中的
                # 尾部邻接序列恢复登记；闭合线路的绘制边只需统一占用。
                _record_edges(path_full[s_idx[-1]:], id(tr), edge_use)
            return path_full, s_idx

        # 失败后优先回滚离失败点最近的已完成段。禁止该段旧边，
        # 下一轮从线路起点重算，避免残留半条路线污染新候选。
        if fail_i is None:
            break
        rollback_candidates = list(range(
            min(fail_i - 1, len(segment_paths) - 1), -1, -1))
        if not rollback_candidates:
            break
        rollback_i = rollback_candidates[0]
        old_path = segment_paths[rollback_i]
        banned_edges.setdefault(rollback_i, set()).update(
            _edge_key(a, b) for a, b in zip(old_path, old_path[1:]))

    label = f"Transport {tr.id} {tr.name or ''}".strip()
    detail = str(last_error) if last_error is not None else "no candidate"
    raise SchematicLayoutError(
        f"{label}: line topology conflict: no valid route ({detail})")


def _octagon_boundary(cells: List[Tuple[int, int]]
                      ) -> List[Tuple[int, int]]:
    """格点集 → 八边形边界 CCW 格点序列（首尾不重复）。

    站点包围盒四角切 45° 角（切角 = 短边/4，至少 1 格）。
    真实地铁环的标准形态（矩形/八边形），杜绝自由搜索的不规则环。
    """
    xs = [c[0] for c in cells]
    ys = [c[1] for c in cells]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    w, h = x1 - x0, y1 - y0
    if w <= 0 and h <= 0:
        return [(x0, y0)]
    c = max(1, min(w, h) // 4)
    c = min(c, w, h)          # 退化包围盒不越界
    verts = [
        (x0, y0 + c), (x0 + c, y0),        # 左下斜边
        (x1 - c, y0), (x1, y0 + c),        # 底边 + 右下斜边
        (x1, y1 - c), (x1 - c, y1),        # 右边 + 右上斜边
        (x0 + c, y1), (x0, y1 - c),        # 顶边 + 左上斜边
    ]
    # 相邻顶点去重（小包围盒下 c 可能等于半边长，产生重复顶点）
    dedup: List[Tuple[int, int]] = []
    for v in verts:
        if not dedup or v != dedup[-1]:
            dedup.append(v)
    if len(dedup) > 1 and dedup[0] == dedup[-1]:
        dedup.pop()
    # 展开为格点序列（CCW）
    out: List[Tuple[int, int]] = []
    for i in range(len(dedup)):
        a, b = dedup[i], dedup[(i + 1) % len(dedup)]
        out.append(a)
        dx, dy = b[0] - a[0], b[1] - a[1]
        n = max(abs(dx), abs(dy))
        if n > 1:
            sx = (dx > 0) - (dx < 0)
            sy = (dy > 0) - (dy < 0)
            for k in range(1, n):
                out.append((a[0] + sx * k, a[1] + sy * k))
    return out


def _octagon_boundary_with_capacity(
        cells: List[Tuple[int, int]], minimum: int,
        boundary: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """仅在边界格点不足时外扩八边形，满足最小站点容量。"""
    if len(boundary) >= minimum:
        return boundary
    xs = [c[0] for c in cells]
    ys = [c[1] for c in cells]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    # 每次外扩一格；正常环线通常第一次即可满足，最多留一倍余量
    # 防止异常数据导致无界循环。
    limit = max(minimum * 2, 16)
    for pad in range(1, limit + 1):
        expanded = list(cells) + [
            (x0 - pad, y0 - pad), (x1 + pad, y0 - pad),
            (x1 + pad, y1 + pad), (x0 - pad, y1 + pad),
        ]
        candidate = _octagon_boundary(expanded)
        if len(candidate) >= minimum:
            return candidate
    raise SchematicLayoutError(
        "loop boundary capacity insufficient: "
        f"perimeter {len(boundary)} < stations {minimum}")


def _allocate_loop_gaps(target_gaps: List[float], perimeter: int
                        ) -> List[int]:
    """动态规划分配整数环线间隔：每段至少 1 格且总和等于周长。"""
    n = len(target_gaps)
    inf = float("inf")
    dp = [[inf] * (perimeter + 1) for _ in range(n + 1)]
    prev_gap: List[List[Optional[int]]] = [
        [None] * (perimeter + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0
    for i in range(n):
        for used in range(perimeter + 1):
            base = dp[i][used]
            if base == inf:
                continue
            max_gap = perimeter - used - (n - i - 1)
            for gap in range(1, max_gap + 1):
                total = used + gap
                cost = base + (gap - target_gaps[i]) ** 2
                if cost < dp[i + 1][total]:
                    dp[i + 1][total] = cost
                    prev_gap[i + 1][total] = gap
    if dp[n][perimeter] == inf:
        raise SchematicLayoutError(
            "loop station spacing conflict: integer gaps cannot close loop")
    gaps = [0] * n
    used = perimeter
    for i in range(n, 0, -1):
        gap = prev_gap[i][used]
        if gap is None:
            raise SchematicLayoutError(
                "loop station spacing conflict: broken gap allocation")
        gaps[i - 1] = gap
        used -= gap
    return gaps


def _project_loop(core: List[int], grid: Dict[int, Tuple[int, int]],
                  placed: Dict[int, Tuple[int, int]],
                  placed_cells: Set[Tuple[int, int]],
                  boundary: List[Tuple[int, int]]
                  ) -> Tuple[List[Tuple[int, int]], Dict[int, Tuple[int, int]],
                             List[int]]:
    """按环线站序把站点投影到八边形边界（一圈内、严格闭合）。

    旧实现先取每站最近边界点，再把局部逆序强制单调；非凸环线
    的最近点序列可能发生一次小幅回退，单调化会错误增加整圈周长。
    这里改为：按原始网格站间弧长比例分配边界间隔，枚举 CW/CCW
    与起始偏移，选择与原始站点位置误差最小的方案。每段至少 1
    格，所有站点天然只覆盖一圈，最后一段明确闭合到首站。

    ``placed`` 保留在签名中用于兼容既有调用；当前八边形分支只在
    无本线路锚点时调用，外部换乘锚点仍由后续 A* 路由处理。
    返回 (环线路由格点列, 新增 placed, 站点路由下标)。
    """
    L = len(boundary)
    n = len(core)
    if L < n:
        raise SchematicLayoutError(
            "loop boundary capacity insufficient: "
            f"perimeter {L} < stations {n}")

    # 原始站间距离 → 每个边界段的目标格数；再用 DP 分配整数格，
    # 保证每段至少 1 格且总和严格等于边界周长。
    distances = []
    for i, g in enumerate(core):
        a = grid[g]
        b = grid[core[(i + 1) % n]]
        distances.append(max(math.hypot(b[0] - a[0], b[1] - a[1]), 1.0))
    total_distance = sum(distances)
    target_gaps = [d / total_distance * L for d in distances]
    gaps = _allocate_loop_gaps(target_gaps, L)

    blocked = set(placed_cells)
    # 同时尝试两个方向和每个边界起点；间隔序列保持不变，只有
    # 投影方向/旋转改变。误差按原始网格站点到边界站点的平方和计算。
    def score_boundary(
            candidate_boundary: List[Tuple[int, int]],
            shift_penalty: float = 0.0,
    ):
        scored = []
        for direction in (1, -1):
            for start in range(L):
                ts = []
                t = start
                score = shift_penalty
                conflicts = 0
                for i, g in enumerate(core):
                    cell = candidate_boundary[t % L]
                    target = placed.get(g, grid[g])
                    score += (cell[0] - target[0]) ** 2 \
                        + (cell[1] - target[1]) ** 2
                    if g not in placed and cell in blocked:
                        conflicts += 1
                    ts.append(t)
                    t += direction * gaps[i]
                scored.append((conflicts, score, 0 if direction == 1 else 1,
                               start, direction, ts, candidate_boundary))
        return scored

    candidates = score_boundary(boundary)
    # 多条独立环线可能落在相近区域。若当前八边形没有任何无冲突
    # 的站点投影，就按距离搜索最近的空闲平移边界；不改变环线的
    # 站序、间隔分配或八方向形态，只为前序线路让出完整边界。
    if not candidates or min(c[0] for c in candidates) > 0:
        max_shift = max(8, min(24, n + 4))
        candidates = []
        for radius in range(1, max_shift + 1):
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    if max(abs(dx), abs(dy)) != radius:
                        continue
                    shifted = [(x + dx, y + dy) for x, y in boundary]
                    if any(cell in blocked for cell in shifted):
                        continue
                    candidates.extend(score_boundary(
                        shifted, shift_penalty=float(dx * dx + dy * dy)))
            if candidates:
                break
    if not candidates:
        raise SchematicLayoutError(
            "loop station projection conflicts with existing station")
    _conflicts, _score, _dir_order, _start, direction, result_ts, boundary = \
        min(candidates)

    # 生成路由：相邻投影间沿边界 CCW 取格点
    route: List[Tuple[int, int]] = []
    new_placed: Dict[int, Tuple[int, int]] = {}
    s_idx: List[int] = []
    for i in range(len(core)):
        t = result_ts[i]
        cell = boundary[t % L]
        if core[i] not in placed:
            if cell in placed_cells:
                raise SchematicLayoutError(
                    "loop station projection conflicts with existing station")
            new_placed[core[i]] = cell
        route.append(cell)
        s_idx.append(len(route) - 1)
        steps = gaps[i]
        if steps < 1:
            raise SchematicLayoutError(
                "loop station spacing conflict: adjacent stations share boundary")
        # 末段多填 1 格闭合回首站（route 首尾同格，环闭合）
        for k in range(1, steps + (1 if i == len(core) - 1 else 0)):
            route.append(boundary[(t + direction * k) % L])
    return route, new_placed, s_idx


# ---------------------------------------------------------------------------
# 主布局
# ---------------------------------------------------------------------------

def _alias_ring(proute: List[Tuple[int, int]], core: List[int],
                placed: Dict[int, Tuple[int, int]]
                ) -> Optional[List[Tuple[int, int]]]:
    """环线对向线路由：主线闭环旋转到本线起点（必要时反转）。

    旋转差环直接按站序 .index 重建索引会乱序（段倒挂丢边）；
    先把环旋转/反转到与本线站序一致，索引自然单调。
    返回 None = 本线站格不全在环上（不应发生，调用方整条复制）。
    """
    fc = placed.get(core[0])
    if fc is None or fc not in proute:
        return None
    cells = [placed.get(g) for g in core]
    for rev in (False, True):
        r = list(reversed(proute)) if rev else list(proute)
        k = r.index(fc)
        rot = r[k:-1] + r[:k + 1]
        idxs = [rot.index(c) for c in cells if c in rot]
        if len(idxs) == len(cells) and idxs == sorted(idxs):
            return rot
    return None


def schematic_layout(
    mapdata: MapData,
    transports: List[Transport],
    merged_stations: list,
    enabled_types: set,
    canvas: Tuple[int, int] = (4096, 4096),
    rotation: int = 0,
    spacing: float = 400.0,
    alias: Optional[Dict[int, int]] = None,
    share_corridors: bool = True,
    station_merge_distance: Optional[float] = None,
    merge_nearby_stations: bool = True,
    adaptive_scale: bool = False,
    adaptive_strength: float = 0.40,
    adaptive_max_compress: float = 0.60,
    adaptive_max_expand: float = 2.0,
    adaptive_dense_gap: int = 1,
    adaptive_dense_threshold: float = 0.45,
    transit_model=None,
    geographic: bool = False,
    station_grid_overrides: Optional[Dict[object, Tuple[int, int]]] = None,
    line_loop_overrides: Optional[Dict[int, bool]] = None,
    shared_corridor_overrides: Optional[
        List[Tuple[object, object, Tuple[int, ...]]]
    ] = None,
) -> SchematicResult:
    """网格 A* 布局主入口。

    alias：外部对向分组结果（id(别名) → id(主线)，transit grouping 服务产出）；
    传入时直接消费，不在布局层内重新猜测；None 时回退布局层内部
    merge_opposite_lines（非地铁图路径，行为不变）。
    share_corridors：共享段几何复用（共站相邻段复用先布线者几何，
    消除共走廊平行四边形伪影）。
    station_merge_distance：布局域近距站合并距离（米）；未设置时沿用
    spacing 的旧比例。merge_nearby_stations=False 时完全关闭该阶段。
    adaptive_scale：是否启用动态比例尺。启用时先为密集站区预留额外网格
    间距并重新进行 A*，再对最终网格构建非均匀 x/y 坐标轴，让密集站区
    获得更多像素空间，现实中的长区段按对数比例压缩。

    参数见模块 docstring。失败抛 SchematicLayoutError（不兜底）。

    rotation：与主图相同的顺时针输出角度。站点在 snap/A* 之前先经
    ``orient_world`` 定向，保证四个角度下示意图与地理底图方位一致。
    """
    enabled = {str(value) for value in enabled_types}
    station_grid_overrides = {
        group: (int(cell[0]), int(cell[1]))
        for group, cell in (station_grid_overrides or {}).items()
    }
    line_loop_overrides = {
        int(line): bool(value)
        for line, value in (line_loop_overrides or {}).items()
    }
    forced_corridors = {
        frozenset((start, end)): {int(line_id) for line_id in line_ids}
        for start, end, line_ids in (shared_corridor_overrides or ())
        if start != end and len(line_ids) >= 2
    }
    lines = [tr for tr in transports
             if (tr.type in enabled
                 or getattr(getattr(tr, "type", None), "value", None)
                 in enabled
                 or str(getattr(tr, "type", "")) in enabled)
             and tr.stops]
    if not lines:
        raise SchematicLayoutError("no transport lines for schematic layout")

    # 逻辑组：node → 组id；组真实位置
    node_group: Dict[object, int] = {}
    group_pos: Dict[int, Tuple[float, float]] = {}

    def station_nodes(station) -> tuple[int, ...]:
        keys = getattr(station, "member_stop_keys", None)
        if keys:
            result = []
            for key in keys:
                try:
                    result.append(int(str(key).split(":", 1)[0]))
                except (TypeError, ValueError):
                    continue
            return tuple(result)
        return tuple(int(stop.node_id)
                     for stop in getattr(station, "stops", ()))

    def station_position(station):
        return getattr(station, "position", None)

    def station_names(station) -> tuple[str, ...]:
        display_name = getattr(station, "name", "")
        value = getattr(display_name, "text", display_name) or ""
        if str(value).strip():
            return (str(value).strip(),)
        return tuple(str(getattr(stop, "name", "") or "").strip()
                     for stop in getattr(station, "stops", ())
                     if str(getattr(stop, "name", "") or "").strip())

    # T02 模型包含节点和交通模式的精确身份；新渲染使用类型化查找，旧接口使用数字回退。
    stop_key_by_line_node: Dict[tuple[int, int], str] = {}
    if transit_model is not None:
        for stop in getattr(transit_model, "stops", ()):
            for line_id in stop.serving_line_ids:
                stop_key_by_line_node[(int(line_id), int(stop.network_node_id))] = (
                    stop.stop_key)

    def group_for_node(node_id: int, line=None) -> int | None:
        if line is not None and transit_model is not None:
            key = stop_key_by_line_node.get((int(line.id), int(node_id)))
            if key in node_group:
                return node_group[key]
        return node_group.get(int(node_id))

    def group_for_type(node_id: int, line_type: str) -> int | None:
        if transit_model is not None:
            wanted = str(line_type).casefold()
            for stop in getattr(transit_model, "stops", ()):
                if (int(stop.network_node_id) == int(node_id)
                        and str(stop.kind.value).casefold() == wanted):
                    value = node_group.get(stop.stop_key)
                    if value is not None:
                        return value
        return group_for_node(node_id)

    for ms in merged_stations:
        position = station_position(ms)
        if position is None:
            continue
        group_pos[ms.id] = orient_world(
            position.x, position.z, rotation)
        keys = getattr(ms, "member_stop_keys", None) or ()
        if keys and transit_model is not None:
            for key in keys:
                node_group[str(key)] = ms.id
        for node_id in station_nodes(ms):
            # 一个节点可有不同模式的站点，数字回退仅在无歧义时填充。
            node_group.setdefault(int(node_id), ms.id)
        if keys:
            for key in keys:
                try:
                    node_group.setdefault(str(key), ms.id)
                except (TypeError, ValueError):
                    pass
    if not group_pos:
        raise SchematicLayoutError("no station positions for schematic layout")

    from .services.transit.grouping import normalize_transit
    from .services.transit.topology import physical_legs
    legacy_stops = normalize_transit(mapdata).stops if transit_model is None else ()
    topology_cache = {}

    # 过滤出至少有 1 个可定位站的线路。Transport.stops 是运行轨迹，
    # 常含 A-B-C-D-C-B 式返程；A* 前折叠为唯一无向物理边。
    # 简单路径/环线变成单件，分支图变成一条主链和若干边不重复支链。
    usable = []
    branch_extra_chains: Dict[int, List[List[int]]] = {}
    topology_edges: Dict[int, Tuple[Tuple[int, int], ...]] = {}
    topology_stats: Dict[int, Tuple[str, int, int]] = {}
    for tr in lines:
        gseq = []
        for s in tr.stops:
            g = group_for_node(s.node_id, tr)
            if geographic and g is None:
                raise SchematicLayoutError(
                    f"Transport {tr.id}: no position for stop {s.node_id}")
            if g is not None and (not gseq or gseq[-1] != g):
                gseq.append(g)
        if gseq:
            if transit_model is not None:
                topology = transit_model.physical_topologies.get(int(tr.id))
            else:
                station_of = {stop.stop_key: group_for_node(stop.network_node_id, tr)
                              for stop in legacy_stops
                              if group_for_node(stop.network_node_id, tr) is not None}
                legs, _issues = physical_legs(mapdata, tr, station_of, legacy_stops, topology_cache)
                sequence = [station for leg in legs for station in leg.stations]
                topology = normalize_station_sequence(sequence or gseq)
            if topology is None:
                raise SchematicLayoutError(f"Transport {tr.id}: missing physical topology")
            if topology.simple_sequence is not None:
                route_seq = list(topology.simple_sequence)
            else:
                # 分支图按最大链拆成多部件。最长链走原有主路由管线，
                # 其余链稍后在共享站锚点上独立布线；每条无向边只出现一次。
                chains = sorted(
                    (list(chain) for chain in topology.chains),
                    key=lambda chain: (-len(chain), tuple(chain)),
                )
                route_seq = chains[0] if chains else gseq
                if len(chains) > 1:
                    branch_extra_chains[id(tr)] = chains[1:]
            # 手动环线意图只影响简单路径和闭环，不改变分支拓扑。
            loop_override = line_loop_overrides.get(int(tr.id))
            if loop_override is not None and topology.simple_sequence is not None:
                if loop_override and len(route_seq) >= 3 and route_seq[0] != route_seq[-1]:
                    route_seq.append(route_seq[0])
                elif not loop_override and len(route_seq) > 1 and route_seq[0] == route_seq[-1]:
                    route_seq.pop()
                topology = normalize_station_sequence(route_seq)
            topology_stats[id(tr)] = (
                topology.kind,
                topology.raw_leg_count,
                topology.collapsed_leg_count,
            )
            topology_edges[id(tr)] = topology.edges
            # route_seq 已包含显式闭环，或由 Metro Line 首尾拓扑边
            # 证明的一圈站序；后续布局统一消费归一化结果。
            usable.append((tr, route_seq))
    if not usable:
        raise SchematicLayoutError("no locatable stations in lines")
    if geographic:
        # 此草稿不执行吸附、寻路或美化约束；交叉点不是新站点，身份与源链保持显式。
        width, height = canvas
        pad = max(min(width, height) * 0.08, 1.0)
        xs, ys = zip(*group_pos.values())
        if not all(math.isfinite(value) for value in (*xs, *ys)):
            raise SchematicLayoutError("non-finite station coordinates")
        scale = min(max(width - 2 * pad, 1) / max(max(xs) - min(xs), 1),
                    max(height - 2 * pad, 1) / max(max(ys) - min(ys), 1))
        result = SchematicResult(
            node_grid=dict(group_pos), topology_stats=topology_stats,
            transform=(scale, width / 2 - (min(xs) + max(xs)) * scale / 2,
                       height / 2 - (min(ys) + max(ys)) * scale / 2),
            spacing_m=spacing, spacing_px=spacing * scale,
            fallback_strategy="geographic",
        )
        for key, group in node_group.items():
            if group not in group_pos:
                continue
            position = result.to_px(*group_pos[group])
            if isinstance(key, str):
                result.stop_positions[key] = position
            else:
                result.positions[key] = position
        for line, main_chain in usable:
            chains = [main_chain, *branch_extra_chains.get(id(line), ())]
            parts = [[group_pos[group] for group in chain] for chain in chains]
            indices = [list(range(len(chain) - int(len(chain) > 1 and chain[0] == chain[-1])))
                       for chain in chains]
            result.routes[id(line)] = parts[0]
            result.station_indices[id(line)] = indices[0]
            result.route_parts[id(line)] = parts[1:]
            result.part_station_indices[id(line)] = indices[1:]
            result.source_chains[int(line.id)] = tuple(tuple(chain) for chain in chains)
        return result
    # 主干优先（站点多者先布局，占用核心走廊）
    usable.sort(key=lambda t: -len(t[1]))
    # 对向线路合并（P6）：站点序列等价的线只路由一次（防 A* 抢道）；
    # 外部分组结果（公共交通 grouping 服务）优先，避免布局层重复猜测
    if alias is None:
        # 直接调用方与渲染作业使用同一个 CMM 分组服务。
        from .cmm import JobParams
        from .services.transit import build_line_groups, normalize_transit

        normalized = normalize_transit(mapdata, JobParams(view_all=True))
        groups = build_line_groups(mapdata, normalized, JobParams(view_all=True))
        alias = {
            id(normalized.source_lines[item.id]): id(normalized.source_lines[group.primary.id])
            for group in groups for item in group.aliases
            if item.id in normalized.source_lines and group.primary.id in normalized.source_lines
        }
    else:
        # 只保留本次参与布局的线路间的映射（防陈旧条目干扰）
        valid_ids = {id(tr) for tr, _g in usable}
        alias = {a: p for a, p in alias.items()
                 if a in valid_ids and p in valid_ids}

    # ---- 布局域近距站合并（P4）：格域连通簇共用一格 ----
    # snap 后格距离 ≤ _STATION_MERGE_GRID 的逻辑组并成一簇（传递闭包），
    # 簇内共用质心格（近距站视觉合并为大站，名字并列，线路汇成换乘）
    gids = sorted(group_pos)
    rep = {g: g for g in gids}
    merge_th = (float(station_merge_distance)
                if station_merge_distance is not None
                and float(station_merge_distance) > 0
                else spacing * _STATION_MERGE_GRID)
    # 同一物理线路上的两个不同逻辑站即使距离很近，也不能被 P4
    # 仅凭距离合并；否则会把环线站序压短，甚至制造伪换乘/自交。
    same_line_station_pairs: Set[Tuple[int, int]] = set()
    for _tr, _gseq in usable:
        line_gids = list(dict.fromkeys(_gseq))
        for j, a in enumerate(line_gids):
            for b in line_gids[j + 1:]:
                same_line_station_pairs.add((min(a, b), max(a, b)))
    for i, a in enumerate(gids) if merge_nearby_stations and transit_model is None else ():
        ax, az = group_pos[a]
        for b in gids[i + 1:]:
            if (a, b) in same_line_station_pairs:
                continue
            bx, bz = group_pos[b]
            if (bx - ax) ** 2 + (bz - az) ** 2 <= merge_th * merge_th:
                # 并查集 union（代表取小 id，保证确定性）
                ra, rb = rep[a], rep[b]
                if ra != rb:
                    members_a = [g for g in gids if rep[g] == ra]
                    members_b = [g for g in gids if rep[g] == rb]
                    if any((min(x, y), max(x, y))
                           in same_line_station_pairs
                           for x in members_a for y in members_b):
                        # 不能通过第三个近距站传递合并出同线伪站。
                        continue
                    for k in gids:
                        if rep[k] == rb:
                            rep[k] = ra
    cluster_of: Dict[int, int] = {g: r for g, r in rep.items() if g != r}
    # 簇质心位置（代表组位置 → 簇内质心；单组簇不变）
    centroid = dict(group_pos)
    groups_by_rep: Dict[int, List[int]] = {}
    for g in gids:
        groups_by_rep.setdefault(rep[g], []).append(g)
    for r, members in groups_by_rep.items():
        if len(members) > 1:
            centroid[r] = (
                sum(group_pos[m][0] for m in members) / len(members),
                sum(group_pos[m][1] for m in members) / len(members),
            )

    # ``spacing`` 同时承担世界坐标采样精度和最终视觉站距。先记录用户请求，
    # 等密集压力计算完成后再决定是否只细化内部网格。
    requested_spacing = float(spacing)
    initial_cells = [_snap(centroid[r], requested_spacing)
                     for r in groups_by_rep]
    collision_ratio = ((len(initial_cells) - len(set(initial_cells)))
                       / max(len(initial_cells), 1))

    # 物理线路邻接关系用于同格冲突消解。过去候选格只按距离和坐标排序，
    # 密集区会把中间站推到前/后站反方向，轻则画出大回环，重则令整线在
    # 禁止自交约束下无解。这里只改变发生冲突的站，未冲突站仍保持原 snap。
    topology_neighbors: Dict[int, Set[int]] = {}
    for edges in topology_edges.values():
        for a, b in edges:
            ra, rb = rep.get(a, a), rep.get(b, b)
            if ra == rb or ra not in groups_by_rep or rb not in groups_by_rep:
                continue
            topology_neighbors.setdefault(ra, set()).add(rb)
            topology_neighbors.setdefault(rb, set()).add(ra)

    # ---- 动态比例尺的密集站区预留 ----
    # 仅在后处理轴上拉伸时，同一/相邻网格的站点仍会挤在一起。
    # 先用真实站点间距和线路数量估计密度，为高压站点预留一格额外
    # 缓冲；随后线路会基于新锚点重新 A*，因此站点与线路几何保持一致。
    group_lines: Dict[int, Set[int]] = {}
    for tr, gseq in usable:
        tid = id(tr)
        for g in set(gseq):
            r = rep.get(g, g)
            group_lines.setdefault(r, set()).add(alias.get(tid, tid))

    def calculate_dense_pressure(active_spacing: float) -> Dict[int, float]:
        pressure: Dict[int, float] = {}
        if not adaptive_scale or not groups_by_rep:
            return pressure
        dense_radius = max(float(active_spacing) * 2.0, 1.0)
        dense_radius_sq = dense_radius * dense_radius
        reps = sorted(groups_by_rep)
        for r in reps:
            x0, z0 = centroid[r]
            nearby = 0
            for other in reps:
                if other == r:
                    continue
                x1, z1 = centroid[other]
                if (x1 - x0) ** 2 + (z1 - z0) ** 2 <= dense_radius_sq:
                    nearby += 1
            density = min(nearby / 5.0, 1.0)
            transfer = min(
                max(len(group_lines.get(r, ())) - 1, 0) / 3.0, 1.0)
            pressure[r] = min(
                density * 0.70 + transfer * 0.30, 1.0)
        return pressure

    # 400m 网格上大量同格站，或三成以上站点处于密集区时，冲突消解会
    # 把中间站推到线路反方向：轻则大回环，重则 A* 无解。内部网格细化
    # 一倍后，fit 比例会相应缩小，最终画面尺度基本不变；它只增加可用
    # 锚点，不覆盖用户选择的输出分辨率或视觉站距。
    preliminary_pressure = calculate_dense_pressure(requested_spacing)
    preliminary_dense_ratio = (
        sum(value >= float(adaptive_dense_threshold)
            for value in preliminary_pressure.values())
        / max(len(preliminary_pressure), 1)
    )
    if (requested_spacing > 200.0
            and (collision_ratio >= 0.15
                 or preliminary_dense_ratio >= 0.30)):
        spacing = max(requested_spacing * 0.5, 200.0)

    dense_pressure = calculate_dense_pressure(float(spacing))

    dense_gap = max(0, min(int(adaptive_dense_gap), 2))
    manual_reps = {
        r: min((g, station_grid_overrides[g])
               for g in members if g in station_grid_overrides)[1]
        for r, members in groups_by_rep.items()
        if any(g in station_grid_overrides for g in members)
    }
    rep_grid = _place_station_grid(
        {r: centroid[r] for r in groups_by_rep}, topology_neighbors,
        {r: len(lines) for r, lines in group_lines.items()}, dense_pressure,
        float(spacing), manual_reps, dense_gap)
    grid = {member: rep_grid[r] for r, members in groups_by_rep.items() for member in members}

    # ---- 逐线路由 ----
    placed: Dict[int, Tuple[int, int]] = {}
    placed_cells: Set[Tuple[int, int]] = set()
    edge_use: Dict[Tuple[int, int, int, int], List[Tuple[int, int]]] = {}
    routes: Dict[int, List[Tuple[int, int]]] = {}
    station_idx: Dict[int, List[int]] = {}
    route_parts: Dict[int, List[List[Tuple[int, int]]]] = {}
    part_station_idx: Dict[int, List[List[int]]] = {}
    octa_loops: Set[int] = set()      # 八边形环线路 id（简化阶段跳过）
    core_of: Dict[int, List[int]] = {}    # 线路 id → 站序（共享段登记用）
    loop_of: Dict[int, bool] = {}
    legal_crossings: Dict[int, Set[Tuple[int, int]]] = {}

    all_cells = list(grid.values())
    xs = [c[0] for c in all_cells]
    ys = [c[1] for c in all_cells]

    for tr, gseq in usable:
        if id(tr) in alias:
            continue          # 对向线：主线路由完成后复制（不独立跑 A*）
        input_closed = len(gseq) >= 2 and gseq[0] == gseq[-1]
        is_loop = input_closed or (
            topology_stats.get(id(tr), ("", 0, 0))[0] == "cycle"
        )
        core = gseq[:-1] if input_closed else gseq
        core_of[id(tr)] = core
        loop_of[id(tr)] = is_loop
        source_crossings = _source_leg_crossings(
            core, group_pos, is_loop)
        if source_crossings:
            legal_crossings[id(tr)] = source_crossings
        if len(core) < 2:
            # 单站线：放自身格（无路由）
            g = core[0]
            if g not in placed:
                placed[g] = grid[g]
                placed_cells.add(grid[g])
            routes[id(tr)] = [grid[g]]
            station_idx[id(tr)] = [0]
            continue

        # 规则环可选，但必须尊重相同的地理锚点。
        if (is_loop and not source_crossings
                and not any(g in station_grid_overrides for g in core)
                and not any(g in placed for g in core)):
            cells = [grid[g] for g in core]
            boundary = _octagon_boundary_with_capacity(cells, len(core), _octagon_boundary(cells))
            path_full, new_placed, s_idx = _project_loop(core, grid, placed, placed_cells, boundary)
            proposed = {**grid, **placed, **new_placed}
            reserved = {cell for g, cell in grid.items() if g not in core}
            acceptable = all(
                math.dist(cell, (group_pos[g][0]/spacing, group_pos[g][1]/spacing)) <= 1.5+1e-9
                and cell not in reserved
                and _station_order_ok(rep.get(g, g), cell,
                    {rep.get(other, other): point for other, point in proposed.items()},
                    centroid, topology_neighbors)
                for g, cell in new_placed.items())
            if acceptable:
                for g, cell in new_placed.items():
                    placed[g] = cell
                    placed_cells.add(cell)
                _validate_line_route(
                    path_full, s_idx, closed=True,
                    label=f"Transport {tr.id} {tr.name or ''}".strip(),
                )
                routes[id(tr)] = path_full
                station_idx[id(tr)] = s_idx
                _record_edges(path_full, id(tr), edge_use)
                octa_loops.add(id(tr))
                continue

        # 起点
        g0 = core[0]
        if g0 not in placed:
            placed[g0] = grid[g0]
            placed_cells.add(grid[g0])

        # ---- P2 骨架先行：按完整站序宏观 A* 定骨架 ----
        # 骨架必须经过每个站点锚点。旧实现只放首末站和已 placed 换乘站，
        # 却让中间站路由承受“贴骨架”惩罚；当中间站偏离首末站直线时，
        # 线路会先朝错误骨架偏移再折回站心，形成擦肩而过的发卡弯。
        waypoints = [placed[g0]]
        for g in core[1:]:
            waypoint = placed.get(g, grid[g])
            if waypoint != waypoints[-1]:
                waypoints.append(waypoint)
        corridor: Optional[Dict[Tuple[int, int], int]] = None
        skel_box: Optional[Tuple[int, int, int, int]] = None
        if len(waypoints) >= 2:
            skel = _build_skeleton(waypoints, placed_cells, edge_use)
            if skel:
                corridor = _skeleton_corridor(skel)
                sxs = [c[0] for c in skel]
                sys_ = [c[1] for c in skel]
                skel_box = (min(sxs) - 8, min(sys_) - 8,
                            max(sxs) + 8, max(sys_) + 8)

        path_full, s_idx = _route_line_with_constraints(
            tr, core, is_loop, grid, placed, placed_cells, edge_use,
            corridor, skel_box, source_crossings,
        )
        routes[id(tr)] = path_full
        station_idx[id(tr)] = s_idx

    if not routes:
        raise SchematicLayoutError("layout produced no routes")

    # ---- 站间段简化：L → Z（≤2 拐点）+ 走廊对齐 + staircase 拉直 ----
    # 八边形环线路由已是规整沿边形态，简化反而会拉直破坏环 → 跳过
    for tr_id in list(routes):
        route = routes[tr_id]
        if len(route) < 2 or tr_id in octa_loops:
            continue
        original_route = list(route)
        original_s_idx = list(station_idx[tr_id])
        original_station_cells = [route[i] for i in original_s_idx]
        # 每线重算快照（前序线路对齐移动会改变格归属/占用）
        station_cells_all = set(placed_cells)
        s_idx = station_idx[tr_id]
        new_route = [route[s_idx[0]]]
        new_s = [0]
        for k in range(len(s_idx) - 1):
            i0, i1 = s_idx[k], s_idx[k + 1]
            seg = _simple_seg(route[i0], route[i1],
                              route[i0:i1 + 1], station_cells_all)
            new_route.extend(seg[1:])
            new_s.append(len(new_route) - 1)
        # 环线闭合段（首尾同格）
        if route[0] == route[-1] and s_idx[-1] != len(route) - 1:
            i0 = s_idx[-1]
            seg = _simple_seg(route[i0], route[0],
                              route[i0:], station_cells_all)
            new_route.extend(seg[1:])
        # 站点落格在首次布局后冻结。旧版走廊对齐会同步移动 placed，
        # 使共享锚点随线路处理顺序漂移；现在只保留不移动站点的简化。
        # staircase 拉直（正交偏移直线删中点；站点格不删，索引重建）
        keep = {new_route[i] for i in new_s}
        cells_seq = [new_route[i] for i in new_s]
        new_route = _straighten(new_route, keep)
        new_route = _repair_octolinear(new_route)   # 移站越界步修复
        new_s = _rebuild_s(new_route, cells_seq)
        try:
            if [new_route[i] for i in new_s] != original_station_cells:
                raise SchematicLayoutError(
                    f"Transport {tr_id} postprocess moved station cell")
            _validate_line_route(new_route, new_s,
                                 closed=(new_route[0] == new_route[-1]),
                                 label=f"Transport {tr_id} postprocess",
                                 allowed_leg_crossings=
                                 legal_crossings.get(tr_id))
        except SchematicLayoutError:
            # 后处理只是视觉优化，不得把有效拓扑变成无效拓扑。
            new_route, new_s = original_route, original_s_idx
        routes[tr_id] = new_route
        station_idx[tr_id] = new_s

    # ---- 共享段几何复用：共站相邻段强制同一格路径 ----
    # 共走廊线路（含未达归并阈值的异线共站对）独立 A* 各走各格是
    # 平行四边形伪影的根源：在共享的相邻站对间复用先布线者的最终
    # 几何（逐格一致），绘制层共边束并行偏移（同色重叠/异色并行）
    # 才有依据。按布线顺序登记，首个提供者几何为准；拼接发生在
    # 简化/拉直定稿后，共享段不再被后处理改写。
    seg_map: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}
    stable_line_id = {id(tr): int(tr.id) for tr, _gseq in usable}

    def shares_segment(tr_id: int, u, v) -> bool:
        return (share_corridors or stable_line_id.get(tr_id)
                in forced_corridors.get(frozenset((u, v)), set()))

    if share_corridors or forced_corridors:
        for tr_id in list(routes):
            core = core_of.get(tr_id)
            s_idx = station_idx.get(tr_id)
            route = routes[tr_id]
            if not core or not s_idx or len(route) < 2:
                continue
            original_route = list(route)
            original_s_idx = list(s_idx)
            original_station_cells = [route[i] for i in s_idx]
            cells_seq = [route[i] for i in s_idx]
            offset = 0
            for k in range(len(s_idx) - 1):
                i0, i1 = s_idx[k] + offset, s_idx[k + 1] + offset
                u, v = core[k], core[k + 1]
                seg = route[i0:i1 + 1]
                enabled_corridor = shares_segment(tr_id, u, v)
                rec = seg_map.get((u, v)) if enabled_corridor else None
                if enabled_corridor and rec is None:
                    rec2 = seg_map.get((v, u))
                    rec = rec2[::-1] if rec2 is not None else None
                if (rec is not None and len(rec) >= 2
                        and rec[0] == seg[0] and rec[-1] == seg[-1]
                        and rec != seg):
                    route = route[:i0] + list(rec) + route[i1 + 1:]
                    offset += len(rec) - (i1 - i0 + 1)
                    seg = list(rec)
                if enabled_corridor:
                    seg_map.setdefault((u, v), list(seg))
                    seg_map.setdefault((v, u), list(seg[::-1]))
            # 环线闭合段（首尾同格）
            if (loop_of.get(tr_id) and route[0] == route[-1]
                    and s_idx[-1] + offset != len(route) - 1):
                i0 = s_idx[-1] + offset
                u, v = core[-1], core[0]
                seg = route[i0:]
                enabled_corridor = shares_segment(tr_id, u, v)
                rec = seg_map.get((u, v)) if enabled_corridor else None
                if enabled_corridor and rec is None:
                    rec2 = seg_map.get((v, u))
                    rec = rec2[::-1] if rec2 is not None else None
                if (rec is not None and len(rec) >= 2
                        and rec[0] == seg[0] and rec[-1] == seg[-1]
                        and rec != seg):
                    route = route[:i0] + list(rec)
                    seg = list(rec)
                if enabled_corridor:
                    seg_map.setdefault((u, v), list(seg))
                    seg_map.setdefault((v, u), list(seg[::-1]))
            candidate_s = _rebuild_s(route, cells_seq)
            try:
                if [route[i] for i in candidate_s] != original_station_cells:
                    raise SchematicLayoutError(
                        f"Transport {tr_id} shared corridor moved station cell")
                _validate_line_route(
                    route, candidate_s,
                    closed=loop_of.get(tr_id, False),
                    label=f"Transport {tr_id} shared corridor",
                    allowed_leg_crossings=legal_crossings.get(tr_id),
                )
            except SchematicLayoutError:
                # 共享段只能复用不破坏消费者拓扑的几何；否则保留
                # 消费者自己的已验证路径。
                route, candidate_s = original_route, original_s_idx
            routes[tr_id] = route
            station_idx[tr_id] = candidate_s

    # 对向线共享主线路由（P0+P6；几何对象与线路对象分离）。
    # 非环对向线直接引用主线路由对象（routes[alias] is routes[main]），
    # 不复制——双向线共享完全一致的中心路径骨架，只在绘制阶段做
    # ±gap 对称偏移。站点索引按本线站序在共享路由上重建（非环反向线
    # 的索引按主线逆序，绘制层按端点匹配段、不依赖索引单调性）。
    # 环线对向：旋转起报差需要旋转路由序列（闭合环绕起报点重建索引），
    # 保留 _alias_ring 旋转副本——但旋转副本与主线边集一致，绘制层
    # 仍按端点匹配同一物理走廊做对称偏移。
    # edge_lines 重建自动登记两线占用——绘制层颜色分组偏移的依据
    for tr, gseq in usable:
        pid = alias.get(id(tr))
        if pid is None or pid not in routes:
            continue
        proute = routes[pid]
        input_closed = len(gseq) >= 2 and gseq[0] == gseq[-1]
        is_loop = input_closed or (
            topology_stats.get(id(tr), ("", 0, 0))[0] == "cycle"
        )
        core = gseq[:-1] if input_closed else gseq
        # 近距合并的独立终点组补落格（邻接已置站同格，绘制并列显示）
        for i, g in enumerate(core):
            if g in placed:
                continue
            ref = None
            for j in range(i + 1, len(core)):
                if core[j] in placed:
                    ref = placed[core[j]]
                    break
            if ref is None:
                for j in range(i - 1, -1, -1):
                    if core[j] in placed:
                        ref = placed[core[j]]
                        break
            if ref is None:
                ref = proute[0] if proute else (0, 0)
            placed[g] = ref
            placed_cells.add(ref)
        if is_loop:
            # 环线：旋转副本（闭合环绕起报点重建索引；旋转后边集不变）
            a_route: List[Tuple[int, int]] = list(proute)
            if proute:
                rot = _alias_ring(proute, core, placed)
                if rot is not None:
                    a_route = rot
            routes[id(tr)] = a_route
        else:
            # 非环：共享主线路由对象（同一几何引用，不复制不反转）
            routes[id(tr)] = proute
            a_route = proute
        # 站点索引按本线站序在路由上重建（非环反向线 = 主线逆序索引；
        # 绘制层按段端点匹配，不依赖索引单调性）。
        # 私有站（交路延伸端头，不在主线路由上）：聚拢到站序上最近
        # 的有效站索引（旧行为塌到线尾会产生浮空连线）
        valid_idx: List[Tuple[int, int]] = []
        for i, g in enumerate(core):
            cell = placed.get(g)
            if cell is not None and cell in a_route:
                valid_idx.append((i, a_route.index(cell)))
        a_s: List[int] = []
        for i, g in enumerate(core):
            cell = placed.get(g)
            j: Optional[int] = None
            if cell is not None and cell in a_route:
                j = a_route.index(cell)
            elif valid_idx:
                best_d, best_j = None, valid_idx[0][1]
                for i2, j2 in valid_idx:
                    d = abs(i2 - i)
                    if best_d is None or d < best_d:
                        best_d, best_j = d, j2
                j = best_j
            else:
                j = len(a_route) - 1
            a_s.append(min(j, len(a_route) - 1))
        station_idx[id(tr)] = a_s if a_s else [0]

    # ---- 分支线多部件路由 ----
    # 主链已经确定全局站点锚点；支链从已放置的分叉站出发，
    # 每条物理无向边只寻路一次。与其他线路共享真实站对时优先复用
    # seg_map 的已定稿几何，不做“相隔一格看起来平行”的推测并轨。
    edge_use = {}
    for tr_id, route in routes.items():
        _record_edges(route, tr_id, edge_use)

    station_cells_all = set(placed.values())
    for tr, _gseq in usable:
        tr_id = id(tr)
        if tr_id in alias:
            continue
        chains = branch_extra_chains.get(tr_id, ())
        if not chains:
            continue
        out_parts: List[List[Tuple[int, int]]] = []
        out_indices: List[List[int]] = []
        out_chains = []
        line_cells: Set[Tuple[int, int]] = set(routes.get(tr_id, ()))
        line_edges: Set[Tuple[int, int, int, int]] = {
            _edge_key(a, b)
            for a, b in zip(routes.get(tr_id, ()), routes.get(tr_id, ())[1:])
        }
        for original_chain in chains:
            chain = list(original_chain)
            # 从已定位端点起步，使支链在分叉站与主链无缝相接。
            if chain[-1] in placed and chain[0] not in placed:
                chain.reverse()
            if chain[0] not in placed:
                placed[chain[0]] = grid[chain[0]]
                placed_cells.add(grid[chain[0]])

            part = [placed[chain[0]]]
            base_line_cells = set(line_cells)
            base_line_edges = set(line_edges)
            line_cells.add(part[0])
            p_idx = [0]
            prev_dir: Optional[int] = None
            for chain_i, (u, v) in enumerate(zip(chain, chain[1:])):
                start = placed[u]
                goal = placed.get(v, grid[v])
                # 布局域近距合并可能把支链上的两个逻辑站聚到同一视觉
                # 站格。它们之间没有可绘制边；若仍追加站点索引，会得到
                # 相同的连续索引并被整线校验判为 station order conflict。
                if start == goal:
                    if v not in placed:
                        placed[v] = start
                    continue
                goals = {goal}
                hard_block = set(placed_cells)
                hard_block.update(
                    placed.get(g, grid[g]) for g in chain[chain_i + 1:]
                )
                enabled_corridor = shares_segment(tr_id, u, v)
                rec = seg_map.get((u, v)) if enabled_corridor else None
                if (rec is not None and len(rec) >= 2
                        and rec[0] == start and rec[-1] == goal
                        and not any(cell in hard_block for cell in rec[1:-1])
                        and not _path_conflicts_with_line(
                            list(rec), line_cells, line_edges, start, goals)):
                    segment = list(rec)
                    prev_dir = _dir_of(segment[-2], segment[-1])
                else:
                    bounds = (
                        min(start[0], goal[0]) - 8,
                        min(start[1], goal[1]) - 8,
                        max(start[0], goal[0]) + 8,
                        max(start[1], goal[1]) + 8,
                    )
                    segment, prev_dir = _astar(
                        start, {goal}, prev_dir, placed_cells,
                        edge_use, bounds,
                        current_line_cells=line_cells,
                        current_line_edges=line_edges,
                        hard_block_cells=hard_block,
                        )
                    raw_segment = list(segment)
                    # 未来分支站点可能尚未放置；简化时仍须像寻路阶段一样保留其占位。
                    candidate = _simple_seg(
                        start, goal, segment, station_cells_all | hard_block)
                    candidate = _repair_octolinear(candidate)
                    segment = (candidate if not any(
                        cell in hard_block for cell in candidate[1:-1])
                        and not _path_conflicts_with_line(
                        candidate, line_cells, line_edges, start, goals)
                               else raw_segment)
                if v not in placed:
                    placed[v] = segment[-1]
                    placed_cells.add(segment[-1])
                    station_cells_all.add(segment[-1])
                part.extend(segment[1:])
                p_idx.append(len(part) - 1)
                line_cells.update(segment)
                line_edges.update(_edge_key(a, b)
                                  for a, b in zip(segment, segment[1:]))
                _record_edges(segment, tr_id, edge_use)
                if enabled_corridor:
                    seg_map.setdefault((u, v), list(segment))
                    seg_map.setdefault((v, u), list(reversed(segment)))

            # 闭合分支返回同一逻辑站点；保留闭合边，但站点只计一次，且必须是同一源链身份。
            if (chain[0] == chain[-1] and len(p_idx) > 1
                    and part[0] == part[-1]
                    and p_idx[-1] == len(part) - 1):
                p_idx.pop()
            station_cells = [part[i] for i in p_idx]
            raw_part, raw_p_idx = list(part), list(p_idx)
            part = _straighten(part, set(station_cells))
            part = _repair_octolinear(part)
            p_idx = _rebuild_s(part, station_cells)
            moved_station_cells = [part[i] for i in p_idx] != station_cells
            conflicts = _path_conflicts_with_line(
                part, base_line_cells, base_line_edges,
                part[0], {part[-1]})
            try:
                if moved_station_cells or conflicts:
                    raise SchematicLayoutError("branch postprocess changed topology")
                _validate_line_route(part, p_idx,
                                     label=f"Transport {tr.id} branch postprocess")
            except SchematicLayoutError:
                # 拉直是可选步骤；若视觉重写自交或移动站点，仍保留有效的未平滑分支。
                part, p_idx = raw_part, raw_p_idx
            _validate_line_route(part, p_idx,
                                 label=f"Transport {tr.id} branch")
            line_cells = base_line_cells | set(part)
            line_edges = base_line_edges | {
                _edge_key(a, b) for a, b in zip(part, part[1:])
            }
            # 全部支腿都被合并到同一视觉站时没有几何可画，不输出空支链。
            if len(part) >= 2:
                out_parts.append(part)
                out_indices.append(p_idx)
                out_chains.append(chain)
        branch_extra_chains[tr_id] = out_chains
        route_parts[tr_id] = out_parts
        part_station_idx[tr_id] = out_indices


    # 对向/同组别名线共享主线的支链几何，仅在绘制时按颜色分槽。
    for alias_id, primary_id in alias.items():
        if primary_id in route_parts:
            route_parts[alias_id] = route_parts[primary_id]
            part_station_idx[alias_id] = part_station_idx[primary_id]
            branch_extra_chains[alias_id] = list(branch_extra_chains.get(primary_id, ()))

    # 所有主线路由在输出前再做一次整线校验；别名线复用主线几何，
    # 只需验证几何，不要求其运行方向的站点索引单调。
    for tr, _gseq in usable:
        tr_id = id(tr)
        route = routes.get(tr_id)
        if route is None:
            continue
        if tr_id in alias:
            primary_id = alias[tr_id]
            _validate_line_route(
                route, station_idx.get(primary_id),
                label=f"Transport {tr.id} alias",
                closed=(route[0] == route[-1]),
                require_station_order=False,
                allowed_leg_crossings=legal_crossings.get(primary_id),
            )
        else:
            _validate_line_route(
                route, station_idx.get(tr_id),
                closed=loop_of.get(tr_id, route[0] == route[-1]),
                label=f"Transport {tr.id} final",
                allowed_leg_crossings=legal_crossings.get(tr_id),
            )


    # 重建网格边占用（简化后路径变化；走廊共享判定/并行偏移依据）
    edge_use = {}
    for tr_id, route in routes.items():
        _record_edges(route, tr_id, edge_use)
        for part in route_parts.get(tr_id, ()):
            _record_edges(part, tr_id, edge_use)

    # ---- fit 画布（等比缩放 + 居中，45° 角不变）----
    pxs = [p[0] for p in placed.values()]
    pys = [p[1] for p in placed.values()]
    bx0, bx1 = min(pxs), max(pxs)
    by0, by1 = min(pys), max(pys)
    bw = max(bx1 - bx0, 1.0)
    bh = max(by1 - by0, 1.0)
    w, h = canvas
    margin = min(w, h) * 0.08
    scale = min((w - 2 * margin) / bw, (h - 2 * margin) / bh)
    # 站距上限：spacing px @ 4096 基准（少站线路不被 fit 拉得过大）
    cap = max(spacing * min(w, h) / 4096.0, 1.0)
    scale = min(scale, cap)
    ox = (w - (bx0 + bx1) * scale) / 2.0
    oz = (h - (by0 + by1) * scale) / 2.0

    adaptive_x: Optional[Dict[int, float]] = None
    adaptive_y: Optional[Dict[int, float]] = None
    adaptive_scale_range = (1.0, 1.0)
    if adaptive_scale and len(groups_by_rep) >= 2:
        # 动态比例尺在有效网格完成后应用为“非均匀网格轴”，不移动站点
        # 锚点、不重跑 A*，因此不会破坏已验证的环线/分支可行性。
        rep_cells: Dict[int, Tuple[int, int]] = {}
        for r, members in groups_by_rep.items():
            cell = next((placed[m] for m in members if m in placed), None)
            if cell is None:
                cell = grid.get(r)
            if cell is not None:
                rep_cells[r] = cell

        line_sets: Dict[int, Set[int]] = {}
        edge_sets: Dict[Tuple[int, int], Set[int]] = {}
        for tr, _gseq in usable:
            tid = id(tr)
            canonical_tid = alias.get(tid, tid)
            for a, b in topology_edges.get(tid, ()):
                ra, rb = rep.get(a, a), rep.get(b, b)
                if ra == rb or ra not in rep_cells or rb not in rep_cells:
                    continue
                line_sets.setdefault(ra, set()).add(canonical_tid)
                line_sets.setdefault(rb, set()).add(canonical_tid)
                edge_sets.setdefault(
                    (min(ra, rb), max(ra, rb)), set()).add(canonical_tid)

        ms_by_id = {ms.id: ms for ms in merged_stations}
        name_pressure: Dict[int, float] = {}
        for r, members in groups_by_rep.items():
            max_name = 0
            for member in members:
                ms = ms_by_id.get(member)
                if ms is None:
                    continue
                for name in station_names(ms):
                    max_name = max(max_name, len(name))
            name_pressure[r] = min(max_name / 8.0, 1.0)

        # 网格邻域密度 + 换乘线数 + 站名长度 → 局部压力。
        pressure: Dict[int, float] = {}
        for r, (x, y) in rep_cells.items():
            nearby = 0
            for other, (ox2, oy2) in rep_cells.items():
                if other == r:
                    continue
                if math.hypot(ox2 - x, oy2 - y) <= 3.5:
                    nearby += 1
            density = min(nearby / 5.0, 1.0)
            transfer = min(max(len(line_sets.get(r, ())) - 1, 0) / 3.0, 1.0)
            pressure[r] = max(dense_pressure.get(r, 0.0), min(
                density * 0.55 + transfer * 0.35
                + name_pressure.get(r, 0.0) * 0.10,
                1.0))

        # 现实长区段在轴方向上减少权重；密集站区则提高局部权重。
        real_lengths = []
        edge_data = []
        for (a, b), tids in sorted(edge_sets.items()):
            ax2, ay2 = centroid[a]
            bx2, by2 = centroid[b]
            real_length = max(math.hypot(bx2 - ax2, by2 - ay2), 1e-3)
            real_lengths.append(real_length)
            edge_data.append((a, b, real_length, len(tids)))
        real_lengths.sort()
        if real_lengths:
            p50 = real_lengths[(len(real_lengths) - 1) // 2]
            p90 = real_lengths[min(
                len(real_lengths) - 1, int(len(real_lengths) * 0.9))]
        else:
            p50 = p90 = 1.0
        compressed_x: List[Tuple[int, int, float]] = []
        compressed_y: List[Tuple[int, int, float]] = []
        for a, b, real_length, _line_count in edge_data:
            if p90 <= p50 * 1.001:
                longness = 0.0
            else:
                ratio = max(real_length / p50, 1.0)
                longness = min(
                    math.log(ratio) / math.log(p90 / p50), 1.0)
            (ax2, ay2), (bx2, by2) = rep_cells[a], rep_cells[b]
            if ax2 != bx2:
                compressed_x.append((ax2, bx2, longness))
            if ay2 != by2:
                compressed_y.append((ay2, by2, longness))

        samples_x = [(cell[0], pressure.get(r, 0.0))
                     for r, cell in rep_cells.items()]
        samples_y = [(cell[1], pressure.get(r, 0.0))
                     for r, cell in rep_cells.items()]
        adaptive_x = _adaptive_axis_map(
            int(bx0), int(bx1), samples_x, compressed_x,
            bx0 * scale + ox, bx1 * scale + ox,
            strength=adaptive_strength,
            max_compress=adaptive_max_compress,
            max_expand=adaptive_max_expand,
        )
        adaptive_y = _adaptive_axis_map(
            int(by0), int(by1), samples_y, compressed_y,
            by0 * scale + oz, by1 * scale + oz,
            strength=adaptive_strength,
            max_compress=adaptive_max_compress,
            max_expand=adaptive_max_expand,
        )
        if not _adaptive_routes_keep_octilinear(
                routes, adaptive_x, adaptive_y, scale):
            raise SchematicLayoutError(
                "adaptive scale exceeded octilinear angle tolerance")
        intervals = [
            (mapping[key + 1] - mapping[key]) / max(scale, 1e-9)
            for mapping in (adaptive_x, adaptive_y)
            for key in sorted(mapping)[:-1]
            if key + 1 in mapping
        ]
        if intervals:
            adaptive_scale_range = (min(intervals), max(intervals))

    # 站点像素（组内所有 node 同位置）。group_pos 已在布局前按主图
    # 同源矩阵定向，此处不再做成图后镜像。
    positions: Dict[int, Tuple[float, float]] = {}
    stop_positions: Dict[str, Tuple[float, float]] = {}
    for ms in merged_stations:
        g = placed.get(ms.id)
        if g is None:
            continue
        if adaptive_x is not None and adaptive_y is not None:
            px = adaptive_x.get(g[0], g[0] * scale + ox)
            py = adaptive_y.get(g[1], g[1] * scale + oz)
        else:
            px, py = g[0] * scale + ox, g[1] * scale + oz
        keys = getattr(ms, "member_stop_keys", None) or ()
        if keys and transit_model is not None:
            for key in keys:
                stop_positions[str(key)] = (px, py)
        for node_id in station_nodes(ms):
            positions.setdefault(int(node_id), (px, py))
        if not keys and transit_model is None:
            for node_id in station_nodes(ms):
                stop_positions[str(node_id)] = (px, py)

    displacements = []
    final_reps = {r: placed[members[0]] for r, members in groups_by_rep.items()
                  if members[0] in placed}
    for r, members in groups_by_rep.items():
        if r not in final_reps:
            continue
        cell = final_reps[r]
        if not _station_order_ok(r, cell, final_reps, centroid, topology_neighbors):
            raise SchematicLayoutError(f"station {r}: routing reversed station order")
        for member in members:
            displacement = math.dist(cell, (group_pos[member][0]/spacing, group_pos[member][1]/spacing))
            if r not in manual_reps:
                displacements.append(displacement)
                if displacement > 2+1e-9:
                    raise SchematicLayoutError(f"station {member}: routing exceeded two-cell displacement budget")
    ordered_displacements = sorted(displacements)
    def quantile(fraction):
        if not ordered_displacements:
            return 0.0
        index = (len(ordered_displacements)-1)*fraction
        lower, upper = math.floor(index), math.ceil(index)
        return (ordered_displacements[lower]*(upper-index)
                + ordered_displacements[upper]*(index-lower)) if lower != upper else ordered_displacements[lower]
    placement_metrics = {"median_cells": quantile(.5), "p90_cells": quantile(.9),
        "max_cells": max(displacements, default=0.0), "axis_flips": 0,
        "manual_station_ids": sorted(station_grid_overrides), "spacing_m": spacing}

    # 线路稳定排序：不使用 Python 对象地址决定共廊左右槽位，
    # 避免同一存档不同进程渲染时发生槽位交换。
    tr_by_obj = {id(tr): tr for tr, _gseq in usable}
    stable_index = {id(tr): i for i, (tr, _gseq) in enumerate(usable)}
    line_order = {
        tid: (tr_by_obj[tid].id, tr_by_obj[tid].name or "",
              stable_index[tid])
        for tid in tr_by_obj
    }

    # 绘制层接口：edge_lines = {边key: [线路id]}（去重+稳定线序）
    edge_lines: Dict[Tuple[int, int, int, int], List[int]] = {
        ek: sorted({tid for tid, _od in occ},
                   key=lambda tid: line_order.get(tid, (1 << 62, "", tid)))
        for ek, occ in edge_use.items()
    }

    # 语义走廊模型：只有共享同一无向物理站对才算共廊。
    corridor_sets: Dict[Tuple[int, int], Set[int]] = {}
    for tr_id, edges in topology_edges.items():
        for u, v in edges:
            corridor_sets.setdefault((min(u, v), max(u, v)), set()).add(tr_id)
    corridor_lines = {
        edge: sorted(tids,
                     key=lambda tid: line_order.get(tid, (1 << 62, "", tid)))
        for edge, tids in corridor_sets.items()
    }

    # 簇站名（代表 → 成员站名有序去重；单组簇不生成）
    cluster_names: Dict[int, List[str]] = {}
    ms_by_id = {ms.id: ms for ms in merged_stations}
    for r, members in groups_by_rep.items():
        if len(members) > 1:
            names: List[str] = []
            for m in members:
                ms = ms_by_id.get(m)
                if ms is None:
                    continue
                for name in station_names(ms):
                    if name not in names:
                        names.append(name)
            cluster_names[r] = names

    # 构建 alias_pairs 双向映射：主线 id → [对向线 id, ...]
    alias_pairs: Dict[int, List[int]] = {}
    for alias_id, primary_id in alias.items():
        alias_pairs.setdefault(primary_id, []).append(alias_id)

    result = SchematicResult(
        rotation=rotation,
        placement_metrics=placement_metrics,
        positions=positions,
        stop_positions=stop_positions,
        routes=routes,
        station_indices=station_idx,
        route_parts=route_parts,
        part_station_indices=part_station_idx,
        edge_lines=edge_lines,
        corridor_lines=corridor_lines,
        node_grid=placed,
        cluster_of=cluster_of,
        cluster_names=cluster_names,
        route_alias=alias,
        alias_pairs=alias_pairs,
        topology_stats=topology_stats,
        transform=(scale, ox, oz),
        spacing_px=scale,
        adaptive_x=adaptive_x,
        adaptive_y=adaptive_y,
        spacing_m=float(spacing),
        adaptive_strength=(float(adaptive_strength)
                           if adaptive_scale else 0.0),
        adaptive_scale_range=adaptive_scale_range,
        source_chains={
            int(tr.id): tuple(
                [tuple(gseq), *(
                    tuple(chain)
                    for chain in branch_extra_chains.get(id(tr), ())
                )]
            )
            for tr, gseq in usable
        },
    )

    pixel_displacements = sorted(math.dist(result.to_px(*placed[g]),
        result.to_px(group_pos[g][0]/spacing, group_pos[g][1]/spacing))
        for g in placed if g not in station_grid_overrides)
    if pixel_displacements:
        ordered_displacements = pixel_displacements
        result.placement_metrics["ideal_reference_pixels"] = {
            "median": quantile(.5), "p90": quantile(.9), "max": max(pixel_displacements)}
    return result
