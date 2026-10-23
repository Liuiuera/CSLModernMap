"""定义交通线路与站点领域的数据对象。

渲染器仍使用兼容的 MapData；这里保存并行生成的标准化事实和派生结果，不包含绘制回调或缓存。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Mapping, Sequence

from .model import (
    Direction,
    Route,
    Station,
    Stop,
    StopKind,
    TransitKind,
    TransitLine,
)


_KIND_ALIASES = {
    "bus": TransitKind.BUS,
    "trolleybus": TransitKind.TROLLEYBUS,
    "tram": TransitKind.TRAM,
    "metro": TransitKind.METRO,
    "train": TransitKind.TRAIN,
    "monorail": TransitKind.MONORAIL,
    "cablecar": TransitKind.CABLECAR,
    "cable car": TransitKind.CABLECAR,
    "ship": TransitKind.SHIP,
    "ferry": TransitKind.FERRY,
    "airplane": TransitKind.AIRPLANE,
    "plane": TransitKind.AIRPLANE,
    "blimp": TransitKind.AIRPLANE,
    "helicopter": TransitKind.HELICOPTER,
}


def normalize_transit_kind(value: TransitKind | StopKind | str | None) -> TransitKind:
    """把 XML 或旧版交通类型名称映射到标准 CMM 类型。"""
    if isinstance(value, TransitKind):
        return value
    if isinstance(value, StopKind):
        return TransitKind(value.value)
    text = str(value or "").strip()
    return _KIND_ALIASES.get(text.casefold(), TransitKind.OTHER)


def normalize_stop_kind(value: TransitKind | StopKind | str | None) -> StopKind:
    kind = normalize_transit_kind(value)
    return StopKind(kind.value)


def stable_group_id(line_ids: Sequence[int]) -> int:
    """根据成员线路标识生成确定性的正数分组标识。"""
    token = ",".join(str(int(item)) for item in sorted(set(line_ids)))
    return int(hashlib.sha1(token.encode("utf-8")).hexdigest()[:12], 16)


@dataclass(frozen=True, order=True)
class StopKey:
    node_id: int
    kind: StopKind
    namespace: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "node_id", int(self.node_id))
        object.__setattr__(self, "kind", normalize_stop_kind(self.kind))
        if self.namespace not in {"", "cs2-stop"}:
            raise ValueError(f"invalid stop key namespace: {self.namespace!r}")

    def __str__(self) -> str:
        display = {
            "CABLECAR": "CableCar",
            "TROLLEYBUS": "Trolleybus",
            "AIRPLANE": "Airplane",
        }.get(self.kind.value, self.kind.value.title())
        prefix = f"{self.namespace}:" if self.namespace else ""
        return f"{prefix}{self.node_id}:{display}"

    @classmethod
    def parse(cls, value: str) -> "StopKey":
        text = str(value)
        namespace = "cs2-stop" if text.startswith("cs2-stop:") else ""
        node_id, _, kind = text.removeprefix("cs2-stop:").partition(":")
        if not node_id or not kind:
            raise ValueError(f"invalid stop key: {value!r}")
        normalized = normalize_stop_kind(kind)
        if normalized is StopKind.OTHER:
            raise ValueError(f"unknown stop kind: {kind!r}")
        return cls(int(node_id), normalized, namespace)


@dataclass(frozen=True)
class LineGroup:
    id: int
    primary: TransitLine
    aliases: tuple[TransitLine, ...] = ()
    overlay_ids: frozenset[int] = frozenset()

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", int(self.id))
        object.__setattr__(self, "aliases", tuple(self.aliases))
        object.__setattr__(self, "overlay_ids", frozenset(int(item) for item in self.overlay_ids))

    @property
    def members(self) -> tuple[TransitLine, ...]:
        return (self.primary, *self.aliases)

    @property
    def member_line_ids(self) -> tuple[int, ...]:
        return tuple(item.id for item in self.members)


@dataclass(frozen=True)
class StationMergeParams:
    merge_enabled: bool = True
    merge_range: float = 48.0
    merge_bus_tram: bool = True
    merge_train_metro: bool = False
    overrides: Mapping[str, float] = field(default_factory=dict)
    blocked_pairs: tuple[tuple[str, str], ...] = ()
    forced_merges: tuple[tuple[str, ...], ...] = ()
    fix_name: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "merge_range", float(self.merge_range))
        object.__setattr__(self, "overrides", dict(self.overrides or {}))
        object.__setattr__(
            self,
            "blocked_pairs",
            tuple(tuple(sorted((str(a), str(b)))) for a, b in self.blocked_pairs),
        )
        object.__setattr__(self, "forced_merges", tuple(tuple(str(item) for item in group)
                                                           for group in self.forced_merges))

    def range_for(self, kind: TransitKind | StopKind | str) -> float:
        canonical = normalize_transit_kind(kind).value
        for key in (canonical, str(kind), canonical.casefold()):
            if key in self.overrides:
                return float(self.overrides[key])
        return self.merge_range


@dataclass(frozen=True)
class GroupingParams:
    by_name: bool = True
    sequence_fallback: bool = True
    share_ratio: float = 0.0
    merge_partial_overlap: bool = True
    share_corridors: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "share_ratio", float(self.share_ratio))


@dataclass(frozen=True)
class JobParams:
    enabled_types: tuple[TransitKind, ...] | None = None
    # None 保留全部源线路，显式元组包含空元组时表示视图级白名单。
    enabled_line_ids: tuple[int, ...] | None = None
    view_all: bool = True
    auto_color: bool = True
    palette: tuple[tuple[int, int, int], ...] = ()
    station_merge: StationMergeParams = field(default_factory=StationMergeParams)
    grouping: GroupingParams = field(default_factory=GroupingParams)
    detail: str = "auto"
    label_min_zoom: float = 0.0
    station_min_zoom: float = 0.0
    svg_lod_mask: int = 0b111

    def __post_init__(self) -> None:
        if self.enabled_types is not None:
            object.__setattr__(self, "enabled_types", tuple(normalize_transit_kind(item)
                                                            for item in self.enabled_types))
        if self.enabled_line_ids is not None:
            object.__setattr__(self, "enabled_line_ids", tuple(dict.fromkeys(
                int(item) for item in self.enabled_line_ids)))
        if not isinstance(self.station_merge, StationMergeParams):
            object.__setattr__(self, "station_merge", StationMergeParams(**dict(self.station_merge)))
        if not isinstance(self.grouping, GroupingParams):
            object.__setattr__(self, "grouping", GroupingParams(**dict(self.grouping)))
        object.__setattr__(self, "palette", tuple(tuple(int(channel) for channel in color)
                                                   for color in self.palette))
        object.__setattr__(self, "label_min_zoom", float(self.label_min_zoom))
        object.__setattr__(self, "station_min_zoom", float(self.station_min_zoom))
        object.__setattr__(self, "svg_lod_mask", int(self.svg_lod_mask))

    def normalized_enabled(self, available: Sequence[TransitKind | str] = ()) -> tuple[TransitKind, ...]:
        if self.view_all and self.enabled_types is None:
            values = [normalize_transit_kind(item) for item in available]
        elif self.enabled_types is None:
            values = []
        else:
            values = list(self.enabled_types)
        return tuple(dict.fromkeys(item for item in values if item is not TransitKind.OTHER))

    @property
    def fingerprint(self) -> str:
        payload = {
            "enabled_types": [item.value for item in self.enabled_types or ()],
            "enabled_line_ids": (None if self.enabled_line_ids is None
                                 else list(self.enabled_line_ids)),
            "view_all": self.view_all,
            "auto_color": self.auto_color,
            "palette": self.palette,
            "station_merge": self.station_merge.__dict__,
            "grouping": self.grouping.__dict__,
            "detail": self.detail,
            "label_min_zoom": self.label_min_zoom,
            "station_min_zoom": self.station_min_zoom,
            "svg_lod_mask": self.svg_lod_mask,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TransitVisibility:
    enabled_kinds: tuple[TransitKind, ...]
    detail_gate: Mapping[str, int] = field(default_factory=dict)
    label_min_zoom: float = 0.0
    station_min_zoom: float = 0.0
    svg_visibility: Mapping[str, int] = field(default_factory=dict)

    def is_enabled(self, kind: TransitKind | str, detail_level: int = 0,
                   *, zoom: float | None = None, output: str = "png") -> bool:
        canonical = normalize_transit_kind(kind)
        if canonical not in self.enabled_kinds:
            return False
        if detail_level > int(self.detail_gate.get(canonical.value, detail_level)):
            return False
        if zoom is not None:
            minimum = self.station_min_zoom if output == "station" else self.label_min_zoom
            if zoom < minimum:
                return False
        if output == "svg" and self.svg_visibility and self.svg_visibility.get(canonical.value, 0) == 0:
            return False
        return True


@dataclass(frozen=True)
class RouteView:
    route: Route

    @property
    def stop_ids(self) -> tuple[int, ...]:
        return tuple(self.route.stop_ids)

    @property
    def path_segment_ids(self) -> tuple[int, ...]:
        return tuple(self.route.path_segment_ids)

    def reverse(self) -> "RouteView":
        if self.route.legs is not None:
            raise ValueError("directed route legs require reversed source geometry")
        return RouteView(replace(
            self.route,
            stop_ids=list(reversed(self.route.stop_ids)),
            path_segment_ids=list(reversed(self.route.path_segment_ids)),
            direction=(Direction.INBOUND if self.route.direction is Direction.OUTBOUND
                       else Direction.OUTBOUND),
        ))


@dataclass
class TransitModel:
    """保存完整且确定性的 T02 派生视图。"""

    job_params: JobParams
    lines: tuple[TransitLine, ...]
    groups: tuple[LineGroup, ...]
    routes: tuple[Route, ...]
    stops: tuple[Stop, ...]
    stations: tuple[Station, ...]
    group_of: dict[int, LineGroup] = field(default_factory=dict)
    auto_colors: dict[int, tuple[int, int, int]] = field(default_factory=dict)
    visibility: TransitVisibility | None = None
    issues: tuple[str, ...] = ()
    station_fix_applied: bool = False
    physical_topologies: dict = field(default_factory=dict)
    physical_legs: dict = field(default_factory=dict)
    source_lines: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.lines = tuple(sorted(self.lines, key=lambda item: item.id))
        self.groups = tuple(sorted(self.groups, key=lambda item: item.id))
        self.routes = tuple(sorted(self.routes, key=lambda item: item.id))
        self.stops = tuple(sorted(self.stops, key=lambda item: item.stop_key))
        self.stations = tuple(sorted(self.stations, key=lambda item: str(item.id)))
        self.group_of = dict(self.group_of)
        self.auto_colors = {int(key): tuple(value) for key, value in self.auto_colors.items()}
        self.station_fix_applied = bool(self.station_fix_applied)

    @property
    def colors(self) -> dict[int, tuple[int, int, int]]:
        return dict(self.auto_colors)

    @property
    def color(self) -> dict[int, tuple[int, int, int]]:
        """提供 AutoColorOutput 的规范命名。"""
        return dict(self.auto_colors)

    def stop(self, key: StopKey | str) -> Stop | None:
        wanted = str(key)
        return next((item for item in self.stops if item.stop_key == wanted), None)

    def station_of_stop(self, key: StopKey | str) -> Station | None:
        wanted = str(key)
        return next((item for item in self.stations if wanted in item.member_stop_keys), None)

    def stations_of_group(self, group_id: int) -> tuple[Station, ...]:
        return tuple(item for item in self.stations if int(group_id) in item.serving_group_ids)

    def routes_of_stop(self, stop_id: int | StopKey | str) -> tuple[Route, ...]:
        if isinstance(stop_id, StopKey) or (isinstance(stop_id, str) and ":" in stop_id):
            wanted = StopKey.parse(str(stop_id)).node_id
        else:
            wanted = int(stop_id)
        return tuple(route for route in self.routes if wanted in route.stop_ids)

    def serving_groups(self, station: Station | str) -> tuple[int, ...]:
        if isinstance(station, Station):
            return tuple(station.serving_group_ids)
        found = next((item for item in self.stations if str(item.id) == str(station)), None)
        return tuple(found.serving_group_ids) if found else ()

    def effective_color(self, line_id: int) -> tuple[int, int, int]:
        line = next((item for item in self.lines if item.id == int(line_id)), None)
        if line and line.user_color:
            return tuple(line.user_color[:3])
        if int(line_id) in self.auto_colors:
            return self.auto_colors[int(line_id)]
        return (128, 128, 128)

    def effective_sequence(self, route: Route | RouteView) -> tuple[int, ...]:
        from ..line_topology import normalize_station_sequence

        value = route.route if isinstance(route, RouteView) else route
        physical = self.physical_topologies.get(value.line_id)
        if value.legs is not None or any(stop.key.namespace for stop in self.stops):
            legs = [leg for leg in self.physical_legs.get(value.line_id, ()) if leg.route_id == value.id]
            physical = normalize_station_sequence(
                [station for leg in legs for station in leg.stations],
                connections=[(a, b) for leg in legs for a, b in zip(leg.stations, leg.stations[1:])])
        if physical is not None:
            stations = {station.id: station for station in self.stations}
            by_key = {stop.stop_key: stop for stop in self.stops}
            return tuple(next(by_key[key].key.node_id
                              for key in stations[ident].member_stop_keys
                              if value.line_id in by_key[key].serving_line_ids)
                         for ident in (physical.simple_sequence or physical.sequence))
        topology = normalize_station_sequence(value.stop_ids)
        return tuple(topology.simple_sequence or topology.sequence)

    def is_transfer(self, station: Station | str) -> bool:
        return len(self.serving_groups(station)) >= 2
