"""真实交通排版进入同一作品，而非网页与 PNG 各自挑选标注。"""
from copy import deepcopy
from dataclasses import replace

import pytest

from csmodernmap.artwork import render_artwork
from csmodernmap.cmm import DocumentSession, Direction, GeomKind, Route, TransitKind, TransitLine
from csmodernmap.transit_artwork import build_transit_artwork
from csmodernmap.ui_contracts import ContextMode
from test_png_session_export import setup_engine
from test_preview_export_sizing import transit_document, SIZING


@pytest.fixture
def transit_job(tmp_path):
    engine, _events = setup_engine(tmp_path)
    backend = engine.backend
    backend.document = transit_document()
    # 站点覆盖真实全城范围；原 96m 地形仅供曲面小样使用。
    backend.document.terrain = replace(backend.document.terrain,
        origin=type(backend.document.terrain.origin)(-8640, -8640), cell=5760)
    backend.document.transit_lines.append(TransitLine(2, "Bus A", TransitKind.BUS, (210, 60, 40, 255)))
    backend.document.routes.append(Route(2, 2, Direction.OUTBOUND, [1, 2, 3], False, GeomKind.STOP_DIRECT))
    for stop in backend.document.stops:
        stop.serving_line_ids.append(2)
    backend.session = DocumentSession(backend.document)
    job, _style, _layers = engine._capture_output_snapshot(ContextMode.TRANSIT, "PNG", {"output": {"size": 1024}})
    job["layers"] = []
    job["region"] = (-8640, -8640, 8640, 8640)
    job["opts"].update(station_name_placement="station", station_name_size=8, line_label_size=8,
                       line_label_enabled=True, facilities_enabled=True)
    cfg = job['ui']['style_overrides'][job['style']]
    cfg['transports']['station'].update(diameter=14, names={'enabled':True,'size':8,'gap':5,'stroke_width':1})
    cfg['transports']['transit_view']['label_size'] = 8
    try:
        yield job, backend.document, backend.session
    finally:
        backend.close()
        engine._preview_files.cleanup()


def build(data, **kwargs):
    job, document, session = data
    return build_transit_artwork(job, document, session=session,
                                 document_epoch=1, revision=1, **kwargs)


def test_final_transit_geometry_symbols_names_and_badges_are_independent_objects(transit_job):
    job, _document, _session = transit_job
    before = deepcopy(job)
    snapshot = build(transit_job)
    payload = snapshot.to_dict()
    objects = payload['objects']
    assert job == before
    assert any(item['kind'] == 'polyline' for item in objects)
    assert any(item['id'].startswith('station:') and ':name:' not in item['id'] for item in objects)
    names = [item for item in objects if ':name:' in item['id']]
    assert names and all(item['textRun']['fontId'] for item in names)
    assert all(item['textRun']['rasterScale'] == 4 for item in names)
    assert any(item['id'].startswith('line-label:') for item in objects)
    digest = snapshot.digest
    image = render_artwork(snapshot)
    assert image.size == (1024, 1024)
    assert render_artwork(snapshot, scale=.5).size == (512, 512)
    assert snapshot.digest == digest


def test_preview_window_measurements_do_not_affect_artwork(transit_job):
    job, _document, _session = transit_job
    first = build(transit_job)
    job['ui']['preview_sizing'] = deepcopy(SIZING)
    with pytest.raises(ValueError, match='retired'):
        build(transit_job)


def test_transit_draft_repeat_distance_changes_label_density(transit_job):
    job, _, _ = transit_job
    job['elementScale'] = 1
    job['opts'].update(line_ids=[1], stations=False, facilities_enabled=False)

    def badges(interval):
        job['opts']['line_label_interval'] = interval
        return [item for item in build(transit_job, draft=True).to_dict()['objects']
                if item['id'].startswith('line-label:')]

    assert len(badges(0)) >= len(badges(120)) > len(badges(1200)) == 1


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
def test_rectangular_drafts_use_shared_output_transform(transit_job, rotation):
    job, _document, _session = transit_job
    job['region'] = (-6500, -2500, 6500, 4500)
    job['ui']['rotation'] = rotation
    payload = build(transit_job).to_dict()
    canvas = payload['canvas']
    assert max(canvas['width'], canvas['height']) == 1024
    assert (canvas['width'], canvas['height']) == ((551, 1024) if rotation in (0, 180) else (1024, 551))
    assert payload['job']['derive_params']['rotation'] == rotation


@pytest.mark.parametrize('empty', ['lines', 'types'])
def test_empty_line_selection_keeps_valid_base_and_has_no_stale_transit_objects(transit_job, empty):
    job, _document, _session = transit_job
    if empty == 'lines':
        job['opts']['line_ids'] = []
    else:
        job['types'] = []
    payload = build(transit_job).to_dict()
    assert not any(item['kind'] == 'polyline' for item in payload['objects'])
    assert not any(item['id'].startswith(('station:', 'line-label:')) for item in payload['objects'])


def test_deferred_region_and_schematic_modes_are_explicit_errors(transit_job):
    job, _document, _session = transit_job
    job['region'] = None
    with pytest.raises(ValueError, match='region'):
        build(transit_job)
    job['region'] = (-8640, -8640, 8640, 8640)
    job['opts']['abstract'] = True
    with pytest.raises(ValueError, match='geographic'):
        build(transit_job)


@pytest.mark.parametrize('mode,expected', [('game', '#D23C28'), ('classic', '#3978C5'), ('soft', '#729BCD')])
def test_view_colors_match_preview_and_both_artworks_after_filtering(transit_job, mode, expected):
    import json
    from csmodernmap.city_artwork import build_city_artwork
    from csmodernmap.preview_scene import PreviewSceneCompiler
    job, document, session = transit_job
    job['opts'].update(line_color_mode=mode, line_ids=[2])
    bundle = PreviewSceneCompiler.compile_full(session, job['ui']['style_overrides'][job['style']], 1,
        options={'transit_color_mode': mode, 'transit_line_ids': [2]})
    try:
        features = json.loads((bundle.root / 'transit.geojson').read_text(encoding='utf-8'))['features']
        assert [(f['properties']['lineId'], f['properties']['color']) for f in features] == [(2, expected)]
    finally:
        bundle.close()
    job['ui']['map_transport'] = deepcopy(job['opts'])
    city = build_city_artwork({**job, 'kind': 'normal', 'layers': ['metro']}, document,
                              session=session, document_epoch=1, revision=1)
    for snapshot in (city, build(transit_job)):
        strokes = {obj['stroke'].upper() for obj in snapshot.to_dict()['objects'] if obj['kind'] == 'polyline'}
        assert expected + 'FF' in strokes
    assert document.transit_lines[1].user_color == (210, 60, 40, 255)
