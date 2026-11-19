"""实际 WebGL 中验证隐藏建筑的定位、高亮和场景恢复。"""
import os
from pathlib import Path

import pytest

from csmodernmap.building_catalog import BuildingCatalog
from csmodernmap.cmm import Polygon, Vec2
from csmodernmap.preview_scene import PreviewSceneServer
from test_building_category_colors import building, document
from browser_support import browser_page, make_bundle, load, screenshot
from road_fixtures import scene_from_lines

pytestmark = [pytest.mark.browser, pytest.mark.skipif(os.environ.get('CSL_PREVIEW_BROWSER_TEST') != '1', reason='opt-in Chromium WebGL test')]


def test_catalog_highlight_and_selection_work_without_visible_building_source(tmp_path, browser_page):
    item = building(1, 'Education')
    item.footprint = Polygon((Vec2(-20, -20), Vec2(20, -20), Vec2(20, 20), Vec2(-20, 20)))
    catalog = BuildingCatalog(document('cs1', [item]))
    bundle = make_bundle(tmp_path / 'scene', scene_from_lines([[(-80, 0, 80), (80, 0, 80)]]))
    server = PreviewSceneServer(Path(__file__).resolve().parents[2] / 'dev/CSLModernMapRenderer/viewer/dist')
    server.replace_bundle(bundle)
    page = browser_page
    dispatch = lambda command: page.evaluate('command => window.cslPreview.dispatch(command)', command)
    try:
        load(page, server, 1)
        before = screenshot(page, 'building-list-before')
        dispatch({'type': 'setBuildingHighlight', 'data': catalog.highlight('education')})
        page.wait_for_timeout(200)
        category = screenshot(page, 'building-list-category')
        assert category.getpixel((320, 320)) != before.getpixel((320, 320))
        dispatch({'type': 'setBuildingHighlight'})
        page.wait_for_timeout(200)
        assert screenshot(page, 'building-list-cleared').getpixel((320, 320)) == before.getpixel((320, 320))
        selection = catalog.selection(1, {})
        dispatch({'type': 'selectCatalogBuilding', 'selection': selection, 'locate': False})
        page.wait_for_timeout(200)
        selected = screenshot(page, 'building-list-selected')
        assert selected.getpixel((320, 320)) != before.getpixel((320, 320))
        dispatch({'type': 'clearObjectSelection'})
        server.replace_bundle(make_bundle(tmp_path / 'rebuilt', scene_from_lines([[(-80, 0, 80), (80, 0, 80)]]), revision=2))
        load(page, server, 2)
        dispatch({'type': 'restoreObjectSelection', 'selection': selection})
        page.wait_for_timeout(200)
        assert screenshot(page, 'building-list-restored').getpixel((320, 320)) == selected.getpixel((320, 320))
        assert not page.evaluate('window.events.filter(e => e.type === "objectSelected")')
        dispatch({'type': 'selectCatalogBuilding', 'selection': selection, 'locate': True})
        page.wait_for_timeout(350)
        assert not page.evaluate('window.events.filter(e => e.type === "renderError")')
    finally:
        server.close()
