"""统一地图版本 4.1 的数据语义与诊断适配。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from ..data import Segment as LegacySegment
from ..transit import normalize_type
from .model import BuildingRole, DisplayName, NetworkKind, StopKind, TransitKind, ZoneKind


_TYPE_TO_TRANSIT = {
    "bus": TransitKind.BUS,
    "trolleybus": TransitKind.TROLLEYBUS,
    "tram": TransitKind.TRAM,
    "metro": TransitKind.METRO,
    "train": TransitKind.TRAIN,
    "monorail": TransitKind.MONORAIL,
    "cablecar": TransitKind.CABLECAR,
    "ship": TransitKind.SHIP,
    "ferry": TransitKind.FERRY,
    "airplane": TransitKind.AIRPLANE,
    "helicopter": TransitKind.HELICOPTER,
}


_TRANSIT_TO_STOP = {item: StopKind[item.name] for item in TransitKind if item.name != "OTHER"}


_LINE_KEYWORDS = (
    ("cable car line", TransitKind.CABLECAR),
    ("cablecar line", TransitKind.CABLECAR),
    ("monorail line", TransitKind.MONORAIL),
    ("metro line", TransitKind.METRO),
    ("train line", TransitKind.TRAIN),
    ("tram line", TransitKind.TRAM),
    ("bus line", TransitKind.BUS),
)


_BUILDING_ROLE_TERMS = (
    (BuildingRole.AIRPORT, ("airport", "airplane", "blimp")),
    (BuildingRole.TRANSIT_STATION, ("publictransport", "station", "metro", "train", "tram", "monorail")),
    (BuildingRole.EDUCATION, ("school", "university", "education")),
    (BuildingRole.HEALTHCARE, ("hospital", "healthcare", "clinic")),
    (BuildingRole.EMERGENCY, ("police", "fire", "emergency")),
    (BuildingRole.PARK_SERVICE, ("park", "zoo", "amusement")),
    (BuildingRole.RESIDENTIAL, ("residential", "house", "apartment")),
    (BuildingRole.COMMERCIAL, ("commercial", "shop", "commercial")),
    (BuildingRole.INDUSTRIAL, ("industrial", "industry", "factory")),
    (BuildingRole.OFFICE, ("office",)),
    (BuildingRole.INFRASTRUCTURE, ("power", "water", "sewage", "sewer", "garbage", "transport")),
    (BuildingRole.LEISURE, ("leisure", "stadium", "park")),
)


_ZONE_TERMS = (
    (ZoneKind.FARMING, ("farming", "farm")),
    (ZoneKind.FORESTRY, ("forestry", "forest")),
    (ZoneKind.ORE, ("ore",)),
    (ZoneKind.OIL, ("oil",)),
    (ZoneKind.HIGH_DENSITY, ("high", "highdensity")),
    (ZoneKind.RESIDENTIAL, ("residential",)),
    (ZoneKind.COMMERCIAL, ("commercial",)),
    (ZoneKind.INDUSTRIAL, ("industrial",)),
    (ZoneKind.OFFICE, ("office",)),
    (ZoneKind.CAMPUS, ("campus", "university", "tradeschool", "liberalarts")),
    (ZoneKind.AIRPORT, ("airport",)),
    (ZoneKind.INDUSTRY, ("industry",)),
    (ZoneKind.ZOO, ("zoo",)),
    (ZoneKind.AMUSEMENT, ("amusement",)),
    (ZoneKind.NATURE_RESERVE, ("nature", "reserve")),
    (ZoneKind.PARK, ("park", "generic")),
)


@dataclass(frozen=True)
class AdapterCapabilities:
    source_version: str = "4.1"
    bidirectional: bool = False
    platforms: bool = False
    unknown_types: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_version": self.source_version,
            "bidirectional": self.bidirectional,
            "platforms": self.platforms,
            "unknown_types": self.unknown_types,
        }


def _compact(value: str) -> str:
    return "".join(ch for ch in (value or "").casefold() if ch.isalnum())


def _source_text(value: str) -> str:
    return (value or "").strip()


def _display_name(value: str) -> DisplayName:
    text = _source_text(value)
    return DisplayName(text, "game" if text else "empty")


def _transit_kind(raw: str) -> TransitKind:
    normalized = normalize_type(raw or "")
    return _TYPE_TO_TRANSIT.get((normalized or raw or "").casefold(), TransitKind.OTHER)


def _stop_kind(raw: str, fallback: TransitKind | None = None) -> StopKind:
    value = _compact(raw)
    for key, kind in (
        ("publictransportbus", StopKind.BUS),
        ("publictransporttrolleybus", StopKind.TROLLEYBUS),
        ("publictransporttram", StopKind.TRAM),
        ("publictransportmetro", StopKind.METRO),
        ("publictransporttrain", StopKind.TRAIN),
        ("publictransportmonorail", StopKind.MONORAIL),
        ("publictransportcablecar", StopKind.CABLECAR),
        ("publictransportship", StopKind.SHIP),
        ("publictransportplane", StopKind.AIRPLANE),
        ("publictransportblimp", StopKind.AIRPLANE),
        ("publictransporthelicopter", StopKind.HELICOPTER),
    ):
        if value == key:
            return kind
    if fallback is not None:
        return _TRANSIT_TO_STOP.get(fallback, StopKind.OTHER)
    return StopKind.OTHER


def _network_kind(segment: LegacySegment, segment_type: Any) -> tuple[NetworkKind, TransitKind | None]:
    name = (segment.name or "").casefold()
    for keyword, transit_kind in _LINE_KEYWORDS:
        if keyword in name:
            return NetworkKind.STOP_LINE, transit_kind
    if segment_type is not None:
        if getattr(segment_type, "metro", False):
            return NetworkKind.METRO, None
        if getattr(segment_type, "monorail", False):
            return NetworkKind.MONORAIL, None
        if getattr(segment_type, "cablecar", False):
            return NetworkKind.CABLECAR, None
        if getattr(segment_type, "tram", False):
            return NetworkKind.TRAM, None
        if getattr(segment_type, "rail", False):
            return NetworkKind.RAIL, None
        if getattr(segment_type, "beautification", False):
            return NetworkKind.BEAUTIFICATION, None
        if getattr(segment_type, "road", False) or getattr(segment_type, "highway", False):
            return NetworkKind.ROAD, None
    return NetworkKind.OTHER, None


def _building_role(service: str, sub_service: str, icls: str) -> BuildingRole:
    text = _compact(" ".join((service, sub_service, icls)))
    for role, terms in _BUILDING_ROLE_TERMS:
        if any(term in text for term in terms):
            return role
    return BuildingRole.OTHER


def _zone_kind(raw: str) -> ZoneKind:
    text = _compact(raw)
    if not text or text == "none":
        return ZoneKind.ORDINARY
    for kind, terms in _ZONE_TERMS:
        if any(term in text for term in terms):
            return kind
    return ZoneKind.OTHER


def _rgba_or_none(value: tuple[int, int, int, int] | None) -> tuple[int, int, int, int] | None:
    if value is None:
        return None
    # 旧解析器按 ARGB 保存 XML 颜色，CMM 使用 RGBA。
    a, r, g, b = (int(item) for item in value)
    rgba = (r, g, b, a)
    return None if rgba == (255, 255, 255, 255) else rgba
