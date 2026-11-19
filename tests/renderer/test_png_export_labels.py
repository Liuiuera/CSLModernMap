"""Exercise exported pixels and the GUI -> worker settings boundary."""
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from csmodernmap import guir_backend as backend_module
from csmodernmap.cmm import District, DisplayName, Vec2
from csmodernmap.data import Building, MapData, Point, Segment, SegmentType
from csmodernmap.gui_render_worker import _build_normal_renderer, run_render_task
from cmm_test_factory import document_from_mapdata
from csmodernmap.ui_contracts import ExportRequest, RenderRegion


def _all_poi_visible():
    return {
        "icons": {"transit": True, "facility": True, "ordinary": True, "area": True},
        "names": {"transit": True, "facility": True, "ordinary": True, "area": True},
    }


@pytest.fixture
def backend(tmp_path):
    return backend_module.GuirBackend(SimpleNamespace(
        call_soon=lambda *_: None, call_later=lambda *_: None, cancel=lambda *_: None),
        settings_dir=tmp_path)


def named_document(game):
    data = MapData(segments=[Segment(
        1, 1, 2, name="Road", icls="Road", custom_name="Named Road",
        points=[Point(-7000, 0, -4000), Point(7000, 0, -4000)])],
        segment_types={"Road": SegmentType(name="Road", road=True, car_lane_count=2)},
        buildings=[Building(2, name="Hospital", custom_name="Named Hospital",
                            category_id=("BUILDING_TITLE[Hospital]" if game == 'cs1'
                                         else 'Assets.NAME[Hospital01]'),
                            service="HealthCare", icls="HealthCare Facility", points=[
                                Point(-100, 0, -100), Point(100, 0, -100),
                                Point(100, 0, 100), Point(-100, 0, 100)])])
    document = document_from_mapdata(data)
    document.metadata.origin.game = game
    document.districts = [
        District(3, DisplayName("Named District", "custom"), "DISTRICT", "ORDINARY",
                 [Vec2(-4000, 4000)], position=Vec2(-4000, 4000)),
        District(4, DisplayName("Named Park", "custom"), "PARK_ZONE", "PARK",
                 [Vec2(4000, 4000)], position=Vec2(4000, 4000), source_raw="Generic"),
    ]
    return document


@pytest.mark.parametrize("game,size,aa", [("cs1", 512, False), ("cs2", 1024, True)])
def test_png_contains_each_enabled_name_and_respects_switches(backend, tmp_path, game, size, aa):
    backend.source_file = "named.cmm"
    backend.size, backend.aa = size, aa
    backend.layer_visibility = {"poi": True, "label": True}
    backend.road_names = backend.district_names = backend.zone_names = backend.poi_names = True
    colors = {"road": "#ef0102", "district": "#03ee04"}
    style = backend.current_style_data()
    for kind, color in colors.items():
        style["labels"][kind].update(size=16, color=color, stroke="#ffffff")
    # 功能区名称由 POI 控制，历史 labels.park 样式不应独立产生文字。
    style['labels']['park'].update(size=16, color='#0506ed', stroke='#ffffff')
    backend.style_overrides[backend.theme_stem] = style
    output_profile = backend.get_output_profile("normal_png")
    output_profile.update(size=size, aa=aa)
    job = backend.capture_png_export_job(
        "map", output_profile=output_profile)
    backend._attach_poi_render_config(
        job, "normal_png", visibility_snapshot=_all_poi_visible())
    # An old queued job / saved preference must no longer suppress text.
    job["detail_mode"] = "overview"
    cache = {job["file"]: named_document(game)}

    def render(payload):
        image = run_render_task(payload, mapdata_cache=cache)["image"]
        path = tmp_path / f"{game}.png"
        image.save(path)
        return np.asarray(Image.open(path).convert("RGB"))

    pixels = render(job)
    assert pixels.shape[:2] == (size, size)

    def has_color(image, color):
        rgb = np.array(tuple(bytes.fromhex(color[1:])))
        # Supersampling blends thin glyphs even at their darkest pixels.
        return np.any(np.max(np.abs(image.astype(int) - rgb), axis=2) <= 16)

    for color in colors.values():
        assert has_color(pixels, color), color

    no_names = deepcopy(job)
    no_names["ui"]["poi_visibility_snapshot"]["names"] = {
        "transit": False, "facility": False, "ordinary": False, "area": False,
    }
    no_poi_names = render(no_names)
    assert np.any(pixels != no_poi_names), "POI names never reached the PNG"

    legacy_name_toggle = deepcopy(job)
    legacy_name_toggle["ui"]["poi_names"] = False
    legacy_name_toggle["ui"]["transport_names"] = False
    assert np.array_equal(pixels, render(legacy_name_toggle))

    for field, kind in (("road_names", "road"), ("district_names", "district")):
        disabled = deepcopy(job)
        disabled["ui"][field] = False
        assert not has_color(render(disabled), colors[kind])

    disabled = deepcopy(job)
    disabled['ui']['zone_names'] = False
    assert np.array_equal(pixels, render(disabled))
    assert not has_color(pixels, '#0506ed')

    disabled = deepcopy(job)
    disabled["layers"] = ["poi"]
    disabled["ui"]["text_enabled"] = False
    poi_only = deepcopy(job)
    poi_only["layers"] = ["poi"]
    assert np.array_equal(render(disabled), render(poi_only))


def test_export_snapshot_is_independent_of_later_live_settings(backend, monkeypatch):
    backend.source_file = "before.cmm"
    backend.size, backend.aa, backend.rotation = 2048, True, 90
    backend.layer_visibility = {"label": True, "poi": True, "road": True}
    backend.road_names = backend.poi_names = True
    backend.poi_hidden_categories = {"school"}
    backend.merge_enabled, backend.merge_distance = False, 321
    backend.terrain = {"mode": "shade", "shade": 72, "sun": 128}
    region = RenderRegion((100, 200, 1100, 1200), source="selection")
    profile = backend.get_poi_profile("normal_png")
    profile["groups"]["transit"]["distance_m"] = 222.0
    visibility = _all_poi_visible()
    output_profile = backend.get_output_profile("normal_png")
    output_profile.update(size=2048, rotation=90, aa=True)
    request = ExportRequest(region=region, render_snapshot=backend.capture_png_export_job(
                                "map", region, output_profile=output_profile,
                                poi_profile=profile,
                                poi_visibility_snapshot=visibility),
                            theme_snapshot={"name": backend.theme_stem,
                                            "data": backend.current_style_data()},
                            layer_snapshot=backend.layer_visibility)
    request = ExportRequest.from_dict(request.to_dict())
    backend.source_file = "after.cmm"
    backend.road_names = backend.poi_names = False
    backend.layer_visibility.clear()
    backend.rotation = 0
    backend.apply_style_override("terrain.shade", 0)
    jobs = []
    monkeypatch.setattr(backend, "submit", lambda job, _: (jobs.append(job), True)[1])
    assert backend.render_png_export(request)
    job = jobs[0]
    assert job["file"] == "before.cmm"
    assert tuple(job["region"]) == region.bounds
    assert job["aa"] and job["ui"]["rotation"] == 90
    assert job["size"] == 2048 and job["sel_size"] == 2048
    assert job["ui"]["road_names"] and job["ui"]["poi_names"]
    assert job["ui"]["poi_profile"]["target"] == "normal_png"
    assert job["ui"]["poi_profile"]["groups"]["transit"]["distance_m"] == 222.0
    assert job["ui"]["poi_visibility_snapshot"] == visibility
    renderer = _build_normal_renderer(job["ui"], job["style"], job["layers"],
                                      job["size"], False, "normal")
    assert renderer.style.get("terrain.hillshade.intensity") == .72
    assert renderer.style.get("terrain.hillshade.azimuth") == 128
    assert renderer.style.get("poi_icons.hidden_categories") == ["school"]
    assert renderer.style.get("poi_icons.active_profile.target") == "normal_png"
    assert renderer.style.get("poi_icons.active_profile.groups.transit.distance_m") == 222.0
    assert renderer.style.get("poi_icons.visual_merge_enabled") is None
    assert renderer.style.get("poi_icons.visual_merge_distance") is None
    assert renderer.style.get("poi_icons.png_export") is None
    assert renderer.style.get("poi_icons.building.show_name") is True
    assert "detail_mode" not in backend._build_job(512, False)


def test_transit_view_options_reach_the_png_consumers(backend):
    from csmodernmap.gui_render_worker import _build_transit_renderer, _transit_base_layers
    options = {
        "layers": {"lines": False}, "stations": False, "line_label_enabled": False,
        "station_name_placement": "hidden", "facilities_enabled": False, "base_water": False,
        "base_road": False, "base_district": False,
        "line_width": 9, "station_diameter": 18, "merge_enabled": False,
        "merge_range": 87, "line_label_size": 21, "line_label_interval": 876,
        "facility_icon_size": 32,
    }
    ui = backend._capture_ui_state()
    backend._attach_poi_render_config(
        {"ui": ui}, "transit_png", visibility_snapshot=_all_poi_visible())
    renderer = _build_transit_renderer(ui, backend.theme_stem,
                                       ["Metro"], 512, opts=options)
    style = renderer.style
    assert style.get("transports.lines_visible") is False
    assert style.get("transports.width") == 9
    assert style.get("transports.station.enabled") is False
    assert style.get("transports.station.diameter") == 18
    assert style.get("transports.station.merge_enabled") is False
    assert style.get("transports.station.merge_range") == 87
    assert style.get("transports.label.enabled") is False
    assert style.get("transports.transit_view.label_size") == 21
    assert style.get("transports.label.repeat_interval") == 876
    assert style.get("poi_icons.enabled") is True
    assert style.get("poi_icons.transit.show_name") is True
    assert style.get("poi_icons.active_profile.target") == "transit_png"
    visibility = _all_poi_visible()
    for gate in ("icons", "names"):
        visibility[gate]["area"] = True
        visibility[gate]["transit"] = False
    assert style.get("poi_icons.visibility_snapshot") == visibility
    assert style.get("poi_icons.transit.icon_size") == 32
    assert _transit_base_layers(["road", "water", "district", "zone", "poi", "label", "metro", "building"],
                               options) == ["building"]
