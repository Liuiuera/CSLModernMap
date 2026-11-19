"""Satellite raster rendering and source reuse in real Chromium WebGL."""
import json
import os
from pathlib import Path
import pytest
from PIL import Image
from csmodernmap.preview_scene import PreviewSceneServer, world_to_geo
from browser_support import browser_page, make_bundle, load, screenshot, expect_color
from road_fixtures import scene_from_lines

pytestmark = [pytest.mark.browser, pytest.mark.skipif(os.environ.get('CSL_PREVIEW_BROWSER_TEST') != '1', reason='opt-in Chromium WebGL integration test')]


def test_satellite_raster_visibility_orientation_and_reuse(tmp_path, browser_page):
    scene = scene_from_lines([[(-80,0,0),(80,0,0)]], width=12)
    bundle = make_bundle(tmp_path/'satellite', scene)
    image = Image.new('RGB', (64,64), '#d84a4a')
    image.paste('#368cd1', (32,0,64,32))
    image.paste('#30b56b', (0,32,32,64))
    image.paste('#a64bce', (32,32,64,64))
    image.save(bundle.root/'satellite.png')
    manifest = bundle.manifest
    manifest['sources']['satellite'] = 'satellite.png'
    manifest['sourceRevisions']['satellite'] = 1
    manifest['satelliteSize'] = [64,64]
    manifest['satelliteAvailable'] = True
    manifest['satelliteCorners'] = [world_to_geo(100,100),world_to_geo(100,-100),world_to_geo(-100,-100),world_to_geo(-100,100)]
    manifest['renderStack'] = ['satellite','road']
    manifest['layers'] = {'satellite': True, 'road': False}
    (bundle.root/'manifest.json').write_text(json.dumps(manifest))
    server = PreviewSceneServer(Path(__file__).resolve().parents[2]/'dev/CSLModernMapRenderer/viewer/dist')
    requests=[]
    browser_page.on('request', lambda request: requests.append(request.url) if 'satellite.png' in request.url else None)
    server.replace_bundle(bundle)
    try:
        load(browser_page,server,1)
        image = screenshot(browser_page,'satellite-quadrants')
        for x,z,color in [(50,50,'#d84a4a'),(50,-50,'#368cd1'),(-50,50,'#30b56b'),(-50,-50,'#a64bce')]: expect_color(image,x,z,color)
        count=len(requests)
        for visible in [False,True,False,True]:
            browser_page.evaluate('(visible) => window.cslPreview.dispatch({type:"setLayerVisibility",layer:"satellite",visible})',visible)
        browser_page.wait_for_timeout(200)
        assert len(requests)==count
        expect_color(screenshot(browser_page,'satellite-reenabled'),50,50,'#d84a4a')
        assert not browser_page.evaluate("window.events.filter(e => e.type === 'renderError')")
        from csmodernmap.web_export_v2 import payload_from_preview_bundle, render_export_html
        payload = payload_from_preview_bundle(bundle, visibility={'satellite': True}, navigation_enabled=False)
        html = tmp_path/'satellite.html'
        html.write_text(render_export_html(payload, Path(__file__).resolve().parents[2]/'dev/CSLModernMapRenderer/viewer/dist'), encoding='utf-8')
        browser_page.goto(html.as_uri())
        browser_page.wait_for_timeout(1600)
        expect_color(screenshot(browser_page,'satellite-offline-html'),50,50,'#d84a4a')
    finally: server.close()
