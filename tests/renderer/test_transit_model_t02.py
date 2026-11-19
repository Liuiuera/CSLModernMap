"""T02 coverage for the transit/station side-channel model."""
from __future__ import annotations

import copy
import json

from csmodernmap.cmm import (
    DisplayName,
    JobParams,
    StationMergeParams,
    Stop,
    StopKind,
    TransitKind,
    TransitLine,
    Vec3,
)
from csmodernmap.data import Building, MapData, Node, Point, Segment, SegmentType, Transport, TransportStop
from csmodernmap.services.station import group_stations
from csmodernmap.services.transit import (
    build_transit_model,
    effective_sequence,
    match_station_facilities,
    resolve_route_geometry,
)
from csmodernmap.transit import line_chain_segment_ids


def _fixture() -> MapData:
    nodes = [
        Node(1, Point(0, 0, 0), subsrv="PublicTransportMetro"),
        Node(2, Point(100, 0, 0), subsrv="PublicTransportMetro"),
        Node(3, Point(100, 0, 100), subsrv="PublicTransportMetro"),
        Node(4, Point(0, 0, 100), subsrv="PublicTransportMetro"),
        Node(5, Point(0, 0, 0), subsrv="PublicTransportBus"),
        Node(6, Point(100, 0, 0), subsrv="PublicTransportBus"),
        Node(7, Point(300, 0, 0), subsrv="PublicTransportMetro"),
        Node(8, Point(400, 0, 0), subsrv="PublicTransportMetro"),
        Node(9, Point(500, 0, 0), subsrv="PublicTransportMetro"),
    ]
    segments = []
    for index, (a, b) in enumerate(((1, 2), (2, 3), (3, 4), (4, 1)), 101):
        segments.append(Segment(
            index, a, b, name="Metro Line",
            points=[nodes[a - 1].pos, nodes[b - 1].pos],
            path_seg_ids=[index + 100],
        ))
        segments.append(Segment(
            index + 100, a, b, name="Metro Track",
            points=[nodes[a - 1].pos, nodes[b - 1].pos],
        ))
    segment_types = {"Metro Track": SegmentType(name="Metro Track", metro=True)}
    lines = [
        Transport(1, "Circle", "Metro", (255, 255, 255, 255),
                  [TransportStop(item) for item in (1, 2, 3, 4, 1)],
                  auto_color=(9, 9, 9)),
        Transport(2, "Circle", "Metro", (255, 255, 255, 255),
                  [TransportStop(item) for item in (1, 4, 3, 2, 1)]),
        Transport(3, "Bus loop", "Bus", (255, 255, 255, 255),
                  [TransportStop(5), TransportStop(6)]),
        Transport(4, "Branch", "Metro", (255, 20, 30, 40),
                  [TransportStop(item) for item in (7, 8, 9)]),
        Transport(5, "Return", "Metro", (255, 255, 255, 255),
                  [TransportStop(item) for item in (7, 8, 7)]),
    ]
    return MapData(
        nodes=nodes,
        segments=segments,
        segment_types=segment_types,
        buildings=[Building(
            id=50,
            custom_name="Central Station",
            icls="Train Station",
            points=[Point(-5, 0, -5), Point(5, 0, -5), Point(5, 0, 5)],
        )],
        transports=lines,
    )


def _projection(model):
    return {
        "lines": [(item.id, item.name, item.kind.value, item.user_color) for item in model.lines],
        "groups": [(item.id, item.member_line_ids, tuple(sorted(item.overlay_ids))) for item in model.groups],
        "routes": [(item.id, item.line_id, item.direction.value, tuple(item.stop_ids),
                    item.is_closed, item.geometry_kind.value, tuple(item.path_segment_ids), item.group_id)
                   for item in model.routes],
        "stops": [(item.stop_key, tuple(item.serving_line_ids), item.name.text) for item in model.stops],
        "stations": [(str(item.id), tuple(item.member_stop_keys), tuple(item.serving_group_ids), item.name.text)
                     for item in model.stations],
        "colors": sorted(model.auto_colors.items()),
    }


def test_loop_round_trip_grouping_and_deterministic_model():
    data = _fixture()
    params = JobParams(enabled_types=(TransitKind.METRO, TransitKind.BUS), view_all=False)
    first = build_transit_model(data, params)
    second = build_transit_model(data, params)

    assert _projection(first) == _projection(second)
    circle_group = next(group for group in first.groups if group.member_line_ids == (1, 2))
    circle_route = next(route for route in first.routes if route.group_id == circle_group.id)
    assert len([route for route in first.routes if route.group_id == circle_group.id]) == 1
    assert circle_route.direction.value == "loop"
    assert circle_route.is_closed
    assert circle_route.geometry_kind.value == "path_chain"
    assert effective_sequence(next(route for route in first.routes if route.line_id == 4)) == (7, 8, 9)
    assert data.transports[0].auto_color == (9, 9, 9)


def test_line_allow_list_filters_lines_routes_and_stations_together():
    model = build_transit_model(
        _fixture(),
        JobParams(enabled_types=(TransitKind.METRO, TransitKind.BUS),
                  enabled_line_ids=(3, 4), view_all=False),
    )

    assert [line.id for line in model.lines] == [3, 4]
    assert {route.line_id for route in model.routes} == {3, 4}
    assert {line_id for stop in model.stops for line_id in stop.serving_line_ids} == {3, 4}
    assert model.job_params.enabled_line_ids == (3, 4)


def test_stop_key_keeps_same_node_different_modes_apart_and_transfer_is_group_based():
    data = _fixture()
    model = build_transit_model(data, JobParams(view_all=True))
    keys = {item.stop_key for item in model.stops}
    assert "1:Metro" in keys and "5:Bus" in keys
    # Node 1 and node 5 are colocated, but their kinds must not merge.
    assert len([station for station in model.stations if "1:Metro" in station.member_stop_keys]) == 1
    assert len([station for station in model.stations if "5:Bus" in station.member_stop_keys]) == 1
    circle = next(station for station in model.stations if "1:Metro" in station.member_stop_keys)
    assert len(circle.serving_group_ids) == 1
    assert not model.is_transfer(circle)

    metro_a = Stop(1, 1, StopKind.METRO, DisplayName(), Vec3(0, 0, 0), serving_line_ids=[11])
    metro_b = Stop(2, 2, StopKind.METRO, DisplayName(), Vec3(1, 0, 0), serving_line_ids=[12])
    line_a = TransitLine(11, "A", TransitKind.METRO)
    line_b = TransitLine(12, "B", TransitKind.METRO)
    from csmodernmap.cmm import LineGroup
    groups = {11: LineGroup(101, line_a), 12: LineGroup(102, line_b)}
    station = group_stations((metro_a, metro_b), groups, StationMergeParams(merge_range=48))
    assert len(station) == 1
    assert station[0].serving_group_ids == [101, 102]


def test_names_facilities_and_geometry_are_equivalent_without_side_effects():
    data = _fixture()
    before = copy.deepcopy(data)
    model = build_transit_model(data, JobParams(enabled_types=(TransitKind.METRO,), view_all=False))
    central = next(station for station in model.stations if "1:Metro" in station.member_stop_keys)
    assert central.name.text == "Central Station"
    assert central.facilities
    assert central.facilities[0].kind.value == "OTHER" or central.facilities[0].kind.value == "RAIL"
    assert data.transports[0].auto_color == before.transports[0].auto_color
    assert not hasattr(data, "_transit_line_index_cache")

    legacy_view = copy.copy(data)
    legacy_view._node_by_id = {node.id: node for node in data.nodes}
    legacy_view._segment_by_id = {segment.id: segment for segment in data.segments}
    legacy_chain = tuple(line_chain_segment_ids(legacy_view, [1, 2, 3, 4, 1], "metro line"))
    geometry = resolve_route_geometry(data, data.transports[0], TransitKind.METRO)
    assert geometry.path_segment_ids == legacy_chain
    assert geometry.kind.value == "path_chain"


def test_json_projection_and_effective_color_are_stable():
    data = _fixture()
    model = build_transit_model(data, JobParams(view_all=True))
    encoded = json.dumps(_projection(model), sort_keys=True, default=str)
    assert json.loads(encoded) == json.loads(json.dumps(_projection(model), sort_keys=True, default=str))
    assert model.effective_color(1) == model.auto_colors[1]
    assert model.effective_color(4) == (20, 30, 40)
