"""验证交互式地图路线与原始导航数据的一致性。"""
from __future__ import annotations

import copy
from math import dist, isclose, isfinite
from . import DURATION_MODEL, LEGACY_DURATION_MODEL, DURATION_WAIT_FACTOR, duration_speed


def _point(value):
    if not isinstance(value, list) or len(value) != 3 or not all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and isfinite(v) for v in value):
        raise ValueError("Invalid route coordinate")
    return value


def _slice(points, start, end):
    cumulative = [0.0]
    for p, q in zip(points, points[1:]):
        cumulative.append(cumulative[-1] + dist(p, q))
    def sample(fraction):
        target = fraction*cumulative[-1]
        for index in range(1, len(points)):
            span = cumulative[index]-cumulative[index-1]
            if target <= cumulative[index] and span > 0:
                t = (target-cumulative[index-1])/span
                return [points[index-1][k] + t*(points[index][k]-points[index-1][k]) for k in range(3)]
        return points[-1][:]
    return [sample(start), *[p for offset, p in zip(cumulative, points)
                           if start*cumulative[-1] < offset < end*cumulative[-1]], sample(end)]


def validate_route(package, route):
    if route is None:
        return None
    if not isinstance(route, dict) or any(route.get(key) != package[key] for key in (
            "snapshotId", "graphRevision", "costRevision")):
        raise ValueError("Route belongs to another road snapshot")
    edges = {edge["id"]: edge for edge in package["edges"]}
    profile = route.get("costProfile")
    transit = profile == "transit"
    if transit:
        from .transit import is_boarding, transit_cost_config
        if any(key in route for key in ("walkFactor", "boardingCost")):
            raise ValueError("Transit weights must come from the snapshot cost configuration")
        package = {**package, **package.get("transit", {})}
        transit_cost = transit_cost_config(package.get("costModel", {}), route.get("costConfigId"))
        edges = {edge["id"]: edge for edge in package["edges"]}
    elif any(key in route for key in ("costConfigId", "candidateId", "balancedCost", "generatedBy")):
        raise ValueError("Transit metadata on a driving route")
    if profile not in ("car-shortest", "car-balanced", "transit"):
        raise ValueError("Unsupported route cost profile")
    duration_model = None if transit else package.get("durationModel", DURATION_MODEL)
    if duration_model not in (None, DURATION_MODEL, LEGACY_DURATION_MODEL) or route.get("durationModel") != duration_model:
        raise ValueError("Unsupported route duration model")
    if profile == "car-balanced" and package.get("costModel", {}).get("id") != "car-balanced-v1":
        raise ValueError("Recommended route cost model is unavailable")
    traversals = route.get("traversals")
    if not isinstance(traversals, list) or len(traversals) > len(edges) + 2:
        raise ValueError("Invalid route traversals")
    length = cost = 0.0
    duration = 0.0 if duration_model else None
    previous = None
    expected = []
    for index, part in enumerate(traversals):
        if not isinstance(part, dict) or part.get("edgeId") not in edges:
            raise ValueError("Route references an unknown road")
        edge = edges[part["edgeId"]]
        start, end = part.get("fromFraction"), part.get("toFraction")
        if any(not isinstance(v, (int, float)) or isinstance(v, bool) or not isfinite(v) for v in (start, end)) or not 0 <= start < end <= 1:
            raise ValueError("Invalid route fraction")
        if index and start != 0 or index < len(traversals)-1 and end != 1:
            raise ValueError("Route contains a partial interior road")
        if previous and (previous["toNode"] != edge["fromNode"] or (not transit and
                previous["sourceSegmentId"] == edge["sourceSegmentId"] and previous["reverse"] != edge["reverse"])):
            raise ValueError("Route contains an unconnected road or unsupported U-turn")
        part_length = (end-start)*edge["lengthM"]
        length += part_length
        cost += (part_length * (1 if edge["mode"] == "RIDE" else transit_cost["walkFactor"])
                 + (transit_cost["boardingCost"] if is_boarding(previous, edge) else 0)
                 if transit else part_length * (edge["recommendedCostPerMeter"] if profile == "car-balanced" else 1))
        if duration is not None:
            speed = duration_speed(edge, duration_model)
            duration += part_length / speed
        points = package["geometries"][edge["geometryRef"]]
        section = _slice(list(reversed(points)) if edge["reverse"] else points, start, end)
        for point in section:
            if not expected or dist(expected[-1], point) > 1e-8:
                expected.append(point)
        previous = edge
    if duration is not None and duration_model == DURATION_MODEL:
        duration *= DURATION_WAIT_FACTOR
    supplied = route.get("distanceM")
    supplied_duration = route.get("durationSeconds")
    if (duration is None and supplied_duration is not None) or (duration is not None and (
            not isinstance(supplied_duration, (int, float)) or isinstance(supplied_duration, bool)
            or not isfinite(supplied_duration) or supplied_duration < 0
            or not isclose(duration, supplied_duration, abs_tol=.01))):
        raise ValueError("Route duration does not match its roads")
    if not isinstance(supplied, (int, float)) or not isfinite(supplied) or not isclose(length, supplied, abs_tol=.01):
        raise ValueError("Route distance does not match its roads")
    supplied_cost = route.get("cost")
    if (profile == "car-balanced" or supplied_cost is not None) and route.get("costUnit") != (
            "meter" if profile == "car-shortest" else "preference-meter"):
        raise ValueError("Route cost unit does not match its profile")
    if (profile == "car-balanced" or supplied_cost is not None) and (
            not isinstance(supplied_cost, (int, float)) or isinstance(supplied_cost, bool)
            or not isfinite(supplied_cost) or not isclose(cost, supplied_cost, abs_tol=.01)):
        raise ValueError("Route cost does not match its roads")
    geometry = route.get("geometry")
    if not isinstance(geometry, list) or len(geometry) < 2:
        raise ValueError("Route geometry is missing")
    points = [_point(point) for point in geometry]
    if len(expected) == 1:
        expected.append(expected[0])
    if traversals and (len(points) != len(expected) or any(dist(p, q) > .01 for p, q in zip(points, expected))):
        raise ValueError("Route geometry does not match its roads")
    for key in ("origin", "destination"):
        endpoint = route.get(key)
        if not isinstance(endpoint, dict) or not isinstance(endpoint.get("snap"), dict):
            raise ValueError("Route endpoint is missing")
        _point(endpoint.get("position")); _point(endpoint["snap"].get("position"))
        ref, fraction = endpoint["snap"].get("geometryRef"), endpoint["snap"].get("fraction")
        if ref not in package["geometries"] or not isinstance(fraction, (int, float)) or not isfinite(fraction) or not 0 <= fraction <= 1:
            raise ValueError("Route endpoint does not belong to this road snapshot")
        if transit and not any(e["geometryRef"] == ref and e["mode"] == "WALK" for e in edges.values()):
            raise ValueError("Transit endpoint must connect to a road")
        snapped = _slice(package["geometries"][ref], fraction, fraction)[0]
        if dist(snapped, endpoint["snap"]["position"]) > .01:
            raise ValueError("Route endpoint projection is invalid")
    if dist(route["origin"]["snap"]["position"], points[0]) > .01 or dist(
            route["destination"]["snap"]["position"], points[-1]) > .01:
        raise ValueError("Route endpoints do not match its geometry")
    if not traversals:
        first, last = route["origin"]["snap"], route["destination"]["snap"]
        if first["geometryRef"] != last["geometryRef"] or abs(first["fraction"]-last["fraction"]) > 1e-10:
            endpoint_nodes = []
            for key in ("origin", "destination"):
                snap = route[key]["snap"]
                nodes = set()
                for edge in edges.values():
                    if edge["geometryRef"] != snap["geometryRef"]:
                        continue
                    if snap["fraction"] == 0:
                        nodes.add(edge["toNode"] if edge["reverse"] else edge["fromNode"])
                    if snap["fraction"] == 1:
                        nodes.add(edge["fromNode"] if edge["reverse"] else edge["toNode"])
                endpoint_nodes.append(nodes)
            if not endpoint_nodes[0].intersection(endpoint_nodes[1]):
                raise ValueError("An empty route requires a shared native endpoint")
    result = copy.deepcopy(route)
    if transit:
        from .transit import itinerary
        trip = itinerary(package, traversals)
        version = route.get("itineraryVersion")
        if "itineraryVersion" in route and (type(version) is not int or version != 1):
            raise ValueError("Unsupported route itinerary version")

        def matches(actual, expected, field=None):
            if field in ("itineraryVersion", "boardings", "stops", "traversalStart", "traversalEnd", "stopIndex"):
                return type(actual) is int and actual == expected
            if isinstance(expected, dict):
                return isinstance(actual, dict) and actual.keys() == expected.keys() and all(
                    matches(actual[key], value, key) for key, value in expected.items())
            if isinstance(expected, list):
                return isinstance(actual, list) and len(actual) == len(expected) and all(
                    matches(a, b) for a, b in zip(actual, expected))
            if isinstance(expected, bool):
                return type(actual) is bool and actual == expected
            if isinstance(expected, (int, float)):
                return type(actual) in (int, float) and isfinite(actual) and isclose(actual, expected, abs_tol=1e-7)
            return actual == expected

        if version == 1:
            if not all(matches(route.get(key), value, key) for key, value in trip.items()):
                raise ValueError("Route itinerary does not match its traversals")
        else:
            for key in ("boardings", "walkingDistanceM"):
                if key in route and not matches(route[key], trip[key], key):
                    raise ValueError("Legacy route itinerary summary does not match its traversals")
            if "legs" in route:
                legacy = route["legs"]
                if not isinstance(legacy, list) or len(legacy) != len(trip["legs"]):
                    raise ValueError("Legacy route itinerary does not match its traversals")
                for actual, expected_leg in zip(legacy, trip["legs"]):
                    if not isinstance(actual, dict) or any(
                            key in actual and not matches(actual[key], expected_leg.get(key), key)
                            for key in ("mode", "name", "fromStop", "toStop", "stops", "distanceM")):
                        raise ValueError("Legacy route itinerary does not match its traversals")
        result.update(trip)
        if any(key in route for key in ("candidateId", "balancedCost", "generatedBy")):
            from .transit import candidate_id, TRANSIT_PREFERENCES
            if route.get("candidateId") != candidate_id(package["graphRevision"], traversals):
                raise ValueError("Route candidate identity does not match its traversals")
            balanced = transit_cost_config(package["costModel"], "balanced")
            balanced_cost = length + (balanced["walkFactor"]-1)*trip["walkingDistanceM"] + balanced["boardingCost"]*trip["boardings"]
            if not matches(route.get("balancedCost"), balanced_cost):
                raise ValueError("Route candidate balanced cost does not match its traversals")
            generated = route.get("generatedBy")
            supported = TRANSIT_PREFERENCES if package["costModel"]["id"] == "transit-roads-v2" else {"balanced"}
            if (not isinstance(generated, list) or not 1 <= len(generated) <= len(supported)
                    or any(not isinstance(key, str) or key not in supported for key in generated)
                    or len(set(generated)) != len(generated) or (route.get("costConfigId") or "balanced") not in generated):
                raise ValueError("Invalid route candidate generation configurations")
    result["durationSeconds"] = duration
    result["durationModel"] = duration_model
    return result
