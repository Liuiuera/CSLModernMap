import json
import os
from pathlib import Path

import pytest

from csmodernmap.preview_scene import PreviewSceneServer, _traffic_features
from csmodernmap.preview_scene import preview_theme
from csmodernmap._paths import resource_dir
from csmodernmap.style import load_style
from csmodernmap.layers.traffic_layer import congestion_color
from csmodernmap.web_export_v2 import payload_from_preview_bundle, render_export_html
from browser_support import browser_page, make_bundle, load, screenshot
from road_fixtures import scene_from_lines
from test_traffic_direction import segment, lane, DIRECTION_BACKWARD
from test_preview_3d_browser import expose_map, instrument


pytestmark = [pytest.mark.browser, pytest.mark.skipif(os.environ.get("CSL_PREVIEW_BROWSER_TEST") != "1",
                                reason="opt-in Chromium test")]
VIEWER = Path(__file__).resolve().parents[2] / "dev/CSLModernMapRenderer/viewer/dist"


def traffic_bundle(root, revision=1):
    scene = scene_from_lines([[(-80, 0, 0), (80, 0, 0)]], width=14)
    bundle = make_bundle(root, scene, revision, colorize=False)
    seg = segment([lane(3.5, 1), lane(-3.5, 0, DIRECTION_BACKWARD)], first=-80, last=80)
    (root / "traffic.geojson").write_text(json.dumps({"type": "FeatureCollection", "features":
        _traffic_features(seg, scene.roads[0])}))
    bundle.manifest["sources"]["traffic"] = f"traffic.geojson?revision={revision}"
    bundle.manifest["sourceRevisions"]["traffic"] = revision
    bundle.manifest["layers"]["traffic"] = False
    (root / "manifest.json").write_text(json.dumps(bundle.manifest))
    return bundle


def toggle(page, visible, layer="traffic"):
    page.evaluate("c => window.cslPreview.dispatch(c)", {
        "type": "setLayerVisibility", "layer": layer, "visible": visible})
    page.wait_for_timeout(250)
    assert not page.evaluate("window.events.filter(e => e.type === 'renderError')")


def wait_for_paint(page):
    page.evaluate("() => new Promise(done => {__testMap.once('idle', () => done()); __testMap.triggerRepaint()})")


@pytest.mark.parametrize("congestion", [0, .15, .3, .45, .6, .9, 1])
def test_theme_traffic_colors_update_live_and_match_png(tmp_path, browser_page, congestion):
    page = browser_page
    expose_map(page)
    bundle = traffic_bundle(tmp_path / "scene")
    seg = segment([lane(0, congestion)], first=-80, last=80)
    road = scene_from_lines([[(-80, 0, 0), (80, 0, 0)]], width=14).roads[0]
    (bundle.root / "traffic.geojson").write_text(json.dumps({
        "type": "FeatureCollection", "features": _traffic_features(seg, road)}))
    server = PreviewSceneServer(VIEWER)
    try:
        server.replace_bundle(bundle)
        load(page, server, 1)
        road_before = screenshot(page, "traffic-theme-road-before")
        toggle(page, True)
        theme = bundle.manifest["theme"]
        for palette in (
            {"free": "#2060A0", "slow": "#80A020", "busy": "#C03070"},
            {"free": "#A03080", "slow": "#20A060", "busy": "#3050C0"},
        ):
            style = load_style(str(resource_dir("styles") / "modern.yaml"))
            style.data["traffic"] = palette
            theme["traffic"] = preview_theme(style)["traffic"]
            page.evaluate("theme => window.cslPreview.dispatch({type:'applyTheme',theme})", theme)
            wait_for_paint(page)
            image = screenshot(page, f"traffic-theme-{congestion}-{palette['free'][1:]}")
            expected = tuple(bytes.fromhex(congestion_color(congestion, theme["traffic"]["ramp"])[1:]))
            assert max(abs(a-b) for a, b in zip(image.getpixel((320, 320)), expected)) <= 2
            assert not page.evaluate("window.events.filter(e => e.type === 'renderError')")
        toggle(page, False)
        assert screenshot(page, "traffic-theme-road-after").tobytes() == road_before.tobytes()

        bundle.manifest["layers"]["traffic"] = True
        target = tmp_path / "traffic-theme.html"
        target.write_text(instrument(render_export_html(payload_from_preview_bundle(bundle), VIEWER, "zh-CN")), encoding="utf-8")
        page.goto(target.as_uri())
        page.wait_for_function("()=>window.__testMap?.getLayer('traffic-forward-fill') && __testMap.loaded()")
        wait_for_paint(page)
        exported = screenshot(page, f"traffic-theme-export-{congestion}")
        assert max(abs(a-b) for a, b in zip(exported.getpixel((320, 320)), expected)) <= 2
    finally:
        server.close()


def test_hidden_unloaded_reopen_and_revision_update(tmp_path, browser_page):
    page = browser_page
    expose_map(page)
    requests = []
    page.on("request", lambda r: requests.append(r.url) if "traffic.geojson" in r.url else None)
    server = PreviewSceneServer(VIEWER)
    try:
        first = traffic_bundle(tmp_path / "first")
        server.replace_bundle(first)
        load(page, server, 1)
        assert requests == []
        hidden = screenshot(page, "traffic-hidden")
        for i in range(2):
            toggle(page, True)
            wait_for_paint(page)
            assert len(requests) == i+1
            shown = screenshot(page, "traffic-shown")
            # Positive world z lies left of the northbound centreline.
            assert any(shown.getpixel((x, 320))[0] > shown.getpixel((x, 320))[1]*2
                       for x in range(305, 320))
            free = tuple(bytes.fromhex(congestion_color(0, first.manifest["theme"]["traffic"]["ramp"])[1:]))
            assert free in [shown.getpixel((x, 320)) for x in range(320, 340)]
            toggle(page, False)
            assert screenshot(page, "traffic-closed").getpixel((312, 320)) == hidden.getpixel((312, 320))
        second = traffic_bundle(tmp_path / "second", 2)
        server.replace_bundle(second)
        load(page, server, 2)
        assert len(requests) == 2
        toggle(page, True)
        assert "revision=2" in requests[-1]
        toggle(page, False, "road")
        screenshot(page, "traffic-standalone")
        third = traffic_bundle(tmp_path / "third", 3)
        third.manifest["sceneId"] = "other-city"
        (third.root / "manifest.json").write_text(json.dumps(third.manifest))
        server.replace_bundle(third)
        load(page, server, 3)
        assert len(requests) == 3
    finally:
        server.close()


@pytest.mark.parametrize("action", ["close", "switch"])
def test_cancelled_fetch_does_not_restore_traffic(tmp_path, browser_page, action):
    page = browser_page
    server = PreviewSceneServer(VIEWER)
    server.replace_bundle(traffic_bundle(tmp_path / "scene"))
    try:
        load(page, server, 1)
        pending = []
        page.route("**/traffic.geojson*", lambda route: pending.append(route))
        page.evaluate("() => {void window.cslPreview.dispatch({type:'setLayerVisibility',layer:'traffic',visible:true})}")
        page.wait_for_timeout(100)
        assert len(pending) == 1
        if action == "close":
            toggle(page, False)
        else:
            replacement = traffic_bundle(tmp_path / "replacement", 2)
            replacement.manifest["sceneId"] = "replacement"
            (replacement.root / "manifest.json").write_text(json.dumps(replacement.manifest))
            server.replace_bundle(replacement)
            load(page, server, 2)
        pending[0].fulfill(json={"type": "FeatureCollection", "features": []})
        page.unroute("**/traffic.geojson*")
        toggle(page, True)
        screenshot(page, "traffic-reopened-after-abort")
    finally:
        server.close()


def test_visible_traffic_reuses_revision_and_reloads_changed_data(tmp_path, browser_page):
    page = browser_page
    requests = []
    page.on("request", lambda r: requests.append(r.url) if "traffic.geojson" in r.url else None)
    server = PreviewSceneServer(VIEWER)
    try:
        for revision in (1, 2, 3):
            bundle = traffic_bundle(tmp_path / str(revision), revision)
            bundle.manifest["layers"]["traffic"] = True
            bundle.manifest["sourceRevisions"]["traffic"] = 1 if revision < 3 else 3
            (bundle.root / "manifest.json").write_text(json.dumps(bundle.manifest))
            server.replace_bundle(bundle)
            load(page, server, revision)
            assert len(requests) == (1 if revision < 3 else 2)
        assert "revision=3" in requests[-1]
    finally:
        server.close()


@pytest.mark.parametrize('visible', [False, True])
def test_offline_html_defers_parsing_and_keeps_toggle(tmp_path, browser_page, visible):
    bundle = traffic_bundle(tmp_path / "scene")
    bundle.manifest['layers']['traffic'] = visible
    payload = payload_from_preview_bundle(bundle, navigation_enabled=False)
    target = tmp_path / "traffic.html"
    target.write_text(instrument(render_export_html(payload, VIEWER)), encoding="utf-8")
    page = browser_page
    page.goto(target.as_uri())
    panel_button = page.locator('button[aria-controls="preview-layer-panel"]')
    panel_button.click()
    checkbox = page.get_by_role('switch', name='路况', exact=True)
    checkbox.wait_for(state='visible')
    assert checkbox.is_checked() is visible
    assert page.locator('input[data-layer="traffic"]').count() == 1
    assert page.evaluate("typeof window.__CSL_SCENE__.embedded.sources['traffic.geojson']") == "string"
    checkbox.check()
    page.wait_for_timeout(250)
    checkbox.uncheck()
    panel_button.click()
    wait_for_paint(page)
    hidden = screenshot(page, 'traffic-offline-off')
    assert page.evaluate('window.__CSL_SCENE__.manifest.layers.traffic') is False
    panel_button.click()
    checkbox.check()
    panel_button.click()
    wait_for_paint(page)
    shown = screenshot(page, 'traffic-offline-reopened')
    assert any(shown.getpixel((x, 320))[0] > shown.getpixel((x, 320))[1] * 2
               and shown.getpixel((x, 320)) != hidden.getpixel((x, 320))
               for x in range(295, 320))
    assert page.evaluate('window.__CSL_SCENE__.manifest.layers.traffic') is True
    assert not page.evaluate("window.events.filter(e => e.type === 'renderError')")


def test_offline_html_without_traffic_has_no_traffic_switch(tmp_path, browser_page):
    scene = scene_from_lines([[(-80, 0, 0), (80, 0, 0)]], width=14)
    bundle = make_bundle(tmp_path / 'roads-only', scene, colorize=False)
    payload = payload_from_preview_bundle(bundle, navigation_enabled=False)
    target = tmp_path / 'roads-only.html'
    target.write_text(render_export_html(payload, VIEWER), encoding='utf-8')
    browser_page.goto(target.as_uri())
    browser_page.locator('button[aria-controls="preview-layer-panel"]').click()
    assert browser_page.get_by_role('switch', name='路况', exact=True).count() == 0


def test_traffic_has_no_signal_points_and_stays_an_overlay(tmp_path, browser_page):
    expose_map(browser_page)
    scene = scene_from_lines([[(-80, 0, 0), (80, 0, 0)],
                              [(0, 10, -80), (0, 10, 80)]], width=14)
    bundle = make_bundle(tmp_path / "crossing", scene, colorize=False)
    seg = segment([lane(3.5, 1), lane(-3.5, 0, DIRECTION_BACKWARD)], first=-80, last=80)
    features = _traffic_features(seg, scene.roads[0])
    features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]},
                     "properties": {"geometryRole": "traffic-light", "active": True}})
    (bundle.root / "traffic.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    bundle.manifest["sources"]["traffic"] = "traffic.geojson"
    bundle.manifest["sourceRevisions"]["traffic"] = 1
    bundle.manifest["layers"]["traffic"] = False
    (bundle.root / "manifest.json").write_text(json.dumps(bundle.manifest))
    server = PreviewSceneServer(VIEWER)
    server.replace_bundle(bundle)
    try:
        load(browser_page, server, 1)
        baseline = screenshot(browser_page, "traffic-crossing-off")
        toggle(browser_page, True)
        wait_for_paint(browser_page)
        shown = screenshot(browser_page, "traffic-crossing-on")
        assert shown.getpixel((304, 240))[0] > shown.getpixel((304, 240))[1]*2
        # Traffic remains an overlay; no local height splitting is required.
        assert shown.getpixel((304, 320))[0] > shown.getpixel((304, 320))[1]*2
        assert shown.getpixel((320, 320)) == baseline.getpixel((320, 320))
    finally:
        server.close()
