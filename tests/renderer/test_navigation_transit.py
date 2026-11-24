from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from csmodernmap.cmm import MapDocument, RoadLevel
from csmodernmap.services.navigation import clip_navigation, compile_navigation
from csmodernmap.services.navigation.route import validate_route
from test_navigation import document, node_router


@pytest.mark.parametrize("anchor", [None, 1])
@pytest.mark.parametrize("same_name", [False, True])
def test_cs2_navigation_uses_stop_identity_without_mutating_source(anchor, same_name):
    from cs2_native_transit_fixture import native_document

    source = native_document()
    for stop in source.stops:
        stop.network_node_id = anchor
        if same_name:
            stop.name = replace(stop.name, text="同名站")
    before = copy.deepcopy(source.to_dict())
    package = compile_navigation(source)
    transit = package["transit"]
    assert {key for key in transit["nodes"] if key.startswith("stop:")} == {
        "stop:101", "stop:102", "stop:103"}
    rides = [edge for edge in transit["edges"] if edge["mode"] == "RIDE"]
    assert [(edge["fromNode"], edge["toNode"]) for edge in rides] == [
        ("stop:101", "stop:102"), ("stop:102", "stop:103"),
        ("stop:103", "stop:102"), ("stop:102", "stop:101")]
    assert all(not edge["schematic"] for edge in rides)
    assert source.to_dict() == before
    assert all(stop._identity_space == "" for stop in source.stops)


def test_cs2_navigation_keeps_unresolved_native_stop_name_empty():
    from cs2_native_transit_fixture import native_document

    source = native_document()
    source.stops[0].network_node_id = None
    source.stops[0].name = replace(source.stops[0].name, source="game")
    source.stops[0].name_kind = "template"
    package = compile_navigation(source)
    ride = next(edge for edge in package["transit"]["edges"] if edge["mode"] == "RIDE")
    assert ride["fromStop"] == ""


def transit_document():
    source = document([(11, 1, 2, False), (12, 2, 3, False)],
                      {1: [0, 0, 0], 2: [3000, 0, 0], 3: [6000, 0, 0]}).to_dict()
    source["stops"] = [{"id": i, "network_node_id": i, "kind": "BUS",
                        "name": {"text": f"站{i}"}, "position": [x, 0, 10]}
                       for i, x in [(21, 100), (22, 3000), (23, 5900)]]
    source["transit_lines"] = [{"id": 1, "name": "一号线", "kind": "BUS"},
                               {"id": 2, "name": "二号线", "kind": "METRO"}]
    source["routes"] = [{"id": i, "line_id": i, "stop_ids": stops, "is_closed": False,
                         "direction": "outbound", "geometry_kind": "STOP_DIRECT"}
                        for i, stops in [(1, [21, 22]), (2, [22, 23])]]
    return MapDocument.from_dict(source)


def plan(node_router, package, start=(20, 0, 0), end=(5980, 0, 0), config=None):
    snaps = node_router(package, [{"snap": start, "profile": "transit"},
                                  {"snap": end, "profile": "transit"}])["results"]
    a, b = snaps[0][0], snaps[1][0]
    result = node_router(package, [{"origin": [a["geometryRef"], a["fraction"]],
                                   "destination": [b["geometryRef"], b["fraction"]],
                                   "profile": "transit", **({'costConfigId': config} if config is not None else {})}])["results"][0]
    result.update({key: package[key] for key in ("snapshotId", "graphRevision", "costRevision")})
    result.update(origin={"position": list(start), "snap": a}, destination={"position": list(end), "snap": b})
    return result


def test_transit_transfer_and_host_validation(node_router):
    package = compile_navigation(transit_document())
    result = plan(node_router, package)
    assert result["status"] == "ok"
    assert result["boardings"] == 2
    assert [leg["name"] for leg in result["legs"] if leg["mode"] == "ride"] == ["一号线", "二号线"]
    assert result["walkingDistanceM"] == pytest.approx(180)
    assert result["durationSeconds"] is None
    assert "TRANSIT_SCHEMATIC" in result["warningCodes"]
    assert validate_route(package, result)["distanceM"] == pytest.approx(5980)
    invalid = copy.deepcopy(result)
    invalid["cost"] -= 500
    with pytest.raises(ValueError, match="cost"):
        validate_route(package, invalid)


def test_transit_obeys_direction_but_walking_ignores_car_oneway(node_router):
    package = compile_navigation(transit_document())
    result = plan(node_router, package, (5980, 0, 0), (20, 0, 0))
    assert result["boardings"] == 0
    assert result["walkingDistanceM"] == pytest.approx(5960)
    assert validate_route(package, result)["distanceM"] == pytest.approx(5960)


def test_transit_walks_without_car_access_and_preserves_stop_order(node_router):
    source = transit_document()
    for road in source.networks:
        road.car_access = None
    source.routes[0].stop_ids = [21, 22, 23]
    source.routes = source.routes[:1]
    package = compile_navigation(source)
    result = plan(node_router, package)
    assert not package["edges"]
    assert result["boardings"] == 1
    assert [leg["stops"] for leg in result["legs"] if leg["mode"] == "ride"] == [2]
    validate_route(package, result)


@pytest.mark.parametrize("has_street", [True, False])
def test_highway_is_driveable_but_excluded_from_walking_and_station_access(has_street):
    source = transit_document()
    overpass = document([(13, 4, 5, True)], {4: [0, 12, 10], 5: [6000, 12, 10]})
    overpass.networks[0].level = RoadLevel.HIGHWAY
    source.nodes.extend(overpass.nodes)
    source.networks = (source.networks if has_street else []) + overpass.networks

    package = compile_navigation(source)
    assert {edge["id"] for edge in package["edges"] if edge["sourceSegmentId"] == "13"} == {
        "road:13:f", "road:13:b"}
    geometries = package["transit"]["geometries"]
    assert not any(ref.startswith("walk:13:") for ref in geometries)
    if has_street:
        assert geometries["access:21"] == [[100, 0, 0], [100, 0, 10]]
    else:
        assert not any(ref.startswith("access:") for ref in geometries)


def test_clipped_transit_route_and_outside_network(node_router):
    package = compile_navigation(transit_document())
    route = plan(node_router, package)
    clipped = clip_navigation(package, [10, -20, 5990, 20], selected_route=route)
    assert clipped["selectedRoute"] is not None
    validate_route(clipped, clipped["selectedRoute"])
    small = clip_navigation(package, [0, -20, 2000, 20], selected_route=route)
    assert small["selectedRoute"] is None
    assert all(0 <= p[0] <= 2000 for points in small["transit"]["geometries"].values() for p in points)


def test_same_road_multiple_stops_do_not_require_walk_detour(node_router):
    source = transit_document()
    package = compile_navigation(source)
    result = plan(node_router, package, (200, 0, 0), (400, 0, 0))
    assert result["distanceM"] == pytest.approx(200)


def test_loop_wrap_keeps_one_boarding(node_router):
    source = transit_document()
    source.routes = source.routes[:1]
    source.routes[0].stop_ids = [21, 22, 23, 21]
    source.routes[0].is_closed = True
    package = compile_navigation(source)
    rides = [e for e in package["transit"]["edges"] if e["mode"] == "RIDE"]
    from csmodernmap.services.navigation.transit import is_boarding
    assert is_boarding(rides[-1], rides[0]) is False
    result = plan(node_router, package, (5980, 0, 0), (20, 0, 0))
    assert result["boardings"] == 1
    validate_route(package, result)


@pytest.mark.parametrize("kind,stop_ids,return_name,can_return", [
    ("METRO", [21, 22, 23], "Workshop METRO Line Elevated", True),
    ("MONORAIL", [21, 22, 23], "Monorail Line", True),
    ("METRO", [21, 22], "Metro Line", True),
    ("METRO", [21, 22], None, False),
    ("METRO", [21, 22], "Train Line", False),
    ("BUS", [21, 22], "Trolleybus Line", False),
    ("METRO", [21, 22, 21], None, True),
])
def test_cs1_transit_uses_directed_return_segment(node_router, kind, stop_ids, return_name, can_return):
    data = transit_document().to_dict()
    for node_id, stop in enumerate(data["stops"], start=1):
        stop.update(network_node_id=node_id, kind=kind)
    data["transit_lines"] = [dict(data["transit_lines"][0], kind=kind)]
    data["routes"] = [dict(data["routes"][0], stop_ids=stop_ids)]
    node_for = {stop["id"]: stop["network_node_id"] for stop in data["stops"]}
    pairs = list(zip(stop_ids, stop_ids[1:]))
    segments = [(a, b, kind.title() + " Line") for a, b in pairs]
    if return_name:
        segments.append((stop_ids[-1], stop_ids[0], return_name))
    for ident, (a, b, name) in enumerate(segments, start=31):
        data["networks"].append(dict(id=ident, name=name, kind="STOP_LINE", mode="GROUND",
                                     start_id=node_for[a], end_id=node_for[b], width=0,
                                     geometry={"points": []}))

    package = compile_navigation(MapDocument.from_dict(data))
    rides = [edge for edge in package["transit"]["edges"] if edge["mode"] == "RIDE"]
    if can_return and stop_ids[0] != stop_ids[-1]:
        pairs.append((stop_ids[-1], stop_ids[0]))
    assert [(edge["fromNode"], edge["toNode"]) for edge in rides] == [
        (f"stop:{a}", f"stop:{b}") for a, b in pairs]
    last_x = 5980 if 23 in stop_ids else 2980
    result = plan(node_router, package, (last_x, 0, 0), (20, 0, 0))
    assert result["boardings"] == int(can_return)
    validate_route(package, result)


def test_unconnected_road_crossing_is_not_a_transfer(node_router):
    source = document([(11, 1, 2, True), (12, 3, 4, True)],
                      {1: [-1000, 0, 0], 2: [1000, 0, 0], 3: [0, 0, -1000], 4: [0, 0, 1000]})
    package = compile_navigation(source)
    result = plan(node_router, package, (-800, 0, 0), (0, 0, 800))
    assert result['code'] == 'NO_PATH'


def test_crop_endpoint_at_stop_retains_road_snap(node_router):
    package = compile_navigation(transit_document())
    route = plan(node_router, package, (100, 0, 10), (5900, 0, 10))
    clipped = clip_navigation(package, [50, -20, 5950, 20], selected_route=route)
    assert clipped['selectedRoute']['origin']['snap']['geometryRef'].startswith('walk:')
    validate_route(clipped, clipped['selectedRoute'])


def test_cs2_directed_leg_geometry_is_used(node_router):
    source = transit_document().to_dict()
    source['metadata']['origin']['game'] = 'cs2'
    source['networks'].append({'id': 51, 'kind': 'TRANSIT', 'start_id': 21, 'end_id': 22,
                               'geometry': {'points': [[100, 0, 10], [1500, 0, 200], [3000, 0, 10]]}})
    source['routes'][0]['legs'] = [{'from_stop_index': 0, 'to_stop_index': 1, 'path_segment_ids': [51]}]
    package = compile_navigation(MapDocument.from_dict(source))
    ride = next(e for e in package['transit']['edges'] if e['mode'] == 'RIDE')
    assert ride['schematic'] is False
    assert [1500, 0, 200] in package['transit']['geometries'][ride['geometryRef']]
    validate_route(package, plan(node_router, package))


def test_itinerary_ranges_stop_occurrences_and_walk_roles(node_router):
    package = compile_navigation(transit_document())
    result = plan(node_router, package)
    assert result['itineraryVersion'] == 1
    assert [(leg['traversalStart'], leg['traversalEnd']) for leg in result['legs']] == [
        (0, 2), (2, 3), (3, 4), (4, 6)]
    rides = [leg for leg in result['legs'] if leg['mode'] == 'ride']
    assert [[stop['stopId'] for stop in leg['stopSequence']] for leg in rides] == [['21', '22'], ['22', '23']]
    assert rides[0]['lineId'] == '1' and rides[1]['routeId'] == '2'
    assert result['legs'][0]['walkKind'] == 'access'
    assert result['legs'][-1]['walkKind'] == 'egress'
    assert result['legs'][0]['toStopRef'] == rides[0]['stopSequence'][0]
    assert validate_route(package, result)['legs'] == result['legs']


@pytest.mark.parametrize('field', ['lineId', 'routeId', 'stopSequence', 'traversalStart', 'traversalEnd', 'distanceM',
                                  'boardings', 'walkingDistanceM', 'itineraryVersion', 'missing'])
def test_host_rejects_forged_itinerary(node_router, field):
    package = compile_navigation(transit_document())
    route = plan(node_router, package)
    ride = next(leg for leg in route['legs'] if leg['mode'] == 'ride')
    if field in ('lineId', 'routeId'):
        ride[field] = 'forged'
    elif field == 'stopSequence':
        ride[field][0]['stopId'] = '22'
    elif field in ('traversalStart', 'traversalEnd', 'distanceM'):
        ride[field] += 1
    elif field == 'missing':
        del ride['stopSequence']
    else:
        route[field] += 1
    with pytest.raises(ValueError, match='itinerary'):
        validate_route(package, route)


def test_legacy_itinerary_rebuild_and_invalid_summary(node_router):
    package = compile_navigation(transit_document())
    route = plan(node_router, package)
    expected = copy.deepcopy(route['legs'])
    del route['itineraryVersion']
    for leg in route['legs']:
        for key in ('traversalStart', 'traversalEnd', 'lineId', 'routeId', 'transitKind', 'stopSequence',
                    'walkKind', 'fromStopRef', 'toStopRef'):
            leg.pop(key, None)
    rebuilt = validate_route(package, route)
    assert rebuilt['itineraryVersion'] == 1 and rebuilt['legs'] == expected
    route['boardings'] = 99
    with pytest.raises(ValueError, match='itinerary'):
        validate_route(package, route)


def test_zero_length_access_retains_traversal_and_boarding(node_router):
    source = transit_document()
    for stop in source.stops:
        stop.position = type(stop.position)(stop.position.x, 0, 0)
    package = compile_navigation(source)
    result = plan(node_router, package)
    assert result['boardings'] == 2
    access_ids = {edge['id'] for edge in package['transit']['edges'] if edge['mode'] == 'ACCESS'}
    assert any(part['edgeId'] in access_ids for part in result['traversals'])
    validate_route(package, result)


def test_boarding_event_does_not_depend_on_price(node_router):
    package = compile_navigation(transit_document())
    package['transit']['costModel'] = dict(id='transit-roads-v1', walkFactor=3, boardingCost=0)
    route = plan(node_router, package)
    assert route['boardings'] == 2
    assert len([leg for leg in route['legs'] if leg['mode'] == 'ride']) == 2


def test_empty_transit_itinerary_and_pure_walk(node_router):
    package = compile_navigation(transit_document())
    empty = plan(node_router, package, (20, 0, 0), (20, 0, 0))
    assert empty['legs'] == [] and empty['boardings'] == 0
    validate_route(package, empty)
    walk = plan(node_router, package, (200, 0, 0), (400, 0, 0))
    assert len(walk['legs']) == 1 and walk['legs'][0]['walkKind'] == 'walking-only'


def test_loop_stop_sequence_retains_repeated_occurrences():
    from csmodernmap.services.navigation.transit import itinerary
    source = transit_document()
    source.routes = source.routes[:1]
    source.routes[0].stop_ids = [21, 22, 23, 21]
    source.routes[0].is_closed = True
    package = compile_navigation(source)['transit']
    rides = [edge for edge in package['edges'] if edge['mode'] == 'RIDE']
    parts = [dict(edgeId=edge['id'], fromFraction=0, toFraction=1) for edge in [rides[2], *rides]]
    trip = itinerary(package, parts)
    assert trip['boardings'] == 1
    assert [stop['stopId'] for stop in trip['legs'][0]['stopSequence']] == ['23', '21', '22', '23', '21']
    assert trip['legs'][0]['stops'] == 4


def test_walk_transfer_has_distinct_stop_identity_and_distance(node_router):
    source = transit_document().to_dict()
    source['stops'][1]['position'] = [2500, 0, 10]
    source['stops'][2]['position'] = [3500, 0, 10]
    source['stops'].append({**source['stops'][2], 'id': 24, 'position': [5900, 0, 10]})
    source['routes'][1]['stop_ids'] = [23, 24]
    for stop in source['stops']:
        stop['name'] = {'text': '同名站'}
    package = compile_navigation(MapDocument.from_dict(source))
    route = plan(node_router, package)
    transfer = next(leg for leg in route['legs'] if leg.get('walkKind') == 'transfer')
    assert transfer['distanceM'] == pytest.approx(1020)
    assert transfer['fromStopRef']['stopId'] == '22' and transfer['toStopRef']['stopId'] == '23'
    validate_route(package, route)


def test_host_rejects_fractional_itinerary_indices(node_router):
    package = compile_navigation(transit_document())
    route = plan(node_router, package)
    route['legs'][0]['traversalStart'] += 1e-8
    with pytest.raises(ValueError, match='itinerary'):
        validate_route(package, route)
