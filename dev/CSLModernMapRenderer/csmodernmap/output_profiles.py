"""管理 PNG 输出配置及旧版全局选项迁移。"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import math
from typing import Any

from .system_info import automatic_antialias
from .export_region import OUTPUT_ROTATIONS, output_rotation


OUTPUT_PROFILE_SETTING_KEY = "output_profiles"
OUTPUT_PROFILE_SCHEMA_VERSION = 1
OUTPUT_PROFILE_TARGETS = ("normal_png", "transit_png")
OUTPUT_SIZE_MIN = 512
OUTPUT_SIZE_MAX = 32768
_MISSING = object()


class OutputProfileValidationError(ValueError):
    """表示某个 PNG 输出配置中的无效显式修改。"""


def default_output_profile(target: str) -> dict[str, Any]:
    if target not in OUTPUT_PROFILE_TARGETS:
        raise ValueError(f"unknown output profile target: {target!r}")
    return {"size": 4096, "rotation": 0, "aa": automatic_antialias()}


def _number(value: Any, *, integer: bool = False) -> int | float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number):
        return None
    if integer and number != int(number):
        return None
    return int(number) if integer else number


def _legacy_size(value: Any) -> int:
    parsed = _number(value, integer=True)
    if parsed is not None:
        return parsed if OUTPUT_SIZE_MIN <= parsed <= OUTPUT_SIZE_MAX else 4096
    text = str(value or "").strip()
    if "(" in text and ")" in text:
        text = text[text.find("(") + 1:text.find(")")].strip()
    elif "·" in text:
        text = text.rsplit("·", 1)[1].strip()
    lowered = text.casefold()
    legacy = {"4k": 3840, "8k": 7680, "16k": 16384, "32k": 32768}
    if lowered in legacy:
        return legacy[lowered]
    if lowered.endswith("k"):
        parsed_k = _number(lowered[:-1])
        if parsed_k is not None:
            parsed = int(parsed_k * 1024)
            return parsed if OUTPUT_SIZE_MIN <= parsed <= OUTPUT_SIZE_MAX else 4096
    parsed = _number(text, integer=True)
    if parsed is None or not OUTPUT_SIZE_MIN <= parsed <= OUTPUT_SIZE_MAX:
        return 4096
    return parsed


def normalize_output_profile(raw: Mapping[str, Any] | None,
                             target: str) -> dict[str, Any]:
    """返回完整目标配置，并修复保存数据中的无效值。"""
    result = default_output_profile(target)
    if not isinstance(raw, Mapping):
        return result
    size = _number(raw.get("size", _MISSING), integer=True)
    if size is not None and OUTPUT_SIZE_MIN <= size <= OUTPUT_SIZE_MAX:
        result["size"] = size
    rotation = _number(raw.get("rotation", _MISSING), integer=True)
    if rotation is not None and rotation % 360 in OUTPUT_ROTATIONS:
        result["rotation"] = output_rotation(rotation)
    return result


def normalize_output_profiles(settings_values: Mapping[str, Any] | None) -> dict[str, Any]:
    """在设置副本中分别规范化两个 PNG 输出目标；已有目标值优先，旧版全局值仅首次填充缺失配置。"""
    values = deepcopy(dict(settings_values or {}))
    raw_store = values.get(OUTPUT_PROFILE_SETTING_KEY)
    if not isinstance(raw_store, Mapping):
        raw_store = {}
    try:
        version = int(raw_store.get("schema_version", OUTPUT_PROFILE_SCHEMA_VERSION))
    except (TypeError, ValueError, OverflowError):
        version = OUTPUT_PROFILE_SCHEMA_VERSION
    if version > OUTPUT_PROFILE_SCHEMA_VERSION:
        raise ValueError(
            f"output profile schema version {version} is newer than supported "
            f"version {OUTPUT_PROFILE_SCHEMA_VERSION}")

    size = _legacy_size(values.get("size_var", 4096))
    rotation = _number(str(values.get("rotation_var", "0")).strip().rstrip("°"),
                       integer=True)
    if rotation is None:
        rotation = 0
    legacy = {"size": size, "rotation": rotation % 360}
    normalized: dict[str, Any] = {
        "schema_version": OUTPUT_PROFILE_SCHEMA_VERSION,
    }
    for target in OUTPUT_PROFILE_TARGETS:
        saved = raw_store.get(target)
        if not isinstance(saved, Mapping):
            saved = {}
        seeded = {**legacy, **dict(saved), "rotation": legacy["rotation"]}
        normalized[target] = normalize_output_profile(seeded, target)
    values[OUTPUT_PROFILE_SETTING_KEY] = normalized
    return values


def validate_output_profile(profile: Mapping[str, Any],
                            target: str) -> dict[str, Any]:
    """允许局部更新，但拒绝用户提交的无效配置。"""
    if target not in OUTPUT_PROFILE_TARGETS:
        raise OutputProfileValidationError(
            f"unknown output profile target: {target!r}")
    if not isinstance(profile, Mapping):
        raise OutputProfileValidationError("output profile must be an object")
    result = normalize_output_profile(profile, target)
    if "size" in profile:
        size = _number(profile["size"], integer=True)
        if size is None or not OUTPUT_SIZE_MIN <= size <= OUTPUT_SIZE_MAX:
            raise OutputProfileValidationError(
                f"输出像素边长必须为 {OUTPUT_SIZE_MIN}–{OUTPUT_SIZE_MAX} 的整数")
        result["size"] = size
    if "rotation" in profile:
        rotation = _number(profile["rotation"], integer=True)
        if rotation is None or rotation % 360 not in OUTPUT_ROTATIONS:
            raise OutputProfileValidationError("旋转角度必须为 0、90、180 或 270 度")
        result["rotation"] = output_rotation(rotation)
    return result
