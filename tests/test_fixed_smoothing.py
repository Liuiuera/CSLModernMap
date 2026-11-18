"""Internal smoothing must not depend on translated or persisted preset names."""
from __future__ import annotations

import json
from types import SimpleNamespace

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev/CSLModernMapRenderer"))

import pytest

from csmodernmap import i18n
from csmodernmap.guir_backend import GuirBackend
from csmodernmap.gui_render_worker import _build_normal_renderer
from csmodernmap.ribbon_schema import FIELDS, LAYER_FIELDS


@pytest.mark.parametrize("language", i18n.AVAILABLE_LANGS)
@pytest.mark.parametrize("legacy", [None, "Standard", "極強", "unknown-preset"])
def test_preview_and_png_ignore_retired_smoothing_settings(tmp_path, language, legacy):
    i18n.set_lang(language)
    path = tmp_path / "settings.json"
    values = {"unrelated_setting": "preserved"}
    if legacy is not None:
        values.update(built_smooth_var=legacy, water_smooth_var=legacy)
    path.write_text(json.dumps({"version": 3, "values": values}), encoding="utf-8")
    scheduler = SimpleNamespace(call_soon=lambda *_: 1, call_later=lambda *_: 1,
                                cancel=lambda *_: None)
    backend = GuirBackend(scheduler, settings_dir=tmp_path)
    try:
        # Exercise the default style and the internal defaults absent from a theme.
        backend._preview_style()
        built = backend._preview_style({"built_area": {}})["built_area"]
        assert (built["contour_upscale"], built["smooth_iters"], built["smooth_blur"]) == (4, 6, 2.0)
        ui = backend._capture_ui_state()
        renderer = _build_normal_renderer(ui, backend.theme_stem, ["water"], 512, False, "normal")
        water, = renderer._layers
        assert (water._upscale, water._smooth_iters, water._rdp_epsilon,
                water._min_area) == (4, 6, 3.0, 45)
        assert "built_smooth" not in ui and "water_smooth" not in ui
        assert "water.smooth" not in FIELDS
        assert "water.smooth" not in LAYER_FIELDS["water"]
    finally:
        backend.close()
    saved = json.loads(path.read_text(encoding="utf-8"))["values"]
    assert "built_smooth_var" not in saved
    assert "water_smooth_var" not in saved
    assert saved["unrelated_setting"] == "preserved"


@pytest.mark.parametrize("language", i18n.AVAILABLE_LANGS)
def test_internal_render_values_are_language_independent(monkeypatch, tmp_path, language):
    from csmodernmap import guir_backend

    monkeypatch.setattr(guir_backend, "load_settings", lambda *_: {"version": 3, "values": {}})
    monkeypatch.setattr(guir_backend, "save_settings", lambda *_: True)
    scheduler = SimpleNamespace(call_soon=lambda *_: 1, call_later=lambda *_: 1,
                                cancel=lambda *_: None)

    def snapshot(lang):
        i18n.set_lang(lang)
        backend = GuirBackend(scheduler, settings_dir=tmp_path)
        try:
            result = {
                "render": backend._capture_ui_state(),
                "settings": backend._collect_settings_values(),
                "fields": {key: backend.field_value(key) for key in FIELDS},
                "transit": backend.transit_options(),
                "metro": backend.transit_options(True),
            }
            themes = {}
            for _label, stem in guir_backend.THEME_CHOICES:
                backend.set_theme(stem, persist=False)
                themes[stem] = backend._preview_style()
            result["themes"] = themes
            return result
        finally:
            backend.close()

    assert snapshot(language) == snapshot("zh-CN")
