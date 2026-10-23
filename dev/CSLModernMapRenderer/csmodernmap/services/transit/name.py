"""在不修改源数据的前提下解析站名。"""
from __future__ import annotations

from dataclasses import replace
from math import floor, hypot
from typing import Iterable

from ...cmm import DisplayName, Station, Stop
from ...data import MapData
from ...transit import _named_station_buildings


class StationNameIndex:
    """保存单个不可变文档的局部候选网格和已解析名称。"""

    def __init__(self, mapdata):
        self._nodes = {item.id: item for item in mapdata.nodes}
        self._grid = {}
        self._names = {}
        self._cell = 90.0
        for order, (x, z, name, source) in enumerate(_named_station_buildings(mapdata)):
            key = (floor(x / self._cell), floor(z / self._cell))
            self._grid.setdefault(key, []).append((order, x, z, name, source))

    def resolve(self, node_id: int, max_distance: float = 90.0) -> tuple[str, str]:
        key = (node_id, max_distance)
        if key in self._names:
            return self._names[key]
        result = self._resolve(node_id, max_distance)
        self._names[key] = result
        return result

    def _resolve(self, node_id: int, max_distance: float) -> tuple[str, str]:
        node = self._nodes.get(node_id)
        if node is None:
            return "", "empty"
        x, z = node.pos.x, node.pos.z
        candidates = []
        for gx in range(floor((x - max_distance) / self._cell),
                        floor((x + max_distance) / self._cell) + 1):
            for gz in range(floor((z - max_distance) / self._cell),
                            floor((z + max_distance) / self._cell) + 1):
                candidates.extend(self._grid.get((gx, gz), ()))
        # 严格比较距离，使并列候选时保留首个建筑。
        candidates.sort(key=lambda item: item[0])
        best_distance, best_name, best_source = float(max_distance), "", "empty"
        for _order, cx, cz, name, source in candidates:
            distance = hypot(cx - x, cz - z)
            if distance < best_distance:
                best_distance, best_name, best_source = distance, name, source
        return best_name, best_source


def resolve_stop_names(mapdata: MapData, stops: Iterable[Stop],
                       *, max_distance: float = 90.0,
                       name_index: StationNameIndex | None = None) -> tuple[Stop, ...]:
    result: list[Stop] = []
    for stop in stops:
        if stop.key.namespace:
            name = stop.name
            if stop.name_kind == "template" and name.source != "custom":
                name = DisplayName()
            result.append(replace(stop, name=name))
            continue
        if stop.name.text.strip():
            result.append(replace(stop))
            continue
        if name_index is None:
            name_index = StationNameIndex(mapdata)
        name, source = name_index.resolve(stop.network_node_id, max_distance)
        result.append(replace(
            stop,
            name=DisplayName(name, source),
        ))
    return tuple(result)


def resolve_station_names(mapdata: MapData, stations: Iterable[Station],
                          stops: Iterable[Stop], *,
                          max_distance: float = 90.0,
                          name_index: StationNameIndex | None = None,
                          names_resolved: bool = False) -> tuple[Station, ...]:
    named_stops = {item.stop_key: item for item in (
        stops if names_resolved else resolve_stop_names(
            mapdata, stops, max_distance=max_distance, name_index=name_index))}
    result: list[Station] = []
    for station in stations:
        name = next((named_stops[key].name for key in station.member_stop_keys
                     if key in named_stops and named_stops[key].name.text), None)
        result.append(replace(station, name=name or station.name))
    return tuple(result)


class NameResolutionService:
    stops = staticmethod(resolve_stop_names)
    stations = staticmethod(resolve_station_names)
