"""文件读取时隔离坏记录；模型的内部构造仍保持严格。"""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from math import isfinite

from .model import MapIssue, NetworkSegment, ObjectRef


COLLECTION_KINDS = {
    "nodes": "node", "networks": "network", "buildings": "building",
    "districts": "district", "stops": "stop", "transit_lines": "line",
    "routes": "route", "forests": "forest", "procedural_objects": "procedural_object",
    "issues": "issue", "stations": "station", "pois": "poi",
    "facility_groups": "facility_group", "labels": "label", "water_bodies": "water_body",
}
_STRING_IDS = {"station", "poi", "facility_group"}
_POINT_FIELDS = {
    "node": ("pos",), "procedural_object": ("pos",), "stop": ("position",),
    "station": ("position",), "poi": ("anchor",), "facility_group": ("anchor",),
    "district": ("position",), "label": ("position",),
}
_GEOMETRY_FIELDS = {
    "network": ("geometry", "footprint"), "building": ("footprint",),
    "district": ("boundary", "grid_points"), "facility_group": ("boundary",),
    "water_body": ("polygon",),
}
_OBJECT_FIELDS = {"network": ("lane_summary",), "stop": ("station_group",),
                  "poi": ("primary_ref", "source"), "label": ("ref",)}
_CHILD_FIELDS = {"issue": (("refs", "ref"),), "station": (("facilities", "facility"),),
                 "route": (("legs", "leg"),)}


def _point(value, dimensions):
    if isinstance(value, dict):
        components = [value.get(key, 0) for key in (("x", "z") if dimensions == 2 else ("x", "y", "z"))]
    elif isinstance(value, list) and len(value) >= dimensions:
        components = value[:dimensions]
    else:
        raise ValueError(f"expected a {dimensions}D coordinate")
    if not all(isfinite(float(component)) for component in components):
        raise ValueError("coordinates must be finite")


def _record_shape(kind, value):
    if not isinstance(value, dict):
        raise ValueError("record must be an object")
    for key in _POINT_FIELDS.get(kind, ()):
        if key not in value or value[key] is None:
            continue
        if kind == "label" and key == "position":
            if not isinstance(value[key], (dict, str)):
                raise ValueError("label position must be an object or anchor kind")
        else:
            _point(value[key], 2 if kind == "district" and key == "position" else 3)
    for key in _GEOMETRY_FIELDS.get(kind, ()):
        if key not in value or value[key] is None:
            continue
        points = value[key]
        if isinstance(points, dict):
            points = points.get("points", [])
        if not isinstance(points, list):
            raise ValueError(f"{key} must contain an array of coordinates")
        for point in points:
            _point(point, 3 if key == "geometry" else 2)
    for key in _OBJECT_FIELDS.get(kind, ()):
        if key in value and value[key] is None and key in {"primary_ref", "ref"}:
            raise ValueError(f"{key} must be an object")
        if key in value and value[key] is not None and not isinstance(value[key], dict):
            raise ValueError(f"{key} must be an object")
    if kind == "stop" and value.get("station_group") is not None and "entity_id" not in value["station_group"]:
        raise ValueError("station_group is missing entity_id")
    if kind == "poi" and "primary_ref" not in value and "source" in value and value["source"] is None:
        raise ValueError("source must be an object")
    for key, child_kind in _CHILD_FIELDS.get(kind, ()):
        if key not in value or value[key] is None:
            continue
        if not isinstance(value[key], list):
            raise ValueError(f"{key} must be an array")
        for child in value[key]:
            _record_shape(child_kind, child)
            if child_kind == "leg" and not {"from_stop_index", "to_stop_index", "path_segment_ids"} <= child.keys():
                raise ValueError("route leg is missing stop indices or path segments")
    if kind == "water_body" and "depth_range" in value:
        depth = value["depth_range"]
        if not isinstance(depth, list) or len(depth) < 2:
            raise ValueError("depth_range must contain two values")


class ReadRecovery:
    def __init__(self):
        self.issues = []
        self.removed = defaultdict(set)

    def skip(self, kind, object_id, location, reason, *, dependency=False):
        refs = []
        if object_id is not None:
            if kind in _STRING_IDS:
                self.removed[kind].add(str(object_id))
            # 非数值的损坏 ID 仍保留在位置说明中，不能伪造成可引用的对象。
            try:
                ref = ObjectRef(kind, object_id)
            except (ValueError, TypeError, OverflowError):
                pass
            else:
                refs.append(ref)
                if kind not in _STRING_IDS:
                    self.removed[kind].add(ref.id)
        self.issues.append(MapIssue("info", "READ_DEPENDENCY_SKIPPED" if dependency else "READ_RECORD_SKIPPED",
                                    f"{location} (id={object_id!r}): {reason}", refs))

    @contextmanager
    def record(self, kind, object_id, location):
        try:
            yield
        except (ValueError, TypeError, OverflowError) as error:
            self.skip(kind, object_id, location, str(error))

    def read(self, name, values, model):
        result = []
        kind = COLLECTION_KINDS[name]
        for index, raw in enumerate(values):
            object_id = raw.get("id") if isinstance(raw, dict) else None
            with self.record(kind, object_id, f"{name}[{index}]"):
                _record_shape(kind, raw)
                item = model.from_dict(raw)
                if isinstance(item, NetworkSegment) and item.is_road and item.level is None:
                    raise ValueError("road has no valid road level")
                result.append(item)
        return result

    def finish(self, document):
        if not self.issues:
            return
        for name, kind in COLLECTION_KINDS.items():
            owner = document.derived if name in {"stations", "pois", "facility_groups", "labels", "water_bodies"} else document
            if name not in {"issues", "forests"}:
                # 相同 ID 仍有成功记录时，引用可以继续解析到有效对象。
                self.removed[kind].difference_update(
                    str(item.id) if kind in _STRING_IDS else item.id for item in getattr(owner, name))

        def discard(items, kind, affected):
            kept = []
            for item in items:
                if affected(item):
                    self.skip(kind, item.id, kind, "required data was skipped", dependency=True)
                else:
                    kept.append(item)
            return kept

        # 只清理由本次读取跳过引起的引用损失，保留既有格式的可选关联语义。
        while True:
            before = len(document.networks)
            document.networks = discard(document.networks, "network", lambda item:
                item.start_id in self.removed["node"] or item.end_id in self.removed["node"]
                or bool(set(item.path_segment_ids) & self.removed["network"]))
            if len(document.networks) == before:
                break
        document.stops = discard(document.stops, "stop", lambda item:
            item.network_node_id in self.removed["node"])
        document.routes = discard(document.routes, "route", lambda item:
            item.line_id in self.removed["line"]
            or bool(set(item.stop_ids) & self.removed["stop"])
            or bool(set(item.path_segment_ids) & self.removed["network"])
            or any(set(leg.path_segment_ids) & self.removed["network"] for leg in item.legs or ()))
        document.derived.stations = discard(document.derived.stations, "station", lambda item:
            bool(set(item.member_stop_ids) & self.removed["stop"])
            or item.building_id in self.removed["building"]
            or any(set(link.building_ids) & self.removed["building"] for link in item.facilities))
        while True:
            before = len(document.derived.facility_groups)
            document.derived.facility_groups = discard(document.derived.facility_groups, "facility_group", lambda item:
                bool(set(item.member_building_ids) & self.removed["building"])
                or bool(set(item.root_building_ids) & self.removed["building"])
                or bool(set(item.source_zone_ids) & self.removed["district"])
                or item.parent_group_id in self.removed["facility_group"])
            if len(document.derived.facility_groups) == before:
                break
        while True:
            before = len(document.derived.pois)
            document.derived.pois = discard(document.derived.pois, "poi", lambda item:
                (str(item.primary_ref.id) if item.primary_ref.kind in _STRING_IDS else item.primary_ref.id)
                in self.removed[item.primary_ref.kind]
                or item.group_id in self.removed["facility_group"]
                or item.parent_poi_id in self.removed["poi"])
            if len(document.derived.pois) == before:
                break
        document.derived.labels = discard(document.derived.labels, "label", lambda item:
            (str(item.ref_id) if item.ref_kind in _STRING_IDS else item.ref_id) in self.removed[item.ref_kind])
        for item in document.districts:
            if item.owner_building_id in self.removed["building"]:
                item.owner_building_id = None
        for item in document.buildings:
            if item.parent_id in self.removed["building"]:
                item.parent_id = None
            item.child_ids = [key for key in item.child_ids if key not in self.removed["building"]]
            if str(item.station_id) in self.removed["station"]:
                item.station_id = None
            if item.area_id in self.removed["district"]:
                item.area_id = None
        for item in document.stops:
            item.serving_line_ids = [key for key in item.serving_line_ids if key not in self.removed["line"]]
        for item in document.derived.stations:
            item.serving_line_ids = [key for key in item.serving_line_ids if key not in self.removed["line"]]
        for item in document.derived.pois:
            item.serving_line_ids = [key for key in item.serving_line_ids if key not in self.removed["line"]]
        for name in ("nodes", "networks", "buildings", "districts", "stops", "transit_lines", "routes",
                     "forests", "procedural_objects"):
            if name in document.metadata.counters:
                document.metadata.counters[name] = len(getattr(document, name))
        for issue in self.issues:
            document._append_issue(issue)
