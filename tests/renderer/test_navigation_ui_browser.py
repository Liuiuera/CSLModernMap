"""Opt-in navigation UI checks against the built Viewer and real routing Worker."""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")
expect, sync_playwright = playwright_api.expect, playwright_api.sync_playwright

from csmodernmap.cmm import DocumentSession
from csmodernmap.preview_scene import PreviewSceneServer, _traffic_features
from csmodernmap.web_export_v2 import payload_from_preview_bundle, render_export_html
from test_navigation import driving_document
from browser_support import load, make_bundle, screenshot
from road_fixtures import scene_from_lines
from test_traffic_direction import segment, lane, DIRECTION_BACKWARD

pytestmark = [pytest.mark.browser, pytest.mark.skipif(os.environ.get("CSL_PREVIEW_BROWSER_TEST") != "1",
                              reason="opt-in Chromium navigation test")]
VIEWER = Path(__file__).resolve().parents[2] / "dev/CSLModernMapRenderer/viewer/dist"


@pytest.fixture
def navigation_page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True,
            channel=os.environ.get("CSL_BROWSER_CHANNEL"),
            args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        page = browser.new_page(viewport={"width": 1100, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script("""
          window.events = []; window.routeQueries = []; window.routeResults = [];
          window.routeRequestsByProfile = {}; window.lastCandidateRequest = null;
          window.holdQueries = false; window.transitBatchQueries = 0;
          window.chrome = window.chrome || {};
          window.chrome.webview = {postMessage: s => window.events.push(JSON.parse(s)), addEventListener() {}};
          const NativeWorker = window.Worker;
          window.Worker = class extends NativeWorker {
            constructor(...args) {
              super(...args);
              this.addEventListener('message', event => {
                const message = event.data;
                if (message?.status !== 'ok') return;
                const enrich = (route, request) => ({...route, snapshotId: message.snapshotId,
                  graphRevision: message.graphRevision, costRevision: message.costRevision,
                  origin: request?.origin, destination: request?.destination});
                if (Array.isArray(message.result)) {
                  for (const route of message.result)
                    if (route?.costProfile === 'transit')
                      window.routeResults.push(enrich(route, window.lastCandidateRequest));
                } else if (message.result?.costProfile) {
                  window.routeResults.push(enrich(message.result,
                    window.routeRequestsByProfile[message.result.costProfile]));
                }
              });
            }
            postMessage(message, ...options) {
              if (message.type === 'routingInit') window.activeRoutingWorker = this;
              if (message.type === 'routeQuery' || message.type === 'transitCandidatesQuery') {
                if (message.type === 'routeQuery') window.routeRequestsByProfile[message.profile] = message;
                else window.lastCandidateRequest = message;
                window.routeQueries.push(message.profile || 'transit');
                if (message.type === 'transitCandidatesQuery') { window.transitBatchQueries++; window.lastCandidateMessage = message; }
                if (window.holdQueries) return;
              }
              super.postMessage(message, ...options);
            }
          };
        """)
        yield page
        browser.close()
        assert errors == []


@pytest.fixture
def navigation_bundle(tmp_path):
    nodes = {1: [0, 0, 0], 2: [100, 0, 0], 3: [100, 0, 100], 4: [0, 0, 110],
             5: [300, 0, 0], 6: [300, 0, 100]}
    source = driving_document([(11, 1, 2, True), (12, 2, 3, True),
        (13, 1, 4, True), (14, 4, 3, True), (15, 5, 6, True)], nodes)
    for road in source.networks:
        road.display_name = f"道路{road.id}"
    for traffic_segment in source.traffic["segments"][:2]:
        for traffic_lane in traffic_segment["lanes"]:
            traffic_lane["congestion"] = .9
    lines = [[(p.x, p.y, p.z) for p in road.geometry.points] for road in source.networks]
    scene = scene_from_lines(lines)
    bundle = make_bundle(tmp_path / "scene", scene, colorize=False,
                         region=[-30, -30, 350, 150])
    traffic = segment([lane(3.5, 1), lane(-3.5, 0, DIRECTION_BACKWARD)])
    (bundle.root / 'traffic.geojson').write_text(json.dumps({'type': 'FeatureCollection',
        'features': _traffic_features(traffic, scene.roads[-1])}), encoding='utf-8')
    bundle.manifest['sources']['traffic'] = 'traffic.geojson?revision=1'
    bundle.manifest['sourceRevisions']['traffic'] = 1
    bundle.manifest['layers']['traffic'] = True
    bundle.navigation_session = DocumentSession(source)
    bundle.manifest["navigation"] = {"schemaVersion": 1, "url": "navigation.json"}
    (bundle.root / "manifest.json").write_text(json.dumps(bundle.manifest), encoding="utf-8")
    return bundle


@pytest.fixture
def navigation_server(navigation_bundle):
    server = PreviewSceneServer(VIEWER)
    server.replace_bundle(navigation_bundle)
    yield server
    server.close()


def open_navigation(page, server):
    page.goto(server.start() + '?desktopUi=1')
    page.wait_for_function("window.events.some(e => e.type === 'ready')")
    load(page, server, 1)
    page.locator('.navigation-control > button').click()


def choose(page, endpoint, coordinate, road):
    page.locator('.navigation-endpoint input').nth(endpoint).click()
    details = page.locator('.navigation-panel > details').first
    if not details.evaluate('(e) => e.open'):
        details.locator('summary').click()
    page.locator('.navigation-actions input').fill(coordinate)
    page.get_by_role('button', name='使用坐标', exact=True).click()
    page.wait_for_function("""({index, road}) => document.querySelectorAll('.navigation-endpoint-name')[index].value === road
        && document.querySelectorAll('.navigation-endpoint')[index].getAttribute('aria-pressed') === 'false'""",
        arg={"index": endpoint, "road": road or '未命名道路'})


def make_route(page):
    choose(page, 0, '20, 0', '道路11')
    choose(page, 1, '100, 80', '道路12')
    expect(page.locator('.navigation-route-option:enabled')).to_have_count(2)
    expect(page.locator('.navigation-map-card')).to_have_count(1)


def last_route(page):
    return page.evaluate("""() => {
        const card = document.querySelector('.navigation-route-option[aria-pressed="true"], .navigation-route-card[aria-pressed="true"]');
        if (!card || card.disabled) return null;
        const candidateId = card.dataset.candidateId, profile = card.dataset.profile;
        const route = [...window.routeResults].reverse().find(candidate =>
          candidateId ? candidate.candidateId === candidateId : candidate.costProfile === profile);
        if (!route) return null;
        const request = profile === 'transit' ? window.lastCandidateRequest
          : window.routeRequestsByProfile[profile];
        return {...route, origin: route.origin ?? request?.origin,
          destination: route.destination ?? request?.destination};
    }""")


def test_two_profiles_and_responsive_layout(navigation_page, navigation_server, navigation_bundle):
    page = navigation_page
    open_navigation(page, navigation_server)
    make_route(page)
    assert page.evaluate('window.routeQueries') == ['car-balanced', 'car-shortest']
    recommended = last_route(page)
    assert recommended['costProfile'] == 'car-balanced'
    assert recommended['durationSeconds'] == pytest.approx(((recommended['distanceM']-40) / 20 + 40/2) * 1.3)
    expect(page.locator('.navigation-map-card .navigation-duration')).to_have_text('不足 1 分钟')
    assert page.locator('.navigation-duration').first.evaluate('(e) => parseFloat(getComputedStyle(e).fontSize)') == 28
    expect(page.locator('.navigation-distance').first).to_contain_text('按限速与导出时路况估算')
    assert page.locator('.navigation-distance').first.evaluate('(e) => parseFloat(getComputedStyle(e).fontSize)') < 24
    expect(page.locator('.navigation-export, .navigation-information')).to_have_count(0)
    page.locator('[data-profile="car-shortest"]').click()
    shortest = last_route(page)
    assert shortest['durationSeconds'] == pytest.approx(104)
    assert shortest['distanceM'] < recommended['distanceM']
    assert page.evaluate('window.routeQueries.length') == 2
    assert 'includeInExports' not in shortest
    assert page.evaluate("events.filter(e => e.type === 'routeChanged').length") == 0
    screenshot(page, 'navigation-floating-light')
    page.evaluate("window.cslPreview.dispatch({type:'setUiAppearance',appearance:'dark'})")
    expect(page.locator('html')).to_have_attribute('data-ui-appearance', 'dark')
    screenshot(page, 'navigation-floating-dark')
    page.set_viewport_size({'width': 360, 'height': 740})
    panel = page.locator('.navigation-panel')
    assert panel.evaluate('(e) => e.scrollWidth <= e.clientWidth')
    bounds = panel.bounding_box()
    assert bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= 360
    page.locator('.navigation-steps > summary').click()
    expect(page.locator('.navigation-steps li')).not_to_have_count(0)
    screenshot(page, 'navigation-floating-narrow')
    page.get_by_role('button', name='清空路线', exact=True).click()
    expect(page.locator('.navigation-route-card')).to_have_count(0)
    expect(page.locator('.navigation-steps li')).to_have_count(0)
    assert last_route(page) is None


def test_failed_recalculation_clears_previous_distance_and_steps(navigation_page, navigation_server):
    page = navigation_page
    open_navigation(page, navigation_server)
    make_route(page)
    choose(page, 1, '300, 50', '道路15')
    expect(page.locator('.navigation-route-card')).to_have_count(0)
    expect(page.locator('.navigation-route-option')).to_have_count(2)
    expect(page.locator('.navigation-route-option:enabled')).to_have_count(0)
    expect(page.locator('.navigation-distance')).to_have_count(0)
    expect(page.locator('.navigation-steps li')).to_have_count(0)
    expect(page.get_by_role('button', name='重试', exact=True)).to_be_visible()
    assert last_route(page) is None


def test_cancel_retry_and_city_switch_discard_pending_results(navigation_page, navigation_server, navigation_bundle):
    page = navigation_page
    open_navigation(page, navigation_server)
    make_route(page)
    page.evaluate('window.holdQueries = true')
    page.get_by_role('button', name='互换', exact=True).click()
    expect(page.locator('.navigation-duration').first).to_have_text('正在查找道路路线…')
    page.get_by_role('button', name='取消', exact=True).click()
    expect(page.locator('.navigation-route-card')).to_have_count(0)
    assert last_route(page) is None
    page.evaluate('window.holdQueries = false')
    page.get_by_role('button', name='重试', exact=True).click()
    expect(page.locator('.navigation-route-option:enabled')).to_have_count(2)
    page.evaluate('window.holdQueries = true')
    page.get_by_role('button', name='互换', exact=True).click()
    expect(page.locator('.navigation-duration').first).to_have_text('正在查找道路路线…')
    navigation_bundle.manifest.update(sceneId='another-city', revision=2)
    navigation_bundle.scene_id = 'another-city'
    navigation_bundle.revision = 2
    (navigation_bundle.root / 'manifest.json').write_text(json.dumps(navigation_bundle.manifest), encoding='utf-8')
    load(page, navigation_server, 2)
    expect(page.locator('.navigation-panel')).to_be_hidden()
    expect(page.locator('.navigation-route-card')).to_have_count(0)
    assert last_route(page) is None
    page.locator('.navigation-control > button').click()
    expect(page.locator('.navigation-endpoint-name').first).to_have_value('')
    expect(page.locator('.navigation-endpoint-name').last).to_have_value('')


def test_offline_html_omits_selection_and_recomputes_choices(tmp_path, navigation_page, navigation_server, navigation_bundle):
    page = navigation_page
    open_navigation(page, navigation_server)
    make_route(page)
    page.locator('[data-profile="car-shortest"]').click()
    payload = payload_from_preview_bundle(navigation_bundle)
    assert 'navigationRoute' not in payload['manifest']
    output = tmp_path / 'navigation.html'
    output.write_text(render_export_html(payload, VIEWER), encoding='utf-8')
    page.context.set_offline(True)
    page.goto(output.as_uri())
    expect(page.locator('.navigation-route-card')).to_have_count(0)
    expect(page.locator('.navigation-pin')).to_have_count(0)
    page.locator('.navigation-control > button').click()
    make_route(page)
    expect(page.locator('.navigation-route-option:enabled')).to_have_count(2)
    expect(page.locator('[data-profile="car-balanced"]')).to_have_attribute('aria-pressed', 'true')
    expect(page.locator('input[data-layer="traffic"]')).to_be_checked()
    assert page.evaluate('window.__CSL_SCENE__.manifest.layers.traffic') is True
    expect(page.locator('.navigation-map-card .navigation-duration')).to_have_text('不足 1 分钟')
    page.locator('[data-profile="car-balanced"]').click()
    expect(page.locator('[data-profile="car-balanced"]')).to_have_attribute('aria-pressed', 'true')
    assert page.evaluate('window.routeQueries.length') == 2


def test_distance_only_snapshot_has_one_working_card(navigation_page, navigation_server, navigation_bundle):
    package = navigation_bundle.navigation_payload().copy()
    package.pop('costModel')
    package.pop('durationModel')
    navigation_bundle.navigation_session = SimpleNamespace(navigation=lambda: package)
    page = navigation_page
    open_navigation(page, navigation_server)
    choose(page, 0, '20, 0', '道路11')
    choose(page, 1, '100, 80', '道路12')
    expect(page.locator('.navigation-route-option:enabled')).to_have_count(1)
    expect(page.locator('[data-profile="car-balanced"]')).to_have_count(0)
    assert page.evaluate('window.routeQueries') == ['car-shortest']
    assert last_route(page)['costProfile'] == 'car-shortest'
    assert last_route(page)['durationSeconds'] == pytest.approx(104)
    expect(page.locator('.navigation-map-card .navigation-duration')).to_have_text('约 2 分钟')


@pytest.mark.parametrize('congestion, model, seconds, label', [
    (1, 'car-speed-traffic-v2', 149.76, '约 3 分钟'),
    (None, 'car-speed-traffic-v2', 12.48, '不足 1 分钟'),
    (1, 'car-speed-limit-v1', 160/30, '不足 1 分钟'),
])
def test_traffic_duration_states(navigation_page, navigation_server, navigation_bundle, congestion, model, seconds, label):
    package = navigation_bundle.navigation_payload().copy()
    package['durationModel'] = model
    package['edges'] = [{**edge, 'congestion': congestion, 'speedLimitMps': 30} for edge in package['edges']]
    navigation_bundle.navigation_session = SimpleNamespace(navigation=lambda: package)
    page = navigation_page
    open_navigation(page, navigation_server)
    make_route(page)
    page.locator('[data-profile="car-shortest"]').click()
    assert last_route(page)['durationSeconds'] == pytest.approx(seconds)
    expect(page.locator('.navigation-map-card .navigation-duration')).to_have_text(label)
    if congestion is None:
        expect(page.locator('.navigation-panel')).to_contain_text('部分路段无路况')


def test_planning_preserves_traffic_visibility(navigation_page, navigation_server):
    page = navigation_page
    open_navigation(page, navigation_server)
    make_route(page)
    page.locator('[data-profile="car-shortest"]').click()
    assert page.evaluate("window.events.filter(e => e.type === 'navigationStarted').length") == 0
    page.get_by_role('button', name='互换', exact=True).click()
    expect(page.locator('.navigation-route-option:enabled')).to_have_count(2)
    page.locator('.navigation-endpoint').first.click()
    assert page.evaluate("window.events.filter(e => e.type === 'navigationStarted').length") == 0
    assert not page.evaluate("window.events.filter(e => e.type === 'renderError')")


@pytest.mark.parametrize('speed, label', [(1, '约 4 分钟'), (.04, '约 1 小时 27 分钟'), (None, '不足 1 分钟')])
def test_duration_labels_and_missing_speed(navigation_page, navigation_server, navigation_bundle, speed, label):
    package = navigation_bundle.navigation_payload().copy()
    package['edges'] = [{**edge, 'speedLimitMps': speed, 'congestion': 0} for edge in package['edges']]
    navigation_bundle.navigation_session = SimpleNamespace(navigation=lambda: package)
    page = navigation_page
    open_navigation(page, navigation_server)
    make_route(page)
    page.locator('[data-profile="car-shortest"]').click()
    expect(page.locator('.navigation-map-card .navigation-duration')).to_have_text(label)
    if speed == .04:
        page.set_viewport_size({'width': 360, 'height': 740})
        assert page.locator('.navigation-panel').evaluate('(e) => e.scrollWidth <= e.clientWidth')
        screenshot(page, 'navigation-duration-hours-narrow')
    if speed is None:
        assert last_route(page)['durationSeconds'] > 0
        expect(page.locator('.navigation-distance').first).to_contain_text('按限速与导出时路况估算')
        expect(page.locator('.navigation-panel')).to_contain_text('部分路段无路况')


def test_planning_preserves_pending_traffic_fetch(navigation_page, navigation_server, navigation_bundle):
    from test_preview_3d_browser import expose_map
    page = navigation_page
    expose_map(page)
    open_navigation(page, navigation_server)
    page.evaluate("window.cslPreview.dispatch({type:'setLayerVisibility',layer:'traffic',visible:false})")
    page.locator('.navigation-control > button').click()
    pending = []
    page.route('**/traffic.geojson*', lambda route: pending.append(route))
    page.evaluate("() => {void window.cslPreview.dispatch({type:'setLayerVisibility',layer:'traffic',visible:true})}")
    page.wait_for_timeout(150)
    assert len(pending) == 1
    page.locator('.navigation-control > button').click()
    pending[0].fulfill(body=(navigation_bundle.root / 'traffic.geojson').read_text(encoding='utf-8'),
                       content_type='application/json')
    page.wait_for_function("() => !!__testMap.getSource('traffic') && __testMap.isSourceLoaded('traffic')")
    assert not page.evaluate("window.events.filter(e => e.type === 'renderError')")
    assert not page.evaluate("window.events.filter(e => e.type === 'navigationStarted')")


def test_building_click_selects_nearest_road_and_keeps_place(navigation_page, navigation_server, navigation_bundle):
    from csmodernmap.preview_scene import world_to_geo
    building = {"type": "Feature", "properties": {"buildingId": 71, "name": "中央图书馆"},
                "geometry": {"type": "Polygon", "coordinates": [[world_to_geo(x, z)
                    for x, z in [(140, 40), (180, 40), (180, 80), (140, 80), (140, 40)]]]}}
    (navigation_bundle.root / 'buildings.geojson').write_text(json.dumps({"type": "FeatureCollection", "features": [building]}), encoding='utf-8')
    navigation_bundle.manifest['sources']['buildings'] = 'buildings.geojson?revision=1'
    navigation_bundle.manifest['layers']['building'] = True
    navigation_bundle.manifest['renderStack'].append('building')
    (navigation_bundle.root / 'manifest.json').write_text(json.dumps(navigation_bundle.manifest), encoding='utf-8')
    page = navigation_page
    open_navigation(page, navigation_server)
    page.locator('.navigation-endpoint').first.click()
    page.mouse.click(550, 450)
    expect(page.locator('.navigation-endpoint-name').first).to_have_value('中央图书馆')
    expect(page.locator('.navigation-endpoint-access')).to_have_count(0)
    expect(page.locator('.navigation-candidates button')).to_have_count(0)
    expect(page.locator('.navigation-pin.origin')).to_have_count(1)
    choose(page, 1, '20, 0', '道路11')
    expect(page.locator('.navigation-route-option:enabled')).to_have_count(2)
    expect(page.locator('.navigation-endpoint-name').first).to_have_value('中央图书馆')
    screenshot(page, 'navigation-building-selected')
    expect(page.get_by_text('更换接入道路', exact=True)).to_have_count(0)
    page.locator('#navigation-origin').click()
    page.get_by_role('button', name='取消', exact=True).click()
    expect(page.locator('.navigation-endpoint-name').first).to_have_value('中央图书馆')


def test_transit_mode_transfer_details_and_offline_html(tmp_path, navigation_page, navigation_server, navigation_bundle):
    from test_navigation_transit import transit_document
    source = transit_document()
    for road in source.networks:
        road.display_name = f'道路{road.id}'
    navigation_bundle.navigation_session = DocumentSession(source)
    page = navigation_page
    open_navigation(page, navigation_server)
    page.get_by_role('button', name='公共交通', exact=True).click()
    choose(page, 0, '20, 0', '道路11')
    choose(page, 1, '5980, 0', '道路12')
    expect(page.locator('[data-profile="transit"]:enabled')).to_have_count(1)
    expect(page.locator('.navigation-duration')).to_have_text('1 次换乘 · 步行 180 m')
    expect(page.locator('.navigation-leg.ride')).to_have_count(2)
    selected = last_route(page)
    assert selected['costProfile'] == 'transit'
    page.get_by_role('button', name='查看整条路线', exact=True).click()
    page.locator('.navigation-open-details').click()
    page.wait_for_timeout(500)
    screenshot(page, 'navigation-transit-transfer')
    page.evaluate("window.cslPreview.dispatch({type:'setUiAppearance',appearance:'dark'})")
    screenshot(page, 'navigation-transit-dark')
    page.set_viewport_size({'width': 360, 'height': 740})
    assert page.locator('.navigation-panel').evaluate('(e) => e.scrollWidth <= e.clientWidth')
    screenshot(page, 'navigation-transit-narrow')
    page.set_viewport_size({'width': 1100, 'height': 900})
    payload = payload_from_preview_bundle(navigation_bundle)
    output = tmp_path / 'transit.html'
    output.write_text(render_export_html(payload, VIEWER), encoding='utf-8')
    page.context.set_offline(True)
    page.goto(output.as_uri())
    expect(page.locator('.navigation-route-card')).to_have_count(0)
    page.locator('.navigation-control > button').click()
    page.get_by_role('button', name='公共交通', exact=True).click()
    choose(page, 0, '20, 0', '道路11')
    choose(page, 1, '5980, 0', '道路12')
    expect(page.locator('[data-profile="transit"]:enabled')).to_have_count(1)
    expect(page.locator('.navigation-duration')).to_have_text('1 次换乘 · 步行 180 m')
    page.get_by_role('button', name='驾车', exact=True).click()
    expect(page.locator('.navigation-route-option:enabled')).to_have_count(2)
    assert last_route(page)['costProfile'] == 'car-balanced'


def test_preview_controls_switch_to_each_shipped_language(navigation_page, navigation_server):
    page = navigation_page
    open_navigation(page, navigation_server)
    catalogs = VIEWER.parent / 'src' / 'locales'
    languages = json.loads((VIEWER.parents[1] / 'resources/i18n/languages.json').read_text(encoding='utf-8'))
    for option in languages:
        path = catalogs / (option['code'] + '.json')
        messages = json.loads(path.read_text(encoding='utf-8'))
        page.evaluate("language => window.cslPreview.dispatch({type: 'setLanguage', language})", path.stem)
        expect(page.locator("button[data-preview-icon='zoom-in'] .preview-button-label").first).to_have_text(
            messages['controls_zoomIn'])
        expect(page.locator("button[data-preview-icon='3d-cube-sphere']").first).to_have_attribute(
            'aria-label', messages['preview3d_title'])
        expect(page.locator('.navigation-swap')).to_have_attribute('aria-label', messages['navigation_swap'])
