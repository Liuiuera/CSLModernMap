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
    assert backend.user_themes[identity]["overrides"] == {"roads": {"highway": {"fill": "#123456"}}}
    assert backend.user_themes[identity]["removed"] == []
    assert backend.user_themes[identity]["base"] == "modern"
    assert next(item for item in server.snapshot()["themes"] if item["id"] == identity)["name"] == "My city"
    saved = json.loads((backend._settings_dir / "settings.json").read_text(encoding="utf-8"))["values"]
    assert saved["style_var"] == identity and saved["user_themes"][identity] == backend.user_themes[identity]
    assert "guir_style_overrides" not in saved


def test_saved_theme_inherits_new_preset_after_restart(engine, monkeypatch):
    backend, server = engine
    draft = server.dispatch("getThemeDraft", {})
    draft["data"]["name"] = "Personal"
    draft["data"]["roads"]["highway"]["fill"] = "#123456"
    identity = save(server, draft)["theme"]
    original_load = backend_module.load_style
    read_text = type(backend._settings_dir).read_text

    def updated_baseline(path, *args, **kwargs):
        raw = read_text(path, *args, **kwargs)
        if path.name == "presets.current.json":
            data = json.loads(raw)
            data["modern"]["water"]["color"] = "#010203"
            data["modern"]["roads"]["highway"]["fill"] = "#ABCDEF"
            return json.dumps(data)
        return raw

    def upgraded(path, *args, **kwargs):
        data = original_load(path, *args, **kwargs).data
        data["water"]["color"] = "#010203"
        data["roads"]["highway"]["fill"] = "#ABCDEF"
        return Style(data)

    monkeypatch.setattr(backend_module, "load_style", upgraded)
    monkeypatch.setattr(type(backend._settings_dir), "read_text", updated_baseline)
    reopened = GuirBackend(backend.scheduler, settings_dir=backend._settings_dir)
    try:
        assert reopened.theme_stem == identity
        assert reopened.current_style_data()["water"]["color"] == "#010203"
        assert reopened.current_style_data()["roads"]["highway"]["fill"] == "#ABCDEF"
        assert reopened.theme_update_notice
        assert reopened.theme_data(reopened.theme_update_notice["previousTheme"])["roads"]["highway"]["fill"] == "#123456"
        assert reopened._capture_ui_state()["style_overrides"][identity] == reopened.current_style_data()
    finally:
        reopened.close()


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
    assert backend.user_themes[backend.theme_stem]["data"] == data
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


def test_changed_and_custom_theme_files_are_backed_up_without_overwriting(tmp_path):
    scheduler = SimpleNamespace(call_soon=lambda *_: 1, call_later=lambda *_: 1, cancel=lambda *_: None)
    settings = tmp_path / "settings"
    styles = settings / "styles"
    styles.mkdir(parents=True)
    default = backend_module.load_style(str(backend_module.resource_dir("styles") / "modern.yaml")).data
    changed = copy.deepcopy(default)
    changed["water"]["color"] = "#123456"
    files = {"apple_maps.yaml": changed, "own.yml": {"name": "Own", "canvas": {"background": "#111111"}},
             "amap.yaml": backend_module.load_style(str(backend_module.resource_dir("styles") / "bright.yaml")).data}
    for name, data in files.items():
        (styles / name).write_text(yaml.safe_dump(data), encoding="utf-8")
    originals = {path: path.read_bytes() for path in styles.iterdir()}
    backend = GuirBackend(scheduler, settings_dir=settings)
    assert len(backend.user_themes) == 2
    assert backend.theme_stem.startswith("user_file_")
    assert backend.current_style_data()["water"]["color"] == "#123456"
    assert len(list((settings / "theme-backups").glob("*.yaml"))) == 2
    backend.close()
    reopened = GuirBackend(scheduler, settings_dir=settings)
    try:
        assert len(reopened.user_themes) == 2
        assert all(path.read_bytes() == raw for path, raw in originals.items())
        assert reopened.theme_data("modern")["water"]["color"] != "#123456"
    finally:
        reopened.close()


def test_corrupt_inactive_legacy_file_is_skipped_and_logged(tmp_path, caplog):
    styles = tmp_path / "styles"
    styles.mkdir()
    raw = b"name: [broken"
    path = styles / "old.yaml"
    path.write_bytes(raw)
    scheduler = SimpleNamespace(call_soon=lambda *_: 1, call_later=lambda *_: 1, cancel=lambda *_: None)
    backend = GuirBackend(scheduler, settings_dir=tmp_path)
    try:
        assert not backend.user_themes and backend.theme_migration_issues
        assert not (tmp_path / "theme-backups").exists()
        assert "Theme migration skipped" in caplog.text
        assert not backend._theme_migration_complete
        server = EngineServer(io.StringIO(), io.StringIO(), backend)
        assert server.snapshot()["themeMigrationIssues"] == []
        server._preview_files.cleanup()
        assert path.read_bytes() == raw
    finally:
        backend.close()


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


def test_modified_canonical_program_theme_is_preserved(engine, tmp_path, monkeypatch):
    backend, _ = engine
    import shutil
    bundle = tmp_path / "program"
    styles = bundle / "styles"
    shutil.copytree(backend_module.resource_dir("styles"), styles)
    path = styles / "dark.yaml"
    changed = load_style(str(path)).data
    changed["water"]["color"] = "#123456"
    path.write_text(yaml.safe_dump(changed), encoding="utf-8")
    raw = path.read_bytes()
    monkeypatch.setattr(backend_module, "bundle_root", lambda: bundle)
    backend._migrate_theme_files()
    assert len(backend.user_themes) == 1
    identity, record = next(iter(backend.user_themes.items()))
    assert record["base"] is None and record["legacyBase"] == "dark"
    assert backend.theme_data(identity)["water"]["color"] == "#123456"
    assert path.read_bytes() == raw
    assert next((backend._settings_dir / "theme-backups").glob("*.yaml")).read_bytes() == raw
    backend._migrate_theme_files()
    assert len(backend.user_themes) == 1


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
        assert next((tmp_path / "theme-backups").glob("settings-*.json")).read_bytes() == raw
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


def test_content_comparison_ignores_metadata_aliases_and_known_defaults(engine):
    backend, _ = engine
    original = backend.theme_data("modern")
    equivalent = copy.deepcopy(original)
    equivalent["name"] = "A different title"
    equivalent["description"] = "A different description"
    equivalent["roads"].pop("opacity", None)
    equivalent["roads"]["highway"]["border_width"] = 1
    original["roads"]["highway"]["border_width"] = 1.0
    equivalent["water"]["color"] = "#abc"
    original["water"]["color"] = "#AABBCC"
    equivalent["poi_icons"]["icons_dir"] = "poi_icons"
    equivalent["poi_icons"]["sprite"] = "unused.png"
    assert content_digest(original) == content_digest(equivalent)
    equivalent["extension"] = {"keep": True}
    assert content_digest(original) != content_digest(equivalent)


def test_equivalent_canonical_alias_and_custom_files_do_not_copy(engine, tmp_path):
    backend, _ = engine
    settings = tmp_path / "upgrade"
    original = backend.theme_data("modern")
    original["name"] = "My renamed copy"
    original.pop("description", None)
    original["roads"].pop("opacity", None)
    for name in ("modern.yaml", "apple_maps.yaml", "renamed.yml"):
        write_theme(settings / "styles" / name, original, sort_keys=True)
    reopened = GuirBackend(scheduler(), settings_dir=settings)
    try:
        assert not reopened.user_themes
        assert not (settings / "theme-backups").exists()
        assert reopened.theme_stem == "modern"
    finally:
        reopened.close()


def test_same_content_deduplicates_files_snapshots_and_saved_personal(tmp_path):
    data = {"name": "Saved", "water": {"color": "#123456"}, "extension": {"values": [1, 2]}}
    record = make_record("Saved personal", data, None, None)
    (tmp_path / "settings.json").write_text(json.dumps({"version": 3, "values": {
        "style_var": "apple_maps", "user_themes": {"user_saved": record,
            "user_duplicate": {**record, "name": "Already saved duplicate"}},
        "guir_style_overrides": {"apple_maps": data}}}), encoding="utf-8")
    for name in ("apple_maps.yaml", "copy.yml"):
        write_theme(tmp_path / "styles" / name, {**data, "name": name}, sort_keys=name.endswith("yaml"))
    backend = GuirBackend(scheduler(), settings_dir=tmp_path)
    try:
        assert list(backend.user_themes) == ["user_saved", "user_duplicate"]
        assert backend.theme_stem == "user_saved"
        assert not (tmp_path / "theme-backups").exists()
    finally:
        backend.close()


def test_different_formatting_of_new_content_creates_only_one_backup(tmp_path):
    data = {"name": "First", "water": {"color": "#123456"}, "extension": {"keep": [1, 2]}}
    write_theme(tmp_path / "styles/apple_maps.yaml", data)
    write_theme(tmp_path / "styles/copy.yml", {**data, "name": "Second"}, sort_keys=True)
    backend = GuirBackend(scheduler(), settings_dir=tmp_path)
    try:
        assert len(backend.user_themes) == 1
        assert len(list((tmp_path / "theme-backups").glob("*.yaml"))) == 1
    finally:
        backend.close()


@pytest.mark.parametrize("filename", ["apple_maps.yaml", "custom.yml"])
def test_backup_failure_keeps_original_selected_and_retries_next_start(tmp_path, monkeypatch, filename):
    path = tmp_path / "styles/apple_maps.yaml"
    data = {"name": "Old", "water": {"color": "#123456"}, "extension": {"keep": True}}
    write_theme(path, data)
    if filename == "custom.yml":
        path.rename(path.with_name(filename))
        path = path.with_name(filename)
    raw = path.read_bytes()
    monkeypatch.setattr(GuirBackend, "_backup_theme", lambda *_: (_ for _ in ()).throw(PermissionError("read only")))
    backend = GuirBackend(scheduler(), settings_dir=tmp_path)
    server = EngineServer(io.StringIO(), io.StringIO(), backend)
    try:
        assert not backend.user_themes and not backend._theme_migration_complete
        if filename == "custom.yml":
            fallback = next(item for item in backend.theme_choices() if not item["builtin"])
            assert backend.set_theme(fallback["id"], persist=False)
        assert backend.current_style_data()["water"]["color"] == "#123456"
        assert server.snapshot()["themeLoadError"] == ""
        assert server.dispatch("getThemeDraft", {})["baseTheme"] is None
        assert backend._capture_ui_state()["style_overrides"][backend.theme_stem]["extension"] == data["extension"]
    finally:
        backend.close()
        server._preview_files.cleanup()
    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))["values"]
    assert not saved["theme_library_version"] and not saved["theme_file_imports"].get(str(path.resolve()))
    assert path.read_bytes() == raw
    monkeypatch.undo()
    reopened = GuirBackend(scheduler(), settings_dir=tmp_path)
    try:
        assert len(reopened.user_themes) == 1 and reopened._theme_migration_complete
        assert next(iter(reopened.user_themes.values()))["data"]["water"]["color"] == "#123456"
    finally:
        reopened.close()


def test_failed_migration_save_rolls_back_all_theme_changes(tmp_path, monkeypatch):
    original = {"version": 3, "values": {"style_var": "apple_maps", "guir_style_overrides": {
        "google": {"name": "Old snapshot", "water": {"color": "#234567"}}}}}
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(original), encoding="utf-8")
    write_theme(tmp_path / "styles/apple_maps.yaml", {"name": "Old file", "water": {"color": "#123456"}})
    raw = path.read_bytes()
    monkeypatch.setattr(backend_module, "save_settings", lambda *_: False)
    backend = GuirBackend(scheduler(), settings_dir=tmp_path)
    try:
        assert not backend.user_themes and backend._theme_migration_aborted
        assert backend.current_style_data()["water"]["color"] == "#123456"
        assert path.read_bytes() == raw
        values = backend._collect_settings_values()
        assert "user_themes" not in values and "theme_library_version" not in values
        assert values["guir_style_overrides"] == original["values"]["guir_style_overrides"]
    finally:
        backend.close()


def test_unreadable_file_does_not_prevent_other_files_migrating(tmp_path, monkeypatch):
    broken = tmp_path / "styles/apple_maps.yaml"
    write_theme(broken, {"water": {"color": "#123456"}})
    write_theme(tmp_path / "styles/dark.yaml", {"water": {"color": "#234567"}})
    open_file = type(broken).open
    monkeypatch.setattr(type(broken), "open", lambda path, *args, **kwargs: (_ for _ in ()).throw(PermissionError("locked")) if path == broken else open_file(path, *args, **kwargs))
    backend = GuirBackend(scheduler(), settings_dir=tmp_path)
    server = EngineServer(io.StringIO(), io.StringIO(), backend)
    try:
        assert len(backend.user_themes) == 1
        assert backend.theme_load_error
        assert server.snapshot()["themeLoadError"]
        with pytest.raises(ValueError):
            backend.current_style_data()
        assert backend.set_theme("dark", persist=False)
        assert not backend.theme_load_error
    finally:
        backend.close()
        server._preview_files.cleanup()


def test_corrupt_active_theme_can_be_reselected_without_blocking_startup(tmp_path):
    path = tmp_path / "styles/apple_maps.yaml"
    path.parent.mkdir()
    path.write_text("name: [broken", encoding="utf-8")
    backend = GuirBackend(scheduler(), settings_dir=tmp_path)
    server = EngineServer(io.StringIO(), io.StringIO(), backend)
    try:
        assert backend.theme_load_error and str(path) in backend.theme_load_error
        assert server.dispatch("hello", {"protocolVersion": 1})
        assert server.snapshot()["themeLoadError"]
        with pytest.raises(ValueError):
            backend.preview_theme()
        assert backend.set_theme("bright", persist=False)
        assert server.snapshot()["themeLoadError"] == ""
    finally:
        backend.close()
        server._preview_files.cleanup()


def test_program_source_directories_are_read_directly_and_only_at_startup(tmp_path, monkeypatch):
    program = tmp_path / "old-install"
    path = program / "styles/apple_maps.yaml"
    write_theme(path, {"name": "Old", "water": {"color": "#123456"}})
    settings = tmp_path / "settings"
    monkeypatch.setenv("CSLMM_THEME_SOURCE_DIRS", json.dumps([str(program)]))
    backend = GuirBackend(scheduler(), settings_dir=settings)
    server = EngineServer(io.StringIO(), io.StringIO(), backend)
    try:
        assert len(backend.user_themes) == 1
        assert backend.current_style_data()["water"]["color"] == "#123456"
        assert not (settings / "legacy-themes").exists()
        monkeypatch.setattr(backend, "_migrate_theme_files", lambda: pytest.fail("Late theme scan"))
        backend.set_theme("bright", persist=False)
        server.dispatch("getThemeDraft", {})
        backend._load_persisted_state()
    finally:
        backend.close()
        server._preview_files.cleanup()


def test_manifest_failure_skips_transaction_and_keeps_original_file_usable(tmp_path, monkeypatch):
    path = tmp_path / "styles/apple_maps.yaml"
    write_theme(path, {"name": "Old", "water": {"color": "#123456"}})
    read_text = type(path).read_text
    monkeypatch.setattr(type(path), "read_text", lambda file, *a, **kw: (_ for _ in ()).throw(PermissionError("manifest locked")) if file.name == "presets.sha256.json" else read_text(file, *a, **kw))
    backend = GuirBackend(scheduler(), settings_dir=tmp_path)
    try:
        assert backend._theme_migration_aborted and not backend.user_themes
        assert backend.current_style_data()["water"]["color"] == "#123456"
        assert not (tmp_path / "theme-backups").exists()
        assert not (tmp_path / "settings.json").exists()
    finally:
        backend.close()


def test_migration_results_are_persisted_before_startup_returns(tmp_path):
    write_theme(tmp_path / "styles/apple_maps.yaml", {"name": "Old", "water": {"color": "#123456"}})
    backend = GuirBackend(scheduler(), settings_dir=tmp_path)
    try:
        values = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))["values"]
        assert values["style_var"] == backend.theme_stem
        assert values["user_themes"] == backend.user_themes
        assert values["theme_library_version"] == 1
    finally:
        backend.close()


def test_directory_scan_failure_keeps_other_roots_available(tmp_path, monkeypatch):
    settings = tmp_path / "settings"
    write_theme(settings / "styles/apple_maps.yaml", {"water": {"color": "#123456"}})
    other = tmp_path / "unreadable/styles"
    other.mkdir(parents=True)
    monkeypatch.setenv("CSLMM_THEME_SOURCE_DIRS", json.dumps([str(other.parent)]))
    scan = type(other).rglob
    monkeypatch.setattr(type(other), "rglob", lambda path, pattern: (_ for _ in ()).throw(PermissionError("no directory access")) if path == other else scan(path, pattern))
    backend = GuirBackend(scheduler(), settings_dir=settings)
    try:
        assert len(backend.user_themes) == 1
        assert backend.current_style_data()["water"]["color"] == "#123456"
        assert not backend._theme_migration_complete
    finally:
        backend.close()


def test_corrupt_program_preset_does_not_block_an_already_migrated_install(tmp_path, monkeypatch):
    import shutil
    bundle = tmp_path / "program"
    shutil.copytree(backend_module.resource_dir("styles"), bundle / "styles")
    path = bundle / "styles/modern.yaml"
    path.write_text("name: [broken", encoding="utf-8")
    settings = tmp_path / "settings"
    settings.mkdir()
    (settings / "settings.json").write_text(json.dumps({"version": 3, "values": {
        "style_var": "modern", "theme_library_version": 1}}), encoding="utf-8")
    monkeypatch.setattr(backend_module, "bundle_root", lambda: bundle)
    monkeypatch.setattr(backend_module, "resource_dir", lambda name: bundle / name)
    backend = GuirBackend(scheduler(), settings_dir=settings)
    server = EngineServer(io.StringIO(), io.StringIO(), backend)
    try:
        assert backend.theme_stem == "modern" and backend.theme_load_error
        assert server.snapshot()["themeLoadError"]
        assert backend.set_theme("dark", persist=False)
        assert not server.snapshot()["themeLoadError"]
        assert path.read_text(encoding="utf-8") == "name: [broken"
    finally:
        backend.close()
        server._preview_files.cleanup()
