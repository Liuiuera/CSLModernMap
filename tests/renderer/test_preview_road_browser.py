"""Opt-in tests against the built Viewer and actual Chromium WebGL rasterizer.

Run after `npm --prefix viewer run build` with CSL_PREVIEW_BROWSER_TEST=1.
These tests do not emulate MapLibre using Pillow; Pillow only reads screenshots.
"""
from __future__ import annotations

import copy
import json
import os
from browser_support import COLORS, make_bundle, browser_page, load, screenshot, expect_color
from pathlib import Path

import pytest

from csmodernmap.preview_scene import (
    PreviewSceneServer,
)
from csmodernmap.style import Style
from road_fixtures import scene_from_lines, joined_surfaces

pytestmark = [pytest.mark.browser, pytest.mark.skipif(os.environ.get('CSL_PREVIEW_BROWSER_TEST') != '1',
                                reason='opt-in Chromium WebGL integration test')]


def test_standard_to_accurate_update_keeps_crossing_layers(tmp_path, browser_page):
    scene = scene_from_lines([[(-80, 0, 0), (80, 0, 0)],
                              [(0, 10, -80), (0, 10, 80)]], width=12)
    server = PreviewSceneServer((Path(__file__).resolve().parents[2] / "dev/CSLModernMapRenderer")/'viewer/dist')
    try:
        for revision, accurate in enumerate((False, True, False), 1):
            server.replace_bundle(make_bundle(tmp_path/f'mode-{revision}', scene,
                revision, accurate=accurate))
            load(browser_page, server, revision)
            image = screenshot(browser_page, f'intersections-{revision}')
            expect_color(image, 0, 0, '#368cd1')
            assert browser_page.evaluate('(r) => window.events.some(e => e.type === "sceneLoaded" && e.revision === r && e.performance.completion === "idle")', revision)
    finally:
        server.close()


def test_native_mixed_junction_core_color_and_source_visibility(tmp_path, browser_page):
    from test_native_junction_paints import junction_document
    from csmodernmap.road_geometry import build_road_scene
    scene = build_road_scene(junction_document())
    bundle = make_bundle(tmp_path/'junction', scene, colorize=False)
    for key, color in [('main:ground','#368cd1'), ('branch:ground','#d84a4a')]:
        bundle.manifest['theme']['road']['levels'][key]['fill'] = color
    (bundle.root/'manifest.json').write_text(json.dumps(bundle.manifest))
    server = PreviewSceneServer((Path(__file__).resolve().parents[2] / "dev/CSLModernMapRenderer")/'viewer/dist')
    server.replace_bundle(bundle)
    try:
        load(browser_page, server, 1)
        image = screenshot(browser_page, 'native-junction-after')
        expect_color(image, 0, 10, '#368cd1')
        expect_color(image, 0, 60, '#d84a4a')
        theme = copy.deepcopy(bundle.manifest['theme'])
        theme['road']['levels']['branch:ground']['enabled'] = False
        browser_page.evaluate('(theme) => window.cslPreview.dispatch({type:"applyTheme",theme})', theme)
        browser_page.wait_for_timeout(350)
        hidden = screenshot(browser_page, 'native-junction-branch-hidden')
        assert hidden.getpixel((round(320-25*2.92),320)) == hidden.getpixel((40,40))
    finally:
        server.close()


def test_four_layers_theme_visibility_and_incremental_rebuild(tmp_path, browser_page):
    scene = scene_from_lines([
        [(-80, 0, 0), (80, 0, 0)], [(0, 10, -80), (0, 10, 80)],
        [(-80, 20, -80), (80, 20, 80)], [(-80, 30, 80), (80, 30, -80)],
    ], width=12)
    bundle = make_bundle(tmp_path/'four', scene)
    server = PreviewSceneServer((Path(__file__).resolve().parents[2] / "dev/CSLModernMapRenderer")/'viewer/dist')
    server.replace_bundle(bundle)
    page = browser_page
    try:
        load(page, server, 1)
        expect_color(screenshot(page, 'four-ranks'), 0, 0, COLORS[3])
        theme = copy.deepcopy(bundle.manifest['theme'])
        theme['road']['levels']['test:4']['enabled'] = False
        page.evaluate('(theme) => window.cslPreview.dispatch({type:"applyTheme",theme})', theme)
        page.wait_for_timeout(350)
        expect_color(screenshot(page, 'hidden-upper'), 0, 0, COLORS[2])
        theme['road']['levels']['test:3']['fill'] = '#edb52f'
        theme['road']['widthScale'] = .3
        page.evaluate('(theme) => window.cslPreview.dispatch({type:"applyTheme",theme})', theme)
        page.wait_for_timeout(350)
        expect_color(screenshot(page, 'live-theme'), 0, 0, '#edb52f')
        second = make_bundle(tmp_path/'one', scene_from_lines([
            [(-80, 0, 0), (80, 0, 0)]]), revision=2)
        server.replace_bundle(second)
        load(page, server, 2)
        expect_color(screenshot(page, 'incremental-one-rank'), 0, 0, COLORS[0])
    finally:
        server.close()


@pytest.mark.parametrize('surfaces', [True, False])
def test_cycle_actual_pixels_and_upper_boundary(tmp_path, browser_page, surfaces):
    scene = scene_from_lines([
        [(-80, -30, 0), (80, 90, 0)], [(-40, 10, -80), (-40, 10, 80)],
        [(-100, 20, 60), (100, 20, -60)],
    ], width=6, surfaces=surfaces)
    bundle = make_bundle(tmp_path/'cycle', scene)
    server = PreviewSceneServer((Path(__file__).resolve().parents[2] / "dev/CSLModernMapRenderer")/'viewer/dist')
    server.replace_bundle(bundle)
    try:
        load(browser_page, server, 1)
        image = screenshot(browser_page, 'spatial-cycle' if surfaces else 'fallback-cycle')
        expect_color(image, -40, 0, COLORS[1])
        expect_color(image, -40, 24, COLORS[2])
        expect_color(image, 0, 0, COLORS[0])
    finally:
        server.close()


def test_hairpin_actual_crossings(tmp_path, browser_page):
    scene = scene_from_lines([
        [(-80, 0, -20), (80, 0, -20), (80, 30, 20), (-80, 30, 20)],
        [(80, 10, -40), (-80, 10, 40)],
    ], width=4)
    bundle = make_bundle(tmp_path/'hairpin', scene)
    server = PreviewSceneServer((Path(__file__).resolve().parents[2] / "dev/CSLModernMapRenderer")/'viewer/dist')
    server.replace_bundle(bundle)
    try:
        load(browser_page, server, 1)
        image = screenshot(browser_page, 'hairpin')
        expect_color(image, 40, -20, COLORS[1])
        expect_color(image, -40, 20, COLORS[0])
    finally:
        server.close()


def test_upper_casing_survives_lower_fill(tmp_path, browser_page):
    scene = scene_from_lines([
        [(-80, 12, 0), (80, 12, 0)], [(0, 0, -80), (0, 0, 80)],
    ], width=12)
    bundle = make_bundle(tmp_path/'border', scene)
    # Expose a 2 px edge outside the polygon so the overlap probe samples a
    # fully covered pixel, rather than the antialiased fringe of a 1 px edge.
    bundle.manifest['theme']['road']['levels']['test:1']['borderFixedPx'] = 4
    (bundle.root/'manifest.json').write_text(json.dumps(bundle.manifest))
    server = PreviewSceneServer((Path(__file__).resolve().parents[2] / "dev/CSLModernMapRenderer")/'viewer/dist')
    server.replace_bundle(bundle)
    try:
        load(browser_page, server, 1)
        image = screenshot(browser_page, 'upper-border')
        expect_color(image, 0, 0, COLORS[0])
        expect_color(image, 0, 6.5, '#111111')
    finally:
        server.close()


def test_runway_boundary_stays_visible_when_zoomed_without_repeating_road_width(
        tmp_path, browser_page):
    scene = scene_from_lines([[(-300, 0, 0), (300, 0, 0)]], width=75)
    server = PreviewSceneServer((Path(__file__).resolve().parents[2] / "dev/CSLModernMapRenderer")/'viewer/dist')
    page = browser_page

    def boundary_pixels(image):
        # The runway runs vertically through the image centre. Count only its
        # dark side edges, away from controls and the runway's end caps.
        return [x for x in range(64, 576)
                if max(abs(c-17) for c in image.getpixel((x, 320))) <= 8]

    counts = []
    try:
        for revision, half in enumerate((200, 100, 50), 1):
            bundle = make_bundle(tmp_path/f'runway-{half}', scene,
                                 revision=revision, region=[-half, -half, half, half])
            theme = bundle.manifest['theme']
            theme['road']['levels']['test:1'].update(borderFixedPx=1.2, borderScale=.4)
            (bundle.root/'manifest.json').write_text(json.dumps(bundle.manifest))
            server.replace_bundle(bundle)
            load(page, server, revision)
            page.evaluate('() => window.cslPreview.dispatch({type:"fitBounds"})')
            page.wait_for_timeout(350)
            pixels = boundary_pixels(screenshot(page, f'runway-border-{half}'))
            # Both edges survive, and their total thickness stays small even
            # for a 75 m runway. Including the fill width fails this bound.
            assert any(x < 320 for x in pixels) and any(x > 320 for x in pixels)
            assert 2 <= len(pixels) <= 20
            counts.append(len(pixels))

            widened = copy.deepcopy(theme)
            widened['road']['widthScale'] = .6
            page.evaluate('(theme) => window.cslPreview.dispatch({type:"applyTheme",theme})', widened)
            page.wait_for_timeout(350)
            assert boundary_pixels(screenshot(page, f'runway-live-width-{half}')) == pixels

        assert counts[-1] > counts[0], counts
        thicker = copy.deepcopy(widened)
        thicker['road']['levels']['test:1']['borderScale'] = .8
        page.evaluate('(theme) => window.cslPreview.dispatch({type:"applyTheme",theme})', thicker)
        page.wait_for_timeout(350)
        assert len(boundary_pixels(screenshot(page, 'runway-live-border'))) > counts[-1]
    finally:
        server.close()


def test_wide_landing_has_no_internal_casing_across_ranks(tmp_path, browser_page):
    from csmodernmap.road_geometry import CrossingEvent
    scene = joined_surfaces()
    # An ordering constraint elsewhere can place adjoining surfaces in different
    # passes. The upper pass must not redraw their common cap across the fill.
    scene.height_reliable = True
    scene.crossings = [CrossingEvent(1, 2, 40, 0, -10, 0, 40, 40, 20)]
    bundle = make_bundle(tmp_path/'wide-landing', scene)
    server = PreviewSceneServer((Path(__file__).resolve().parents[2] / "dev/CSLModernMapRenderer")/'viewer/dist')
    server.replace_bundle(bundle)
    try:
        load(browser_page, server, 1)
        image = screenshot(browser_page, 'wide-landing')
        assert bundle.manifest['roadOrder']['ranks'] == 2
        expect_color(image, .5, 6, COLORS[1])
        expect_color(image, .5, -6, COLORS[1])
        expect_color(image, 40, 12.5, '#111111')
    finally:
        server.close()


def test_exported_html_uses_the_same_ranked_vector_pipeline(tmp_path, browser_page):
    from csmodernmap.web_export_v2 import payload_from_preview_bundle, render_export_html
    scene = scene_from_lines([
        [(-80, 0, 0), (80, 0, 0)], [(0, 12, -80), (0, 12, 80)]], width=12)
    bundle = make_bundle(tmp_path/'offline', scene)
    try:
        payload = payload_from_preview_bundle(bundle)
        target = tmp_path/'preview.html'
        target.write_text(render_export_html(payload,
            (Path(__file__).resolve().parents[2] / "dev/CSLModernMapRenderer")/'viewer/dist'), encoding='utf-8')
        browser_page.goto(target.as_uri())
        browser_page.wait_for_function("() => window.events.some(e => e.type === 'sceneLoaded')")
        browser_page.wait_for_timeout(800)
        assert not browser_page.evaluate("window.events.filter(e => e.type === 'renderError')")
        expect_color(screenshot(browser_page, 'offline-export'), 0, 0, COLORS[1])
    finally:
        bundle.close()


@pytest.mark.skipif(not os.environ.get('CSL_PREVIEW_REAL_ARCHIVE'), reason='optional real city archive')
def test_real_city_with_png_reference(tmp_path, browser_page):
    from collections import Counter
    from math import floor
    from csmodernmap.cmm.loader import load_document
    from csmodernmap.cmm.session import DocumentSession
    from csmodernmap.layers.road_layer import RoadLayer
    from csmodernmap.renderer import Renderer
    document = load_document(os.environ['CSL_PREVIEW_REAL_ARCHIVE'], validate=False)
    session = DocumentSession(document)
    scene = session.road_scene()
    cells = Counter((floor(e.x/400), floor(e.z/400)) for e in scene.crossings)
    (gx, gz), _ = cells.most_common(1)[0]
    cx, cz = (gx+.5)*400, (gz+.5)*400
    region = [cx-200, cz-200, cx+200, cz+200]
    bundle = make_bundle(tmp_path/'real', scene, colorize=False, region=region)
    server = PreviewSceneServer((Path(__file__).resolve().parents[2] / "dev/CSLModernMapRenderer")/'viewer/dist')
    server.replace_bundle(bundle)
    try:
        load(browser_page, server, 1)
        screenshot(browser_page, 'real-city-detail')
        assert bundle.manifest['roadOrder']['constraints'] > 0
        if directory := os.environ.get('CSL_PREVIEW_BROWSER_OUTPUT'):
            renderer = Renderer(Style({}), width=640, height=640)
            renderer.add_layer(RoadLayer())
            half = 200*640/584  # include the Viewer's 28px camera padding
            png = renderer.render(document, region=(cx-half, cz-half, cx+half, cz+half),
                                  final_size=640, session=session)
            png.save(Path(directory)/'real-city-png-reference.png')
            (Path(directory)/'real-city.json').write_text(json.dumps({
                'region': region, **bundle.manifest['roadOrder']}, indent=2))
        for preset in ('district', 'region', 'detail'):
            browser_page.evaluate('(preset) => window.cslPreview.dispatch({type:"setZoomPreset",preset})', preset)
            browser_page.wait_for_timeout(600)
            screenshot(browser_page, f'real-city-{preset}')
        assert not browser_page.evaluate("window.events.filter(e => e.type === 'renderError')")
    finally:
        server.close()
