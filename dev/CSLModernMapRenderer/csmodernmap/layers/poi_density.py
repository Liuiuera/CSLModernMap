"""分两阶段选择需要显示的 POI 而不修改原始目录。"""
import math
from collections import defaultdict


CATEGORY_GRID_MULTIPLIER = {
    "airport": 2.0, "private_airport": 2.0, "train": 1.5,
    "park": 1.5, "highway_exit": 1.5, "highway_entrance": 1.5,
}

# 界面上的「POI 密度」档位 → (grid_size, max_per_cell)。
# 唯一事实源：预览装配（guir_backend）与 PNG 密度写回（_handler_poi_density）
# 都从这里取，不要各自照抄一份数值表。
POI_DENSITY_PRESETS = {
    "extremely_sparse": (300.0, 1),
    "very_sparse": (220.0, 1),
    "sparse": (150.0, 1),
    "standard": (100.0, 2),
    "dense": (70.0, 4),
    "very_dense": (50.0, 6),
    "extremely_dense": (35.0, 8),
}

_DENSITY_ALIASES = {"auto": "standard", "full": "dense"}


def poi_density_params(value) -> tuple[float, int]:
    """将预览密度级别规范化到保留的缩放感知网格预设。"""
    key = str(value or "standard").strip().casefold()
    key = _DENSITY_ALIASES.get(key, key)
    return POI_DENSITY_PRESETS.get(key, POI_DENSITY_PRESETS["standard"])


def filter_pois_by_distance(items, profile, *, allow_different_station_names=False):
    """按实体锚点筛选 PNG 候选对象而不修改目录；身份已在此之前解析，距离筛选只选显示代表。"""
    if not isinstance(profile, dict):
        return list(items)
    groups = profile.get("groups", {})
    if not isinstance(groups, dict):
        return list(items)

    def world(item):
        x = getattr(item, "world_x", None)
        z = getattr(item, "world_z", None)
        if x is None or z is None:
            return None
        return float(x), float(z)

    def rank(item):
        return (getattr(item, "name_source", "") != "custom",
                -int(getattr(item, "priority", 0)),
                str(getattr(item, "category", "")),
                str(getattr(item, "poi_id", "")),
                float(getattr(item, "world_x", 0.0) or 0.0),
                float(getattr(item, "world_z", 0.0) or 0.0))

    def key_for(item, protect_name):
        group = str(getattr(item, "poi_group", "ordinary") or "ordinary")
        kind = str(getattr(item, "kind", "") or getattr(item, "category", ""))
        name = str(getattr(item, "strong_name", "") or "") if protect_name else ""
        return group, kind, name

    class SpatialSelection:
        def __init__(self, radius):
            self.radius = max(float(radius), 1e-6)
            self.radius_sq = float(radius) ** 2
            self.cells = defaultdict(list)

        def cell(self, point):
            return math.floor(point[0] / self.radius), math.floor(point[1] / self.radius)

        def nearby(self, point):
            cx, cz = self.cell(point)
            for dx in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for other in self.cells.get((cx + dx, cz + dz), ()):
                        other_point = world(other)
                        if other_point is None:
                            continue
                        ddx, ddz = point[0] - other_point[0], point[1] - other_point[1]
                        if ddx * ddx + ddz * ddz < self.radius_sq:
                            return True
            return False

        def add(self, item):
            point = world(item)
            if point is not None:
                self.cells[self.cell(point)].append(item)

    selected = []
    passthrough = []
    by_group = {group: [] for group in ("transit", "facility", "ordinary")}
    for item in items:
        group = str(getattr(item, "poi_group", "ordinary") or "ordinary")
        cfg = groups.get(group, {})
        point = world(item)
        if (group not in by_group or point is None
                or not isinstance(cfg, dict)
                or not cfg.get("distance_filter_enabled", False)):
            passthrough.append(item)
            continue
        try:
            distance = float(cfg.get("distance_m", 0))
        except (TypeError, ValueError):
            distance = 0.0
        if not math.isfinite(distance) or distance <= 0:
            passthrough.append(item)
            continue
        by_group[group].append((item, distance))

    for group, candidates in by_group.items():
        if not candidates:
            continue
        # 每个配置按分组提供距离；遇到混合的异常值时分别处理各距离段。
        distance_buckets = defaultdict(list)
        for item, distance in candidates:
            distance_buckets[distance].append(item)
        for distance, members in distance_buckets.items():
            protect_names = (group in {"transit", "ordinary"}
                             and not (group == "transit"
                                      and allow_different_station_names))
            if not protect_names:
                local = []
                by_kind = defaultdict(list)
                for item in members:
                    by_kind[key_for(item, False)].append(item)
                for kind_members in by_kind.values():
                    spatial = SpatialSelection(distance)
                    for item in sorted(kind_members, key=rank):
                        point = world(item)
                        if not spatial.nearby(point):
                            spatial.add(item)
                            local.append(item)
                selected.extend(local)
                continue

            named = defaultdict(list)
            unnamed = defaultdict(list)
            for item in members:
                group_key, kind, name_key = key_for(item, True)
                if name_key:
                    named[(group_key, kind, name_key)].append(item)
                else:
                    unnamed[(group_key, kind, "")].append(item)
            kept_named_by_kind = defaultdict(list)
            for name_members in named.values():
                spatial = SpatialSelection(distance)
                kept = []
                for item in sorted(name_members, key=rank):
                    point = world(item)
                    if not spatial.nearby(point):
                        spatial.add(item)
                        kept.append(item)
                kind = key_for(name_members[0], False)
                kept_named_by_kind[kind].extend(kept)
            for unnamed_key, unnamed_members in unnamed.items():
                named_index = SpatialSelection(distance)
                for item in kept_named_by_kind.get(unnamed_key, ()):
                    named_index.add(item)
                unnamed_index = SpatialSelection(distance)
                for item in sorted(unnamed_members, key=rank):
                    point = world(item)
                    if named_index.nearby(point):
                        continue
                    if not unnamed_index.nearby(point):
                        unnamed_index.add(item)
                        selected.append(item)
            for values in kept_named_by_kind.values():
                selected.extend(values)

    return sorted([*passthrough, *selected], key=rank)


def select_pois(items, *, grid_size, max_per_cell, origin=(0, 0),
                pixel_scale=1.0, distance_px=0.0, overrides=None,
                pixels_per_world=1.0):
    """先按类别网格选择，再限制各子图层合并后的网格数量；坐标以最终输出像素计。"""
    items = list(items)
    # 区域主体不参与密度淘汰，也不占用普通 POI 的网格配额。
    areas = [item for item in items
             if str(getattr(item, "semantic_level", "")).upper() == "AREA"]
    items = [item for item in items
             if str(getattr(item, "semantic_level", "")).upper() != "AREA"]
    if grid_size <= 0:
        return [*areas, *items]
    if max_per_cell <= 0:
        return areas
    rules = overrides or {}
    base = max(grid_size, distance_px, 1.0)

    def rank(p):
        # 玩家命名属于地图身份；仍遵守缩放和网格限制，但同格竞争时优先显示命名对象。
        parent_rank = bool(getattr(p, "parent_poi_id", ""))
        return (parent_rank, p.name_source != "custom", -p.priority, p.category,
                p.px, p.py, p.label, p.icon_id, p.source)

    def cell(p, size):
        return (math.floor(round((p.px-origin[0])/pixel_scale/size, 10)),
                math.floor(round((p.py-origin[1])/pixel_scale/size, 10)))

    groups = defaultdict(list)
    for p in items:
        groups[p.category].append(p)
    selected, bypass = [], []
    for category, candidates in sorted(groups.items()):
        rule = rules.get(category, {})
        if not isinstance(rule, dict):
            rule = {"distance": rule}
        if not rule.get("enabled", rule.get("merge_enabled", True)):
            bypass.extend(candidates)
            continue
        local_distance = rule.get("distance", rule.get("distance_m", rule.get("merge_distance", 0)))
        try:
            local_distance = max(0.0, float(local_distance)) * pixels_per_world
        except (TypeError, ValueError):
            local_distance = 0.0
        size = max(base, grid_size * CATEGORY_GRID_MULTIPLIER.get(category, 1), local_distance)
        cells = set()
        for p in sorted(candidates, key=rank):
            key = cell(p, size)
            if key not in cells:
                cells.add(key)
                selected.append(p)
    result = []
    cells = defaultdict(set)
    for p in sorted(selected, key=rank):
        categories = cells[cell(p, base)]
        if p.category not in categories and len(categories) < max_per_cell:
            categories.add(p.category)
            result.append(p)
    return sorted([*areas, *result, *bypass], key=rank)
