"""Quality changes exercise the production MapLibre sources and renderer."""
import copy
import json
import os

import pytest

from test_navigation_ui_browser import (navigation_page, navigation_bundle, navigation_server,
    make_route, VIEWER, expect)
from browser_support import load
from test_preview_3d_browser import buildings, expose_map, instrument
from csmodernmap.web_export_v2 import payload_from_preview_bundle, render_export_html
from urllib.parse import urljoin

pytestmark = [pytest.mark.browser, pytest.mark.skipif(os.environ.get('CSL_PREVIEW_BROWSER_TEST') != '1', reason='opt-in WebGL test')]
STANDARD = dict(pixelScale=1, lineDetail='standard', polygonDetail='standard')
FINE = dict(pixelScale=1.25, lineDetail='fine', polygonDetail='fine')


def apply(page, quality, request='quality-test', outcome='applied'):
    page.evaluate('(c)=>window.cslPreview.dispatch(c)',
        dict(type='setPreviewQuality', requestId=request, quality=quality))
    page.wait_for_function('(r)=>events.some(e=>e.type==="previewQualityApplied" && e.requestId===r)', arg=request)
    assert page.evaluate('(r)=>events.findLast(e=>e.type==="previewQualityApplied" && e.requestId===r).outcome', request) == outcome
    page.wait_for_function('()=>__testMap.loaded() && !__testMap.isMoving()')


def snapshot(page):
    return page.evaluate('''()=>{const m=__testMap,s=m.getStyle();return {
        camera:[m.getCenter().toArray(),m.getZoom(),m.getBearing(),m.getPitch()],
        layers:s.layers, sources:s.sources, ratio:m.getPixelRatio(),
        canvas:[m.getCanvas().width,m.getCanvas().height]};}''')


def prepare(bundle):
    buildings(bundle)
    for key in ['labels', 'railway', 'zones']:
        (bundle.root/f'{key}.geojson').write_text(json.dumps(dict(type='FeatureCollection', features=[])))
        bundle.manifest['sources'][key] = f'{key}.geojson?revision=1'
        bundle.manifest['sourceRevisions'][key] = 1
    (bundle.root/'manifest.json').write_text(json.dumps(bundle.manifest), encoding='utf-8')


def test_district_far_cap_after_zooming_out(navigation_page, navigation_bundle, navigation_server):
    page = navigation_page
    prepare(navigation_bundle)
    manifest = navigation_bundle.manifest
    manifest['layers']['label'] = True
    manifest['theme']['label']['metrics']['sizes']['district'] = 24
    label = dict(type='Feature', geometry=dict(type='Point', coordinates=[0, 0]),
                 properties=dict(featureId='district:1', labelScope='district', text='泉都岛'))
    (navigation_bundle.root/'labels.geojson').write_text(json.dumps(dict(type='FeatureCollection', features=[label])))
    (navigation_bundle.root/'manifest.json').write_text(json.dumps(manifest))
    expose_map(page)
    load(page, navigation_server, 1)
    def zoom(level):
        page.evaluate('z=>{__testMap.jumpTo({center:[0,0],zoom:z})}', level)
        page.evaluate("() => new Promise(done => {__testMap.once('idle', () => done()); __testMap.triggerRepaint()})")
        assert page.evaluate("__testMap.queryRenderedFeatures({layers:['district-labels']}).length") > 0
        return page.evaluate("__testMap.getLayoutProperty('district-labels','text-size')")
    assert zoom(11) == 10
    assert zoom(17) == 30
    apply(page, {**STANDARD, 'textPercent': 150})
    assert zoom(11) == 10
    assert zoom(12) == 23
    assert zoom(15) == 36


def test_map_and_transport_icon_sizes_follow_zoom_and_middle_preference(navigation_page, navigation_bundle, navigation_server):
    page = navigation_page
    prepare(navigation_bundle)
    navigation_bundle.manifest['theme']['poi']['size'] = 18
    (navigation_bundle.root/'manifest.json').write_text(json.dumps(navigation_bundle.manifest))
    expose_map(page)
    load(page, navigation_server, 1)
    page.evaluate("()=>cslPreview.dispatch({type:'setContextMode', mode:'transit', options:{facility_icon_size:32}})")
    def sizes(zoom):
        page.evaluate("z=>{__testMap.jumpTo({zoom:z})}", zoom)
        return page.evaluate("['pois','transit-pois','tv-poi'].map(id=>__testMap.getLayoutProperty(id,'icon-size')*24)")
    assert sizes(17) == [22.5, 22.5, 40]
    assert sizes(11) == [9, 9, 12]
    assert sizes(14) == [18, 18, 32]
    apply(page, {**STANDARD, 'iconPercent': 150})
    assert sizes(14) == [27, 27, 48]
    assert sizes(11) == [12, 12, 12]
    assert sizes(17) == [33.75, 33.75, 60]
    apply(page, {**STANDARD, 'iconPercent': 0}, 'invalid-icons', 'error')
    assert sizes(17) == [33.75, 33.75, 60]


def test_quality_preserves_sources_camera_layers_navigation_and_updates(navigation_page, navigation_bundle, navigation_server):
    page = navigation_page
    prepare(navigation_bundle)
    expose_map(page)
    load(page, navigation_server, 1)
    page.locator('.navigation-control > button').click()
    make_route(page)
    page.get_by_role('button', name='3D 预览', exact=True).click()
    page.wait_for_function('()=>!__testMap.isMoving() && __testMap.getPitch()>50')
    page.evaluate('''()=>{__testMap.setLayoutProperty('buildings','visibility','none');
        window.originalRoadSource=__testMap.getSource('roads');
        window.originalNavigationSource=__testMap.getSource('navigation');}''')
    before = snapshot(page)
    apply(page, {**STANDARD, 'pixelScale': .75}, 'smooth')
    assert page.evaluate("__testMap.getSource('roads')===originalRoadSource")
    assert snapshot(page)['canvas'] == [825, 675]
    apply(page, FINE, 'fine')
    after = snapshot(page)
    assert after['camera'] == before['camera']
    assert after['layers'] == before['layers']
    assert after['canvas'] == [1375, 1125]
    for key in ['roads', 'labels', 'railway', 'buildings', 'zones']:
        original = copy.deepcopy(before['sources'][key])
        original['tolerance'] = .375 if key in ['roads', 'labels', 'railway'] else .125
        assert after['sources'][key] == original
    assert page.evaluate("__testMap.getSource('navigation')===originalNavigationSource")
    expect(page.locator('.navigation-map-card')).to_have_count(1)
    assert page.evaluate('__testMap.queryRenderedFeatures({layers:["buildings-3d"]}).length') > 0
    apply(page, dict(pixelScale=.5, lineDetail='standard', polygonDetail='fine'), 'invalid', 'error')
    assert snapshot(page) == after
    apply(page, STANDARD, 'standard')
    restored = snapshot(page)
    assert restored['sources'] == before['sources']
    assert restored['layers'] == before['layers']
    # A subsequent engine revision still updates the replacement sources.
    navigation_bundle.manifest['revision'] = 2
    navigation_bundle.manifest['sourceRevisions']['buildings'] = 2
    source = json.loads((navigation_bundle.root/'buildings.geojson').read_text())
    source['features'][0]['properties']['name'] = 'updated after quality switch'
    (navigation_bundle.root/'buildings.geojson').write_text(json.dumps(source))
    (navigation_bundle.root/'manifest.json').write_text(json.dumps(navigation_bundle.manifest))
    load(page, navigation_server, 2)
    assert page.evaluate('__testMap.getStyle().sources.buildings.data.features[0].properties.name') == 'updated after quality switch'
    assert not page.evaluate('events.filter(e=>e.type==="renderError")')


@pytest.mark.parametrize('preset,ratio,line,polygon', [
    ('smooth', .75, 1, .375), ('standard', 1, 1, .375), ('fine', 1.25, .375, .125)])
def test_initial_quality_and_export_default_are_independent(navigation_page, navigation_bundle, navigation_server, tmp_path,
                                                           preset, ratio, line, polygon):
    page = navigation_page
    prepare(navigation_bundle)
    page.add_init_script(f'window.__CSL_PREVIEW_QUALITY__={json.dumps(FINE)}')
    expose_map(page)
    load(page, navigation_server, 1)
    initial = snapshot(page)
    assert initial['ratio'] == 1.25
    assert initial['sources']['roads']['tolerance'] == .375
    # A fresh standalone document has no WinUI setting injection.
    exported = tmp_path/'quality-independent.html'
    exported.write_text(instrument(render_export_html(payload_from_preview_bundle(navigation_bundle, html_quality=preset), VIEWER)), encoding='utf-8')
    context = page.context.browser.new_context()
    fresh = context.new_page()
    try:
        fresh.add_init_script(f'window.__CSL_PREVIEW_QUALITY__={json.dumps(FINE)}')
        # Init scripts belong to the first page, not the context or export.
        fresh.goto(exported.as_uri())
        fresh.wait_for_function('()=>window.__testMap?.getSource("buildings") && __testMap.loaded()')
        state = snapshot(fresh)
        assert state['ratio'] == ratio
        assert state['sources']['roads']['tolerance'] == line
        assert state['sources']['buildings']['tolerance'] == polygon
        assert 'htmlQuality' not in navigation_bundle.manifest
    finally:
        context.close()


def test_live_quandu_sample_uses_production_renderer_and_syncs_view(navigation_page, navigation_server):
    page = navigation_page
    page.set_viewport_size({'width': 772, 'height': 340})
    requests = []
    page.on('request', lambda request: requests.append(request.url) if request.url.startswith(('http:', 'https:')) else None)
    page.add_init_script('window.chrome.webview.addEventListener = (name, handler) => window.sampleHostMessage = handler')
    expose_map(page)
    page.goto(urljoin(navigation_server.start(), 'quality-demo.html'))
    page.wait_for_function('()=>events.some(e=>e.type==="qualityDemoReady")')
    frames = [frame for frame in page.frames if 'qualitySample=1' in frame.url]
    assert len(frames) == 2
    left, right = frames
    assert 0 < left.evaluate('__testMap.getStyle().sources.roads.data.features.length') < 2000
    assert 50 < left.evaluate('__testMap.getStyle().sources.buildings.data.features.length') < 5000
    assert left.evaluate("__testMap.queryRenderedFeatures({layers:['pois']}).length") > 0
    base_icon = left.evaluate("__testMap.getLayoutProperty('pois','icon-size')")
    state = dict(type='setQualityDemo', quality={**FINE, 'iconPercent': 150},
        baselineLabel='Standard', draftLabel='Current', appearance='light', language='en')
    def send():
        page.evaluate('(data)=>sampleHostMessage({data})', state)
        page.wait_for_function('()=>events.filter(e=>e.type==="qualityDemoApplied").length>=2')
    send()
    right.wait_for_function('()=>__testMap.getPixelRatio()===1.25 && __testMap.loaded()')
    assert left.evaluate('__testMap.getPixelRatio()') == 1
    assert right.evaluate("__testMap.getLayoutProperty('pois','icon-size')") == pytest.approx(base_icon * 1.5)
    right.wait_for_function('()=>__testMap.getStyle().sources.roads.tolerance===.375 && __testMap.loaded()')
    assert left.evaluate('__testMap.getStyle().sources.roads.tolerance') == 1
    right.wait_for_function('()=>__testMap.getStyle().sources.buildings.tolerance===.125 && __testMap.loaded()')
    assert left.evaluate('__testMap.getStyle().sources.buildings.tolerance') == .375
    state['quality'] = STANDARD; send()
    right.wait_for_function('()=>__testMap.getPixelRatio()===1 && __testMap.getStyle().sources.roads.tolerance===1 && __testMap.getStyle().sources.buildings.tolerance===.375 && __testMap.loaded()')
    before = left.evaluate('__testMap.getCenter().toArray()')
    canvas = left.locator('canvas').bounding_box()
    page.mouse.move(canvas['x']+canvas['width']/2, canvas['y']+canvas['height']/2)
    page.mouse.down(); page.mouse.move(canvas['x']+canvas['width']/2+75, canvas['y']+canvas['height']/2+30, steps=8); page.mouse.up()
    page.wait_for_timeout(500)
    center = left.evaluate('__testMap.getCenter().toArray()')
    assert center != before
    right.wait_for_function('(center)=>__testMap.getCenter().toArray().every((value,i)=>Math.abs(value-center[i])<1e-9)', arg=center)
    assert not page.evaluate('events.filter(e=>e.type==="qualityDemoError")')
    assert requests and all(url.startswith(navigation_server.start().rsplit('/', 1)[0]+'/') for url in requests)
    if output := os.environ.get('CSL_PREVIEW_BROWSER_OUTPUT'):
        from pathlib import Path
        Path(output).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(output)/'quandu-live-demo.png'))
