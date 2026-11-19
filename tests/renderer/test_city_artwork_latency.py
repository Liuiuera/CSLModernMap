"""草稿复用保持固定布局、正式资源和依赖失效。"""
from copy import deepcopy

import pytest
from PIL import ImageChops

from csmodernmap.city_artwork import build_city_artwork
from csmodernmap.artwork import render_artwork
from csmodernmap.cmm import DocumentSession
from csmodernmap import fonts
from test_city_artwork import city_job, build
from test_transit_artwork import transit_job


def test_building_and_text_paint_reuse_placement_and_match_cold_export(city_job, monkeypatch):
    _, job, document, session = city_job
    job['layers'] += ['building', 'watermark']
    style = job['ui']['style_overrides'][job['style']]
    style['buildings']['color_mode'] = 'uniform'
    first = build(city_job, draft=True).to_dict()
    for section in ('building', 'text', 'watermark'):
        if section == 'building':
            style['buildings']['default'] = '#C12234'
        elif section == 'text':
            style['labels']['district']['color'] = '#4621D4'
        else:
            style['name'] = 'Renamed'
        info = {}
        with monkeypatch.context() as patch:
            patch.setattr('csmodernmap.labeling.LabelPipeline.run', lambda *a, **k: pytest.fail('ran placement'))
            patch.setattr('csmodernmap.layers.poi_icon_layer.POIIconLayer.render', lambda *a: pytest.fail('ran POI'))
            changed = build(city_job, draft=True, diagnostics=info)
        cold = build_city_artwork(job, document, session=DocumentSession(document), draft=True,
                                  document_epoch=0, revision=1)
        assert changed.to_dict() == cold.to_dict()
        if section == 'building':
            assert info['reusedLayout'] and changed.to_dict()['objects'] == first['objects']
        with render_artwork(changed) as actual, render_artwork(cold) as expected:
            assert actual.tobytes() == expected.tobytes()


def test_preset_switch_uses_effective_styles_and_matches_cold(city_job):
    engine, job, document, _ = city_job
    build(city_job, draft=True)
    themes = job['ui']['style_overrides']
    themes['copy'] = deepcopy(themes[job['style']])
    themes['dark'] = engine.backend.theme_data('dark')
    for theme in ('copy', 'dark'):
        job['style'] = theme
        info = {}
        changed = build(city_job, draft=True, diagnostics=info)
        cold = build_city_artwork(job, document, session=DocumentSession(document), draft=True,
                                  document_epoch=0, revision=1)
        assert info['reusedLayout'] == (theme == 'copy')
        assert changed.to_dict() == cold.to_dict()


def test_transit_draft_reuses_independent_components(transit_job):
    from csmodernmap.transit_artwork import build_transit_artwork
    job, document, session = transit_job
    job['opts']['ordinary_pois'] = 'hidden'
    job['layers'] = ['building']
    def render(active, info):
        return build_transit_artwork(job, document, document_epoch=0, revision=1,
                                     draft=True, session=active, diagnostics=info)
    render(session, {})
    job['ui']['style_overrides'][job['style']]['buildings']['default'] = '#1266AB'
    info = {}
    assert render(session, info).digest == render(DocumentSession(document), {}).digest
    assert info['reusedLayout'] and not info['reusedBase']
    job['opts']['station_name_size'] += 4
    assert render(session, info).digest == render(DocumentSession(document), {}).digest
    assert info['reusedBase'] and not info['reusedLayout']
    job['ui']['style_overrides'][job['style']]['poi_icons']['name_color'] = '#132457'
    assert render(session, info).digest == render(DocumentSession(document), {}).digest
    assert info['reusedBase'] and not info['reusedLayout']


def test_layer_changes_reuse_other_groups_and_update_export(city_job, monkeypatch):
    _, job, document, session = city_job
    build(city_job, draft=True)
    for change in ('building', 'road-color', 'watermark', 'hide-names', 'hide-poi'):
        if change == 'hide-names':
            job['ui']['road_names'] = False
        elif change == 'road-color':
            job['ui']['style_overrides'][job['style']]['labels']['road']['color'] = '#884455'
        elif change == 'hide-poi':
            job['layers'].remove('poi')
        else:
            job['layers'].append(change)
        with monkeypatch.context() as patch:
            if change != 'hide-names':
                patch.setattr('csmodernmap.labeling.LabelPipeline.run', lambda *a, **k: pytest.fail('reran labels'))
            patch.setattr('csmodernmap.layers.poi_icon_layer.POIIconLayer.render', lambda *a: pytest.fail('reran POI'))
            changed = build(city_job, draft=True)
        cold = build_city_artwork(job, document, session=DocumentSession(document), draft=True, document_epoch=0, revision=1)
        assert changed.to_dict() == cold.to_dict()
        with render_artwork(changed) as actual, render_artwork(cold) as expected:
            assert actual.tobytes() == expected.tobytes()


@pytest.mark.parametrize('background', ['#e7f0e4', '#101010'])
def test_background_reuses_layout_and_updates_frozen_export(city_job, monkeypatch, background):
    _, job, document, session = city_job
    job['_preview_binding'] = {'sceneId': 'old', 'revision': 1}
    first = build(city_job, draft=True)
    before = first.to_dict()
    job['ui']['style_overrides'][job['style']]['canvas']['background'] = background
    job['_preview_binding'] = {'sceneId': 'new', 'revision': 2}
    diagnostics = {}
    with monkeypatch.context() as patch:
        patch.setattr('csmodernmap.labeling.LabelPipeline.run',
                      lambda *a, **k: pytest.fail('Background change reran layout'))
        updated = build_city_artwork(job, document, session=session, draft=True,
            document_epoch=0, revision=2, diagnostics=diagnostics)
    actual = updated.to_dict()
    assert diagnostics['reusedLayout'] and diagnostics['timings']['layout'] == 0
    assert actual['objects'] == before['objects'] and actual['resources'] == before['resources']
    assert actual['previewBase']['sceneId'] == 'new' and actual['previewBase']['revision'] == 2
    assert actual['revision'] == actual['previewBase']['layoutRevision'] == 2
    assert actual['background'] == background+'ff' and actual['styleId'] != before['styleId']
    assert first.to_dict() == before
    # 新的正式底图配方采用新颜色，旧版仍保留原配方；与独立重建逐像素相同。
    cold = build_city_artwork(job, document, session=DocumentSession(document), draft=True,
                             document_epoch=0, revision=2)
    assert cold.to_dict() == actual
    with render_artwork(updated) as output, render_artwork(cold) as expected:
        assert ImageChops.difference(output.convert('RGB'), expected.convert('RGB')).getbbox() is None
        assert output.getpixel((0, 0))[:3] == tuple(bytes.fromhex(background[1:]))


@pytest.mark.parametrize('change', ['font-size', 'rotation'])
def test_layout_dependencies_invalidate_reuse(city_job, change):
    _, job, _, _ = city_job
    first = build(city_job, draft=True)
    style = job['ui']['style_overrides'][job['style']]
    if change == 'font-size':
        style['labels']['road']['size'] += 12
    else:
        job['ui']['rotation'] = 90
    diagnostics = {}
    changed = build(city_job, draft=True, diagnostics=diagnostics)
    assert not diagnostics['reusedLayout']
    assert diagnostics['timings']['layout'] > 0
    assert changed.digest != first.digest


def test_font_candidates_reused_only_within_render(monkeypatch, tmp_path):
    calls = []
    chosen = [tmp_path/'one.ttf']
    def find(name, bold):
        calls.append((name, bold))
        return list(chosen)
    monkeypatch.setattr(fonts, '_find_candidate_paths', find)
    monkeypatch.setattr(fonts, 'font_covers', lambda path, text: True)
    with fonts.font_resolution_scope():
        for text in ('Road', '中文', 'Station'):
            assert fonts.resolve_text_font('Test', text=text) == chosen[0]
    assert len(calls) == 1
    chosen[:] = [tmp_path/'new.ttf']
    with fonts.font_resolution_scope():
        assert fonts.resolve_text_font('Test', text='Road') == chosen[0]
    assert len(calls) == 2


def test_poi_mapping_cache_follows_content_not_temporary_filename(city_job, monkeypatch):
    _, _, _, session = city_job
    mapping = {'entry': {'display': True}}
    calls = []
    monkeypatch.setattr('csmodernmap.services.poi.direct_mapping.load_mapping', lambda path: deepcopy(mapping))
    def produce(*a, **k):
        calls.append(k['override_path'])
        return deepcopy(mapping)
    monkeypatch.setattr('csmodernmap.services.poi.service.produce', produce)
    first = session.poi_catalog(override_path='first.json')
    assert session.poi_catalog(override_path='second.json') is first
    mapping['entry']['display'] = False
    changed = session.poi_catalog(override_path='second.json')
    assert changed != first and calls == ['first.json', 'second.json']


def test_transit_session_reuse_preserves_selection_and_station_identity(transit_job):
    from csmodernmap.transit_context import build_transit_context
    from csmodernmap.gui_render_worker import _load_ui_style
    job, _, session = transit_job
    data = session._legacy_view()
    cfg = _load_ui_style(job['ui'], job['style']).get('transports')
    first = build_transit_context(data, cfg, ['Metro', 'Bus'], session=session)
    second = build_transit_context(data, cfg, ['Metro', 'Bus'], session=session)
    cold = build_transit_context(data, cfg, ['Metro', 'Bus'])
    assert first[0] is second[0]
    assert first[0].stops == cold[0].stops
    assert [(s.id, s.member_stop_keys) for s in first[0].stations] == [
        (s.id, s.member_stop_keys) for s in cold[0].stations]
    selected = build_transit_context(data, dict(cfg, enabled_line_ids=[2]), ['Metro', 'Bus'], session=session)
    assert [line.id for line in selected[0].lines] == [2]
    assert selected[0] is not first[0]
