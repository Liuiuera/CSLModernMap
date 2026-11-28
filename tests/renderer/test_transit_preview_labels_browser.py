"""公共交通路线标签在缩放间保留地图坐标和线路覆盖。"""
import json
import os

import pytest

from browser_support import load
from test_navigation_ui_browser import navigation_page, navigation_bundle, navigation_server, expect
from test_preview_3d_browser import expose_map


pytestmark = [pytest.mark.browser, pytest.mark.skipif(
    os.environ.get('CSL_PREVIEW_BROWSER_TEST') != '1', reason='opt-in Chromium test')]


def test_transit_labels_cover_selected_routes_without_relayout_on_pan(
        navigation_page, navigation_bundle, navigation_server):
    page = navigation_page
    features = [
        {'type': 'Feature', 'geometry': {'type': 'LineString', 'coordinates': coords},
         'properties': {'lineId': identity, 'kind': 'Bus', 'name': title, 'color': '#4285A0'}}
        for identity, title, coords in [
            (1, '市内线', [[-.0015, 0], [.0015, 0]]),
            (2, '市内线', [[-.0015, .0001], [.0015, .0001]]),
            (3, '另一侧', [[.15, -.002], [.15, .002]]),
        ]
    ]
    manifest = navigation_bundle.manifest
    manifest['sources']['transit'] = 'transit.geojson?revision=1'
    manifest['sourceRevisions']['transit'] = 1
    manifest['theme']['transit']['labelStyle']['layout'].update(min_gap=10_000, max_per_line=1)
    (navigation_bundle.root/'transit.geojson').write_text(
        json.dumps({'type': 'FeatureCollection', 'features': features}), encoding='utf-8')
    (navigation_bundle.root/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    expose_map(page)
    load(page, navigation_server, 1)
    page.evaluate("""() => {
        __testMap.jumpTo({center:[0,0], zoom:12});
        return cslPreview.dispatch({type:'setContextMode', mode:'transit',
            options:{ordinary_pois:'hidden',stations:false,line_ids:[1,2,3]}});
    }""")
    expect(page.locator('.map-transit-badge').filter(has_text='市内线')).to_have_count(2)
    for identity in (1, 2, 3):
        expect(page.locator(f'.map-transit-badge[data-route-id="{identity}"]')).to_have_count(1)
    remote = page.locator('.map-transit-badge[data-route-id="3"]')
    page.evaluate("""() => {
        window.remoteBadge = document.querySelector('[data-route-id="3"]');
        window.badgeLayoutWrites = 0;
        new MutationObserver(records => { window.badgeLayoutWrites += records.length; })
            .observe(document.querySelector('#map'), {subtree:true,attributes:true,
                attributeFilter:['data-route-id']});
    }""")
    assert remote.bounding_box()['x'] > 1100
    page.evaluate("""() => new Promise(resolve => {
        __testMap.once('idle', () => resolve());
        __testMap.jumpTo({center:[.15,0]});
    })""")
    # 横移触发常规标签工作后，线路徽章仍是原 DOM，且没有重新执行徽章绘制。
    page.wait_for_timeout(500)
    assert page.evaluate("window.remoteBadge === document.querySelector('[data-route-id=\"3\"]')")
    assert page.evaluate('window.badgeLayoutWrites') == 0
    center = remote.bounding_box()
    assert 0 < center['x'] < 1100 - center['width']

    page.evaluate("() => { __testMap.jumpTo({zoom:13}); }")
    page.wait_for_function('() => window.badgeLayoutWrites > 0')
    page.evaluate("""() => cslPreview.dispatch({type:'setContextMode', mode:'transit',
        options:{ordinary_pois:'hidden',stations:false,line_ids:[3]}})""")
    expect(page.locator('.map-transit-badge')).to_have_count(1)
    expect(remote).to_have_count(1)
    assert not page.evaluate("window.events.some(e=>e.type==='renderError')")
