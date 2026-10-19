"""定义独立于输出分辨率的地图内容密度规则。"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import FrozenSet, Optional

from .services.poi.catalog import load_catalog


class DetailMode(str, Enum):
    AUTO = "auto"
    OVERVIEW = "overview"
    STANDARD = "standard"
    DETAILED = "detailed"


OVERVIEW_TRANSIT_TYPES: FrozenSet[str] = frozenset({
    "Metro", "Train", "Monorail", "Ship", "Ferry", "Airplane",
    "Helicopter",
})
STANDARD_TRANSIT_TYPES: FrozenSet[str] = frozenset(
    set(OVERVIEW_TRANSIT_TYPES) | {"Tram", "Trolleybus", "CableCar"}
)


@dataclass(frozen=True)
class DetailPolicy:
    mode: DetailMode
    abstract_level: int
    building_min_area: Optional[float]
    important_building_priority: Optional[int]
    poi_min_priority: Optional[int]
    poi_grid_multiplier: float
    poi_max_per_cell: Optional[int]
    show_road_names: bool
    show_park_names: bool
    transit_types: Optional[FrozenSet[str]]
    procedural_object_min_area: Optional[float]


def normalize_detail_mode(value) -> DetailMode:
    if isinstance(value, DetailMode):
        return value
    try:
        return DetailMode(str(value or "auto").strip().lower())
    except ValueError:
        return DetailMode.AUTO


def automatic_abstract_level(zoom: float) -> int:
    """保留自动模式沿用的输出密度阈值。"""
    if zoom >= 1.0:
        return 0
    if zoom >= 0.5:
        return 1
    if zoom >= 0.25:
        return 2
    return 3


def _policy_for_level(mode: DetailMode, level: int) -> DetailPolicy:
    if level <= 0:
        return DetailPolicy(
            mode, 0, 0.0, None, None, 1.0, None, True, True, None, 0.0,
        )
    if level == 1:
        return DetailPolicy(
            mode, 1, 200.0, 35, 35, 1.0, 2, True, True,
            STANDARD_TRANSIT_TYPES, 1.0,
        )
    if level == 2:
        return DetailPolicy(
            mode, 2, 800.0, 50, 50, 1.5, 1, True, False,
            OVERVIEW_TRANSIT_TYPES, 25.0,
        )
    return DetailPolicy(
        mode, 3, None, None, None, 1.0, 0, False, False,
        frozenset(), None,
    )


def _legacy_auto_policy(level: int) -> DetailPolicy:
    """保持自动、网页及分块输出与旧版缩放细节规则一致。"""
    if level <= 0:
        return _policy_for_level(DetailMode.AUTO, 0)
    if level == 1:
        return DetailPolicy(
            DetailMode.AUTO, 1, 0.0, None, None, 1.0, None, False, False,
            None, 0.0,
        )
    if level == 2:
        return DetailPolicy(
            DetailMode.AUTO, 2, 800.0, None, None, 1.0, 0, False, False,
            None, 1.0,
        )
    return DetailPolicy(
        DetailMode.AUTO, 3, None, None, None, 1.0, 0, False, False,
        None, None,
    )


def resolve_detail_policy(mode, final_zoom: float) -> DetailPolicy:
    normalized = normalize_detail_mode(mode)
    if normalized is DetailMode.AUTO:
        level = automatic_abstract_level(final_zoom)
        return _legacy_auto_policy(level)
    else:
        level = {
            DetailMode.DETAILED: 0,
            DetailMode.STANDARD: 1,
            DetailMode.OVERVIEW: 2,
        }[normalized]
    return _policy_for_level(normalized, level)


# POI 优先级由目录提供，此映射仅供现有渲染代码只读兼容。
POI_PRIORITY = load_catalog().priorities


def classify_building_poi(
    item_class: str, service: str, sub_service: str,
) -> tuple[int, str]:
    """为目录驱动的分类器提供兼容入口。"""
    from .services.poi.classifier import classify_building_poi as _classify

    return _classify(item_class, service, sub_service)
