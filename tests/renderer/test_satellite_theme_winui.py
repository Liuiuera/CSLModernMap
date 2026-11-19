"""WinUI satellite theme, persisted controls and imagery-backed editor previews."""
import io
from types import SimpleNamespace
from urllib.parse import urlparse
from urllib.request import url2pathname

import pytest
from PIL import Image, ImageChops

from csmodernmap._paths import resource_dir
from csmodernmap.cmm.session import DocumentSession
from csmodernmap.guir_backend import GuirBackend, THEME_CHOICES
from csmodernmap.style import load_style
from csmodernmap.winui_engine import EngineServer
from theme_editor_fixture import theme_editor_document


def backend_at(path):
    scheduler = SimpleNamespace(call_soon=lambda *_: 1, call_later=lambda *_: 1, cancel=lambda *_: None)
    return GuirBackend(scheduler, settings_dir=path)


def test_satellite_theme_edit_save_reopen_and_text_halo(tmp_path):
    backend = backend_at(tmp_path)
    server = EngineServer(io.StringIO(), io.StringIO(), backend)
    try:
        assert ('gui.theme.choice.satellite', 'satellite') in THEME_CHOICES
        backend.theme_stem = 'satellite'
        before_layers = backend.layer_visibility.copy()
        draft = server.dispatch('getThemeDraft', {})
        data = draft['data']
        assert data['roads']['opacity'] == .60
        assert data['labels']['road']['stroke'] == '#25231FBF'
        assert backend.layer_visibility == before_layers
        data['roads']['opacity'] = .42
        data['labels']['road']['size'] = 24
        sample_args = {'data': data, 'type': 'road', 'text': 'Satellite Avenue', 'documentEpoch': 0}
        sharp = server.dispatch('renderThemeTextSample', sample_args)
        data['labels']['road']['stroke_blur'] = 3
        soft = server.dispatch('renderThemeTextSample', sample_args)
        assert soft['strokeBlur'] == 3
        with Image.open(url2pathname(urlparse(sharp['url']).path)) as a, Image.open(url2pathname(urlparse(soft['url']).path)) as b:
            assert b.width > a.width and b.height > a.height
            assert b.getbbox() and b.getchannel('A').getextrema()[1] == 255
        result = server.dispatch('commitThemeDraft', {'data': data, 'theme': 'satellite',
            'persist': True, 'documentEpoch': 0})
        assert result['applied'] and result['saved']
        assert backend.layer_visibility == before_layers
        reopened = backend_at(tmp_path)
        try:
            assert reopened.theme_stem == result['theme']
            assert reopened.current_style_data()['roads']['opacity'] == .42
            assert reopened.current_style_data()['labels']['road']['stroke_blur'] == 3
        finally:
            reopened.close()
    finally:
        backend.close()
        server._preview_files.cleanup()


@pytest.mark.parametrize('scene', ['roads', 'labels', 'overall'])
def test_theme_preview_keeps_satellite_background_and_overlay(tmp_path, scene):
    backend = backend_at(tmp_path)
    doc = theme_editor_document()
    source = Image.new('RGB', (64,64), '#203040')
    stream = io.BytesIO()
    source.save(stream, format='PNG')
    doc._satellite_png = stream.getvalue()
    doc._satellite_info = {'world_bounds_xz': [0,0,1280,1280], 'output_size': 64}
    backend.document = doc
    backend.session = DocumentSession(doc)
    backend.apply_satellite_recommendations()
    backend._style_editor_regions['roads'] = (0,0,1280,1280)
    backend._style_editor_regions['overall'] = (0,0,1280,1280)
    data = load_style(str(resource_dir('styles') / 'satellite.yaml')).data
    try:
        before = backend.layer_visibility.copy()
        image = backend.style_preview_render(data, 256, scene)
        assert image.getpixel((250,250))[:3] == (32,48,64)
        assert backend.layer_visibility == before
        backend.layer_visibility['terrain'] = True
        covered = backend.style_preview_render(data, 256, scene)
        assert covered.getpixel((250,250))[:3] != (32,48,64)
        backend.layer_visibility['terrain'] = False
        data['roads']['opacity'] = 0
        hidden = backend.style_preview_render(data, 256, scene)
        assert ImageChops.difference(image.convert('RGB'), hidden.convert('RGB')).getbbox()
    finally:
        backend.close()
