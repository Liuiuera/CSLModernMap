import json
import io
from types import SimpleNamespace

from csmodernmap import _paths, guir_backend, i18n
from csmodernmap.guir_backend import GuirBackend


def test_macos_shared_directory_keeps_legacy_settings(tmp_path, monkeypatch):
    shared = tmp_path / "CSLModernMap"
    shared.mkdir()
    experimental = tmp_path / "CSLModernMap Native"
    experimental.mkdir()
    monkeypatch.setattr(_paths, "app_support_dir", lambda: shared)
    monkeypatch.setattr(guir_backend, "app_support_dir", lambda: shared)
    monkeypatch.setattr(_paths.sys, "platform", "darwin")
    monkeypatch.setenv("CSLMM_NATIVE_DATA_DIR", str(shared))
    monkeypatch.delenv("CSLMM_LANG", raising=False)

    def write(folder, values):
        raw = json.dumps({"version": 3, "values": values}).encode()
        (folder / "settings.json").write_bytes(raw)
        return raw

    original = write(shared, {"style_var": "modern", "language": "en",
                              "layer_railway": False, "unknown_user_key": 42})
    experimental_original = write(experimental, {"layer_railway": True})
    preferences = shared / "preferences"
    preferences.mkdir()
    write(preferences, {"language": "ja"})
    city = tmp_path / "old-city"
    city.mkdir()
    city_original = write(city, {"style_var": "modern", "layer_railway": True,
                                 "unknown_user_key": 23})
    new_city = tmp_path / "new-city"
    new_city.mkdir()
    assert _paths.user_data_dir() == shared
    assert _paths.application_settings_dir() == preferences
    assert i18n.detect_lang() == "ja"
    scheduler = SimpleNamespace(call_soon=lambda *_: 1, call_later=lambda *_: 1,
                                cancel=lambda *_: None)
    backend = GuirBackend(scheduler)
    try:
        assert backend._settings_dir == shared
        assert backend.layer_visibility["railway"] is False
        assert backend._settings["values"]["unknown_user_key"] == 42
        assert any(p.read_bytes() == original for p in
                   (shared / "theme-backups").glob("*/settings.json"))
        backend._switch_settings_dir_for_city(str(city / "map.cmm"))
        assert backend._settings_dir == city
        assert backend.layer_visibility["railway"] is True
        assert backend._settings["values"]["unknown_user_key"] == 23
        assert any(p.read_bytes() == city_original for p in
                   (city / "theme-backups").glob("*/settings.json"))
        backend._switch_settings_dir_for_city(str(new_city / "map.cmm"))
        assert backend._settings_dir == shared
        assert backend.layer_visibility["railway"] is False
        assert not (new_city / "settings.json").exists()
        assert (experimental / "settings.json").read_bytes() == experimental_original
    finally:
        backend.close()


def test_explicit_test_directory_stays_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(_paths.sys, "platform", "darwin")
    monkeypatch.setenv("CSLMM_NATIVE_DATA_DIR", str(tmp_path))
    assert _paths.application_settings_dir() == tmp_path
    assert _paths.user_data_dir() == tmp_path


def test_windows_ignores_macos_data_directory(tmp_path, monkeypatch):
    native = tmp_path / "macos"
    windows = tmp_path / "windows"
    monkeypatch.setattr(_paths.sys, "platform", "win32")
    monkeypatch.setenv("CSLMM_NATIVE_DATA_DIR", str(native))
    monkeypatch.setenv("CSLMM_USER_DATA_DIR", str(windows))
    assert _paths.application_settings_dir() == windows
    assert _paths.user_data_dir() == windows


def test_text_icon_contract_keeps_legacy_fields_without_opt_in(tmp_path, monkeypatch):
    from csmodernmap.winui_engine import EngineServer, TEXT_ICON_FIELDS, EXTENDED_TEXT_ICON_FIELDS

    scheduler = SimpleNamespace(call_soon=lambda *_: 1, call_later=lambda *_: 1,
                                cancel=lambda *_: None)
    for enabled in (False, True):
        monkeypatch.setenv("CSLMM_SPLIT_MAP_SYMBOLS", "1" if enabled else "0")
        backend = GuirBackend(scheduler, settings_dir=tmp_path / str(enabled))
        server = EngineServer(io.StringIO(), io.StringIO(), backend)
        try:
            model = server.dispatch("getTextIconSettings", {"target": "normal"})
            expected = EXTENDED_TEXT_ICON_FIELDS if enabled else TEXT_ICON_FIELDS
            assert set(model["fields"]) == set(model["defaults"]) == set(expected)
            assert ("mapSymbolVisibility" in model) is enabled
            assert ("label.outline.park" in model["fields"]) is enabled
        finally:
            backend.close()
            server._preview_files.cleanup()
