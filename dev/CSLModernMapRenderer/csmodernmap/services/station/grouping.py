"""按既有距离规则对站点进行确定性分组。"""
from __future__ import annotations

import hashlib
import math
from typing import Iterable, Mapping

from ...cmm import (
    LineGroup,
    Station,
    StationMergeParams,
    Stop,
    StopKind,
    TransitKind,
    Vec3,
    normalize_transit_kind,
)


def _category(kind: StopKind | TransitKind | str) -> str:
    value = normalize_transit_kind(kind).value.casefold()
    if value in {"bus", "trolleybus", "tram"}:
        return "bus"
    if value in {"metro", "train"}:
        return value
    if value in {"ship", "ferry"}:
        return "ship"
    return value


def station_merge_policy(params: StationMergeParams, kind) -> tuple[bool, float]:
    value = params.overrides.get(_category(kind), params.overrides.get(normalize_transit_kind(kind).value))
    if isinstance(value, Mapping):
        return bool(value.get("enabled", params.merge_enabled)), max(float(value.get("distance", params.merge_range)), 1e-6)
    if value is not None:
        return params.merge_enabled, max(float(value), 1e-6)
    return params.merge_enabled, max(float(params.merge_range), 1e-6)


def _can_merge(a: Stop, b: Stop, params: StationMergeParams) -> bool:
    ak = normalize_transit_kind(a.kind)
    bk = normalize_transit_kind(b.kind)
    if ak is TransitKind.OTHER or bk is TransitKind.OTHER:
        return False
    if ak is bk:
        return True
    if params.merge_bus_tram and ak in {TransitKind.BUS, TransitKind.TROLLEYBUS, TransitKind.TRAM} \
            and bk in {TransitKind.BUS, TransitKind.TROLLEYBUS, TransitKind.TRAM}:
        return True
    if params.merge_train_metro and ak in {TransitKind.TRAIN, TransitKind.METRO} \
            and bk in {TransitKind.TRAIN, TransitKind.METRO}:
        return True
    return False


def _blocked(a: str, b: str, params: StationMergeParams) -> bool:
    pair = tuple(sorted((a, b)))
    return pair in {tuple(sorted(item)) for item in params.blocked_pairs}


def _station_id(keys: Iterable[str]) -> str:
    token = "|".join(sorted(set(keys)))
    return "ST-" + hashlib.sha1(token.encode("utf-8")).hexdigest()[:12]


def _station_from_indices(values: list[Stop], indices: Iterable[int],
                          group_of: Mapping[int, LineGroup] | None,
                          params: StationMergeParams) -> Station:
    """构建单个站点，同时保持成员和服务线路顺序稳定。"""
    member_stops = [values[index] for index in indices]
    keys = sorted(item.stop_key for item in member_stops)
    position = Vec3(
        sum(item.position.x for item in member_stops) / len(member_stops),
        sum(item.position.y for item in member_stops) / len(member_stops),
        sum(item.position.z for item in member_stops) / len(member_stops),
    )
    line_ids: list[int] = []
    for stop in member_stops:
        for line_id in stop.serving_line_ids:
            if line_id not in line_ids:
                line_ids.append(line_id)
    group_ids: list[int] = []
    for line_id in line_ids:
        group = (group_of or {}).get(line_id)
        if group is not None and group.id not in group_ids:
            group_ids.append(group.id)
    if not group_of:
        for stop in member_stops:
            for group_id in stop.serving_group_ids:
                if group_id not in group_ids:
                    group_ids.append(group_id)
    return Station(
        id=_station_id(keys),
        member_stop_ids=[item.id for item in member_stops],
        member_stop_keys=keys,
        position=position,
        name=next((item.name for item in member_stops if item.name.text), member_stops[0].name),
        serving_line_ids=line_ids,
        serving_group_ids=group_ids,
        derived_from=keys,
        merge_params={
            "merge_enabled": params.merge_enabled,
            "merge_range": params.merge_range,
            "merge_bus_tram": params.merge_bus_tram,
            "merge_train_metro": params.merge_train_metro,
            "blocked_pairs": tuple(sorted(params.blocked_pairs)),
            "forced_merges": tuple(sorted(params.forced_merges)),
        },
    )


def _group_stations_with_constraints(
    values: list[Stop], group_of: Mapping[int, LineGroup] | None,
    params: StationMergeParams,
) -> tuple[Station, ...]:
    """应用强制合并与禁止配对而不改变旧流程；强制组件作为整体，候选对象仅在全部成员配对允许时加入。"""
    index_by_key = {stop.stop_key: index for index, stop in enumerate(values)}
    parent = list(range(len(values)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left, right = find(left), find(right)
        if left == right:
            return
        if left > right:
            left, right = right, left
        parent[right] = left

    for group in params.forced_merges:
        indices = [index_by_key[key] for key in group if key in index_by_key]
        if len(indices) < 2:
            continue
        for index in indices[1:]:
            union(indices[0], index)

    components: dict[int, list[int]] = {}
    for index in range(len(values)):
        components.setdefault(find(index), []).append(index)
    # 路径压缩会改变根节点，首次合并后规范化字典并记录强制规则来源。
    normalized: dict[int, list[int]] = {}
    for indices in components.values():
        root = find(indices[0])
        normalized.setdefault(root, []).extend(indices)
    components = {root: sorted(indices) for root, indices in normalized.items()}
    component_of = {
        index: root for root, indices in components.items() for index in indices
    }

    blocked = {
        tuple(sorted((str(left), str(right))))
        for left, right in params.blocked_pairs
    }

    max_range = max([
        params.merge_range,
        *(station_merge_policy(params, stop.kind)[1] for stop in values),
        1e-6,
    ])
    grid: dict[tuple[int, int], list[int]] = {}
    for index, stop in enumerate(values):
        grid.setdefault((math.floor(stop.position.x / max_range),
                         math.floor(stop.position.z / max_range)), []).append(index)

    def has_blocked_pair(left_indices: Iterable[int], right_indices: Iterable[int]) -> bool:
        return any(
            tuple(sorted((values[left].stop_key, values[right].stop_key))) in blocked
            for left in left_indices for right in right_indices
        )

    used: set[int] = set()
    result: list[Station] = []
    ordered_roots = sorted(components, key=lambda root: components[root][0])
    for seed_root in ordered_roots:
        if seed_root in used:
            continue
        members = list(components[seed_root])
        used.add(seed_root)
        stack = list(members)
        while stack and params.merge_enabled:
            current_index = stack.pop()
            current = values[current_index]
            allow_current, range_current = station_merge_policy(params, current.kind)
            if not allow_current:
                continue
            cell_x = math.floor(current.position.x / max_range)
            cell_z = math.floor(current.position.z / max_range)
            for gx in (cell_x - 1, cell_x, cell_x + 1):
                for gz in (cell_z - 1, cell_z, cell_z + 1):
                    for other_index in grid.get((gx, gz), ()):
                        other_root = component_of[other_index]
                        if other_root in used:
                            continue
                        other = values[other_index]
                        allow_other, range_other = station_merge_policy(params, other.kind)
                        if not allow_other or not _can_merge(current, other, params):
                            continue
                        candidate = components[other_root]
                        if has_blocked_pair(members, candidate):
                            continue
                        distance_sq = ((other.position.x - current.position.x) ** 2
                                       + (other.position.z - current.position.z) ** 2)
                        if distance_sq > min(range_current, range_other) ** 2:
                            continue
                        used.add(other_root)
                        members.extend(index for index in candidate if index not in members)
                        stack.extend(candidate)
        result.append(_station_from_indices(values, members, group_of, params))
    return tuple(sorted(result, key=lambda item: str(item.id)))


def group_stations(stops: Iterable[Stop], group_of: Mapping[int, LineGroup] | None = None,
                   params: StationMergeParams | None = None) -> tuple[Station, ...]:
    """通过确定性的空间哈希扩展合并标准化站点。"""
    params = params or StationMergeParams()
    values = sorted(tuple(stops), key=lambda item: item.stop_key)
    if not values:
        return ()
    if params.forced_merges or params.blocked_pairs:
        return _group_stations_with_constraints(values, group_of, params)
    max_range = max([params.merge_range, *(station_merge_policy(params, item.kind)[1] for item in values), 1e-6])
    grid: dict[tuple[int, int], list[int]] = {}
    for index, stop in enumerate(values):
        grid.setdefault((math.floor(stop.position.x / max_range),
                         math.floor(stop.position.z / max_range)), []).append(index)
    used = [False] * len(values)
    result: list[Station] = []
    for seed_index, seed in enumerate(values):
        if used[seed_index]:
            continue
        used[seed_index] = True
        members = [seed_index]
        stack = [seed_index]
        while stack and params.merge_enabled:
            current_index = stack.pop()
            current = values[current_index]
            allow_current, range_current = station_merge_policy(params, current.kind)
            if not allow_current:
                continue
            cell_x = math.floor(current.position.x / max_range)
            cell_z = math.floor(current.position.z / max_range)
            for gx in (cell_x - 1, cell_x, cell_x + 1):
                for gz in (cell_z - 1, cell_z, cell_z + 1):
                    for other_index in grid.get((gx, gz), ()):
                        if used[other_index]:
                            continue
                        other = values[other_index]
                        allow_other, range_other = station_merge_policy(params, other.kind)
                        if not allow_other or not _can_merge(current, other, params):
                            continue
                        if _blocked(current.stop_key, other.stop_key, params):
                            continue
                        distance_sq = ((other.position.x - current.position.x) ** 2
                                       + (other.position.z - current.position.z) ** 2)
                        if distance_sq <= min(range_current, range_other) ** 2:
                            used[other_index] = True
                            members.append(other_index)
                            stack.append(other_index)
        member_stops = [values[index] for index in members]
        keys = sorted(item.stop_key for item in member_stops)
        position = Vec3(
            sum(item.position.x for item in member_stops) / len(member_stops),
            sum(item.position.y for item in member_stops) / len(member_stops),
            sum(item.position.z for item in member_stops) / len(member_stops),
        )
        line_ids: list[int] = []
        for stop in member_stops:
            for line_id in stop.serving_line_ids:
                if line_id not in line_ids:
                    line_ids.append(line_id)
        group_ids: list[int] = []
        for line_id in line_ids:
            group = (group_of or {}).get(line_id)
            if group is not None and group.id not in group_ids:
                group_ids.append(group.id)
        if not group_of:
            for stop in member_stops:
                for group_id in stop.serving_group_ids:
                    if group_id not in group_ids:
                        group_ids.append(group_id)
        result.append(Station(
            id=_station_id(keys),
            member_stop_ids=[item.id for item in member_stops],
            member_stop_keys=keys,
            position=position,
            name=next((item.name for item in member_stops if item.name.text), member_stops[0].name),
            serving_line_ids=line_ids,
            serving_group_ids=group_ids,
            derived_from=keys,
            merge_params={
                "merge_enabled": params.merge_enabled,
                "merge_range": params.merge_range,
                "merge_bus_tram": params.merge_bus_tram,
                "merge_train_metro": params.merge_train_metro,
            },
        ))
    return tuple(sorted(result, key=lambda item: str(item.id)))


def is_transfer_station(station: Station) -> bool:
    """换乘语义依据两个逻辑分组，而非两条源线路。"""
    return len(set(station.serving_group_ids)) >= 2


class StationGroupingService:
    group = staticmethod(group_stations)
    is_transfer = staticmethod(is_transfer_station)
