"""Satellite styling against synthetic image backgrounds, without theme/UI changes."""
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image, ImageDraw

from csmodernmap.layers.base import RenderContext
from csmodernmap.layers.label_layer import LabelLayer
from csmodernmap.layers.road_layer import RoadLayer
from csmodernmap.style import hex_to_rgba
from style_fixtures import pixel_style
from road_fixtures import scene_from_lines


class Transform:
    scale = 1.0

    def __call__(self, x, z):
        return 128+x, 128-z


def background(mode="RGB"):
    y, x = np.indices((256, 256))
    rgb = np.stack((40+x//2, 60+y//2, 50+(x+y)//4), axis=-1).astype("uint8")
    return Image.fromarray(rgb).convert(mode)


def context(image, style=None, **kwargs):
    return RenderContext(image, ImageDraw.Draw(image), Transform(), pixel_style(style or {}),
                         world_x0=-128, world_z0=-128, world_x1=128, world_z1=128,
                         **kwargs)


class ColoredRoads(RoadLayer):
    def _cfg_record(self, road):
        return {"fill": ("#e6b45a", "#64c8e6")[road.id-1],
                "border": "#242830", "border_width": 3, "line_width": 20, "footprint_scale": 1}


def draw_roads(image, scene, opacity=None):
    ctx = context(image, {"roads": {} if opacity is None else {"opacity": opacity}})
    ctx.shared["road_scene"] = scene
    original_draw = ctx.draw
    ColoredRoads().render(ctx, None)
    assert ctx.image is image and ctx.draw is original_draw
    # Subsequent layers still draw into the original canvas.
    ctx.draw.point((0, 0), fill="white")
    return image


@pytest.mark.parametrize("surfaces", [True, False])
@pytest.mark.parametrize("mode", ["RGB", "RGBA"])
def test_roads_blend_completed_layer_once_preserving_upper_road(surfaces, mode):
    scene = scene_from_lines([[(-100, 0, -10), (100, 0, 10)],
                              [(0, 20, -100), (0, 20, 100)]],
                             width=20, surfaces=surfaces)
    base = background(mode)
    opaque = draw_roads(base.copy(), scene)
    actual = draw_roads(base.copy(), scene, .4)
    expected = Image.blend(base, opaque, .4)
    expected.putpixel((0, 0), opaque.getpixel((0, 0)))
    error = np.abs(np.asarray(actual, dtype=int)-np.asarray(expected, dtype=int))
    assert error.max() <= 3  # AGG coverage and integer alpha rounding at edges.
    upper = np.array((100, 200, 230))
    center = np.array(base.getpixel((128, 128))[:3])
    assert np.max(np.abs(np.array(actual.getpixel((128, 128))[:3]) -
                         np.round(.4*upper + .6*center))) <= 1
    assert actual.mode == mode


def test_roads_opacity_defaults_and_endpoints():
    scene = scene_from_lines([[(-100, 0, 0), (100, 0, 0)]], width=20)
    base = background()
    assert draw_roads(base.copy(), scene).tobytes() == draw_roads(base.copy(), scene, 1).tobytes()
    expected = base.copy()
    expected.putpixel((0, 0), (255, 255, 255))
    assert draw_roads(base.copy(), scene, 0).tobytes() == expected.tobytes()


def test_roads_on_transparent_canvas_keep_straight_color_and_layer_alpha():
    scene = scene_from_lines([[(-100, 0, 0), (100, 0, 0)]], width=20)
    image = draw_roads(Image.new("RGBA", (256, 256)), scene, .5)
    assert image.getpixel((128, 128)) == (230, 180, 90, 128)
    assert image.getpixel((20, 20)) == (0, 0, 0, 0)
    pixels = np.asarray(image)
    edge = (pixels[..., 3] > 10) & (pixels[..., 3] < 120)
    assert edge.any()


@pytest.mark.parametrize("opacity", [-.1, 1.1, float("nan"), float("inf"), "invalid", None])
def test_invalid_road_opacity_reports_field(opacity):
    with pytest.raises(ValueError, match="roads.opacity"):
        ColoredRoads().render(context(background(), {"roads": {"opacity": opacity}}), None)


@pytest.mark.parametrize("color,expected", [("#abc", (170, 187, 204, 255)),
    ("#123456", (18, 52, 86, 255)), ("#12345680", (18, 52, 86, 128))])
def test_label_color_formats(color, expected):
    assert hex_to_rgba(color) == expected


def draw_text(image, kind, *, fill="#ffffff", stroke="#000000", width=3,
              blur=0, angle=0):
    layer = LabelLayer()
    layer.ctx = context(image)
    layer._aa_k = 1
    if kind == "district":
        layer._render_text((128, 128), "HH", 48, fill, width, stroke,
                           angle=angle, stroke_blur=blur)
    else:
        font = layer._get_font(48, text="HH")
        bbox = font.getbbox("HH", stroke_width=width)
        span = bbox[2]-bbox[0]
        layer._render_text_run([(128, 128, angle, 0, span)],
                               (70, 90, 186, 166), "HH", 48, fill, width, stroke,
                               stroke_blur=blur)
    return image


@pytest.mark.parametrize("kind", ["district", "road"])
@pytest.mark.parametrize("angle", [0, 35])
def test_label_fill_and_stroke_alpha_are_independent(kind, angle):
    image = draw_text(Image.new("RGBA", (256, 256)), kind, fill="#ffffff80",
                      stroke="#00000040", angle=angle)
    pixels = np.asarray(image)
    assert pixels[..., 3].max() <= 128
    assert np.any((pixels[..., 3] == 128) & (pixels[..., 0] == 255))
    assert np.any((pixels[..., 3] == 64) & (pixels[..., 0] == 0))
    invisible = draw_text(background(), kind, fill="#ffffff00", stroke="#00000000",
                          angle=angle)
    assert invisible.tobytes() == background().tobytes()


@pytest.mark.parametrize("kind", ["district", "road"])
@pytest.mark.parametrize("angle", [0, -30])
def test_alpha_text_and_soft_halo_blend_with_synthetic_background(kind, angle):
    transparent = draw_text(Image.new("RGBA", (256, 256)), kind, fill="#ffffffb0",
                            stroke="#00000080", blur=3, angle=angle)
    expected = Image.alpha_composite(background("RGBA"), transparent).convert("RGB")
    for mode in ("RGB", "RGBA"):
        actual = draw_text(background(mode), kind, fill="#ffffffb0", stroke="#00000080",
                           blur=3, angle=angle)
        assert actual.mode == mode
        assert np.max(np.abs(np.asarray(actual.convert("RGB"), dtype=int)-
                             np.asarray(expected, dtype=int))) <= 1
    hard = np.asarray(draw_text(Image.new("RGBA", (256, 256)), kind,
                                fill="#ffffffb0", stroke="#00000080", angle=angle))
    soft = np.asarray(transparent)
    expanded = (hard[..., 3] == 0) & (soft[..., 3] > 0)
    assert expanded.any()
    assert np.all(soft[..., :3][expanded] == 0), "halo must retain its dark stroke color"


@pytest.mark.parametrize("kind", ["district", "road"])
def test_opaque_labels_keep_original_pillow_raster(kind):
    layer = LabelLayer()
    image = Image.new("RGB", (256, 256), "#809060")
    layer.ctx = context(image)
    layer._aa_k = 1
    font = layer._get_font(48, text="HH")
    bbox = font.getbbox("HH", stroke_width=3)
    tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
    pad = 7 if kind == "district" else 0
    patch = Image.new("RGBA", (tw+2*pad, th+2*pad))
    ImageDraw.Draw(patch).text((pad-bbox[0], pad-bbox[1]), "HH", font=font,
                              fill="white", stroke_width=3, stroke_fill="black")
    expected = image.copy()
    if kind == "district":
        expected.paste(patch, (round(128-patch.width/2), round(128-patch.height/2)), patch)
    else:
        # Integer inverse sampling on a single straight strip reproduces the old run raster.
        expected.paste(patch.crop((0, 0, tw-1, th-1)), (80, 100),
                       patch.crop((0, 0, tw-1, th-1)))
        layer._render_text_run([(80+tw/2, 100+th/2, 0, 0, tw)],
                               (80, 100, 80+tw, 100+th), "HH", 48,
                               "#ffffff", 3, "#000000")
    if kind == "district":
        layer._render_text((128, 128), "HH", 48, "#ffffff", 3, "#000000")
    assert image.tobytes() == expected.tobytes()


@pytest.mark.parametrize("kind,method", [("DISTRICT_NAME", "_render_text"),
                                        ("ROAD_NAME", "_render_text_run")])
def test_style_stroke_blur_scales_pixel_size(monkeypatch, kind, method):
    layer = LabelLayer()
    layer.ctx = context(background())
    layer.render_scale, layer._aa_k = 2, 1
    captured = []
    monkeypatch.setattr(layer, method, lambda *args, **kwargs: captured.append(kwargs))
    layer._paint([SimpleNamespace(label=SimpleNamespace(type=kind, priority=1, text="Road"),
        style={"size": 24, "stroke_blur": 3}, x=128, y=128,
        bbox=(50, 50, 200, 200), metadata={"text_run": []})])
    assert captured[0]["stroke_blur"] == 6


@pytest.mark.parametrize("blur", [-1, float("nan"), float("inf")])
def test_invalid_label_blur_reports_field(blur):
    layer = LabelLayer()
    layer.ctx = context(background())
    layer.render_scale = 1
    with pytest.raises(ValueError, match="stroke_blur"):
        layer._paint([SimpleNamespace(label=SimpleNamespace(priority=1),
                                     style={"stroke_blur": blur})])
