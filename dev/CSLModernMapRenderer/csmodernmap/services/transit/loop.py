"""识别环线证据并生成有效站序。"""
from __future__ import annotations

import re

from ...cmm import Route, TransitKind
from ...data import MapData, Transport
from ...line_topology import normalize_station_sequence
from ...transit import TYPE_REGISTRY, type_def


_LINE_NAME = re.compile("|".join(re.escape(t.line_network) for t in TYPE_REGISTRY.values()),
                        re.IGNORECASE)


def effective_sequence(route_or_ids: Route | list[int] | tuple[int, ...]) -> tuple[int, ...]:
    ids = route_or_ids.stop_ids if isinstance(route_or_ids, Route) else tuple(route_or_ids)
    topology = normalize_station_sequence(ids)
    if topology.simple_sequence is not None:
        return tuple(topology.simple_sequence)
    return tuple(topology.sequence)


def has_stop_line_closure(mapdata: MapData, stop_node_ids, kind: TransitKind | str) -> bool:
    if len(stop_node_ids) < 2:
        return False
    first, last = stop_node_ids[0], stop_node_ids[-1]
    if first is None or last is None:
        return False
    if first == last:
        return len(stop_node_ids) > 2
    definition = type_def(kind)
    if definition is None or mapdata.node(first) is None or mapdata.node(last) is None:
        return False
    for segment in mapdata.segments:
        if segment.start_node_id == last and segment.end_node_id == first:
            match = _LINE_NAME.search(segment.name or "")
            if match and match.group().casefold() == definition.line_network:
                return True
    return False


def is_loop_route(mapdata: MapData, transport: Transport,
                  kind: TransitKind | str | None = None) -> bool:
    stops = [item.node_id for item in transport.stops]
    return has_stop_line_closure(mapdata, stops, kind or transport.type)


def loop_evidence(mapdata: MapData, route: Route, kind: TransitKind | str) -> bool:
    return bool(route.is_closed or has_stop_line_closure(mapdata, route.stop_ids, kind))


class LoopEvidenceService:
    is_loop = staticmethod(is_loop_route)
    effective_sequence = staticmethod(effective_sequence)
