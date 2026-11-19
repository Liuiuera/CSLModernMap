"""二代原生站台、独立路线和有向区间必须完整进入既有模型。"""
import copy
from dataclasses import replace

import pytest

from csmodernmap.cmm import JobParams, MapDocument, StopKey
from csmodernmap.cmm.document_view import MapDocumentView
from csmodernmap.services.transit.grouping import normalize_transit
from csmodernmap.services.transit.model import build_transit_model
from csmodernmap.services.transit.geometry import resolve_route_geometry
from csmodernmap.services.transit.native import migrate_sidecar
from csmodernmap.services.station import parse_station_sidecar


def native_document(sequences=((101, 102, 103, 102, 101),), *, mode="METRO", positions=None):
    positions = positions or {101: (0, 0), 102: (1000, 0), 103: (2000, 0),
                              104: (0, 6), 105: (1000, 6), 106: (2000, 6)}
    stops, routes, networks = [], [], []
    used = sorted({ident for seq in sequences for ident in seq})
    for ident in used:
        x, z = positions[ident]
        stops.append({"id": ident, "network_node_id": 1, "kind": mode,
                      "position": [x, 0, z], "name": {"text": str(ident), "source": "custom"}})
    for index, sequence in enumerate(sequences):
        legs = []
        for stop_index, (a, b) in enumerate(zip(sequence, sequence[1:])):
            ident = -(len(networks) + 1)
            x1, z1 = positions[a]
            x2, z2 = positions[b]
            networks.append({"id": ident, "kind": "STOP_LINE", "transit_for": mode,
                             "start_id": 1, "end_id": 1,
                             "geometry": {"points": [[x1, 0, z1], [x2, 0, z2]]}})
            legs.append({"from_stop_index": stop_index, "to_stop_index": stop_index + 1,
                         "path_segment_ids": [ident]})
        routes.append({"id": 20 + index, "line_id": 10, "stop_ids": sequence,
                       "is_closed": sequence[0] == sequence[-1],
                       "geometry_kind": "path_chain", "legs": legs,
                       "path_segment_ids": [ident for leg in legs for ident in leg["path_segment_ids"]]})
    return MapDocument.from_dict({"schema_version": "cmm-v1",
        "metadata": {"origin": {"game": "cs2"}}, "nodes": [{"id": 1, "pos": [9000, 0, 9000]}],
        "stops": stops, "transit_lines": [{"id": 10, "name": "Test", "kind": mode}],
        "routes": routes, "networks": networks})


def test_native_stops_keep_identity_coordinates_and_repeated_visits():
    document = native_document()
    original = copy.deepcopy(document.to_dict())
    view = MapDocumentView(document)
    normalized = normalize_transit(view)
    assert len(normalized.stops) == 3
    assert normalized.routes[0].stop_ids == [101, 102, 103, 102, 101]
    assert normalized.stops[0].position.x == 0
    assert normalized.stops[0].stop_key == "cs2-stop:101:Metro"
    assert normalized.stops[0].stop_key == str(normalized.stops[0].key)
    assert StopKey.parse(normalized.stops[0].stop_key).namespace == "cs2-stop"
    model = build_transit_model(view, station_fix=False)
    assert model.physical_topologies[10].kind == "path"
    assert len(model.stations) == 3
    assert model.effective_sequence(model.routes[0]) == (101, 102, 103)
    assert document.to_dict() == original


@pytest.mark.parametrize("anchor", [None, 1])
def test_line_catalog_uses_stop_identity_and_all_route_parts(anchor):
    from csmodernmap.cmm.session import DocumentSession
    from csmodernmap.guir_backend import _build_transit_line_catalog

    document = native_document(((101, 102), (105, 106)),
        positions={101: (0, 0), 102: (1000, 0), 105: (0, 1000), 106: (1000, 1000)})
    for stop in document.stops:
        stop.network_node_id = anchor
    catalog = _build_transit_line_catalog(DocumentSession(document))
    assert len(catalog) == 1
    assert catalog[0]["stops"] == 4
    assert catalog[0]["length_m"] == pytest.approx(2000)


@pytest.mark.parametrize("legacy", [False, True])
def test_multiple_routes_never_create_a_connection_between_routes(legacy):
    document = native_document(((101, 102), (105, 106)),
        positions={101: (0, 0), 102: (1000, 0), 105: (0, 1000), 106: (1000, 1000)})
    if legacy:
        document.extensions.clear()
        for route in document.routes:
            route.legs = None
    model = build_transit_model(MapDocumentView(document), station_fix=False)
    assert [route.id for route in model.routes] == [20, 21]
    assert [route.stop_ids for route in model.routes] == [[101, 102], [105, 106]]
    assert model.routes[0].path_segment_ids != model.routes[1].path_segment_ids
    assert {leg.route_id for leg in model.physical_legs[10]} == {20, 21}
    assert len(model.physical_topologies[10].edges) == 2
    assert model.effective_sequence(model.routes[0]) == (101, 102)
    assert model.effective_sequence(model.routes[1]) == (105, 106)


@pytest.mark.parametrize("mode", ["METRO", "TRAIN", "TRAM", "BUS", "SHIP", "FERRY"])
def test_native_directed_geometry_does_not_search_other_lines(mode):
    document = native_document(((103, 102, 101),), mode=mode)
    view = MapDocumentView(document)
    geometry = resolve_route_geometry(view, view.transports[0])
    assert geometry.points[0].x == 2000
    assert geometry.points[-1].x == 0
    assert geometry.path_segment_ids == (-1, -2)


@pytest.mark.parametrize("version", [None, 1, 2, 99])
@pytest.mark.parametrize("status", [None, "partial", "verified"])
def test_version_and_status_are_not_generation_requirements(version, status):
    raw = native_document().to_dict()
    raw["extensions"]["cs2"] = {} if version is None else {"transit_contract_version": version}
    for leg in raw["routes"][0]["legs"]:
        if status is None:
            leg.pop("status", None)
        else:
            leg["status"] = status
    document = MapDocument.from_dict(raw)
    assert all("status" not in leg for leg in document.to_dict()["routes"][0]["legs"])
    view = MapDocumentView(document)
    model = build_transit_model(view, station_fix=False)
    assert model.physical_topologies[10].kind == "path"
    assert model.effective_sequence(model.routes[0]) == (101, 102, 103)
    geometry = resolve_route_geometry(view, model.source_lines[10])
    assert geometry.path_segment_ids == (-1, -2, -3, -4)


def test_native_curve_sequence_is_used_without_distance_revalidation():
    document = native_document()
    extra = copy.deepcopy(document.networks[0].to_dict())
    extra["id"] = -99
    extra["geometry"]["points"] = [[1005, 0, 0], [1100, 0, 0]]
    document.networks.append(type(document.networks[0]).from_dict(extra))
    document.rebuild_index()
    document.routes[0].legs[0].path_segment_ids.append(-99)
    document.routes[0].path_segment_ids = [ident for leg in document.routes[0].legs for ident in leg.path_segment_ids]
    view = MapDocumentView(document)
    model = build_transit_model(view, station_fix=False)
    assert len(model.physical_topologies[10].edges) == 2
    assert [p.x for p in model.physical_legs[10][0].points] == [0, 1000, 1005, 1100]
    geometry = resolve_route_geometry(view, model.source_lines[10])
    assert len(geometry.parts) == 1
    assert [p.x for p in geometry.parts[0][:4]] == [0, 1000, 1005, 1100]
    assert not model.issues


def test_generation_does_not_repeat_document_validation(monkeypatch):
    document = native_document()
    document.validate()

    def unexpected_validation():
        pytest.fail("generation repeated file-boundary validation")

    monkeypatch.setattr(document, "validate_transit", unexpected_validation)
    view = MapDocumentView(document)
    model = build_transit_model(view, station_fix=False)
    assert model.effective_sequence(model.routes[0]) == (101, 102, 103)
    assert resolve_route_geometry(view, model.source_lines[10]).path_segment_ids == (-1, -2, -3, -4)


@pytest.mark.parametrize("damage,code", [
    (lambda d: setattr(d.routes[0].legs[0], "to_stop_index", 99), "ROUTE_LEG_INDICES"),
    (lambda d: d.routes[0].legs[0].path_segment_ids.append(-99), "DANGLING_REF"),
    (lambda d: d.routes[0].stop_ids.append(999), "DANGLING_REF"),
])
def test_missing_external_references_are_reported_at_document_boundary(damage, code):
    document = native_document()
    damage(document)
    assert code in {issue.code for issue in document.validate()}


@pytest.mark.parametrize("omit", [False, True])
def test_native_stops_without_road_anchors_roundtrip_and_generate_pois(omit):
    from csmodernmap.services.poi import POIJobParams
    from csmodernmap.services.poi.service import _transport_stops, produce

    raw = native_document().to_dict()
    for stop in raw["stops"]:
        if omit:
            stop.pop("network_node_id")
        else:
            stop["network_node_id"] = None
    document = MapDocument.from_dict(raw)
    assert not [issue for issue in document.validate() if issue.severity == "error"]
    document = MapDocument.from_dict(document.to_dict())
    view = MapDocumentView(document)
    model = build_transit_model(view, station_fix=False)
    assert [stop.stop_key for stop in model.stops] == ["cs2-stop:101:Metro", "cs2-stop:102:Metro", "cs2-stop:103:Metro"]
    assert all(stop.network_node_id is None for stop in model.stops)
    assert model.effective_sequence(model.routes[0]) == (101, 102, 103)
    stops = _transport_stops(view, POIJobParams())
    assert [(stop.id, stop.name.text) for stop in stops] == [(101, "101"), (102, "102"), (103, "103")]
    catalog = produce(view, POIJobParams())
    assert {poi.source.id for poi in catalog.pois if poi.source.kind == "stop"} == {101, 102, 103}

    from PIL import Image, ImageDraw, ImageChops
    from csmodernmap.layers.base import RenderContext
    from csmodernmap.layers.transport_layer import TransportLayer
    from csmodernmap._paths import resource_dir
    from csmodernmap.style import load_style

    images = []
    for source in (MapDocumentView(native_document()), view):
        image = Image.new("RGBA", (240, 120), "white")
        ctx = RenderContext(image, ImageDraw.Draw(image), lambda x, z: (20 + x / 10, 60 + z / 10),
            load_style(str(resource_dir("styles") / "modern.yaml")).merged({"transports": {"enabled": ["Metro"], "abstract": False,
                                  "label": {"enabled": False}, "station": {"enabled": True}}}))
        TransportLayer().render(ctx, source)
        images.append(image.convert("RGB"))
    assert ImageChops.difference(images[0], images[1]).getbbox() is None
    assert ImageChops.difference(images[1], Image.new("RGB", images[1].size, "white")).getbbox()
    assert ImageChops.difference(images[1], Image.new("RGB", images[1].size, "white")).getbbox()


def test_roundtrip_and_selection_preserve_native_identity():
    document = native_document()
    document.stops[0].station_group = {"entity_id": 400, "source": "AccessRestriction"}
    document.stops[0].name_kind = "station"
    document.stops[0].name_entity_id = 400
    restored = MapDocument.from_dict(document.to_dict())
    assert restored.to_dict() == document.to_dict()
    model = build_transit_model(MapDocumentView(restored), JobParams(enabled_line_ids=(10,)), station_fix=False)
    assert model.stops[0].station_group == document.stops[0].station_group


def test_template_name_is_not_used_but_custom_text_is_preserved():
    document = native_document()
    document.stops[0].name_kind = "template"
    document.stops[1].name_kind = "template"
    document.stops[1].name = replace(document.stops[1].name, source="game")
    model = build_transit_model(MapDocumentView(document), station_fix=False)
    by_id = {stop.id: stop for stop in model.stops}
    assert by_id[101].name.text == "101"
    assert not by_id[102].name.text


def test_old_sidecar_ambiguous_mapping_is_retained_and_rejected():
    document = native_document()
    stops = normalize_transit(MapDocumentView(document)).stops
    sidecar = parse_station_sidecar({"schema_version": "station-fix/v1", "scope": "stop-anchored",
        "source_map": "city.cmm.gz", "edits": [{"id": "rename", "kind": "rename_stop", "stop_key": "1:Metro", "name": "Old"}]})
    with pytest.raises(ValueError, match="多个"):
        migrate_sidecar(sidecar, stops, "city.cmm.gz")
    assert sidecar.edits[0].stop_keys == ("1:Metro",)
    migrated = migrate_sidecar(sidecar, stops[:1], "city.cmm.gz")
    assert migrated.edits[0].stop_keys == ("cs2-stop:101:Metro",)
    assert parse_station_sidecar(migrated.to_dict()) == migrated


def test_native_station_constraints_honor_disabled_kind_and_transitive_user_rules():
    from csmodernmap.cmm import StationMergeParams
    from csmodernmap.services.transit.native import station_constraints
    from csmodernmap.services.station import group_stations

    document = native_document()
    for stop, group in zip(document.stops, (400, 400, 500)):
        stop.station_group = {"entity_id": group, "source": "Owner"}
    stops = normalize_transit(MapDocumentView(document)).stops
    a, b, c = (stop.stop_key for stop in stops)
    disabled = station_constraints(stops, StationMergeParams(overrides={"metro": {"enabled": False}}))
    assert len(group_stations(stops, params=disabled)) == 3
    manual = StationMergeParams(forced_merges=((a, c), (c, b)))
    constrained = station_constraints(stops, manual)
    assert not constrained.blocked_pairs
    assert len(group_stations(stops, params=constrained)) == 1
    split = station_constraints(stops, StationMergeParams(forced_merges=((a, c),), blocked_pairs=((b, c),)))
    assert len(group_stations(stops, params=split)) == 2


def test_native_station_group_uses_entity_identity_across_evidence_sources():
    from csmodernmap.cmm import StationMergeParams
    from csmodernmap.services.transit.native import station_constraints
    from csmodernmap.services.station import group_stations

    document = native_document(((101, 102),))
    document.stops[0].station_group = {"entity_id": 400, "source": "Owner"}
    document.stops[1].station_group = {"entity_id": 400, "source": "AccessRestriction"}
    stops = normalize_transit(MapDocumentView(document)).stops
    assert len(group_stations(stops, params=station_constraints(stops, StationMergeParams()))) == 1


def test_old_metro_edits_require_same_file_and_unique_platform_mapping():
    from csmodernmap.metro_edit import MetroEditDocument

    document = native_document()
    view = MapDocumentView(document)
    document.metadata.source_path = "city.cmm.gz"
    edits = MetroEditDocument(source="other.cmm.gz")
    edits.move_station([1], 2, 3)
    with pytest.raises(ValueError, match="源文件"):
        edits.for_map(view)
    edits.source = "city.cmm.gz"
    with pytest.raises(ValueError, match="多个"):
        edits.for_map(view)
    for stop in document.stops[1:]:
        stop.network_node_id = stop.id
    migrated = edits.for_map(view)
    assert migrated.identity_space == "cs2-stop"
    assert migrated.stations["nodes:101"].cell == (2, 3)
    assert "nodes:1" in edits.stations
    assert MetroEditDocument.from_dict(migrated.to_dict()).for_map(view).to_dict() == migrated.to_dict()


def test_native_directed_route_cannot_be_reversed_without_geometry():
    from csmodernmap.cmm import RouteView

    with pytest.raises(ValueError, match="directed"):
        RouteView(native_document().routes[0]).reverse()


@pytest.mark.parametrize("sequence", [
    (101, 102, 103),
    (101, 102, 103, 102, 101),
    (101, 102, 103, 101),
    (101, 102, 103, 102, 104),
])
@pytest.mark.parametrize("legacy", [False, True])
def test_cs1_and_cs2_semantic_station_chains_are_equivalent(sequence, legacy):
    from csmodernmap.data import MapData, Node, Point, Segment, Transport, TransportStop

    positions = {101: (0, 0), 102: (1000, 0), 103: (2000, 800), 104: (1000, -1000)}
    document = native_document((sequence,), positions=positions)
    if legacy:
        document.extensions.clear()
        document.routes[0].legs = None
    nodes = [Node(stop.id - 100, Point(stop.position.x, 0, stop.position.z), subsrv="PublicTransportMetro")
             for stop in document.stops]
    segments = []
    for index, (a, b) in enumerate(dict.fromkeys(zip(sequence, sequence[1:]))):
        points = [Point(positions[ident][0], 0, positions[ident][1]) for ident in (a, b)]
        segments.extend([
            Segment(1000 + index, a - 100, b - 100, name="Metro Track", points=points),
            Segment(2000 + index, a - 100, b - 100, name="Metro Line", path_seg_ids=[1000 + index]),
        ])
    cs1 = MapData(nodes=nodes, segments=segments, transports=[Transport(10, "Test", "Metro",
        stops=[TransportStop(ident - 100) for ident in sequence])])
    one = build_transit_model(cs1, station_fix=False)
    two = build_transit_model(MapDocumentView(document), station_fix=False)

    def project(model, offset):
        members = {station.id: tuple(sorted(ident - offset for ident in station.member_stop_ids))
                   for station in model.stations}
        topology = model.physical_topologies[10]
        return (sorted(members.values()), topology.kind,
                {frozenset((members[a], members[b])) for a, b in topology.edges},
                [group.member_line_ids for group in model.groups])

    assert project(one, 0) == project(two, 100)


def test_native_preview_keeps_separate_routes_without_drawing_a_bridge(tmp_path):
    import json
    from csmodernmap.cmm import DocumentSession
    from csmodernmap.preview_scene import PreviewSceneCompiler

    from contextlib import ExitStack
    document = native_document(((101, 102), (105, 106)))
    with ExitStack() as scope:
        PreviewSceneCompiler._compile_domain_sources(document, DocumentSession(document), tmp_path, 1, {},
            {"changed": {"transit"}, "style": {}}, domain_scope=scope)
    feature, = json.loads((tmp_path / "transit.geojson").read_text(encoding="utf-8"))["features"]
    assert feature["geometry"]["type"] == "MultiLineString"
    assert [len(part) for part in feature["geometry"]["coordinates"]] == [2, 2]
    assert feature["properties"]["lengthMeters"] == 2000
