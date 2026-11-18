"""建筑目录在两代数据中保持完整身份和父子归并。"""
from copy import deepcopy

import pytest

from csmodernmap.building_catalog import BuildingCatalog
from csmodernmap.cmm import DisplayName
from pathlib import Path
from urllib.parse import unquote, urlparse
from test_building_category_colors import building, document
from test_object_inspector import server


@pytest.mark.parametrize('game', ['cs1', 'cs2'])
def test_all_buildings_include_unnamed_and_merge_only_exported_children(game):
    items = [building(1, 'Education', game=game), building(2, 'None', game=game, parent=1),
             building(3, 'Residential', game=game), building(4, 'Education', game=game)]
    items[0].display_name = items[3].display_name = DisplayName('Same name', 'custom')
    items[2].display_name = DisplayName()
    doc = document(game, items)
    before = deepcopy(doc.to_dict())
    catalog = BuildingCatalog(doc)
    rows = {row['id']: row for row in catalog.rows()}
    assert set(rows) == {1, 3, 4}
    assert rows[1]['memberIds'] == [1, 2]
    assert rows[1]['category'] == 'education'
    assert rows[3]['name'] == ''
    selection = catalog.selection(2, {})
    assert selection['properties']['buildingId'] == 1
    assert [member['id'] for member in selection['members']] == [2]
    assert len(selection['feature']['geometry']['coordinates']) == 2
    highlight = catalog.highlight('education')
    assert {feature['properties']['buildingId'] for feature in highlight['features']} == {1, 4}
    assert doc.to_dict() == before


def test_child_id_contract_is_also_used():
    items = [building(1, 'Education'), building(2, 'Education')]
    items[0].child_ids = [2]
    assert BuildingCatalog(document('cs1', items)).rows()[0]['memberIds'] == [1, 2]


@pytest.mark.parametrize('game,native', [('cs1', 'Police Station'), ('cs2', 'PoliceStation01')])
def test_row_uses_actual_poi_icon_file(game, native):
    item = building(1, 'PoliceDepartment', native, game=game)
    catalog = BuildingCatalog(document(game, [item]))
    for appearance in ('light', 'dark'):
        uri = catalog.rows({'poi_icons': {'appearance': appearance}})[0]['iconPath']
        assert uri.startswith('file:///')
        path = Path(unquote(urlparse(uri).path).lstrip('/'))
        assert path.is_file() and path.suffix == '.png'
        assert ('dark' in path.parts) == (appearance == 'dark')
    item.category_id = 'no-such-asset'
    assert catalog.rows()[0]['iconPath'] == ''


def test_engine_catalog_rejects_stale_and_invalid_identity_and_refreshes_document(server):
    server.backend.document = document('cs2', [building(1, 'Residential', game='cs2')])
    request = lambda method, **args: server.dispatch(method, {'documentEpoch': 0, **args})
    assert request('getBuildingCatalog')['items'][0]['id'] == 1
    assert request('getBuildingSelection', buildingId=1)['properties']['buildingId'] == 1
    model = request('getObjectInspector', object=request('getBuildingSelection', buildingId=1))
    assert model['objectType'] == 'building'
    with pytest.raises(RuntimeError):
        server.dispatch('getBuildingCatalog', {'documentEpoch': -1})
    for ident in (True, '1', 99, None):
        with pytest.raises(ValueError):
            request('getBuildingSelection', buildingId=ident)
    with pytest.raises(ValueError):
        request('getBuildingHighlight', category='made-up')
    server.backend.document = document('cs2', [building(9, 'Office', game='cs2')])
    assert request('getBuildingCatalog')['items'][0]['id'] == 9
    with pytest.raises(ValueError):
        request('getBuildingSelection', buildingId=1)


@pytest.mark.parametrize('game', ['cs1', 'cs2'])
def test_engine_catalog_refreshes_display_name_policy_and_preserves_custom_names(server, game):
    item = building(1, 'Residential', game=game)
    item.display_name = DisplayName('Game name', 'game')
    item.category_id, item.category_name, item.category_locale = 'residential', '住宅', 'zh-CN'
    custom = building(2, 'Residential', game=game)
    custom.display_name = DisplayName('Player name', 'custom')
    server.backend.document = document(game, [item, custom])
    request = lambda: server.dispatch('getBuildingCatalog', {'documentEpoch': 0})
    server.dispatch('setField', {'key': 'poi.game_names', 'value': False})
    result = request()
    assert result['useGameNames'] is False
    assert [(row['name'], row['nameSource']) for row in result['items']] == [('住宅', 'category'), ('Player name', 'custom')]
    server.dispatch('setField', {'key': 'poi.game_names', 'value': True})
    result = request()
    assert result['useGameNames'] is True
    assert [(row['name'], row['nameSource']) for row in result['items']] == [('Game name', 'game'), ('Player name', 'custom')]
