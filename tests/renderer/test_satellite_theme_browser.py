"""Satellite theme tokens and opt-in Chromium road/halo rendering checks."""
import copy
import json
import os
from pathlib import Path

import pytest
from PIL import Image, ImageChops

from csmodernmap.preview_scene import PreviewSceneServer, preview_theme, road_theme, world_to_geo
from test_preview_3d_browser import expose_map
from test_preview_road_browser import browser_page, expect_color, load, make_bundle, screenshot
from test_preview_road_order import scene_from_lines
from test_navigation_ui_browser import (
    navigation_page, navigation_bundle, navigation_server, open_navigation, make_route,
)

browser_test = pytest.mark.skipif(os.environ.get('CSL_PREVIEW_BROWSER_TEST') != '1',
                                reason='opt-in Chromium WebGL integration test')
VIEWER = Path(__file__).resolve().parents[2] / 'dev/CSLModernMapRenderer/viewer/dist'


def save_manifest(bundle):
    (bundle.root / 'manifest.json').write_text(json.dumps(bundle.manifest), encoding='utf-8')


def dispatch(page, **command):
    page.evaluate('command => window.cslPreview.dispatch(command)', command)
    page.wait_for_timeout(350)


def assert_road_opacity(page, opacity):
    paints = page.evaluate("""() => __testMap.getStyle().layers
        .filter(layer => layer.source === 'roads')
        .map(layer => layer.paint[`${layer.type}-opacity`])""")
    assert paints
    assert paints == pytest.approx([opacity] * len(paints))
    assert not page.evaluate("window.events.filter(e => e.type === 'renderError')")


def test_satellite_theme_tokens_defaults_scaling_and_alpha():
    assert road_theme({})['opacity'] == 1
    for value, expected in [(-.5, 0), (0, 0), (.35, .35), (1, 1), (2, 1)]:
        assert road_theme({'roads': {'opacity': value}})['opacity'] == expected
    labels = {kind: {'color': '#12345680', 'stroke': '#ABCDEF40',
                     'stroke_width': {'fixed': 1, 'scaled': 4},
                     'stroke_blur': {'fixed': 2, 'scaled': 8}}
              for kind in ('road', 'district')}
    theme = preview_theme({'roads': {'opacity': .35}, 'labels': labels})
    assert theme['road']['opacity'] == .35
    for kind in labels:
        assert theme['label']['colors'][kind] == '#12345680'
        assert theme['label']['strokes'][kind] == {
            'color': '#ABCDEF40', 'width': 2, 'blur': 4}
        assert theme['label']['metrics']['strokeBlurs'][kind] == {'fixed': 2, 'scaled': 8}
    defaults = preview_theme({'labels': {'road': {'stroke': '#FFFFFF', 'stroke_width': 2}}})
    assert defaults['label']['strokes']['road']['blur'] == 0
    assert defaults['label']['metrics']['strokeBlurs']['road'] == 0
    assert preview_theme({'poi_icons': {'name_color': '#F7F4EA'}})['label']['colors']['poi'] == '#F7F4EA'


@browser_test
@pytest.mark.parametrize('surfaces', [True, False])
def test_road_opacity_add_theme_context_and_rebuild(tmp_path, browser_page, surfaces):
    page = browser_page
    expose_map(page)
    scene = scene_from_lines([[(-80, 0, 0), (80, 0, 0)]], width=12, surfaces=surfaces)
    bundle = make_bundle(tmp_path / 'initial', scene)
    theme = bundle.manifest['theme']
    theme['road'].update(opacity=.5, background='#FFFFFF')
    theme['road']['levels']['test:1'].update(fill='#00000080', border='#00000000')
    save_manifest(bundle)
    server = PreviewSceneServer(VIEWER)
    server.replace_bundle(bundle)
    try:
        load(page, server, 1)
        assert_road_opacity(page, .5)
        # Color alpha and global opacity multiply for a single road fill.
        expect_color(screenshot(page, f'road-alpha-{surfaces}'), 40, 0, '#BFBFBF')
        theme['road']['opacity'] = 0
        dispatch(page, type='applyTheme', theme=theme)
        assert_road_opacity(page, 0)
        expect_color(screenshot(page, f'road-transparent-{surfaces}'), 40, 0, '#FFFFFF')
        dispatch(page, type='setContextMode', mode='transit', options={'transit_mask': .6})
        assert_road_opacity(page, 0)
        theme['road']['opacity'] = .25
        dispatch(page, type='applyRoadTheme', theme=theme['road'])
        assert_road_opacity(page, .1)
        dispatch(page, type='setContextMode', mode='transit', options={'base_road': False})
        assert_road_opacity(page, 0)
        dispatch(page, type='setContextMode', mode='map')
        assert_road_opacity(page, .25)
        dispatch(page, type='setContextMode', mode='transit', options={'transit_mask': .6})
        rebuilt = make_bundle(tmp_path / 'rebuilt', scene_from_lines([
            [(-80, 0, 0), (80, 0, 0)], [(0, 10, -80), (0, 10, 80)]], surfaces=surfaces), revision=2)
        rebuilt.manifest['theme'] = copy.deepcopy(theme)
        save_manifest(rebuilt)
        server.replace_bundle(rebuilt)
        load(page, server, 2)
        assert_road_opacity(page, .1)
        # An older road-theme payload without opacity restores the opaque default.
        del theme['road']['opacity']
        dispatch(page, type='applyTheme', theme=theme)
        assert_road_opacity(page, .4)
        dispatch(page, type='setContextMode', mode='map')
        assert_road_opacity(page, 1)
    finally:
        server.close()


@browser_test
@pytest.mark.parametrize('surfaces', [True, False])
def test_satellite_backed_crossing_blends_per_layer(tmp_path, browser_page, surfaces):
    """5.x opacity blends each road pass: two .30 fills cover .51 at crossings.

    This intentionally records the visible difference from fading one fully
    composited road image, which would retain .30 coverage at the crossing.
    """
    page = browser_page
    expose_map(page)
    bundle = make_bundle(tmp_path / 'crossing', scene_from_lines([
        [(-80, 0, 0), (80, 0, 0)], [(0, 10, -80), (0, 10, 80)]],
        width=12, surfaces=surfaces))
    Image.new('RGB', (32, 32), '#204060').save(bundle.root / 'satellite.png')
    bundle.manifest.update(satelliteAvailable=True, satelliteSize=[32, 32],
        satelliteCorners=[world_to_geo(100, 100), world_to_geo(100, -100),
                          world_to_geo(-100, -100), world_to_geo(-100, 100)],
        renderStack=['satellite', 'road'])
    bundle.manifest['sources']['satellite'] = 'satellite.png'
    bundle.manifest['sourceRevisions']['satellite'] = 1
    bundle.manifest['layers']['satellite'] = True
    theme = bundle.manifest['theme']
    theme['road']['opacity'] = .30
    for paint in theme['road']['levels'].values():
        paint.update(fill='#FFFFFF', border='#00000000')
    save_manifest(bundle)
    server = PreviewSceneServer(VIEWER)
    server.replace_bundle(bundle)
    try:
        load(page, server, 1)
        assert_road_opacity(page, .30)
        image = screenshot(page, f'satellite-crossing-opacity-030-{surfaces}')
        expect_color(image, 60, 60, '#204060')
        expect_color(image, 40, 0, '#637990')
        expect_color(image, 0, 0, '#92A1B1')
        dispatch(page, type='setLayerVisibility', layer='road', visible=False)
        expect_color(screenshot(page, f'satellite-crossing-hidden-{surfaces}'), 0, 0, '#204060')
        dispatch(page, type='setLayerVisibility', layer='road', visible=True)
        assert_road_opacity(page, .30)
        expect_color(screenshot(page, f'satellite-crossing-restored-{surfaces}'), 0, 0, '#92A1B1')
    finally:
        server.close()


@browser_test
def test_native_and_dom_halo_blur_updates(tmp_path, browser_page):
    page = browser_page
    expose_map(page)
    bundle = make_bundle(tmp_path / 'labels', scene_from_lines([[(-80, 0, 0), (80, 0, 0)]]))
    bundle.manifest['layers'].update(road=False, label=True, poi=True)
    bundle.manifest['renderStack'].extend(['poi', 'label'])
    theme = bundle.manifest['theme']
    theme['road']['background'] = '#FFFFFF'
    theme['label'] = preview_theme({'labels': {'district': {
        'size': 32, 'color': '#20202080', 'stroke': '#FF000080',
        'stroke_width': {'fixed': 1, 'scaled': .1}, 'stroke_blur': 0}}})['label']
    theme['label']['metrics']['sizes']['poi'] = 32
    for field in ('sizes', 'strokeWidths', 'strokeBlurs'):
        theme['label']['metrics'][field]['road'] = copy.deepcopy(theme['label']['metrics'][field]['district'])
    for field in ('colors', 'strokes', 'halos'):
        theme['label'][field]['road'] = copy.deepcopy(theme['label'][field]['district'])
    theme['label']['strokes']['poi'] = {'color': '#FF000080', 'width': 3, 'blur': 0}
    theme['label']['colors']['poi'] = '#20202080'
    theme['poiVisibility'] = {'icons': {'facility': True}, 'names': {'facility': True}}
    collections = {
        'labels': [{'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': world_to_geo(-30, 0)},
                    'properties': {'featureId': 'district:halo', 'text': 'Native halo', 'labelScope': 'district'}},
                   {'type': 'Feature', 'geometry': {'type': 'LineString', 'coordinates': [
                       world_to_geo(0, -80), world_to_geo(0, 80)]}, 'properties': {
                           'featureId': 'road:halo', 'text': 'Road halo', 'labelScope': 'road',
                           'roadLevel': 'main', 'nameSource': 'custom'}}],
        'pois': [{'type': 'Feature', 'id': 1, 'geometry': {'type': 'Point', 'coordinates': world_to_geo(35, 0)},
                  'properties': {'featureId': 'poi:halo', 'name': 'DOM halo', 'owner': 'building',
                                 'visibilityGroup': 'facility', 'visibilityGroups': ['facility'],
                                 'poiGroup': 'facility', 'iconStatus': 'empty', 'minZoom': 0,
                                 'color': '#00FF00',
                                 'visibleZooms': list(range(25)), 'drawName': True}}],
    }
    for name, features in collections.items():
        (bundle.root / f'{name}.geojson').write_text(json.dumps({'type': 'FeatureCollection', 'features': features}))
        bundle.manifest['sources'][name] = f'{name}.geojson'
        bundle.manifest['sourceRevisions'][name] = 1
    save_manifest(bundle)
    server = PreviewSceneServer(VIEWER)
    server.replace_bundle(bundle)
    try:
        load(page, server, 1)
        label = page.locator('.map-label').filter(has_text='DOM halo')
        label.wait_for()
        assert label.evaluate("e => getComputedStyle(e).color") == 'rgba(32, 32, 32, 0.5)'
        page.wait_for_function("__testMap.queryRenderedFeatures({layers:['district-labels']}).length > 0")
        sharp = screenshot(page, 'satellite-halo-sharp')
        theme['label']['metrics']['strokeBlurs']['district'] = {'fixed': 1, 'scaled': .1}
        theme['label']['metrics']['strokeBlurs']['road'] = {'fixed': 1, 'scaled': .1}
        theme['label']['strokes']['poi']['blur'] = 3
        dispatch(page, type='applyTheme', theme=theme)
        assert page.evaluate("__testMap.getPaintProperty('district-labels','text-halo-blur')") == page.evaluate(
            "__testMap.getPaintProperty('district-labels','text-halo-width')")
        assert page.evaluate("__testMap.getPaintProperty('road-labels','text-halo-blur')") == page.evaluate(
            "__testMap.getPaintProperty('road-labels','text-halo-width')")
        assert page.evaluate("__testMap.getPaintProperty('district-labels','text-color')") == '#20202080'
        assert page.evaluate("__testMap.getPaintProperty('district-labels','text-halo-color')") == '#FF000080'
        assert label.evaluate("e => e.classList.contains('map-label-blurred-halo')")
        assert label.evaluate("e => getComputedStyle(e).webkitTextStrokeWidth") == '0px'
        assert label.evaluate("e => getComputedStyle(e,'::before').filter") == 'blur(3px)'
        assert label.evaluate("e => getComputedStyle(e,'::before').webkitTextStrokeColor") == 'rgba(255, 0, 0, 0.5)'
        blurred = screenshot(page, 'satellite-halo-blurred')
        # Both the native district label (lower half) and DOM label (upper half) change pixels.
        for box in [(80, 80, 560, 320), (80, 320, 560, 560)]:
            assert ImageChops.difference(sharp.crop(box), blurred.crop(box)).getbbox()
        theme['label']['metrics']['strokeBlurs']['district'] = 0
        theme['label']['metrics']['strokeBlurs']['road'] = 0
        theme['label']['strokes']['poi']['blur'] = 0
        dispatch(page, type='applyTheme', theme=theme)
        assert not label.evaluate("e => e.classList.contains('map-label-blurred-halo')")
        theme['label']['halos'].update(district=False, poi=False)
        theme['label']['strokes']['poi']['blur'] = 3
        dispatch(page, type='applyTheme', theme=theme)
        assert page.evaluate("__testMap.getPaintProperty('district-labels','text-halo-blur')") == 0
        assert label.evaluate("e => e.classList.contains('map-label-no-halo') && !e.classList.contains('map-label-blurred-halo')")
        assert not page.evaluate("window.events.filter(e => e.type === 'renderError')")
    finally:
        server.close()


@browser_test
def test_navigation_preserves_theme_road_opacity(navigation_page, navigation_bundle, navigation_server):
    page = navigation_page
    expose_map(page)
    theme = navigation_bundle.manifest['theme']
    theme['road']['opacity'] = .3
    save_manifest(navigation_bundle)
    open_navigation(page, navigation_server)
    assert_road_opacity(page, .3)
    make_route(page)
    assert_road_opacity(page, .3)
    theme['road']['opacity'] = .15
    dispatch(page, type='applyTheme', theme=theme)
    assert_road_opacity(page, .15)
    page.get_by_role('button', name='清空路线', exact=True).click()
    assert_road_opacity(page, .15)
