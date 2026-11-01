"""按源站序乘车，沿双向道路步行；不依赖班次或步行车道数据。"""
from __future__ import annotations

from math import dist, isfinite
import hashlib
import json
import struct

from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

WALK_FACTOR = 3
BOARDING_COST = 500
TRANSIT_PREFERENCES = {
    "balanced": {"walkFactor": WALK_FACTOR, "boardingCost": BOARDING_COST},
    "less-walking": {"walkFactor": 6, "boardingCost": BOARDING_COST},
    "fewer-transfers": {"walkFactor": WALK_FACTOR, "boardingCost": 1500},
}


def transit_cost_config(model, config_id):
    if model.get("id") == "transit-roads-v1":
        if config_id not in (None, "balanced"):
            raise ValueError("Unsupported legacy transit cost configuration")
        return {key: model[key] for key in ("walkFactor", "boardingCost")}
    if model.get("id") != "transit-roads-v2":
        raise ValueError("Unsupported transit cost model")
    if not isinstance(config_id, str) or config_id not in TRANSIT_PREFERENCES:
        raise ValueError("Unsupported transit cost configuration")
    return model["preferences"][config_id]


def candidate_id(graph_revision, traversals):
    signature = json.dumps([[part["edgeId"], *[struct.pack(
        ">d", 0 if part[key] == 0 else part[key]).hex() for key in ("fromFraction", "toFraction")]]
        for part in traversals], ensure_ascii=False, separators=(",", ":"))
    return "transit:" + hashlib.sha256(f"{graph_revision}\n{signature}".encode("utf-8")).hexdigest()


def compile_transit(document, roads):
    from .route import _slice
    from ...cmm.document_view import MapDocumentView
    from ..transit.name import resolve_stop_names
    from ..transit.geometry import track_path_index, track_chain_points
    from ..transit.loop import has_stop_line_closure
    from ...transit import type_def

    nodes, geometries, edges = {}, {}, []
    view = MapDocumentView(document)
    stops = {str(s.id): s for s in resolve_stop_names(view, document.stops)}
    lines = {line.id: line for line in document.transit_lines}
    networks = {segment.id: segment for segment in document.networks}
    road_shapes = [LineString([(p[0], p[2]) for p in road["points"]]) for road in roads]
    tree = STRtree(road_shapes)
    anchors = [[(0.0, road["fromNode"]), (1.0, road["toNode"])] for road in roads]
    path_cache = {}

    def add(ref, start, end, points, *, name="", mode="WALK", reverse=False, **facts):
        nodes[start], nodes[end] = points[0], points[-1]
        geometries[ref] = points
        base = dict(sourceSegmentId=ref, geometryRef=ref,
                    lengthM=sum(dist(a, b) for a, b in zip(points, points[1:])),
                    modeAccess="allowed", directionEvidence="road-walking-assumption" if mode == "WALK" else "exported-stop-order",
                    name=name, mode=mode, level="", **facts)
        edges.append(dict(base, id=ref+":f", fromNode=start, toNode=end, reverse=False))
        if reverse:
            edges.append(dict(base, id=ref+":b", fromNode=end, toNode=start, reverse=True))

    used_stops = {str(s) for route in document.routes for s in route.stop_ids}
    for ident in sorted(used_stops):
        stop = stops.get(ident)
        if stop is None:
            continue
        pos = [stop.position.x, stop.position.y, stop.position.z]
        if not all(isfinite(v) for v in pos):
            continue
        node = "stop:"+ident
        nodes[node] = pos
        if not roads:
            continue
        point = Point(pos[0], pos[2])
        index = int(tree.nearest(point))
        shape = road_shapes[index]
        # 站台到道路的短连接只表示出入口接驳；其余步行均走道路。
        if shape.distance(point) > 300:
            continue
        planar_fraction = shape.project(point, normalized=True)
        projected = shape.interpolate(planar_fraction, normalized=True)
        points = roads[index]["points"]
        offset = best = 0.0
        error = float("inf")
        total = sum(dist(a, b) for a, b in zip(points, points[1:]))
        for a, b in zip(points, points[1:]):
            segment = LineString([(a[0], a[2]), (b[0], b[2])])
            if segment.length and segment.distance(projected) < error:
                error = segment.distance(projected)
                best = offset + segment.project(projected, normalized=True)*dist(a, b)
            offset += dist(a, b)
        fraction = best/total
        anchor = roads[index]["fromNode"] if fraction == 0 else roads[index]["toNode"] if fraction == 1 else "access:"+ident
        anchors[index].append((fraction, anchor))
        snapped = _slice(points, fraction, fraction)[0]
        add("access:"+ident, anchor, node, [snapped, pos], mode="ACCESS", reverse=True)

    for road, cuts in zip(roads, anchors):
        cuts.sort()
        for index, ((a, start), (b, end)) in enumerate(zip(cuts, cuts[1:])):
            if start == end and a == b:
                continue
            # 重合站台投影仍需连接，不能把相同坐标当作相同拓扑节点。
            add(f"walk:{road['id']}:{index}", start, end, _slice(road["points"], a, b),
                name=road["name"], reverse=True, roadMode=road["mode"])

    for route in document.routes:
        line = lines.get(route.line_id)
        if line is None:
            continue
        source_nodes = [stops[str(s)].network_node_id for s in route.stop_ids if str(s) in stops]
        definition = type_def(str(line.kind).title())
        cs1 = document.metadata.origin.game.casefold() == "cs1"
        index_by_node = (track_path_index(view, definition.line_network, path_cache)
                         if cs1 and definition else {})
        closed = route.is_closed or (cs1 and len(source_nodes) == len(route.stop_ids)
                                    and has_stop_line_closure(view, source_nodes, line.kind))
        pairs = [(i, i+1, []) for i in range(len(route.stop_ids)-1)]
        if closed and len(route.stop_ids) > 1 and route.stop_ids[-1] != route.stop_ids[0]:
            pairs.append((len(route.stop_ids)-1, 0, []))
        if route.legs is not None:
            pairs = [(leg.from_stop_index, leg.to_stop_index, leg.path_segment_ids) for leg in route.legs]
        for index, (a, b, path) in enumerate(pairs):
            if not (0 <= a < len(route.stop_ids) and 0 <= b < len(route.stop_ids)):
                continue
            start, end = "stop:"+str(route.stop_ids[a]), "stop:"+str(route.stop_ids[b])
            if start not in nodes or end not in nodes:
                continue
            points = []
            for sid in path:
                segment = networks.get(sid)
                if segment is None:
                    points = []
                    break
                part = [[p.x, p.y, p.z] for p in segment.geometry.points]
                if not part or (points and dist(points[-1], part[0]) > 1):
                    points = []
                    break
                points.extend(part)
            if cs1 and route.legs is None:
                first = stops[str(route.stop_ids[a])]
                last = stops[str(route.stop_ids[b])]
                paths = {tuple(c[2]) for c in index_by_node.get(first.network_node_id, ()) if c[1] == last.network_node_id}
                if not paths:
                    paths = {tuple(reversed(c[2])) for c in index_by_node.get(last.network_node_id, ()) if c[1] == first.network_node_id}
                if len(paths) == 1:
                    chain = next(iter(paths))
                    if all(sid in networks for sid in chain):
                        points = [[p.x, p.y, p.z] for p in track_chain_points(view, chain, first.position)]
            schematic = not points or not all(isfinite(v) for p in points for v in p)
            points = [nodes[start], nodes[end]] if schematic else [nodes[start], *points, nodes[end]]
            add(f"ride:{route.id}:{index}", start, end, points, mode="RIDE", name=line.name,
                lineId=str(line.id), routeId=str(route.id), fromStopIndex=a,
                toStopIndex=0 if b == len(route.stop_ids)-1 and route.stop_ids[b] == route.stop_ids[0] else b,
                fromStop=stops[str(route.stop_ids[a])].name.text,
                toStop=stops[str(route.stop_ids[b])].name.text,
                transitKind=str(line.kind), schematic=schematic,
                color=("#%02x%02x%02x" % tuple(line.user_color[:3])) if line.user_color else "#7c3aed")
    return {"nodes": nodes, "geometries": geometries, "edges": edges,
            "costModel": {"id": "transit-roads-v2", "preferences": {
                key: dict(value) for key, value in TRANSIT_PREFERENCES.items()}}}


def is_boarding(previous, edge):
    return edge["mode"] == "RIDE" and not (
        previous and previous["mode"] == "RIDE"
        and previous.get("lineId") == edge.get("lineId")
        and previous.get("routeId") == edge.get("routeId")
        and previous.get("toStopIndex") == edge.get("fromStopIndex"))


def itinerary(package, traversals):
    """分段范围和停站出现位置均来自可信边，不按站名或距离合并。"""
    edges = {edge["id"]: edge for edge in package["edges"]}
    legs, previous = [], None
    boardings, walking = 0, 0.0

    def stop(edge, side):
        node = edge["fromNode" if side == "from" else "toNode"]
        return dict(stopId=node.removeprefix("stop:"), stopIndex=edge[side+"StopIndex"],
                    name=edge[side+"Stop"], position=package["nodes"][node])

    for index, part in enumerate(traversals):
        edge = edges[part["edgeId"]]
        boarding = is_boarding(previous, edge)
        boardings += int(boarding)
        mode = "ride" if edge["mode"] == "RIDE" else "walk"
        length = (part["toFraction"]-part["fromFraction"])*edge["lengthM"]
        if mode == "walk":
            walking += length
        if legs and legs[-1]["mode"] == mode and not boarding:
            leg = legs[-1]
            leg["distanceM"] += length
            leg["traversalEnd"] = index+1
        else:
            leg = dict(mode=mode, name=edge["name"] if mode == "ride" else "",
                       stops=0, distanceM=length, traversalStart=index, traversalEnd=index+1)
            if mode == "ride":
                leg.update(lineId=edge["lineId"], routeId=edge["routeId"], transitKind=edge["transitKind"],
                           fromStop=edge["fromStop"], color=edge["color"], schematic=False,
                           stopSequence=[stop(edge, "from")])
            legs.append(leg)
        if mode == "ride":
            leg["toStop"] = edge["toStop"]
            leg["stops"] += 1
            leg["schematic"] |= edge["schematic"]
            leg["stopSequence"].append(stop(edge, "to"))
        previous = edge
    for index, leg in enumerate(legs):
        if leg["mode"] != "walk":
            continue
        before = legs[index-1] if index else None
        after = legs[index+1] if index+1 < len(legs) else None
        leg["walkKind"] = "transfer" if before and after else "egress" if before else "access" if after else "walking-only"
        if before:
            leg["fromStopRef"] = before["stopSequence"][-1]
        if after:
            leg["toStopRef"] = after["stopSequence"][0]
    return dict(itineraryVersion=1, legs=legs, boardings=boardings, walkingDistanceM=walking)
