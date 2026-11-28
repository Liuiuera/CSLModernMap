"""Observe native-host messages while individual scene resources arrive or fail."""
import json
import os
from pathlib import Path

import pytest

from browser_support import browser_page, expect_color, make_bundle, screenshot
from road_fixtures import scene_from_lines
from csmodernmap.preview_scene import PreviewSceneServer

pytestmark = pytest.mark.skipif(os.environ.get('CSL_PREVIEW_BROWSER_TEST') != '1', reason='opt-in Chromium WebGL test')
VIEWER = Path(__file__).resolve().parents[2] / 'dev/CSLModernMapRenderer/viewer/dist'
EMPTY = {'type': 'FeatureCollection', 'features': []}


@pytest.fixture
def server():
    host = PreviewSceneServer(VIEWER)
    try:
        yield host
    finally:
        host.close()


@pytest.fixture
def page(browser_page, server):
    browser_page.add_init_script("""
        window.hold = new Set(); window.fail = new Set(); window.release = {}; window.read = new Set();
        const fetchResource = window.fetch.bind(window);
        window.fetch = async (url, options) => {
            const resource = new URL(url, location.href).pathname.split('/').slice(-2).join('/');
            if (fail.has(resource)) throw new Error('fixture: ' + resource);
            if (hold.has(resource)) await new Promise(done => release[resource] = done);
            const response = await fetchResource(url, options);
            const json = response.json.bind(response);
            response.json = async () => { const data = await json(); read.add(resource); return data; };
            return response;
        };
    """)
    browser_page.goto(server.start())
    browser_page.wait_for_function("() => events.some(e => e.type === 'ready')")
    return browser_page


def bundle(path, phase='base'):
    road = scene_from_lines([[(-80, 0, 0), (80, 0, 0)]], width=12)
    result = make_bundle(path, road)
    result.manifest['completion'] = phase
    return result


def source(scene, name, data):
    scene.manifest['sources'][name] = name + '.geojson'
    scene.manifest['sourceRevisions'][name] = 1
    (scene.root / (name + '.geojson')).write_text(json.dumps(data))


def dispatch(page, server, scene):
    (scene.root / 'manifest.json').write_text(json.dumps(scene.manifest))
    server.replace_bundle(scene)
    page.evaluate('c => { void window.cslPreview.dispatch(c); }', {
        'type': 'loadScene', 'sceneId': scene.scene_id, 'revision': scene.revision, 'manifestUrl': server.manifest_url})


def wait_event(page, scene, kind='sceneLoaded'):
    page.wait_for_function('([id, type]) => events.some(e => e.type === type && e.bundleId === id)',
                           arg=[scene.root.name, kind])


@pytest.mark.parametrize('held', ['roads', 'buildings'])
def test_empty_buildings_and_one_drawable_road(tmp_path, page, server, held):
    scene = bundle(tmp_path / 'base')
    scene.manifest['layers']['building'] = True
    source(scene, 'buildings', EMPTY)
    resource = f'base/{held}.geojson'
    page.evaluate('key => hold.add(key)', resource)
    dispatch(page, server, scene)
    page.wait_for_function('key => key in release', arg=resource)
    if held == 'buildings':
        wait_event(page, scene)
    else:
        # Observe after the empty source has actually been read and applied.
        page.wait_for_function("() => read.has('base/buildings.geojson')")
        page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        assert not page.evaluate("events.some(e => e.type === 'sceneLoaded')")
    page.evaluate('key => release[key]()', resource)
    wait_event(page, scene, 'scenePresentationReady')
    expect_color(screenshot(page, 'loading-road'), 0, 0, '#d84a4a')


def test_empty_city_finishes(tmp_path, page, server):
    scene = bundle(tmp_path / 'empty')
    source(scene, 'roads', EMPTY)
    dispatch(page, server, scene)
    wait_event(page, scene)


def test_water_can_present_while_roads_and_buildings_are_pending(tmp_path, page, server):
    scene = bundle(tmp_path / 'water')
    scene.manifest['layers'].update(water=True, building=True)
    scene.manifest['theme']['water'].update(fill='#246abc', opacity=1)
    source(scene, 'buildings', EMPTY)
    source(scene, 'water', {'type': 'FeatureCollection', 'features': [{
        'type': 'Feature', 'properties': {'geometryRole':'water'},
        'geometry': {'type':'Polygon', 'coordinates': [[[-.0006,-.0006],[.0006,-.0006],
                                                       [.0006,.0006],[-.0006,.0006],[-.0006,-.0006]]]}}]})
    page.evaluate("hold.add('water/roads.geojson'); hold.add('water/buildings.geojson')")
    dispatch(page, server, scene)
    wait_event(page, scene)
    assert page.evaluate("['water/roads.geojson', 'water/buildings.geojson'].every(k => k in release)")
    page.wait_for_function("() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(() => done(true))))")
    expect_color(screenshot(page, 'loading-water'), 20, 20, '#246abc')
    page.evaluate("() => Object.values(release).forEach(done => done())")


@pytest.mark.parametrize('base_presented,old_phase,new_revision,change_city', [
    (True, 'base', 1, False),
    (False, 'base', 1, False),
    (True, 'complete', 1, False),
    (True, 'complete', 2, False),
    (True, 'complete', 2, True),
])
def test_failed_update_only_keeps_a_presented_map_of_the_same_city(
        tmp_path, page, server, base_presented, old_phase, new_revision, change_city):
    base = bundle(tmp_path / 'base', old_phase)
    if not base_presented:
        page.evaluate("hold.add('base/roads.geojson')")
    dispatch(page, server, base)
    if base_presented:
        wait_event(page, base, 'scenePresentationReady')
    else:
        page.wait_for_function("() => 'base/roads.geojson' in release")
    full = bundle(tmp_path / 'full', 'complete')
    full.manifest['sourceRevisions']['roads'] = 2
    full.revision = full.manifest['revision'] = new_revision
    if change_city:
        full.scene_id = full.manifest['sceneId'] = 'another-city'
    page.evaluate("events.length = 0; fail.add('full/roads.geojson')")
    dispatch(page, server, full)
    if base_presented and not change_city:
        for attempt in range(2):
            if attempt:
                page.evaluate('events.length = 0')
                dispatch(page, server, full)
            wait_event(page, full, 'sceneDetailError')
            assert not page.evaluate('events.some(e => e.fatal)')
            expect_color(screenshot(page, 'loading-retained-road'), 0, 0, '#d84a4a')
    else:
        page.wait_for_function("() => events.some(e => e.type === 'renderError' && e.fatal)")
        assert not page.evaluate("events.some(e => e.type === 'sceneDetailError')")
        if not base_presented:
            page.evaluate("release['base/roads.geojson']()")


def test_traffic_toggle_failure_keeps_interactive_map(tmp_path, page, server):
    scene = bundle(tmp_path / 'city')
    scene.manifest['layers']['traffic'] = False
    source(scene, 'traffic', EMPTY)
    dispatch(page, server, scene)
    wait_event(page, scene, 'scenePresentationReady')
    page.evaluate("events.length = 0; fail.add('city/traffic.geojson')")
    page.evaluate('c => window.cslPreview.dispatch(c)', {
        'type':'setLayerVisibility', 'sceneId':scene.scene_id, 'revision':1, 'layer':'traffic', 'visible':True})
    assert page.evaluate("events.some(e => e.type === 'renderError' && e.fatal === false)")
    assert not page.evaluate('events.some(e => e.fatal)')
    expect_color(screenshot(page, 'loading-traffic-warning'), 0, 0, '#d84a4a')


def test_invalid_quality_can_start_viewer(browser_page, server):
    browser_page.add_init_script("window.__CSL_PREVIEW_QUALITY__ = {pixelScale: -1, lineDetail:'bad'}")
    browser_page.goto(server.start())
    browser_page.wait_for_function("() => events.some(e => e.type === 'ready')")
    assert not browser_page.evaluate('events.some(e => e.fatal)')
