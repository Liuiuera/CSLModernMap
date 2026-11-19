"""公共交通后端单位、真实框、共享占位及指定修订输出。"""
from copy import deepcopy
from dataclasses import replace
import time
import threading
import json
from pathlib import Path

import pytest
from PIL import Image

from csmodernmap.artwork import ArtworkState
from csmodernmap.gui_render_worker import _build_transit_renderer
from csmodernmap.transit_artwork_sizing import prepare_style
from test_artwork import sample
from test_transit_artwork import transit_job, build
from test_png_session_export import setup_engine
from csmodernmap.artwork_capture import ArtworkCapture
from csmodernmap.layers.poi_icon_layer import POIIconLayer, POIItem, _Occupancy
from types import SimpleNamespace
from csmodernmap.style import Style


def test_capture_precision_keeps_all_points_and_connections_with_subpixel_error():
    capture = ArtworkCapture()
    points = [(1.123456789, 2.987654321), (10.987654321, 20.123456789)]
    capture.line(points, 2, '#202830')
    capture.cap(points[0], 2, '#202830')
    assert len(capture.objects[0]['points']) == len(points)
    assert capture.objects[1]['center'] == capture.objects[0]['points'][0]
    for original, saved in zip(points, capture.objects[0]['points']):
        assert saved == pytest.approx(original, abs=.0005)


def test_pixels_are_independent_of_world_rotation_and_canvas(transit_job):
    job, document, session = transit_job
    first = None
    for dims in ((2048,1024),(4096,4096),(4096,8192)):
        document.terrain = replace(document.terrain, cell=3200)
        renderer = _build_transit_renderer(job['ui'],job['style'],job['types'],dims,opts=job['opts'])
        result = prepare_style(renderer.style, document, dims, session=session, element_scale=.75)
        if first is None: first=result
        assert result == first
    with pytest.raises(ValueError, match='retired'):
        prepare_style(renderer.style, document, dims, sizing={'unit':'reference-px'})


def test_effective_facility_owner_overrides_are_resolved_once(transit_job):
    from csmodernmap.pixel_sizes import resolve
    job, _, _ = transit_job
    data=deepcopy(job['ui']['style_overrides'][job['style']])
    data['poi_icons']['transit']={'icon_size':30}
    resolved, _=resolve(data,.5)
    assert resolved['poi_icons']['transit']['icon_size']==15
    assert data['poi_icons']['transit']['icon_size']==30


def test_shared_resource_dimensions_and_label_occupancy(transit_job):
    diagnostics = {}
    snapshot = build(transit_job, diagnostics=diagnostics)
    data = snapshot.to_dict()
    for obj in data['objects']:
        if 'textRun' not in obj:
            continue
        resource = data['resources'][obj['resource']]
        assert obj['bbox'][2]-obj['bbox'][0] == pytest.approx(resource['width']/4)
        assert obj['bbox'][3]-obj['bbox'][1] == pytest.approx(resource['height']/4)
    assert diagnostics['sizing']['inputUnit'] == 'pixel-v2'
    assert diagnostics['names']
    for name in diagnostics['names']:
        assert name['overlap'] == min(name['scores'])
    assert any(row['reason'] == 'placed' for row in diagnostics['badges'])


def test_pinned_revision_can_complete_after_current_and_releases_references():
    state = ArtworkState(1)
    state.request()
    pinned = state.pin_export()
    state.request()
    assert state.accept(sample(revision=2))
    assert not state.accept(sample(revision=1))
    assert pinned.result().identity == (1, 1)
    assert state.freeze().identity == (1, 2)
    assert not state.is_pinned((1, 1))
    state.request()
    failed = state.pin_export()
    state.fail((1, 3), 'layout failed')
    with pytest.raises(RuntimeError, match='layout failed'):
        failed.result()
    state.request()
    cancelled = state.pin_export()
    state.set_document(2)
    assert cancelled.cancelled()


@pytest.mark.parametrize('avoid', [True, False])
def test_all_candidates_collide_name_survives_and_registers_occupancy(avoid):
    layer = POIIconLayer()
    capture = ArtworkCapture()
    image = Image.new('RGBA', (600, 600))
    layer.ctx = SimpleNamespace(image=image, style=Style({'poi_icons': {}}),
        shared={'artwork_capture': capture}, zoom=1, final_zoom=1)
    layer._poi_cfg = {'name_size':14,'name_color':'#202830','name_stroke_width':1}
    layer._raster_scale = layer.render_scale = layer._collision_scale = 1
    layer._transit_view_scale = 1
    layer._static_png_profile = False
    layer._active_profile = {'target': 'transit_png'}
    layer._overlap_avoidance = avoid
    layer._occupancy = _Occupancy()
    layer._occupancy.add((0, 0, 600, 600))
    item = POIItem(300, 300, '', '中文 AV test', 'station', 100, 'station', poi_id='test')
    layer._draw_icon_name(item, 0, 290, 290, 20, 20)
    diagnostic = capture.diagnostics['names'][0]
    assert len(capture.objects) == 1
    assert diagnostic['box'] == diagnostic['candidates'][0]
    assert diagnostic['overlap'] > 0
    assert diagnostic['avoid'] is avoid
    assert len({id(box) for boxes in layer._occupancy.cells.values() for box in boxes}) == 2


def pump(engine, predicate):
    deadline = time.monotonic()+10
    while not predicate():
        engine.scheduler.pump()
        if time.monotonic() >= deadline:
            raise AssertionError('Artwork task did not finish')
        time.sleep(.01)


def test_engine_pins_job_before_later_edits_and_exports_without_webview(tmp_path, monkeypatch, transit_job):
    engine, _ = setup_engine(tmp_path)
    job, document, session = transit_job
    engine.backend.document, engine.backend.session = document, session
    entered, release = threading.Event(), threading.Event()
    revisions = []
    def layout(job, document, **kwargs):
        revision = kwargs['revision']
        revisions.append((revision, job['ui']['rotation']))
        if revision == 1:
            entered.set()
            assert release.wait(5)
        return sample(epoch=0, revision=revision)
    monkeypatch.setattr('csmodernmap.transit_artwork.build_transit_artwork', layout)
    try:
        assert engine.submit_artwork(job) == (0, 1)
        assert entered.wait(5)
        output = engine.export_artwork(tmp_path/'pinned.png', long_edge=512)
        job['ui']['rotation'] = 90
        assert engine.submit_artwork(job) == (0, 2)
        job['ui']['rotation'] = 180
        release.set()
        pump(engine, lambda: not engine._artwork_active)
        assert output.result() == str(tmp_path/'pinned.png')
        with Image.open(output.result()) as image:
            assert image.size == (512, 256)
        assert revisions == [(1, 0), (2, 90)]
        assert engine.artwork_state.freeze().identity == (0, 2)
        assert not engine.artwork_state._exports
    finally:
        release.set()
        engine.backend.close()
        engine._preview_files.cleanup()


@pytest.mark.parametrize('action', ['failure', 'cancel', 'document'])
def test_engine_failure_and_cancellation_do_not_replace_target(tmp_path, monkeypatch, transit_job, action):
    engine, _ = setup_engine(tmp_path)
    job, document, session = transit_job
    engine.backend.document, engine.backend.session = document, session
    entered, release = threading.Event(), threading.Event()
    def layout(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        if action == 'failure':
            raise ValueError('font resource unavailable')
        return sample(epoch=0)
    monkeypatch.setattr('csmodernmap.transit_artwork.build_transit_artwork', layout)
    target = tmp_path/'existing.png'
    target.write_bytes(b'keep original')
    try:
        engine.submit_artwork(job)
        assert entered.wait(5)
        output = engine.export_artwork(target)
        if action != 'failure':
            engine.cancel_artwork()
        if action == 'document':
            engine._document_epoch = 1
            engine.artwork_state.set_document(1)
        release.set()
        pump(engine, lambda: not engine._artwork_active)
        if action == 'failure':
            with pytest.raises(RuntimeError, match='font resource unavailable'):
                output.result()
        else:
            assert output.cancelled()
        assert target.read_bytes() == b'keep original'
        assert not list(tmp_path.glob('.csl-artwork-*'))
    finally:
        release.set()
        engine.backend.close()
        engine._preview_files.cleanup()


def test_pending_preview_coalescing_preserves_exported_revision(tmp_path, monkeypatch, transit_job):
    engine, _ = setup_engine(tmp_path)
    job, document, session = transit_job
    engine.backend.document, engine.backend.session = document, session
    entered, release = threading.Event(), threading.Event()
    revisions = []
    def layout(*args, **kwargs):
        revision = kwargs['revision']
        revisions.append(revision)
        if revision == 1:
            entered.set()
            assert release.wait(5)
        return sample(epoch=0, revision=revision)
    monkeypatch.setattr('csmodernmap.transit_artwork.build_transit_artwork', layout)
    try:
        engine.submit_artwork(job)
        assert entered.wait(5)
        engine.submit_artwork(job)
        output = engine.export_artwork(tmp_path/'revision-2.png')
        engine.submit_artwork(job)
        engine.submit_artwork(job)
        assert [task[1][1] for task in engine._artwork_jobs if task[0]=='layout'] == [2, 4]
        release.set()
        pump(engine, lambda: not engine._artwork_active)
        assert output.result() == str(tmp_path/'revision-2.png')
        assert revisions == [1, 2, 4]
        assert engine.artwork_state.freeze().identity == (0, 4)
    finally:
        release.set()
        engine.backend.close()
        engine._preview_files.cleanup()


@pytest.mark.parametrize('exists', [True, False])
def test_engine_freezes_mapping_contents_and_absence(tmp_path, monkeypatch, transit_job, exists):
    from csmodernmap.services.poi.direct_mapping import load_mapping
    engine, _ = setup_engine(tmp_path)
    job, document, session = transit_job
    engine.backend.document, engine.backend.session = document, session
    entry = deepcopy(next(iter(load_mapping(defaults=True).values())))
    key = f"{entry['game']}::{entry['id']}"
    original = entry['display']
    path = tmp_path/'poi_direct_mapping.json'
    def write(display):
        entry['display'] = display
        path.write_text(json.dumps({'schema_version': 3, 'entries': [entry]}), encoding='utf-8')
    if exists:
        write(not original)
    job['ui']['poi_mapping_override'] = str(path)
    entered, release = threading.Event(), threading.Event()
    observed = []
    def layout(job, document, **kwargs):
        entered.set()
        assert release.wait(5)
        frozen_path = Path(job['ui']['poi_mapping_override'])
        observed.append((frozen_path, load_mapping(frozen_path)[key]['display']))
        return sample(epoch=0, revision=kwargs['revision'])
    monkeypatch.setattr('csmodernmap.transit_artwork.build_transit_artwork', layout)
    try:
        engine.submit_artwork(job)
        assert entered.wait(5)
        write(original if exists else not original)
        release.set()
        pump(engine, lambda: not engine._artwork_active)
        assert engine.artwork_state.freeze().identity == (0, 1)
        assert observed[0][1] == (not original if exists else original)
        assert not observed[0][0].exists()
    finally:
        release.set()
        engine.backend.close()
        engine._preview_files.cleanup()


def test_real_engine_job_exports_frozen_rotation_and_keeps_input_unchanged(tmp_path, transit_job):
    engine, _ = setup_engine(tmp_path)
    job, document, session = transit_job
    engine.backend.document, engine.backend.session = document, session
    job['region'] = (-6500, -2500, 6500, 4500)
    job['ui']['rotation'] = 90
    before = deepcopy(job)
    try:
        engine.submit_artwork(job)
        output = engine.export_artwork(tmp_path/'real.png', long_edge=512, aa=2)
        pump(engine, lambda: not engine._artwork_active)
        assert job == before
        assert engine.artwork_state.freeze().to_dict()['job']['derive_params']['rotation'] == 90
        with Image.open(output.result()) as image:
            assert image.size == (512, 276)
        assert not list(tmp_path.glob('.csl-artwork-*'))
    finally:
        engine.backend.close()
        engine._preview_files.cleanup()
