"""城市作品的文字、POI、单位和冻结导出。"""
from copy import deepcopy
from dataclasses import replace
import time

import pytest
from PIL import Image

from csmodernmap.artwork import render_artwork
from csmodernmap.city_artwork import build_city_artwork
from csmodernmap.city_artwork_sizing import prepare_style
from csmodernmap.cmm import DocumentSession
from csmodernmap.gui_render_worker import _build_normal_renderer
from csmodernmap.ui_contracts import ContextMode
from test_png_export_labels import named_document
from test_png_session_export import setup_engine
from test_surface_model import _document
from test_transit_artwork import transit_job


@pytest.fixture
def city_job(tmp_path):
    engine, _ = setup_engine(tmp_path)
    backend = engine.backend
    document = named_document('cs1')
    document.terrain = replace(_document().terrain, cell=5760)
    backend.document, backend.session = document, DocumentSession(document)
    job, _, _ = engine._capture_output_snapshot(ContextMode.NORMAL, 'PNG', {'output': {'size': 1024}})
    job['layers'] = ['poi', 'label']
    job['region'] = (-8640, -8640, 8640, 8640)
    job['ui']['road_names'] = job['ui']['district_names'] = True
    job['ui']['poi_visibility_snapshot'] = {
        'icons': dict.fromkeys(('transit', 'facility', 'ordinary', 'area'), True),
        'names': dict.fromkeys(('transit', 'facility', 'ordinary', 'area'), True)}
    style = backend.current_style_data()
    style['labels']['road']['size'] = 40
    style['labels']['district']['size'] = 60
    style['poi_icons']['name_size'] = 40
    style['watermark']['content']['render_time'] = False
    job['ui']['style_overrides'][job['style']] = style
    try:
        yield engine, job, document, backend.session
    finally:
        backend.close()
        engine._preview_files.cleanup()


def build(data, **kwargs):
    _, job, document, session = data
    return build_city_artwork(job, document, session=session,
        document_epoch=0, revision=1, **kwargs)


def test_city_captures_road_paths_districts_and_poi_without_independent_park_text(city_job):
    _, job, _, _ = city_job
    before = deepcopy(job)
    diagnostics = {}
    snapshot = build(city_job, diagnostics=diagnostics)
    assert job == before
    objects = snapshot.to_dict()['objects']
    names = {item['textRun']['text'] for item in objects if 'textRun' in item}
    assert {'Named Road', 'Named District', 'Named Hospital', 'Named Park'} <= names
    labels = [item for item in objects if item['id'].startswith('label:')]
    assert {item['textRun']['text'] for item in labels} == {'Named Road', 'Named District'}
    assert any('path' in item.get('textRun', {}) for item in labels)
    assert all(item['textRun']['fontId'] for item in labels)
    assert 'PARK_NAME' not in diagnostics['baseLabels']['counts_by_kind']
    assert render_artwork(snapshot).size == (1024, 1024)


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
def test_city_rectangles_use_frozen_transform_and_equal_scale_output(city_job, rotation):
    _, job, _, _ = city_job
    job['region'] = (-8000, -6000, 8000, 7000)
    job['ui']['rotation'] = rotation
    snapshot = build(city_job)
    payload = snapshot.to_dict()
    edge = max(payload['canvas'][key] for key in ('width', 'height'))
    digest = snapshot.digest
    for factor in (.5, 1, 2):
        image = render_artwork(snapshot, scale=factor)
        assert max(image.size) == round(edge*factor)
        image.close()
    assert snapshot.digest == digest
    assert payload['job']['derive_params']['rotation'] == rotation
    assert set(payload['job']['layers']) == {'poi', 'label'}


def test_city_name_visibility_is_owned_by_original_layer_and_poi_switches(city_job):
    _, job, _, _ = city_job
    job['ui']['district_names'] = job['ui']['road_names'] = False
    job['ui']['poi_visibility_snapshot']['names']['area'] = False
    payload = build(city_job).to_dict()
    names = {item['textRun']['text'] for item in payload['objects'] if 'textRun' in item}
    assert 'Named Hospital' in names
    assert not {'Named District', 'Named Road', 'Named Park'} & names


def test_city_pixels_are_independent_of_bounds_and_resolution(city_job):
    _, job, document, _ = city_job
    first = None
    for edge, cell in ((2048, 5760), (4096, 4800), (8192, 3200)):
        document.terrain = replace(document.terrain, cell=cell)
        renderer = _build_normal_renderer(job['ui'], job['style'], job['layers'], edge, False, 'normal')
        actual = prepare_style(renderer.style, document, (edge, edge), element_scale=.75)
        if first is None: first = actual
        assert actual == first
    with pytest.raises(ValueError, match='retired'):
        prepare_style(renderer.style, document, (4096, 4096), sizing={'unit':'reference-px'})


def test_city_engine_accepts_normal_jobs_and_exports_without_viewer(city_job, tmp_path):
    engine, job, _, _ = city_job
    identity = engine.submit_artwork(job)
    output = engine.export_artwork(str(tmp_path/'city.png'), long_edge=512)
    job['ui']['rotation'] = 90
    deadline = time.monotonic()+20
    while engine._artwork_active and time.monotonic() < deadline:
        engine.scheduler.pump()
        time.sleep(.01)
    engine.scheduler.pump()
    assert output.result(timeout=1) == str(tmp_path/'city.png')
    assert engine.artwork_state.freeze().identity == identity
    assert engine.artwork_state.freeze().to_dict()['job']['kind'] == 'normal'
    with Image.open(output.result()) as image:
        assert image.size == (512, 512)


def test_city_export_pins_accepted_revision_during_later_edit(city_job, tmp_path, monkeypatch):
    import threading
    from test_transit_artwork_backend import pump
    engine, job, _, _ = city_job
    entered, release = threading.Event(), threading.Event()
    def layout(*args, **kwargs):
        if kwargs['revision'] == 1:
            entered.set()
            assert release.wait(10)
        return build_city_artwork(*args, **kwargs)
    monkeypatch.setattr('csmodernmap.city_artwork.build_city_artwork', layout)
    try:
        engine.submit_artwork(job)
        assert entered.wait(10)
        output = engine.export_artwork(tmp_path/'accepted.png', long_edge=512)
        job['region'] = (-8000, -4000, 8000, 4000)
        engine.submit_artwork(job)
        release.set()
        pump(engine, lambda: not engine._artwork_active)
        with Image.open(output.result()) as image:
            assert image.size == (512, 512)
        latest = engine.artwork_state.freeze()
        assert latest.identity == (0, 2)
        assert latest.to_dict()['canvas']['width'] != latest.to_dict()['canvas']['height']
    finally:
        release.set()


def test_city_ignores_observation_snapshot_and_rejects_deferred_region(city_job):
    _, job, _, _ = city_job
    first = build(city_job)
    job['ui']['preview_sizing'] = {'viewport': [1, 1]}
    with pytest.raises(ValueError, match='retired'):
        build(city_job)
    job['ui'].pop('preview_sizing')
    job['region'] = None
    with pytest.raises(ValueError, match='region'):
        build(city_job)


def test_city_keeps_standard_transit_symbols_names_and_badges(transit_job):
    job, document, session = transit_job
    job['kind'] = 'normal'
    job['layers'] = ['metro']
    job['ui']['transit_types'] = ['Metro', 'Bus']
    job['ui']['map_transport'].update(types=['Metro', 'Bus'], stations=True,
        station_name_placement='station', line_label_enabled=True)
    cfg = job['ui']['style_overrides'].setdefault(job['style'], {})
    cfg['transports'].update({'station': {'enabled': True, 'diameter': 16, 'min_zoom': 0,
        'names': {'enabled': True, 'size': 16}}, 'label': {'enabled': True, 'size': 16, 'min_zoom': 0}})
    payload = build_city_artwork(job, document, session=session,
        document_epoch=1, revision=1).to_dict()
    assert any(obj['kind'] == 'polyline' for obj in payload['objects'])
    assert any(obj['id'].startswith('station:') and ':name:' not in obj['id']
               for obj in payload['objects'])
    assert any(':name:' in obj['id'] for obj in payload['objects'])
    assert any(obj['id'].startswith('line-label:') for obj in payload['objects'])
    for obj in payload['objects']:
        if 'textRun' in obj:
            resource = payload['resources'][obj['resource']]
            assert resource['width']/4 == pytest.approx(obj['bbox'][2]-obj['bbox'][0])


def test_station_capture_ignores_legacy_aspect_and_uses_outer_diameter():
    from types import SimpleNamespace
    from csmodernmap.artwork_capture import ArtworkCapture
    from csmodernmap.layers.transport_layer import TransportLayer
    from test_station_poi_adapter import context
    ctx = context()
    layer = TransportLayer()
    layer.ctx, layer.render_scale, layer._cfg = ctx, 1, {}
    layer._artwork = ArtworkCapture()
    cfg = {'diameter':12,'border_width':1}
    info = (100, 100, 6, 2, ['#204080', '#804020'], True, 0, 1.5,
            'Metro', True, 'auto', SimpleNamespace(id='transfer'))
    layer._paint_station_symbols([info], cfg)
    assert len(layer._artwork.objects) == 1
    assert layer._artwork.objects[0]['bbox'] == [94,94,106,106]
    assert layer._artwork.resources
