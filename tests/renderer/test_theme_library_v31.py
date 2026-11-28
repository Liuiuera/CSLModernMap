"""Configuration 3.1 keeps historic themes and applies release changes only on request."""
from copy import deepcopy
from contextlib import contextmanager
import json
import hashlib
from types import SimpleNamespace

import pytest
import yaml

from csmodernmap import guir_backend as backend_module
from csmodernmap.guir_backend import GuirBackend
from csmodernmap.settings_store import load_settings, save_settings, SettingsVersionError
from csmodernmap.view_settings import default_view_settings, load_view_settings, save_view_settings
from csmodernmap import theme_migration, theme_preset_update


@contextmanager
def backend_at(path):
    scheduler = SimpleNamespace(call_soon=lambda *_: 1, call_later=lambda *_: 1, cancel=lambda *_: None)
    backend = GuirBackend(scheduler, settings_dir=path)
    try:
        yield backend
    finally:
        backend.close()


def write_settings(path, values, version=3):
    path.mkdir(parents=True, exist_ok=True)
    target = path / 'settings.json'
    target.write_text(json.dumps({'version': version, 'values': values}), encoding='utf-8')
    return target


def test_decimal_version_round_trip_and_pending_version_preserved(tmp_path):
    path = write_settings(tmp_path, {'keep': 42})
    assert save_settings({'keep': 43}, tmp_path)
    assert load_settings(tmp_path)['version'] == 3
    assert save_settings({'keep': 44}, tmp_path, 3.1)
    assert load_settings(tmp_path) == {'version': 3.1, 'values': {'keep': 44}}
    assert json.loads(path.read_text())['version'] == 3.1


@pytest.mark.parametrize('version', [3.2, 4, True, '3.1'])
def test_unsupported_version_cannot_be_overwritten(tmp_path, version):
    path = write_settings(tmp_path, {'keep': 42}, version)
    raw = path.read_bytes()
    assert load_settings(tmp_path)["values"] == {}
    with backend_at(tmp_path) as backend:
        assert backend.theme_load_error
        backend.save_settings_now()
    with pytest.raises(SettingsVersionError):
        save_settings({}, tmp_path)
    assert path.read_bytes() == raw


def test_bad_recovery_journal_keeps_original_files_and_allows_render_defaults(tmp_path):
    settings = write_settings(tmp_path, {"keep": 42})
    journal = tmp_path / theme_migration.JOURNAL
    journal.write_text("{")
    original = settings.read_bytes()
    views = default_view_settings()
    views["transit"]["mask"] = .27
    views["metro"]["diagram"]["spacing"] = 439
    assert save_view_settings(views, tmp_path)
    expected_views = load_view_settings(tmp_path)
    with backend_at(tmp_path) as backend:
        assert backend.theme_load_error
        assert backend.current_style_data()["roads"]
        backend.save_settings_now()
        backend.update_transit_options(False, {"base_water": False})
    expected_views["transit"]["base_layers"]["water"] = False
    assert load_view_settings(tmp_path) == expected_views
    assert settings.read_bytes() == original
    assert journal.read_text() == "{"


def test_fresh_install_does_not_import_current_bundled_presets(tmp_path):
    with backend_at(tmp_path) as backend:
        assert backend.user_themes == {}
        assert len(backend.theme_choices()) == 7
        assert all(item['builtin'] for item in backend.theme_choices())
    assert load_settings(tmp_path)['version'] == 3.1


def test_all_old_files_preserved_even_equal_to_current_and_restart_is_stable(tmp_path):
    with backend_at(tmp_path / 'reference') as reference:
        data = reference.theme_data('modern')
    directory = tmp_path / 'user'
    write_settings(directory, {'style_var': 'apple_maps', 'unrelated': 17})
    (directory / 'styles').mkdir()
    for name in ('apple_maps.yaml', 'same-content.yml'):
        (directory / 'styles' / name).write_text(yaml.safe_dump(data), encoding='utf-8')
    raw = (directory / 'styles/apple_maps.yaml').read_bytes()
    with backend_at(directory) as backend:
        assert not backend.theme_migration_issues
        assert len(backend.user_themes) == 2
        assert backend.theme_stem in backend.user_themes
        identities = set(backend.user_themes)
        assert all(record['base'] is None and record['data']['size_contract'] == 'pixel-v2'
                   for record in backend.user_themes.values())
        assert [item['builtin'] for item in backend.theme_choices()] == [False, False] + [True] * 7
    before = (directory / 'settings.json').read_bytes()
    with backend_at(directory) as backend:
        assert set(backend.user_themes) == identities
    assert (directory / 'settings.json').read_bytes() == before
    assert (directory / 'styles/apple_maps.yaml').read_bytes() == raw
    assert load_settings(directory)['values']['unrelated'] == 17


def test_legacy_lengths_convert_automatically_without_renaming(tmp_path):
    source = {'name': 'My historic theme', 'labels': {'road': {'size': {'fixed': 8, 'scaled': 3}}},
              'water': {'color': '#123456'}, 'extension': {'keep': [7, 11]}}
    identity = 'user_saved'
    write_settings(tmp_path, {'style_var': identity, 'user_themes': {
        identity: {'name': source['name'], 'base': None, 'data': source}}})
    with backend_at(tmp_path) as backend:
        assert not backend.theme_migration_issues
        assert backend.theme_stem == identity
        data = backend.theme_data(identity)
        assert data['name'] == source['name']
        assert isinstance(data['labels']['road']['size'], (int, float))
        assert data['water']['color'] == '#123456' and data['extension'] == source['extension']
        assert len(backend.user_themes) == 1
    assert load_settings(tmp_path)['version'] == 3.1


def test_backup_failure_preserves_source_and_version(tmp_path, monkeypatch):
    path = write_settings(tmp_path, {'style_var': 'modern'})
    raw = path.read_bytes()
    monkeypatch.setattr(GuirBackend, '_backup_theme', lambda *_: (_ for _ in ()).throw(OSError('backup denied')))
    with backend_at(tmp_path) as backend:
        assert backend._theme_migration_aborted
        assert 'backup denied' in '\n'.join(backend.theme_migration_issues)
        assert not backend.user_themes
    assert path.read_bytes() == raw
    assert load_settings(tmp_path)['version'] == 3


def test_external_file_only_theme_migrates_and_backs_up_exact_source(tmp_path):
    source = tmp_path / 'external.yaml'
    raw = b'# historic personal theme\nwater: {color: "#123456"}\n'
    source.write_bytes(raw)
    directory = tmp_path / 'user'
    identity = 'user_external'
    write_settings(directory, {'style_var': identity, 'user_themes': {
        identity: {'name': 'External personal', 'base': None, 'sourceFile': str(source)}}})
    with backend_at(directory) as backend:
        assert not backend.theme_migration_issues
        assert backend.theme_stem == identity
        assert backend.theme_data(identity)['water']['color'] == '#123456'
        assert backend.theme_data(identity)['name'] == 'External personal'
    assert source.read_bytes() == raw
    backup = next((directory / 'theme-backups').glob('settings-3.1-*'))
    inventory = json.loads((backup / 'sources.json').read_text(encoding='utf-8'))
    assert len(inventory) == 1 and inventory[0]['source'] == str(source)
    assert (backup / inventory[0]['backup']).read_bytes() == raw
    with backend_at(directory) as backend:
        assert not backend.theme_migration_issues
        assert list(backend.user_themes) == [identity]


@pytest.mark.parametrize('raw', [None, b'name: [broken', b'[]'])
def test_unavailable_external_file_only_theme_preserves_settings(tmp_path, raw):
    source = tmp_path / 'external.yaml'
    if raw is not None:
        source.write_bytes(raw)
    directory = tmp_path / 'user'
    settings = write_settings(directory, {'style_var': 'user_external', 'user_themes': {
        'user_external': {'name': 'External personal', 'base': None, 'sourceFile': str(source)}}})
    original = settings.read_bytes()
    with backend_at(directory) as backend:
        assert backend._theme_migration_aborted
        issues = '\n'.join(backend.theme_migration_issues)
        assert str(source) in issues or repr(str(source)) in issues
    assert settings.read_bytes() == original
    if raw is not None:
        assert source.read_bytes() == raw


def test_failed_commit_restores_exact_view_file(tmp_path, monkeypatch):
    path = write_settings(tmp_path, {'style_var': 'modern', 'transit_view_station_size': 7})
    old_settings = path.read_bytes()
    old_views = b'{"version":2,"transit":{"stations":{"size":9}},"metro":{}}'
    (tmp_path / 'views.json').write_bytes(old_views)
    monkeypatch.setattr(theme_migration, 'save_settings', lambda *_: False)
    with backend_at(tmp_path) as backend:
        assert backend._theme_migration_aborted
    assert path.read_bytes() == old_settings
    assert (tmp_path / 'views.json').read_bytes() == old_views
    assert not (tmp_path / theme_migration.JOURNAL).exists()


def test_same_value_or_color_alias_does_not_create_theme(tmp_path):
    with backend_at(tmp_path) as backend:
        color = backend.current_style_data()['water']['color']
        backend.apply_style_override('water.color', color)
        backend.apply_style_override('water.color', color.lower())
        assert backend.theme_stem == 'modern' and not backend.user_themes


def test_reset_defaults_keep_preset_origin_after_save_and_restart(tmp_path):
    with backend_at(tmp_path) as backend:
        preset = backend.theme_data('modern')
        personal = deepcopy(preset)
        personal['water']['color'] = '#010203'
        identity = backend.save_personal_theme(personal, identity=None, base_id='modern', name='Personal')
        assert backend.theme_reset_defaults() == preset
        assert backend.theme_data(identity)['water']['color'] == '#010203'
    with backend_at(tmp_path) as backend:
        assert backend.theme_reset_defaults(identity) == preset
        assert backend.theme_data(identity)['water']['color'] == '#010203'
        standalone = backend.save_personal_theme(personal, identity=None, base_id=None, name='Standalone')
        assert backend.theme_reset_defaults(standalone) == backend.theme_data(standalone)


def test_bad_old_file_reports_its_path_without_partial_migration(tmp_path):
    settings = write_settings(tmp_path, {'style_var': 'modern', 'unrelated': 42})
    raw = settings.read_bytes()
    styles = tmp_path / 'styles'
    styles.mkdir()
    (styles / 'first.yaml').write_text('name: First\nwater: {color: "#123456"}\n', encoding='utf-8')
    bad = styles / 'broken.yaml'
    bad.write_text('name: [broken', encoding='utf-8')
    with backend_at(tmp_path) as backend:
        assert backend._theme_migration_aborted and not backend.user_themes
        assert str(bad) in '\n'.join(backend.theme_migration_issues)
        assert backend.theme_load_error
        assert backend.set_theme('bright')
        assert not backend.theme_load_error
    assert load_settings(tmp_path)['version'] == 3
    assert load_settings(tmp_path)['values']['unrelated'] == 42
    assert bad.read_text(encoding='utf-8') == 'name: [broken'


def test_unreadable_directory_keeps_entire_original_configuration(tmp_path, monkeypatch):
    from pathlib import Path
    settings = write_settings(tmp_path, {'style_var': 'modern'})
    raw = settings.read_bytes()
    styles = tmp_path / 'styles'
    styles.mkdir()
    original = Path.rglob
    def scan(path, pattern):
        if path == styles:
            raise PermissionError('source directory denied')
        return original(path, pattern)
    monkeypatch.setattr(Path, 'rglob', scan)
    with backend_at(tmp_path) as backend:
        assert backend._theme_migration_aborted
        assert 'source directory denied' in '\n'.join(backend.theme_migration_issues)
    assert settings.read_bytes() == raw


def test_source_change_during_backup_cancels_commit(tmp_path, monkeypatch):
    settings = write_settings(tmp_path, {'style_var': 'modern'})
    raw = settings.read_bytes()
    styles = tmp_path / 'styles'
    styles.mkdir()
    source = styles / 'personal.yaml'
    source.write_text('name: Personal\n', encoding='utf-8')
    backup = GuirBackend._backup_theme
    def changed(backend, name, data):
        backup(backend, name, data)
        if name.endswith('sources.json'):
            source.write_text('name: Edited during update\n', encoding='utf-8')
    monkeypatch.setattr(GuirBackend, '_backup_theme', changed)
    with backend_at(tmp_path) as backend:
        assert backend._theme_migration_aborted
        assert 'changed during' in '\n'.join(backend.theme_migration_issues)
    assert settings.read_bytes() == raw
    assert source.read_text(encoding='utf-8') == 'name: Edited during update\n'


@pytest.mark.parametrize('committed', [False, True])
def test_interrupted_migration_restores_originals_or_keeps_completed_commit(tmp_path, committed):
    settings = write_settings(tmp_path, {'old': True})
    old_settings = settings.read_bytes()
    old_views = b'{"version":2,"transit":{},"metro":{}}'
    backup = tmp_path / 'theme-backups/settings-3.1-test'
    backup.mkdir(parents=True)
    (backup / 'settings.json').write_bytes(old_settings)
    (backup / 'views.json').write_bytes(old_views)
    current_views = b'{"version":3,"transit":{},"metro":{}}'
    (tmp_path / 'views.json').write_bytes(current_views)
    target = json.dumps({'version': 3.1, 'values': {'new': True}}, ensure_ascii=False, indent=2).encode()
    if committed:
        assert save_settings({'new': True}, tmp_path, 3.1)
    (tmp_path / theme_migration.JOURNAL).write_text(json.dumps({
        'backup': backup.name, 'originals': {'settings.json': hashlib.sha256(old_settings).hexdigest(),
                                           'views.json': hashlib.sha256(old_views).hexdigest()},
        'targetDigest': hashlib.sha256(target).hexdigest()}), encoding='utf-8')
    theme_migration.recover(tmp_path)
    assert settings.read_bytes() == (target if committed else old_settings)
    assert (tmp_path / 'views.json').read_bytes() == (current_views if committed else old_views)
    assert not (tmp_path / theme_migration.JOURNAL).exists()


def test_preset_update_is_explicit_preserves_unrelated_fields_and_keeps_identity(tmp_path, monkeypatch):
    manifest = {'schemaVersion': 1, 'revision': 'release-test', 'fromVersion': '6.7.3', 'toVersion': '6.7.4',
                'themes': {'modern': [{'path': ['water', 'color'], 'beforeExists': True,
                                      'afterExists': True, 'before': '#111111', 'after': '#224466'}]}}
    monkeypatch.setattr(theme_preset_update, 'release_manifest', lambda: manifest)
    with backend_at(tmp_path / 'reference') as reference:
        source = reference.theme_data('modern')
    source['water']['color'] = '#123456'
    source['extension'] = {'keep': [7, 11]}
    identity = 'user_saved'
    write_settings(tmp_path / 'user', {'style_var': identity, 'user_themes': {
        identity: {'name': 'Personal', 'base': None, 'data': source, 'legacyBase': 'modern'}}}, 3.1)
    with backend_at(tmp_path / 'user') as backend:
        assert backend.current_style_data()['water']['color'] == '#123456'
        notice = backend.pending_preset_update()
        assert notice and notice['changes'][0]['conflict']
        backend.acknowledge_theme_update(True, identity, notice['revision'], notice['sourceDigest'])
        assert backend.theme_stem == identity and len(backend.user_themes) == 1
        assert backend.current_style_data()['water']['color'] == '#224466'
        assert backend.current_style_data()['extension'] == source['extension']
        assert backend.pending_preset_update() is None
    with backend_at(tmp_path / 'user') as backend:
        assert backend.pending_preset_update() is None


def test_keep_preset_appearance_is_remembered_and_stale_preview_is_rejected(tmp_path, monkeypatch):
    manifest = {'schemaVersion': 1, 'revision': 'release-test', 'fromVersion': '6.7.3', 'toVersion': '6.7.4',
                'themes': {'modern': [{'path': ['water', 'color'], 'beforeExists': True,
                                      'afterExists': True, 'before': '#111111', 'after': '#224466'}]}}
    monkeypatch.setattr(theme_preset_update, 'release_manifest', lambda: manifest)
    with backend_at(tmp_path) as backend:
        data = backend.current_style_data()
        identity = backend.save_personal_theme(data, identity=None, base_id='modern', name='Personal')
        backend.user_themes[identity].pop('presetUpdateRevision')
        notice = backend.pending_preset_update()
        backend.apply_style_override('water.color', '#123456')
        with pytest.raises(ValueError, match='changed'):
            backend.acknowledge_theme_update(True, identity, notice['revision'], notice['sourceDigest'])
        notice = backend.pending_preset_update()
        backend.acknowledge_theme_update(False, identity, notice['revision'], notice['sourceDigest'])
        assert backend.current_style_data()['water']['color'] == '#123456'
        assert backend.pending_preset_update() is None
        assert backend.pending_preset_update(include_handled=True)
