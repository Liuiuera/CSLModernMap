"""持久化按视图区分的界面设置并处理版本迁移。

应用通用设置保存在 settings.json，交通和地铁视图选项保存在 views.json。首次读取时复制旧版视图键，但不删除旧文件中的数据。"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from .settings_store import settings_path
from ._paths import user_data_dir


VIEWS_FILE = "views.json"
VIEWS_VERSION = 1

DEFAULT_TRANSIT_TYPES = (
    "Metro",
    "Train",
    "Monorail",
    "Tram",
    "CableCar",
    "Ship",
    "Ferry",
    "Bus",
    "Trolleybus",
    "Airplane",
    "Helicopter",
)
DEFAULT_METRO_TYPES = ("Metro", "Train", "Monorail")


def _default_transit() -> dict[str, Any]:
    return {
        "mask": 0.6,
        "base_layers": {"water": True, "road": True, "district": True},
        "layers": {
            "lines": True,
            "line_labels": True,
            "base_water": True,
            "base_road": True,
            "base_district": True,
        },
        "types": list(DEFAULT_TRANSIT_TYPES),
        "line_width": 3.0,
        "palette": "game",
        "auto_color": False,
        "line_label": {"enabled": True, "size": 12.0, "interval": 800.0},
        "stations": {
            "enabled": True,
            "size": 8.0,
            "merge": True,
            "merge_range": 48.0,
        },
        "station_names": {"enabled": False, "size": 12.0},
        "facilities": {
            "enabled": True,
            "names_enabled": True,
            "icon_size": 24.0,
            "density": "all",
            "min_zoom": 8.0,
        },
        "ordinary_pois": "hidden",
        "presentation": {
            "label_scale": 1.0,
            "icon_scale": 1.0,
            "palette_index": 0,
            "title_scale": 1.0,
            "legend_scale": 1.0,
        },
    }


def _default_metro() -> dict[str, Any]:
    return {
        "types": list(DEFAULT_METRO_TYPES),
        "line_ids": None,
        "station_line_ids": None,
        "stations": {"enabled": True, "size": 8.0, "diameter": 12.0, "merge": True, "merge_range": 48.0},
        "line_label": {"enabled": True, "size": 12.0, "interval": 800.0},
        "station_names": {
            "enabled": True,
            "mode": "all",
            "style": "all",
            "size": 16.0,
            "font": "SimHei", "color": "#4A4F5A", "stroke_width": 2.0, "stroke_color": "#FFFFFF",
        },
        "output_size": 0,
        "diagram": {
            "adaptive_scale": True,
            "adaptive_strength": 0.85,
            "spacing": 281,
            "parallel_gap": 1.55,
        },
        "legend": {"enabled": True, "position": "right_top", "scale": 1.0},
        "background": "",
        "title": {"enabled": True, "template": "", "scale": 1.0},
        "schematic_water": True,
        "water_smoothing": 8.0,
        # 接驳图标默认开（2026-09-17 产品经理拍板）。它与站点圆点不是平行的两个
        # 开关：圆点是父设置，圆点不画时接驳图标也不画（见 main.ts 的 tv-poi 判据）。
        "facilities": {"enabled": True, "scale": 1.0},
        "presentation": {
            "label_scale": 1.0,
            "icon_scale": 1.0,
            "palette_index": 0,
            "auto_color": False,
        },
        "annotations": {
            "disclaimer": True,
            "scale_info": True,
            "data_notice": True,
            "generated_at": True,
            "north_arrow": False,
            "credit": True,
        },
    }


def default_view_settings() -> dict[str, Any]:
    """返回包含全部受支持默认值的新版本一文档。"""
    return {
        "version": VIEWS_VERSION,
        "migrated": False,
        "display_contract": 2,
        "schematic_contract": 2,
        "transit": _default_transit(),
        "metro": _default_metro(),
    }


# 默认值快照按约定只读，需要修改时须调用 default_view_settings 获取独立副本。
DEFAULT_VIEW_SETTINGS = default_view_settings()


def views_path(directory: str | Path | None = None) -> Path:
    """返回设置目录下 views.json 的绝对路径。"""
    base = user_data_dir() if directory is None else Path(directory)
    return base.resolve() / VIEWS_FILE


def _json_copy(value: Any) -> Any:
    """复制为 JSON 兼容值，同时保留未来版本的未知字段。"""
    if isinstance(value, Mapping):
        return {str(key): _json_copy(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_copy(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _json_copy(value.to_dict())
    return value


def _deep_merge(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(base))
    for key, value in overlay.items():
        key = str(key)
        if (isinstance(result.get(key), Mapping)
                and isinstance(value, Mapping)):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = _json_copy(value)
    return result


def _version(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_document(value: Mapping[str, Any]) -> dict[str, Any]:
    """填充版本一缺失字段，不丢弃未知扩展字段。"""
    if not isinstance(value, Mapping):
        raise TypeError("view settings must be a mapping")

    raw: Mapping[str, Any] = value
    # 迁移工具可接受 settings.json 的同类包装，但磁盘上的 views.json 保持顶层交通和地铁结构。
    wrapped = value.get("values")
    if (isinstance(wrapped, Mapping)
            and "transit" not in value and "metro" not in value):
        raw = wrapped

    version = _version(raw.get("version", VIEWS_VERSION))
    if version != VIEWS_VERSION:
        raise ValueError(f"unsupported views.json version: {version!r}")

    result = _deep_merge(DEFAULT_VIEW_SETTINGS, raw)
    # 旧写入器曾保存工作进程选项名而非嵌套键。
    transit_label = result["transit"]["line_label"]
    for option, key in (("line_label_enabled", "enabled"),
                        ("line_label_size", "size"),
                        ("line_label_interval", "interval")):
        if option in transit_label:
            transit_label[key] = transit_label.pop(option)
    if raw.get("display_contract") != 2:
        transit = result["transit"]
        gates = transit["layers"]
        archived = {key: gates.pop(key) for key in (
            "stations", "facilities", "facility_names", "station_names") if key in gates}
        for gate, section, field in (("stations", "stations", "enabled"),
                                     ("facilities", "facilities", "enabled"),
                                     ("facility_names", "facilities", "names_enabled"),
                                     ("station_names", "facilities", "names_enabled")):
            if archived.get(gate) is False:
                transit[section][field] = False
        result.setdefault("migration", {})["display_gates"] = archived
    result["display_contract"] = 2
    if raw.get("schematic_contract") != 2:
        metro = result["metro"]
        presentation = metro["presentation"]
        result.setdefault("migration", {})["schematic_presentation"] = deepcopy(presentation)
        metro["stations"]["diameter"] = float(metro["stations"]["size"]) * 2 * float(presentation.get("icon_scale", 1))
        metro["station_names"]["size"] = max(int(13.5 * float(presentation.get("label_scale", 1))), 8)
    result["schematic_contract"] = 2
    result["version"] = VIEWS_VERSION
    result["migrated"] = bool(raw.get("migrated", False))
    return _json_copy(result)


def _settings_values(value: Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value.get("values"), Mapping):
        return _json_copy(value["values"])
    return _json_copy(value)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return dict(raw) if isinstance(raw, Mapping) else None


def _legacy_value(values: Mapping[str, Any], *keys: str) -> tuple[Any, bool]:
    for key in keys:
        if key in values:
            return values[key], True
    return None, False


def _put(section: dict[str, Any], path: tuple[str, ...], values: Mapping[str, Any],
         *keys: str) -> None:
    value, present = _legacy_value(values, *keys)
    if not present:
        return
    target = section
    for key in path[:-1]:
        target = target.setdefault(key, {})
    target[path[-1]] = _json_copy(value)


def _put_list(section: dict[str, Any], path: tuple[str, ...],
              values: Mapping[str, Any], *keys: str) -> None:
    value, present = _legacy_value(values, *keys)
    if not present:
        return
    if value is None:
        normalized = None
    elif isinstance(value, (list, tuple)):
        normalized = list(value)
    else:
        return
    target = section
    for key in path[:-1]:
        target = target.setdefault(key, {})
    target[path[-1]] = _json_copy(normalized)


def _legacy_keys(values: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): _json_copy(value)
        for key, value in values.items()
        if str(key).startswith(("transit_view_", "metro_view_"))
    }


def _legacy_metro_merge(values: Mapping[str, Any]) -> dict[str, Any]:
    overrides = values.get("poi_merge_overrides_var")
    if not isinstance(overrides, Mapping):
        return {}
    metro = overrides.get("metro")
    if not isinstance(metro, Mapping):
        return {}
    result = {}
    if isinstance(metro.get("enabled"), bool):
        result["enabled"] = metro["enabled"]
    distance = metro.get("distance")
    if isinstance(distance, (int, float)) and not isinstance(distance, bool) and 0 <= distance <= 500:
        result["distance"] = distance
    return result


def migrate_legacy_settings(
    settings: Mapping[str, Any],
    *,
    base: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """把旧版扁平视图键转换为版本一文档，保留原键供兼容回退。"""
    if not isinstance(settings, Mapping):
        raise TypeError("legacy settings must be a mapping")
    values = _settings_values(settings)
    legacy = _legacy_keys(values)

    if base is None:
        result = default_view_settings()
    else:
        result = _normalize_document(base)
    overrides = _legacy_metro_merge(values)
    if overrides:
        result["metro"]["stations"].update({key: overrides[source] for source, key in
            (("enabled", "merge"), ("distance", "merge_range")) if source in overrides})
    if not legacy:
        return result

    transit = result["transit"]
    metro = result["metro"]

    # 公共交通视图设置。
    _put(transit, ("mask",), values, "transit_view_mask_strength")
    _put(transit, ("base_layers", "water"), values,
         "transit_view_base_water")
    _put(transit, ("base_layers", "road"), values,
         "transit_view_base_road")
    _put(transit, ("base_layers", "district"), values,
         "transit_view_base_district")
    _put_list(transit, ("types",), values, "transit_view_types")
    _put_list(transit, ("line_ids",), values, "transit_view_line_ids")
    _put(transit, ("stations", "enabled"), values,
         "transit_view_stations")
    _put(transit, ("stations", "size"), values,
         "transit_view_station_size")
    _put(transit, ("stations", "merge"), values,
         "transit_view_station_merge", "transit_view_station_merge_enabled")
    _put(transit, ("stations", "merge_range"), values,
         "transit_view_station_merge_range")
    _put(transit, ("facilities", "names_enabled"), values,
         "transit_view_facility_names_enabled",
         "transit_view_station_names_enabled")
    _put(transit, ("line_label", "enabled"), values,
         "transit_view_line_label_enabled", "transit_view_line_names_enabled")
    _put(transit, ("line_label", "size"), values,
         "transit_view_line_label_size")
    _put(transit, ("line_label", "interval"), values,
         "transit_view_line_label_interval")
    _put(transit, ("line_width",), values, "transit_view_line_width")
    _put(transit, ("auto_color",), values, "transit_view_auto_color")
    _put(transit, ("facilities", "enabled"), values,
         "transit_view_facilities_enabled")
    _put(transit, ("facilities", "icon_size"), values,
         "transit_view_facility_icon_size")
    _put(transit, ("facilities", "density"), values,
         "transit_view_facility_density")
    _put(transit, ("facilities", "min_zoom"), values,
         "transit_view_facility_min_zoom")
    _put(transit, ("ordinary_pois",), values,
         "transit_view_ordinary_pois")
    for old, new in (
        ("transit_view_label_scale", "label_scale"),
        ("transit_view_icon_scale", "icon_scale"),
        ("transit_view_palette_index", "palette_index"),
        ("transit_view_title_scale", "title_scale"),
        ("transit_view_legend_scale", "legend_scale"),
    ):
        _put(transit, ("presentation", new), values, old)

    # 地铁示意图视图设置。
    _put_list(metro, ("types",), values, "metro_view_types")
    _put_list(metro, ("line_ids",), values, "metro_view_line_ids")
    _put_list(metro, ("station_line_ids",), values,
              "metro_view_station_line_ids")
    _put(metro, ("stations", "enabled"), values, "metro_view_stations")
    _put(metro, ("line_label", "enabled"), values,
         "metro_view_line_label_enabled", "metro_view_line_names_enabled")
    _put(metro, ("line_label", "size"), values,
         "metro_view_line_label_size")
    _put(metro, ("line_label", "interval"), values,
         "metro_view_line_label_interval")
    _put(metro, ("station_names", "enabled"), values,
         "metro_view_station_names_enabled")
    _put(metro, ("station_names", "mode"), values,
         "metro_view_station_names_mode")
    _put(metro, ("station_names", "style"), values,
         "metro_view_station_name_style")
    _put(metro, ("station_names", "size"), values,
         "metro_view_station_name_size")
    _put(metro, ("output_size",), values, "metro_view_output_size")
    for old, new in (
        ("metro_view_adaptive_scale", "adaptive_scale"),
        ("metro_view_adaptive_strength", "adaptive_strength"),
        ("metro_view_spacing", "spacing"),
        ("metro_view_parallel_gap", "parallel_gap"),
    ):
        _put(metro, ("diagram", new), values, old)
    _put(metro, ("legend", "enabled"), values,
         "metro_view_legend_enabled")
    _put(metro, ("legend", "position"), values,
         "metro_view_legend_position")
    _put(metro, ("legend", "scale"), values, "metro_view_legend_scale")
    _put(metro, ("background",), values, "metro_view_background")
    _put(metro, ("title", "enabled"), values, "metro_view_title_enabled")
    _put(metro, ("title", "template"), values,
         "metro_view_title_template")
    _put(metro, ("title", "scale"), values, "metro_view_title_scale")
    _put(metro, ("schematic_water",), values, "metro_view_water")
    _put(metro, ("water_smoothing",), values, "metro_view_water_smoothing")
    _put(metro, ("facilities", "enabled"), values,
         "metro_view_facilities_enabled")
    _put(metro, ("facilities", "scale"), values,
         "metro_view_facilities_scale")
    for name in ("disclaimer", "scale_info", "data_notice", "generated_at",
                 "north_arrow", "credit"):
        _put(metro, ("annotations", name), values, f"metro_view_ann_{name}")
    for old, new in (
        ("metro_view_label_scale", "label_scale"),
        ("metro_view_icon_scale", "icon_scale"),
        ("metro_view_palette_index", "palette_index"),
        ("metro_view_auto_color", "auto_color"),
    ):
        _put(metro, ("presentation", new), values, old)

    result["migrated"] = True
    result["legacy"] = legacy
    # 顶层保留旧拼写，便于只复制 views.json 的工具完成迁移。
    result.update(deepcopy(legacy))
    if base is None:
        result.pop("schematic_contract", None)
        result.pop("display_contract", None)
    return _normalize_document(result)


def _read_legacy_values(directory: str | Path | None) -> dict[str, Any]:
    raw = _read_json(settings_path(directory))
    return {} if raw is None else _settings_values(raw)


def load_view_settings(directory: str | Path | None = None) -> dict[str, Any]:
    """加载版本一视图设置，并在首次使用时迁移旧设置。"""
    path = views_path(directory)
    raw = _read_json(path) if path.exists() else None
    if raw is not None:
        try:
            stations = raw.setdefault("metro", {}).setdefault("stations", {})
            if "merge" not in stations or "merge_range" not in stations:
                overrides = _legacy_metro_merge(_read_legacy_values(directory))
                stations.update({key: overrides[source]
                    for source, key in (("enabled", "merge"), ("distance", "merge_range"))
                    if source in overrides and key not in stations})
            return _normalize_document(raw)
        except (TypeError, ValueError):
            # 损坏或较新版本的视图文件不能被覆盖；回退默认值，待显式保存时修复。
            return default_view_settings()

    legacy = _read_legacy_values(directory)
    if _legacy_keys(legacy):
        migrated = migrate_legacy_settings(legacy)
        # 迁移尽力保存；目录只读时返回内存结果，保存成功后下次无需依赖旧设置。
        save_view_settings(migrated, directory)
        return migrated
    return default_view_settings()


def save_view_settings(settings: Mapping[str, Any],
                       directory: str | Path | None = None) -> bool:
    """原子写入规范化的版本一视图文档。"""
    if hasattr(settings, "to_dict") and callable(settings.to_dict):
        settings = settings.to_dict()
    normalized = _normalize_document(settings)
    path = views_path(directory)
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(
            json.dumps(normalized, ensure_ascii=False, indent=2,
                       sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)
        return True
    except (OSError, TypeError, ValueError):
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return False


__all__ = [
    "DEFAULT_METRO_TYPES",
    "DEFAULT_TRANSIT_TYPES",
    "DEFAULT_VIEW_SETTINGS",
    "VIEWS_FILE",
    "VIEWS_VERSION",
    "default_view_settings",
    "load_view_settings",
    "migrate_legacy_settings",
    "save_view_settings",
    "views_path",
]
