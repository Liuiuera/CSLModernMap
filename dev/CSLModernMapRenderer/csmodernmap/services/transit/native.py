from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from ...cmm import StopKey, StationMergeParams
from ...data import Transport, TransportStop


def cs2_document(mapdata):
    document = getattr(mapdata, "_document", None)
    return document if document is not None and document.metadata.origin.game.casefold() == "cs2" else None


def source_transports(document):
    by_stop = {stop.id: stop for stop in document.stops}
    by_line = {}
    for route in sorted(document.routes, key=lambda item: item.id):
        by_line.setdefault(route.line_id, []).extend(route.stop_ids)
    result = []
    for line in document.transit_lines:
        color = line.user_color
        result.append(Transport(
            id=line.id, name=line.name,
            type=str(line.kind).title().replace("Cablecar", "CableCar"),
            color=(color[3], *color[:3]) if color else (255, 255, 255, 255),
            stops=[TransportStop(ident, by_stop[ident].name.text)
                   for ident in by_line.get(line.id, ()) if ident in by_stop],
        ))
    return result


def station_constraints(stops, params: StationMergeParams):
    from ..station.grouping import station_merge_policy

    stops = tuple(stops)
    grouped = {}
    for stop in stops:
        if stop.station_group is not None:
            grouped.setdefault((stop.station_group["entity_id"], stop.kind), []).append(stop.stop_key)
    blocked = {tuple(sorted(pair)) for pair in params.blocked_pairs}
    forced = list(params.forced_merges)
    parent = {stop.stop_key: stop.stop_key for stop in stops}

    def root(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def join(keys):
        keys = [key for key in keys if key in parent]
        if keys:
            target = root(keys[0])
            for key in keys[1:]:
                parent[root(key)] = target

    for keys in params.forced_merges:
        join(keys)

    identified = [stop for stop in stops if stop.station_group is not None]
    for index, left in enumerate(identified):
        for right in identified[index + 1:]:
            pair = tuple(sorted((left.stop_key, right.stop_key)))
            if (left.station_group["entity_id"] != right.station_group["entity_id"]
                    and left.kind == right.kind and root(left.stop_key) != root(right.stop_key)):
                blocked.add(pair)
    for (_, kind), keys in grouped.items():
        keys = tuple(sorted(keys))
        if len(keys) < 2 or not params.merge_enabled or not station_merge_policy(params, kind)[0]:
            continue
        roots = {root(key) for key in keys}

        if any(root(a) in roots and root(b) in roots
               for a, b in blocked if a in parent and b in parent):
            continue
        forced.append(keys)
        join(keys)
    return replace(params, forced_merges=tuple(forced), blocked_pairs=tuple(sorted(blocked)))


def migrate_sidecar(sidecar, stops, source_map):
    from ...services.station.sidecar import StationSidecarError

    old_keys = {key for edit in sidecar.edits for key in edit.stop_keys if not key.startswith("cs2-stop:")}
    if not old_keys:
        return sidecar
    if not source_map or not sidecar.source_map or Path(sidecar.source_map).resolve() != Path(source_map).resolve():
        raise StationSidecarError("旧二代站点编辑与源文件不匹配，请重新指定；原文件已保留", ())
    candidates = {}
    for stop in stops:
        if stop.network_node_id is not None:
            candidates.setdefault(str(StopKey(stop.network_node_id, stop.kind)), []).append(stop.stop_key)
    if any(len(candidates.get(key, ())) != 1 for key in old_keys):
        raise StationSidecarError("旧二代站点编辑对应多个或缺失站台，请重新指定；原文件已保留", ())
    return replace(sidecar, edits=tuple(replace(edit, stop_keys=tuple(
        candidates[key][0] if key in old_keys else key for key in edit.stop_keys)) for edit in sidecar.edits))
