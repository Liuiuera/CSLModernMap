"""Exercise the built Viewer messages consumed by the native host."""
import os
from pathlib import Path

import pytest

from csmodernmap.cmm import DocumentSession
from csmodernmap.cmm.loader import load_document
from csmodernmap.preview_scene import PreviewSceneCompiler, PreviewSceneServer
from csmodernmap._paths import resource_dir
from csmodernmap.style import load_style

pytestmark = pytest.mark.skipif(os.environ.get("CSL_PREVIEW_BROWSER_TEST") != "1",
                                reason="opt-in Chromium WebGL test")


@pytest.mark.parametrize("hold_details", [False, True])
def test_base_and_complete_messages_include_native_host_fields(hold_details):
    from playwright.sync_api import sync_playwright

    viewer = Path(os.environ.get("CSL_TEST_VIEWER_DIST", str(
        Path(__file__).resolve().parents[1] / "dev/CSLModernMapRenderer/viewer/dist")))
    session = DocumentSession(load_document(Path(__file__).parent / "fixtures/tiny-city.cmm"))
    server = PreviewSceneServer(viewer)
    errors = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=[
                "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
            try:
                page = browser.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.add_init_script("""window.events = []; window.chrome = {webview: {
                    postMessage: text => window.events.push(JSON.parse(text)),
                    addEventListener: () => {}}};""")
                page.add_init_script("""const fetchResource = window.fetch.bind(window);
                    window.fetch = (url, options) => {
                      const path = new URL(url, location.href).pathname;
                      if (path === window.failPath) return Promise.reject(new Error('missing optional atlas'));
                      if (path === window.holdPath) return new Promise(resolve => {
                        window.unblock = () => resolve(fetchResource(url, options));
                      });
                      return fetchResource(url, options);
                    };""")
                previous = None
                for phase, expected in (("base", "base"), ("full", "complete")):
                    bundle = PreviewSceneCompiler.compile_full(session, load_style(str(resource_dir("styles") / "modern.yaml")), 1,
                        previous=previous, options={"phase": phase})
                    server.replace_bundle(bundle)
                    if previous is None:
                        page.goto(server.start())
                        page.wait_for_function("window.events.some(e => e.type === 'ready')")
                    page.evaluate("window.events = []")
                    if phase == "full" and hold_details:
                        from urllib.parse import urljoin, urlparse
                        def path(url):
                            return urlparse(urljoin(server.manifest_url, url)).path
                        page.evaluate("([hold, fail]) => {window.holdPath=hold; window.failPath=fail}", [
                            path(bundle.manifest["sources"]["pois"]), path(bundle.manifest["poiAtlas"]["index"])])
                    page.evaluate("command => window.cslPreview.dispatch(command)", {
                        "type": "loadScene", "sceneId": bundle.scene_id, "revision": 1, "manifestUrl": server.manifest_url})
                    event = page.evaluate("window.events.find(e => e.type === 'sceneLoaded')")
                    assert event is not None, page.evaluate("window.events")
                    assert event["sceneId"] == bundle.scene_id
                    assert event["revision"] == 1
                    assert event["bundleId"] == bundle.root.name
                    assert event["completion"] == expected
                    assert event["performance"]["completion"] == "applied"
                    if phase == "full" and hold_details:
                        page.wait_for_function("typeof window.unblock === 'function'")
                        page.evaluate("window.cslPreview.dispatch({type:'beginSelection'})")
                        box = page.locator('canvas.maplibregl-canvas').bounding_box()
                        x, y = box["x"] + box["width"]/2, box["y"] + box["height"]/2
                        page.mouse.move(x, y)
                        page.mouse.down()
                        page.mouse.move(x + 45, y + 45, steps=4)
                        page.mouse.up()
                        page.wait_for_function("window.events.some(e => e.type === 'selectionChanged')")
                        assert not page.evaluate("id => window.events.some(e => e.type === 'scenePresentationReady' && e.bundleId===id)", bundle.root.name)
                        page.evaluate("window.unblock()")
                    page.wait_for_function("id => window.events.some(e => e.type === 'scenePresentationReady' && e.bundleId===id)", arg=bundle.root.name)
                    assert not page.evaluate("window.events.filter(e => e.type === 'renderError' && e.fatal)")
                    previous = bundle
                assert not errors
            finally:
                browser.close()
    finally:
        server.close()
