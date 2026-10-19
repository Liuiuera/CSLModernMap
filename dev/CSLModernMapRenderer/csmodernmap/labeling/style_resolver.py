"""集中解析标注样式引用。

路径缺失时返回 None，由调用方报告错误，避免隐式生成默认样式掩盖主题问题。"""
from __future__ import annotations

import copy
from typing import Any, Mapping


STYLE_REFERENCES = {
    "TITLE": "transports.title",
    "LEGEND": "transports.legend",
    "ANNOTATION": "transports.annotation",
    "STATION_NAME": "transports.station.names",
    "LINE_NAME": "transports.label",
    "DISTRICT_NAME": "labels.district",
    "PARK_NAME": "labels.park",
    "ROAD_NAME": "labels.road",
    "BUILDING_NAME": "poi_icons",
    "POI_NAME": "poi_icons",
}


def _get(style: Any, path: str) -> Any:
    if hasattr(style, "get") and not isinstance(style, Mapping):
        return style.get(path, None)
    current = style
    for part in str(path).split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def resolve(style_ref: str, style: Any) -> dict | None:
    """将点分隔的主题路径解析为映射副本，缺失时返回 None。"""
    path = str(style_ref or "").strip()
    if not path:
        return None
    value = _get(style, path)
    if not isinstance(value, Mapping):
        return None
    return copy.deepcopy(dict(value))


resolve_style = resolve


def style_ref_for(label_type: str) -> str:
    return STYLE_REFERENCES.get(str(label_type or "").upper(), "")


def resolve_for_type(label_type: str, style: Any) -> dict | None:
    return resolve(style_ref_for(label_type), style)


def resolve_with_override(style_ref: str, style: Any,
                          style_override: Mapping[str, Any] | None = None) -> dict | None:
    """解析样式引用并应用可选的同路径作业覆盖。"""
    value = resolve(style_ref, style)
    if value is None:
        return None
    override = _get(style_override or {}, style_ref)
    if not isinstance(override, Mapping):
        return value
    value.update(copy.deepcopy(dict(override)))
    return value


def validate_style_references(style: Any, references: Mapping[str, str] | None = None) -> tuple[str, ...]:
    errors = []
    for label_type, path in (references or STYLE_REFERENCES).items():
        if resolve(path, style) is None:
            errors.append(f"{label_type}: missing style reference {path}")
    return tuple(errors)
