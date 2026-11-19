"""作品布局的冻结、修订隔离与等比绘制不变量。"""
from copy import deepcopy
import random

import pytest
from PIL import Image

from csmodernmap.artwork import ArtworkSnapshot, ArtworkState, bitmap, circle, image_resource, polyline, render_artwork
from csmodernmap.artwork_text import text_primitive
from csmodernmap.render_jobs import build_artwork_job
from csmodernmap.renderer import _build_transform


def sample(*, epoch=1, revision=1, rotation=0, edge=256):
    job = build_artwork_job('transit', (-200, -400, 200, 400), long_edge=edge, rotation=rotation)
    line = polyline('line:1', [(12, 24), (80, 65), (200, 65)], 3.5, '#2580bd')
    stop = circle('station:1', (80, 65), 12, stroke_width=1.5)
    return ArtworkSnapshot.create(job, document_epoch=epoch, revision=revision,
                                  objects=[line, stop], style_id='test-style')


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
@pytest.mark.parametrize('bounds', [(-200, -400, 200, 400), (-8640, -8640, 8640, 8640)])
def test_artwork_uses_existing_output_transform(bounds, rotation):
    job = build_artwork_job('normal', bounds, rotation=rotation)
    work = ArtworkSnapshot.create(job, document_epoch=1, revision=1, objects=[], style_id='plain')
    data = work.to_dict()
    assert max(data['canvas']['width'], data['canvas']['height']) == 4096
    t = _build_transform(job.canvas['width'], job.canvas['height'], bounds, rotation)
    assert job.canvas['zoom'] == pytest.approx(t.zoom)
    assert job.derive_params['render_scale'] == pytest.approx(t.zoom)
    a, b, c, d, e, f = data['canvas']['worldToArtwork']
    for x, z in [(bounds[0], bounds[1]), (bounds[2], bounds[3]), (0, 0), (95, -123)]:
        assert (a*x+c*z+e, b*x+d*z+f) == pytest.approx(t(x, z), abs=1e-7)
        assert t.inverse(*t(x, z)) == pytest.approx((x, z), abs=1e-7)


def test_snapshot_does_not_alias_mutable_jobs_or_objects():
    job = build_artwork_job('normal', (-200, -200, 200, 200))
    stop = circle('stop', (20, 20), 12)
    work = ArtworkSnapshot.create(job, document_epoch=1, revision=1, objects=[stop], style_id='plain')
    digest = work.digest
    job.canvas['width'] = 100
    stop['center'][0] = 99
    output = work.to_dict()
    output['objects'][0]['center'][0] = 88
    assert work.digest == digest
    assert work.to_dict()['objects'][0]['center'] == [20, 20]


def test_preview_recipe_is_validated_and_frozen_with_objects():
    job = build_artwork_job('normal', (-200, -200, 200, 200))
    recipe = {'theme': {}, 'layers': ['road'], 'requiredSources': ['roads'], 'layoutRevision': 1}
    work = ArtworkSnapshot.create(job, document_epoch=1, revision=1, objects=[],
        style_id='test', preview_base=recipe)
    recipe['layers'].append('water')
    assert work.to_dict()['previewBase']['layers'] == ['road']
    recipe['layoutRevision'] = 2
    with pytest.raises(ValueError, match='preview base'):
        ArtworkSnapshot.create(job, document_epoch=1, revision=1, objects=[],
            style_id='test', preview_base=recipe)


def test_valid_artwork_above_32_mib_roundtrips_and_renders_all_resources():
    rng = random.Random(42)
    resources = []
    for _ in range(3):
        with Image.frombytes('RGB', (1792, 1792), rng.randbytes(1792 * 1792 * 3)) as image:
            resources.append(image_resource(image))
    job = build_artwork_job('normal', (-100, -100, 100, 100), long_edge=96)
    objects = [bitmap(f'tile:{i}', resource, (32*i, 0, 32*(i+1), 96))
               for i, resource in enumerate(resources)]
    work = ArtworkSnapshot.create(job, document_epoch=1, revision=1, objects=objects,
                                  resources=resources, style_id='large-local-resources')
    assert len(work._encoded) > 32 * 1024 * 1024
    restored = ArtworkSnapshot.from_dict(work.to_dict())
    assert restored.digest == work.digest
    assert restored.to_dict()['resources'] == {resource['id']: resource for resource in resources}
    with render_artwork(restored) as image:
        for i in range(3):
            assert image.getpixel((32*i+16, 48))[:3] != (255, 255, 255)


@pytest.mark.parametrize('field,value', [('id', 'wrong-hash'), ('width', 999)])
def test_resource_integrity_checks_remain_required(field, value):
    data = sample().to_dict()
    resource = image_resource(Image.new('RGB', (8, 8), 'red'))
    data['resources'][resource['id']] = resource
    resource[field] = value
    with pytest.raises(ValueError, match='resource'):
        ArtworkSnapshot.from_dict(data)


def test_latest_revision_and_document_are_required_before_export():
    state = ArtworkState(1)
    assert state.request() == (1, 1)
    first = sample()
    assert state.accept(first) and state.status == 'ready'
    assert state.acknowledge((1, 1)) and state.status == 'presented'
    state.request()
    assert not state.accept(first)
    assert not state.acknowledge((1, 1))
    with pytest.raises(RuntimeError, match='not ready'):
        state.freeze()
    assert state.fail((1, 2), 'font unavailable') and state.status == 'failed'
    with pytest.raises(RuntimeError, match='font unavailable'):
        state.freeze()
    assert state.request() == (1, 3)
    assert not state.fail((1, 2), 'old failure')
    latest = sample(revision=3)
    assert state.accept(latest) and state.freeze() is latest
    state.set_document(2)
    assert not state.accept(latest)
    assert state.request() == (2, 1)
    assert state.accept(sample(epoch=2))
    assert state.freeze().identity == (2, 1)


def test_vector_batches_keep_compositing_order_across_images():
    job = build_artwork_job('transit', (-100, -100, 100, 100), long_edge=100)
    resource = image_resource(Image.new('RGBA', (10, 10), 'blue'))
    snapshot = ArtworkSnapshot.create(job, document_epoch=1, revision=1, style_id='order',
        objects=[circle('back', (40, 40), 50, fill='red', stroke_width=0),
                 bitmap('middle', resource, (30, 30, 60, 60)),
                 circle('front', (45, 45), 10, fill='green', stroke_width=0)],
        resources=[resource])
    image = render_artwork(snapshot)
    assert image.getpixel((20, 40))[:3] == (255, 0, 0)
    assert image.getpixel((35, 40))[:3] == (0, 0, 255)
    assert image.getpixel((45, 45))[:3] == (0, 128, 0)


@pytest.mark.parametrize('mutate', [
    lambda d: d.update(observation={'scale': 2}),
    lambda d: d['canvas'].update(width=0),
    lambda d: d['canvas']['worldToArtwork'].__setitem__(0, 99),
    lambda d: d['objects'].append(deepcopy(d['objects'][0])),
    lambda d: d['objects'][0].update(width=float('nan')),
    lambda d: d['objects'][1].update(diameter=-1),
])
def test_invalid_boundary_data_is_rejected(mutate):
    data = sample().to_dict()
    mutate(data)
    with pytest.raises(ValueError):
        ArtworkSnapshot.from_dict(data)


@pytest.mark.parametrize('aa', [1, 2, 4])
def test_raster_quality_does_not_mutate_layout_or_object_set(aa):
    work = sample()
    before = work.to_dict()
    for scale in [.5, 1, 2]:
        image = render_artwork(work, scale=scale, aa=aa)
        assert image.size == (round(256*scale), round(128*scale))
        assert work.to_dict() == before


def test_shared_text_resource_keeps_font_and_missing_character_results():
    item, resource = text_primitive('name', 'AV 中文 \U0010ffff', (30, 50), size=16, font_name='Microsoft YaHei')
    assert item['textRun']['missing'] == ['U+10FFFF']
    assert len(item['textRun']['fontId']) == 64
    job = build_artwork_job('normal', (-200, -200, 200, 200), long_edge=256)
    work = ArtworkSnapshot.create(job, document_epoch=1, revision=1, objects=[item],
                                  resources=[resource], style_id='text')
    assert render_artwork(work).size == (256, 256)
    corrupted = work.to_dict()
    corrupted['resources'][resource['id']]['url'] = 'https://example.com/font.png'
    with pytest.raises(ValueError, match='local PNG'):
        ArtworkSnapshot.from_dict(corrupted)


def test_bold_run_uses_an_actual_bold_font_face():
    regular, normal_asset = text_primitive('regular', '字体 AV', (30, 50), size=20, font_name='Microsoft YaHei')
    bold, bold_asset = text_primitive('bold', '字体 AV', (30, 50), size=20, font_name='msyhbd.ttc', bold=True)
    assert regular['textRun']['fontWeight'] == 400
    assert bold['textRun']['fontWeight'] == 700
    assert normal_asset['id'] != bold_asset['id']
    with pytest.raises(ValueError, match='does not supply bold'):
        text_primitive('invalid-bold', '字体 AV', (30, 50), size=20, font_name='msyh.ttc', bold=True)


def test_page_clips_images_without_repositioning_or_allocating_their_full_outside_area():
    job = build_artwork_job('normal', (-100, -100, 100, 100), long_edge=128)
    resource = image_resource(Image.new('RGBA', (8, 8), '#ff0000'))
    item = bitmap('large-outside-image', resource, [-1e8, -1e8, 64, 64])
    work = ArtworkSnapshot.create(job, document_epoch=1, revision=1, objects=[item],
                                  resources=[resource], style_id='clipping')
    image = render_artwork(work)
    assert image.getpixel((0, 0))[:3] == (255, 0, 0)
    assert image.getpixel((80, 80))[:3] == (255, 255, 255)
    assert work.to_dict()['objects'][0]['bbox'] == [-1e8, -1e8, 64, 64]
