"""将界面快照转换为统一的 POI 合并配置。"""

from collections.abc import Mapping

def apply_merge_settings(cfg: dict, ui: dict) -> None:
    for key in ("building_merge_enabled", "transit_merge_enabled",
                "block_gap_m", "hub_core_m", "merge_overrides"):
        cfg[key] = ui[key]
    hierarchy = cfg.get("facility_hierarchy")
    if not isinstance(hierarchy, dict):
        hierarchy = {}
        cfg["facility_hierarchy"] = hierarchy
    hierarchy["enabled"] = bool(ui.get(
        "facility_hierarchy_enabled", ui.get("same_facility_merge", True)))
    hierarchy.pop("child_min_zoom", None)
    hierarchy["children_min_zoom"] = float(ui.get(
        "facility_child_min_zoom", 1.0))
    hierarchy["overrides"] = dict(ui.get("facility_poi_overrides", {}))
    for owner in ("building", "transit"):
        key = f"poi_{owner}_enabled"
        if key in ui:
            cfg.setdefault(owner, {})["enabled"] = bool(ui[key])


def normalized_facility_hierarchy(cfg):
    """在设置入口统一旧的子图标缩放层级名称。"""
    raw = cfg.get("facility_hierarchy", {})
    hierarchy = dict(raw) if isinstance(raw, Mapping) else {}
    if "child_min_zoom" in hierarchy:
        hierarchy.setdefault("children_min_zoom", hierarchy.pop("child_min_zoom"))
    elif "facility_child_min_zoom" in cfg:
        hierarchy.setdefault("children_min_zoom", cfg["facility_child_min_zoom"])
    overrides = {}
    for key, state in (hierarchy.get("overrides", {}) or {}).items():
        state = dict(state)
        if "child_min_zoom" in state:
            state.setdefault("children_min_zoom", state.pop("child_min_zoom"))
        overrides[key] = state
    hierarchy["overrides"] = overrides
    return hierarchy
