"""Theme ownership, preset updates and lossless migration of existing edits."""
import copy
import hashlib
import io
import json
from types import SimpleNamespace

import pytest
import yaml

from csmodernmap import guir_backend as backend_module
from csmodernmap.guir_backend import GuirBackend, _theme_stem_for
from csmodernmap.style import Style, load_style
from csmodernmap.theme_contract import normalize_theme
from csmodernmap.theme_library import content_digest, difference, make_record, resolve, validate_name
from csmodernmap.winui_engine import EngineServer


@pytest.fixture
def engine(tmp_path):
    scheduler = SimpleNamespace(call_soon=lambda *_: 1, call_later=lambda *_: 1, cancel=lambda *_: None)
    backend = GuirBackend(scheduler, settings_dir=tmp_path / "settings")
    server = EngineServer(io.StringIO(), io.StringIO(), backend)
    yield backend, server
    backend.close()
    server._preview_files.cleanup()


def save(server, draft, **kwargs):
    return server.dispatch("commitThemeDraft", {"theme": draft["theme"], "data": draft["data"],
        "baseTheme": draft["baseTheme"], "new": draft["isNew"], "documentEpoch": draft["documentEpoch"],
        "settingsRevision": draft["settingsRevision"], **kwargs})


def test_preset_upgrade_keeps_only_personal_changes_and_whole_lists():
    old = {"name": "Preset", "water": {"color": "blue"}, "roads": {"color": "white"},
        "ramp": [1, 2], "extension": {"keep": True}, "deleted": True}
    personal = copy.deepcopy(old)
    personal["roads"]["color"] = "red"
    personal["ramp"] = [2, 4]
    personal.pop("deleted")
    record = make_record("Personal", personal, "modern", old)
    upgraded = copy.deepcopy(old)
    upgraded["water"]["color"] = "cyan"
    upgraded["roads"]["color"] = "yellow"
    upgraded["ramp"] = [0, 3, 5]
    upgraded["new"] = 42
    result = resolve(record, upgraded)
    assert result == {"name": "Personal", "water": {"color": "cyan"}, "roads": {"color": "red"},
        "ramp": [2, 4], "extension": {"keep": True}, "new": 42}
    assert difference(old, old) == ({}, [])


def test_first_save_creates_personal_theme_and_preserves_builtin(engine):
    backend, server = engine
    original = backend.theme_data("modern")
    draft = server.dispatch("getThemeDraft", {})
    draft["data"]["name"] = "My city"
    draft["data"]["roads"]["highway"]["fill"] = "#123456"
    result = save(server, draft)
    identity = result["theme"]
    assert identity.startswith("user_") and result["saved"]
    assert backend.theme_data("modern") == original
    assert backend.user_themes[identity]["data"]["roads"]["highway"]["fill"] == "#123456"
    assert backend.user_themes[identity]["base"] is None
    assert backend.user_themes[identity]["legacyBase"] == "modern"
    assert next(item for item in server.snapshot()["themes"] if item["id"] == identity)["name"] == "My city"
    saved = json.loads((backend._settings_dir / "settings.json").read_text(encoding="utf-8"))["values"]
    assert saved["style_var"] == identity and saved["user_themes"][identity] == backend.user_themes[identity]
    assert "guir_style_overrides" not in saved


def test_export_is_independent_and_atomic(engine, tmp_path, monkeypatch):
    backend, server = engine
    draft = server.dispatch("getThemeDraft", {})
    draft["data"]["roads"]["highway"]["fill"] = "#123456"
    original = backend.current_style_data()
    backend.save_settings_now()
    settings = (backend._settings_dir / "settings.json").read_bytes()
    target = tmp_path / "export.yaml"
    server.dispatch("exportThemeDraft", {"data": draft["data"], "path": str(target)})
    assert load_style(str(target)).get("roads.highway.fill") == "#123456"
    assert backend.current_style_data() == original and backend.theme_stem == "modern"
    assert (backend._settings_dir / "settings.json").read_bytes() == settings
    existing = target.read_bytes()
    import csmodernmap.theme_library as library
    monkeypatch.setattr(library.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("write failed")))
    with pytest.raises(OSError):
        server.dispatch("exportThemeDraft", {"data": draft["data"], "path": str(target)})
    assert target.read_bytes() == existing


def test_import_and_new_drafts_do_not_change_current_or_persist(engine, tmp_path):
    backend, server = engine
    original = backend.current_style_data()
    new = server.dispatch("newThemeDraft", {"baseTheme": "bright"})
    assert new["isNew"] and new["baseTheme"] == "bright"
    target = tmp_path / "custom.yml"
    target.write_text(yaml.safe_dump({**original, "name": "Imported"}), encoding="utf-8")
    imported = server.dispatch("importThemeDraft", {"path": str(target)})
    assert imported["isNew"] and imported["baseTheme"] is None
    assert backend.theme_stem == "modern" and backend.current_style_data() == original
    assert not backend.user_themes
    identity = save(server, imported)["theme"]
    assert backend.user_themes[identity]["base"] is None
    assert backend.current_style_data()["name"] == "Imported"


def test_save_failure_keeps_selection_library_and_disk(engine, monkeypatch):
    backend, server = engine
    backend.save_settings_now()
    original = backend.current_style_data()
    path = backend._settings_dir / "settings.json"
    saved = path.read_bytes()
    draft = server.dispatch("getThemeDraft", {})
    draft["data"]["name"] = "Personal"
    monkeypatch.setattr(backend_module, "save_settings", lambda *_: False)
    with pytest.raises(OSError):
        save(server, draft)
    assert backend.theme_stem == "modern" and not backend.user_themes
    assert backend.current_style_data() == original and path.read_bytes() == saved


@pytest.mark.parametrize("value", ["", "   ", "x" * 65, "a\nb", "a\u0000b"])
def test_invalid_names_are_rejected(value):
    with pytest.raises(ValueError):
        validate_name(value)


def test_names_are_display_text_and_cannot_collide():
    assert validate_name("  City: North/South  ") == "City: North/South"
    with pytest.raises(ValueError):
        validate_name("MY THEME", ["My theme"])


def test_legacy_snapshot_keeps_original_content_and_migrates_once(tmp_path):
    settings = tmp_path / "settings"
    settings.mkdir()
    data = {"name": "Old personal", "water": {"color": "#123456"}, "extension": {"future": [1, 2]}}
    (settings / "settings.json").write_text(json.dumps({"version": 3, "values": {
        "style_var": "apple_maps", "guir_style_overrides": {"apple_maps": data, "google": {"name": "Other"}}}}), encoding="utf-8")
    scheduler = SimpleNamespace(call_soon=lambda *_: 1, call_later=lambda *_: 1, cancel=lambda *_: None)
    backend = GuirBackend(scheduler, settings_dir=settings)
    assert backend.theme_stem == "user_legacy_modern"
    assert len(backend.user_themes) == 2
    assert backend.current_style_data()["extension"] == data["extension"]
    assert backend.user_themes[backend.theme_stem]["base"] is None
    assert backend.user_themes[backend.theme_stem]["data"]["size_contract"] == "pixel-v2"
    assert backend.user_themes[backend.theme_stem]["data"]["water"]["color"] == data["water"]["color"]
    backend.close()
    reopened = GuirBackend(scheduler, settings_dir=settings)
    try:
        assert len(reopened.user_themes) == 2
        assert reopened.current_style_data()["water"]["color"] == "#123456"
    finally:
        reopened.close()


@pytest.mark.parametrize("selected", ["modern", "warm_natural"])
def test_migrated_snapshots_do_not_override_later_builtin_selection(tmp_path, selected):
    (tmp_path / "settings.json").write_text(json.dumps({"version": 3, "values": {
        "style_var": "apple_maps", "guir_style_overrides": {
            "apple_maps": {"name": "First", "water": {"color": "#123456"}},
            "google": {"name": "Other", "water": {"color": "#234567"}},
        }}}), encoding="utf-8")
    scheduler = SimpleNamespace(call_soon=lambda *_: 1, call_later=lambda *_: 1, cancel=lambda *_: None)
    backend = GuirBackend(scheduler, settings_dir=tmp_path)
    assert backend.theme_stem == "user_legacy_modern"
    records = copy.deepcopy(backend.user_themes)
    backend.set_theme(selected, persist=False)
    backend.close()
    reopened = GuirBackend(scheduler, settings_dir=tmp_path)
    try:
        assert reopened.theme_stem == selected
        assert reopened.user_themes == records
    finally:
        reopened.close()


@pytest.mark.parametrize("old,new", [("apple_maps", "modern"), ("amap", "bright"), ("google", "warm_natural")])
def test_old_ids_and_files_remain_readable(old, new):
    assert _theme_stem_for(old) == new
    assert _theme_stem_for("gui.theme.choice." + old) == new
    assert load_style(str(backend_module.resource_dir("styles") / (old + ".yaml"))).data == load_style(
        str(backend_module.resource_dir("styles") / (new + ".yaml"))).data


def test_shipped_manifest_matches_presets():
    styles = backend_module.resource_dir("styles")
    manifest = json.loads((styles / "presets.sha256.json").read_text(encoding="utf-8"))
    assert manifest == {p.name: {"sha256": hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
                                "content": content_digest(load_style(str(p)).data)}
                        for p in styles.glob("*.yaml") if p.stem != "metro_map"}
    current = json.loads((styles / "presets.current.json").read_text(encoding="utf-8"))
    assert {stem + ".yaml": content_digest(data) for stem, data in current.items()} == {
        name: entry["content"] for name, entry in manifest.items()}


def test_multiple_alias_snapshots_and_same_filename_versions_are_all_preserved(tmp_path):
    settings = tmp_path / "settings.json"
    raw = json.dumps({"version": 3, "values": {"guir_style_overrides": {
        "apple_maps": {"name": "First", "water": {"color": "#112233"}},
        "modern": {"name": "Second", "water": {"color": "#223344"}}}}}).encode()
    settings.write_bytes(raw)
    for directory, color in (("one", "#334455"), ("two", "#445566")):
        path = tmp_path / "legacy-themes" / directory / "apple_maps.yaml"
        path.parent.mkdir(parents=True)
        path.write_text(yaml.safe_dump({"name": "File", "water": {"color": color}}), encoding="utf-8")
    scheduler = SimpleNamespace(call_soon=lambda *_: 1, call_later=lambda *_: 1, cancel=lambda *_: None)
    backend = GuirBackend(scheduler, settings_dir=tmp_path)
    try:
        assert len(backend.user_themes) == 4
        assert {backend.theme_data(key)["water"]["color"] for key in backend.user_themes} == {
            "#112233", "#223344", "#334455", "#445566"}
        assert next((tmp_path / "theme-backups").glob("settings-3.1-*/settings.json")).read_bytes() == raw
        backend.save_settings_now()
        reopened = GuirBackend(scheduler, settings_dir=tmp_path)
        try:
            assert reopened.user_themes == backend.user_themes
        finally:
            reopened.close()
    finally:
        backend.close()


def scheduler():
    return SimpleNamespace(call_soon=lambda *_: 1, call_later=lambda *_: 1, cancel=lambda *_: None)


def write_theme(path, data, *, sort_keys=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# User theme\n" + yaml.safe_dump(data, allow_unicode=True, sort_keys=sort_keys), encoding="utf-8")


def test_content_comparison_ignores_display_metadata_and_compatible_aliases(engine):
    backend, _ = engine
    original = backend.theme_data("modern")
    equivalent = copy.deepcopy(original)
    equivalent["name"] = "A different title"
    equivalent["description"] = "A different description"
    equivalent["roads"]["highway"]["border_width"] = 1
    original["roads"]["highway"]["border_width"] = 1.0
    equivalent["water"]["color"] = "#abc"
    original["water"]["color"] = "#AABBCC"
    equivalent["poi_icons"]["icons_dir"] = "poi_icons"
    equivalent["poi_icons"]["sprite"] = "unused.png"
    assert content_digest(original) == content_digest(equivalent)
    equivalent["extension"] = {"keep": True}
    assert content_digest(original) != content_digest(equivalent)
