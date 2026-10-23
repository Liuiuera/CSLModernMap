"""将 CMM 站点与设施进行匹配。"""
from __future__ import annotations

import math
import re

from ...cmm import DisplayName, FacilityKind, StationFacilityLink


_RULES = {
    "airport": {"distance": 1000.0, "priority": 30,
                "words": ("airport", "airfield", "航站", "机场")},
    "rail": {"distance": 600.0, "priority": 20,
             "words": ("trainstation", "train station", "railway station",
                       "rail station", "火车站", "铁路客站", "客运站")},
    "port": {"distance": 600.0, "priority": 10,
             "words": ("ferry", "passenger harbor", "passenger port",
                       "passenger terminal", "码头", "客运港", "客运码头")},
}


def _text(item) -> str:
    return " ".join(str(getattr(item, key, "") or "") for key in
                    ("name", "custom_name", "icls", "service",
                     "sub_service", "ptype", "source_raw")).lower()


def _facility_kind(item) -> str | None:
    text = _text(item)
    if any(word in text for word in ("cargo", "freight", "货运", "工业港")):
        return None
    local_rail = any(word in text for word in
                     ("monorail", "metro", "subway", "light rail", "tram"))
    for kind, rule in _RULES.items():
        if kind == "rail" and local_rail:
            continue
        if any((word in text if not word.isascii() else
                re.search(r"(?<![a-z])" + re.escape(word) + r"(?![a-z])", text))
               for word in rule["words"]):
            return kind
    return None


def _points(item):
    values = getattr(item, "points", None)
    if values is not None:
        return values
    shape = getattr(item, "footprint", getattr(item, "boundary", None))
    return getattr(shape, "points", ()) if shape is not None else ()


def _centre(item) -> tuple[float, float] | None:
    points = list(_points(item) or ())
    if points:
        return (sum(float(point.x) for point in points) / len(points),
                sum(float(point.z) for point in points) / len(points))
    position = getattr(item, "position", None)
    if position is not None:
        return float(position.x), float(position.z)
    return None


def _name(item) -> str:
    custom = str(getattr(item, "custom_name", "") or "").strip()
    if custom:
        return custom
    display = getattr(item, "display_name", None)
    return str(getattr(display, "text", display) or getattr(item, "name", "") or "").strip()


def _merge_candidates(candidates, distances):
    merged = []
    for kind in _RULES:
        pending = [item for item in candidates if item[0] == kind]
        limit = max(float(distances.get(kind, 0.0)), 0.0)
        while pending:
            component = [pending.pop()]
            changed = True
            while changed:
                changed = False
                remaining = []
                for item in pending:
                    if any(math.hypot(item[1][0] - other[1][0],
                                      item[1][1] - other[1][1]) <= limit
                           for other in component):
                        component.append(item)
                        changed = True
                    else:
                        remaining.append(item)
                pending = remaining
            merged.append((
                kind,
                (sum(item[1][0] for item in component) / len(component),
                 sum(item[1][1] for item in component) / len(component)),
                next((item[2] for item in component if item[2]), ""),
                sorted({item[3] for item in component if item[3] is not None}),
            ))
    return merged


def match_facilities(mapdata, stations, *, distances=None, merge_distances=None
                     ) -> dict[str, StationFacilityLink]:
    """将每个客运设施关联到最合适的单个 CMM 站点。"""
    limits = {kind: float(rule["distance"]) for kind, rule in _RULES.items()}
    limits.update({kind: float(value) for kind, value in (distances or {}).items()
                   if kind in limits and float(value) > 0})
    merge_limits = {"airport": 1200.0, "rail": 300.0, "port": 400.0}
    merge_limits.update({kind: float(value) for kind, value in (merge_distances or {}).items()
                         if kind in merge_limits and float(value) > 0})

    sources = [*(getattr(mapdata, "buildings", ()) or ()),
               *(getattr(mapdata, "parks", ()) or ())]
    candidates = []
    for item in sources:
        kind, position = _facility_kind(item), _centre(item)
        if kind is not None and position is not None:
            raw_id = getattr(item, "id", None)
            candidates.append((kind, position, _name(item),
                               int(raw_id) if raw_id is not None else None))
    candidates = _merge_candidates(candidates, merge_limits)

    matches = []
    station_by_id = {str(station.id): station for station in stations}
    for candidate_id, (kind, (x, z), name, building_ids) in enumerate(candidates):
        limit = limits[kind]
        for station_id, station in station_by_id.items():
            distance = math.hypot(x - station.position.x, z - station.position.z)
            if distance <= limit:
                matches.append((-
                    int(_RULES[kind]["priority"]), distance / max(limit, 1.0),
                    distance, candidate_id, station_id, kind, name, building_ids,
                ))

    result: dict[str, StationFacilityLink] = {}
    used_candidates: set[int] = set()
    for (_priority, _normal, distance, candidate_id, station_id,
         kind, name, building_ids) in sorted(matches):
        if candidate_id in used_candidates or station_id in result:
            continue
        used_candidates.add(candidate_id)
        result[station_id] = StationFacilityLink(
            kind=FacilityKind(kind.upper()),
            name=DisplayName(name, "facility" if name else "empty"),
            building_ids=building_ids,
            distance=distance,
        )
    return result


match_station_facilities = match_facilities


class StationFacilityService:
    match = staticmethod(match_facilities)
