"""统一道路语义等级和物理宽度计算。"""
from __future__ import annotations

from enum import Enum


class RoadLevel(str, Enum):
    HIGHWAY = "highway"
    MAIN = "main"
    BRANCH = "branch"
    SPECIAL = "special"
    QUAY = "quay"
    BEAUTIFICATION = "beautification"


ROAD_LEVEL_RANK = {
    RoadLevel.QUAY: 0,
    RoadLevel.BEAUTIFICATION: 1,
    RoadLevel.SPECIAL: 2,
    RoadLevel.BRANCH: 3,
    RoadLevel.MAIN: 4,
    RoadLevel.HIGHWAY: 5,
}

_SPECIAL_TERMS = (
    "airport",
    "airplane",
    "runway",
    "taxiway",
    "concourse",
    "busstop",
    "shipdock",
)


def _normalized(*values: str) -> str:
    return "".join(
        ch for value in values for ch in (value or "").casefold()
        if ch.isalnum()
    )


def is_special_road(asset_name: str = "", item_class: str = "") -> bool:
    value = _normalized(asset_name, item_class)
    return any(term in value for term in _SPECIAL_TERMS)


def is_quay_network(*values: str) -> bool:
    return "quay" in _normalized(*values)


def segment_physical_width(segment, segment_type) -> float:
    """返回导出的道路宽度，缺失时使用车道包络宽度。"""
    width = float(getattr(segment, "width", 0.0) or 0.0)
    if width > 0.0:
        return width
    return max(float(getattr(segment_type, "width", 0.0) or 0.0), 0.0)


def classify_segment(segment, segment_type) -> RoadLevel:
    """按项目统一规则对地图路段分级。"""
    if bool(getattr(segment_type, "highway", False)):
        return RoadLevel.HIGHWAY
    if is_special_road(
        getattr(segment, "name", ""), getattr(segment, "icls", "")
    ):
        return RoadLevel.SPECIAL
    beautification = bool(getattr(segment_type, "beautification", False))
    if beautification and is_quay_network(
            getattr(segment, "name", ""), getattr(segment, "icls", ""),
            getattr(segment_type, "name", "")):
        return RoadLevel.QUAY
    if beautification:
        return RoadLevel.BEAUTIFICATION

    car_lanes = int(getattr(segment_type, "car_lane_count", 0) or 0)
    if car_lanes > 0:
        return RoadLevel.MAIN if car_lanes >= 4 else RoadLevel.BRANCH
    return (
        RoadLevel.MAIN
        if segment_physical_width(segment, segment_type) >= 20.0
        else RoadLevel.BRANCH
    )


def classify_traffic_segment(segment) -> RoadLevel:
    """对交通快照路段应用同一等级规则。"""
    stats = getattr(segment, "seg_stats", None)
    if bool(getattr(stats, "highway", False)):
        return RoadLevel.HIGHWAY
    if is_special_road(
        getattr(segment, "road_name", ""), getattr(segment, "item_class", "")
    ):
        return RoadLevel.SPECIAL

    car_lanes = int(getattr(stats, "vehicle_lanes", 0) or 0)
    if car_lanes <= 0:
        car_lanes = sum(
            1 for lane in getattr(segment, "lanes", ())
            if getattr(lane, "is_vehicle", False)
        )
    if car_lanes > 0:
        return RoadLevel.MAIN if car_lanes >= 4 else RoadLevel.BRANCH
    return (
        RoadLevel.MAIN
        if float(getattr(segment, "width", 0.0) or 0.0) >= 20.0
        else RoadLevel.BRANCH
    )


def road_level_rank(level: RoadLevel) -> int:
    return ROAD_LEVEL_RANK[level]


_STYLE_ALIASES = {
    RoadLevel.HIGHWAY: ("highway",),
    RoadLevel.MAIN: ("main", "primary", "road"),
    RoadLevel.BRANCH: ("branch", "tertiary", "secondary", "road"),
    RoadLevel.SPECIAL: ("special", "branch", "road"),
    RoadLevel.QUAY: ("quay", "beautification", "pedestrian"),
    RoadLevel.BEAUTIFICATION: ("beautification", "pedestrian"),
}


def road_style_candidates(level: RoadLevel, mode: str = "ground") -> tuple[str, ...]:
    """按道路和路面模式依次返回标准及旧版样式键。"""
    bases = _STYLE_ALIASES[level]
    suffix = "_tunnel" if mode == "tunnel" else (
        "_elevated" if mode == "elevated" else ""
    )
    candidates = [base + suffix for base in bases] if suffix else []
    candidates.extend(bases)
    return tuple(dict.fromkeys(candidates))


def road_style_config(roads, level, mode="ground"):
    candidates = road_style_candidates(level, mode)
    if level is RoadLevel.QUAY:
        # 单独调整护岸比例时，未指定的颜色仍继承景观道路。
        cfg = {}
        for key in reversed(candidates):
            cfg.update(roads.get(key, {}))
        return cfg
    return next((roads[key] for key in candidates if roads.get(key)), {})


def visible_at_abstract_level(
    level: RoadLevel, abstract_level: int, *, has_highways: bool = True,
) -> bool:
    if abstract_level <= 0:
        return True
    if abstract_level == 1:
        return level not in (RoadLevel.QUAY, RoadLevel.BEAUTIFICATION)
    if abstract_level == 2:
        return level in (RoadLevel.HIGHWAY, RoadLevel.MAIN)
    if has_highways:
        return level is RoadLevel.HIGHWAY
    return level is RoadLevel.MAIN
