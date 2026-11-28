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


def test_base_and_complete_messages_include_native_host_fields():
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
                previous = None
                for phase, expected in (("base", "base"), ("full", "complete")):
                    bundle = PreviewSceneCompiler.compile_full(session, load_style(str(resource_dir("styles") / "modern.yaml")), 1,
                        previous=previous, options={"phase": phase})
                    server.replace_bundle(bundle)
                    if previous is None:
                        page.goto(server.start())
                        page.wait_for_function("window.events.some(e => e.type === 'ready')")
                    page.evaluate("window.events = []")
                    page.evaluate("command => window.cslPreview.dispatch(command)", {
                        "type": "loadScene", "sceneId": bundle.scene_id, "revision": 1, "manifestUrl": server.manifest_url})
                    event = page.evaluate("window.events.find(e => e.type === 'sceneLoaded')")
                    assert event is not None, page.evaluate("window.events")
                    assert event["sceneId"] == bundle.scene_id
                    assert event["revision"] == 1
                    assert event["bundleId"] == bundle.root.name
                    assert event["completion"] == expected
                    assert event["performance"]["completion"] == "applied"
                    page.wait_for_function("id => window.events.some(e => e.type === 'scenePresentationReady' && e.bundleId===id)", arg=bundle.root.name)
                    assert not page.evaluate("window.events.filter(e => e.type === 'renderError')")
                    previous = bundle
                assert not errors
            finally:
                browser.close()
    finally:
        server.close()
