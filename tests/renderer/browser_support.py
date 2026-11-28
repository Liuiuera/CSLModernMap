"""Shared Chromium fixtures and presentation checks; no browser starts during collection."""
import io
import json
import os
from pathlib import Path
import pytest
from PIL import Image
from csmodernmap.preview_scene import PreviewSceneBundle, _ordered_road_features, preview_theme
from road_fixtures import scene_from_lines, joined_surfaces

COLORS = ['#d84a4a', '#368cd1', '#30b56b', '#a64bce']


def expect(locator):
    from playwright.sync_api import expect as playwright_expect
    return playwright_expect(locator)

def make_bundle(root, scene, revision=1, *, colorize=True, region=None, accurate=True, style=None):
    root.mkdir()
    from csmodernmap.preview_road_order import build_road_order
    from csmodernmap.style import load_style
    from csmodernmap._paths import resource_dir
    from csmodernmap.pixel_sizes import footprint_factors
    if style is None:
        style = load_style(str(resource_dir('styles')/'modern.yaml'))
    features, diagnostics = _ordered_road_features(scene, build_road_order(scene, accurate=accurate),
        footprint_factors=footprint_factors(style.data['roads']))
    theme = preview_theme(style, {})
    template = next(iter(theme['road']['levels'].values()))
    for i, color in enumerate(COLORS, 1):
        theme['road']['levels'][f'test:{i}'] = {
            **template, 'fill': color, 'border': '#111111', 'enabled': True,
            'borderPx': 2,
        }
    if colorize:
        for feature in features:
            feature['properties']['styleKey'] = f"test:{feature['properties']['roadId']}"
    (root/'roads.geojson').write_text(json.dumps({'type': 'FeatureCollection', 'features': features}))
    Image.new('RGBA', (1, 1)).save(root/'poi-sprite.png')
    (root/'poi-sprite.json').write_text('{}')
    manifest = {
        'schemaVersion': 1, 'sceneId': 'road-browser-test', 'revision': revision,
        'sources': {'roads': f'roads.geojson?revision={revision}'},
        'sourceRevisions': {'roads': revision},
        'world': {'geoBounds': [[-.001, -.001], [.001, .001]]},
        'initialRegion': region or [-100, -100, 100, 100],
        'poiAtlas': {'image': 'poi-sprite.png', 'index': 'poi-sprite.json'},
        'layers': {'road': True}, 'renderStack': ['road'], 'theme': theme,
        'roadOrder': diagnostics,
    }
    (root/'manifest.json').write_text(json.dumps(manifest))
    return PreviewSceneBundle(root, manifest['sceneId'], revision, manifest)



@pytest.fixture
def browser_page():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=[
            '--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
        page = browser.new_page(viewport={'width': 640, 'height': 640}, device_scale_factor=1)
        page.add_init_script("""window.events = []; window.chrome = window.chrome || {};
          window.chrome.webview = {postMessage: s => window.events.push(JSON.parse(s)),
                                   addEventListener: () => {}};""")
        yield page
        browser.close()



def load(page, server, revision):
    if page.url == 'about:blank':
        page.goto(server.start())
        page.wait_for_function("() => window.events.some(e => e.type === 'ready')")
    page.evaluate('(command) => window.cslPreview.dispatch(command)', {
        'type': 'loadScene', 'revision': revision, 'manifestUrl': server.manifest_url})
    page.wait_for_function('(r) => window.events.some(e => e.type === "sceneLoaded" && e.revision === r)',
                           arg=revision)
    event = page.evaluate('(r) => window.events.findLast(e => e.type === "sceneLoaded" && e.revision === r)', revision)
    assert event['performance']['completion'] == 'applied', event
    page.wait_for_function("id => window.events.some(e => e.type === 'scenePresentationReady' && e.bundleId === id)",
                           arg=event['bundleId'])
    assert not page.evaluate("window.events.filter(e => e.type === 'renderError')")



def screenshot(page, name):
    image = Image.open(io.BytesIO(page.screenshot())).convert('RGB')
    if directory := os.environ.get('CSL_PREVIEW_BROWSER_OUTPUT'):
        output = Path(directory)
        output.mkdir(parents=True, exist_ok=True)
        image.save(output/f'{name}.png')
    return image



def expect_color(image, x, z, color):
    # fitWorldBounds([-100,-100,100,100], 28), north-up, zero latitude.
    px, py = round(320-z*2.92), round(320-x*2.92)
    actual = image.getpixel((px, py))
    expected = tuple(bytes.fromhex(color[1:]))
    assert max(abs(a-b) for a, b in zip(actual, expected)) <= 5, (x, z, actual, expected)
