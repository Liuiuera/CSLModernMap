"""在邻近聚合前解析 POI 的实体归属。

保留来源引用用于诊断，不以图标资源相同作为实体相同的依据。"""
from copy import copy

from .merge import strong_name_key, _pick_representative


def _compatible(a, b):
    left, right = strong_name_key(a), strong_name_key(b)
    return a.kind == b.kind and (not left or not right or left == right)


def _combine(primary, members):
    if len(members) == 1:
        return primary
    result = copy(primary)
    result.name = _pick_representative(members).name
    result.derived_from = sorted({ref for item in members for ref in item.derived_from})
    result.dedupe_group = "identity"
    return result


def resolve_identities(mapdata, buildings, stops):
    """按结构解析建筑身份而不跨越 POI 分组；实际站点与设施建筑保持独立候选对象。"""
    raw = {int(b.id): b for b in getattr(mapdata, "buildings", ())}
    pois = {int(p.source.id): p for p in buildings}
    parent_ids = {}
    for object_id, building in sorted(raw.items()):
        value = getattr(building, "parent_id", None) or getattr(building, "parent_building_id", "")
        try:
            parent_ids[object_id] = int(value)
        except (ValueError, TypeError):
            pass
    for object_id, building in sorted(raw.items()):
        children = list(getattr(building, "child_ids", ()))
        try:
            children.append(int(getattr(building, "sub_building_id", "")))
        except (ValueError, TypeError):
            pass
        for child_id in children:
            if child_id in raw:
                parent_ids.setdefault(child_id, object_id)
    groups = {}
    for child_id, poi in pois.items():
        root = child_id
        trail = [root]
        while root in raw:
            parent = parent_ids.get(root)
            if parent not in pois or not _compatible(poi, pois[parent]):
                break
            if parent in trail:
                root = min(trail[trail.index(parent):])
                break
            trail.append(parent)
            root = parent
        groups.setdefault(root, []).append(poi)
    facilities = [_combine(pois[root], members) for root, members in sorted(groups.items())]
    return facilities, list(stops)
