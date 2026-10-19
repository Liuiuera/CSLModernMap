"""定义不回写交互地图样式的 PNG 图标规则。"""
from __future__ import annotations

from copy import deepcopy


def normalize_png_poi_options(options=None) -> dict:
    options = options or {}
    if not isinstance(options, dict):
        return {}
    active_profile = options.get("active_profile", options.get("profile"))
    if (not isinstance(active_profile, dict)
            and isinstance(options.get("groups"), dict)
            and options.get("target") in {"normal_png", "transit_png"}):
        active_profile = options
    if isinstance(active_profile, dict):
        target = str(active_profile.get("target", options.get("target", "normal_png")))
        if target not in {"normal_png", "transit_png"}:
            raise ValueError("PNG POI 配置目标必须是 normal_png 或 transit_png")
        result = {"active_profile": deepcopy(active_profile)}
        snapshot = options.get("visibility_snapshot")
        if isinstance(snapshot, dict):
            result["visibility_snapshot"] = deepcopy(snapshot)
        return result

    # 旧版适配器仅供新 WinUI 输出流程以外的调用方使用。
    # 作业局部配置不进入此分支。
    mode = options.get("mode", "major")
    if mode not in {"major", "detail", "off", "custom"}:
        mode = "major"
    return {
        "mode": mode,
        "internal_buildings": (bool(options.get("internal_buildings", False))
                               if mode == "custom" else mode == "detail"),
        "merge_same_kind": (bool(options.get("merge_same_kind", True))
                            if mode == "custom" else True),
    }


def apply_png_poi_options(cfg: dict, options) -> None:
    """只应用于导出工作进程的局部 POI 配置，并在静态布局前保留全部建筑候选对象。"""
    if not options:
        return
    policy = normalize_png_poi_options(options)
    if "active_profile" in policy:
        profile = policy["active_profile"]
        cfg["active_profile"] = deepcopy(profile)
        if "visibility_snapshot" in policy:
            cfg["visibility_snapshot"] = deepcopy(policy["visibility_snapshot"])
        hierarchy = deepcopy(cfg.get("facility_hierarchy", {}))
        hierarchy.update(enabled=True)
        cfg["facility_hierarchy"] = hierarchy
        cfg.pop("png_export", None)
        return
    cfg["png_export"] = policy
    if policy["mode"] == "off":
        cfg["enabled"] = False
    for owner_cfg in (cfg, cfg.setdefault("building", {}),
                      cfg.setdefault("transit", {})):
        owner_cfg["png_export"] = dict(policy)
        owner_cfg["building_merge_enabled"] = False
        owner_cfg["min_priority"] = 0
        hierarchy = deepcopy(owner_cfg.get(
            "facility_hierarchy", cfg.get("facility_hierarchy", {})))
        hierarchy.update(enabled=True, children_enabled=True, children_min_zoom=0.0)
        owner_cfg["facility_hierarchy"] = hierarchy
