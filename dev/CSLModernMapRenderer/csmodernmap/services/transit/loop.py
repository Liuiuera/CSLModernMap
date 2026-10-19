"""识别环线证据并生成有效站序。"""
from __future__ import annotations

from ...cmm import Route, TransitKind, normalize_transit_kind
from ...data import MapData, Transport
from ...line_topology import normalize_station_sequence
from ...transit import type_def


def effective_sequence(route_or_ids: Route | list[int] | tuple[int, ...]) -> tuple[int, ...]:
    ids = route_or_ids.stop_ids if isinstance(route_or_ids, Route) else tuple(route_or_ids)
    topology = normalize_station_sequence(ids)
    if topology.simple_sequence is not None:
        return tuple(topology.simple_sequence)
    return tuple(topology.sequence)


def _stop_line_graph(mapdata: MapData, keyword: str) -> dict[int, set[int]]:
    graph: dict[int, set[int]] = {}
    wanted = (keyword or "").lower()
    for segment in mapdata.segments:
        if wanted not in (segment.name or "").lower():
            continue
        if segment.start_node_id == segment.end_node_id:
            continue
        graph.setdefault(segment.start_node_id, set()).add(segment.end_node_id)
        graph.setdefault(segment.end_node_id, set()).add(segment.start_node_id)
    return graph


def has_stop_line_closure(mapdata: MapData, stop_node_ids, kind: TransitKind | str) -> bool:
    definition = type_def(normalize_transit_kind(kind).value)
    if definition is None or not definition.line_network:
        return False
    stops = [int(item) for item in stop_node_ids if mapdata.node(int(item)) is not None]
    if len(stops) < 3:
        return False
    if stops[0] == stops[-1]:
        return True
    graph = _stop_line_graph(mapdata, definition.line_network)
    return stops[-1] in graph.get(stops[0], set())


def is_loop_route(mapdata: MapData, transport: Transport,
                  kind: TransitKind | str | None = None) -> bool:
    stops = [item.node_id for item in transport.stops]
    if len(stops) < 3:
        return False
    if stops[0] == stops[-1]:
        return True
    return has_stop_line_closure(mapdata, stops, kind or transport.type)


def loop_evidence(mapdata: MapData, route: Route, kind: TransitKind | str) -> bool:
    return bool(route.is_closed or has_stop_line_closure(mapdata, route.stop_ids, kind))


class LoopEvidenceService:
    is_loop = staticmethod(is_loop_route)
    effective_sequence = staticmethod(effective_sequence)
