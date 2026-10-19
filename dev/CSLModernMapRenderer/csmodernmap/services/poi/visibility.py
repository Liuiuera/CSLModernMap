"""在屏幕碰撞布局前判定设施可见性。"""
from __future__ import annotations

from copy import copy

from .settings import normalized_facility_hierarchy
from .ownership import poi_visibility_group_for, sublayer_for


def icon_visibility_dependencies(catalog):
    """返回图标所需的分组及明确设施祖先。"""
    parents = {str(poi.id): poi for poi in catalog.pois}
    groups = {str(group.id): group for group in catalog.facility_groups}
    result = {}
    for poi in catalog.pois:
        required = {poi_visibility_group_for(poi)}
        parent_id = poi.parent_poi_id
        seen = {str(poi.id)}
        while parent_id and parent_id not in seen:
            seen.add(parent_id)
            parent = parents.get(parent_id)
            if parent is not None:
                required.add(poi_visibility_group_for(parent))
                parent_id = parent.parent_poi_id
            else:
                group = groups.get(parent_id)
                if group is not None and group.source_zone_ids:
                    required.add("area")
                parent_id = group.parent_group_id if group is not None else ""
        result[str(poi.id)] = sorted(required)
    return result


def visible_pois(catalog, cfg, *, visibility_snapshot=None, include_hidden_icons=False):
    """应用显式父级控制，不依赖最终绘制图标的可见性；密度或碰撞隐藏父级时仍可显示子对象。"""
    snapshot = visibility_snapshot
    if snapshot is None:
        snapshot = cfg.get("visibility_snapshot")
    has_snapshot = isinstance(snapshot, dict)
    if not include_hidden_icons and not has_snapshot and not cfg.get("enabled", True):
        return ()

    active_profile = cfg.get("active_profile", {})
    modern_profile = isinstance(active_profile, dict) and bool(active_profile)
    if modern_profile:
        # 展示层级属于作业局部配置；工作进程已解析配置后，旧主题父级覆盖不能再成为第二道门槛。
        hierarchy = {
            "children_enabled": True,
            "children_min_zoom": float(active_profile.get("children_min_zoom", 1.0)),
            "overrides": {},
        }
    else:
        hierarchy = normalized_facility_hierarchy(cfg)
    overrides = hierarchy.get("overrides", {}) or {}
    selection = cfg.get("facility_selection", {}) or {}
    groups = {group.id: group for group in catalog.facility_groups}
    parents = {str(poi.id): poi for poi in catalog.pois}
    icon_visibility = snapshot.get("icons", {}) if has_snapshot else {}
    hidden = set(cfg.get("hidden_categories", ()))

    def enabled(poi):
        group = poi_visibility_group_for(poi)
        category = str(getattr(poi, "kind", getattr(poi, "category", "")))
        if category in hidden or getattr(poi, "category", "") in hidden:
            return False
        if include_hidden_icons:
            return True
        if has_snapshot:
            return bool(icon_visibility.get(group, True))
        return (poi.kind not in hidden and poi.category not in hidden
                and cfg.get(sublayer_for(poi), {}).get("enabled", True))

    def selected(poi):
        # 排除设施也排除后代，即使设施自身图标因密度或合并未绘制；沿语义分组追踪归属。
        if not selection:
            return True
        pending = [str(poi.id), str(poi.group_id or ""), str(poi.parent_poi_id or "")]
        seen = set()
        while pending:
            key = pending.pop()
            if not key or key in seen:
                continue
            seen.add(key)
            if not selection.get(key, {}).get("enabled", True):
                return False
            parent = parents.get(key)
            if parent is not None:
                pending.extend([str(parent.group_id or ""), str(parent.parent_poi_id or "")])
            group = groups.get(key)
            if group is not None:
                pending.append(str(group.parent_group_id or ""))
        return True

    result = []
    for poi in catalog.pois:
        if not enabled(poi) or not selected(poi):
            continue
        state = overrides.get(str(poi.id), {})
        if not modern_profile and not state.get("enabled", True):
            continue
        parent_id = poi.parent_poi_id
        if parent_id and not hierarchy.get("children_enabled", True):
            continue
        seen = {str(poi.id)}
        min_zoom = float(poi.min_zoom)
        if parent_id:
            min_zoom = max(min_zoom, float(hierarchy.get("children_min_zoom", 0)))
        admitted = True
        while parent_id and parent_id not in seen:
            seen.add(parent_id)
            state = overrides.get(parent_id, {})
            parent = parents.get(parent_id)
            if (not state.get("enabled", True)
                    or not state.get("children_enabled", True)
                    or (parent is not None and not enabled(parent))):
                admitted = False
                break
            min_zoom = max(min_zoom, float(state.get("children_min_zoom", 0)))
            group = groups.get(parent_id)
            if (group is not None and group.source_zone_ids
                    and has_snapshot and not include_hidden_icons
                    and not snapshot["icons"].get("area", True)):
                admitted = False
                break
            parent_id = (parent.parent_poi_id if parent is not None else
                         group.parent_group_id if group is not None else "")
        if admitted:
            item = copy(poi)
            item.min_zoom = min_zoom
            result.append(item)
    return tuple(result)
