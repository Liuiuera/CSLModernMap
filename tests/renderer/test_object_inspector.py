"""Exercise typed object editing against the real engine and paint resolvers."""
from copy import deepcopy
import io
import math

import pytest

from csmodernmap import i18n
from csmodernmap.guir_backend import GuirBackend
from csmodernmap.winui_engine import EngineServer, LoopScheduler
from csmodernmap.preview_scene import road_theme, preview_theme
from csmodernmap.layers.railway_layer import resolve_railway_style


@pytest.fixture
def server(tmp_path):
    backend = GuirBackend(LoopScheduler(), settings_dir=tmp_path)
    backend.document = object()
    backend.transit_line_catalog = lambda: [{'id': 12, 'name': '测试线路', 'kind': 'Metro'}]
    engine = EngineServer(io.StringIO(), io.StringIO(), backend)
    yield engine
    backend.close()
    engine._preview_files.cleanup()


def selection(object_type, **props):
    return {'objectType': object_type, 'featureId': 'test:1', 'properties': props}


def read(server, obj, context='map'):
    return server.dispatch('getObjectInspector', {'object': obj, 'context': context, 'documentEpoch': 0})


def write(server, obj, model, key, value, context='map'):
    return server.dispatch('setInspectorField', {'object': obj, 'context': context, 'documentEpoch': 0,
                                               'settingsRevision': model['settingsRevision'], 'key': key, 'value': value})


def fields(model, section=None):
    return {f['key']: f for s in model['sections'] if section is None or s['key'] == section for f in s['fields']}


@pytest.mark.parametrize('kind,props', [
    ('road', {'level': 'branch', 'mode': 'ground'}), ('building', {'category': 'school'}),
    ('poi', {'visibilityGroup': 'transit', 'category': 'metro'}),
    ('transit_line', {'kind': 'metro', 'lineId': 12, 'stopCount': 0}),
    ('transit_station', {'kind': 'metro', 'servingLineIds': '[12]', 'servingLineCount': 1}),
    ('railway', {'railType': 'train', 'station': False}),
    ('district', {'kind': 'DISTRICT'}), ('zone', {'kind': 'PARK_ZONE'}),
])
def test_every_selectable_type_has_summary_and_typed_controls(server, kind, props):
    model = read(server, selection(kind, **props))
    assert model['summary']
    assert len(fields(model, 'appearance')) >= 3
    assert model['sections'][0]['scope']
    for field in fields(model).values():
        assert field['kind'] in ('color', 'number', 'toggle', 'choice', 'font')
        assert not isinstance(field['value'], (dict, list))
    assert model['technical']


@pytest.mark.parametrize('level', ['highway', 'main', 'branch', 'special', 'beautification'])
@pytest.mark.parametrize('mode', ['ground', 'elevated', 'tunnel'])
def test_road_edits_follow_actual_level_and_mode(server, level, mode):
    obj = selection('road', level=level, mode=mode, styleKey=f'{level}:{mode}')
    before = deepcopy(road_theme(server.backend.current_style_data()))
    model = write(server, obj, read(server, obj), 'road.fill', '#123456')
    after = road_theme(server.backend.current_style_data())
    assert after['levels'][f'{level}:{mode}']['fill'] == '#123456'
    if level == 'branch':
        assert after['levels']['main:ground'] == before['levels']['main:ground']
    assert fields(model)['road.fill']['value'] == '#123456'


def test_independent_elevated_override_and_scoped_reset(server):
    style = server.backend._style_data_mut()
    style['roads']['branch_elevated'] = {**style['roads']['branch'], 'fill': '#AABBCC', 'border': '#BBCCDD', 'line_width': 1.2}
    obj = selection('road', level='branch', mode='elevated')
    before_main = deepcopy(road_theme(style)['levels']['main:ground'])
    model = write(server, obj, read(server, obj), 'road.fill', '#123456')
    assert road_theme(server.backend.current_style_data())['levels']['branch:elevated']['fill'] == '#123456'
    assert road_theme(server.backend.current_style_data())['levels']['branch:ground']['fill'] != '#123456'
    server.backend.apply_style_override('label.size.road', 21)
    model = read(server, obj)
    server.dispatch('resetInspectorGroup', {'object': obj, 'context': 'map', 'documentEpoch': 0,
                                          'settingsRevision': model['settingsRevision'], 'group': 'appearance'})
    assert road_theme(server.backend.current_style_data())['levels']['main:ground'] == before_main
    assert server.backend.field_value('label.size.road') == 21


def test_building_mode_reveals_relevant_controls(server):
    obj = selection('building', category='hospital')
    model = read(server, obj)
    assert 'building.poi_tint.contrast' in fields(model, 'appearance')
    assert 'building.default' not in fields(model, 'appearance')
    model = write(server, obj, model, 'building.color_mode', 'uniform')
    assert 'building.default' in fields(model, 'appearance')
    assert 'building.poi_tint.contrast' not in fields(model)


def test_poi_group_visibility_preserves_other_groups_and_can_reenable(server):
    obj = selection('poi', visibilityGroup='transit', category='metro')
    before = server.backend.poi_visibility_snapshot()
    model = write(server, obj, read(server, obj), 'visibility.icons', False)
    assert not fields(model)['visibility.names']['enabled']
    assert model['notices']
    model = write(server, obj, model, 'visibility.icons', True)
    assert fields(model)['visibility.names']['enabled']
    for group in ('facility', 'ordinary'):
        assert server.backend.poi_visibility_snapshot()['icons'][group] == before['icons'][group]


def test_line_and_station_controls_use_different_channels(server):
    line = read(server, selection('transit_line', kind='metro'))
    station = read(server, selection('transit_station', kind='metro'))
    assert 'view.line_width' in fields(line) and 'view.station_diameter' not in fields(line)
    assert 'view.station_diameter' in fields(station) and 'view.line_width' not in fields(station)
    assert 'view.station_name_size' in fields(station)
    assert 'view.facilities_enabled' in fields(station)


def test_transit_context_options_update_server_session_without_touching_map_labels(server):
    obj = selection('transit_line', kind='metro')
    before = deepcopy(server.backend.current_style_data()['transports'].get('label', {}))
    model = write(server, obj, read(server, obj, 'transit'), 'view.line_label_size', 23, 'transit')
    assert server.transit_options()['line_label_size'] == 23
    assert fields(model)['view.line_label_size']['value'] == 23
    assert server.backend.current_style_data()['transports'].get('label', {}) == before


def test_zone_fill_and_border_are_distinct_and_opacity_is_percent(server):
    obj = selection('zone', kind='PARK_ZONE')
    model = write(server, obj, read(server, obj), 'zone.fill', '#112233')
    model = write(server, obj, model, 'zone.color', '#445566')
    model = write(server, obj, model, 'zone.opacity', 40)
    style = server.backend.current_style_data()
    assert style['zone']['fill'] == '#112233'
    assert style['zone']['border'] == '#445566'
    assert style['zone']['fill_alpha'] == 102
    paint = preview_theme(style)
    assert paint['zone']['fill'] == '#112233'
    assert paint['zone']['opacity'] == pytest.approx(.4)


def test_railway_edit_materializes_inherited_color_without_changing_other_types(server):
    obj = selection('railway', railType='train', station=False)
    before = resolve_railway_style(server.backend.current_style_data()['railway'])
    model = write(server, obj, read(server, obj), 'track.width', 5)
    after = resolve_railway_style(server.backend.current_style_data()['railway'])
    assert after['types']['train']['baseColor'] == before['types']['train']['baseColor']
    assert after['types']['metro'] == before['types']['metro']
    assert fields(model)['track.width']['value'] == 5


@pytest.mark.parametrize('key,value', [('road.fill', 'red'), ('road.fill', 'roads.primary.fill'),
                                     ('road.line_width', math.nan), ('road.line_width', True),
                                     ('road.line_width', 0), ('arbitrary.path', '#112233')])
def test_invalid_or_arbitrary_mutation_is_rejected_without_changes(server, key, value):
    obj = selection('road', level='branch')
    model = read(server, obj)
    before = deepcopy(server.backend.current_style_data())
    with pytest.raises(ValueError):
        write(server, obj, model, key, value)
    assert server.backend.current_style_data() == before


def test_zero_width_keeps_selected_theme_and_allows_further_edits(server):
    obj = selection('road', level='branch')
    model = read(server, obj)
    theme = server.backend.theme_stem
    width = fields(model)['road.line_width']
    assert width['low'] > 0
    with pytest.raises(ValueError, match='outside the supported range'):
        write(server, obj, model, 'road.line_width', 0)
    assert server.backend.theme_stem == theme
    assert read(server, obj)['settingsRevision'] == model['settingsRevision']
    model = write(server, obj, model, 'road.line_width', 2.5)
    model = write(server, obj, model, 'road.border_width', 0)
    assert fields(model)['road.line_width']['value'] == 2.5
    assert fields(model)['road.border_width']['value'] == 0


def test_stale_epoch_and_changed_settings_are_rejected(server):
    obj = selection('road', level='branch')
    model = read(server, obj)
    server.backend.apply_style_override('road.level.main.fill', '#123456')
    with pytest.raises(RuntimeError, match='设置已改变'):
        write(server, obj, model, 'road.fill', '#445566')
    with pytest.raises(RuntimeError, match='城市已切换'):
        server.dispatch('getObjectInspector', {'object': obj, 'documentEpoch': 99})


def test_all_raw_data_is_preserved_and_english_labels_are_available(server):
    obj = selection('unknown', **{f'extra{i}': i for i in range(60)}, missing=None, zero=0, disabled=False)
    model = read(server, obj)
    assert len(model['technical']) == 64
    assert not model['sections']
    previous = i18n.LANG
    try:
        i18n.set_lang('en')
        model = read(server, selection('road', level='branch', mode='tunnel'))
        assert model['typeLabel'] == 'Road'
        assert fields(model)['road.fill']['label'] == 'Fill color'
    finally:
        i18n.set_lang(previous)


def test_preview_layout_controls_synchronize_shared_png_options(server):
    obj = selection('poi', poiGroup='ordinary', visibilityGroup='ordinary')
    png = deepcopy(server.backend.get_poi_profile('normal_png'))
    model = write(server, obj, read(server, obj), 'preview.density_tier', 'dense')
    model = write(server, obj, model, 'preview.merge_distance', 42)
    assert server.backend.get_poi_profile('preview')['density_tier'] == 'dense'
    assert server.backend.get_poi_profile('preview')['groups']['ordinary']['distance_m'] == 42
    png['density_tier'] = 'dense'
    png['groups']['ordinary']['distance_m'] = 42
    assert server.backend.get_poi_profile('normal_png') == png


def test_open_theme_draft_cannot_overwrite_inspector_edits(server):
    draft = server.dispatch('getThemeDraft', {})
    obj = selection('road', level='branch')
    write(server, obj, read(server, obj), 'road.fill', '#123456')
    with pytest.raises(RuntimeError, match='其他页面修改'):
        server.dispatch('commitThemeDraft', {'theme': draft['theme'], 'data': draft['data'],
                       'documentEpoch': 0, 'settingsRevision': draft['settingsRevision']})
    assert road_theme(server.backend.current_style_data())['levels']['branch:ground']['fill'] == '#123456'


def history(server, obj, model, method, context='map'):
    return server.dispatch(method, {'object': obj, 'context': context, 'documentEpoch': 0,
                                    'settingsRevision': model['settingsRevision']})


def test_undo_redo_restore_only_the_edited_style_path_and_pixel_width(server):
    obj = selection('road', level='branch')
    before = deepcopy(server.backend.current_style_data())
    model = write(server, obj, read(server, obj), 'road.fill', '#123456')
    before['name'] = server.backend.current_style_data()['name']  # Editing a preset creates a personal theme.
    model = write(server, obj, model, 'road.line_width', 2.5)
    assert model['canUndo']
    model = history(server, obj, model, 'undoInspector')
    assert road_theme(server.backend.current_style_data())['levels']['branch:ground']['lineWidth'] == road_theme(before)['levels']['branch:ground']['lineWidth']
    assert fields(model)['road.fill']['value'] == '#123456'
    model = history(server, obj, model, 'undoInspector')
    assert server.backend.current_style_data() == before
    model = history(server, obj, model, 'redoInspector')
    model = history(server, obj, model, 'redoInspector')
    assert fields(model)['road.line_width']['value'] == 2.5
    assert fields(model)['road.fill']['value'] == '#123456'
    assert not model['canRedo']


def test_history_cannot_overwrite_other_page_changes(server):
    obj = selection('road', level='branch')
    write(server, obj, read(server, obj), 'road.fill', '#123456')
    server.backend.apply_style_override('road.level.main.fill', '#445566')
    with pytest.raises(RuntimeError, match='其他操作修改'):
        history(server, obj, read(server, obj), 'undoInspector')
    assert road_theme(server.backend.current_style_data())['levels']['main:ground']['fill'] == '#445566'


def test_undo_poi_visibility_and_profile_retains_png_state(server):
    obj = selection('poi', visibilityGroup='ordinary')
    before = server.backend.poi_visibility_snapshot()
    png = deepcopy(server.backend.get_poi_profile('normal_png'))
    model = write(server, obj, read(server, obj), 'preview.density_tier', 'dense')
    model = write(server, obj, model, 'visibility.icons', False)
    model = history(server, obj, model, 'undoInspector')
    assert server.backend.poi_visibility_snapshot() == before
    model = history(server, obj, model, 'undoInspector')
    assert server.backend.get_poi_profile('preview')['density_tier'] == 'standard'
    assert server.backend.get_poi_profile('normal_png') == png


def test_history_across_map_and_transit_contexts(server):
    road = selection('road', level='branch')
    line = selection('transit_line', kind='metro')
    before = server.transit_options()['line_label_size']
    write(server, road, read(server, road), 'road.fill', '#123456')
    write(server, line, read(server, line, 'transit'), 'view.line_label_size', 23, 'transit')
    model = history(server, road, read(server, road), 'undoInspector')
    assert server.transit_options()['line_label_size'] == before
    model = history(server, road, model, 'undoInspector')
    model = history(server, line, read(server, line, 'transit'), 'redoInspector', 'transit')
    model = history(server, line, model, 'redoInspector', 'transit')
    assert server.transit_options()['line_label_size'] == 23


def test_new_edit_after_external_change_invalidates_old_history(server):
    obj = selection('road', level='branch')
    write(server, obj, read(server, obj), 'road.fill', '#123456')
    server.backend.apply_style_override('road.level.branch.fill', '#445566')
    model = write(server, obj, read(server, obj), 'road.border', '#778899')
    model = history(server, obj, model, 'undoInspector')
    assert fields(model)['road.fill']['value'] == '#445566'
    assert not model['canUndo']


def test_history_is_reset_when_the_document_epoch_changes(server):
    obj = selection('road', level='branch')
    write(server, obj, read(server, obj), 'road.fill', '#123456')
    server._document_epoch = 1
    model = server.dispatch('getObjectInspector', {'object': obj, 'context': 'map', 'documentEpoch': 1})
    assert not model['canUndo'] and not model['canRedo']
