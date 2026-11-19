"""Satellite-only imaging, shared settings and immutable preview resources."""
import io

import numpy as np
import pytest
from PIL import Image

from csmodernmap.satellite_filter import PRESETS, apply_filter, filter_key, image_key, satellite_image
from csmodernmap.theme_contract import normalize_theme, validate_theme_values
from csmodernmap.theme_metadata import describe_theme
from csmodernmap.ribbon_schema import LAYER_FIELDS
from test_cmm11_archive import members, package
from csmodernmap.cmm.loader import load_document
from style_fixtures import complete_style


@pytest.mark.parametrize('preset', PRESETS)
def test_imaging_preserves_alpha_size_and_is_repeatable(preset):
    rng = np.random.default_rng(12)
    with Image.fromarray(rng.integers(0, 256, (64, 64, 4), dtype=np.uint8)) as source:
        key = filter_key({'satellite': {'preset': preset}})
        with apply_filter(source, key, (0, 0, 270, 270)) as first, apply_filter(source, key, (0, 0, 270, 270)) as second:
            assert first.size == source.size
            assert first.tobytes() == second.tobytes()
            assert first.getchannel('A').tobytes() == source.getchannel('A').tobytes()
            assert (first.tobytes() == source.tobytes()) == (preset == 'original')


def test_shared_settings_and_validation(modern_style):
    theme = normalize_theme(modern_style.data)
    fields = describe_theme(theme)
    assert theme['satellite'] == {'preset': 'original', 'intensity': 100, 'quality': 'original'}
    assert LAYER_FIELDS['satellite'] == ('satellite.quality', 'satellite.preset', 'satellite.intensity')
    assert {c['value'] for c in fields['satellite.quality']['choices']} == {'smooth', 'standard', 'original'}
    assert fields['satellite.preset']['kind'] == 'choice'
    assert {c['value'] for c in fields['satellite.preset']['choices']} == set(PRESETS)
    assert fields['satellite.intensity']['high'] == 100
    assert fields['satellite.intensity']['supported']
    assert filter_key({'satellite': {'preset': 'combined', 'intensity': 0}}) == ('original', 0)
    for config in ({'quality': 'unknown'}, {'quality': 2048}, {'quality': None}, {'preset': 'unknown'}, {'intensity': -1}, {'intensity': 101}, {'intensity': float('nan')}, {'intensity': True}):
        with pytest.raises(ValueError):
            validate_theme_values(modern_style.merged({'satellite': config}).data)


@pytest.mark.parametrize('quality', ['original', 'smooth'])
def test_cache_uses_original_and_preview_png_html_share_pixels(tmp_path, monkeypatch, quality):
    from csmodernmap.cmm import DocumentSession
    from csmodernmap.preview_scene import PreviewSceneCompiler
    from csmodernmap.renderer import Renderer
    from csmodernmap.layers.satellite_layer import SatelliteLayer
    from csmodernmap.web_export_v2 import payload_from_preview_bundle
    from csmodernmap.satellite_filter import QUALITY_LIMITS

    monkeypatch.setitem(QUALITY_LIMITS, 'smooth', 2)
    doc = load_document(package(tmp_path/'map.cmm', members()))
    original = doc._satellite_png
    base = complete_style({})
    haze = base.merged({'satellite': {'preset': 'haze', 'quality': quality}})
    grain = base.merged({'satellite': {'preset': 'grain'}})
    processed = satellite_image(doc, haze)
    assert satellite_image(doc, haze) is processed
    satellite_image(doc, grain)
    assert satellite_image(doc, haze) == processed
    assert doc._satellite_png is original
    assert satellite_image(doc, {'satellite': {'preset': 'haze', 'intensity': 0}}) is original

    session = DocumentSession(doc)
    first = PreviewSceneCompiler.compile_full(session, base, 1, visibility={'satellite': True})
    second = third = None
    try:
        before = (first.root/'satellite.png').read_bytes()
        second = PreviewSceneCompiler.compile_full(session, haze, 2, visibility={'satellite': True}, previous=first)
        assert (first.root/'satellite.png').read_bytes() == before
        assert first.manifest['sourceRevisions']['satellite'] != second.manifest['sourceRevisions']['satellite']
        third = PreviewSceneCompiler.compile_full(session, haze.merged({'water': {'color': '#123456'}}), 3,
                                                 visibility={'satellite': True}, previous=second)
        assert second.manifest['sourceRevisions']['satellite'] == third.manifest['sourceRevisions']['satellite']
        payload = payload_from_preview_bundle(second, visibility={'satellite': True}, navigation_enabled=False)
        import base64
        embedded = payload['embedded']['images']['satellite.png']
        assert base64.b64decode(embedded.split(',', 1)[1]) == (second.root/'satellite.png').read_bytes()
        renderer = Renderer(haze, width=4, height=4)
        renderer.add_layer(SatelliteLayer())
        reference = load_document(package(tmp_path/'reference.cmm', {**members(), 'satellite.png': processed}))
        reference_renderer = Renderer(base, width=4, height=4)
        reference_renderer.add_layer(SatelliteLayer())
        with renderer.render(doc, region=(0, 0, 4, 4)) as png, reference_renderer.render(reference, region=(0, 0, 4, 4)) as expected:
            assert png.size == (4, 4)
            assert png.tobytes() == expected.tobytes()
        with Image.open(second.root/'satellite.png') as preview, Image.open(io.BytesIO(processed)) as expected:
            assert preview.tobytes() == expected.transpose(Image.Transpose.ROTATE_90).tobytes()
    finally:
        for bundle in (first, second, third):
            if bundle:
                bundle.close()


def test_sidebar_changes_shared_theme_and_persists(tmp_path):
    from csmodernmap.guir_backend import GuirBackend
    from csmodernmap.winui_engine import LoopScheduler
    from csmodernmap.i18n import set_lang, tr

    backend = GuirBackend(LoopScheduler(), settings_dir=tmp_path/'settings')
    try:
        backend.apply_style_override('satellite.preset', 'combined')
        backend.apply_style_override('satellite.intensity', 65)
        backend.apply_style_override('satellite.quality', 'smooth')
        assert image_key(backend.current_style_data()) == ('combined', 65, 'smooth')
        assert filter_key(backend.current_style_data()) == ('combined', 65)
        assert backend.field_value('satellite.intensity') == 65
        with pytest.raises(ValueError):
            backend.apply_style_override('satellite.quality', 'unknown')
        assert backend.field_value('satellite.quality') == 'smooth'
        set_lang('zh-CN')
        assert tr('style.satellite.preset.label') == '影像质感'
        with pytest.raises(ValueError):
            backend.apply_style_override('satellite.intensity', 101)
        assert backend.field_value('satellite.intensity') == 65
    finally:
        backend.close()
    restored = GuirBackend(LoopScheduler(), settings_dir=tmp_path/'settings')
    try:
        assert filter_key(restored.current_style_data()) == ('combined', 65)
        assert restored.field_value('satellite.intensity') == 65
        assert restored.field_value('satellite.quality') == 'smooth'
    finally:
        restored.close()


@pytest.mark.parametrize('quality,edge', [('smooth', 2048), ('standard', 4096), ('original', 8192)])
@pytest.mark.parametrize('size', [(8192, 32), (32, 8192)])
def test_quality_limits_longest_edge_preserves_alpha_and_original(quality, edge, size):
    from types import SimpleNamespace

    stream = io.BytesIO()
    with Image.new('RGBA', size, (20, 80, 120, 96)) as source:
        source.save(stream, format='PNG')
    original = stream.getvalue()
    doc = SimpleNamespace(_satellite_png=original, _satellite_filtered=None,
                          _satellite_info={'world_bounds_xz': [0, 0, 4, 4]})
    style = {'satellite': {'quality': quality}}
    result = satellite_image(doc, style)
    assert satellite_image(doc, style) is result
    with Image.open(io.BytesIO(result)) as image:
        assert max(image.size) == edge
        assert image.width / image.height == size[0] / size[1]
        assert image.getchannel('A').getextrema() == (96, 96)
    assert doc._satellite_png is original
    assert satellite_image(doc) is original


def test_quality_change_refreshes_preview_and_export_snapshot(tmp_path, monkeypatch):
    from csmodernmap.cmm import DocumentSession
    from csmodernmap.guir_backend import GuirBackend
    from csmodernmap.preview_scene import PreviewSceneCompiler
    from csmodernmap.satellite_filter import QUALITY_LIMITS
    from csmodernmap.winui_engine import EngineServer, LoopScheduler
    from csmodernmap.web_export_v2 import payload_from_preview_bundle
    import base64

    # 用小图覆盖完整预览与导出链路，实际档位尺寸由上面的测试验证。
    monkeypatch.setitem(QUALITY_LIMITS, 'smooth', 2)
    doc = load_document(package(tmp_path/'quality.cmm', members()))
    session = DocumentSession(doc)
    base = complete_style({})
    original = PreviewSceneCompiler.compile_full(session, base, 1, visibility={'satellite': True})
    reduced = None
    backend = GuirBackend(LoopScheduler(), settings_dir=tmp_path/'settings')
    backend.document = doc
    backend.session = session
    backend.file = str(tmp_path/'quality.cmm')
    server = EngineServer(io.StringIO(), io.StringIO(), backend)
    try:
        style = base.merged({'satellite': {'quality': 'smooth'}})
        reduced = PreviewSceneCompiler.compile_full(session, style, 2, visibility={'satellite': True}, previous=original)
        assert original.manifest['sourceRevisions']['satellite'] != reduced.manifest['sourceRevisions']['satellite']
        assert original.manifest['satelliteCorners'] == reduced.manifest['satelliteCorners']
        with Image.open(original.root/'satellite.png') as image:
            assert image.size == (4, 4)
        with Image.open(reduced.root/'satellite.png') as image:
            assert image.size == (2, 2)
        payload = payload_from_preview_bundle(reduced, visibility={'satellite': True}, navigation_enabled=False)
        embedded = payload['embedded']['images']['satellite.png']
        assert base64.b64decode(embedded.split(',', 1)[1]) == (reduced.root/'satellite.png').read_bytes()
        backend.apply_style_override('satellite.quality', 'smooth')
        # 显卡限制应检查处理后的底图，而不是原始影像尺寸。
        server.dispatch('setLayerVisibility', {'key': 'satellite', 'enabled': True, 'maxTextureSize': 2})
        job = backend.capture_png_export_job('normal')
        assert job['satellite_filter_key'] == ('original', 0, 'smooth')
        from pathlib import Path
        frozen = Path(job['satellite_filtered_path']).read_bytes()
        backend.apply_style_override('satellite.quality', 'original')
        assert Path(job['satellite_filtered_path']).read_bytes() == frozen
        with Image.open(io.BytesIO(frozen)) as image:
            assert image.size == (2, 2)
    finally:
        original.close()
        if reduced:
            reduced.close()
        backend.close()
        server._preview_files.cleanup()
