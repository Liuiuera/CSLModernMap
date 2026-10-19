"""按照数据规则将游戏资源分类为 POI。"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from .mapping_registry import PoiMappingRegistry, load_mapping_registry


def _text(value: Any) -> str:
    return str(value or "").strip().casefold()


def functional_area_kind(value: Any) -> str:
    """保留设施分组与 POI 图标共享的区域语义。"""
    raw = "".join(ch for ch in _text(getattr(value, "value", value)) if ch.isalnum())
    return {
        "airport": "airport",
        "campus": "campus",
        "genericcampus": "campus",
        "tradeschool": "campus",
        "liberalarts": "campus",
        "university": "university",
        "citypark": "park",
        "park": "park",
        "generic": "park",
        "zoo": "zoo",
        "amusement": "amusement",
        "amusementpark": "amusement",
        "naturereserve": "nature_reserve",
        "nature": "nature_reserve",
        "forest": "nature_reserve",
        "industry": "industry",
        "industrial": "industry",
        "farming": "industry",
        "forestry": "industry",
        "ore": "industry",
        "oil": "industry",
        "ship": "ship",
        "harbor": "ship",
        "harbour": "ship",
        "cargoharbor": "cargo_harbor",
        "cargoharbour": "cargo_harbor",
        "pedestrian": "pedestrian",
        "pedestrianarea": "pedestrian",
        "pedestrianzone": "pedestrian",
    }.get(raw, "")


@dataclass(frozen=True)
class AssetClassification:
    """返回供诊断使用的详细分类结果。"""

    priority: int
    kind: str
    rule_id: str = ""
    source_raw: str = ""

    def as_tuple(self) -> tuple[int, str]:
        return self.priority, self.kind

    def __iter__(self):
        yield self.priority
        yield self.kind


class AssetClassifier:
    """通过标准映射表分类导出的建筑。"""

    def __init__(self, mapping: PoiMappingRegistry | None = None) -> None:
        self.mapping = mapping or load_mapping_registry()

    @property
    def output_kinds(self) -> tuple[str, ...]:
        return tuple(self.mapping.poi_kinds)

    def classify_building_result(
        self, icls: str = "", service: str = "", sub_service: str = "",
    ) -> AssetClassification:
        values = {
            "icls": str(icls or ""),
            "service": str(service or ""),
            "sub_service": str(sub_service or ""),
            "subsrv": str(sub_service or ""),
        }
        source_raw = " ".join(item for item in (
            values["service"], values["sub_service"], values["icls"]
        ) if item)

        mapping_entry = self.mapping.lookup_building(
            values["icls"], values["service"], values["sub_service"])
        if mapping_entry is not None:
            if mapping_entry.display == "hidden":
                return AssetClassification(0, "", "mapping:hidden", source_raw)
            kind = mapping_entry.poi_kind
            spec = self.mapping.require_kind(kind)
            return AssetClassification(
                priority=spec.priority,
                kind=kind,
                rule_id="mapping:" + kind,
                source_raw=source_raw,
            )
        other = self.mapping.require_kind("other")
        return AssetClassification(other.priority, "other", "unmapped", source_raw)

    def classify_building(
        self, icls: str = "", service: str = "", sub_service: str = "",
    ) -> tuple[int, str]:
        return self.classify_building_result(icls, service, sub_service).as_tuple()

    def classify_building_object(self, building: Any) -> tuple[int, str]:
        icls = getattr(building, "icls", "")
        service = getattr(building, "service", "")
        sub_service = getattr(building, "sub_service", "")
        return self.classify_building(icls, service, sub_service)

    def classify_park(self, park_type: str = "") -> tuple[int, str]:
        kind = functional_area_kind(park_type)
        if kind in ("", "pedestrian"):
            return 0, ""
        kind = {"campus": "university", "nature_reserve": "park"}.get(kind, kind)
        return self.mapping.require_kind(kind).priority, kind

    def classify_transport(self, transport_type: Any = "") -> tuple[int, str]:
        raw = _text(getattr(transport_type, "value", transport_type))
        for prefix in ("publictransport", "public transport"):
            if raw.startswith(prefix):
                raw = raw[len(prefix):].strip()
        if raw in {"plane", "airplane", "blimp", "air"}:
            kind = "airport"
        elif raw in {"train", "rail"}:
            kind = "train"
        elif raw in {"metro", "subway"}:
            kind = "metro"
        elif raw in {"ship", "ferry", "boat"}:
            kind = "ship"
        elif raw == "bus":
            kind = "bus"
        elif raw in {"tram", "monorail", "trolleybus", "helicopter"}:
            kind = raw
        elif raw in {"cablecar", "cable car", "cable_car"}:
            kind = "cable_car"
        else:
            return self.mapping.require_kind("other").priority, "other"
        return self.mapping.require_kind(kind).priority, kind

    def classify(self, *, icls: str = "", service: str = "", sub_service: str = "") -> tuple[int, str]:
        return self.classify_building(icls, service, sub_service)


_default_classifier: AssetClassifier | None = None


def _get_default_classifier() -> AssetClassifier:
    global _default_classifier
    if _default_classifier is None:
        _default_classifier = AssetClassifier()
    return _default_classifier


@lru_cache(maxsize=None)
def _classify_building_cached(
    icls: str, service: str, sub_service: str,
) -> tuple[int, str]:
    return _get_default_classifier().classify_building(
        icls, service, sub_service)


def classify_building_poi(
    item_class: str, service: str, sub_service: str,
) -> tuple[int, str]:
    """使用映射表分类建筑，并缓存重复的 BuildingAI 类型。"""
    return _classify_building_cached(
        str(item_class or ""), str(service or ""), str(sub_service or ""))
