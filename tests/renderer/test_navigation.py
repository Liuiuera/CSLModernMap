from __future__ import annotations

import copy
import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

from csmodernmap.cmm import DocumentSession, MapDocument
from csmodernmap.services.navigation import clip_navigation, compile_navigation
from csmodernmap.services.navigation.route import validate_route

ROOT = Path(__file__).resolve().parents[2]
VIEWER = ROOT / "dev/CSLModernMapRenderer/viewer"


def document(roads, nodes=None):
    nodes = nodes or {1: [0, 0, 0], 2: [100, 0, 0], 3: [100, 0, 100], 4: [0, 0, 100]}
    return MapDocument.from_dict({
        "schema_version": "cmm-v1", "metadata": {"origin": {"game": "cs1", "source_name": "routing"}},
        "nodes": [{"id": key, "pos": value} for key, value in nodes.items()],
        "networks": [{"id": road[0], "kind": "ROAD", "start_id": road[1], "end_id": road[2],
            "mode": "GROUND", "width": 12, "level": "BRANCH",
            "geometry": {"points": road[4] if len(road) > 4 else [nodes[road[1]], nodes[road[2]]]},
            "car_access": {"forward": "allowed", "backward": "allowed" if road[3] else "forbidden",
                           "source": "fixture-native-lanes"}} for road in roads]})


@pytest.fixture(scope="module")
def node_router(tmp_path_factory):
    if not shutil.which("node") or not (VIEWER / "node_modules/typescript").exists():
        pytest.skip("Viewer Node dependencies are required")
    target = tmp_path_factory.mktemp("navigation-node")
    setup = r"""
const fs = require('node:fs'), path = require('node:path'), {pathToFileURL} = require('node:url');
const viewer = process.argv[1], output = process.argv[2];
const ts = require(path.join(viewer, 'node_modules/typescript'));
const modules = {'ngraph.graph': 'ngraph.graph/index.js', 'ngraph.path': 'ngraph.path/dist/ngraph.path.es.js', 'flatbush': 'flatbush/index.js'};
for (const name of ['types', 'geometry', 'itinerary', 'candidates', 'router']) {
  const source = fs.readFileSync(path.join(viewer, 'src/routing', name+'.ts'), 'utf8');
  let code = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022}}).outputText;
  code = code.replace(/from ['"]\.\/(\w+)['"]/g, "from './$1.mjs'");
  for (const [key, value] of Object.entries(modules)) code = code.replaceAll("'"+key+"'", JSON.stringify(pathToFileURL(path.join(viewer, 'node_modules', value)).href));
  fs.writeFileSync(path.join(output, name+'.mjs'), code);
}
"""
    subprocess.run(["node", "-e", setup, str(VIEWER), str(target)], check=True, capture_output=True, text=True)
    driver = target / "query.mjs"
    driver.write_text("""
import fs from 'node:fs';
import {Router} from './router.mjs';
import {transitCandidates} from './candidates.mjs';
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const router = new Router(input.package);
const results = [];
for (const query of input.queries) {
  results.push(await (async () => {
  try {
    if (query.combine) return await transitCandidates(query.combine, input.package.graphRevision,
      new Map(input.package.transit.edges.map(edge=>[edge.id,edge])), input.package.transit.costModel.preferences.balanced);
    if (query.snap) return router.snap(query.snap, query.profile);
    const snap = value => ({geometryRef: String(value[0]), fraction: value[1]});
    if (query.candidates) return await router.candidates(snap(query.origin), snap(query.destination));
    return router.route(snap(query.origin), snap(query.destination), query.knownRulesOnly, query.profile, query.costConfigId);
  } catch(error) { return {status: 'error', code: error.code, detail: error.message}; }
  })());
}
process.stdout.write(JSON.stringify({results, nodes: router.graph.getNodesCount(), links: router.graph.getLinksCount()}));
""", encoding="utf-8")
    def run(package, queries):
        result = subprocess.run(["node", str(driver)], input=json.dumps({"package": package, "queries": queries}),
                                text=True, encoding="utf-8", capture_output=True, check=True, timeout=15)
        return json.loads(result.stdout)
    return run


def query(origin, destination):
    return {"origin": origin, "destination": destination}


def test_access_roundtrip_and_old_map_has_no_invented_access():
    source = document([(11, 1, 2, True)])
    assert MapDocument.from_dict(source.to_dict()).networks[0].car_access == source.networks[0].car_access
    source.networks[0].car_access = None
    package = compile_navigation(source)
    assert package["edges"] == []
    assert package["diagnostics"]["excluded"] == {"car_access_unknown": 1}
    assert package["capabilities"]["carAccess"] == "missing"


def test_graph_revision_ignores_road_name_and_theme_cache():
    source = document([(11, 1, 2, True)])
    first = compile_navigation(source)
    source.networks[0].display_name = "人民路"
    second = compile_navigation(source)
    assert first["graphRevision"] == second["graphRevision"]
    assert first["snapshotId"] != second["snapshotId"]
    session = DocumentSession(source)
    assert session.navigation() is session.navigation()


def test_same_segment_partial_and_reverse(node_router):
    package = compile_navigation(document([(11, 1, 2, True)]))
    results = node_router(package, [query([11, .2], [11, .8]), query([11, .8], [11, .2]), query([11, .5], [11, .5])])
    assert [r["distanceM"] for r in results["results"]] == pytest.approx([60, 60, 0])
    assert results["results"][0]["edgeIds"] == ["road:11:f"]
    assert results["results"][1]["edgeIds"] == ["road:11:b"]
    assert results["nodes"] == 2
    assert results["links"] == 0


def test_oneway_detour_and_disconnected_crossing(node_router):
    package = compile_navigation(document([(11, 1, 2, False), (12, 2, 3, False), (13, 3, 4, False), (14, 4, 1, False)]))
    result = node_router(package, [query([11, .8], [11, .2])])["results"][0]
    assert result["distanceM"] == pytest.approx(340)
    assert result["edgeIds"] == ["road:11:f", "road:12:f", "road:13:f", "road:14:f", "road:11:f"]
    package = compile_navigation(document([(11, 1, 3, True), (12, 2, 4, True)]))
    assert node_router(package, [query([11, .2], [12, .8])])["results"][0]["code"] == "NO_PATH"


def test_parallel_edge_identity_and_curve_length(node_router):
    source = document([(11, 1, 2, False), (12, 1, 2, False, [[0, 0, 0], [50, 0, 50], [100, 0, 0]]),
                       (13, 4, 1, False), (14, 2, 3, False)])
    package = compile_navigation(source)
    result = node_router(package, [query([13, .5], [14, .5]), query([12, .25], [12, .75])])["results"]
    assert result[0]["edgeIds"] == ["road:13:f", "road:11:f", "road:14:f"]
    assert result[0]["distanceM"] == pytest.approx(200)
    assert result[1]["distanceM"] == pytest.approx(math.sqrt(5000))


def test_exact_junction_and_snap_candidates(node_router):
    source = document([(11, 1, 2, True), (12, 2, 3, False)])
    package = compile_navigation(source)
    results = node_router(package, [query([11, 1], [12, 0]), query([11, 1], [12, .5]), {"snap": [50, 0, 2]},
                                   {**query([11, .2], [12, .8]), "knownRulesOnly": True}, query([12, 0], [11, 1])])["results"]
    assert results[0]["distanceM"] == 0
    assert results[0]["geometry"] == [[100, 0, 0], [100, 0, 0]]
    assert results[1]["distanceM"] == pytest.approx(50)
    assert results[2][0]["fraction"] == pytest.approx(.5)
    assert results[2][0]["distanceM"] == pytest.approx(2)
    assert results[3]["code"] == "RULE_COVERAGE_INCOMPLETE"
    assert results[4]["distanceM"] == 0


def test_clipping_preserves_height_and_boundary_identity(node_router):
    source = document([(11, 1, 2, True)], {1: [0, 0, 0], 2: [100, 100, 0]})
    package = clip_navigation(compile_navigation(source), [20, -20, 80, 20])
    points = next(iter(package["geometries"].values()))
    assert points == [[20, 20, 0], [80, 80, 0]]
    assert len(package["nodes"]) == 2
    assert all(key.startswith("boundary:") for key in package["nodes"])
    ref = package["edges"][0]["geometryRef"]
    result = node_router(package, [query([ref, .2], [ref, .8])])["results"][0]
    assert result["distanceM"] == pytest.approx(math.sqrt(60**2*2)*.6)
    assert "RANGE_LIMITED" in result["warningCodes"]


def test_host_rejects_spoofed_distance_and_geometry(node_router):
    package = compile_navigation(document([(11, 1, 2, True)]))
    result = node_router(package, [query([11, .2], [11, .8])])["results"][0]
    result.update({key: package[key] for key in ("snapshotId", "graphRevision", "costRevision")})
    for key, fraction in (("origin", .2), ("destination", .8)):
        position = [100*fraction, 0, 0]
        result[key] = {"position": position, "snap": {"geometryRef": "11", "fraction": fraction, "position": position}}
    assert validate_route(package, result)["distanceM"] == pytest.approx(60)
    invalid = copy.deepcopy(result); invalid["distanceM"] += 10
    with pytest.raises(ValueError, match="distance"):
        validate_route(package, invalid)
    invalid = copy.deepcopy(result); invalid["geometry"][0] = [1000, 0, 0]
    with pytest.raises(ValueError, match="geometry"):
        validate_route(package, invalid)


def _stored_route(node_router):
    package = compile_navigation(document([(11, 1, 2, True)]))
    result = node_router(package, [query([11, .2], [11, .8])])["results"][0]
    result.update({key: package[key] for key in ("snapshotId", "graphRevision", "costRevision")})
    for key, fraction in (("origin", .2), ("destination", .8)):
        point = [100*fraction, 0, 0]
        result[key] = {"position": point, "snap": {"geometryRef": "11", "fraction": fraction, "position": point}}
    return package, validate_route(package, result)


def test_selected_route_survives_local_export_without_outside_coordinates(node_router):
    package, route = _stored_route(node_router)
    clipped = clip_navigation(package, [10, -10, 90, 10], selected_route=route)
    exported = clipped.pop("selectedRoute")
    assert exported["distanceM"] == pytest.approx(60)
    assert exported["graphRevision"] == clipped["graphRevision"]
    assert exported["origin"]["snap"]["fraction"] == pytest.approx(.125)
    assert validate_route(clipped, exported)["distanceM"] == pytest.approx(60)
    assert clip_navigation(package, [30, -10, 90, 10], selected_route=route)["selectedRoute"] is None


def test_invalid_optional_access_is_reported_without_breaking_the_map():
    source = document([(11, 1, 2, True)])
    source.networks[0].car_access["forward"] = []
    assert compile_navigation(source)["diagnostics"]["excluded"] == {"invalid_car_access": 1}


def test_oneway_loop_uses_occurrence_distance_and_shared_endpoint(node_router):
    source = document([(11, 1, 1, False, [[0, 0, 0], [100, 0, 0], [100, 0, 100], [0, 0, 0]])])
    package = compile_navigation(source)
    result = node_router(package, [query([11, 0], [11, 1]), query([11, .8], [11, .2])])["results"]
    assert result[0]["distanceM"] == 0
    assert result[1]["distanceM"] == pytest.approx(package["edges"][0]["lengthM"]*.4)


def test_export_embeds_navigation_and_escapes_road_names(tmp_path):
    from html.parser import HTMLParser
    from csmodernmap.preview_scene import PreviewSceneBundle
    from csmodernmap.web_export_v2 import payload_from_preview_bundle, render_export_html

    source = document([(11, 1, 2, True)])
    source.networks[0].display_name = '</SCRIPT><script>alert("road")</script>'
    session = DocumentSession(source)
    bundle_path = tmp_path / "scene"; bundle_path.mkdir()
    bundle = PreviewSceneBundle(bundle_path, "snapshot", 1,
        {"sources": {}, "navigation": {"schemaVersion": 1, "url": "navigation.json"}}, navigation_session=session)
    payload = payload_from_preview_bundle(bundle)
    assert payload["embedded"]["sources"]["navigation.json"]["edges"][0]["name"] == source.networks[0].display_name
    viewer = tmp_path / "viewer"; assets = viewer / "assets"; assets.mkdir(parents=True)
    (viewer / "index.html").write_text('<script src="./assets/index-test.js"></script><link href="./assets/index-test.css">', encoding="utf8")
    for name in ("index-test.js", "index-test.css", "maplibre-gl-csp-worker.js"):
        (assets / name).write_text("", encoding="utf8")
    for name in ("preview-errors.js", "THIRD_PARTY_LICENSES.txt"):
        (viewer / name).write_text("", encoding="utf8")
    class Parser(HTMLParser):
        count = 0
        def handle_starttag(self, tag, attrs):
            self.count += tag == "script"
    parser = Parser(); parser.feed(render_export_html(payload, viewer))
    assert parser.count == 4


def test_html_retains_network_but_drops_selected_route(tmp_path):
    from types import SimpleNamespace
    from csmodernmap.preview_scene import PreviewSceneBundle
    from csmodernmap.web_export_v2 import payload_from_preview_bundle
    package = compile_navigation(document([(11, 1, 2, True)]))
    bundle = PreviewSceneBundle(tmp_path, 'scene', 1, {'sources': {},
        'navigation': {'schemaVersion': 1, 'url': 'navigation.json'},
        'navigationRoute': {'includeInExports': True}, 'navigationRouteOmitted': 'old-warning'},
        navigation_session=SimpleNamespace(navigation=lambda: package))
    payload = payload_from_preview_bundle(bundle)
    assert payload['embedded']['sources']['navigation.json'] == package
    assert payload['manifest']['navigation']['schemaVersion'] == 1
    for key in ('navigationRoute', 'navigationRouteOmitted'):
        assert key not in payload['manifest'] and key not in payload


def test_export_can_omit_navigation_without_preparing_the_graph(tmp_path):
    from types import SimpleNamespace
    from csmodernmap.preview_scene import PreviewSceneBundle
    from csmodernmap.web_export_v2 import payload_from_preview_bundle

    def unexpected_compile():
        raise AssertionError("Navigation was disabled for this export")
    bundle = PreviewSceneBundle(tmp_path, "scene", 1, {
        "sources": {}, "navigation": {"schemaVersion": 1, "url": "navigation.json"}},
        navigation_session=SimpleNamespace(navigation=unexpected_compile))
    payload = payload_from_preview_bundle(bundle, navigation_enabled=False)
    assert "navigation" not in payload["manifest"]
    assert "navigation.json" not in payload["embedded"]["sources"]


def driving_document(roads, nodes=None):
    source = document(roads, nodes)
    for road in source.networks:
        road.car_speed = {"forward": 20.0, "backward": 20.0, "unit": "m/s", "source": "fixture-effective"}
    source.traffic = {"schemaVersion": "1.2", "segments": [
        {"segmentId": road.id, "lanes": [
            {"laneType": "Vehicle", "vehicleType": "Car", "width": 3.0,
             "laneDirection": direction, "congestion": 0.0, "maxSpeed": 1.0}
            for direction in ("Forward", "Backward")]} for road in source.networks]}
    return source


def test_driving_facts_roundtrip_and_cost_revision_excludes_topology():
    source = driving_document([(11, 1, 2, True)])
    assert MapDocument.from_dict(source.to_dict()).networks[0].car_speed == source.networks[0].car_speed
    first = compile_navigation(source)
    source.traffic["segments"][0]["lanes"][0]["congestion"] = .9
    second = compile_navigation(source)
    assert first["graphRevision"] == second["graphRevision"]
    assert first["costRevision"] != second["costRevision"]
    assert first["snapshotId"] != second["snapshotId"]
    assert second["edges"][0]["recommendedCostPerMeter"] > first["edges"][0]["recommendedCostPerMeter"]


@pytest.mark.parametrize("preference", ["traffic", "speed", "level"])
def test_recommended_route_uses_traffic_speed_and_class(node_router, preference):
    nodes = {1: [0, 0, 0], 2: [100, 0, 0], 3: [100, 0, 100], 4: [0, 0, 110]}
    source = driving_document([(11, 1, 2, True), (12, 2, 3, True), (13, 1, 4, True), (14, 4, 3, True)], nodes)
    for road in source.networks:
        road.level = "MAIN"
        if preference == "level":
            road.level = "BRANCH" if road.id in (11, 12) else "HIGHWAY"
        if preference == "speed" and road.id in (11, 12):
            road.car_speed.update(forward=10.0, backward=10.0)
    if preference == "traffic":
        for segment in source.traffic["segments"][:2]:
            for lane in segment["lanes"]:
                lane["congestion"] = .9
    package = compile_navigation(source)
    results = node_router(package, [query([11, 0], [12, 1]),
        {**query([11, 0], [12, 1]), "profile": "car-balanced"}])["results"]
    assert results[0]["edgeIds"] == ["road:11:f", "road:12:f"]
    assert results[0]["distanceM"] == pytest.approx(200)
    assert results[1]["edgeIds"] == ["road:13:f", "road:14:f"]
    assert results[1]["distanceM"] > results[0]["distanceM"]
    assert results[1]["durationSeconds"] == pytest.approx((110 + math.hypot(100, 10)) / 20 * 1.3)


def test_directional_partial_cost_host_validation_and_clipping(node_router):
    source = driving_document([(11, 1, 2, True)])
    source.networks[0].car_speed.update(forward=30, backward=10)
    source.traffic["segments"][0]["lanes"][1]["congestion"] = .9
    package = compile_navigation(source)
    queries = [{**query([11, a], [11, b]), "profile": "car-balanced"} for a, b in ((.2, .8), (.8, .2))]
    results = node_router(package, queries)["results"]
    for result, query_value, edge in zip(results, queries, package["edges"]):
        assert result["distanceM"] == pytest.approx(60)
        assert result["cost"] == pytest.approx(60*edge["recommendedCostPerMeter"])
        result.update({key: package[key] for key in ("snapshotId", "graphRevision", "costRevision")})
        for key in ("origin", "destination"):
            fraction = query_value[key][1]; point = [100*fraction, 0, 0]
            result[key] = {"position": point, "snap": {"geometryRef": "11", "fraction": fraction, "position": point}}
        validated = validate_route(package, result)
        assert validated["cost"] == pytest.approx(60*edge["recommendedCostPerMeter"])
        invalid = copy.deepcopy(result); invalid["cost"] += 10
        with pytest.raises(ValueError, match="cost"):
            validate_route(package, invalid)
        clipped = clip_navigation(package, [10, -10, 90, 10], selected_route=result)
        assert validate_route(clipped, clipped["selectedRoute"])["cost"] == pytest.approx(result["cost"])
    assert results[1]["cost"] > results[0]["cost"]


def test_legacy_prefab_units_and_explicit_missing_speed():
    source = driving_document([(11, 1, 2, True)])
    source.networks[0].car_speed = None
    legacy = compile_navigation(source)
    assert legacy["edges"][0]["speedLimitMps"] == pytest.approx(50/3.6)
    assert legacy["edges"][0]["speedSource"] == "cs1-prefab-legacy"
    source.networks[0].car_speed = {"forward": None, "backward": 12, "unit": "m/s", "source": "native"}
    current = compile_navigation(source)
    assert current["edges"][0]["speedLimitMps"] is None
    assert current["edges"][0]["recommendedCostPerMeter"] == pytest.approx(1.15)
    assert current["edges"][1]["speedLimitMps"] == 12


@pytest.mark.parametrize("game", ["cs1", "cs2"])
def test_duration_uses_directional_speed_and_partial_distance(node_router, game):
    source = driving_document([(11, 1, 2, True)])
    source.metadata.origin.game = game
    source.networks[0].car_speed.update(forward=20, backward=10)
    source.traffic['segments'][0]['lanes'][0]['congestion'] = .5
    package = compile_navigation(source)
    queries = [{**query([11, a], [11, b]), 'profile': profile}
               for profile in ('car-shortest', 'car-balanced') for a, b in ((.2, .8), (.8, .2), (.5, .5))]
    results = node_router(package, queries)['results']
    assert [result['durationSeconds'] for result in results] == pytest.approx([7.8, 7.8, 0, 7.8, 7.8, 0])
    assert all(result['durationModel'] == 'car-speed-traffic-v2' for result in results)
    assert results[0]['cost'] != results[3]['cost']


def test_duration_missing_limit_uses_assumed_60_kmh(node_router):
    source = driving_document([(11, 1, 2, True), (12, 2, 3, True)])
    source.networks[1].car_speed.update(forward=None, backward=None)
    package = compile_navigation(source)
    results = node_router(package, [query([11, .2], [11, .8]), query([11, .2], [12, .8])])['results']
    assert results[0]['durationSeconds'] == pytest.approx(3.9)
    assert results[1]['durationSeconds'] == pytest.approx(11.44)
    assert 'SPEED_LIMIT_MISSING' in results[1]['warningCodes']
    package.pop('durationModel')
    old = node_router(package, [query([11, .2], [11, .8])])['results'][0]
    assert old['durationSeconds'] == pytest.approx(3.9)
    assert old['durationModel'] == 'car-speed-traffic-v2'


@pytest.mark.parametrize('model, congestion, limit, seconds', [
    ('car-speed-traffic-v2', .5, 20, 7.8),
    ('car-speed-traffic-v2', None, 30, 4.68),
    ('car-speed-traffic-v2', None, 10, 7.8),
    ('car-speed-traffic-v2', 1, 20, 56.16),
    ('car-speed-traffic-v2', .9999, 20, 56.16),
    ('car-speed-traffic-v2', 1, 1, 78),
    ('car-speed-traffic-v2', .5, None, 9.36),
    ('car-speed-traffic-v2', None, None, 4.68),
    ('car-speed-limit-v1', 1, 20, 3),
    ('car-speed-limit-v1', 1, None, 3.6),
    (None, 1, 20, 56.16),
])
def test_duration_host_validation_and_clipping(node_router, model, congestion, limit, seconds):
    package = compile_navigation(driving_document([(11, 1, 2, True)]))
    if model is None:
        package.pop('durationModel')
    else:
        package['durationModel'] = model
    for edge in package['edges']:
        edge.update(congestion=congestion, speedLimitMps=limit)
    route = node_router(package, [query([11, .2], [11, .8])])['results'][0]
    assert route['durationSeconds'] == pytest.approx(seconds)
    assert 'TRAFFIC_BLOCKED' not in route['warningCodes']
    route.update({key: package[key] for key in ('snapshotId', 'graphRevision', 'costRevision')})
    for key, fraction in (('origin', .2), ('destination', .8)):
        point = [100*fraction, 0, 0]
        route[key] = {'position': point, 'snap': {'geometryRef': '11', 'fraction': fraction, 'position': point}}
    assert validate_route(package, route)['durationSeconds'] == pytest.approx(seconds)
    for invalid in [None, True, -1, float('nan'), float('inf'), 4]:
        with pytest.raises(ValueError, match='duration'):
            validate_route(package, {**route, 'durationSeconds': invalid})
    with pytest.raises(ValueError, match='duration model'):
        validate_route(package, {**route, 'durationModel': 'other'})
    clipped = clip_navigation(package, [10, -10, 90, 10], selected_route=route)
    assert validate_route(clipped, clipped['selectedRoute'])['durationSeconds'] == pytest.approx(seconds)


def test_duration_uses_explicit_legacy_limit(node_router):
    source = driving_document([(11, 1, 2, True)])
    source.networks[0].car_speed = None
    package = compile_navigation(source)
    route = node_router(package, [query([11, .1], [11, .9])])['results'][0]
    assert route['durationSeconds'] == pytest.approx(80/(50/3.6)*1.3)
    assert 'PREFAB_SPEED_LIMIT' in route['warningCodes']


def test_continue_only_at_native_junctions(node_router):
    nodes = {1: [0, 0, 0], 2: [100, 0, 0], 3: [200, 0, 0], 4: [300, 0, 0],
             5: [100, 0, 100], 6: [200, 0, -100]}
    roads = [(11, 1, 2, True), (12, 2, 3, True), (13, 3, 4, True)]
    source = driving_document(roads, nodes)
    for index, road in enumerate(source.networks):
        road.display_name = f'名称{index}'
        road.level = 'HIGHWAY'
    result = node_router(compile_navigation(source), [query([11, 0], [13, 1])])['results'][0]
    assert [step['kind'] for step in result['maneuvers']] == ['depart', 'arrive']
    assert result['maneuvers'][0]['distanceM'] == pytest.approx(300)
    assert result['roadNames'] == ['名称0', '名称1', '名称2']
    # 同名直行道路经过两个实际路口，保留两个继续提示。
    source = driving_document(roads + [(14, 2, 5, True), (15, 6, 3, False)], nodes)
    for road in source.networks:
        road.display_name = '同一条道路'
    result = node_router(compile_navigation(source), [query([11, 0], [13, 1])])['results'][0]
    assert [step['kind'] for step in result['maneuvers']] == ['depart', 'continue', 'continue', 'arrive']
    assert sum(step['distanceM'] for step in result['maneuvers']) == pytest.approx(300)


def test_curves_and_projected_crossings_are_not_turn_instructions(node_router):
    nodes = {1: [0, 0, 0], 2: [100, 0, 0], 3: [100, 0, 100],
             4: [50, 20, -50], 5: [150, 20, 50], 6: [100, 0, -100]}
    roads = [(11, 1, 2, True), (12, 2, 3, True), (13, 4, 5, True)]
    result = node_router(compile_navigation(driving_document(roads, nodes)), [query([11, 0], [12, 1])])['results'][0]
    assert [step['kind'] for step in result['maneuvers']] == ['depart', 'arrive']
    source = driving_document(roads + [(14, 2, 6, True)], nodes)
    result = node_router(compile_navigation(source), [query([11, 0], [12, 1])])['results'][0]
    assert [step['kind'] for step in result['maneuvers']] == ['depart', 'left', 'arrive']
