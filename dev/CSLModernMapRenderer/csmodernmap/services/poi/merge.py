"""合并来源不同但属于同一对象的 POI。

先解析实体身份，再按语义执行邻近合并：普通地点使用占地间隙，实际交通站点使用站点半径，设施身份记录不按距离合并。显示密度筛选不得回写目录。"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ...cmm import POI, Vec3
from .ownership import poi_group_for

# 这些配对来自真实 CS2 CMM 导出；只依据源预制件模板识别弱名称，未知模板仍保持强名称。
VERIFIED_GENERIC_GAME_STOP_LABELS = frozenset({
    ("Integrated Passenger Ship Stop", "综合客船站"),
    ("Integrated Cargo Ship Stop", "综合货船站"),
    ("Integrated Passenger Train Stop", "综合客运火车站"),
    ("Integrated Cargo Train Stop", "综合货运火车站"),
    ("Integrated Taxi Stand", "综合出租车站"),
    ("NA_BusStop02", "北美公交站顶棚"),
    ("Integrated Subway Stop - Middle", "综合地铁站 - 中央"),
    ("Integrated Subway Stop - Side", "综合地铁站 - 侧边"),
    ("Integrated Bus Stop", "综合公交站"),
    ("Integrated Cargo Airplane Stop", "综合货机航站楼"),
    ("Integrated Passenger Airplane Stop", "客运航站楼"),
})

_NAME_RANK = {"custom": 3, "neighbor": 2, "station": 2, "fallback": 0}


@dataclass(frozen=True)
class MergeProfile:
    view: str = "normal"
    merge_enabled: bool = True
    groups: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    allow_different_station_names: bool = False
    # 旧别名仍可读取；新渲染作业只传 groups，不读取分类专属规则。
    building_merge_enabled: bool = True
    transit_merge_enabled: bool = True
    block_gap_m: float = 24.0
    hub_core_m: float = 80.0
    category_rules: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    block_geometry: Any = None


def _normalize(text: Any) -> str:
    return " ".join(str(text or "").casefold().split())


def name_strength(poi: POI) -> str:
    source = str(getattr(poi.name, "source", "") or "").casefold()
    text = _normalize(poi.name.text)
    if source == "custom":
        return "custom"
    if source == "building":
        return "neighbor"
    if source == "game":
        source_raw = str(getattr(poi, "source_raw", "") or "").strip()
        return ("fallback" if (not text or
                               (source_raw, text) in VERIFIED_GENERIC_GAME_STOP_LABELS)
                else "station")
    return "fallback"


def strong_name_key(poi: POI) -> str | None:
    if name_strength(poi) in ("custom", "neighbor", "station"):
        text = _normalize(poi.name.text)
        return text or None
    return None


def _merge_enabled_for(rules: Mapping[str, Mapping[str, Any]], kind: str) -> bool:
    rule = rules.get(kind)
    if rule is None:
        return True
    return bool(rule.get("enabled", rule.get("merge_enabled", True))) if isinstance(rule, Mapping) else True


def _distance_for(
    rules: Mapping[str, Mapping[str, Any]], kind: str, default: float,
) -> float:
    rule = rules.get(kind)
    if rule is not None:
        value = (rule.get("distance", rule.get("distance_m", rule.get("merge_distance")))
                 if isinstance(rule, Mapping) else rule)
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            pass
    return float(default)


def _group_enabled(profile: MergeProfile, group: str) -> bool:
    if not profile.merge_enabled:
        return False
    configured = profile.groups.get(group)
    if isinstance(configured, Mapping):
        return bool(configured.get("merge_enabled", True))
    if group == "transit":
        return bool(profile.transit_merge_enabled)
    if group == "facility":
        # 旧建筑邻近开关不表示设施身份分组，只有显式设施设置才选单个代表对象。
        return False
    return bool(profile.building_merge_enabled)


def _group_distance(profile: MergeProfile, group: str, legacy_kind: str,
                    default: float) -> float:
    configured = profile.groups.get(group)
    if isinstance(configured, Mapping) and "distance_m" in configured:
        try:
            return max(0.0, float(configured["distance_m"]))
        except (TypeError, ValueError):
            return float(default)
    return _distance_for(profile.category_rules, legacy_kind, default)


def merge(
    candidates: Sequence[POI],
    profile: MergeProfile | None = None,
) -> list[POI]:
    profile = profile or MergeProfile()
    items = list(candidates or ())
    blocks: list[POI] = []
    hubs: list[POI] = []
    facilities: list[POI] = []
    passthrough: list[POI] = []
    for item in items:
        group = poi_group_for(item)
        # 设施身份先于邻近合并解析；父子记录仍是独立目录项，邻近的独立设施不按距离合并。
        if group == "area":
            passthrough.append(item)
        elif group == "facility":
            facilities.append(item)
        elif group == "transit":
            hubs.append(item)
        elif item.source.kind in {"building", "park"}:
            blocks.append(item)
        else:
            passthrough.append(item)

    result = list(passthrough)
    if _group_enabled(profile, "facility"):
        result.extend(_merge_facilities_by_identity(facilities))
    else:
        result.extend(facilities)
    if _group_enabled(profile, "ordinary"):
        result.extend(_merge_ordinary(blocks, profile))
    else:
        result.extend(blocks)
    if _group_enabled(profile, "transit"):
        result.extend(_merge_hubs(
            hubs, profile,
            allow_different_names=profile.allow_different_station_names))
    else:
        result.extend(hubs)
    return sorted(result, key=lambda item: str(item.id))


def _facility_identity_key(item: POI) -> str:
    """返回拥有该设施候选对象的实际目录身份。"""
    group_id = str(getattr(item, "group_id", "") or "")
    parent_id = str(getattr(item, "parent_poi_id", "") or "")
    if group_id.startswith("facility:"):
        return group_id
    if parent_id.startswith("facility:"):
        return parent_id
    return ""


def _merge_facilities_by_identity(items: Sequence[POI]) -> list[POI]:
    """按真实设施选择一个父级代表，不按距离合并；内部子对象仍可独立展示。"""
    by_identity: dict[str, list[POI]] = {}
    ungrouped = []
    for item in items:
        if str(getattr(item, "semantic_level", "")).upper() != "FACILITY":
            ungrouped.append(item)
            continue
        identity = _facility_identity_key(item)
        if identity:
            by_identity.setdefault(identity, []).append(item)
        else:
            # 独立机场即使分类为设施，缺少导出身份时也不能与其他对象合并。
            ungrouped.append(item)
    result = list(ungrouped)
    for identity, members in sorted(by_identity.items()):
        result.append(_make_merged(members, f"facility_identity:{identity}"))
    return result


def _pick_representative(members: Sequence[POI]) -> POI:
    def key(item: POI):
        return (-_NAME_RANK[name_strength(item)], -int(item.priority), str(item.id))

    return min(members, key=key)


def _cluster_anchor(members: Sequence[POI]) -> Vec3:
    cx = sum(item.anchor.x for item in members) / len(members)
    cz = sum(item.anchor.z for item in members) / len(members)
    return min(
        members,
        key=lambda item: (item.anchor.x - cx) ** 2 + (item.anchor.z - cz) ** 2,
    ).anchor


def _mean_anchor(members: Sequence[POI]) -> Vec3:
    return Vec3(
        sum(item.anchor.x for item in members) / len(members),
        sum(item.anchor.y for item in members) / len(members),
        sum(item.anchor.z for item in members) / len(members),
    )


def _make_merged(members: Sequence[POI], reason: str, *, on_member: bool = False) -> POI:
    if len(members) == 1:
        return members[0]
    rep = _pick_representative(members)
    derived = sorted({ref for item in members for ref in item.derived_from})
    anchor = _cluster_anchor(members) if on_member else _mean_anchor(members)
    return POI(
        id=rep.id,
        kind=rep.kind,
        category=rep.category,
        source=rep.source,
        anchor=anchor,
        icon_key=rep.icon_key,
        name=rep.name,
        priority=rep.priority,
        min_zoom=rep.min_zoom,
        pool_key=rep.pool_key,
        name_pool_key=rep.name_pool_key,
        draw_name=rep.draw_name,
        dedupe_group=reason,
        derived_from=derived,
        semantic_level=getattr(rep, "semantic_level", "BUILDING"),
        group_id=getattr(rep, "group_id", ""),
        parent_poi_id=getattr(rep, "parent_poi_id", ""),
        area_id=getattr(rep, "area_id", None),
        source_raw=getattr(rep, "source_raw", ""),
        serving_line_ids=sorted({line_id for item in members for line_id in item.serving_line_ids}),
        growth_kind=rep.growth_kind,
        growth_use=rep.growth_use,
    )


def _dist2_anchor(left: POI, right: POI) -> float:
    return (left.anchor.x - right.anchor.x) ** 2 + (left.anchor.z - right.anchor.z) ** 2


def _union_cluster(
    members: Sequence[POI], distance: float, reason: str, *,
    on_member: bool = False, geometry=None,
) -> list[POI]:
    if len(members) <= 1:
        return list(members)
    parent = list(range(len(members)))

    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: int, right: int) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[max(a, b)] = min(a, b)

    cell_size = max(float(distance) + (geometry.max_extent * 2 if geometry else 0), 1.0)
    distance_sq = float(distance) ** 2
    cells: dict[tuple[int, int], list[int]] = {}
    for index, item in enumerate(members):
        cell = (math.floor(item.anchor.x / cell_size),
                math.floor(item.anchor.z / cell_size))
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for other in cells.get((cell[0] + dx, cell[1] + dz), ()):
                    matches = (geometry.near(item, members[other], distance) if geometry
                               else _dist2_anchor(item, members[other]) <= distance_sq)
                    if matches:
                        union(index, other)
        cells.setdefault(cell, []).append(index)

    components: dict[int, list[POI]] = {}
    for index, item in enumerate(members):
        components.setdefault(find(index), []).append(item)
    return [_make_merged(items, reason, on_member=on_member) for items in components.values()]


def _merge_ordinary(items: Sequence[POI], profile: MergeProfile) -> list[POI]:
    """按单一占地间隙距离合并兼容的普通地点。"""
    groups: dict[tuple, list[POI]] = {}
    result: list[POI] = []
    for item in items:
        if item.source.kind == "building" and (
                item.growth_kind != "zoned"
                or item.growth_use not in {"residential", "commercial", "industrial", "office"}
                or item.name.source == "custom"):
            result.append(item)
            continue
        groups.setdefault((item.kind, strong_name_key(item),
                           getattr(item, "area_id", None), item.growth_use), []).append(item)
    for (kind, _name, _area_id, _use), members in groups.items():
        # 保留旧接口供非界面调用方使用；新的预览和 PNG 配置提供显式分组键。
        if (not profile.groups
                and not _merge_enabled_for(profile.category_rules, kind)):
            result.extend(members)
            continue
        distance = _group_distance(
            profile, "ordinary", kind, profile.block_gap_m)
        result.extend(_union_cluster(
            members, distance, "ordinary", on_member=True,
            geometry=profile.block_geometry))
    return result


def _radius_cluster(members: Sequence[POI], radius: float) -> list[list[POI]]:
    remaining = sorted(members, key=lambda item: str(item.id))
    radius_sq = float(radius) ** 2
    clusters: list[list[POI]] = []
    while remaining:
        seed = remaining[0]
        inside = [item for item in remaining if _dist2_anchor(item, seed) <= radius_sq]
        clusters.append(inside)
        inside_ids = {id(item) for item in inside}
        remaining = [item for item in remaining if id(item) not in inside_ids]
    return clusters


def _cluster_hubs(members: Sequence[POI], radius: float, *,
                  allow_different_names: bool = False) -> list[POI]:
    if allow_different_names:
        return [_make_merged(cluster, "hub")
                for cluster in _radius_cluster(members, radius)]
    core_groups: dict[str, list[POI]] = {}
    unnamed: list[POI] = []
    for item in members:
        key = strong_name_key(item)
        if key:
            core_groups.setdefault(key, []).append(item)
        else:
            unnamed.append(item)

    clusters: list[list[POI]] = []
    for group in core_groups.values():
        clusters.extend(_radius_cluster(group, radius))

    if unnamed:
        seeds = [cluster[0] for cluster in clusters]
        still_unnamed: list[POI] = []
        for item in unnamed:
            best_cluster = None
            best_distance = float(radius)
            for cluster, seed in zip(clusters, seeds):
                distance = math.sqrt(_dist2_anchor(item, seed))
                if distance <= best_distance:
                    best_distance = distance
                    best_cluster = cluster
            if best_cluster is not None:
                best_cluster.append(item)
            else:
                still_unnamed.append(item)
        clusters.extend(_radius_cluster(still_unnamed, radius))

    return [_make_merged(cluster, "hub") for cluster in clusters]


def _merge_hubs(items: Sequence[POI], profile: MergeProfile, *,
                allow_different_names: bool = False) -> list[POI]:
    by_kind: dict[tuple[str, int | None], list[POI]] = {}
    for item in items:
        by_kind.setdefault((item.kind, getattr(item, "area_id", None)), []).append(item)
    result: list[POI] = []
    for (kind, _area_id), members in by_kind.items():
        radius = _group_distance(profile, "transit", kind, profile.hub_core_m)
        result.extend(_cluster_hubs(
            members, radius, allow_different_names=allow_different_names))
    return result
