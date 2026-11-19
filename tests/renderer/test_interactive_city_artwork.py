"""复用底图的草稿与绑定正式绘制配方。"""
from copy import deepcopy
from dataclasses import replace

from PIL import ImageChops
import pytest

from csmodernmap.artwork import ArtworkSnapshot, render_artwork
from csmodernmap.city_artwork import build_city_artwork
from test_city_artwork import city_job, build
from test_transit_artwork_backend import pump


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
def test_draft_skips_base_and_exports_same_layout(city_job, monkeypatch, rotation):
    _, job, _, _ = city_job
    job['ui']['rotation'] = rotation
    job['region'] = (-8000, -6000, 8000, 7000)
    full = build(city_job)
    from csmodernmap.renderer import Renderer
    original = Renderer.render
    monkeypatch.setattr(Renderer, 'render', lambda *a, **kw: pytest.fail('Draft rebuilt its base'))
    diagnostics = {}
    draft = build(city_job, draft=True, diagnostics=diagnostics)
    assert diagnostics['baseReuse'] == {'enabled': True, 'renders': 0, 'transferredResources': 0}
    formal = [obj for obj in full.to_dict()['objects'] if not obj['id'].startswith('base:')]
    actual = draft.to_dict()['objects']
    assert len(actual) == len(formal)
    for a, b in zip(actual, formal):
        assert {k: v for k, v in a.items() if k != 'resource'} == {k: v for k, v in b.items() if k != 'resource'}
    assert sum(r['width']*r['height'] for r in draft.to_dict()['resources'].values()) < sum(
        r['width']*r['height'] for r in full.to_dict()['resources'].values())
    monkeypatch.setattr(Renderer, 'render', original)
    with render_artwork(full) as expected, render_artwork(draft) as output:
        assert ImageChops.difference(expected.convert('RGB'), output.convert('RGB')).getbbox() is None
    with pytest.raises(ValueError, match='bound document'):
        render_artwork(ArtworkSnapshot.from_dict(draft.to_dict()))


def test_draft_export_uses_high_quality_resources_without_relayout(city_job, monkeypatch):
    draft = build(city_job, draft=True)
    before = deepcopy(draft.to_dict())
    monkeypatch.setattr('csmodernmap.labeling.LabelPipeline.run',
                        lambda *a, **kw: pytest.fail('Export reran layout'))
    with render_artwork(draft, scale=2, aa=2) as output:
        assert output.size == (2048, 2048)
    assert draft.to_dict() == before
    job = city_job[1]
    job['ui']['style_overrides'][job['style']]['canvas']['background'] = '#aa0000'
    with render_artwork(draft) as frozen:
        assert frozen.getpixel((0, 0))[:3] != (170, 0, 0)


def test_editor_protocol_waits_for_required_sources_and_tracks_presentation(city_job, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from csmodernmap.city_artwork import required_sources
    from csmodernmap.ui_contracts import ContextMode
    engine, _, _, _ = city_job
    events = []
    engine.event = lambda name, payload: events.append((name, payload))
    monkeypatch.setattr(engine, '_city_base_needs_rebuild', lambda *args: False)
    monkeypatch.setattr(engine.backend, 'compile_preview_scene', lambda *args, **kwargs: True)
    job, _, _ = engine._capture_output_snapshot(ContextMode.NORMAL, 'PNG', {'output': {'size': 1024}})
    sources = {name: 'ready' for name in required_sources(job['layers'], 'shade')}
    bundle = SimpleNamespace(scene_id='city', revision=1, manifest={'sources': dict(sources)})
    bundle.manifest['sources'].pop('water')
    engine._artwork_scene = (0, bundle)
    answer = engine.dispatch('beginCityArtwork', {'documentEpoch': 0, 'longEdge': 1024})
    assert answer == {'started': False, 'waiting': True}
    assert not engine._artwork_active
    bundle.manifest['sources']['water'] = 'ready'
    engine._scene_ready(bundle, 'http://localhost/manifest.json')
    pump(engine, lambda: not engine._artwork_active)
    snapshot = engine.artwork_state.freeze()
    assert snapshot._render_plan is not None
    assert snapshot.to_dict()['previewBase']['sceneId'] == 'city'
    assert any(name == 'artworkReady' for name, _ in events)
    assert engine.dispatch('artworkPresented', {'documentEpoch': 0, 'artworkRevision': 1,
        'performance': {'receiveToPresentedMs': 12}})['accepted']
    assert engine.artwork_diagnostics['presentation']['requestToPresentedSeconds'] > 0
    target = tmp_path/'protocol-artwork.png'
    before = engine.dispatch('getCityArtworkState', {'documentEpoch': 0})
    assert engine.dispatch('exportCityArtwork', {'documentEpoch': 0, 'requestId': 'draft-export',
        'expectedRevision': before['revision'], 'target': str(target), 'longEdge': 512})['started']
    pump(engine, lambda: not engine._artwork_active)
    assert target.exists()
    assert ('artworkExported', {'documentEpoch': 0, 'requestId': 'draft-export', 'context': 'normal',
                                'format': 'PNG', 'path': str(target), 'target': str(target)}) in events
    assert engine.dispatch('getCityArtworkState', {'documentEpoch': 0}) == before
    bundle.revision = 2
    engine._scene_ready(bundle, 'http://localhost/manifest.json')
    pump(engine, lambda: not engine._artwork_active)
    assert engine.artwork_state.freeze().identity == (0, 2)
    assert engine.artwork_state.freeze().to_dict()['previewBase']['revision'] == 2
    assert engine.dispatch('leaveCityArtwork', {'documentEpoch': 0}) == {'left': True}


def test_color_edit_and_queued_export_bind_different_revisions(city_job, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from csmodernmap.city_artwork import required_sources
    from csmodernmap.ui_contracts import ContextMode
    engine, _, _, _ = city_job
    monkeypatch.setattr(engine, '_city_base_needs_rebuild', lambda *args: False)
    job, _, _ = engine._capture_output_snapshot(ContextMode.NORMAL, 'PNG', {'output': {'size': 1024}})
    engine._artwork_scene = (0, SimpleNamespace(scene_id='city', revision=1,
        manifest={'sources': {source: 'ready' for source in required_sources(job['layers'], 'shade')}}))
    engine.dispatch('beginCityArtwork', {'documentEpoch': 0, 'longEdge': 1024})
    first = engine.export_artwork(tmp_path/'first.png', long_edge=512)
    style = engine.backend.current_style_data()
    style['canvas']['background'] = '#112233'
    engine.backend.style_overrides[engine.backend.theme_stem] = style
    engine.dispatch('refreshCityArtwork', {'documentEpoch': 0})
    pump(engine, lambda: not engine._artwork_active)
    assert first.result().endswith('first.png')
    assert engine.artwork_state.freeze().identity == (0, 2)
    assert engine.artwork_state.freeze().to_dict()['previewBase']['theme']['road']['background'] == '#112233'
