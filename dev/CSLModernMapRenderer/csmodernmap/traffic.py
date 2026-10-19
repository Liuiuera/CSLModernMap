"""交通快照数据模型：加载 CSLModernMap 模组导出的城市交通 JSON（schema v1.2）。

CSLModernMap 模组把游戏内的车道级交通数据导出为 JSON（UTF-8 无 BOM），
字段与 csmodernmap 渲染器同坐标系（游戏世界 x/z 平面，单位米），
本模块将其解析为与 csmodernmap 数据模型风格一致的 dataclass，
供 TrafficLayer 直接按车道渲染。

字段约定（来自 schema v1.2，已由验证脚本确认）：
- 每条车道 lane.congestion ∈ [0,1]；非机动车道 / 无 TMPE 统计 = -1
- lane.position 为相对道路中心线的横向偏移（米，正=前进方向右侧）
- lane.width 为车道宽度（米）
- seg.segStats.weightedAvgCongestion 为该路段加权平均拥堵
- lights[] 红绿灯节点（schema v1.2 新增，游戏本体采集，不依赖 TM:PE）
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Dict, List

# 车道行驶方向（对应 JSON laneDirection）。``Both`` 表示该车道双向共用，
# 分色时并入 Forward；``None`` / 空 表示方向未知，不参与分色。
DIRECTION_FORWARD = "Forward"
DIRECTION_BACKWARD = "Backward"
DIRECTION_BOTH = "Both"


@dataclass
class Vec3:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


@dataclass
class TrafficLane:
    """单车道交通数据（对应 JSON lanes[]）。"""

    lane_index: int = 0
    lane_type: str = ""            # 车辆、行人、停车等类别。
    position: float = 0.0          # 相对道路中心线横向偏移（米）
    width: float = 0.0             # 车道宽（米）
    speed: float = 0.0             # 真实车速 m/s（= relativeSpeed × maxSpeed）
    max_speed: float = 0.0
    relative_speed: float = -1.0   # [0,1]，无统计 = -1
    vehicle_count: int = 0
    congestion: float = -1.0       # [0,1]，非机动车道/无统计 = -1
    data_quality: int = 0          # 0 正常 / 1 降级（TMPE 滑窗未积累）
    direction: str = ""            # 前向、后向、双向或无方向。

    @property
    def is_vehicle(self) -> bool:
        """是否为机动车道（参与拥堵色渲染）。"""
        return self.lane_type == "Vehicle"

    @property
    def has_congestion(self) -> bool:
        """是否拥有有效拥堵值（机动车道 + 数据正常 + congestion >= 0）。"""
        return (self.is_vehicle and self.data_quality == 0
                and math.isfinite(self.congestion) and 0.0 <= self.congestion <= 1.0
                and math.isfinite(self.width) and self.width > 0
                and math.isfinite(self.position))


@dataclass
class SegStats:
    """路段级聚合（对应 JSON segStats）。"""

    total_lanes: int = 0
    vehicle_lanes: int = 0
    total_vehicles: int = 0
    one_way: bool = False
    highway: bool = False
    has_buslane: bool = False
    weighted_avg_congestion: float = -1.0
    simple_avg_congestion: float = -1.0
    weighted_avg_relative_speed: float = -1.0


@dataclass
class TrafficSegment:
    """路段几何 + 车道列表（对应 JSON segments[]）。"""

    segment_id: int = 0
    road_name: str = ""
    item_class: str = ""
    width: float = 0.0
    node_a: Vec3 = field(default_factory=Vec3)
    node_b: Vec3 = field(default_factory=Vec3)
    curve_points: List[Vec3] = field(default_factory=list)  # 贝塞尔 t=0.25/0.5/0.75
    lanes: List[TrafficLane] = field(default_factory=list)
    seg_stats: SegStats = field(default_factory=SegStats)

    def world_points(self) -> List[Vec3]:
        """中心线世界坐标点序列：nodeA + 3 个贝塞尔中间点 + nodeB。"""
        return [self.node_a] + list(self.curve_points) + [self.node_b]

    def directional_lanes(self) -> Dict[str, List[TrafficLane]]:
        """按行驶方向分桶的有效机动车道（只读派生，不改 dataclass 字段）。

        只收 ``has_congestion`` 的车道（机动车道 + ``congestion >= 0`` +
        ``data_quality == 0``）：降级数据（TMPE 滑窗未积累）画出来会骗人，
        无统计的 ``-1`` 更会被渲染器当成"畅通"涂成绿色。
        ``Both`` 并入 ``Forward``，方向未知（``None`` / 空）丢弃。
        返回值只包含**真正有车道**的方向键。
        """
        buckets: Dict[str, List[TrafficLane]] = {}
        for lane in self.lanes:
            if not lane.has_congestion:
                continue
            if lane.direction == DIRECTION_BACKWARD:
                key = DIRECTION_BACKWARD
            elif lane.direction in (DIRECTION_FORWARD, DIRECTION_BOTH):
                key = DIRECTION_FORWARD
            else:
                continue
            buckets.setdefault(key, []).append(lane)
        return buckets

    def direction_offset(self, direction: str, lanes: List[TrafficLane] | None = None
                         ) -> float:
        """该方向车道按宽度加权的横向偏移（米），与 PNG/Web 色带共用。

        ``position`` 正 = 前进方向右侧，所以 ``Forward`` 通常得到正值、
        ``Backward`` 得到负值，两条色带自然分列中心线两侧。
        ``lanes`` 可传入 ``directional_lanes()`` 已算好的结果避免重复分桶；
        缺省时自行分桶。该方向无有效车道时兜底 ``±width/4``（正负按方向）。
        """
        if lanes is None:
            lanes = self.directional_lanes().get(direction, [])
        if lanes:
            width = sum(max(lane.width, 0.0) for lane in lanes)
            if width > 0:
                return sum(lane.position * max(lane.width, 0.0) for lane in lanes) / width
        fallback = abs(float(self.width or 0.0)) / 4.0
        return fallback if direction == DIRECTION_FORWARD else -fallback


@dataclass
class LightState:
    """单条连接道路方向的信号灯状态（对应 JSON lights[].states[]）。"""

    segment_id: int = 0
    vehicle_state: str = "unknown"      # 绿灯、红灯、黄灯或未知状态。
    vehicle_raw: int = -1               # 状态编号依次表示绿灯、红转绿、红灯和绿转红。
    pedestrian_state: str = "unknown"
    pedestrian_raw: int = -1


@dataclass
class NodeLight:
    """单个红绿灯节点（对应 JSON lights[]，schema v1.2 新增）。"""

    node_id: int = 0
    position: Vec3 = field(default_factory=Vec3)
    active: bool = False
    custom: bool = False                # 自定义灯组（通常 = TM:PE 接管）
    states: List[LightState] = field(default_factory=list)

    @property
    def green_segments(self) -> List[int]:
        """当前绿灯放行方向的道路段 ID 列表。"""
        return [s.segment_id for s in self.states if s.vehicle_state == "green"]


@dataclass
class TrafficSnapshot:
    """整份交通快照（对应 JSON 根对象）。"""

    schema_version: str = ""
    city_name: str = ""
    timestamp: str = ""
    simulation_paused: bool = False
    segments: List[TrafficSegment] = field(default_factory=list)
    lights: List[NodeLight] = field(default_factory=list)  # schema v1.2 红绿灯
    stats: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path) -> "TrafficSnapshot":
        """从 JSON 文件加载交通快照。"""
        text = Path(path).read_text(encoding="utf-8")
        return cls.load_from_string(text)

    @classmethod
    def load_from_string(cls, text: str) -> "TrafficSnapshot":
        """从 JSON 字符串直接加载交通快照（用于 csmodernmap 内嵌 CDATA/文本节点）。"""
        return cls.from_dict(json.loads(text))

    @classmethod
    def from_dict(cls, data: dict) -> "TrafficSnapshot":
        """直接读取原生游戏 JSON 和 CMM 数据字典，不重新序列化。"""
        snap = cls(
            schema_version=_get(data, "schemaVersion", ""),
            city_name=_get(data, "cityName", ""),
            timestamp=_get(data, "timestamp", ""),
            simulation_paused=bool(_get(data, "simulationPaused", False)),
            stats=_get(data, "stats", {}) or {},
        )
        for seg in _get(data, "segments", []) or ():
            s = TrafficSegment(
                segment_id=int(_get(seg, "segmentId", 0)),
                road_name=_get(seg, "roadName", ""),
                item_class=_get(seg, "itemClass", ""),
                width=float(_get(seg, "width", 0.0)),
                node_a=_vec(_get(seg, "nodeA")),
                node_b=_vec(_get(seg, "nodeB")),
            )
            for cp in _get(seg, "curvePoints") or []:
                s.curve_points.append(_vec(cp))
            st = _get(seg, "segStats") or {}
            s.seg_stats = SegStats(
                total_lanes=int(_get(st, "totalLanes", 0)),
                vehicle_lanes=int(_get(st, "vehicleLanes", 0)),
                total_vehicles=int(_get(st, "totalVehicles", 0)),
                one_way=bool(_get(st, "oneWay", False)),
                highway=bool(_get(st, "highway", False)),
                has_buslane=bool(_get(st, "hasBuslane", False)),
                weighted_avg_congestion=_f(_get(st, "weightedAvgCongestion"), -1.0),
                simple_avg_congestion=_f(_get(st, "simpleAvgCongestion"), -1.0),
                weighted_avg_relative_speed=_f(
                    _get(st, "weightedAvgRelativeSpeed"), -1.0
                ),
            )
            for lane in _get(seg, "lanes", []) or ():
                s.lanes.append(
                    TrafficLane(
                        lane_index=int(_get(lane, "laneIndex", 0)),
                        lane_type=_get(lane, "laneType", ""),
                        position=_f(_get(lane, "position"), 0.0),
                        width=_f(_get(lane, "width"), 0.0),
                        speed=_f(_get(lane, "speed"), 0.0),
                        max_speed=_f(_get(lane, "maxSpeed"), 0.0),
                        relative_speed=_f(_get(lane, "relativeSpeed"), -1.0),
                        vehicle_count=int(_get(lane, "vehicleCount", 0)),
                        congestion=_f(_get(lane, "congestion"), -1.0),
                        data_quality=int(_get(lane, "dataQuality", 0)),
                        direction=_get(lane, "laneDirection", ""),
                    )
                )
            snap.segments.append(s)

        # schema v1.2：红绿灯节点（游戏本体采集，不依赖 TM:PE）
        for light in _get(data, "lights", []) or ():
            nl = NodeLight(
                node_id=int(_get(light, "nodeId", 0)),
                position=_vec(_get(light, "position")),
                active=bool(_get(light, "active", False)),
                custom=bool(_get(light, "custom", False)),
            )
            for st in _get(light, "states", []) or ():
                nl.states.append(
                    LightState(
                        segment_id=int(_get(st, "segmentId", 0)),
                        vehicle_state=_get(st, "vehicleState", "unknown"),
                        vehicle_raw=int(_get(st, "vehicleRaw", -1)),
                        pedestrian_state=_get(st, "pedestrianState", "unknown"),
                        pedestrian_raw=int(_get(st, "pedestrianRaw", -1)),
                    )
                )
            snap.lights.append(nl)
        return snap


_SNAKE_FIELDS = {
    item.name.split("_")[0] + "".join(word.capitalize() for word in item.name.split("_")[1:]): item.name
    for model in (TrafficSnapshot, TrafficSegment, TrafficLane, SegStats, NodeLight, LightState)
    for item in fields(model)
}
_SNAKE_FIELDS["laneDirection"] = "direction"


def _get(raw: dict, wire_name: str, default=None):
    return raw.get(wire_name, raw.get(_SNAKE_FIELDS.get(wire_name, wire_name), default))


def _vec(d) -> Vec3:
    if not d:
        return Vec3()
    return Vec3(
        _f(d.get("x"), 0.0), _f(d.get("y"), 0.0), _f(d.get("z"), 0.0)
    )


def _f(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def ensure_snapshot(value) -> "TrafficSnapshot | None":
    """统一类型化快照、原生驼峰字段和序列化蛇形字段。"""
    if value is None or isinstance(value, TrafficSnapshot):
        return value
    if not isinstance(value, dict):
        return None
    return TrafficSnapshot.from_dict(value)
