import copy
import io
import json
from types import SimpleNamespace

import numpy as np
import pytest

from csmodernmap import gui_render_worker
from csmodernmap.cmm import DocumentSession
from csmodernmap.guir_backend import GuirBackend
from csmodernmap.preview_scene import preview_theme
from csmodernmap.preview_terrain import PreviewTerrainCompiler
from csmodernmap.terrain import TERRAIN_MODES, resolve_terrain_config
from csmodernmap.theme_contract import normalize_theme
from csmodernmap.winui_engine import EngineServer
from theme_editor_fixture import theme_editor_document


def backend_at(path):
    scheduler = SimpleNamespace(call_soon=lambda *_: 1, call_later=lambda *_: 1,
                                cancel=lambda *_: None)
    return GuirBackend(scheduler, settings_dir=path)


@pytest.mark.parametrize("mode", TERRAIN_MODES)
def test_editor_mode_drives_ribbon_preview_png_and_saved_theme(tmp_path, monkeypatch, mode):
    backend = backend_at(tmp_path)
    server = EngineServer(io.StringIO(), io.StringIO(), backend)
    try:
        assert backend.terrain["mode"] == "simple"
        backend.apply_style_override("terrain.mode", mode)
        draft = server.dispatch("getThemeDraft", {})
        assert [v["value"] for v in draft["fields"]["terrain.mode"]["choices"]] == list(TERRAIN_MODES)
        assert draft["fields"]["terrain.elevation_mode"]["hidden"]
        assert draft["fields"]["terrain.hillshade.enabled"]["hidden"]
        # Simulate editor changes while the old derived fields remain in the draft.
        data = draft["data"]
        data["terrain"]["mode"] = mode
        data["terrain"]["elevation_mode"] = "continuous" if mode == "simple" else "stepped"
        data["terrain"]["hillshade"].update(enabled=mode != "shade", intensity=.42, azimuth=90)
        data["built_area"]["edge_meters"] = 8
        result = server.dispatch("commitThemeDraft", {
            "theme": draft["theme"], "data": data, "documentEpoch": 0,
            "settingsRevision": draft["settingsRevision"], "name": "Terrain check"})
        assert result["saved"]
        identity = backend.theme_stem
        assert backend.terrain == {"mode": mode, "shade": 42, "sun": 90}
        current = backend.current_style_data()
        cfg = current["terrain"]
        assert cfg["elevation_mode"] == ("stepped" if mode == "simple" else "continuous")
        assert cfg["hillshade"]["enabled"] == (mode == "shade")
        assert backend.field_value("terrain.mode") == mode

        ui = backend._capture_ui_state()
        renderer = gui_render_worker._build_normal_renderer(ui, identity, ["terrain"], 128, False, "normal")
        assert renderer.style.data["terrain"]["hillshade"] == cfg["hillshade"]
        assert renderer.style.data["terrain"]["elevation_mode"] == cfg["elevation_mode"]
        document = theme_editor_document()
        backend.document, backend.session = document, DocumentSession(document)
        bounds = (0, 0, 1280, 1280)
        monkeypatch.setattr(backend, "_style_editor_region", lambda _: bounds)
        png = renderer.render(document, region=bounds, final_size=128, session=backend.session)
        editor = backend.style_preview_render(data, 128, "terrain")
        np.testing.assert_array_equal(np.asarray(png), np.asarray(editor))

        observed = []
        monkeypatch.setattr("csmodernmap.preview_terrain.TerrainLayer.render",
                            lambda self, ctx, _: observed.append(copy.deepcopy(ctx.style.data["terrain"])))
        monkeypatch.setattr("PIL.Image.Image.save", lambda *_a, **_kw: None)
        PreviewTerrainCompiler.compile(backend.session, current, ui["terrain"], tmp_path)
        assert observed[0]["mode"] == mode
        assert observed[0]["hillshade"] == cfg["hillshade"]
        assert preview_theme(current, {"terrain": ui["terrain"]})["built_area"]["edgeMeters"] == (0 if mode == "simple" else 8)
        backend.set_theme("dark")
        assert backend.terrain["mode"] == "simple"
        assert backend._preview_source_options(current)["terrain"]["mode"] == mode
        assert backend.preview_theme(current)["built_area"]["edgeMeters"] == (0 if mode == "simple" else 8)
        backend.set_theme(identity)
        assert backend.terrain["mode"] == mode
    finally:
        backend.close()
    reopened = backend_at(tmp_path)
    try:
        assert reopened.terrain == {"mode": mode, "shade": 42, "sun": 90}
        saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))["values"]
        assert not {"terrain_mode_var", "terrain_intensity_var", "terrain_azimuth_var"} & saved.keys()
    finally:
        reopened.close()


@pytest.mark.parametrize("legacy, expected", [("simple", "simple"), ("continuous", "continuous"),
                                                ("shade", "shade"), ("gradient", "shade")])
def test_old_main_settings_migrate_once_into_theme(tmp_path, legacy, expected):
    (tmp_path / "settings.json").write_text(json.dumps({"version": 3, "values": {
        "terrain_mode_var": legacy, "terrain_intensity_var": "0.6", "terrain_azimuth_var": "135"}}), encoding="utf-8")
    backend = backend_at(tmp_path)
    try:
        assert backend.terrain == {"mode": expected, "shade": 60, "sun": 135}
        backend.apply_style_override("terrain.mode", "continuous")
    finally:
        backend.close()
    reopened = backend_at(tmp_path)
    try:
        assert reopened.terrain == {"mode": "continuous", "shade": 60, "sun": 135}
    finally:
        reopened.close()


def test_partial_personal_theme_preserves_mode_when_only_lighting_changes(tmp_path):
    backend = backend_at(tmp_path)
    try:
        backend.apply_style_override("terrain.mode", "shade")
        backend.apply_style_override("terrain.shade", 67)
        identity = backend.theme_stem
        data = backend.current_style_data()
        identity = backend.save_personal_theme(data, identity=identity, base_id="modern", name="Lighting check")
        assert backend.theme_data(identity)["terrain"]["hillshade"]["intensity"] == .67
        assert backend.theme_data(identity)["terrain"]["mode"] == "shade"
        # A preset already using shade must retain it when its diff only edits intensity.
        from csmodernmap import theme_library
        from style_fixtures import complete_style
        base = normalize_theme(complete_style({"terrain": {"mode": "shade", "hillshade": {"intensity": .3}}}).data)
        record = theme_library.make_record("Intensity only", {**base, "terrain": {
            **base["terrain"], "hillshade": {**base["terrain"]["hillshade"], "intensity": .67}}}, "modern", base)
        assert theme_library.resolve(record, base)["terrain"]["mode"] == "shade"
    finally:
        backend.close()


def test_legacy_theme_modes_and_invalid_canonical_mode():
    assert resolve_terrain_config({})["mode"] == "simple"
    assert resolve_terrain_config({"elevation_mode": "linear"})["mode"] == "continuous"
    assert resolve_terrain_config({"elevation_mode": "stepped", "hillshade": {"enabled": True}})["mode"] == "shade"
    with pytest.raises(ValueError, match="terrain.mode"):
        resolve_terrain_config({"mode": "unsupported"})
