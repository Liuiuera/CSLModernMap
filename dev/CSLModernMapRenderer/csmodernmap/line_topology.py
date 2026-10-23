"""公共交通原始站序的物理拓扑归一化。

游戏导出的 ``Transport.stops`` 是车辆运行轨迹，不一定是地图上应当
绘制一次的物理线路。例如开放线路常见站序为 ``A-B-C-D-C-B``：后半段
是返程，不是第二条地图几何。本模块把有向运行腿折叠为唯一无向边，并
从无向图恢复简单路径/环线；分支图则拆成不重复覆盖边的最大链。

本模块不负责判断两个不同 Transport 是否属于同一物理线路。那是更上层
的线路身份问题，应使用名称、站序和轨道等多种证据保守判断。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set, Tuple


StationId = int | str
Edge = Tuple[StationId, StationId]


def canonical_edge(a: StationId, b: StationId) -> Edge:
    """返回无向边的稳定键。"""
    return (a, b) if a <= b else (b, a)


@dataclass(frozen=True)
class NormalizedLineTopology:
    """一条原始运行轨迹折叠后的物理线路拓扑。"""

    raw_sequence: Tuple[StationId, ...]
    sequence: Tuple[StationId, ...]
    edges: Tuple[Edge, ...]
    chains: Tuple[Tuple[StationId, ...], ...]
    kind: str
    simple_sequence: Optional[Tuple[StationId, ...]]
    raw_leg_count: int
    collapsed_leg_count: int

    @property
    def is_simple(self) -> bool:
        return self.simple_sequence is not None


def _dedupe_adjacent(values: Iterable[StationId]) -> List[StationId]:
    out: List[StationId] = []
    for value in values:
        if not out or out[-1] != value:
            out.append(value)
    return out


def _adjacency(edges: Iterable[Edge]) -> Dict[StationId, Set[StationId]]:
    adj: Dict[StationId, Set[StationId]] = {}
    for a, b in edges:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    return adj


def _first_position(sequence: List[StationId]) -> Dict[StationId, int]:
    pos: Dict[StationId, int] = {}
    for i, station in enumerate(sequence):
        pos.setdefault(station, i)
    return pos


def _walk_path(
    start: StationId,
    adj: Dict[StationId, Set[StationId]],
    first_pos: Dict[StationId, int],
) -> Tuple[StationId, ...]:
    """从端点沿度数不超过 2 的图走到另一端。"""
    out = [start]
    prev: Optional[StationId] = None
    cur = start
    while True:
        nxt = [v for v in adj.get(cur, ()) if v != prev]
        if not nxt:
            break
        nxt.sort(key=lambda v: (first_pos.get(v, 1 << 30), v))
        value = nxt[0]
        out.append(value)
        prev, cur = cur, value
    return tuple(out)


def _walk_cycle(
    sequence: List[StationId],
    adj: Dict[StationId, Set[StationId]],
) -> Tuple[StationId, ...]:
    """按原始轨迹的起始方向遍历一个简单环，并在尾部闭合。"""
    start = sequence[0]
    first_neighbor: Optional[StationId] = None
    for value in sequence[1:]:
        if value in adj[start]:
            first_neighbor = value
            break
    if first_neighbor is None:
        first_neighbor = min(adj[start])

    out = [start, first_neighbor]
    prev, cur = start, first_neighbor
    while cur != start:
        nxt = sorted(v for v in adj[cur] if v != prev)
        if not nxt:
            break
        value = start if start in nxt else nxt[0]
        out.append(value)
        prev, cur = cur, value
        if len(out) > len(adj) + 1:
            break
    return tuple(out)


def _maximal_chains(
    adj: Dict[StationId, Set[StationId]],
    first_pos: Dict[StationId, int],
) -> Tuple[Tuple[StationId, ...], ...]:
    """把图拆成最大链，保证每条无向边恰好出现一次。"""
    unused = {
        canonical_edge(a, b)
        for a, neighbors in adj.items()
        for b in neighbors
    }
    chains: List[Tuple[StationId, ...]] = []
    breaks = sorted(
        (v for v, neighbors in adj.items() if len(neighbors) != 2),
        key=lambda v: (first_pos.get(v, 1 << 30), v),
    )

    def walk(start: StationId, neighbor: StationId) -> Tuple[StationId, ...]:
        chain = [start, neighbor]
        unused.discard(canonical_edge(start, neighbor))
        prev, cur = start, neighbor
        while len(adj[cur]) == 2:
            candidates = [v for v in adj[cur] if v != prev]
            if not candidates:
                break
            value = candidates[0]
            edge = canonical_edge(cur, value)
            if edge not in unused:
                break
            chain.append(value)
            unused.remove(edge)
            prev, cur = cur, value
        return tuple(chain)

    for start in breaks:
        neighbors = sorted(
            adj[start], key=lambda v: (first_pos.get(v, 1 << 30), v)
        )
        for neighbor in neighbors:
            if canonical_edge(start, neighbor) in unused:
                chains.append(walk(start, neighbor))

    # 没有断点的剩余分量是简单环；从原始顺序最早出现的节点开始。
    while unused:
        nodes = {v for edge in unused for v in edge}
        start = min(nodes, key=lambda v: (first_pos.get(v, 1 << 30), v))
        neighbor = min(
            (v for v in adj[start] if canonical_edge(start, v) in unused),
            key=lambda v: (first_pos.get(v, 1 << 30), v),
        )
        chain = list(walk(start, neighbor))
        if chain[-1] != start and canonical_edge(chain[-1], start) in unused:
            unused.remove(canonical_edge(chain[-1], start))
            chain.append(start)
        chains.append(tuple(chain))

    return tuple(chains)


def normalize_station_sequence(
    station_ids: Iterable[StationId],
    *, connections: Iterable[Edge] | None = None,
) -> NormalizedLineTopology:
    """把车辆运行站序折叠为唯一无向物理拓扑。

    简单路径和环线会返回 ``simple_sequence``，可直接作为单部件
    布局。分支图的 ``simple_sequence`` 为 None，调用方应消费
    ``chains`` 作为边不重复的多部件线路。
    """
    raw = tuple(station_ids)
    sequence = _dedupe_adjacent(raw)
    pairs = list(zip(sequence, sequence[1:])) if connections is None else list(connections)
    raw_leg_count = max(len(sequence) - 1, 0) if connections is None else sum(a != b for a, b in pairs)
    edge_set = {
        canonical_edge(a, b)
        for a, b in pairs
        if a != b
    }
    edges = tuple(sorted(edge_set))
    collapsed = raw_leg_count - len(edges)

    if not sequence:
        return NormalizedLineTopology(
            raw, (), (), (), "empty", (), 0, 0
        )
    if not edges:
        station = sequence[0]
        return NormalizedLineTopology(
            raw, tuple(sequence), (), ((station,),), "single", (station,),
            raw_leg_count, collapsed,
        )

    adj = _adjacency(edges)
    first_pos = _first_position(sequence)
    degrees = [len(neighbors) for neighbors in adj.values()]
    endpoints = [v for v, neighbors in adj.items() if len(neighbors) == 1]

    if max(degrees) <= 2 and len(endpoints) == 2:
        start = min(endpoints, key=lambda v: (first_pos.get(v, 1 << 30), v))
        simple = _walk_path(start, adj, first_pos)
        if connections is None or len(simple) == len(adj):
            return NormalizedLineTopology(
                raw, tuple(sequence), edges, (simple,), "path", simple,
                raw_leg_count, collapsed,
            )

    if all(degree == 2 for degree in degrees):
        simple = _walk_cycle(sequence, adj)
        if connections is None or len(simple) == len(adj) + 1:
            return NormalizedLineTopology(
                raw, tuple(sequence), edges, (simple,), "cycle", simple,
                raw_leg_count, collapsed,
            )

    chains = _maximal_chains(adj, first_pos)
    return NormalizedLineTopology(
        raw, tuple(sequence), edges, chains, "branch", None,
        raw_leg_count, collapsed,
    )
