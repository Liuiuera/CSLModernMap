from __future__ import annotations

import json
import pickle
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageDraw

from csmodernmap.cmm import (
    Building,
    BuildingRole,
    DisplayName,
    District,
    Grid,
    MapDocument,
    Metadata,
    Origin,
    Polygon,
    Terrain,
    Vec2,
    DocumentSession,
    ZoneKind,
)
from csmodernmap.services.surface_cache import SurfaceCache
from csmodernmap.services.surface_compiler import (
    SurfaceCompileOptions,
    SurfaceCompiler,
    surface_cache_key,
)
from csmodernmap.layers.base import RenderContext
from csmodernmap.layers.built_area_layer import BuiltAreaLayer
from style_fixtures import complete_style
from csmodernmap.preview_scene import (
    PreviewSceneCompiler,
    preview_theme,
    preview_source_signatures,
)


def _document() -> MapDocument:
    water = np.zeros((3, 3), dtype=np.float32)
    water[1, 1] = 8.0
    return MapDocument(
        metadata=Metadata(Origin("cs1", "test", "surface-fixture")),
        sea_level=0.0,
        nodes=[], networks=[], districts=[], stops=[], transit_lines=[], routes=[],
        buildings=[Building(
            id=7,
            name="house",
            display_name=DisplayName("house", "game"),
            role=BuildingRole.RESIDENTIAL,
            service="Residential",
            footprint=Polygon((
                Vec2(-40, -40), Vec2(40, -40),
                Vec2(40, 40), Vec2(-40, 40),
            )),
        )],
        terrain=Terrain(
            water_depth=Grid(
                rows=3, cols=3, origin=Vec2(-48, -48), cell=32,
                _values=water,
            ),
            origin=Vec2(-48, -48), cell=32, rows=3, cols=3,
        ),
    )


def test_compiler_produces_typed_shared_fields() -> None:
    model = SurfaceCompiler().compile(
        _document(), SurfaceCompileOptions(cell_meters=32))
    assert model.water_field is not None
    assert model.water_field.data.dtype == np.float16
    assert model.building_occupancy is not None
    assert model.building_occupancy.data.dtype == np.uint8
    assert model.built_area_distance is not None
    assert model.built_area_distance.data.dtype == np.int16
    centre = model.world_transform.rows // 2
    assert model.built_area_distance.data[centre, centre] <= 0
    assert model.built_area_distance.sampling == "linear"
    assert not model.built_area_distance.data.flags.writeable


def test_park_mode_without_park_zones_keeps_building_buffer() -> None:
    document = _document()
    compiler = SurfaceCompiler()
    plain = compiler.compile(document, SurfaceCompileOptions(
        cell_meters=32, include_parks=False))
    park_mode = compiler.compile(document, SurfaceCompileOptions(
        cell_meters=32, include_parks=True, built_area_buffer_meters=80))
    plain_distance = plain.built_area_distance.data.astype(np.int32)
    buffered_distance = park_mode.built_area_distance.data.astype(np.int32)
    assert np.array_equal(buffered_distance, plain_distance - 80)


def test_grid_only_park_zones_are_included_in_built_area_distance() -> None:
    document = _document()
    document.districts.append(District(
        id=42, name=DisplayName("park", "game"), kind="PARK_ZONE",
        zone_kind=ZoneKind.OTHER,
        grid_points=[Vec2(-160, -160), Vec2(-128, -160),
                     Vec2(-160, -128), Vec2(-128, -128)],
        grid_cell=32, source_raw="PARK"))
    model = SurfaceCompiler().compile(document, SurfaceCompileOptions(
        cell_meters=32, include_parks=True))
    field = model.built_area_distance.data
    transform = model.world_transform
    row = round((-144 - transform.origin_z) / transform.cell_meters)
    col = round((-144 - transform.origin_x) / transform.cell_meters)
    assert field[row, col] <= 0


def test_surface_preserves_park_holes_and_does_not_buffer_parks_or_excluded_buildings() -> None:
    compiler = SurfaceCompiler()
    document = _document()
    document.buildings.clear()
    document.districts.append(District(
        id=43, name=DisplayName("ring park", "game"), kind="PARK_ZONE",
        zone_kind=ZoneKind.OTHER,
        grid_points=[Vec2(x, z) for x, z in (
            (-32, -32), (0, -32), (32, -32),
            (-32, 0), (32, 0),
            (-32, 32), (0, 32), (32, 32))],
        grid_cell=32, source_raw="PARK"))
    parked = compiler.compile(document, SurfaceCompileOptions(
        cell_meters=8, include_parks=True, built_area_buffer_meters=60))
    field = parked.built_area_distance.data
    transform = parked.world_transform
    sample = lambda x, z: field[round((z-transform.origin_z)/transform.cell_meters),
                               round((x-transform.origin_x)/transform.cell_meters)]
    assert sample(0, 0) > 0  # the missing centre grid cell remains a hole

    document.districts[0].grid_points.clear()
    document.districts[0].boundary = Polygon((
        Vec2(-32, -32), Vec2(32, -32), Vec2(32, 32), Vec2(-32, 32)))
    square = compiler.compile(document, SurfaceCompileOptions(
        cell_meters=8, include_parks=True, built_area_buffer_meters=60))
    square_field = square.built_area_distance.data
    square_transform = square.world_transform
    outside = square_field[round((-0-square_transform.origin_z)/8),
                           round((40-square_transform.origin_x)/8)]
    assert outside > 0  # the configured building buffer never expands a park

    excluded_document = _document()
    excluded_document.buildings[0].service = "Water"
    excluded = compiler.compile(excluded_document, SurfaceCompileOptions(
        cell_meters=32, exclude_services=("Water",)))
    included = compiler.compile(excluded_document, SurfaceCompileOptions(
        cell_meters=32, exclude_services=()))
    center = excluded.world_transform.rows // 2
    assert excluded.building_occupancy.data[center, center] == 0
    assert included.building_occupancy.data[center, center] == 1


def test_built_area_png_uses_surface_only_for_default_session_path(monkeypatch) -> None:
    document = _document()
    style = complete_style({"built_area": {
        "enabled": True, "color": "#FF0000", "buffer_meters": 60,
        "cell_meters": 8,
    }})
    calls = []
    monkeypatch.setattr("csmodernmap.layers.built_area_layer.composite_distance_fill",
                        lambda *args, **kwargs: calls.append("surface"))
    monkeypatch.setattr("csmodernmap.layers.built_area_layer.compute_built_area_rings",
                        lambda *args, **kwargs: ([], []))

    class Session:
        def surface_model(self, options):
            calls.append(("model", options["cell_meters"]))
            return SimpleNamespace(built_area_distance=object(),
                                   world_transform=object())

    def render(session, cfg=None):
        image = Image.new("RGB", (64, 64), (0, 0, 0))
        context = RenderContext(image=image, draw=ImageDraw.Draw(image),
            transform=lambda x, z: (x + 32, z + 32),
            style=complete_style({"built_area": {**style.data["built_area"], **(cfg or {})}}),
            session=session)
        BuiltAreaLayer().render(context, document)

    render(Session())
    assert calls == [("model", 8.0), "surface"]
    calls.clear()
    render(Session(), {"backend": "legacy"})
    assert calls == []
    render(Session(), {"include_parks": True})
    assert calls == []


def test_shape_parameters_change_cache_key_but_presentation_is_absent() -> None:
    document = _document()
    a = surface_cache_key(document, SurfaceCompileOptions(cell_meters=8))
    b = surface_cache_key(document, SurfaceCompileOptions(cell_meters=16))
    assert a != b
    # The compiler accepts no colour/opacity/output-size input by design.
    assert "color" not in SurfaceCompileOptions.__dataclass_fields__
    assert "opacity" not in SurfaceCompileOptions.__dataclass_fields__


def test_grid_cell_size_changes_surface_cache_identity() -> None:
    document = _document()
    document.districts.append(District(
        id=42, name=DisplayName("park", "game"), kind="PARK_ZONE",
        zone_kind=ZoneKind.OTHER, grid_points=[Vec2(-160, -160)],
        grid_cell=32, source_raw="PARK"))
    options = SurfaceCompileOptions(include_parks=True)
    first = surface_cache_key(document, options)
    document.districts[0].grid_cell = 64
    assert surface_cache_key(document, options) != first


def test_surface_paint_defaults_to_cartographic_hard_edges() -> None:
    theme = preview_theme(complete_style({
        "built_area": {"smooth_blur": 2.0},
        "poi_area": {"smooth_blur": 3.0},
    }))

    assert theme["built_area"]["edgeMeters"] == 0.0
    assert "poi_area" not in theme


def test_surface_feathering_requires_explicit_edge_meters() -> None:
    theme = preview_theme(complete_style({
        "terrain": {"mode": "continuous", "elevation_mode": "continuous"},
        "built_area": {"edge_meters": 4.0},
        "poi_area": {"edge_meters": 6.0},
    }))

    assert theme["built_area"]["edgeMeters"] == 4.0
    assert "poi_area" not in theme


def test_preview_theme_exposes_transit_badge_contract() -> None:
    theme = preview_theme(complete_style({
        "transports": {"label": {
            "enabled": False, "size": 15, "repeat_interval": 900,
            "max_per_line": 3, "bg": "#FAFAFA", "border": "#CCCCCC",
        }},
    }))

    assert theme["transit"]["label"] == {
        "enabled": False,
        "size": 15.0,
        "repeatInterval": 900.0,
        "maxPerLine": 3,
        "background": "#FAFAFA",
        "border": "#CCCCCC",
    }


def test_surface_cache_reuses_identity_and_obeys_budget() -> None:
    cache = SurfaceCache(max_bytes=32 * 1024 * 1024)
    compiler = SurfaceCompiler(cache)
    options = SurfaceCompileOptions(cell_meters=64)
    first = compiler.compile(_document(), options)
    second = compiler.compile(_document(), options)
    assert first is second
    assert len(cache) == 1
    assert cache.byte_size == first.byte_size

    too_small = SurfaceCache(max_bytes=1)
    too_small.put(first)
    assert len(too_small) == 0
    assert too_small.byte_size == 0


def test_document_session_owns_and_releases_surface_cache() -> None:
    session = DocumentSession(_document())
    first = session.surface_model({"cell_meters": 64})
    assert session.surface_model({"cell_meters": 64}) is first
    assert len(session._surface_cache) == 1
    session.release()
    assert len(session._surface_cache) == 0


def test_document_session_pickle_drops_process_local_surface_cache() -> None:
    session = DocumentSession(_document())
    session.surface_model({"cell_meters": 64})
    restored = pickle.loads(pickle.dumps(session, protocol=pickle.HIGHEST_PROTOCOL))
    assert restored._surface_cache is None
    assert restored.document.metadata.origin.source_name == "surface-fixture"
    assert restored.surface_model({"cell_meters": 64}).built_area_distance is not None


def test_cpu_built_area_uses_smoothed_contours_in_session_render() -> None:
    session = DocumentSession(_document())
    image = Image.new("RGB", (64, 64), (0, 0, 0))
    scale = 64.0 / 17280.0

    def transform(x, z):
        return (64.0 - (z + 8640.0) * scale,
                64.0 - (x + 8640.0) * scale)

    ctx = RenderContext(
        image=image,
        draw=ImageDraw.Draw(image),
        transform=transform,
        style=complete_style({"built_area": {
            "enabled": True, "color": "#FF0000",
            "cell_meters": 8, "buffer_meters": 160,
            "contour_upscale": 4, "smooth_blur": 2.0,
            "smooth_iters": 6,
        }}),
        session=session,
    )
    BuiltAreaLayer().render(ctx, session.document)
    assert image.getpixel((32, 32))[0] > 200
    assert image.getpixel((32, 32))[1:] == (0, 0)
    assert image.getpixel((0, 0)) == (0, 0, 0)


def test_preview_built_area_geometry_and_paint_dependencies_are_separate() -> None:
    base = {"built_area": {
        "buffer_meters": 60, "cell_meters": 8,
        "contour_upscale": 4, "smooth_blur": 2, "smooth_iters": 6,
        "color": "#FFFFFF", "opacity": 1.0,
        "exclude_services": ["Water"],
    }}
    changed_paint = {"built_area": {
        "buffer_meters": 60, "cell_meters": 8,
        "contour_upscale": 4, "smooth_blur": 2, "smooth_iters": 6,
        "color": "#000000", "opacity": 0.5,
        "exclude_services": ["Water"],
    }}
    changed_shape = {"built_area": {
        "buffer_meters": 60, "cell_meters": 8,
        "contour_upscale": 4, "smooth_blur": 2, "smooth_iters": 8,
        "exclude_services": ["Water", "Road"],
    }}
    assert (preview_source_signatures(complete_style(base).data, {})["builtArea"]
            == preview_source_signatures(complete_style(changed_paint).data, {})["builtArea"])
    assert (preview_source_signatures(complete_style(base).data, {})["builtArea"]
            != preview_source_signatures(complete_style(changed_shape).data, {})["builtArea"])


def test_station_merge_options_only_invalidate_station_source() -> None:
    base = preview_source_signatures(complete_style({}).data, {
        "station_merge_enabled": True, "station_merge_range": 48,
    })
    changed = preview_source_signatures(complete_style({}).data, {
        "station_merge_enabled": False, "station_merge_range": 80,
    })

    assert base["stations"] != changed["stations"]
    assert base["transit"] == changed["transit"]


def test_preview_bundle_uses_smoothed_built_area_geojson() -> None:
    session = DocumentSession(_document())
    style = complete_style({"built_area": {
        "enabled": True, "buffer_meters": 60,
        "cell_meters": 8, "contour_upscale": 4,
        "smooth_blur": 2, "smooth_iters": 6, "backend": "legacy",
    }})
    bundle = PreviewSceneCompiler.compile_full(session, style, 3)
    try:
        assert "builtArea" not in bundle.manifest["surfaceFields"]
        source = bundle.manifest["sources"]["builtArea"]
        payload = json.loads((bundle.root / source.split("?")[0]).read_text())
        assert payload["features"]
        assert all(feature["properties"]["geometryRole"] == "built-area"
                   for feature in payload["features"])
    finally:
        bundle.close()
