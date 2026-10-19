"""从导出的有向轨道 Path 推导物理站链。"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math

from ...line_topology import normalize_station_sequence
from ...services.station import group_stations
from ...services.station.grouping import station_merge_policy
from .geometry import WorldPoint, track_chain_points, track_path_index
from .loop import has_stop_line_closure

RAIL_TYPES = {"Metro", "Train", "Monorail"}


@dataclass(frozen=True)
class PhysicalLeg:
    start_node: int
    end_node: int
    points: tuple[WorldPoint, ...]
    segment_ids: tuple[int, ...]
    stations: tuple[str, ...]
    fractions: tuple[float, ...]


def _projections(point, points, radius):
    """轨道多次经过同一位置时保留独立访问。"""
    hits = []
    offset = 0.0
    for a, b in zip(points, points[1:]):
        dx, dz = b.x-a.x, b.z-a.z
        length = math.hypot(dx, dz)
        if length == 0:
            continue
        fraction = max(0.0, min(1.0, ((point.x-a.x)*dx+(point.z-a.z)*dz)/(length*length)))
        distance = math.hypot(point.x-a.x-fraction*dx, point.z-a.z-fraction*dz)
        height = a.y+fraction*(b.y-a.y)
        if distance <= radius and abs(point.y-height) <= 12:
            hits.append((offset+fraction*length, distance))
        offset += length
    # 相邻轨道段可能重复投影同一站。
    visits = []
    for distance, error in sorted(hits):
        if visits and distance-visits[-1][0] <= radius*2:
            if error < visits[-1][1]:
                visits[-1] = (distance, error)
        else:
            visits.append((distance, error))
    return offset, visits


def physical_legs(mapdata, transport, station_of, stops, cache):
    nodes = [stop.node_id for stop in transport.stops]
    keyword = transport.type.casefold()+" line"
    index = track_path_index(mapdata, keyword, cache) if transport.type in RAIL_TYPES else {}
    pairs = list(zip(nodes, nodes[1:]))
    if (len(nodes) > 1 and nodes[0] != nodes[-1]
            and (any(c[1] == nodes[0] for c in index.get(nodes[-1], ()))
                 or has_stop_line_closure(mapdata, nodes, transport.type))):
        pairs.append((nodes[-1], nodes[0]))
    key_of = {stop.network_node_id: stop.stop_key for stop in stops
              if transport.id in stop.serving_line_ids and stop.stop_key in station_of}
    members = [(key, mapdata.node(node).pos) for node, key in key_of.items() if mapdata.node(node) is not None]
    legs, issues = [], []
    for start, end in pairs:
        if start not in key_of or end not in key_of:
            continue
        a, b = station_of[key_of[start]], station_of[key_of[end]]
        candidates = {tuple(c[2]) for c in index.get(start, ()) if c[1] == end}
        if not candidates:
            candidates = {tuple(reversed(c[2])) for c in index.get(end, ()) if c[1] == start}
        points, segments = (), ()
        if len(candidates) == 1:
            segments = next(iter(candidates))
            if all(mapdata.segment(sid) is not None for sid in segments):
                points = track_chain_points(mapdata, segments, mapdata.node(start).pos)
        elif len(candidates) > 1:
            issues.append(f"Line {transport.id}, leg {start}->{end}: ambiguous exported track paths")
        visits = []
        length = sum(math.hypot(v.x-u.x, v.z-u.z) for u, v in zip(points, points[1:]))
        if length:
            for key, position in members:
                station = station_of[key]
                if station in (a, b):
                    continue
                _, projections = _projections(position, points, 20)
                visits.extend((distance, station) for distance, _error in projections
                              if 20 < distance < length-20)
        elif transport.type in RAIL_TYPES:
            issues.append(f"Line {transport.id}, leg {start}->{end}: no verified exported track geometry")
        ordered = [(0.0, a), *sorted(set(visits)), (length, b)]
        station_ids, fractions = [], []
        for distance, station in ordered:
            if not station_ids or station_ids[-1] != station:
                station_ids.append(station)
                fractions.append(distance/length if length else (1.0 if station == b else 0.0))
        legs.append(PhysicalLeg(start, end, points, segments, tuple(station_ids), tuple(fractions)))
    return tuple(legs), issues


def _sequence(legs, transport, station_of, stops):
    if legs:
        return [station for leg in legs for station in leg.stations]
    keys = {stop.network_node_id: stop.stop_key for stop in stops if transport.id in stop.serving_line_ids}
    return [station_of[keys[s.node_id]] for s in transport.stops if s.node_id in keys]


def _platform_pairs(mapdata, transport, legs, sequence, stations, params):
    if transport.type not in RAIL_TYPES or not params.merge_enabled:
        return ()
    topology = normalize_station_sequence(sequence)
    core = list(topology.sequence)
    if len(core) > 1 and core[0] == core[-1]:
        core.pop()
    # 真正的单向环线没有重复折返站。
    paired_terminal = (len(transport.stops) > 1
        and transport.stops[0].node_id != transport.stops[-1].node_id
        and sequence[0] == sequence[-1])
    if topology.kind not in {"branch", "cycle"} or (len(set(core)) == len(core) and not paired_terminal):
        return ()
    by_id = {station.id: station for station in stations}
    served_nodes = {stop.node_id for stop in transport.stops}
    positions = {}
    for station in stations:
        members = [mapdata.node(node).pos for node in station.member_stop_ids
                   if node in served_nodes and mapdata.node(node) is not None]
        if members:
            positions[station.id] = WorldPoint(*(sum(getattr(p, axis) for p in members)/len(members)
                                                 for axis in ("x", "y", "z")))
    directions = {}
    for leg in legs:
        if len(leg.points) < 2 or not leg.stations:
            continue
        a = leg.points[0]
        b = next((point for point in leg.points[1:]
                  if math.hypot(point.x-a.x, point.z-a.z) >= 50), leg.points[-1])
        length = math.hypot(b.x-a.x, b.z-a.z)
        if length:
            directions.setdefault(leg.stations[0], []).append(((b.x-a.x)/length, (b.z-a.z)/length))
    unique = sorted(set(core))
    adjacent = set(topology.edges)
    candidates = {station: [] for station in unique}
    blocked = set(params.blocked_pairs)
    for i, a in enumerate(unique):
        left = by_id[a]
        for b in unique[i+1:]:
            right = by_id[b]
            if (a, b) in adjacent or not directions.get(a) or not directions.get(b):
                continue
            lp, rp = positions[a], positions[b]
            if math.hypot(lp.x-rp.x, lp.z-rp.z) > 250:
                continue
            if abs(lp.y-rp.y) > 12:
                continue
            terrain = mapdata.terrain
            water = terrain.water_array
            if water is not None:
                states = []
                for i in range(9):
                    fraction = i/8
                    x = int((lp.x+fraction*(rp.x-lp.x)-terrain.origin_x)/terrain.cell)
                    z = int((lp.z+fraction*(rp.z-lp.z)-terrain.origin_z)/terrain.cell)
                    if 0 <= z < water.shape[0] and 0 <= x < water.shape[1]:
                        states.append(bool(water[z, x] > .2))
                if len(set(states)) > 1:
                    continue
            if any(tuple(sorted((ka, kb))) in blocked for ka in left.member_stop_keys for kb in right.member_stop_keys):
                continue
            if not station_merge_policy(params, transport.type)[0]:
                continue
            if not all(u[0]*v[0]+u[1]*v[1] < -.7 for u in directions[a] for v in directions[b]):
                continue
            candidates[a].append(b)
            candidates[b].append(a)
    pairs = [(a, values[0]) for a, values in candidates.items() if len(values) == 1
             and a < values[0] and candidates[values[0]] == [a]]
    if not pairs:
        return ()
    representative = {b: a for a, b in pairs}
    quotient = normalize_station_sequence(representative.get(station, station) for station in sequence)
    if quotient.kind != "path":
        return ()
    return tuple(tuple(sorted((*by_id[a].member_stop_keys, *by_id[b].member_stop_keys))) for a, b in pairs)


def build_physical_topologies(mapdata, source_lines, stops, stations, group_of, params):
    cache = {}
    station_of = {key: station.id for station in stations for key in station.member_stop_keys}
    legs_by_line, issues, merges = {}, [], []
    for ident, transport in sorted(source_lines.items()):
        legs, leg_issues = physical_legs(mapdata, transport, station_of, stops, cache)
        legs_by_line[ident] = legs
        issues.extend(leg_issues)
        sequence = _sequence(legs, transport, station_of, stops)
        merges.extend(_platform_pairs(mapdata, transport, legs, sequence, stations, params))
    if merges:
        effective = replace(params, forced_merges=tuple(dict.fromkeys((*params.forced_merges, *merges))))
        stations = group_stations(stops, group_of, effective)
        station_of = {key: station.id for station in stations for key in station.member_stop_keys}
        for ident, transport in sorted(source_lines.items()):
            legs_by_line[ident], _ = physical_legs(mapdata, transport, station_of, stops, cache)
    topologies = {ident: normalize_station_sequence(_sequence(legs_by_line[ident], transport, station_of, stops))
                  for ident, transport in sorted(source_lines.items())}
    issues.extend(f"Line {ident}: physical branch retained ({len(topology.chains)} chains)"
                  for ident, topology in topologies.items() if topology.kind == "branch")
    return stations, topologies, legs_by_line, tuple(issues)
