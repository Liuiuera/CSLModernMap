"""管理各视图的地图配置。"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import math
from typing import Any

from .services.poi.ownership import POI_GROUPS

PROFILE_SETTING_KEY = "poi_profiles"
PROFILE_SCHEMA_VERSION = 3
PROFILE_TARGETS = ("normal", "transit", "metro")
DENSITY_TIERS = ("extremely_sparse", "very_sparse", "sparse", "standard",
                 "dense", "very_dense", "extremely_dense")
_TARGET_ALIASES = {
    "preview": "normal", "normal_png": "normal", "normal": "normal",
    "map": "normal", "selection": "normal", "random": "normal",
    "random_spot": "normal", "spot": "normal", "transit_png": "transit",
    "transit": "transit", "public_transit": "transit", "metro": "metro",
}


class PoiProfileValidationError(ValueError):
    """表示设置边界上的无效显式修改。"""


def canonical_profile_target(target: str) -> str:
    try:
        return _TARGET_ALIASES[str(target).strip().casefold()]
    except KeyError as exc:
        raise ValueError(f"未知成图设置视图：{target!r}") from exc


def default_poi_profile(target: str) -> dict[str, Any]:
    canonical_profile_target(target)
    return {
        "merge_enabled": True,
        "groups": {
            "transit": {"merge_enabled": True, "distance_m": 80.0},
            "facility": {"merge_enabled": True},
            "ordinary": {"merge_enabled": True, "distance_m": 24.0},
            "area": {"merge_enabled": True, "distance_m": 100.0},
        },
        "allow_different_station_names": False,
        "density_enabled": True,
        "density_tier": "standard",
        "overlap_avoidance": True,
        "png_internal_buildings": False,
        "children_min_zoom": 1.0,
    }


def default_poi_profiles() -> dict[str, Any]:
    return {"schema_version": PROFILE_SCHEMA_VERSION,
            **{view: default_poi_profile(view) for view in PROFILE_TARGETS}}


def _valid_number(value, *, low, high):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and low <= number <= high else None


def normalize_poi_profile(profile: Mapping[str, Any] | None,
                          target: str) -> dict[str, Any]:
    result = default_poi_profile(target)
    raw = profile if isinstance(profile, Mapping) else {}
    for key, default in result.items():
        value = raw.get(key)
        if isinstance(default, bool):
            if isinstance(value, bool):
                result[key] = value
        elif key == "density_tier" and value in DENSITY_TIERS:
            result[key] = value
        elif key == "children_min_zoom":
            number = _valid_number(value, low=0, high=2)
            if number is not None:
                result[key] = number
    groups = raw.get("groups")
    if isinstance(groups, Mapping):
        for group, defaults in result["groups"].items():
            values = groups.get(group)
            if not isinstance(values, Mapping):
                continue
            if isinstance(values.get("merge_enabled"), bool):
                defaults["merge_enabled"] = values["merge_enabled"]
            if "distance_m" in defaults:
                number = _valid_number(values.get("distance_m"), low=0, high=500)
                if number is not None:
                    defaults["distance_m"] = number
    return result


def normalize_poi_profiles(settings_values: Mapping[str, Any] | None) -> dict[str, Any]:
    values = deepcopy(dict(settings_values or {}))
    raw = values.get(PROFILE_SETTING_KEY)
    store = raw if isinstance(raw, Mapping) else {}
    version = store.get("schema_version", PROFILE_SCHEMA_VERSION)
    if type(version) is not int or version < 1:
        raise ValueError("成图设置版本必须是正整数")
    if version > PROFILE_SCHEMA_VERSION:
        raise ValueError(f"POI profile schema version {version} is newer than supported version {PROFILE_SCHEMA_VERSION}")
    result = default_poi_profiles()
    if version == PROFILE_SCHEMA_VERSION:
        for view in PROFILE_TARGETS:
            result[view] = normalize_poi_profile(store.get(view), view)
    values[PROFILE_SETTING_KEY] = result
    return values


def get_poi_profile(settings_values: Mapping[str, Any] | None, target: str) -> dict[str, Any]:
    return deepcopy(normalize_poi_profiles(settings_values)[PROFILE_SETTING_KEY][
        canonical_profile_target(target)])


def validate_poi_profile(profile: Mapping[str, Any], target: str) -> dict[str, Any]:
    if not isinstance(profile, Mapping):
        raise PoiProfileValidationError("成图设置必须是对象")
    defaults = default_poi_profile(target)
    for key, default in defaults.items():
        if isinstance(default, bool) and key in profile and not isinstance(profile[key], bool):
            raise PoiProfileValidationError(f"{key} 必须是布尔值")
    groups = profile.get("groups", {})
    if not isinstance(groups, Mapping):
        raise PoiProfileValidationError("成图设置的 groups 必须是对象")
    for group in POI_GROUPS:
        raw = groups.get(group, {})
        if not isinstance(raw, Mapping):
            raise PoiProfileValidationError(f"{group} 设置必须是对象")
        if "merge_enabled" in raw and not isinstance(raw["merge_enabled"], bool):
            raise PoiProfileValidationError(f"{group}.merge_enabled 必须是布尔值")
        if "distance_m" in raw and _valid_number(raw["distance_m"], low=0, high=500) is None:
            raise PoiProfileValidationError("合并距离必须为 0–500 米")
    if "children_min_zoom" in profile and _valid_number(profile["children_min_zoom"], low=0, high=2) is None:
        raise PoiProfileValidationError("设施子项最小缩放必须为 0–2")
    if "density_tier" in profile and profile["density_tier"] not in DENSITY_TIERS:
        raise PoiProfileValidationError("未知的密度档位")
    return normalize_poi_profile(profile, target)


def default_poi_visibility() -> dict[str, dict[str, bool]]:
    return {
        "icons": {group: True for group in POI_GROUPS},
        "names": {group: group in {"transit", "area"} for group in POI_GROUPS},
    }


def normalize_poi_visibility_snapshot(
    raw: Mapping[str, Any],
) -> dict[str, dict[str, bool]]:
    """校验共享的只读图标与名称可见性快照，不将其写入各输出目标配置。"""
    if not isinstance(raw, Mapping):
        raise ValueError("地点可见性快照必须是对象")
    defaults = default_poi_visibility()
    result: dict[str, dict[str, bool]] = {}
    for channel in ("icons", "names"):
        channel_values = raw.get(channel, defaults[channel])
        if not isinstance(channel_values, Mapping):
            raise ValueError(f"地点可见性快照缺少 {channel} 对象")
        result[channel] = {}
        for group in POI_GROUPS:
            value = channel_values.get(group, defaults[channel][group])
            if not isinstance(value, bool):
                raise ValueError(
                    f"地点可见性快照 {channel}.{group} 必须是布尔值")
            result[channel][group] = value
    return result
