"""从原生道路身份编译米制导航数据。"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from math import dist, isfinite

SCHEMA_VERSION = 1
DURATION_MODEL = "car-speed-traffic-v2"
LEGACY_DURATION_MODEL = "car-speed-limit-v1"
DURATION_WAIT_FACTOR = 1.3
COST_MODEL = {"id": "car-balanced-v1", "referenceSpeedMps": 50 / 3.6,
              "congestionWeight": 4.0,
              "roadLevelFactors": {"HIGHWAY": 1.0, "MAIN": 1.05, "BRANCH": 1.15}}


def _positive_speed(value):
    return (float(value) if isinstance(value, (float, int)) and not isinstance(value, bool)
            and isfinite(value) and value > 0 else None)


def duration_speed(edge, model):
    limit = edge.get("speedLimitMps") or 60 / 3.6
    if model == LEGACY_DURATION_MODEL:
        return limit
    congestion = edge.get("congestion")
    return min(limit, 60 / 3.6 if congestion is None else max(5 / 3.6, limit * (1 - congestion)))


def _driving_facts(document):
    from ...traffic import TrafficSnapshot

    traffic = document.traffic
    if isinstance(traffic, dict):
        traffic = TrafficSnapshot.from_dict(traffic)
    if traffic is None:
        return {}, {}
    congestion, legacy_limits = {}, {}
    for segment in traffic.segments:
        ref = str(segment.segment_id)
        for direction, value in segment.directional_congestion().items():
            congestion[ref, direction == "Backward"] = value
        if document.metadata.origin.game == "cs1" and traffic.schema_version in ("1.1", "1.2"):
            # 已知旧 CS1 契约保存预设的游戏单位，不含 TM:PE 覆盖限速。
            for lane in segment.lanes:
                speed = _positive_speed(lane.max_speed)
                if not lane.is_vehicle or speed is None:
                    continue
                for reverse, direction in ((False, "Forward"), (True, "Backward")):
                    if lane.direction in (direction, "Both"):
                        key = ref, reverse
                        legacy_limits[key] = max(legacy_limits.get(key, 0), speed * 50 / 3.6)
    return congestion, legacy_limits


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False).encode("utf-8")).hexdigest()[:24]


def compile_navigation(document) -> dict:
    congestion, legacy_limits = _driving_facts(document)
    nodes = {str(node.id): [node.pos.x, node.pos.y, node.pos.z]
             for node in document.nodes}
    duplicate_nodes = {str(key) for key, count in Counter(
        node.id for node in document.nodes).items() if count > 1}
    duplicate_segments = {key for key, count in Counter(
        road.id for road in document.networks).items() if count > 1}
    excluded = Counter()
    geometries, edges, walking_roads = {}, [], []
    parent = {}

    def component(node):
        parent.setdefault(node, node)
        root = node
        while parent[root] != root:
            root = parent[root]
        while parent[node] != node:
            previous = node
            node = parent[node]
            parent[previous] = root
        return root

    road_count = direction_known = access_known = 0
    for road in sorted(document.networks, key=lambda item: item.id):
        if str(road.kind) != "ROAD":
            continue
        road_count += 1
        start, end = str(road.start_id), str(road.end_id)
        if road.id in duplicate_segments or start in duplicate_nodes or end in duplicate_nodes:
            excluded["duplicate_identity"] += 1
            continue
        if start not in nodes or end not in nodes:
            excluded["missing_endpoint"] += 1
            continue
        points = []
        for point in road.geometry.points:
            xyz = [point.x, point.y, point.z]
            if not points or xyz != points[-1]:
                points.append(xyz)
        if len(points) < 2 or not all(isfinite(v) for p in points + [nodes[start], nodes[end]] for v in p):
            excluded["invalid_geometry"] += 1
            continue
        if dist(points[0], nodes[start]) > 1 or dist(points[-1], nodes[end]) > 1:
            excluded["endpoint_geometry_mismatch"] += 1
            continue
        length = sum(dist(a, b) for a, b in zip(points, points[1:]))
        if length <= 0:
            excluded["zero_length"] += 1
            continue
        walking_roads.append({"id": str(road.id), "fromNode": start, "toNode": end,
                              "points": points, "name": road.display_name or road.game_name,
                              "mode": str(road.mode)})
        access = road.car_access
        if not isinstance(access, dict) or not isinstance(access.get("source"), str) or not access["source"]:
            excluded["car_access_unknown"] += 1
            continue
        values = [access.get("forward"), access.get("backward")]
        if any(not isinstance(value, str) or value not in {"allowed", "forbidden", "unknown"} for value in values):
            excluded["invalid_car_access"] += 1
            continue
        direction_known += not any(value == "unknown" for value in values)
        access_known += not any(value == "unknown" for value in values)
        if "allowed" not in values:
            excluded["car_forbidden" if "unknown" not in values else "car_access_unknown"] += 1
            continue
        source_id = str(road.id)
        geometries[source_id] = points
        for reverse, value in enumerate(values):
            if value != "allowed":
                continue
            speed, speed_source = None, None
            if road.car_speed is not None:
                facts = road.car_speed
                if (isinstance(facts, dict) and facts.get("unit") == "m/s"
                        and isinstance(facts.get("source"), str) and facts["source"]):
                    speed = _positive_speed(facts.get("backward" if reverse else "forward"))
                    speed_source = facts["source"] if speed is not None else None
            elif (source_id, bool(reverse)) in legacy_limits:
                speed, speed_source = legacy_limits[source_id, bool(reverse)], "cs1-prefab-legacy"
            jam = congestion.get((source_id, bool(reverse)))
            # 缺限速时速度因子保持中性；不把归一化基准写成道路限速。
            factor = COST_MODEL["referenceSpeedMps"] / speed if speed is not None else 1.0
            factor *= (1 + COST_MODEL["congestionWeight"] * jam**2) if jam is not None else 1.0
            factor *= COST_MODEL["roadLevelFactors"].get(str(road.level), 1.0)
            edges.append({
                "id": f"road:{source_id}:{'b' if reverse else 'f'}",
                "sourceSegmentId": source_id,
                "fromNode": end if reverse else start,
                "toNode": start if reverse else end,
                "geometryRef": source_id, "reverse": bool(reverse), "lengthM": length,
                "modeAccess": "allowed", "directionEvidence": access["source"],
                "name": road.display_name or road.game_name,
                "mode": str(road.mode), "level": str(road.level or ""),
                "widthM": float(road.width),
                "congestion": jam, "speedLimitMps": speed, "speedSource": speed_source,
                "recommendedCostPerMeter": factor,
            })
        parent[component(end)] = component(start)
    used = {node for edge in edges for node in (edge["fromNode"], edge["toNode"])}
    nodes = {node: nodes[node] for node in sorted(used)}
    components = {node: component(node) for node in nodes}
    cost_keys = {"congestion", "speedLimitMps", "speedSource", "recommendedCostPerMeter"}
    topology = [{key: value for key, value in edge.items() if key not in cost_keys | {"name"}}
                for edge in edges]
    from .transit import compile_transit
    transit = compile_transit(document, walking_roads)
    transit_topology = {**{k: v for k, v in transit.items() if k != "costModel"}, "edges": [{k: v for k, v in edge.items()
                        if k not in {"name", "fromStop", "toStop", "color"}} for edge in transit["edges"]]}
    graph_revision = _digest([nodes, geometries, topology, transit_topology])
    cost_revision = _digest([COST_MODEL, DURATION_MODEL, transit["costModel"],
        [{key: edge[key] for key in ("id", *sorted(cost_keys))} for edge in edges]])
    snapshot_id = _digest([graph_revision, cost_revision, [edge["name"] for edge in edges],
                           transit, document.metadata.exported_at, document.metadata.origin.to_dict()])

    def coverage(count):
        return "missing" if count == 0 else "complete" if count == road_count else "partial"

    return {
        "routingSchemaVersion": SCHEMA_VERSION,
        "cityId": f"snapshot:{snapshot_id}", "cityIdentity": "snapshot-only",
        "snapshotId": snapshot_id, "graphRevision": graph_revision, "costRevision": cost_revision,
        "costModel": COST_MODEL, "durationModel": DURATION_MODEL,
        "capturedAt": document.metadata.exported_at,
        "coordinates": {"unit": "meter", "axes": "x,y,z;y-up", "projection": "csl-local-v1"},
        "nodes": nodes, "geometries": geometries, "edges": edges, "components": components,
        "transit": transit,
        "capabilities": {"directedRoads": coverage(direction_known), "carAccess": coverage(access_known),
                         "turnRules": "missing", "walkingGraph": "bidirectional-roads",
                         "speedModel": "available" if any(e["speedLimitMps"] is not None for e in edges) else "missing",
                         "transitOrder": "exported" if any(e["mode"] == "RIDE" for e in transit["edges"]) else "missing"},
        "diagnostics": {"roadCount": road_count, "nodeCount": len(nodes), "edgeCount": len(edges),
                        "weakComponents": len(set(components.values())), "excluded": dict(excluded),
                        "trafficDirections": sum(e["congestion"] is not None for e in edges),
                        "speedLimitDirections": sum(e["speedLimitMps"] is not None for e in edges),
                        "legacySpeedDirections": sum(e["speedSource"] == "cs1-prefab-legacy" for e in edges),
                        "ruleScope": "native-car-lanes;turns-and-third-party-overrides-unverified"},
    }


def clip_navigation(package: dict, bounds, *, selected_route=None) -> dict:
    """边界只终止道路，不为切点建立共享路口。"""
    from shapely.geometry import LineString, Point, box

    region = box(bounds[0], bounds[1], bounds[2], bounds[3])
    result = {**package, "nodes": {}, "geometries": {}, "edges": [], "components": {},
              "exportRegion": list(bounds), "rangeLimited": True}
    parts_by_ref = {}
    edge_spans = {}
    for ref, xyz in package["geometries"].items():
        spans = []
        traversed = 0.0
        for p, q in zip(xyz, xyz[1:]):
            length = dist(p, q)
            if p[0] == q[0] and p[2] == q[2]:
                clipped_points = [p[:], q[:]] if region.covers(Point(p[0], p[2])) else []
            else:
                line = LineString([(p[0], p[2]), (q[0], q[2])])
                clipped = line.intersection(region)
                clipped_points = []
                if clipped.geom_type == "LineString" and not clipped.is_empty:
                    for coordinate in clipped.coords:
                        t = line.project(Point(coordinate)) / line.length
                        clipped_points.append([p[k] + t*(q[k]-p[k]) for k in range(3)])
            if len(clipped_points) >= 2:
                a = traversed + dist(p, clipped_points[0])
                b = traversed + dist(p, clipped_points[-1])
                if spans and abs(spans[-1][1]-a) <= 1e-7:
                    spans[-1][2].extend(clipped_points[1:])
                    spans[-1] = (spans[-1][0], b, spans[-1][2])
                else:
                    spans.append((a, b, clipped_points))
            traversed += length
        parts_by_ref[ref] = spans
    for edge in package["edges"]:
        ref = edge["geometryRef"]
        original = package["geometries"][ref]
        total = sum(dist(p, q) for p, q in zip(original, original[1:]))
        for index, (a, b, points) in enumerate(parts_by_ref[ref]):
            whole = a <= 1e-7 and b >= total - 1e-7
            part_ref = ref if whole else f"{ref}:clip:{index}"
            edge_id = edge["id"] if whole else f"{edge['id']}:clip:{index}"
            original_start = edge["toNode"] if edge["reverse"] else edge["fromNode"]
            original_end = edge["fromNode"] if edge["reverse"] else edge["toNode"]
            start = original_start if a <= 1e-7 else f"boundary:{part_ref}:start"
            end = original_end if b >= total - 1e-7 else f"boundary:{part_ref}:end"
            result["nodes"].update({start: points[0], end: points[-1]})
            result["geometries"][part_ref] = points
            result["edges"].append({**edge, "id": edge_id,
                "geometryRef": part_ref, "fromNode": end if edge["reverse"] else start,
                "toNode": start if edge["reverse"] else end,
                "lengthM": sum(dist(p, q) for p, q in zip(points, points[1:]))})
            low, high = ((1-b/total, 1-a/total) if edge["reverse"] else (a/total, b/total)) if total else (0, 1)
            edge_spans.setdefault(edge["id"], []).append((edge_id, low, high))
    result["graphRevision"] = _digest([package["graphRevision"], bounds])
    result["diagnostics"] = {**package["diagnostics"], "edgeCount": len(result["edges"]),
                              "nodeCount": len(result["nodes"]), "weakComponents": None}
    if "transit" in package:
        network = {**package, **package["transit"]}
        del network["transit"]
        clipped = clip_navigation(network, bounds)
        result["transit"] = {key: clipped[key] for key in ("nodes", "geometries", "edges", "costModel")}
    if selected_route is not None:
        result["selectedRoute"] = _clip_selected_route(package, result, edge_spans, selected_route, bounds)
    return result


def _clip_selected_route(original, clipped, spans, route, bounds):
    import copy
    from .route import validate_route

    if route.get("costProfile") == "transit" and "transit" in original:
        network = {**original, **original["transit"]}
        del network["transit"]
        return clip_navigation(network, bounds, selected_route=route)["selectedRoute"]

    if any(not (bounds[0] <= p[0] <= bounds[2] and bounds[1] <= p[2] <= bounds[3])
           for p in [*route["geometry"], route["origin"]["position"], route["destination"]["position"]]):
        return None
    result = copy.deepcopy(route)
    by_id = {edge["id"]: edge for edge in clipped["edges"]}
    parts = []
    for part in route["traversals"]:
        found = [(edge, a, b) for edge, a, b in spans.get(part["edgeId"], [])
                 if a-1e-9 <= part["fromFraction"] and part["toFraction"] <= b+1e-9]
        if len(found) != 1:
            return None
        edge, a, b = found[0]
        parts.append({"edgeId": edge, "fromFraction": max(0, (part["fromFraction"]-a)/(b-a)),
                      "toFraction": min(1, (part["toFraction"]-a)/(b-a))})
    result["traversals"] = parts
    result["edgeIds"] = [part["edgeId"] for part in parts]
    for key in ("origin", "destination"):
        snap = route[key]["snap"]
        old_edge = next(edge for edge in original["edges"] if edge["geometryRef"] == snap["geometryRef"])
        old_fraction = 1-snap["fraction"] if old_edge["reverse"] else snap["fraction"]
        found = [(edge, a, b) for edge, a, b in spans.get(old_edge["id"], []) if a <= old_fraction <= b]
        if len(found) != 1:
            return None
        identity, a, b = found[0]
        edge, fraction = by_id[identity], (old_fraction-a)/(b-a)
        result[key]["snap"].update(geometryRef=edge["geometryRef"], fraction=1-fraction if edge["reverse"] else fraction)
        if result[key].get("place"):
            from shapely.geometry import box, mapping, shape
            region = box(-bounds[3]/111195, bounds[0]/111195, -bounds[1]/111195, bounds[2]/111195)
            result[key]["place"]["geometry"] = mapping(shape(result[key]["place"]["geometry"]).intersection(region))
    result["graphRevision"] = clipped["graphRevision"]
    result["warningCodes"] = list(dict.fromkeys([*result.get("warningCodes", []), "RANGE_LIMITED"]))
    return validate_route(clipped, result)
