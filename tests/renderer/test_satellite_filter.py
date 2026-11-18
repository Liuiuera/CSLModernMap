"""Satellite-only imaging, shared settings and immutable preview resources."""
import io

import numpy as np
import pytest
from PIL import Image

from csmodernmap.satellite_filter import PRESETS, apply_filter, filter_key, satellite_image
from csmodernmap.theme_contract import normalize_theme, validate_theme_values
from csmodernmap.theme_metadata import describe_theme
from csmodernmap.ribbon_schema import LAYER_FIELDS
from test_cmm11_archive import members, package
from csmodernmap.cmm.loader import load_document


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


def test_shared_settings_and_validation():
    theme = normalize_theme({})
    fields = describe_theme(theme)
    assert theme['satellite'] == {'preset': 'original', 'intensity': 100}
    assert LAYER_FIELDS['satellite'] == ('satellite.preset', 'satellite.intensity')
    assert fields['satellite.preset']['kind'] == 'choice'
    assert {c['value'] for c in fields['satellite.preset']['choices']} == set(PRESETS)
    assert fields['satellite.intensity']['high'] == 100
    assert fields['satellite.intensity']['supported']
    assert filter_key({'satellite': {'preset': 'combined', 'intensity': 0}}) == ('original', 0)
    for config in ({'preset': 'unknown'}, {'intensity': -1}, {'intensity': 101}, {'intensity': float('nan')}, {'intensity': True}):
        with pytest.raises(ValueError):
            validate_theme_values({'satellite': config})


def test_cache_uses_original_and_preview_png_html_share_pixels(tmp_path):
    from csmodernmap.cmm import DocumentSession
    from csmodernmap.preview_scene import PreviewSceneCompiler
    from csmodernmap.style import Style
    from csmodernmap.renderer import Renderer
    from csmodernmap.layers.satellite_layer import SatelliteLayer
    from csmodernmap.web_export_v2 import payload_from_preview_bundle

    doc = load_document(package(tmp_path/'map.cmm', members()))
    original = doc._satellite_png
    haze = Style({'satellite': {'preset': 'haze'}})
    grain = Style({'satellite': {'preset': 'grain'}})
    processed = satellite_image(doc, haze)
    assert satellite_image(doc, haze) is processed
    satellite_image(doc, grain)
    assert satellite_image(doc, haze) == processed
    assert doc._satellite_png is original
    assert satellite_image(doc, {'satellite': {'preset': 'haze', 'intensity': 0}}) is original

    session = DocumentSession(doc)
    first = PreviewSceneCompiler.compile_full(session, Style({}), 1, visibility={'satellite': True})
    second = third = None
    try:
        before = (first.root/'satellite.png').read_bytes()
        second = PreviewSceneCompiler.compile_full(session, haze, 2, visibility={'satellite': True}, previous=first)
        assert (first.root/'satellite.png').read_bytes() == before
        assert first.manifest['sourceRevisions']['satellite'] != second.manifest['sourceRevisions']['satellite']
        third = PreviewSceneCompiler.compile_full(session, Style({**haze.data, 'water': {'color': '#123456'}}), 3,
                                                 visibility={'satellite': True}, previous=second)
        assert second.manifest['sourceRevisions']['satellite'] == third.manifest['sourceRevisions']['satellite']
        payload = payload_from_preview_bundle(second, visibility={'satellite': True}, navigation_enabled=False)
        import base64
        embedded = payload['embedded']['images']['satellite.png']
        assert base64.b64decode(embedded.split(',', 1)[1]) == (second.root/'satellite.png').read_bytes()
        renderer = Renderer(haze, width=4, height=4)
        renderer.add_layer(SatelliteLayer())
        reference = load_document(package(tmp_path/'reference.cmm', {**members(), 'satellite.png': processed}))
        reference_renderer = Renderer(Style({}), width=4, height=4)
        reference_renderer.add_layer(SatelliteLayer())
        with renderer.render(doc, region=(0, 0, 4, 4)) as png, reference_renderer.render(reference, region=(0, 0, 4, 4)) as expected:
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
        assert filter_key(backend.current_style_data()) == ('combined', 65)
        assert backend.field_value('satellite.intensity') == 65
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
    finally:
        restored.close()
