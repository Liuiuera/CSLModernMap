"""CMM 1.1 archive compatibility, integrity and satellite placement."""
import gzip
import io
import json
import zipfile
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from csmodernmap.cmm.model import MapDocument, dump_json
from csmodernmap.cmm.loader import load_document
from csmodernmap.cmm.archive import read_archive
from csmodernmap.preview_terrain import PreviewTerrainCompiler
from csmodernmap.satellite_image import render_satellite, compile_satellite


def members():
    doc = MapDocument.from_dict({}).to_dict()
    doc['schema_version'] = 'cmm1.1'
    doc['satellite'] = {'world_bounds_xz': [0, 0, 4, 4]}
    pixels = np.zeros((4, 4, 3), dtype=np.uint8)
    pixels[:2,:2] = [255,0,0]
    pixels[:2,2:] = [0,255,0]
    pixels[2:,:2] = [0,0,255]
    pixels[2:,2:] = [255,255,0]
    stream = io.BytesIO()
    Image.fromarray(pixels).save(stream, format='PNG')
    return {'city.json': json.dumps(doc), 'satellite.png': stream.getvalue()}


def package(path, entries):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    return path


def test_old_gzip_and_plain_cmm_remain_readable(tmp_path):
    raw = MapDocument.from_dict({}).to_json()
    old = tmp_path / 'old.cmm.gz'
    old.write_bytes(gzip.compress(raw.encode()))
    plain = tmp_path / 'plain.cmm'
    plain.write_text(raw, encoding='utf-8')
    assert load_document(old).schema_version == 'cmm-v1'
    assert load_document(plain).schema_version == 'cmm-v1'


def test_archive_roundtrip_preserves_image_coordinates_and_edits(tmp_path):
    entries = members()
    entries['edits.json'] = '{"building_names":{"7":"学校"}}'
    path = package(tmp_path/'city.cmm', entries)
    doc = load_document(path)
    assert doc.schema_version == 'cmm1.1'
    assert doc._satellite_png == entries['satellite.png']
    assert not any(i.code == 'SCHEMA_VERSION' for i in doc.validate())
    saved = tmp_path/'saved.cmm'
    dump_json(doc, saved)
    reread = load_document(saved)
    assert reread._satellite_info == doc._satellite_info
    assert reread._archive_edits == doc._archive_edits
    assert reread._satellite_png == doc._satellite_png
    with pytest.raises(ValueError, match='preserve'):
        dump_json(doc, tmp_path/'lossy.json')


def test_city_only_archive_and_optional_unknown_member(tmp_path):
    entries = members()
    raw = json.loads(entries['city.json']); raw.pop('satellite')
    path = package(tmp_path/'city.cmm', {'city.json': json.dumps(raw), 'future.txt':'ignored'})
    assert load_document(path)._satellite_png is None


@pytest.mark.parametrize('case', ['missing_city','missing_png','missing_coords','bad_bounds','bad_png','bad_version','bad_metadata'])
def test_archive_integrity_and_optional_image_recovery(tmp_path, case):
    entries = members(); raw = json.loads(entries['city.json'])
    if case == 'missing_city': entries.pop('city.json')
    elif case == 'missing_png': entries.pop('satellite.png')
    elif case == 'missing_coords': raw.pop('satellite')
    elif case == 'bad_bounds': raw['satellite']['world_bounds_xz'] = [0,0,0,4]
    elif case == 'bad_metadata': raw['satellite'] = []
    elif case == 'bad_version': raw['schema_version'] = 'cmm9.0'
    elif case == 'bad_png': entries['satellite.png'] = b'broken'
    if 'city.json' in entries: entries['city.json'] = json.dumps(raw)
    path = package(tmp_path/'invalid.cmm', entries)
    with pytest.raises((ValueError, OSError)):
        read_archive(path)
    if case in {'missing_city', 'bad_version'}:
        with pytest.raises(ValueError):
            load_document(path)
    else:
        from csmodernmap.cmm.file_format import read_information
        original = path.read_bytes()
        recovered = load_document(path)
        assert recovered._satellite_info is None and recovered._satellite_png is None
        assert any(issue.code == 'READ_SATELLITE_SKIPPED' for issue in recovered.issues)
        notice, = read_information(recovered)
        assert notice and not notice.startswith("gui.")
        assert path.read_bytes() == original


@pytest.mark.parametrize("broken", [b"{", b"[]", b"\xff"])
def test_unreadable_edits_keep_city_and_satellite(tmp_path, broken):
    from csmodernmap.cmm.file_format import file_warnings
    entries = members()
    entries["edits.json"] = broken
    path = package(tmp_path / "edits.cmm", entries)
    original = path.read_bytes()
    document = load_document(path)
    assert document._archive_edits is None
    assert document._satellite_png == entries["satellite.png"]
    assert any("edits.json" in warning for warning in file_warnings(document))
    assert path.read_bytes() == original


def test_image_crc_failure_keeps_city_and_edits(tmp_path):
    import struct
    entries = members()
    entries["edits.json"] = '{"building_names":{"7":"School"}}'
    path = tmp_path / "bad-image-crc.cmm"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
        offset = archive.getinfo("satellite.png").header_offset
    data = bytearray(path.read_bytes())
    name_size, extra_size = struct.unpack_from("<HH", data, offset + 26)
    data[offset + 30 + name_size + extra_size] ^= 1
    path.write_bytes(data)
    recovered = load_document(path)
    assert recovered._archive_edits == {"building_names": {"7": "School"}}
    assert recovered._satellite_info is None
    assert any("CRC" in issue.message for issue in recovered.issues)
    with pytest.raises(ValueError):
        read_archive(path)


def test_duplicate_and_crc_corruption_rejected(tmp_path):
    path = package(tmp_path/'duplicate.cmm', members())
    with pytest.warns(UserWarning):
        with zipfile.ZipFile(path, 'a') as archive:
            archive.writestr('city.json', '{}')
    with pytest.raises(ValueError, match='Duplicate'):
        read_archive(path)
    path = package(tmp_path/'crc.cmm', members())
    data = bytearray(path.read_bytes()); data[50] ^= 255; path.write_bytes(data)
    with pytest.raises(ValueError):
        read_archive(path)


@pytest.mark.parametrize('case', ['rectangular', 'oversize', 'target_mismatch'])
def test_composite_dimensions_rejected(tmp_path, case):
    entries = members()
    if case == 'target_mismatch':
        raw = json.loads(entries['city.json'])
        raw['satellite']['output_size'] = 4096
        entries['city.json'] = json.dumps(raw)
    else:
        stream = io.BytesIO()
        with Image.new('RGB', (8, 4) if case == 'rectangular' else (8193, 1)) as image:
            image.save(stream, format='PNG')
        entries['satellite.png'] = stream.getvalue()
    with pytest.raises(ValueError):
        read_archive(package(tmp_path/'bad-composite.cmm', entries))


def test_raster_and_preview_orientation_agree(tmp_path):
    doc = load_document(package(tmp_path/'city.cmm', members()))
    with Image.open(io.BytesIO(doc._satellite_png)) as source:
        expected = np.array(source)
    canvas = Image.new('RGBA', (4,4))
    ctx = SimpleNamespace(image=canvas, transform=lambda x,z:(x,4-z))
    assert render_satellite(ctx,doc)
    assert np.array_equal(np.array(canvas)[:,:,:3], expected)
    rotated = SimpleNamespace(image=Image.new('RGBA',(4,4)),transform=lambda x,z:(4-z,4-x))
    render_satellite(rotated,doc)
    corners = compile_satellite(doc, tmp_path)
    with Image.open(tmp_path/'satellite.png') as preview:
        assert np.array_equal(np.array(preview),np.rot90(expected))
        assert np.array_equal(np.array(rotated.image)[:,:,:3],np.array(preview))
    from csmodernmap.preview_scene import world_to_geo
    assert corners == [world_to_geo(4,4),world_to_geo(4,0),world_to_geo(0,0),world_to_geo(0,4)]


def test_native_cs1_archive_cross_language():
    import os
    from pathlib import Path
    directory = os.environ.get("CS1_CMM11_FIXTURE_DIR")
    if not directory:
        pytest.skip('set CS1_CMM11_FIXTURE_DIR to the native CMM 1.1 example directory')
    root = Path(directory)
    sat=load_document(root/'native-satellite.cmm')
    city=load_document(root/'native-city.cmm')
    legacy=load_document(root/'legacy-city.cmm.gz')
    assert sat.schema_version == city.schema_version == 'cmm1.1'
    assert legacy.schema_version == 'cmm-v1'
    assert sat.networks[0].to_dict() == legacy.networks[0].to_dict()
    assert len(sat._satellite_png) > 0 and city._satellite_png is None


def test_full_preview_satellite_is_independent_and_disabled_by_default(tmp_path, modern_style):
    from csmodernmap.cmm import DocumentSession
    from csmodernmap.preview_scene import PreviewSceneCompiler
    from style_fixtures import pixel_style
    from csmodernmap.layers.water_layer import WaterLayer
    doc = load_document(package(tmp_path/'city.cmm', members()))
    # This fixture has no vector water; imagery no longer overrides terrain implicitly.
    canvas = Image.new('RGBA',(4,4),(23,45,67,255))
    WaterLayer().render(SimpleNamespace(image=canvas), doc)
    assert canvas.getpixel((0,0)) == (23,45,67,255)
    bundle = PreviewSceneCompiler.compile_full(DocumentSession(doc), modern_style, 1)
    try:
        assert not bundle.manifest['layers']['satellite']
        assert 'satellite' not in bundle.manifest['sources']
        water = json.loads((bundle.root/'water.geojson').read_text(encoding='utf-8'))
        assert water['features'] == []
    finally:
        bundle.close()


def test_satellite_scene_incremental_and_html_omission(tmp_path, modern_style):
    from csmodernmap.cmm import DocumentSession
    from csmodernmap.preview_scene import PreviewSceneCompiler
    from style_fixtures import pixel_style
    from csmodernmap.web_export_v2 import payload_from_preview_bundle
    doc = load_document(package(tmp_path/'city.cmm', members()))
    session = DocumentSession(doc)
    first = PreviewSceneCompiler.compile_full(session, modern_style, 1, visibility={'satellite': True})
    second = None
    try:
        assert first.manifest['satelliteCorners']
        assert 'satellite' in first.manifest['sources']
        second = PreviewSceneCompiler.compile_full(session, modern_style, 2, visibility={'satellite': False}, previous=first)
        assert first.manifest['sourceRevisions']['satellite'] == second.manifest['sourceRevisions']['satellite']
        payload = payload_from_preview_bundle(second, visibility={'satellite': False}, navigation_enabled=False)
        assert 'satellite' not in payload['manifest']['sources']
        assert 'satellite.png' not in payload['embedded']['images']
        assert not payload['manifest']['satelliteAvailable']
    finally:
        first.close()
        if second: second.close()


def test_backend_satellite_preserves_layers_and_failed_enable():
    from csmodernmap.guir_backend import GuirBackend
    from csmodernmap.satellite_image import COVERING_LAYERS
    backend = GuirBackend.__new__(GuirBackend)
    backend.layer_visibility = {key: True for key in (*COVERING_LAYERS, 'road', 'railway', 'traffic', 'poi', 'label', 'district')}
    backend.layer_visibility['satellite'] = False
    backend.document = MapDocument.from_dict({})
    original = dict(backend.layer_visibility)
    with pytest.raises(ValueError): backend.set_layer_visibility('satellite', True)
    assert backend.layer_visibility == original
    backend.document._satellite_png = members()['satellite.png']
    backend.set_layer_visibility('satellite', True)
    assert backend.layer_visibility == {**original, 'satellite': True}
    assert backend.layer_visibility['label'] and backend.layer_visibility['poi']
    backend.layer_visibility['road'] = False
    backend.set_layer_visibility('satellite', False)
    assert backend.layer_visibility == {**original, 'road': False}


def layered_members(size=4096, target=4096):
    entries = members()
    entries.pop('satellite.png')
    raw = json.loads(entries['city.json'])
    raw['satellite'] = {'world_bounds_xz': [0, 0, 100, 100], 'output_size': target,
                        'layers': {'land': 'satellite/land.png', 'water': 'satellite/water.png'}}
    entries['city.json'] = json.dumps(raw)
    for name, mode, color in [('land', 'RGB', (180, 90, 30)), ('water', 'RGBA', (20, 70, 160, 128))]:
        stream = io.BytesIO()
        with Image.new(mode, (size, size), color) as im: im.save(stream, format='PNG')
        entries[f'satellite/{name}.png'] = stream.getvalue()
    return entries


def test_layered_archive_composition_and_original_roundtrip(tmp_path):
    from csmodernmap.satellite_image import ensure_satellite
    entries = layered_members()
    doc = load_document(package(tmp_path/'raw.cmm', entries))
    assert doc._satellite_png is None and doc._satellite_composite is None
    result = ensure_satellite(doc)
    assert ensure_satellite(doc) is result
    with Image.open(io.BytesIO(result)) as image:
        assert image.size == (4096, 4096)
        assert image.getpixel((100, 100)) == (100, 80, 95)
    assert doc._satellite_png is None and 'layers' in doc._satellite_info
    dump_json(doc, tmp_path/'copy.cmm')
    with zipfile.ZipFile(tmp_path/'copy.cmm') as archive:
        assert 'satellite.png' not in archive.namelist()
        assert archive.read('satellite/land.png') == entries['satellite/land.png']


@pytest.mark.parametrize('case', ['missing', 'dimensions', 'water_mode', 'target', 'path'])
def test_invalid_layered_archive(tmp_path, case):
    entries = layered_members()
    raw = json.loads(entries['city.json'])
    if case == 'missing': entries.pop('satellite/water.png')
    elif case == 'target': raw['satellite']['output_size'] = 8192
    elif case == 'path': raw['satellite']['layers']['land'] = '../land.png'
    else:
        stream = io.BytesIO()
        with Image.new('RGB' if case == 'water_mode' else 'RGBA', (4096, 4096) if case == 'water_mode' else (32,32)) as image:
            image.save(stream, format='PNG')
        entries['satellite/water.png'] = stream.getvalue()
    entries['city.json'] = json.dumps(raw)
    path = package(tmp_path/'bad.cmm', entries)
    with pytest.raises(ValueError): read_archive(path)
    assert not load_document(path)._satellite_layers


def test_png_renderer_satellite_corners_and_rotation(tmp_path):
    from csmodernmap.renderer import Renderer
    from style_fixtures import pixel_style
    from csmodernmap.layers.satellite_layer import SatelliteLayer
    doc = load_document(package(tmp_path/'city.cmm', members()))
    for angle in (0, 90, 180, 270):
        renderer = Renderer(pixel_style({}), width=64, height=64, rotation=angle)
        renderer.add_layer(SatelliteLayer())
        image = renderer.render(doc, region=(0,0,4,4))
        assert image.size == (64,64)
        colors = {image.getpixel(point)[:3] for point in ((16,16),(48,16),(16,48),(48,48))}
        expected = {(255,0,0),(0,255,0),(0,0,255),(255,255,0)}
        assert all(any(max(abs(a-b) for a,b in zip(actual,wanted)) <= 5 for actual in colors) for wanted in expected)


def test_gpu_limit_failure_preserves_layer_state(tmp_path):
    from csmodernmap.guir_backend import GuirBackend
    from csmodernmap.winui_engine import EngineServer, LoopScheduler
    backend = GuirBackend(LoopScheduler(), settings_dir=tmp_path/'settings')
    backend.document = load_document(package(tmp_path/'city.cmm', members()))
    server = EngineServer(io.StringIO(), io.StringIO(), backend)
    original = dict(backend.layer_visibility)
    try:
        with pytest.raises(ValueError, match='2'):
            server.dispatch('setLayerVisibility', {'key': 'satellite', 'enabled': True, 'maxTextureSize': 2})
        assert backend.layer_visibility == original
        result = server.dispatch('setLayerVisibility', {'key': 'satellite', 'enabled': True, 'maxTextureSize': 4096})
        assert result['layers']['satellite']
        assert result['mapLayers'] == backend.map_layer_visibility()
    finally:
        backend.close()
        server._preview_files.cleanup()
