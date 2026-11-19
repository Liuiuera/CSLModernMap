"""Release patches are reviewed before changing a personal theme."""
from copy import deepcopy
import json

import pytest

from csmodernmap import guir_backend as backend_module
from csmodernmap import theme_preset_update as updates
from csmodernmap.style import load_style
from csmodernmap._paths import resource_dir
from test_theme_library_v31 import backend_at, write_settings


@pytest.fixture
def pending(tmp_path, monkeypatch):
    manifest = {'schemaVersion': 1, 'revision': 'test-release', 'fromVersion': '6.7.3', 'toVersion': '6.7.4',
                'themes': {'modern': [{'path': ['water', 'color'], 'beforeExists': True,
                    'afterExists': True, 'before': '#112233', 'after': '#224466'}]}}
    monkeypatch.setattr(updates, 'release_manifest', lambda: manifest)
    data = load_style(str(resource_dir('styles') / 'modern.yaml')).data
    data['water']['color'] = '#123456'
    data['extension'] = {'keep': [7, 11]}
    write_settings(tmp_path, {'style_var': 'user_personal', 'unrelated': 42, 'user_themes': {
        'user_personal': {'name': 'Personal', 'base': None, 'data': data, 'legacyBase': 'modern'}}}, 3.1)
    return tmp_path, manifest, data


def decide(backend, apply, identity=None):
    notice = backend.pending_preset_update(identity, include_handled=True)
    backend.acknowledge_theme_update(apply, notice['theme'], notice['revision'], notice['sourceDigest'])


def test_restart_does_not_apply_preset_or_create_archived_choice(pending):
    directory, _, original = pending
    for _ in range(2):
        with backend_at(directory) as backend:
            assert backend.theme_stem == 'user_personal'
            assert backend.current_style_data()['water']['color'] == original['water']['color']
            assert len(backend.user_themes) == 1
            assert backend.pending_preset_update()['changes'][0]['conflict']


@pytest.mark.parametrize('apply', [True, False])
def test_choice_is_saved_once_without_switching_identity(pending, apply):
    directory, _, original = pending
    with backend_at(directory) as backend:
        decide(backend, apply)
        assert backend.theme_stem == 'user_personal' and len(backend.user_themes) == 1
        assert backend.current_style_data()['water']['color'] == ('#224466' if apply else original['water']['color'])
        assert backend.current_style_data()['extension'] == original['extension']
        assert backend.pending_preset_update() is None
    with backend_at(directory) as backend:
        assert backend.pending_preset_update() is None
        assert (backend.pending_preset_update(include_handled=True) is not None) == (not apply)
    assert json.loads((directory / 'settings.json').read_text(encoding='utf-8'))['values']['unrelated'] == 42
    assert bool(list((directory / 'theme-backups').glob('preset-*.yaml'))) == apply


def test_save_failure_preserves_original_settings_and_pending_notice(pending, monkeypatch):
    directory, _, original = pending
    with backend_at(directory) as backend:
        backend.save_settings_now()
        raw = (directory / 'settings.json').read_bytes()
        monkeypatch.setattr(backend_module, 'save_settings', lambda *_: False)
        with pytest.raises(OSError):
            decide(backend, True)
        assert backend.pending_preset_update()
        assert backend.current_style_data()['water']['color'] == original['water']['color']
        assert (directory / 'settings.json').read_bytes() == raw


def test_inactive_theme_can_be_reviewed_without_changing_current_theme(pending):
    directory, _, _ = pending
    with backend_at(directory) as backend:
        backend.set_theme('bright')
        assert backend.pending_preset_update() is None
        decide(backend, True, 'user_personal')
        assert backend.theme_stem == 'bright'
        assert backend.theme_data('user_personal')['water']['color'] == '#224466'


def test_already_equal_and_unrelated_custom_fields_do_not_prompt(pending):
    directory, _, _ = pending
    with backend_at(directory) as backend:
        backend.apply_style_override('water.color', '#224466')
        assert backend.pending_preset_update() is None
        assert backend.current_style_data()['extension'] == {'keep': [7, 11]}


def test_next_release_can_offer_new_changes_after_keep(pending):
    directory, manifest, _ = pending
    with backend_at(directory) as backend:
        decide(backend, False)
        manifest['revision'] = 'next-release'
        manifest['themes']['modern'][0]['after'] = '#556677'
        notice = backend.pending_preset_update()
        assert notice and notice['changes'][0]['after'] == '#556677'


def test_known_source_release_uses_its_cumulative_changes(pending):
    directory, manifest, _ = pending
    cumulative = {'modern': updates.release_changes({'canvas': {'background': '#FFFFFF'}},
                                                     {'canvas': {'background': '#ABCDEF'}})}
    with backend_at(directory) as backend:
        decide(backend, False)
        manifest.update(fromVersion='6.7.5', toVersion='6.7.6', revision='newer-release',
                        history={'6.7.4': {'themes': cumulative}})
        notice = backend.pending_preset_update()
        assert notice['fromVersion'] == '6.7.4'
        assert notice['changes'][0]['path'] == ['canvas', 'background']
        decide(backend, False)
        assert backend.pending_preset_update(include_handled=True)['fromVersion'] == '6.7.4'


def test_release_patch_handles_deletion_null_and_arrays_without_touching_other_values(pending):
    _, _, original = pending
    before = {'extension': {'deleted': 1, 'nullable': 1, 'array': [1, 2]}, 'name': 'Old'}
    after = {'extension': {'nullable': None, 'array': [3, 4]}, 'name': 'New'}
    changes = updates.release_changes(before, after)
    assert len(changes) == 3
    original['extension'] = {**before['extension'], 'keep': True}
    result = updates.apply_changes(original, changes)
    assert result['extension'] == {**after['extension'], 'keep': True}
    assert original['extension']['deleted'] == 1


def test_current_shipped_release_manifest_only_contains_actual_published_theme_origins():
    data = json.loads((resource_dir('styles') / 'presets.updates.json').read_text(encoding='utf-8'))
    assert set(data['themes']) == {'modern', 'bright', 'dark', 'warm_natural', 'vintage'}
    assert data['fromVersion'] == '6.7.3' and data['toVersion'] == '6.7.4'
    assert data['sourceRevision'].startswith('e0d7044')
    assert all(change['path'][0] != 'size_contract' for changes in data['themes'].values() for change in changes)
