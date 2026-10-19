"""地铁示意图的独立语义/背景组件。

本模块只消费已解析的 ``MapData`` 与示意图布局结果，不修改存档对象，
因此可以安全地被 PNG、SVG/网页导出等不同渲染器复用。
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Iterable


from .data import MapData


@dataclass(frozen=True)
class StationFacility:
    """一个站点的最佳客运设施关联。"""

    kind: str              # 机场、铁路和港口设施。
    distance: float
    name: str = ""


_FACILITY_RULES = {
    "airport": {
        "distance": 1000.0,
        "priority": 30,
        "words": ("airport", "airfield", "航站", "机场"),
    },
    "rail": {
        "distance": 600.0,
        "priority": 20,
        "words": ("trainstation", "train station", "railway station",
                  "rail station", "火车站", "铁路客站", "客运站"),
    },
    "port": {
        "distance": 600.0,
        "priority": 10,
        "words": ("ferry", "passenger harbor", "passenger port",
                  "passenger terminal", "码头", "客运港", "客运码头"),
    },
}


def metro_station_ids(mapdata: MapData, enabled_types) -> set:
    """返回参与本次示意图的唯一站点节点。"""
    from . import transit

    enabled = set(enabled_types or ())
    station_ids = set()
    for transport in getattr(mapdata, "transports", ()):
        raw_type = getattr(transport, "type", None)
        kind = transit.normalize_type(raw_type) or raw_type
        if kind in enabled:
            station_ids.update(
                stop.node_id for stop in getattr(transport, "stops", ()))
    return station_ids


def recommended_metro_output_size(mapdata: MapData, enabled_types) -> int:
    """优先低分辨率配合紧凑字号；极端大线网才提升到 4K。"""
    count = len(metro_station_ids(mapdata, enabled_types))
    if count <= 180:
        return 1024
    if count <= 500:
        return 2048
    return 4096


def metro_content_canvas_size(mapdata: MapData, enabled_types,
                              long_edge: int,
                              rotation: int = 0) -> tuple[int, int]:
    """按旋转后的站点包围盒选择横/竖版，长边保持用户选择的分辨率。"""
    from .schematic import orient_world

    long_edge = max(int(long_edge), 128)
    station_ids = metro_station_ids(mapdata, enabled_types)
    nodes = {node.id: node for node in getattr(mapdata, "nodes", ())}
    points = []
    for node_id in station_ids:
        node = nodes.get(node_id)
        pos = getattr(node, "pos", None)
        if pos is not None:
            points.append(orient_world(
                float(pos.x), float(pos.z), rotation))
    if len(points) < 2:
        return long_edge, long_edge
    span_x = max(x for x, _z in points) - min(x for x, _z in points)
    span_z = max(z for _x, z in points) - min(z for _x, z in points)
    dominant = max(span_x, span_z, 1.0)
    # 给近乎直线的线网保留站名宽度；平方根缓和真实地理比例，避免
    # 狭长城市生成难以阅读的横幅或竖条。
    raw_ratio = max(span_x, dominant * 0.12) / max(
        span_z, dominant * 0.12)
    ratio = max(1 / 1.65, min(math.sqrt(raw_ratio), 1.65))
    if 0.90 <= ratio <= 1.10:
        return long_edge, long_edge

    def rounded_side(value: float) -> int:
        quantum = 64 if long_edge >= 512 else 1
        side = int(round(value / quantum) * quantum)
        return max(128, min(side, long_edge))

    if ratio > 1.0:
        return long_edge, rounded_side(long_edge / ratio)
    return rounded_side(long_edge * ratio), long_edge


def _centre(points) -> tuple[float, float] | None:
    if not points:
        return None
    return (sum(p.x for p in points) / len(points),
            sum(p.z for p in points) / len(points))


def _facility_kind(item) -> str | None:
    text = " ".join(str(getattr(item, key, "") or "") for key in
                     ("name", "custom_name", "icls", "service",
                      "sub_service", "ptype")).lower()
    # 货运设施不能被铁路/港口客运换乘误标。
    if any(w in text for w in ("cargo", "freight", "货运", "工业港")):
        return None
    # “monorail station”自身包含子串“rail station”。简单 contains 会把
    # 每座单轨站都误判成国铁客站；地铁、单轨、轻轨和有轨电车明确排除。
    sub_service = str(getattr(item, "sub_service", "") or "").lower()
    local_rail = any(word in text or word in sub_service for word in
                     ("monorail", "metro", "subway", "light rail", "tram"))
    for kind, rule in _FACILITY_RULES.items():
        if kind == "rail" and local_rail:
            continue
        if any((word in text if not word.isascii() else
                re.search(r"(?<![a-z])" + re.escape(word) + r"(?![a-z])",
                          text))
               for word in rule["words"]):
            return kind
    return None


def _merge_facility_candidates(candidates, merge_distances: dict):
    """把同一设施的多个建筑/分区候选合并为一个设施中心。

    存档通常会把机场拆成航站楼、跑道附属建筑和机场分区，不能把每个
    对象都当成独立机场。使用同类候选的连通分量合并，允许机场由多个
    相邻建筑组成，同时不会把机场、铁路站和港口混在一起。
    """
    merged = []
    for kind in _FACILITY_RULES:
        pending = [item for item in candidates if item[0] == kind]
        limit = max(float(merge_distances.get(kind, 0.0)), 0.0)
        while pending:
            group = [pending.pop()]
            changed = True
            while changed:
                changed = False
                keep = []
                for item in pending:
                    _, (x, z), _name = item
                    if any(math.hypot(x - gx, z - gz) <= limit
                           for _, (gx, gz), _ in group):
                        group.append(item)
                        changed = True
                    else:
                        keep.append(item)
                pending = keep
            gx = sum(item[1][0] for item in group) / len(group)
            gz = sum(item[1][1] for item in group) / len(group)
            name = next((str(item[2]).strip() for item in group
                         if str(item[2]).strip()), "")
            merged.append((kind, (gx, gz), name))
    return merged


def match_station_facilities(mapdata: MapData, merged_stations: Iterable,
                             *, distances: dict | None = None,
                             merge_distances: dict | None = None) -> dict[int, StationFacility]:
    """以类别、名称和距离打分，给每个合并站选一个客运设施。

    ``distances`` 可按 ``airport`` / ``rail`` / ``port`` 覆盖站点关联阈值；
    ``merge_distances`` 控制同类设施建筑的合并半径；不向 ``MapData`` 写
    任何派生字段。
    """
    limits = {k: float(v["distance"]) for k, v in _FACILITY_RULES.items()}
    limits.update({k: float(v) for k, v in (distances or {}).items()
                   if k in limits and float(v) > 0})
    merge_limits = {"airport": 1200.0, "rail": 300.0, "port": 400.0}
    merge_limits.update({k: float(v) for k, v in (merge_distances or {}).items()
                         if k in merge_limits and float(v) > 0})
    raw_candidates = []
    for item in list(mapdata.buildings) + list(mapdata.parks):
        kind = _facility_kind(item)
        pos = _centre(getattr(item, "points", ()))
        if kind and pos:
            raw_candidates.append((kind, pos,
                                   (getattr(item, "custom_name", "")
                                    or getattr(item, "name", ""))))

    # 先把同一机场拆出的多个建筑合成一个候选，再执行“最近站点唯一关联”。
    candidates = [
        (index, kind, pos, name)
        for index, (kind, pos, name) in enumerate(
            _merge_facility_candidates(raw_candidates, merge_limits))
    ]

    station_positions = []
    for station in list(merged_stations):
        positions = [mapdata.node(stop.node_id).pos for stop in station.stops
                     if mapdata.node(stop.node_id) is not None]
        if positions:
            station_positions.append(
                (station.id,
                 sum(p.x for p in positions) / len(positions),
                 sum(p.z for p in positions) / len(positions)))

    # 一个真实设施只挂到一个最近的合并站；同时保留类别优先级，避免
    # 机场/铁路/港口都在同一站附近时被距离微差改变语义。
    matches = []
    for candidate_id, kind, (x, z), name in candidates:
        limit = limits[kind]
        for station_id, sx, sz in station_positions:
            distance = math.hypot(x - sx, z - sz)
            if distance <= limit:
                matches.append((-
                    _FACILITY_RULES[kind]["priority"],
                    distance / max(limit, 1.0),
                    distance,
                    candidate_id,
                    station_id,
                    kind,
                    name,
                ))

    found: dict[int, StationFacility] = {}
    used_candidates = set()
    used_stations = set()
    for (_priority, _normal, distance, candidate_id, station_id,
         kind, name) in sorted(matches):
        if candidate_id in used_candidates or station_id in used_stations:
            continue
        used_candidates.add(candidate_id)
        used_stations.add(station_id)
        found[station_id] = StationFacility(kind, distance, str(name or ""))
    return found
