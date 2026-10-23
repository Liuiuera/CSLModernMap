"""定义可序列化的标准地图模型及其基础数据结构。

CMM 与既有 MapData 并行存在；渲染器继续使用兼容模型，本模块保存后续迁移所需的稳定事实。"""
from __future__ import annotations

import base64

import gzip
import json
import zlib
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from math import isfinite
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np


class _StringEnum(str, Enum):
    """定义在诊断和 JSON 中可读的字符串枚举。"""

    def __str__(self) -> str:
        return self.value


class NetworkKind(_StringEnum):
    ROAD = "ROAD"
    RAIL = "RAIL"
    TRAM = "TRAM"
    METRO = "METRO"
    MONORAIL = "MONORAIL"
    CABLECAR = "CABLECAR"
    BEAUTIFICATION = "BEAUTIFICATION"
    STOP_LINE = "STOP_LINE"
    OTHER = "OTHER"


class RoadLevel(_StringEnum):
    HIGHWAY = "HIGHWAY"
    MAIN = "MAIN"
    BRANCH = "BRANCH"
    SPECIAL = "SPECIAL"
    BEAUTIFICATION = "BEAUTIFICATION"


class SegmentMode(_StringEnum):
    GROUND = "GROUND"
    ELEVATED = "ELEVATED"
    TUNNEL = "TUNNEL"


class TransitKind(_StringEnum):
    BUS = "BUS"
    TROLLEYBUS = "TROLLEYBUS"
    TRAM = "TRAM"
    METRO = "METRO"
    TRAIN = "TRAIN"
    MONORAIL = "MONORAIL"
    CABLECAR = "CABLECAR"
    SHIP = "SHIP"
    FERRY = "FERRY"
    AIRPLANE = "AIRPLANE"
    HELICOPTER = "HELICOPTER"
    OTHER = "OTHER"


class StopKind(_StringEnum):
    BUS = "BUS"
    TROLLEYBUS = "TROLLEYBUS"
    TRAM = "TRAM"
    METRO = "METRO"
    TRAIN = "TRAIN"
    MONORAIL = "MONORAIL"
    CABLECAR = "CABLECAR"
    SHIP = "SHIP"
    FERRY = "FERRY"
    AIRPLANE = "AIRPLANE"
    HELICOPTER = "HELICOPTER"
    OTHER = "OTHER"


class BuildingRole(_StringEnum):
    RESIDENTIAL = "RESIDENTIAL"
    COMMERCIAL = "COMMERCIAL"
    INDUSTRIAL = "INDUSTRIAL"
    OFFICE = "OFFICE"
    EDUCATION = "EDUCATION"
    HEALTHCARE = "HEALTHCARE"
    TRANSIT_STATION = "TRANSIT_STATION"
    AIRPORT = "AIRPORT"
    LEISURE = "LEISURE"
    PARK_SERVICE = "PARK_SERVICE"
    EMERGENCY = "EMERGENCY"
    INFRASTRUCTURE = "INFRASTRUCTURE"
    OTHER = "OTHER"


class ZoneKind(_StringEnum):
    ORDINARY = "ORDINARY"
    RESIDENTIAL = "RESIDENTIAL"
    COMMERCIAL = "COMMERCIAL"
    INDUSTRIAL = "INDUSTRIAL"
    OFFICE = "OFFICE"
    FARMING = "FARMING"
    FORESTRY = "FORESTRY"
    ORE = "ORE"
    OIL = "OIL"
    HIGH_DENSITY = "HIGH_DENSITY"
    PARK = "PARK"
    CAMPUS = "CAMPUS"
    AIRPORT = "AIRPORT"
    INDUSTRY = "INDUSTRY"
    ZOO = "ZOO"
    AMUSEMENT = "AMUSEMENT"
    NATURE_RESERVE = "NATURE_RESERVE"
    SHIP = "SHIP"
    CARGO_HARBOR = "CARGO_HARBOR"
    OTHER = "OTHER"


class FacilityKind(_StringEnum):
    AIRPORT = "AIRPORT"
    RAIL = "RAIL"
    PORT = "PORT"
    OTHER = "OTHER"


class SemanticLevel(_StringEnum):
    """定义建筑及派生 POI 的制图归属层级。"""

    BUILDING = "BUILDING"
    COMPOSITE_BUILDING = "COMPOSITE_BUILDING"
    FACILITY = "FACILITY"
    AREA = "AREA"


class Direction(_StringEnum):
    OUTBOUND = "outbound"
    INBOUND = "inbound"
    LOOP = "loop"


class GeomKind(_StringEnum):
    PATH_CHAIN = "path_chain"
    TRACK_BFS = "track_bfs"
    STOP_DIRECT = "stop_direct"


class LabelKind(_StringEnum):
    TITLE = "TITLE"
    STATION_NAME = "STATION_NAME"
    LINE_NAME = "LINE_NAME"
    DISTRICT_NAME = "DISTRICT_NAME"
    PARK_NAME = "PARK_NAME"
    ROAD_NAME = "ROAD_NAME"
    BUILDING_NAME = "BUILDING_NAME"
    POI_NAME = "POI_NAME"
    LEGEND = "LEGEND"
    ANNOTATION = "ANNOTATION"


class LabelSource(_StringEnum):
    CUSTOM = "CUSTOM"
    GAME = "GAME"
    DERIVED = "DERIVED"
    POOL = "POOL"
    FALLBACK = "FALLBACK"
    CATEGORY = "CATEGORY"


def _read_document_text(path: Path) -> str:
    """按后缀选择读法：``.gz`` 走 gzip，其余按明文。

    游戏内 Mod 现在恒输出 gzip（``.cmm.gz``），但用户手上还有一批老的
    ``.cmm.json``、以及手工解压出来的 ``.cmm``，三种都要能读 —— 这是
    CMM 的**唯一读入口**，改这里所有调用方（loader / load_json / CLI）一起受益。
    """
    if path.name.casefold().endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8-sig") as stream:
            return stream.read()
    return path.read_text(encoding="utf-8")


def _enum_value(enum_type: type[Enum], raw: Any, default: Enum) -> Enum:
    if isinstance(raw, enum_type):
        return raw
    text = str(raw or "")
    for member in enum_type:
        if text.casefold() in (member.value.casefold(), member.name.casefold()):
            return member
    return default


def _q3(value: Any) -> float:
    return round(float(value), 3)


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, _Serializable):
        return value.to_dict()
    if is_dataclass(value):
        return {
            item.name: _json_value(getattr(value, item.name))
            for item in fields(value)
            if not item.name.startswith("_")
        }
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


def _dataclass_dict(value: Any) -> dict:
    return {
        item.name: _json_value(getattr(value, item.name))
        for item in fields(value)
        if not item.name.startswith("_")
    }


class _Serializable:
    def to_dict(self) -> dict:
        return _dataclass_dict(self)


@dataclass(frozen=True)
class Vec2(_Serializable):
    x: float
    z: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "x", _q3(self.x))
        object.__setattr__(self, "z", _q3(self.z))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | Sequence[Any]) -> "Vec2":
        if isinstance(value, Mapping):
            return cls(value.get("x", 0), value.get("z", value.get("y", 0)))
        return cls(value[0], value[1])


@dataclass(frozen=True)
class Vec3(_Serializable):
    x: float
    y: float
    z: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "x", _q3(self.x))
        object.__setattr__(self, "y", _q3(self.y))
        object.__setattr__(self, "z", _q3(self.z))

    def as_2d(self) -> Vec2:
        return Vec2(self.x, self.z)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | Sequence[Any]) -> "Vec3":
        if isinstance(value, Mapping):
            return cls(value.get("x", 0), value.get("y", 0), value.get("z", 0))
        return cls(value[0], value[1], value[2])


@dataclass(frozen=True)
class Polyline(_Serializable):
    points: tuple[Vec3, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "points",
            tuple(point if isinstance(point, Vec3) else Vec3.from_dict(point)
                  for point in self.points),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | Sequence[Any]) -> "Polyline":
        points = value.get("points", []) if isinstance(value, Mapping) else value
        return cls(tuple(Vec3.from_dict(point) for point in points))


@dataclass(frozen=True)
class Polygon(_Serializable):
    points: tuple[Vec2, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "points",
            tuple(point if isinstance(point, Vec2) else Vec2.from_dict(point)
                  for point in self.points),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | Sequence[Any]) -> "Polygon":
        points = value.get("points", []) if isinstance(value, Mapping) else value
        return cls(tuple(Vec2.from_dict(point) for point in points))


@dataclass(frozen=True)
class Bounds(_Serializable):
    min_x: float
    min_z: float
    max_x: float
    max_z: float

    def __post_init__(self) -> None:
        for name in ("min_x", "min_z", "max_x", "max_z"):
            object.__setattr__(self, name, _q3(getattr(self, name)))


@dataclass
class Grid(_Serializable):
    """保存行轴朝世界正 Z 方向的行优先 float32 网格；连续数组避免逐元素 Python 浮点开销。"""

    rows: int = 0
    cols: int = 0
    origin: Vec2 = field(default_factory=lambda: Vec2(0, 0))
    cell: float = 0.0
    axis: str = "rows=north;columns=east"
    # _values 保持在最后，以兼容旧版位置参数顺序。
    _values: np.ndarray | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.rows = int(self.rows)
        self.cols = int(self.cols)
        if not isinstance(self.origin, Vec2):
            self.origin = Vec2.from_dict(self.origin)
        # 网格间距须乘以网格尺寸；取整到毫米会缩小 CS1 网格并丢失第九块地。
        self.cell = float(self.cell)
        if self._values is not None:
            self._values = self._as_array(self._values)

    # 公开值访问入口。

    @property
    def values(self) -> np.ndarray | None:
        """返回二维 float32 数组的只读视图。"""
        return self._values

    @values.setter
    def values(self, raw: Any) -> None:
        self._values = self._as_array(raw) if raw is not None else None

    @property
    def size(self) -> int:
        return self.rows * self.cols

    # 内部辅助函数。

    @staticmethod
    def _as_array(raw: Any) -> np.ndarray:
        if isinstance(raw, np.ndarray):
            arr = raw.astype(np.float32, copy=False)
        else:
            arr = np.asarray(raw, dtype=np.float32)
        if arr.ndim == 1:
            return arr.reshape(1, -1)
        return arr

    # 序列化支持。

    def to_dict(self) -> dict:
        encoded = None
        if self._values is not None:
            raw = self._values.tobytes()
            encoded = base64.b64encode(zlib.compress(raw, level=9)).decode("ascii")
        return {
            "rows": self.rows,
            "cols": self.cols,
            "origin": self.origin.to_dict(),
            "cell": self.cell,
            "axis": self.axis,
            "encoding": "base64+zlib+float32-row-major" if encoded is not None else None,
            "data": encoded,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "Grid":
        value = value or {}
        encoded = value.get("data")
        values = None
        if isinstance(encoded, str) and encoded:
            raw = zlib.decompress(base64.b64decode(encoded))
            if len(raw) % 4:
                raise ValueError("invalid CMM grid payload")
            arr = np.frombuffer(raw, dtype=np.float32)
            rows = value.get("rows", 0)
            cols = value.get("cols", 0)
            if rows and cols:
                arr = arr.reshape(rows, cols)
            values = arr
        elif isinstance(encoded, (list, tuple)):
            values = np.array(encoded, dtype=np.float32).reshape(
                value.get("rows", 0) or -1, value.get("cols", 0) or -1,
            )
        return cls(
            rows=value.get("rows", 0),
            cols=value.get("cols", 0),
            origin=Vec2.from_dict(value.get("origin", [0, 0])),
            cell=value.get("cell", 0),
            _values=values,
            axis=value.get("axis", "rows=north;columns=east"),
        )

@dataclass(frozen=True)
class LaneSummary(_Serializable):
    car_lane_count: int = 0
    direction: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "car_lane_count", int(self.car_lane_count))
        object.__setattr__(self, "direction", int(self.direction))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "LaneSummary | None":
        return None if value is None else cls(
            car_lane_count=value.get("car_lane_count", 0),
            direction=value.get("direction", 0),
        )


@dataclass(frozen=True)
class DisplayName(_Serializable):
    text: str = ""
    source: str = "empty"

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", str(self.text or ""))
        object.__setattr__(self, "source", str(self.source or "empty"))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | str | None) -> "DisplayName":
        if isinstance(value, Mapping):
            return cls(value.get("text", ""), value.get("source", "empty"))
        return cls(value or "", "game" if value else "empty")


@dataclass(frozen=True)
class ObjectRef(_Serializable):
    kind: str
    id: int
    dropped: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", str(self.kind))
        object.__setattr__(self, "id", int(self.id))

    def to_dict(self) -> dict:
        result = {"kind": self.kind, "id": self.id}
        if self.dropped:
            result["dropped"] = True
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ObjectRef":
        return cls(value.get("kind", ""), value.get("id", 0), bool(value.get("dropped", False)))


@dataclass
class Origin(_Serializable):
    game: str
    exporter: str
    source_name: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Origin":
        return cls(value.get("game", ""), value.get("exporter", ""), value.get("source_name", ""))


@dataclass
class Metadata(_Serializable):
    origin: Origin
    exported_at: str = ""
    counters: dict = field(default_factory=dict)
    anonymized: bool = False
    crs: str = "world-meters-y-up; rows=north"
    source_path: str = ""

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Metadata":
        return cls(
            origin=Origin.from_dict(value.get("origin", {})),
            exported_at=value.get("exported_at", ""),
            counters=dict(value.get("counters", {})),
            anonymized=bool(value.get("anonymized", False)),
            crs=value.get("crs", "world-meters-y-up; rows=north"),
            source_path=value.get("source_path", ""),
        )


@dataclass
class MapIssue(_Serializable):
    severity: str
    code: str
    message: str
    refs: list[ObjectRef] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.severity = str(self.severity)
        self.code = str(self.code)
        self.message = str(self.message)
        self.refs = [ref if isinstance(ref, ObjectRef) else ObjectRef.from_dict(ref)
                     for ref in self.refs]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MapIssue":
        return cls(
            value.get("severity", "warn"),
            value.get("code", "UNKNOWN"),
            value.get("message", ""),
            [ObjectRef.from_dict(ref) for ref in value.get("refs", [])],
        )


@dataclass
class NetworkNode(_Serializable):
    id: int
    pos: Vec3
    mode_hint: SegmentMode = SegmentMode.GROUND
    station_service: StopKind | None = None
    source_raw: str = ""

    def __post_init__(self) -> None:
        self.id = int(self.id)
        if not isinstance(self.pos, Vec3):
            self.pos = Vec3.from_dict(self.pos)
        raw_mode = self.mode_hint
        self.mode_hint = _enum_value(SegmentMode, raw_mode, SegmentMode.GROUND)  # type: ignore[assignment]
        if self.station_service is not None:
            raw_service = self.station_service
            self.station_service = _enum_value(StopKind, raw_service, StopKind.OTHER)
            if (self.station_service is StopKind.OTHER and not self.source_raw and raw_service
                    and not isinstance(raw_service, StopKind)):
                self.source_raw = str(raw_service)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "NetworkNode":
        return cls(
            id=value.get("id", 0),
            pos=Vec3.from_dict(value.get("pos", [0, 0, 0])),
            mode_hint=value.get("mode_hint", SegmentMode.GROUND),
            station_service=value.get("station_service"),
            source_raw=value.get("source_raw", ""),
        )


@dataclass
class NetworkSegment(_Serializable):
    id: int
    kind: NetworkKind
    start_id: int
    end_id: int
    geometry: Polyline
    mode: SegmentMode
    width: float
    level: RoadLevel | None = None
    lane_summary: LaneSummary | None = None
    transit_for: TransitKind | None = None
    path_segment_ids: list[int] = field(default_factory=list)
    platform: bool = False
    name: str = ""
    display_name: str = ""
    game_name: str = ""
    source_raw: str = ""
    footprint: Polygon | None = None

    def __post_init__(self) -> None:
        self.id = int(self.id)
        self.start_id = int(self.start_id)
        self.end_id = int(self.end_id)
        raw_kind = self.kind
        self.kind = _enum_value(NetworkKind, raw_kind, NetworkKind.OTHER)
        raw_mode = self.mode
        self.mode = _enum_value(SegmentMode, raw_mode, SegmentMode.GROUND)
        if self.level is not None:
            self.level = _enum_value(RoadLevel, self.level, RoadLevel.SPECIAL)
        if self.transit_for is not None:
            raw_transit = self.transit_for
            self.transit_for = _enum_value(TransitKind, raw_transit, TransitKind.OTHER)
            if (self.transit_for is TransitKind.OTHER and not self.source_raw and raw_transit
                    and not isinstance(raw_transit, TransitKind)):
                self.source_raw = str(raw_transit)
        if not isinstance(self.geometry, Polyline):
            self.geometry = Polyline.from_dict(self.geometry)
        self.width = float(self.width)
        if self.footprint is not None and not isinstance(self.footprint, Polygon):
            self.footprint = Polygon.from_dict(self.footprint)
        self.lane_summary = (self.lane_summary if isinstance(self.lane_summary, LaneSummary)
                             else LaneSummary.from_dict(self.lane_summary))
        self.path_segment_ids = [int(item) for item in self.path_segment_ids]
        self.platform = bool(self.platform)
        if (self.kind is NetworkKind.OTHER and not self.source_raw and raw_kind
                and not isinstance(raw_kind, NetworkKind)):
            self.source_raw = str(raw_kind)

    @property
    def is_road(self) -> bool:
        return self.kind in (NetworkKind.ROAD, NetworkKind.BEAUTIFICATION)

    @property
    def is_track(self) -> bool:
        return self.kind in (
            NetworkKind.RAIL, NetworkKind.TRAM, NetworkKind.METRO,
            NetworkKind.MONORAIL, NetworkKind.CABLECAR,
        )

    @property
    def is_stop_line(self) -> bool:
        return self.kind is NetworkKind.STOP_LINE

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "NetworkSegment":
        return cls(
            id=value.get("id", 0),
            kind=value.get("kind", NetworkKind.OTHER),
            start_id=value.get("start_id", 0),
            end_id=value.get("end_id", 0),
            geometry=Polyline.from_dict(value.get("geometry", [])),
            mode=value.get("mode", SegmentMode.GROUND),
            width=value.get("width", 0),
            level=value.get("level"),
            lane_summary=LaneSummary.from_dict(value.get("lane_summary")),
            transit_for=value.get("transit_for"),
            path_segment_ids=value.get("path_segment_ids", []),
            platform=value.get("platform", False),
            name=value.get("name", ""),
            display_name=value.get("display_name", ""),
            game_name=value.get("game_name", ""),
            source_raw=value.get("source_raw", ""),
            footprint=(Polygon.from_dict(value["footprint"])
                       if value.get("footprint") is not None else None),
        )


@dataclass
class TransitLine(_Serializable):
    id: int
    name: str
    kind: TransitKind
    user_color: tuple[int, int, int, int] | None = None
    bidirectional: bool | None = None
    branch: str = ""
    source_raw: str = ""

    def __post_init__(self) -> None:
        self.id = int(self.id)
        raw_kind = self.kind
        self.kind = _enum_value(TransitKind, raw_kind, TransitKind.OTHER)
        if (self.kind is TransitKind.OTHER and not self.source_raw and raw_kind
                and not isinstance(raw_kind, TransitKind)):
            self.source_raw = str(raw_kind)
        if self.user_color is not None:
            self.user_color = tuple(int(v) for v in self.user_color)  # type: ignore[assignment]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TransitLine":
        return cls(
            id=value.get("id", 0),
            name=value.get("name", ""),
            kind=value.get("kind", TransitKind.OTHER),
            user_color=(None if value.get("user_color") is None else tuple(value["user_color"])),
            bidirectional=value.get("bidirectional"),
            branch=value.get("branch", ""),
            source_raw=value.get("source_raw", ""),
        )


@dataclass
class Stop(_Serializable):
    id: int
    network_node_id: int | None
    kind: StopKind
    name: DisplayName
    position: Vec3
    platform_hint: str = ""
    source_raw: str = ""
    # 一个站点可属于多条源线路；迁移期间此字段保持独立，不改变 MapData 的权威地位。
    serving_line_ids: list[int] = field(default_factory=list)
    serving_group_ids: list[int] = field(default_factory=list)
    station_group: dict | None = None
    name_kind: str = "unknown"
    name_entity_id: int | None = None
    _identity_space: str = field(default="", repr=False, compare=False)

    def __post_init__(self) -> None:
        self.id = int(self.id)
        if self.network_node_id is not None:
            self.network_node_id = int(self.network_node_id)
        raw_kind = self.kind
        self.kind = _enum_value(StopKind, raw_kind, StopKind.OTHER)
        if (self.kind is StopKind.OTHER and not self.source_raw and raw_kind
                and not isinstance(raw_kind, StopKind)):
            self.source_raw = str(raw_kind)
        if not isinstance(self.name, DisplayName):
            self.name = DisplayName.from_dict(self.name)
        if not isinstance(self.position, Vec3):
            self.position = Vec3.from_dict(self.position)
        self.serving_line_ids = [int(item) for item in self.serving_line_ids]
        self.serving_group_ids = [int(item) for item in self.serving_group_ids]
        if self.station_group is not None:
            self.station_group = dict(self.station_group)
            self.station_group["entity_id"] = int(self.station_group["entity_id"])
            if self.station_group.get("source") not in {"AccessRestriction", "Owner", "Parent"}:
                raise ValueError("unknown station group source")
        if self.name_kind not in {"station", "template", "unknown"}:
            raise ValueError(f"invalid stop name_kind: {self.name_kind!r}")
        if self.name_entity_id is not None:
            self.name_entity_id = int(self.name_entity_id)

    @property
    def stop_key(self) -> str:
        """返回由节点标识和类型组成的稳定语义键。"""
        from .transit_domain import StopKey

        return str(self.key)

    @property
    def key(self):
        """以 StopKey 类型视图暴露字段，同时保留旧版数字字段。"""
        from .transit_domain import StopKey

        return StopKey(self.id if self._identity_space else self.network_node_id,
                       self.kind, self._identity_space)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Stop":
        return cls(
            id=value.get("id", 0),
            network_node_id=value.get("network_node_id"),
            kind=value.get("kind", StopKind.OTHER),
            name=DisplayName.from_dict(value.get("name")),
            position=Vec3.from_dict(value.get("position", [0, 0, 0])),
            platform_hint=value.get("platform_hint", ""),
            source_raw=value.get("source_raw", ""),
            serving_line_ids=value.get("serving_line_ids", []),
            serving_group_ids=value.get("serving_group_ids", []),
            station_group=value.get("station_group"),
            name_kind=value.get("name_kind", "unknown"),
            name_entity_id=value.get("name_entity_id"),
        )


@dataclass
class RouteLeg(_Serializable):
    from_stop_index: int
    to_stop_index: int
    path_segment_ids: list[int]

    def __post_init__(self) -> None:
        if type(self.from_stop_index) is not int or type(self.to_stop_index) is not int:
            raise ValueError("route leg stop indices must be integers")
        self.path_segment_ids = [int(item) for item in self.path_segment_ids]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RouteLeg":
        return cls(value["from_stop_index"], value["to_stop_index"],
                   value["path_segment_ids"])


@dataclass
class Route(_Serializable):
    id: int
    line_id: int
    direction: Direction
    stop_ids: list[int]
    is_closed: bool
    geometry_kind: GeomKind
    path_segment_ids: list[int] = field(default_factory=list)
    source_raw: str = ""
    group_id: int | None = None
    legs: list[RouteLeg] | None = None

    def __post_init__(self) -> None:
        self.id = int(self.id)
        self.line_id = int(self.line_id)
        self.direction = _enum_value(Direction, self.direction, Direction.OUTBOUND)
        self.geometry_kind = _enum_value(GeomKind, self.geometry_kind, GeomKind.STOP_DIRECT)
        self.stop_ids = [int(item) for item in self.stop_ids]
        self.path_segment_ids = [int(item) for item in self.path_segment_ids]
        self.is_closed = bool(self.is_closed)
        if self.group_id is not None:
            self.group_id = int(self.group_id)
        if self.legs is not None:
            self.legs = [item if isinstance(item, RouteLeg) else RouteLeg.from_dict(item)
                         for item in self.legs]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Route":
        return cls(
            id=value.get("id", 0),
            line_id=value.get("line_id", 0),
            direction=_enum_value(Direction, value.get("direction"), Direction.OUTBOUND),
            stop_ids=value.get("stop_ids", []),
            is_closed=value.get("is_closed", False),
            geometry_kind=_enum_value(GeomKind, value.get("geometry_kind"), GeomKind.STOP_DIRECT),
            path_segment_ids=value.get("path_segment_ids", []),
            source_raw=value.get("source_raw", ""),
            group_id=value.get("group_id"),
            legs=value.get("legs"),
        )


@dataclass
class StationFacilityLink(_Serializable):
    kind: FacilityKind
    name: DisplayName
    building_ids: list[int]
    distance: float

    def __post_init__(self) -> None:
        self.kind = _enum_value(FacilityKind, self.kind, FacilityKind.OTHER)
        if not isinstance(self.name, DisplayName):
            self.name = DisplayName.from_dict(self.name)
        self.building_ids = [int(item) for item in self.building_ids]
        self.distance = float(self.distance)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "StationFacilityLink":
        return cls(
            kind=value.get("kind", FacilityKind.OTHER),
            name=DisplayName.from_dict(value.get("name")),
            building_ids=value.get("building_ids", []),
            distance=value.get("distance", 0),
        )


@dataclass
class Station(_Serializable):
    id: int | str
    member_stop_ids: list[int]
    position: Vec3
    name: DisplayName
    serving_line_ids: list[int]
    building_id: int | None = None
    facilities: list[StationFacilityLink] = field(default_factory=list)
    derived_from: list[str] = field(default_factory=list)
    merge_params: dict = field(default_factory=dict)
    # 交通语义站点身份另行记录，数字成员列表仍用于旧版 CMM 兼容和文档索引。
    member_stop_keys: list[str] = field(default_factory=list)
    serving_group_ids: list[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.id = str(self.id) if isinstance(self.id, str) else int(self.id)
        self.member_stop_ids = [int(item) for item in self.member_stop_ids]
        self.serving_line_ids = [int(item) for item in self.serving_line_ids]
        if not isinstance(self.position, Vec3):
            self.position = Vec3.from_dict(self.position)
        if not isinstance(self.name, DisplayName):
            self.name = DisplayName.from_dict(self.name)
        self.facilities = [item if isinstance(item, StationFacilityLink)
                           else StationFacilityLink.from_dict(item) for item in self.facilities]
        self.member_stop_keys = [str(item) for item in self.member_stop_keys]
        self.serving_group_ids = [int(item) for item in self.serving_group_ids]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Station":
        return cls(
            id=value.get("id", 0),
            member_stop_ids=value.get("member_stop_ids", []),
            position=Vec3.from_dict(value.get("position", [0, 0, 0])),
            name=DisplayName.from_dict(value.get("name")),
            serving_line_ids=value.get("serving_line_ids", []),
            building_id=value.get("building_id"),
            facilities=[StationFacilityLink.from_dict(item) for item in value.get("facilities", [])],
            derived_from=value.get("derived_from", []),
            merge_params=dict(value.get("merge_params", {})),
            member_stop_keys=value.get("member_stop_keys", []),
            serving_group_ids=value.get("serving_group_ids", []),
        )


@dataclass
class Building(_Serializable):
    id: int
    name: str
    display_name: DisplayName
    role: BuildingRole
    footprint: Polygon
    parent_id: int | None = None
    child_ids: list[int] = field(default_factory=list)
    area_id: int | None = None
    station_id: int | None = None
    build_index: int | None = None
    source_raw: str = ""
    service: str = ""
    sub_service: str = ""
    icls: str = ""
    sub_building_id: str = ""
    parent_building_id: str = ""
    geometry_source: str = ""
    category_id: str = ""
    category_name: str = ""
    category_locale: str = ""
    growth_kind: str = "unknown"
    growth_use: str = ""

    def __post_init__(self) -> None:
        self.id = int(self.id)
        raw_role = self.role
        self.role = _enum_value(BuildingRole, raw_role, BuildingRole.OTHER)
        if (self.role is BuildingRole.OTHER and not self.source_raw and raw_role
                and not isinstance(raw_role, BuildingRole)):
            self.source_raw = str(raw_role)
        if not isinstance(self.display_name, DisplayName):
            self.display_name = DisplayName.from_dict(self.display_name)
        if not isinstance(self.footprint, Polygon):
            self.footprint = Polygon.from_dict(self.footprint)
        self.parent_id = None if self.parent_id is None else int(self.parent_id)
        self.area_id = None if self.area_id is None else int(self.area_id)
        self.station_id = None if self.station_id is None else int(self.station_id)
        self.child_ids = [int(item) for item in self.child_ids]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Building":
        for key in ("category_id", "category_name", "category_locale", "growth_kind", "growth_use"):
            if key in value and not isinstance(value[key], str):
                raise ValueError(f"building {value.get('id')}: {key} must be a string")
        return cls(
            id=value.get("id", 0),
            name=value.get("name", ""),
            display_name=DisplayName.from_dict(value.get("display_name")),
            role=value.get("role", BuildingRole.OTHER),
            footprint=Polygon.from_dict(value.get("footprint", [])),
            parent_id=value.get("parent_id"),
            child_ids=value.get("child_ids", []),
            area_id=value.get("area_id"),
            station_id=value.get("station_id"),
            build_index=value.get("build_index"),
            source_raw=value.get("source_raw", ""),
            service=value.get("service", ""),
            sub_service=value.get("sub_service", ""),
            icls=value.get("icls", ""),
            sub_building_id=value.get("sub_building_id", ""),
            parent_building_id=value.get("parent_building_id", ""),
            geometry_source=value.get("geometry_source", ""),
            category_id=value.get("category_id", ""),
            category_name=value.get("category_name", ""),
            category_locale=value.get("category_locale", ""),
            growth_kind=value.get("growth_kind", "unknown"),
            growth_use=value.get("growth_use", ""),
        )


@dataclass(init=False)
class POI(_Serializable):
    """定义兼容两代字段名称的可序列化 POI，并分离语义类型和最终图标键。"""

    id: int | str
    category: str
    name: DisplayName
    anchor: Vec3
    primary_ref: ObjectRef
    priority: int
    draw_name: bool
    name_pool_key: str
    dedupe_group: str
    icon_key: str
    min_zoom: float
    derived_from: list[str]
    kind: str
    pool_key: str
    semantic_level: str
    group_id: str
    parent_poi_id: str
    area_id: int | None
    source_raw: str
    serving_line_ids: list[int]
    growth_kind: str
    growth_use: str

    def __init__(
        self,
        id: int | str,
        category: str = "",
        name: DisplayName | Mapping[str, Any] | str | None = None,
        anchor: Vec3 | Mapping[str, Any] | Sequence[Any] | None = None,
        primary_ref: ObjectRef | Mapping[str, Any] | None = None,
        priority: int = 0,
        draw_name: bool = True,
        name_pool_key: str = "",
        dedupe_group: str = "",
        *,
        kind: str | None = None,
        source: ObjectRef | Mapping[str, Any] | None = None,
        icon_key: str = "",
        min_zoom: float = 0.8,
        pool_key: str | None = None,
        derived_from: Sequence[str] | None = None,
        semantic_level: str | SemanticLevel = SemanticLevel.BUILDING,
        group_id: str = "",
        parent_poi_id: str = "",
        area_id: int | None = None,
        source_raw: str = "",
        serving_line_ids: Sequence[int] = (),
        growth_kind: str = "unknown",
        growth_use: str = "",
    ) -> None:
        self.id = id if isinstance(id, str) else int(id)
        self.category = str(category or kind or "")
        self.kind = str(kind or self.category)
        self.category = self.category or self.kind
        self.name = name if isinstance(name, DisplayName) else DisplayName.from_dict(name)
        self.anchor = anchor if isinstance(anchor, Vec3) else Vec3.from_dict(anchor or [0, 0, 0])
        ref = source if source is not None else primary_ref
        self.primary_ref = ref if isinstance(ref, ObjectRef) else ObjectRef.from_dict(ref or {})
        self.priority = int(priority)
        self.draw_name = bool(draw_name)
        self.name_pool_key = str(name_pool_key if pool_key is None else pool_key)
        self.pool_key = self.name_pool_key
        self.dedupe_group = str(dedupe_group or "")
        self.icon_key = str(icon_key or self.category)
        self.min_zoom = float(min_zoom)
        self.derived_from = [str(item) for item in (derived_from or ())]
        self.semantic_level = str(getattr(semantic_level, "value", semantic_level)
                                  or SemanticLevel.BUILDING.value)
        self.group_id = str(group_id or "")
        self.parent_poi_id = str(parent_poi_id or "")
        self.area_id = None if area_id is None else int(area_id)
        self.source_raw = str(source_raw or "")
        self.serving_line_ids = sorted({int(value) for value in serving_line_ids})
        self.growth_kind = growth_kind
        self.growth_use = growth_use

    @property
    def source(self) -> ObjectRef:
        """提供 CMM primary_ref 字段的新版名称。"""
        return self.primary_ref

    def to_dict(self) -> dict:
        result = _dataclass_dict(self)
        result["source"] = self.primary_ref.to_dict()
        result["pool_key"] = self.pool_key
        result["kind"] = self.kind
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "POI":
        return cls(
            id=value.get("id", 0),
            category=value.get("category", value.get("kind", "")),
            kind=value.get("kind"),
            name=DisplayName.from_dict(value.get("name")),
            anchor=Vec3.from_dict(value.get("anchor", [0, 0, 0])),
            primary_ref=ObjectRef.from_dict(value.get("primary_ref", value.get("source", {}))),
            source=ObjectRef.from_dict(value["source"]) if value.get("source") else None,
            priority=value.get("priority", 0),
            draw_name=value.get("draw_name", True),
            name_pool_key=value.get("name_pool_key", value.get("pool_key", "")),
            pool_key=value.get("pool_key"),
            dedupe_group=value.get("dedupe_group", ""),
            icon_key=value.get("icon_key", value.get("category", value.get("kind", ""))),
            min_zoom=value.get("min_zoom", 0.8),
            derived_from=value.get("derived_from", []),
            semantic_level=value.get("semantic_level", SemanticLevel.BUILDING.value),
            group_id=value.get("group_id", ""),
            parent_poi_id=value.get("parent_poi_id", ""),
            area_id=value.get("area_id"),
            source_raw=value.get("source_raw", ""),
            serving_line_ids=value.get("serving_line_ids", []),
            growth_kind=value.get("growth_kind", "unknown"),
            growth_use=value.get("growth_use", ""),
        )


@dataclass
class WaterBody(_Serializable):
    id: int
    polygon: Polygon
    kind: str
    depth_range: tuple[float, float]
    derived_from: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.id = int(self.id)
        if not isinstance(self.polygon, Polygon):
            self.polygon = Polygon.from_dict(self.polygon)
        self.depth_range = (float(self.depth_range[0]), float(self.depth_range[1]))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WaterBody":
        return cls(
            id=value.get("id", 0),
            polygon=Polygon.from_dict(value.get("polygon", [])),
            kind=value.get("kind", ""),
            depth_range=tuple(value.get("depth_range", [0, 0])),
            derived_from=value.get("derived_from", []),
        )


@dataclass
class District(_Serializable):
    id: int
    name: DisplayName
    kind: str
    zone_kind: ZoneKind
    grid_points: list[Vec2]
    grid_cell: float = 0.0
    boundary: Polygon | None = None
    position: Vec2 | None = None
    source_raw: str = ""
    owner_building_id: int | None = None

    def __post_init__(self) -> None:
        self.id = int(self.id)
        raw_zone = self.zone_kind
        self.zone_kind = _enum_value(ZoneKind, raw_zone, ZoneKind.OTHER)
        if (self.zone_kind is ZoneKind.OTHER and not self.source_raw and raw_zone
                and not isinstance(raw_zone, ZoneKind)):
            self.source_raw = str(raw_zone)
        if not isinstance(self.name, DisplayName):
            self.name = DisplayName.from_dict(self.name)
        self.grid_points = [point if isinstance(point, Vec2) else Vec2.from_dict(point)
                            for point in self.grid_points]
        self.grid_cell = float(self.grid_cell)
        if self.boundary is not None and not isinstance(self.boundary, Polygon):
            self.boundary = Polygon.from_dict(self.boundary)
        if self.position is not None and not isinstance(self.position, Vec2):
            self.position = Vec2.from_dict(self.position)
        self.owner_building_id = (
            None if self.owner_building_id is None else int(self.owner_building_id))

    @property
    def is_background(self) -> bool:
        """游戏默认未分配区，不参与制图和外部导出。"""
        if self.kind == "PARK_ZONE":
            return self.type_label.strip().casefold() in {"", "none", "0"}
        return self.id == 0

    @property
    def type_label(self) -> str:
        """区划/功能区类型标签（源值优先，回退 ZoneKind 枚举值）。"""
        return self.source_raw or str(
            getattr(self.zone_kind, "value", self.zone_kind))

    @property
    def grid_points_xz(self) -> list[tuple[float, float]]:
        """采样格点的 (x, z) 元组列表（栅格化消费格式）。"""
        return [(point.x, point.z) for point in self.grid_points]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "District":
        return cls(
            id=value.get("id", 0),
            name=DisplayName.from_dict(value.get("name")),
            kind=value.get("kind", "DISTRICT"),
            zone_kind=value.get("zone_kind", ZoneKind.OTHER),
            grid_points=[Vec2.from_dict(item) for item in value.get("grid_points", [])],
            grid_cell=float(value.get("grid_cell", 0)),
            boundary=(None if value.get("boundary") is None else Polygon.from_dict(value["boundary"])),
            position=(None if value.get("position") is None else Vec2.from_dict(value["position"])),
            source_raw=value.get("source_raw", ""),
            owner_building_id=value.get("owner_building_id"),
        )


@dataclass
class FacilityGroup(_Serializable):
    """返回派生的制图分组，不修改源 XML。"""

    id: str
    semantic_level: SemanticLevel
    kind: str
    name: DisplayName
    anchor: Vec3
    boundary: Polygon | None = None
    source_zone_id: int | None = None
    member_building_ids: list[int] = field(default_factory=list)
    root_building_ids: list[int] = field(default_factory=list)
    parent_group_id: str = ""
    source_zone_ids: list[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.id = str(self.id)
        self.semantic_level = _enum_value(
            SemanticLevel, self.semantic_level, SemanticLevel.BUILDING)
        self.kind = str(self.kind or "")
        if not isinstance(self.name, DisplayName):
            self.name = DisplayName.from_dict(self.name)
        if not isinstance(self.anchor, Vec3):
            self.anchor = Vec3.from_dict(self.anchor)
        if self.boundary is not None and not isinstance(self.boundary, Polygon):
            self.boundary = Polygon.from_dict(self.boundary)
        self.source_zone_id = (
            None if self.source_zone_id is None else int(self.source_zone_id))
        self.member_building_ids = [int(item) for item in self.member_building_ids]
        self.root_building_ids = [int(item) for item in self.root_building_ids]
        self.parent_group_id = str(self.parent_group_id or "")
        self.source_zone_ids = sorted(set(int(item) for item in (
            self.source_zone_ids or ([self.source_zone_id] if self.source_zone_id else []))))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FacilityGroup":
        return cls(
            id=value.get("id", ""),
            semantic_level=value.get("semantic_level", SemanticLevel.BUILDING.value),
            kind=value.get("kind", ""),
            name=DisplayName.from_dict(value.get("name")),
            anchor=Vec3.from_dict(value.get("anchor", [0, 0, 0])),
            boundary=(Polygon.from_dict(value["boundary"])
                      if value.get("boundary") else None),
            source_zone_id=value.get("source_zone_id"),
            member_building_ids=value.get("member_building_ids", []),
            root_building_ids=value.get("root_building_ids", []),
            parent_group_id=value.get("parent_group_id", ""),
            source_zone_ids=value.get("source_zone_ids", []),
        )


def stable_label_id(kind: str, ref_kind: str, ref_id: int, text: str) -> int:
    """生成确定性的源局部标注身份。"""
    token = "\x1f".join((str(kind), str(ref_kind), str(int(ref_id)), str(text)))
    value = 2166136261
    for byte in token.encode("utf-8"):
        value ^= byte
        value = (value * 16777619) & 0xFFFFFFFF
    return value & 0x3FFFFFFF


label_id = stable_label_id
derive_label_id = stable_label_id


@dataclass(frozen=True)
class LabelAnchor(_Serializable):
    kind: str
    x: float | None = None
    z: float | None = None

    def __post_init__(self) -> None:
        kind = str(self.kind or "").upper()
        if kind not in {"POINT", "LINE", "AREA"}:
            raise ValueError(f"unknown label anchor kind: {self.kind!r}")
        object.__setattr__(self, "kind", kind)
        if self.x is not None:
            object.__setattr__(self, "x", float(self.x))
        if self.z is not None:
            object.__setattr__(self, "z", float(self.z))
        if kind == "POINT" and (self.x is None or self.z is None):
            raise ValueError("POINT label anchors require x and z")
        if ((self.x is not None and not isfinite(self.x))
                or (self.z is not None and not isfinite(self.z))):
            raise ValueError("label anchor coordinates must be finite")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | str | None) -> "LabelAnchor":
        if isinstance(value, str):
            return cls(value)
        value = value or {}
        return cls(
            value.get("kind", value.get("type", "POINT")),
            value.get("x"), value.get("z", value.get("y")),
        )


@dataclass
class Label(_Serializable):
    """定义项目中唯一的标准标注实体，并通过兼容属性暴露旧版 CMM 字段。"""

    id: int
    type: str
    source: str
    text: str
    ref_kind: str
    ref_id: int
    position: LabelAnchor
    priority: int
    layer: str = "label"
    visibility: str = "AUTO"
    detail_min: int = 0
    detail_max: int = 3
    collision_group: str = "base_text"
    style_ref: str = ""
    ref_dropped: bool = False

    def __post_init__(self) -> None:
        self.id = int(self.id)
        self.type = str(getattr(self.type, "value", self.type) or "").upper()
        self.source = str(getattr(self.source, "value", self.source) or "GAME").upper()
        self.text = str(self.text or "")
        self.ref_kind = str(self.ref_kind or "")
        self.ref_id = int(self.ref_id)
        if not isinstance(self.position, LabelAnchor):
            self.position = LabelAnchor.from_dict(self.position)
        self.priority = int(self.priority)
        self.layer = str(self.layer or "label")
        self.visibility = str(getattr(self.visibility, "value", self.visibility) or "AUTO").upper()
        if self.visibility not in {"AUTO", "ON", "OFF"}:
            raise ValueError(f"unknown label visibility: {self.visibility!r}")
        self.detail_min = int(self.detail_min)
        self.detail_max = int(self.detail_max)
        if not 0 <= self.detail_min <= self.detail_max <= 3:
            raise ValueError("label detail range must be within 0..3")
        self.collision_group = str(self.collision_group or "base_text")
        self.style_ref = str(self.style_ref or "")
        self.ref_dropped = bool(self.ref_dropped)

    @property
    def kind(self) -> str:
        return self.type

    @property
    def ref(self) -> ObjectRef:
        return ObjectRef(self.ref_kind, self.ref_id, self.ref_dropped)

    @property
    def geometry_hint(self) -> str:
        return self.position.kind

    @property
    def suggested_layer(self) -> str:
        return self.layer

    @property
    def visibility_condition(self) -> str:
        return self.visibility

    @property
    def style_reference(self) -> str:
        return self.style_ref

    def source_object_id(self) -> tuple[str, int]:
        return self.ref_kind, self.ref_id

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Label":
        value = value or {}
        raw_text = value.get("text", "")
        if isinstance(raw_text, Mapping):
            text = str(raw_text.get("text", ""))
            source = value.get("source", raw_text.get("source", "GAME"))
        else:
            text = str(raw_text or "")
            source = value.get("source", "GAME")
        ref = ObjectRef.from_dict(value.get("ref", {}))
        ref_kind = value.get("ref_kind", ref.kind)
        ref_id = value.get("ref_id", ref.id)
        position = value.get("position")
        if position is None:
            hint = value.get("geometry_hint", "POINT")
            position = ({"kind": hint, "x": value.get("x", 0.0), "z": value.get("z", 0.0)}
                        if str(hint).upper() == "POINT" else {"kind": hint})
        return cls(
            id=value.get("id", stable_label_id(
                value.get("type", value.get("kind", "")), ref_kind, ref_id, text)),
            type=value.get("type", value.get("kind", "")),
            source=source, text=text, ref_kind=ref_kind, ref_id=ref_id,
            position=position, priority=value.get("priority", 0),
            layer=value.get("layer", value.get("suggested_layer", "label")),
            visibility=value.get("visibility", value.get("visibility_condition", "AUTO")),
            detail_min=value.get("detail_min", 0), detail_max=value.get("detail_max", 3),
            collision_group=value.get("collision_group", "base_text"),
            style_ref=value.get("style_ref", value.get("style_reference", "")),
            ref_dropped=value.get("ref_dropped", ref.dropped),
        )


@dataclass
class LayerDef(_Serializable):
    key: str
    title: str
    content: str
    z: int
    modes: list[str]
    default_on: bool = True

    def __post_init__(self) -> None:
        self.z = int(self.z)
        self.modes = [str(mode) for mode in self.modes]
        self.default_on = bool(self.default_on)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LayerDef":
        return cls(value.get("key", ""), value.get("title", ""), value.get("content", ""),
                   value.get("z", 0), value.get("modes", []), value.get("default_on", True))


@dataclass(frozen=True, init=False)
class RenderJob(_Serializable):
    """定义各输出入口共用的渲染作业身份；detail_mode 保留旧版 Python 名称，detail 为只读协议别名。"""

    kind: str
    layers: tuple[str, ...]
    canvas: dict
    detail_mode: str = "auto"
    derive_params: dict = field(default_factory=dict)
    style_override: dict = field(default_factory=dict)
    rule_overrides: tuple[dict[str, Any], ...] = ()

    def __init__(self, kind: str, layers: tuple[str, ...] | list[str],
                 canvas: dict, detail_mode: str = "auto",
                 derive_params: dict | None = None,
                 style_override: dict | None = None,
                 rule_overrides: tuple[dict[str, Any], ...] | list[dict[str, Any]] = (),
                 *, detail: str | None = None) -> None:
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "layers", layers)
        object.__setattr__(self, "canvas", canvas)
        object.__setattr__(self, "detail_mode", detail if detail is not None else detail_mode)
        object.__setattr__(self, "derive_params", derive_params or {})
        object.__setattr__(self, "style_override", style_override or {})
        object.__setattr__(self, "rule_overrides", rule_overrides or ())
        self.__post_init__()

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", str(self.kind or "normal").strip().lower())
        object.__setattr__(self, "layers", tuple(str(item) for item in (self.layers or ())))
        object.__setattr__(self, "canvas", dict(self.canvas or {}))
        raw_mode = getattr(self.detail_mode, "value", self.detail_mode)
        object.__setattr__(self, "detail_mode", str(raw_mode or "auto").strip().lower())
        object.__setattr__(self, "derive_params", dict(self.derive_params or {}))
        object.__setattr__(self, "style_override", dict(self.style_override or {}))
        object.__setattr__(
            self,
            "rule_overrides",
            tuple(dict(item) for item in (self.rule_overrides or ())),
        )

    @property
    def detail(self) -> str:
        """为使用 detail 简称的协议消费者提供只读别名。"""
        return self.detail_mode

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "layers": list(self.layers),
            "canvas": dict(self.canvas),
            "detail_mode": self.detail_mode,
            "detail": self.detail_mode,
            "derive_params": dict(self.derive_params),
            "style_override": dict(self.style_override),
            "rule_overrides": [dict(item) for item in self.rule_overrides],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RenderJob":
        value = value or {}
        return cls(
            kind=value.get("kind", "normal"),
            layers=tuple(value.get("layers", ())),
            canvas=value.get("canvas", {}),
            detail_mode=value.get("detail_mode", value.get("detail", "auto")),
            derive_params=value.get("derive_params", {}),
            style_override=value.get("style_override", {}),
            rule_overrides=tuple(value.get("rule_overrides", ())),
        )

    def validate(self) -> tuple[str, ...]:
        errors: list[str] = []
        if self.kind not in {"normal", "transit", "metro", "svg", "web_tile"}:
            errors.append(f"unknown render job kind: {self.kind}")
        if self.detail_mode not in {"auto", "overview", "standard", "detailed"}:
            errors.append(f"unknown detail mode: {self.detail_mode}")
        for index, override in enumerate(self.rule_overrides):
            value = str(override.get("value", "")).upper()
            if value not in {"AUTO", "ON", "OFF"}:
                errors.append(f"rule_overrides[{index}] has invalid value {value!r}")
        return tuple(errors)


@dataclass
class Terrain(_Serializable):
    height: Grid = field(default_factory=Grid)
    water_depth: Grid = field(default_factory=Grid)
    origin: Vec2 = field(default_factory=lambda: Vec2(0, 0))
    cell: float = 0.0
    rows: int = 0
    cols: int = 0
    source_subsampled: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.height, Grid):
            self.height = Grid.from_dict(self.height)
        if not isinstance(self.water_depth, Grid):
            self.water_depth = Grid.from_dict(self.water_depth)
        if not isinstance(self.origin, Vec2):
            self.origin = Vec2.from_dict(self.origin)
        self.cell = float(self.cell)
        self.rows = int(self.rows)
        self.cols = int(self.cols)
        self.source_subsampled = int(self.source_subsampled)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "Terrain":
        value = value or {}
        rows = value.get("rows", 0)
        cols = value.get("cols", 0)
        origin = Vec2.from_dict(value.get("origin", [0, 0]))
        cell = value.get("cell", 0)
        height_value = value.get("height")
        water_value = value.get("water_depth")
        if isinstance(height_value, str):
            height_value = {"rows": rows, "cols": cols, "origin": origin.to_dict(), "cell": cell,
                            "data": height_value}
        if isinstance(water_value, str):
            water_value = {"rows": rows, "cols": cols, "origin": origin.to_dict(), "cell": cell,
                           "data": water_value}
        return cls(
            height=Grid.from_dict(height_value) if isinstance(height_value, Mapping) else Grid(),
            water_depth=Grid.from_dict(water_value) if isinstance(water_value, Mapping) else Grid(),
            origin=origin,
            cell=cell,
            rows=rows,
            cols=cols,
            source_subsampled=value.get("source_subsampled", 1),
        )


@dataclass
class Forest(_Serializable):
    """保存游戏森林区域的树木坐标，以连续整数列表中的 X/Z 成对数据编码。"""
    data: list[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.data = [int(v) for v in self.data]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "Forest":
        value = value or {}
        return cls(data=list(value.get("data", [])))


@dataclass
class ProceduralObject(_Serializable):
    """保存程序化对象的位置、有向边界框、旋转、比例、资源引用及可选玩家颜色。"""
    id: int
    pos: Vec3
    center_x: float
    center_z: float
    extent_x: float
    extent_z: float
    rotation_y: float
    scale: float
    prefab_name: str
    source_type: str
    color_rgb: tuple[int, int, int] | None = None

    def __post_init__(self) -> None:
        self.id = int(self.id)
        if not isinstance(self.pos, Vec3):
            self.pos = Vec3.from_dict(self.pos)
        self.center_x = float(self.center_x)
        self.center_z = float(self.center_z)
        self.extent_x = max(float(self.extent_x), 0.1)
        self.extent_z = max(float(self.extent_z), 0.1)
        self.rotation_y = float(self.rotation_y)
        self.scale = max(float(self.scale), 0.01)
        if self.color_rgb is not None:
            self.color_rgb = tuple(int(v) for v in self.color_rgb)  # type: ignore[assignment]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ProceduralObject":
        value = value or {}
        color = value.get("color_rgb")
        return cls(
            id=value.get("id", 0),
            pos=Vec3.from_dict(value.get("pos", [0, 0, 0])),
            center_x=value.get("center_x", 0),
            center_z=value.get("center_z", 0),
            extent_x=value.get("extent_x", 0.5),
            extent_z=value.get("extent_z", 0.5),
            rotation_y=value.get("rotation_y", 0),
            scale=value.get("scale", 1.0),
            prefab_name=value.get("prefab_name", ""),
            source_type=value.get("source_type", ""),
            color_rgb=(tuple(int(v) for v in color) if color else None),
        )


@dataclass
class DerivedCatalog(_Serializable):
    stations: list[Station] = field(default_factory=list)
    pois: list[POI] = field(default_factory=list)
    facility_groups: list[FacilityGroup] = field(default_factory=list)
    labels: list[Label] = field(default_factory=list)
    water_bodies: list[WaterBody] = field(default_factory=list)
    params: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.stations = [item if isinstance(item, Station) else Station.from_dict(item) for item in self.stations]
        self.pois = [item if isinstance(item, POI) else POI.from_dict(item) for item in self.pois]
        self.facility_groups = [item if isinstance(item, FacilityGroup)
                                else FacilityGroup.from_dict(item)
                                for item in self.facility_groups]
        self.labels = [item if isinstance(item, Label) else Label.from_dict(item) for item in self.labels]
        self.water_bodies = [item if isinstance(item, WaterBody) else WaterBody.from_dict(item)
                             for item in self.water_bodies]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "DerivedCatalog":
        value = value or {}
        return cls(
            stations=[Station.from_dict(item) for item in value.get("stations", [])],
            pois=[POI.from_dict(item) for item in value.get("pois", [])],
            facility_groups=[FacilityGroup.from_dict(item)
                             for item in value.get("facility_groups", [])],
            labels=[Label.from_dict(item) for item in value.get("labels", [])],
            water_bodies=[WaterBody.from_dict(item) for item in value.get("water_bodies", [])],
            params=dict(value.get("params", {})),
        )


class DocumentIndex:
    """构建单个 MapDocument 的只读查找视图。"""

    def __init__(self, document: "MapDocument | None" = None) -> None:
        self._maps: dict[str, dict[int, Any]] = {}
        self._routes_by_stop: dict[int, tuple[Route, ...]] = {}
        self._routes_by_line: dict[int, tuple[Route, ...]] = {}
        self._networks_by_node: dict[int, tuple[NetworkSegment, ...]] = {}
        self._pois_by_ref: dict[tuple[str, int], tuple[POI, ...]] = {}
        self._stations_by_stop: dict[int, Station] = {}
        if document is not None:
            self.rebuild(document)

    def rebuild(self, document: "MapDocument") -> None:
        self._maps = {
            "node": {item.id: item for item in document.nodes},
            "network": {item.id: item for item in document.networks},
            "building": {item.id: item for item in document.buildings},
            "stop": {item.id: item for item in document.stops},
            "line": {item.id: item for item in document.transit_lines},
            "route": {item.id: item for item in document.routes},
            "station": {item.id: item for item in document.derived.stations},
            "poi": {item.id: item for item in document.derived.pois},
        }
        networks_by_node: dict[int, list[NetworkSegment]] = {}
        for segment in document.networks:
            networks_by_node.setdefault(segment.start_id, []).append(segment)
            networks_by_node.setdefault(segment.end_id, []).append(segment)
        self._networks_by_node = {key: tuple(value) for key, value in networks_by_node.items()}
        routes_by_stop: dict[int, list[Route]] = {}
        routes_by_line: dict[int, list[Route]] = {}
        for route in document.routes:
            routes_by_line.setdefault(route.line_id, []).append(route)
            for stop_id in route.stop_ids:
                routes_by_stop.setdefault(stop_id, []).append(route)
        self._routes_by_stop = {key: tuple(value) for key, value in routes_by_stop.items()}
        self._routes_by_line = {key: tuple(value) for key, value in routes_by_line.items()}
        pois_by_ref: dict[tuple[str, int], list[POI]] = {}
        for poi in document.derived.pois:
            pois_by_ref.setdefault((poi.primary_ref.kind, poi.primary_ref.id), []).append(poi)
        self._pois_by_ref = {key: tuple(value) for key, value in pois_by_ref.items()}
        self._stations_by_stop = {
            stop_id: station
            for station in document.derived.stations
            for stop_id in station.member_stop_ids
        }

    def get(self, kind: str, object_id: int | str) -> Any | None:
        mapping = self._maps.get(kind, {})
        try:
            key: int | str = int(object_id)
        except (TypeError, ValueError):
            key = str(object_id)
        return mapping.get(key)

    def routes_of_stop(self, stop_id: int) -> tuple[Route, ...]:
        return self._routes_by_stop.get(int(stop_id), ())

    def routes_of_line(self, line_id: int) -> tuple[Route, ...]:
        return self._routes_by_line.get(int(line_id), ())

    def networks_of_node(self, node_id: int) -> tuple[NetworkSegment, ...]:
        return self._networks_by_node.get(int(node_id), ())

    def stops_of_line(self, line_id: int) -> tuple[Stop, ...]:
        stop_ids: list[int] = []
        for route in self.routes_of_line(line_id):
            for stop_id in route.stop_ids:
                if stop_id not in stop_ids:
                    stop_ids.append(stop_id)
        return tuple(self._maps["stop"][stop_id] for stop_id in stop_ids if stop_id in self._maps["stop"])

    def pois_of(self, kind: str, object_id: int) -> tuple[POI, ...]:
        return self._pois_by_ref.get((kind, int(object_id)), ())

    def pois_of_building(self, building_id: int) -> tuple[POI, ...]:
        return self.pois_of("building", building_id)

    def station_of_stop(self, stop_id: int) -> Station | None:
        return self._stations_by_stop.get(int(stop_id))


@dataclass
class MapDocument(_Serializable):
    metadata: Metadata
    sea_level: float
    nodes: list[NetworkNode]
    networks: list[NetworkSegment]
    buildings: list[Building]
    districts: list[District]
    stops: list[Stop]
    transit_lines: list[TransitLine]
    routes: list[Route]
    terrain: Terrain
    schema_version: str = "cmm-v1"
    derived: DerivedCatalog = field(default_factory=DerivedCatalog)
    forests: list[Forest] = field(default_factory=list)
    procedural_objects: list[ProceduralObject] = field(default_factory=list)
    traffic: Any = None
    issues: list[MapIssue] = field(default_factory=list)
    # 尚未提升为独立 CMM 实体的源细节仍以可往返的 JSON 值保存，保证迁移期间无损。
    extensions: dict[str, Any] = field(default_factory=dict)
    _index: DocumentIndex = field(default_factory=DocumentIndex, init=False, repr=False, compare=False)
    _index_built: bool = field(default=False, init=False, repr=False, compare=False)
    _issue_keys: set = field(default_factory=set, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.schema_version = str(self.schema_version or "cmm-v1")
        if not isinstance(self.metadata, Metadata):
            self.metadata = Metadata.from_dict(self.metadata)
        self.sea_level = float(self.sea_level)
        self.nodes = [item if isinstance(item, NetworkNode) else NetworkNode.from_dict(item) for item in self.nodes]
        self.networks = [item if isinstance(item, NetworkSegment) else NetworkSegment.from_dict(item)
                         for item in self.networks]
        self.buildings = [item if isinstance(item, Building) else Building.from_dict(item) for item in self.buildings]
        self.districts = [item if isinstance(item, District) else District.from_dict(item) for item in self.districts]
        self.stops = [item if isinstance(item, Stop) else Stop.from_dict(item) for item in self.stops]
        self.transit_lines = [item if isinstance(item, TransitLine) else TransitLine.from_dict(item)
                              for item in self.transit_lines]
        self.routes = [item if isinstance(item, Route) else Route.from_dict(item) for item in self.routes]
        if not isinstance(self.terrain, Terrain):
            self.terrain = Terrain.from_dict(self.terrain)
        if not isinstance(self.derived, DerivedCatalog):
            self.derived = DerivedCatalog.from_dict(self.derived)
        self.forests = [item if isinstance(item, Forest) else Forest.from_dict(item) for item in self.forests]
        self.procedural_objects = [
            item if isinstance(item, ProceduralObject) else ProceduralObject.from_dict(item)
            for item in self.procedural_objects
        ]
        self.issues = [item if isinstance(item, MapIssue) else MapIssue.from_dict(item) for item in self.issues]
        self._issue_keys = {self._issue_key(item) for item in self.issues}
        self.extensions = dict(self.extensions or {})

    def rebuild_index(self) -> DocumentIndex:
        self._index.rebuild(self)
        self._index_built = True
        return self._index

    @property
    def index(self) -> DocumentIndex:
        if not self._index_built:
            self.rebuild_index()
        return self._index

    def node(self, object_id: int) -> NetworkNode | None:
        return self.index.get("node", object_id)

    def network(self, object_id: int) -> NetworkSegment | None:
        return self.index.get("network", object_id)

    def segment(self, object_id: int) -> NetworkSegment | None:
        return self.network(object_id)

    def building(self, object_id: int) -> Building | None:
        return self.index.get("building", object_id)

    def stop(self, object_id: int) -> Stop | None:
        return self.index.get("stop", object_id)

    def line(self, object_id: int) -> TransitLine | None:
        return self.index.get("line", object_id)

    def route(self, object_id: int) -> Route | None:
        return self.index.get("route", object_id)

    def routes_of_stop(self, stop_id: int) -> tuple[Route, ...]:
        return self.index.routes_of_stop(stop_id)

    def routes_of_line(self, line_id: int) -> tuple[Route, ...]:
        return self.index.routes_of_line(line_id)

    def networks_of_node(self, node_id: int) -> tuple[NetworkSegment, ...]:
        return self.index.networks_of_node(node_id)

    def stops_of_line(self, line_id: int) -> tuple[Stop, ...]:
        return self.index.stops_of_line(line_id)

    def pois_of(self, kind: str, object_id: int) -> tuple[POI, ...]:
        return self.index.pois_of(kind, object_id)

    def pois_of_building(self, building_id: int) -> tuple[POI, ...]:
        return self.index.pois_of_building(building_id)

    def station_of_stop(self, stop_id: int) -> Station | None:
        return self.index.station_of_stop(stop_id)

    @property
    def anonymized(self) -> bool:
        return bool(self.metadata.anonymized)

    @property
    def city_name(self) -> str:
        """兼容界面和导出标题使用的旧版元数据名称。"""
        return self.metadata.origin.source_name

    @property
    def generated(self) -> str:
        return self.metadata.exported_at

    # 行政区与功能区便捷访问入口。

    @property
    def administrative_districts(self) -> list["District"]:
        """行政区划（不含 PARK_ZONE 功能区）。"""
        return [d for d in self.districts if d.kind != "PARK_ZONE" and not d.is_background]

    @property
    def park_zones(self) -> list["District"]:
        """功能区（公园区/产业区等，kind == PARK_ZONE）。"""
        return [d for d in self.districts if d.kind == "PARK_ZONE" and not d.is_background]

    @property
    def district_cell(self) -> float:
        """行政区划网格尺寸（米）：导出 counters 优先，区划自带值覆盖。"""
        cell = float(self.metadata.counters.get("district_cell", 0) or 0)
        for district in self.districts:
            if district.grid_cell and district.kind != "PARK_ZONE":
                cell = float(district.grid_cell)
        return cell

    @property
    def park_cell(self) -> float:
        """功能区网格尺寸（米）：导出 counters 优先，区划自带值覆盖。"""
        cell = float(self.metadata.counters.get("park_cell", 0) or 0)
        for district in self.districts:
            if district.grid_cell and district.kind == "PARK_ZONE":
                cell = float(district.grid_cell)
        return cell

    def anonymize(self, **kwargs) -> "MapDocument":
        """返回可渲染的匿名副本，具体规则由匿名化模块实现。"""
        from .anonymize import anonymize

        return anonymize(self, **kwargs)

    @staticmethod
    def _issue_key(issue: MapIssue) -> tuple:
        return (
            issue.severity,
            issue.code,
            issue.message,
            tuple((ref.kind, ref.id) for ref in issue.refs),
        )

    def _append_issue(self, issue: MapIssue) -> None:
        key = self._issue_key(issue)
        if key not in self._issue_keys:
            self.issues.append(issue)
            self._issue_keys.add(key)

    def validate(self) -> list[MapIssue]:
        """校验核心引用并返回完整问题列表；可选引用缺失不构成错误。"""
        if self.schema_version != "cmm-v1":
            self._append_issue(MapIssue("error", "SCHEMA_VERSION", f"unsupported schema: {self.schema_version}"))

        collections = (
            ("node", self.nodes), ("network", self.networks), ("building", self.buildings),
            ("district", self.districts), ("stop", self.stops), ("line", self.transit_lines),
            ("route", self.routes),
        )
        for kind, items in collections:
            seen: set[int] = set()
            for item in items:
                if item.id in seen:
                    self._append_issue(MapIssue("error", "DUPLICATE_ID", f"duplicate {kind} id {item.id}",
                                                [ObjectRef(kind, item.id)]))
                seen.add(item.id)

        for segment in self.networks:
            if self.node(segment.start_id) is None:
                self._append_issue(MapIssue("error", "DANGLING_REF", f"network {segment.id} start node is missing",
                                            [ObjectRef("network", segment.id), ObjectRef("node", segment.start_id)]))
            if self.node(segment.end_id) is None:
                self._append_issue(MapIssue("error", "DANGLING_REF", f"network {segment.id} end node is missing",
                                            [ObjectRef("network", segment.id), ObjectRef("node", segment.end_id)]))
            if segment.path_segment_ids and not segment.is_stop_line:
                self._append_issue(MapIssue("error", "PATH_KIND", f"network {segment.id} has a path outside STOP_LINE",
                                            [ObjectRef("network", segment.id)]))
            if segment.is_road and segment.level is None:
                self._append_issue(MapIssue("error", "MISSING_LEVEL", f"road {segment.id} has no road level",
                                            [ObjectRef("network", segment.id)]))
            if segment.is_stop_line and segment.transit_for is None:
                self._append_issue(MapIssue("error", "MISSING_TRANSIT_KIND", f"stop line {segment.id} has no transit kind",
                                            [ObjectRef("network", segment.id)]))
            if not segment.geometry.points:
                self._append_issue(MapIssue("warn", "GEOMETRY_EMPTY", f"network {segment.id} has empty geometry",
                                            [ObjectRef("network", segment.id)]))
            for path_id in segment.path_segment_ids:
                path_segment = self.network(path_id)
                if path_segment is None:
                    self._append_issue(MapIssue("error", "DANGLING_REF", f"network path segment {path_id} is missing",
                                                [ObjectRef("network", segment.id), ObjectRef("network", path_id)]))
                elif not (path_segment.is_track or (
                    segment.transit_for in {TransitKind.BUS, TransitKind.TROLLEYBUS}
                    and path_segment.kind is NetworkKind.ROAD
                    and len(path_segment.geometry.points) >= 2
                ) or (
                    path_segment.is_stop_line
                    and path_segment.transit_for is not None
                    and path_segment.transit_for == segment.transit_for
                    and not path_segment.path_segment_ids
                    and len(path_segment.geometry.points) >= 2
                )):
                    # 原生线路几何可直接是 STOP_LINE 叶节点；拒绝嵌套载体、环、缺失几何和跨交通模式叶节点。
                    self._append_issue(MapIssue("error", "PATH_NOT_TRACK", f"network path segment {path_id} is neither a supported road/track nor a matching route leaf",
                                                [ObjectRef("network", segment.id), ObjectRef("network", path_id)]))

        self.validate_transit()

        enum_fields = (
            ((item.kind, item.source_raw), "UNKNOWN_KIND", "network", item.id)
            for item in self.networks
        )
        enum_fields = list(enum_fields) + [
            ((item.transit_for, item.source_raw), "UNKNOWN_KIND", "network", item.id)
            for item in self.networks
            if item.transit_for is TransitKind.OTHER and item.source_raw
        ] + [
            ((item.station_service, item.source_raw), "UNKNOWN_KIND", "node", item.id)
            for item in self.nodes if item.station_service is StopKind.OTHER and item.source_raw
        ] + [
            ((item.kind, item.source_raw), "UNKNOWN_KIND", "line", item.id)
            for item in self.transit_lines if item.kind is TransitKind.OTHER and item.source_raw
        ] + [
            ((item.kind, item.source_raw), "UNKNOWN_KIND", "stop", item.id)
            for item in self.stops if item.kind is StopKind.OTHER and item.source_raw
        ] + [
            ((item.role, item.source_raw), "UNKNOWN_ROLE", "building", item.id)
            for item in self.buildings if item.role is BuildingRole.OTHER and item.source_raw
        ] + [
            ((item.zone_kind, item.source_raw), "UNKNOWN_ZONE", "district", item.id)
            for item in self.districts if item.zone_kind is ZoneKind.OTHER and item.source_raw
        ]
        for (enum_value, raw), code, kind, object_id in enum_fields:
            if enum_value is NetworkKind.OTHER or enum_value is TransitKind.OTHER \
                    or enum_value is StopKind.OTHER or enum_value is BuildingRole.OTHER \
                    or enum_value is ZoneKind.OTHER:
                self._append_issue(MapIssue("warn", code, f"unknown value {raw!r} mapped to OTHER",
                                            [ObjectRef(kind, object_id)]))

        return list(self.issues)

    def validate_transit(self) -> list[MapIssue]:
        for stop in self.stops:
            if stop.network_node_id is None and self.metadata.origin.game.casefold() == "cs2":
                continue
            if self.node(stop.network_node_id) is None:
                self._append_issue(MapIssue("error", "DANGLING_REF", f"stop {stop.id} node is missing",
                                            [ObjectRef("stop", stop.id)]))

        for route in self.routes:
            if self.line(route.line_id) is None:
                self._append_issue(MapIssue("error", "DANGLING_REF", f"route {route.id} line is missing",
                                            [ObjectRef("route", route.id), ObjectRef("line", route.line_id)]))
            for stop_id in route.stop_ids:
                if self.stop(stop_id) is None:
                    self._append_issue(MapIssue("error", "DANGLING_REF", f"route {route.id} stop is missing",
                                                [ObjectRef("route", route.id), ObjectRef("stop", stop_id)]))
            path_ids = set(route.path_segment_ids)
            for leg in route.legs or ():
                if not (0 <= leg.from_stop_index < len(route.stop_ids)
                        and 0 <= leg.to_stop_index < len(route.stop_ids)):
                    self._append_issue(MapIssue("error", "ROUTE_LEG_INDICES",
                        f"route {route.id} leg references a missing stop visit", [ObjectRef("route", route.id)]))
                path_ids.update(leg.path_segment_ids)
            for segment_id in sorted(path_ids):
                if self.network(segment_id) is None:
                    self._append_issue(MapIssue("error", "DANGLING_REF", f"route {route.id} path segment is missing",
                                                [ObjectRef("route", route.id), ObjectRef("network", segment_id)]))

        return list(self.issues)

    def to_dict(self) -> dict:
        result = {
            "schema_version": self.schema_version,
            "metadata": self.metadata.to_dict(),
            "sea_level": self.sea_level,
            "terrain": self.terrain.to_dict(),
            "nodes": [item.to_dict() for item in self.nodes],
            "networks": [item.to_dict() for item in self.networks],
            "buildings": [item.to_dict() for item in self.buildings],
            "districts": [item.to_dict() for item in self.districts],
            "stops": [item.to_dict() for item in self.stops],
            "transit_lines": [item.to_dict() for item in self.transit_lines],
            "routes": [item.to_dict() for item in self.routes],
            "derived": self.derived.to_dict(),
            "forests": [item.to_dict() for item in self.forests],
            "procedural_objects": [item.to_dict() for item in self.procedural_objects],
            "issues": [item.to_dict() for item in self.issues],
            "extensions": self.extensions,
        }
        if self.traffic is not None:
            result["traffic"] = _json_value(self.traffic)
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MapDocument":

        return cls(
            schema_version=value.get("schema_version", "cmm-v1"),
            metadata=Metadata.from_dict(value.get("metadata", {})),
            sea_level=value.get("sea_level", 0),
            nodes=[NetworkNode.from_dict(item) for item in value.get("nodes", [])],
            networks=[NetworkSegment.from_dict(item) for item in value.get("networks", [])],
            buildings=[Building.from_dict(item) for item in value.get("buildings", [])],
            districts=[District.from_dict(item) for item in value.get("districts", [])],
            stops=[Stop.from_dict(item) for item in value.get("stops", [])],
            transit_lines=[TransitLine.from_dict(item) for item in value.get("transit_lines", [])],
            routes=[Route.from_dict(item) for item in value.get("routes", [])],
            terrain=Terrain.from_dict(value.get("terrain", {})),
            derived=DerivedCatalog.from_dict(value.get("derived", {})),
            forests=[Forest.from_dict(item) for item in value.get("forests", [])],
            procedural_objects=[ProceduralObject.from_dict(item) for item in value.get("procedural_objects", [])],
            traffic=value.get("traffic"),
            issues=[MapIssue.from_dict(item) for item in value.get("issues", [])],
            extensions=dict(value.get("extensions", {}) or {}),
        )

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, indent=indent)

    @classmethod
    def from_json(cls, value: str | Path) -> "MapDocument":
        if isinstance(value, Path):
            text = _read_document_text(value)
        elif isinstance(value, str) and not value.lstrip().startswith(("{", "[")) and Path(value).exists():
            text = _read_document_text(Path(value))
        else:
            text = value
        raw = json.loads(text)
        return cls.from_dict(raw)


def dump_json(document: MapDocument, path: str | Path, *, indent: int | None = 2) -> None:
    Path(path).write_text(document.to_json(indent=indent), encoding="utf-8")


def load_json(path: str | Path) -> MapDocument:
    return MapDocument.from_json(Path(path))
