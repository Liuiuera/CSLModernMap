"""配置 3.1 的一次性主题接收、像素转换和提交恢复。"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path

import yaml

from ._paths import resource_dir
from .pixel_sizes import validate
from .settings_store import SETTINGS_VERSION, save_settings, settings_bytes
from .style import Style, canonical_theme_id
from .theme_library import make_record, new_id, resolve, unique_name, validate_name
from .theme_size_update import plan_update, include_view_sizes
from .view_settings import save_view_settings

JOURNAL = 'theme-migration-3.1.pending.json'


def convert_theme(data, source_id, view_sizes=None):
    calibration = json.loads((resource_dir('resources') / 'theme-sizing-calibration.json').read_text(encoding='utf-8'))
    plan = plan_update(data, calibration, source_id=source_id)
    if view_sizes:
        plan = include_view_sizes(plan, data, calibration, view_sizes)
    return Style(plan['theme']).data


def _publish(path, raw):
    temporary = path.with_name(path.name + '.migration.tmp')
    try:
        temporary.write_bytes(raw)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def recover(directory):
    directory = Path(directory)
    journal = directory / JOURNAL
    if not journal.is_file():
        return
    transaction = json.loads(journal.read_text(encoding='utf-8'))
    backup = (directory / 'theme-backups' / transaction['backup']).resolve()
    if backup.parent != (directory / 'theme-backups').resolve() or not backup.name.startswith('settings-3.1-'):
        raise ValueError('Invalid theme migration backup directory')
    settings = directory / 'settings.json'
    committed = settings.is_file() and hashlib.sha256(settings.read_bytes()).hexdigest() == transaction['targetDigest']
    if not committed:
        for name in ('views.json', 'settings.json'):
            target = directory / name
            if transaction['originals'][name]:
                raw = (backup / name).read_bytes()
                if hashlib.sha256(raw).hexdigest() != transaction['originals'][name]:
                    raise ValueError(f'Theme migration backup changed: {name}')
                _publish(target, raw)
            elif name == 'views.json':
                target.unlink(missing_ok=True)
    journal.unlink()


def _sources(backend):
    current = resource_dir('styles').resolve()
    roots = [backend._settings_dir / 'styles', backend._settings_dir / 'legacy-themes']
    explicit = json.loads(os.environ.get('CSLMM_THEME_SOURCE_DIRS', '[]'))
    if not isinstance(explicit, list) or any(not isinstance(root, str) for root in explicit):
        raise ValueError('CSLMM_THEME_SOURCE_DIRS: expected a list of directories')
    roots.extend(Path(root) / 'styles' for root in explicit)
    for root in dict.fromkeys(root.resolve() for root in roots):
        if root == current or root.is_relative_to(current) or not root.is_dir():
            continue
        for path in sorted(root.rglob('*')):
            if path.suffix.lower() in ('.yaml', '.yml') and path.stem != 'metro_map':
                yield path


def migrate(backend):
    from .guir_backend import _theme_stem_for
    directory = backend._settings_dir
    source_settings = directory / 'settings.json'
    paths = list(_sources(backend)) if backend._settings['version'] < SETTINGS_VERSION or not source_settings.exists() else []
    if backend._settings['version'] == SETTINGS_VERSION and not paths and not backend._view_settings.get('migration', {}).get('theme_sizes'):
        for record in backend.user_themes.values():
            validate(record['data'])
        return
    if paths and not source_settings.exists():
        backend._settings['version'] = 3

    values = copy.deepcopy(backend._settings['values'])
    records = copy.deepcopy(backend.user_themes)
    imports = copy.deepcopy(backend._theme_file_imports)
    names = [record['name'] for record in records.values()]
    selected = str(values.get('style_var', 'modern')).split('·', 1)[0].strip()
    selected_stem = _theme_stem_for(selected) or selected
    builtins = {item['id'] for item in backend.theme_choices() if item['builtin']}
    originals = {name: (directory / name).read_bytes() if (directory / name).is_file() else None
                 for name in ('settings.json', 'views.json')}
    source_bytes = {}
    selected_sources = {}

    for identity, record in list(records.items()):
        successor = backend.user_themes.get(record.get('upgradeTo'))
        if (identity != selected and identity.startswith('user_previous_')
                and successor and successor.get('previousTheme') == identity):
            del records[identity]
            continue
        base = record.get('base')
        baseline = record.get('presetData') if base else None
        if base and not isinstance(baseline, dict):
            raise ValueError(f"{record['name']}: missing original preset baseline")
        if base is None and 'data' not in record:
            path = Path(record['sourceFile']).resolve()
            raw = path.read_bytes()
            try:
                data = yaml.safe_load(raw.decode('utf-8-sig'))
            except (UnicodeError, yaml.YAMLError) as error:
                raise ValueError(f'{path}: {error}') from error
            if not isinstance(data, dict):
                raise ValueError(f'{path}: expected a theme object')
            source_bytes[str(path)] = raw
            data['name'] = record['name']
        else:
            data = resolve(record, baseline)
        updated = make_record(record['name'], data, None, None)
        for key in ('sourceFile', 'sourceSetting', 'legacyBase', 'presetUpdateRevision', 'presetUpdateDecision',
                    'presetUpdateVersion', 'presetUpdateFromVersion'):
            if key in record:
                updated[key] = record[key]
        if base:
            updated['legacyBase'] = base
        records[identity] = updated
        if updated.get('sourceFile'):
            updated['sourceFile'] = str(Path(updated['sourceFile']).resolve())
            imports.setdefault(updated['sourceFile'], {'theme': identity})

    for stem, data in values.get('guir_style_overrides', {}).items():
        if not isinstance(data, dict):
            raise ValueError(f'{stem}: invalid historic theme data')
        preset = _theme_stem_for(stem)
        identity = 'user_legacy_' + (preset or hashlib.sha256(stem.encode()).hexdigest())
        existing = next((key for key, record in records.items() if record.get('sourceSetting') == stem), None)
        if preset and existing is None and identity in records and 'sourceSetting' not in records[identity]:
            existing = identity
        if existing:
            identity = existing
            records[identity]['sourceSetting'] = stem
        else:
            if identity in records:
                identity = new_id()
            name = unique_name(str(data.get('name') or stem), names)
            names.append(name)
            records[identity] = make_record(name, data, None, None)
            records[identity]['sourceSetting'] = stem
            if preset in builtins:
                records[identity]['legacyBase'] = preset
        selected_sources[stem] = identity
        selected_sources.setdefault(preset or stem, identity)

    for path in paths:
        key = str(path.resolve())
        raw = path.read_bytes()
        try:
            data = yaml.safe_load(raw.decode('utf-8-sig'))
        except (UnicodeError, yaml.YAMLError) as error:
            raise ValueError(f'{path}: {error}') from error
        if not isinstance(data, dict):
            raise ValueError(f'{path}: expected a theme object')
        source_bytes[key] = raw
        identity = imports.get(key, {}).get('theme')
        if identity not in records or records[identity].get('sourceFile') != key:
            identity = new_id()
            name = unique_name(str(data.get('name') or path.stem), names)
            names.append(name)
            records[identity] = make_record(name, data, None, None)
            records[identity]['sourceFile'] = key
            preset = canonical_theme_id(path.stem)
            if preset in builtins:
                records[identity]['legacyBase'] = preset
        imports[key] = {'theme': identity, 'sha256': hashlib.sha256(raw).hexdigest()}
        selected_sources.setdefault(canonical_theme_id(path.stem), identity)

    if selected not in records:
        selected = selected_sources.get(selected, selected_sources.get(selected_stem, selected_stem))
    if selected not in records and selected not in builtins:
        raise ValueError(f'Unknown historic selected theme: {selected}')
    views = copy.deepcopy(backend._view_settings)
    view_sizes = views.get('migration', {}).get('theme_sizes', {})
    if view_sizes and selected not in records:
        if selected not in builtins:
            raise ValueError(f'Unknown historic selected theme: {selected}')
        data = backend.theme_data(selected)
        identity = new_id()
        name = unique_name(str(data['name']), names)
        records[identity] = make_record(name, data, None, None)
        records[identity]['legacyBase'] = selected
        selected = identity
    for identity, record in records.items():
        try:
            record['data'] = convert_theme(record['data'], identity, view_sizes if identity == selected else None)
            record['data']['name'] = validate_name(record['name'])
        except (ValueError, TypeError, KeyError) as error:
            raise ValueError(f"{record['name']}: {error}") from error
    views.get('migration', {}).pop('theme_sizes', None)
    values.update(user_themes=records, theme_file_imports=imports, style_var=selected)
    for key in ('guir_style_overrides', 'theme_library_version', 'theme_update_notice'):
        values.pop(key, None)

    inputs = {**{name: raw for name, raw in originals.items() if raw is not None}, **source_bytes}
    signature = hashlib.sha256(json.dumps({name: hashlib.sha256(raw).hexdigest() for name, raw in inputs.items()}, sort_keys=True).encode()).hexdigest()
    backup_name = 'settings-3.1-' + signature
    directory.mkdir(parents=True, exist_ok=True)
    for name, raw in originals.items():
        if raw is not None:
            backend._backup_theme(backup_name + '/' + name, raw)
    inventory = []
    for index, (name, raw) in enumerate(source_bytes.items()):
        target = f'theme-{index}.yaml'
        backend._backup_theme(backup_name + '/' + target, raw)
        inventory.append({'source': name, 'backup': target, 'sha256': hashlib.sha256(raw).hexdigest()})
    backend._backup_theme(backup_name + '/sources.json', json.dumps(inventory, ensure_ascii=False, indent=2).encode('utf-8'))
    for name, raw in originals.items():
        path = directory / name
        if (path.read_bytes() if path.exists() else None) != raw:
            raise ValueError(f'{name} changed during theme migration')
    if any(Path(name).read_bytes() != raw for name, raw in source_bytes.items()):
        raise ValueError('Source theme changed during migration')
    target = settings_bytes(values, SETTINGS_VERSION)
    journal = {'backup': backup_name, 'originals': {name: hashlib.sha256(raw).hexdigest() if raw is not None else None for name, raw in originals.items()},
               'targetDigest': hashlib.sha256(target).hexdigest()}
    _publish(directory / JOURNAL, json.dumps(journal).encode('utf-8'))
    try:
        if view_sizes and not save_view_settings(views, directory):
            raise OSError('Could not save converted view settings')
        if not save_settings(values, directory, SETTINGS_VERSION):
            raise OSError('Could not save configuration 3.1')
    except (OSError, ValueError):
        recover(directory)
        raise
    (directory / JOURNAL).unlink()
    backend.user_themes = records
    backend._theme_file_imports = imports
    backend._view_settings = views
    backend._settings = {'version': SETTINGS_VERSION, 'values': values}
    backend.theme_stem = selected
    backend.theme_load_error = ''
