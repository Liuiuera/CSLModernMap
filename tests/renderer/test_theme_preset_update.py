"""Preset updates win conflicts while preserving the exact previous appearance."""
import copy
import io
import json
import shutil
from types import SimpleNamespace

import pytest
import yaml

from csmodernmap import guir_backend as module
from csmodernmap.guir_backend import GuirBackend
from csmodernmap.theme_library import content_digest, make_record, preset_update
from csmodernmap.winui_engine import EngineServer


def scheduler():
    return SimpleNamespace(call_soon=lambda *_: 1, call_later=lambda *_: 1, cancel=lambda *_: None)


def settings(path):
    return json.loads((path / "settings.json").read_text(encoding="utf-8"))["values"]


def test_three_way_update_only_changes_publisher_fields():
    previous = {"water": {"color": "#112233"}, "roads": {"width_scale": 1},
                "extension": {"delete": 1, "same": 2}, "ramp": [1, 2]}
    personal = copy.deepcopy(previous)
    personal["water"]["color"] = "#334455"
    personal["roads"]["width_scale"] = 1.37
    personal["extension"]["delete"] = 3
    personal["own"] = {"keep": [7, 11]}
    personal["ramp"] = [3, 4]
    current = copy.deepcopy(previous)
    current["water"]["color"] = "#556677"
    current["extension"].pop("delete")
    current["new"] = True
    current["ramp"] = [5, 6]
    original = copy.deepcopy(personal)
    merged, changes, preserved = preset_update(previous, personal, current)
    assert merged["water"]["color"] == "#556677"
    assert merged["roads"]["width_scale"] == 1.37
    assert merged["own"] == personal["own"] and merged["ramp"] == [5, 6]
    assert merged["extension"] == {"same": 2} and merged["new"] is True
    assert {tuple(item["path"]) for item in changes if item["conflict"]} == {("water", "color"), ("extension", "delete"), ("ramp",)}
    assert ["roads", "width_scale"] in preserved and ["own"] in preserved
    assert personal == original


@pytest.fixture
def old_install(tmp_path, monkeypatch):
    bundle = tmp_path / "program"
    shutil.copytree(module.resource_dir("styles"), bundle / "styles")
    monkeypatch.setattr(module, "bundle_root", lambda: bundle)
    original_resource = module.resource_dir
    monkeypatch.setattr(module, "resource_dir", lambda name: bundle / "styles" if name == "styles" else original_resource(name))
    old = json.loads((bundle / "styles/presets.current.json").read_text(encoding="utf-8"))["modern"]
    current = copy.deepcopy(old)
    current["water"]["color"] = "#224466"
    current["terrain"]["hillshade"]["intensity"] = 0.6
    current["new_update_field"] = {"keep": True}
    manifest = json.loads((bundle / "styles/presets.current.json").read_text(encoding="utf-8"))
    manifest["modern"] = current
    (bundle / "styles/presets.current.json").write_text(json.dumps(manifest), encoding="utf-8")
    hashes = json.loads((bundle / "styles/presets.sha256.json").read_text(encoding="utf-8"))
    hashes["modern.yaml"]["content"] = content_digest(current)
    (bundle / "styles/presets.sha256.json").write_text(json.dumps(hashes), encoding="utf-8")
    (bundle / "styles/modern.yaml").write_text(yaml.safe_dump(current), encoding="utf-8")
    personal = copy.deepcopy(old)
    personal["water"]["color"] = "#123456"
    personal["roads"]["width_scale"] = 1.37
    personal["upgrade_extension"] = {"keep": [7, 11]}
    user = tmp_path / "UserData"
    user.mkdir()
    return bundle, user, old, personal, current


def write_personal(user, old, personal):
    record = make_record("Personal", personal, "modern", old)
    record["presetData"] = old
    (user / "settings.json").write_text(json.dumps({"version": 3, "values": {
        "style_var": "user_active", "theme_library_version": 1, "user_themes": {"user_active": record}, "unrelated": 42}}), encoding="utf-8")


def test_saved_personal_updates_once_and_user_can_switch_previous(old_install):
    _, user, old, personal, current = old_install
    write_personal(user, old, personal)
    backend = GuirBackend(scheduler(), settings_dir=user)
    server = EngineServer(io.StringIO(), io.StringIO(), backend)
    try:
        assert backend.theme_stem == "user_active"
        assert backend.current_style_data()["water"]["color"] == "#224466"
        assert backend.current_style_data()["roads"]["width_scale"] == 1.37
        assert backend.current_style_data()["upgrade_extension"] == personal["upgrade_extension"]
        notice = server.snapshot()["themeUpdate"]
        assert notice and any(item["conflict"] for item in notice["changes"])
        previous = notice["previousTheme"]
        assert content_digest(backend.theme_data(previous)) == content_digest(personal)
        assert len(backend.user_themes) == 2
        before = (user / "settings.json").read_bytes()
        reopened = GuirBackend(scheduler(), settings_dir=user)
        try:
            assert (user / "settings.json").read_bytes() == before
            assert reopened.theme_update_notice and len(reopened.user_themes) == 2
        finally:
            reopened.close()
        server.dispatch("acknowledgeThemeUpdate", {"usePrevious": True})
        assert backend.theme_stem == previous and server.snapshot()["themeUpdate"] is None
        assert settings(user)["style_var"] == previous and "theme_update_notice" not in settings(user)
    finally:
        server._preview_files.cleanup()
        backend.close()
    reopened = GuirBackend(scheduler(), settings_dir=user)
    try:
        assert reopened.theme_stem == previous and reopened.theme_update_notice is None
        assert content_digest(reopened.current_style_data()) == content_digest(personal)
        assert len(reopened.user_themes) == 2 and settings(user)["unrelated"] == 42
    finally:
        reopened.close()


def test_known_legacy_file_merges_using_verified_history(old_install):
    bundle, user, old, personal, current = old_install
    (bundle / "styles/presets.previous.json").write_text(json.dumps({"apple_maps": {"preset": "modern", "data": old}}), encoding="utf-8")
    source = user / "styles/apple_maps.yaml"
    source.parent.mkdir()
    source.write_text(yaml.safe_dump(personal), encoding="utf-8")
    raw = source.read_bytes()
    backend = GuirBackend(scheduler(), settings_dir=user)
    try:
        assert backend.theme_update_notice and backend.theme_stem.startswith("user_updated_")
        assert backend.current_style_data()["water"]["color"] == "#224466"
        assert backend.current_style_data()["roads"]["width_scale"] == 1.37
        assert source.read_bytes() == raw
        assert len(backend.user_themes) == 2
        assert next((user / "theme-backups").glob("*.yaml")).read_bytes() == raw
        backend.acknowledge_theme_update(False)
        assert not backend.theme_update_notice
    finally:
        backend.close()


def test_update_save_failure_rolls_back_and_keeps_old_personal_usable(old_install, monkeypatch):
    _, user, old, personal, _ = old_install
    write_personal(user, old, personal)
    raw = (user / "settings.json").read_bytes()
    monkeypatch.setattr(module, "save_settings", lambda *_: False)
    backend = GuirBackend(scheduler(), settings_dir=user)
    try:
        assert backend._theme_migration_aborted and not backend.theme_update_notice
        assert len(backend.user_themes) == 1
        assert (user / "settings.json").read_bytes() == raw
        assert content_digest(backend.current_style_data()) == content_digest(personal)
    finally:
        backend.close()


def test_no_baseline_leaves_custom_file_unchanged(old_install):
    _, user, _, personal, _ = old_install
    source = user / "styles/my-theme.yaml"
    source.parent.mkdir()
    source.write_text(yaml.safe_dump(personal), encoding="utf-8")
    backend = GuirBackend(scheduler(), settings_dir=user)
    try:
        assert len(backend.user_themes) == 1 and not backend.theme_update_notice
        record = next(iter(backend.user_themes.values()))
        assert content_digest(record["data"]) == content_digest(personal)
    finally:
        backend.close()


def test_acknowledgement_save_failure_keeps_pending_notice_and_selection(old_install, monkeypatch):
    _, user, old, personal, _ = old_install
    write_personal(user, old, personal)
    backend = GuirBackend(scheduler(), settings_dir=user)
    try:
        before = (user / "settings.json").read_bytes()
        notice = copy.deepcopy(backend.theme_update_notice)
        monkeypatch.setattr(module, "save_settings", lambda *_: False)
        with pytest.raises(OSError):
            backend.acknowledge_theme_update(True)
        assert backend.theme_stem == "user_active" and backend.theme_update_notice == notice
        assert (user / "settings.json").read_bytes() == before
    finally:
        backend.close()


def test_inactive_personal_updates_without_switching_or_showing_notice(old_install):
    _, user, old, personal, _ = old_install
    write_personal(user, old, personal)
    data = json.loads((user / "settings.json").read_text(encoding="utf-8"))
    data["values"]["style_var"] = "bright"
    (user / "settings.json").write_text(json.dumps(data), encoding="utf-8")
    backend = GuirBackend(scheduler(), settings_dir=user)
    try:
        assert backend.theme_stem == "bright" and not backend.theme_update_notice
        assert len(backend.user_themes) == 2
        assert backend.theme_data("user_active")["water"]["color"] == "#224466"
    finally:
        backend.close()


def test_modified_installed_preset_cannot_change_the_merged_result(old_install):
    bundle, user, old, personal, _ = old_install
    write_personal(user, old, personal)
    installed = yaml.safe_load((bundle / "styles/modern.yaml").read_text(encoding="utf-8"))
    installed["terrain"]["hillshade"]["intensity"] = 0.1
    (bundle / "styles/modern.yaml").write_text(yaml.safe_dump(installed), encoding="utf-8")
    backend = GuirBackend(scheduler(), settings_dir=user)
    try:
        assert backend.current_style_data()["terrain"]["hillshade"]["intensity"] == 0.6
        assert backend.current_style_data()["roads"]["width_scale"] == 1.37
        assert backend.user_themes["user_active"]["name"] == "Personal"
    finally:
        backend.close()
