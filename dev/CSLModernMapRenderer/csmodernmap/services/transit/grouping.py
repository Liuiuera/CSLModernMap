"""规范化并确定性分组 CMM 逻辑线路。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ...cmm import (
    DisplayName,
    JobParams,
    LineGroup,
    Route,
    Stop,
    TransitKind,
    TransitLine,
    Vec3,
    normalize_stop_kind,
    normalize_transit_kind,
    stable_group_id,
)
from ...data import MapData, Transport


@dataclass(frozen=True)
class NormalizedTransit:
    lines: tuple[TransitLine, ...]
    stops: tuple[Stop, ...]
    routes: tuple[Route, ...]
    source_lines: dict[int, Transport]
    stop_by_key: dict[str, Stop]


def _user_color(value) -> tuple[int, int, int, int] | None:
    if not value or len(value) < 3:
        return None
    # 旧 XML 使用 RGBA，纯白表示玩家未指定颜色。
    rgb = tuple(int(channel) for channel in value[-3:])
    if rgb == (255, 255, 255):
        return None
    alpha = int(value[0]) if len(value) >= 4 else 255
    return (*rgb, alpha)


def _available_kinds(transports: Iterable[Transport]) -> tuple[TransitKind, ...]:
    return tuple(dict.fromkeys(
        normalize_transit_kind(item.type)
        for item in transports
        if normalize_transit_kind(item.type) is not TransitKind.OTHER
    ))


def _stop_kind(mapdata: MapData, transport: Transport, node_id: int):
    map_node = mapdata.node(node_id)
    if map_node is not None and map_node.subsrv:
        kind = normalize_stop_kind(map_node.subsrv.replace("PublicTransport", ""))
        if kind.value != "OTHER":
            return kind
    return normalize_stop_kind(transport.type)


def normalize_transit(mapdata: MapData, params: JobParams | None = None) -> NormalizedTransit:
    """按节点和类型创建语义站点，并保留稳定路线。"""
    params = params or JobParams()
    transports = [item for item in mapdata.transports
                  if normalize_transit_kind(item.type) is not TransitKind.OTHER]
    if params.enabled_line_ids is not None:
        enabled_line_ids = set(params.enabled_line_ids)
        transports = [item for item in transports if int(item.id) in enabled_line_ids]
    enabled = set(params.normalized_enabled(_available_kinds(transports)))
    if params.enabled_types is not None or not params.view_all:
        transports = [item for item in transports if normalize_transit_kind(item.type) in enabled]

    lines: list[TransitLine] = []
    stops_by_key: dict[str, Stop] = {}
    routes: list[Route] = []
    source_lines: dict[int, Transport] = {}
    for transport in transports:
        kind = normalize_transit_kind(transport.type)
        line = TransitLine(
            id=transport.id,
            name=transport.name or "",
            kind=kind,
            user_color=_user_color(transport.color),
            source_raw=transport.type or "",
        )
        lines.append(line)
        source_lines[line.id] = transport
        stop_ids: list[int] = []
        for source_stop in transport.stops:
            map_node = mapdata.node(source_stop.node_id)
            if map_node is None:
                continue
            stop_kind = _stop_kind(mapdata, transport, source_stop.node_id)
            key = f"{int(source_stop.node_id)}:{stop_kind.value}"
            stop = stops_by_key.get(key)
            if stop is None:
                stop = Stop(
                    id=source_stop.node_id,
                    network_node_id=source_stop.node_id,
                    kind=stop_kind,
                    name=DisplayName(source_stop.name or "", "game" if source_stop.name else "empty"),
                    position=Vec3(map_node.pos.x, map_node.pos.y, map_node.pos.z),
                    source_raw=map_node.subsrv or transport.type or "",
                )
                stops_by_key[key] = stop
            if line.id not in stop.serving_line_ids:
                stop.serving_line_ids.append(line.id)
            # 路线保留源站序列，包括表示闭环的重复首尾站；语义站点身份单独去重。
            stop_ids.append(stop.id)
        if stop_ids:
            routes.append(Route(
                id=(line.id * 2 + 1),
                line_id=line.id,
                direction="outbound",
                stop_ids=stop_ids,
                is_closed=len(stop_ids) > 2 and stop_ids[0] == stop_ids[-1],
                geometry_kind="stop_direct",
                source_raw=transport.type or "",
            ))

    return NormalizedTransit(
        lines=tuple(sorted(lines, key=lambda item: item.id)),
        stops=tuple(stops_by_key.values()),
        routes=tuple(routes),
        source_lines=source_lines,
        stop_by_key=stops_by_key,
    )


def build_line_groups(mapdata: MapData, normalized: NormalizedTransit,
                      params: JobParams | None = None) -> tuple[LineGroup, ...]:
    """不调用旧渲染代码，确定性地构建 CMM 线路分组。"""
    params = params or JobParams()
    del mapdata
    lines = sorted(
        normalized.lines,
        key=lambda item: (-len(normalized.source_lines[item.id].stops), item.id),
    )

    def sequence(line: TransitLine) -> list[int]:
        values: list[int] = []
        for stop in normalized.source_lines[line.id].stops:
            value = int(stop.node_id)
            if not values or values[-1] != value:
                values.append(value)
        return values

    def cyclic_key(values: list[int]) -> tuple[int, ...]:
        core = values[:-1] if len(values) >= 2 and values[0] == values[-1] else values
        if not core:
            return ()
        rotations = []
        for base in (core, list(reversed(core))):
            rotations.extend(tuple(base[index:] + base[:index]) for index in range(len(base)))
        return min(rotations)

    def reverse_equal(left: list[int], right: list[int]) -> bool:
        if len(left) != len(right) or not left:
            return False
        reversed_right = list(reversed(right))
        return any(left[index:] + left[:index] == reversed_right
                   for index in range(len(left)))

    def terminal_diff_ok(left: list[int], right: list[int],
                         left_set: frozenset[int], right_set: frozenset[int]) -> bool:
        left_only, right_only = left_set - right_set, right_set - left_set
        if len(left_only) > 1 or len(right_only) > 1:
            return False
        return (all(left.index(value) in (0, len(left) - 1) for value in left_only)
                and all(right.index(value) in (0, len(right) - 1) for value in right_only))

    def relation(left: list[int], right: list[int]) -> str | None:
        left_loop = len(left) >= 2 and left[0] == left[-1]
        right_loop = len(right) >= 2 and right[0] == right[-1]
        left_core = left[:-1] if left_loop else left
        right_core = right[:-1] if right_loop else right
        left_set, right_set = frozenset(left_core), frozenset(right_core)
        if cyclic_key(left_core) == cyclic_key(right_core):
            if left_loop and right_loop:
                return "overlay"
            if reverse_equal(left_core, right_core):
                return "opposite"
            if left_core == right_core:
                return "overlay"
        intersection = left_set & right_set
        left_projection = [value for value in left_core if value in intersection]
        right_projection = [value for value in right_core if value in intersection]
        projection_equal = cyclic_key(left_projection) == cyclic_key(right_projection)
        if (len(left_set ^ right_set) <= 2 and len(intersection) >= 3
                and terminal_diff_ok(left_core, right_core, left_set, right_set)
                and projection_equal and reverse_equal(left_projection, right_projection)):
            return "opposite"
        minimum = min(len(left_set), len(right_set))
        threshold = float(params.grouping.share_ratio)
        overlap = len(intersection) / minimum if minimum else 0.0
        if (params.grouping.merge_partial_overlap and minimum >= 3 and projection_equal
                and (overlap >= 0.70 or (threshold > 0 and overlap >= threshold))):
            return ("opposite" if reverse_equal(left_projection, right_projection)
                    else "overlay")
        return None

    grouped: list[tuple[TransitLine, list[TransitLine], set[int]]] = []
    leftovers: list[TransitLine] = []
    if params.grouping.by_name:
        buckets: dict[tuple[TransitKind, str], list[TransitLine]] = {}
        for line in lines:
            name = line.name.strip()
            if name:
                buckets.setdefault((line.kind, name), []).append(line)
            else:
                leftovers.append(line)
        for members in buckets.values():
            if len(members) > 1:
                grouped.append((members[0], members[1:], set()))
            else:
                leftovers.extend(members)
    else:
        leftovers = list(lines)

    if params.grouping.sequence_fallback:
        pending_by_kind: dict[TransitKind, list[TransitLine]] = {}
        for line in leftovers:
            pending_by_kind.setdefault(line.kind, []).append(line)
        for kind_lines in pending_by_kind.values():
            local_groups: list[tuple[TransitLine, list[TransitLine], set[int]]] = []
            for line in kind_lines:
                match = next((item for item in local_groups
                              if relation(sequence(line), sequence(item[0])) is not None), None)
                if match is None:
                    local_groups.append((line, [], set()))
                    continue
                mode = relation(sequence(line), sequence(match[0]))
                match[1].append(line)
                if mode == "overlay":
                    match[2].add(line.id)
            grouped.extend(local_groups)
    else:
        grouped.extend((line, [], set()) for line in leftovers)

    return tuple(LineGroup(
        id=stable_group_id([primary.id, *(item.id for item in aliases)]),
        primary=primary,
        aliases=tuple(aliases),
        overlay_ids=frozenset(overlays),
    ) for primary, aliases, overlays in grouped)


def group_index(groups: Iterable[LineGroup]) -> dict[int, LineGroup]:
    return {line.id: group for group in groups for line in group.members}


class LineGroupingService:
    """为偏好对象接口的调用方提供具名服务入口。"""

    normalize = staticmethod(normalize_transit)
    build = staticmethod(build_line_groups)
