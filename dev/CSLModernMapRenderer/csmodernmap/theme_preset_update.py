"""按随包发行差异预览、应用预设变化，不在启动时修改个人外观。"""
from __future__ import annotations

import copy
import json

from ._paths import resource_dir
from .pixel_sizes import validate
from .theme_library import content_digest, comparison_value

MISSING = object()


def release_manifest():
    data = json.loads((resource_dir('styles') / 'presets.updates.json').read_text(encoding='utf-8'))
    if data.get('schemaVersion') != 1:
        raise ValueError('Unsupported preset update manifest')
    return data


def value_at(data, path):
    for key in path:
        if not isinstance(data, dict) or key not in data:
            return MISSING
        data = data[key]
    return data


def release_changes(previous, current, path=()):
    changes = []
    if isinstance(previous, dict) and isinstance(current, dict):
        for key in sorted(previous.keys() | current.keys()):
            if not path and key in ('name', 'description'):
                continue
            changes.extend(release_changes(previous.get(key, MISSING), current.get(key, MISSING), (*path, key)))
    elif comparison_value(previous, path) != comparison_value(current, path):
        changes.append({'path': list(path), 'beforeExists': previous is not MISSING,
                        'afterExists': current is not MISSING,
                        'before': None if previous is MISSING else previous,
                        'after': None if current is MISSING else current})
    return changes


def preview(backend, identity=None, *, include_handled=False):
    identity = identity or backend.theme_stem
    record = backend.user_themes.get(identity)
    if not record or not record.get('legacyBase'):
        return None
    if backend._preset_updates is None:
        backend._preset_updates = release_manifest()
    manifest = backend._preset_updates
    handled = record.get('presetUpdateRevision') == manifest['revision']
    if handled and not include_handled:
        return None
    source_version = record.get('presetUpdateFromVersion' if handled else 'presetUpdateVersion')
    history = manifest.get('history', {})
    if source_version not in history:
        source_version = manifest['fromVersion']
    release = history.get(source_version, manifest)
    data = backend.current_style_data() if identity == backend.theme_stem else backend.theme_data(identity)
    changes = []
    for change in release['themes'].get(record['legacyBase'], []):
        current = value_at(data, change['path'])
        canonical = comparison_value(current, tuple(change['path']))
        desired = change['after'] if change['afterExists'] else MISSING
        if canonical == comparison_value(desired, tuple(change['path'])):
            continue
        changes.append({**change, 'before': None if current is MISSING else copy.deepcopy(current),
                        'beforeExists': current is not MISSING,
                        'conflict': canonical != comparison_value(change['before'] if change['beforeExists'] else MISSING, tuple(change['path']))})
    if not changes:
        return None
    return {'theme': identity, 'name': record['name'], 'preset': record['legacyBase'],
            'revision': manifest['revision'], 'fromVersion': source_version,
            'toVersion': manifest['toVersion'], 'sourceDigest': content_digest(data),
            'changes': changes, 'preserved': []}


def apply_changes(data, changes):
    result = copy.deepcopy(data)
    for change in changes:
        parent = result
        for key in change['path'][:-1]:
            if key not in parent:
                parent[key] = {}
            if not isinstance(parent[key], dict):
                raise ValueError('Preset update path no longer refers to an object')
            parent = parent[key]
        key = change['path'][-1]
        if change['afterExists']:
            parent[key] = copy.deepcopy(change['after'])
        else:
            parent.pop(key, None)
    validate(result)
    return result
