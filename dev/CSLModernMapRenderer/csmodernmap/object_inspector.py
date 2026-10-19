"""生成对象检查信息并处理限定范围内的样式修改。

渲染属性仅供查看；所有可修改的目标路径都在本模块构造，客户端不能提交任意主题路径。"""
from __future__ import annotations

from copy import deepcopy
import json
import hashlib
import math
import re

from . import i18n
from .ribbon_schema import FIELDS
from .guir_backend import _baseline_1024, road_level_style_key
from .style import load_style
from ._paths import resource_dir





TYPES = {
    'road': 'ui.object_inspector.catalog.a44c7ba211', 'building': 'ui.object_inspector.catalog.5b46860a8e',
    'poi': 'ui.object_inspector.catalog.9fc7d0cf1d', 'transit_line': 'ui.object_inspector.catalog.3fb4ae5556',
    'transit_station': 'ui.object_inspector.catalog.ed867ccbf0', 'railway': 'ui.object_inspector.catalog.10b329e9a6',
    'district': 'ui.object_inspector.catalog.4f56683e73', 'zone': 'ui.object_inspector.catalog.06f15683dd',
}
LEVELS = {'highway': 'ui.object_inspector.catalog.534c553126', 'main': 'ui.object_inspector.catalog.85f71ec8da',
          'branch': 'ui.object_inspector.catalog.109688e695', 'special': 'ui.object_inspector.catalog.ede0519a4c',
          'beautification': 'ui.object_inspector.catalog.35cde54ceb'}
GROUPS = {'transit': 'ui.object_inspector.catalog.f7ccd76bf8', 'facility': 'ui.object_inspector.catalog.666dd3886c',
          'ordinary': 'ui.object_inspector.catalog.85155a34a5', 'area': 'gui.style_editor.section.zone'}
MODES = {'ground': 'ui.object_inspector.catalog.c9a0955518', 'elevated': 'ui.object_inspector.catalog.f543a15123', 'tunnel': 'ui.object_inspector.catalog.55d2d31f2a'}
CATEGORIES = {
    'residential': 'ui.object_inspector.catalog.2a8548b84f', 'commercial': 'ui.object_inspector.catalog.b1c0532855',
    'industrial': 'ui.object_inspector.catalog.af5a323677', 'industry': 'ui.object_inspector.catalog.af5a323677',
    'office': 'ui.object_inspector.catalog.7823428e45', 'hospital': 'ui.object_inspector.catalog.1f6835c0d8',
    'school': 'ui.object_inspector.catalog.dc6e2aafeb', 'university': 'ui.object_inspector.catalog.4513bba543',
    'police': 'ui.object_inspector.catalog.b76daf0239', 'fire': 'ui.object_inspector.catalog.674ddf7793',
    'fire_station': 'ui.object_inspector.catalog.4a91350aa5', 'park': 'ui.object_inspector.catalog.e852dca995',
    'metro': 'ui.object_inspector.catalog.b8466cd555', 'train': 'ui.object_inspector.catalog.97492a0e5d', 'bus': 'ui.object_inspector.catalog.0711877f34',
    'tram': 'ui.object_inspector.catalog.170c9b910c', 'monorail': 'ui.object_inspector.catalog.cf7a18690f',
    'ship': 'ui.object_inspector.catalog.92f0b57fd4', 'ferry': 'ui.object_inspector.catalog.7f48b47270', 'airport': 'ui.object_inspector.catalog.6a5347ac5f',
    'airplane': 'ui.object_inspector.catalog.7f2e47390d', 'cablecar': 'ui.object_inspector.catalog.70b41c7440',
    'trolleybus': 'ui.object_inspector.catalog.358023d968', 'helicopter': 'ui.object_inspector.catalog.341394deda',
    'station': 'ui.object_inspector.catalog.a59fe62777', 'other': 'ui.object_inspector.catalog.d2909f1647',
    'park_zone': 'ui.object_inspector.catalog.3cb53b9c23', 'district': 'ui.object_inspector.catalog.4f56683e73',
}


def readable(value):
    value = str(value or '')
    entry = CATEGORIES.get(value.lower()) or MODES.get(value.lower()) or LEVELS.get(value.lower())
    return i18n.tr(entry) if entry else value


def read_path(data, path, default=None):
    node = data
    for part in path:
        if not isinstance(node, dict) or part not in node:
            return deepcopy(default)
        node = node[part]
    return deepcopy(node)


def write_path(data, path, value):
    node = data
    for part in path[:-1]:
        node = node.setdefault(part, {})
    node[path[-1]] = deepcopy(value)


def display_color(value, fallback='#808080'):
    from PIL import ImageColor
    if isinstance(value, dict):
        value = value.get('color', fallback)
    try:
        r, g, b, a = ImageColor.getcolor(str(value), 'RGBA')
        return f'#{r:02X}{g:02X}{b:02X}' + (f'{a:02X}' if a != 255 else '')
    except (TypeError, ValueError):
        return fallback


def _array(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return []
    return value if isinstance(value, list) else []


def describe(backend, frame, context='map', options=None):
    """汇总选中对象的只读事实与可编辑样式字段。"""
    if context not in ('map', 'transit'):
        raise ValueError('Unsupported inspector context')
    props = frame.get('properties') or {}
    if not isinstance(props, dict):
        raise ValueError('Object properties must be an object')
    kind = str(frame.get('objectType') or frame.get('styleLayer') or '')
    style = backend.current_style_data()
    options = options or {}
    sections, bindings, summary = [], {}, []
    used = {'name', 'featureId'}
    type_label = i18n.tr(TYPES[kind]) if kind in TYPES else i18n.tr('ui.object_inspector.describe.efc9ada2bd')
    name = str(props.get('name') or '').strip()
    title = name or i18n.tr('ui.object_inspector.describe.67bee6acfc') + ' ' + type_label
    notice = []

    def fact(key, label, unit='', value=None):
        used.add(key)
        value = props.get(key) if value is None else value
        if value is None or value == '':
            return
        if isinstance(value, float):
            value = f'{value:,.1f}'
        elif isinstance(value, bool):
            value = i18n.tr('ui.object_inspector.fact.b5141d3d19') if value else i18n.tr('ui.object_inspector.fact.0c70665b6e')
        summary.append({'label': label, 'value': str(value) + (' ' + unit if unit else ''),
                        'kind': 'color' if key in ('color', 'poiColor') else 'text'})

    def section(key, label, scope, expanded=True):
        result = {'key': key, 'title': label, 'scope': scope, 'expanded': expanded, 'fields': []}
        sections.append(result)
        return result

    def path_field(group, key, label, path, default, field_kind='color', unit='', low=0, high=100,
                   choices=None, conversion=None):
        raw = read_path(style, path, default)
        from .preview_scene import _preview_number
        value = (_baseline_1024(raw, default) if conversion == 'label_size' else _preview_number(raw, default)) if conversion in ('size', 'label_size') else (
            float(raw) / 255 * 100 if conversion == 'alpha' else raw)
        if field_kind == 'color':
            value = display_color(value)
        bindings[key] = {'path': path, 'default': default, 'conversion': conversion}
        group['fields'].append({'key': key, 'label': label, 'kind': field_kind,
                                'value': value, 'unit': unit, 'low': low, 'high': high,
                                'choices': choices or [], 'enabled': True})

    def field(group, key, label=None, unit=''):
        meta = FIELDS[key]
        value = backend.field_value(key, meta.default, style_data=style)
        if meta.kind == 'color':
            value = display_color(value, meta.default)
        if isinstance(value, dict):
            # 复合尺寸公式由服务端管理，写回时保持原有比例。
            from .guir_backend import STYLE_FIELD_PATHS
            path_field(group, key, label or meta.label, STYLE_FIELD_PATHS[key], meta.default,
                       'number', unit or 'px', meta.low, meta.high, conversion='label_size' if key.startswith('label.size.') else 'size')
            return
        bindings[key] = {'field': key}
        group['fields'].append({'key': key, 'label': label or meta.label, 'kind': meta.kind,
                                'value': value, 'unit': unit, 'low': meta.low, 'high': meta.high,
                                'choices': [{'value': v, 'label': meta.option_label(v)} for v in meta.choices],
                                'enabled': True})

    def names(label_kind, toggle_key):
        group = section('names', i18n.tr('ui.object_inspector.names.c86c040c4c'),
                        i18n.tr('ui.object_inspector.names.2f66a7f608'), False)
        if toggle_key:
            field(group, toggle_key, i18n.tr('ui.object_inspector.names.a98585871c'))
        for attr, label in [('size', i18n.tr('ui.object_inspector.names.f71561b5af')), ('color', i18n.tr('ui.object_inspector.names.f21ef4aba5')),
                            ('outline', i18n.tr('ui.object_inspector.names.3cc1ba50ed')), ('font', i18n.tr('ui.object_inspector.names.078838da42'))]:
            field(group, f'label.{attr}.{label_kind}', label, 'px' if attr == 'size' else '')

    def option_field(group, key, label, default, field_kind='number', unit='', low=0, high=100, choices=None):
        bindings['view.' + key] = {'option': key, 'default': default}
        group['fields'].append({'key': 'view.' + key, 'label': label, 'kind': field_kind,
                                'value': options.get(key, default), 'unit': unit,
                                'low': low, 'high': high, 'choices': choices or [], 'enabled': True})

    def visibility_field(group, channel, group_key):
        visibility = backend.poi_visibility_snapshot()
        key = 'visibility.' + channel
        bindings[key] = {'visibility': (channel, group_key)}
        group['fields'].append({'key': key,
                               'label': i18n.tr(GROUPS[group_key]) + ' · ' + (i18n.tr('ui.object_inspector.visibility_field.a98585871c') if channel == 'names' else i18n.tr('ui.object_inspector.visibility_field.39e0705286')),
                               'kind': 'toggle', 'value': visibility[channel][group_key],
                               'enabled': channel == 'icons' or visibility['icons'][group_key], 'choices': []})

    layer = {'transit_line': 'metro', 'transit_station': 'metro'}.get(kind, kind)
    theme_section = {'road': 'roads', 'building': 'buildings', 'poi': 'poi_icons', 'metro': 'transports'}.get(layer, layer)
    if kind == 'road':
        level = str(props.get('level') or '').lower()
        mode = str(props.get('mode') or 'ground').lower()
        fact('level', i18n.tr('ui.object_inspector.describe.fbe666f1e0'), value=readable(level))
        fact('mode', i18n.tr('ui.object_inspector.describe.44c9e48874'), value=readable(mode))
        chain = int(props.get('segmentCount') or 1) > 1
        fact('physicalWidth', (i18n.tr('ui.object_inspector.describe.c97044edbe') if chain else i18n.tr('ui.object_inspector.describe.81a3bdaa03')), 'm')
        fact('lengthMeters', (i18n.tr('ui.object_inspector.describe.b874b614a7') if chain else i18n.tr('ui.object_inspector.describe.29941bc99d')), 'm')
        if chain:
            fact('segmentCount', i18n.tr('ui.object_inspector.describe.23fb29e3a5'))
        if level in LEVELS:
            roads = style.get('roads', {})
            # 只接受当前语义等级和模式实际使用的键。
            from .road_hierarchy import RoadLevel, road_style_candidates
            candidates = road_style_candidates(RoadLevel(level), mode)
            style_key = next((k for k in candidates if isinstance(roads.get(k), dict) and roads[k]), road_level_style_key(roads, level))
            group = section('appearance', i18n.tr(LEVELS[level]) + ' · ' + readable(mode),
                            i18n.tr('ui.object_inspector.describe.ae299c3aef'))
            path_field(group, 'road.fill', i18n.tr('ui.object_inspector.describe.54f5431ad3'), ('roads', style_key, 'fill'), '#FFFFFF')
            path_field(group, 'road.border', i18n.tr('ui.object_inspector.describe.5e3f198ad9'), ('roads', style_key, 'border'), '#C6C5C0')
            from .preview_scene import _LEVEL_MIN_FILL_PX
            path_field(group, 'road.minimum', i18n.tr('ui.object_inspector.describe.9a6361d604'),
                       ('roads', style_key, 'min_fill_px'), _LEVEL_MIN_FILL_PX[RoadLevel(level)], 'number', 'px', .2, 6)
            used.add('styleKey')
        else:
            notice.append(i18n.tr('ui.object_inspector.describe.747c2ffdcb'))
        names('road', 'road.names')
        group = section('overall', i18n.tr('ui.object_inspector.describe.047fa9d803'), i18n.tr('ui.object_inspector.describe.4c93e617e4'), False)
        for key in ('road.width', 'road.bridge', 'road.tunnel'):
            field(group, key, unit='%' if key == 'road.width' else '')
    elif kind == 'building':
        fact('category', i18n.tr('ui.object_inspector.describe.72c55cb5e7'), value=readable(props.get('category')))
        if props.get('groupMemberCount'):
            fact('groupMemberCount', i18n.tr('ui.object_inspector.describe.1d4da9cc30'))
        mode = backend.field_value('building.color_mode', 'poi', style_data=style)
        fact('colorSource', i18n.tr('ui.object_inspector.describe.dcbc3f5adb'), value=i18n.tr('ui.object_inspector.describe.9659c81207') if mode == 'uniform' else i18n.tr('ui.object_inspector.describe.1a1ef51c8d'))
        fact('poiColor', i18n.tr('ui.object_inspector.describe.4ff1d894c3'), value=backend.field_value('building.default', '#D8D2C6', style_data=style) if mode == 'uniform' else props.get('poiColor'))
        group = section('appearance', i18n.tr('ui.object_inspector.describe.2b42dd52ee'), i18n.tr('ui.object_inspector.describe.1712bbea62'))
        field(group, 'building.color_mode', i18n.tr('ui.object_inspector.describe.836cbed510'))
        field(group, 'building.default' if mode == 'uniform' else 'building.poi_tint.contrast',
              i18n.tr('ui.object_inspector.describe.711617512b') if mode == 'uniform' else i18n.tr('ui.object_inspector.describe.e55e6f2bac'), '' if mode == 'uniform' else '%')
        field(group, 'building.border', i18n.tr('ui.object_inspector.describe.5e3f198ad9'))
        if mode != 'uniform':
            group = section('fallback', i18n.tr('ui.object_inspector.describe.43f6aa3376'), i18n.tr('ui.object_inspector.describe.df6859de66'), False)
            field(group, 'building.default', i18n.tr('ui.object_inspector.describe.fbe0108a42'))
        names('building', None)
        if props.get('parentPoiId') or props.get('groupId'):
            notice.append(i18n.tr('ui.object_inspector.describe.c3cdf137f2'))
    elif kind == 'poi':
        group_key = str(props.get('visibilityGroup') or 'ordinary')
        if group_key not in GROUPS:
            group_key = 'ordinary'
        fact('category', i18n.tr('ui.object_inspector.describe.f8febafe49'), value=readable(props.get('category')))
        fact('visibilityGroup', i18n.tr('ui.object_inspector.describe.6efd17aac1'), value=i18n.tr(GROUPS[group_key]))
        statuses = {'ready': 'ui.object_inspector.describe.4d99c976be', 'pending': 'ui.object_inspector.describe.e1cfcbb06d', 'missing': 'ui.object_inspector.describe.b842da00b0',
                    'orphan': 'ui.object_inspector.describe.4317d9a490', 'hidden': 'ui.object_inspector.describe.145219e726'}
        if props.get('iconStatus') in statuses:
            fact('iconStatus', i18n.tr('ui.object_inspector.describe.ea3973e49b'), value=i18n.tr(statuses[props['iconStatus']]))
        fact('groupMemberCount', i18n.tr('ui.object_inspector.describe.c3b7111911'))
        visibility = backend.poi_visibility_snapshot()
        group = section('appearance', i18n.tr('ui.object_inspector.describe.bc8600a16e'),
                        i18n.tr('ui.object_inspector.describe.a54fa2904e'))
        field(group, 'poi.size', i18n.tr('ui.object_inspector.describe.7aeb540769'), 'px')
        visibility_field(group, 'names', group_key)
        field(group, 'poi.name_size', i18n.tr('ui.object_inspector.describe.3fbdb548d1'), 'px')
        field(group, 'poi.set', i18n.tr('ui.object_inspector.describe.c8a2d11ead'))
        group = section('names', i18n.tr('ui.object_inspector.describe.bf21786b6a'), i18n.tr('ui.object_inspector.describe.c6c54bf846'), False)
        for key in ('poi.font', 'poi.name_stroke', 'poi.name_stroke_ratio'):
            field(group, key)
        group = section('visibility', i18n.tr('ui.object_inspector.describe.e3b04e065c'), i18n.tr(GROUPS[group_key]), False)
        visibility_field(group, 'icons', group_key)
        if not visibility['icons'][group_key]:
            notice.append(i18n.tr('ui.object_inspector.describe.f7ec5c27de'))
        if props.get('iconStatus') in ('missing', 'orphan'):
            notice.append(i18n.tr('ui.object_inspector.describe.34cf4e5507'))
        profile = backend.get_poi_profile('preview')
        merge_group = str(props.get('poiGroup') or group_key)
        if merge_group not in GROUPS:
            merge_group = 'ordinary'
        group = section('layout', i18n.tr('ui.object_inspector.describe.0af02ac692'),
                        i18n.tr('ui.object_inspector.describe.1c786295e3') + i18n.tr(GROUPS[merge_group]) + i18n.tr('ui.object_inspector.describe.9fe74bef39'), False)
        def profile_field(key, label, path, field_kind='toggle', unit='', low=0, high=500, choices=None):
            bindings[key] = {'profile': path}
            group['fields'].append({'key': key, 'label': label, 'kind': field_kind,
                                    'value': read_path(profile, path), 'unit': unit,
                                    'low': low, 'high': high, 'choices': choices or [], 'enabled': True})
        profile_field('preview.density_enabled', i18n.tr('ui.object_inspector.describe.9035e1788e'), ('density_enabled',))
        profile_field('preview.density_tier', i18n.tr('ui.object_inspector.describe.aa3955f916'), ('density_tier',), 'choice', choices=[
            {'value': v, 'label': i18n.tr(label_key)} for v, label_key in [
                ('extremely_sparse', 'ui.poi_density.extremely_sparse'),
                ('very_sparse', 'ui.poi_density.very_sparse'),
                ('sparse', 'ui.object_inspector.describe.395ee8e80b'),
                ('standard', 'ui.object_inspector.describe.6bea77acef'),
                ('dense', 'ui.object_inspector.describe.638668bac9'),
                ('very_dense', 'ui.poi_density.very_dense'),
                ('extremely_dense', 'ui.poi_density.extremely_dense')]])
        profile_field('preview.overlap', i18n.tr('ui.object_inspector.describe.ead247ca08'), ('overlap_avoidance',))
        profile_field('preview.merge_all', i18n.tr('ui.object_inspector.describe.3c18993515'), ('merge_enabled',))
        if merge_group != 'area':
            profile_field('preview.merge_group', i18n.tr(GROUPS[merge_group]) + ' · ' + i18n.tr('ui.object_inspector.describe.a9f595ad22'), ('groups', merge_group, 'merge_enabled'))
        if 'distance_m' in profile['groups'].get(merge_group, {}):
            profile_field('preview.merge_distance', i18n.tr('ui.object_inspector.describe.c2ced2decf'), ('groups', merge_group, 'distance_m'), 'number', 'm')
    elif kind in ('transit_line', 'transit_station'):
        fact('kind', i18n.tr('ui.object_inspector.describe.c122178ce4'), value=readable(props.get('kind')))
        if kind == 'transit_line':
            fact('stopCount', i18n.tr('ui.object_inspector.describe.0af2dc2f96'))
            fact('lengthMeters', i18n.tr('ui.object_inspector.describe.614eb9e832'), 'm')
            fact('color', i18n.tr('ui.object_inspector.describe.a76488cf50'))
            group = section('appearance', i18n.tr('ui.object_inspector.describe.1efb861668'), i18n.tr('ui.object_inspector.describe.77007520ba'))
            kind_key = next((k for k in style.get('transports', {}).get('types', {}) if k.lower() == str(props.get('kind') or '').lower()), None)
            if context == 'transit':
                option_field(group, 'line_width', i18n.tr('ui.object_inspector.describe.e211926d3a'), 3, unit='px', low=1, high=12)
                option_field(group, 'line_label_enabled', i18n.tr('ui.object_inspector.describe.a8a9631df4'), True, 'toggle')
                option_field(group, 'line_label_size', i18n.tr('ui.object_inspector.describe.8b301292a2'), 12, unit='px', low=6, high=36)
            else:
                path = ('transports', 'types', kind_key, 'width') if kind_key else ('transports', 'width')
                base = read_path(style, ('transports', 'width'), 3)
                from .preview_scene import _preview_number
                path_field(group, 'line.width', i18n.tr('ui.object_inspector.describe.e4f5889abd') if kind_key else i18n.tr('ui.object_inspector.describe.5807d1dc81'), path, _preview_number(base, 3), 'number', 'px', .2, 12, conversion='size')
                path_field(group, 'line.names', i18n.tr('ui.object_inspector.describe.b87011f842'), ('transports', 'label', 'enabled'), True, 'toggle')
                path_field(group, 'line.name_size', i18n.tr('ui.object_inspector.describe.8b301292a2'), ('transports', 'label', 'size'), 12, 'number', 'px', 6, 36, conversion='size')
        else:
            fact('servingLineCount', i18n.tr('ui.object_inspector.describe.57baf59886'))
            ids = _array(props.get('servingLineIds'))
            if ids:
                catalog = {str(item['id']): item for item in backend.transit_line_catalog()}
                fact('servingLineIds', i18n.tr('ui.object_inspector.describe.995f11ecdc'), value=' · '.join(str(catalog.get(str(n), {}).get('name') or i18n.tr('ui.object_inspector.describe.e4a79af431') + str(n)) for n in ids[:3]) + (' …' if len(ids) > 3 else ''))
            group = section('appearance', i18n.tr('ui.object_inspector.describe.e5bd2eef37'), i18n.tr('ui.object_inspector.describe.b05f246ac9'))
            if context == 'transit':
                option_field(group, 'station_size', i18n.tr('ui.object_inspector.describe.d211657dd1'), 8, unit='px', low=4, high=24)
            else:
                field(group, 'transport.size', i18n.tr('ui.object_inspector.describe.d211657dd1'), 'px')
            visibility_field(group, 'names', 'transit')
            field(group, 'poi.name_size', i18n.tr('ui.object_inspector.describe.5910b70e80'), 'px')
            group = section('names', i18n.tr('ui.object_inspector.describe.ac41b1adda'), i18n.tr('ui.object_inspector.describe.f2c5395498'), False)
            for key in ('poi.font', 'poi.name_stroke', 'poi.name_stroke_ratio'):
                field(group, key)
            group = section('visibility', i18n.tr('ui.object_inspector.describe.4247a9c3ad'), i18n.tr('ui.object_inspector.describe.ac0bf8c3b7'), False)
            visibility_field(group, 'icons', 'transit')
    elif kind == 'railway':
        fact('railType', i18n.tr('ui.object_inspector.describe.18202c5a9d'), value=readable(props.get('railType')))
        fact('station', i18n.tr('ui.object_inspector.describe.eccafde326'))
        rail_kind = str(props.get('railType') or '').lower()
        if rail_kind in ('train', 'tram', 'metro', 'monorail', 'cablecar'):
            from .layers.railway_layer import railway_kind_config
            cfg = railway_kind_config(style.get('railway', {}), rail_kind)
            group = section('appearance', i18n.tr('ui.object_inspector.describe.2cb3f5a066'), i18n.tr('ui.object_inspector.describe.f4017deede'))
            path_field(group, 'track.color', i18n.tr('ui.object_inspector.describe.2abba61725'), ('railway', rail_kind, 'line'), cfg.get('line', '#C36E69'))
            width_key = 'station_width' if props.get('station') else 'width'
            base = cfg.get(width_key, cfg.get('width', 4.5 if props.get('station') else 2.0))
            from .preview_scene import _preview_number
            path_field(group, 'track.width', (i18n.tr('ui.object_inspector.describe.ef34af6177') if props.get('station') else i18n.tr('ui.object_inspector.describe.e58ee3c2db')), ('railway', rail_kind, width_key), _preview_number(base, 2), 'number', 'px', .2, 12, conversion='size')
            path_field(group, 'track.dash', i18n.tr('ui.object_inspector.describe.42a2766d08'), ('railway', rail_kind, 'dash'), cfg.get('dash', '#FFFFFF'))
    elif kind in ('district', 'zone'):
        fact('kind', i18n.tr('ui.object_inspector.describe.f8ddaae464'), value=readable(props.get('kind')))
        fact('zoneKind', i18n.tr('ui.object_inspector.describe.492aa3d50c'), value=readable(props.get('zoneKind')))
        ids = _array(props.get('sourceAreaIds'))
        if ids:
            fact('sourceAreaIds', i18n.tr('ui.object_inspector.describe.876c0c149d'), value=len(ids))
        group = section('appearance', i18n.tr('ui.object_inspector.describe.5d2232b103'), i18n.tr('ui.object_inspector.describe.fbd529d0c7') if kind == 'district' else i18n.tr('ui.object_inspector.describe.86a200764b'))
        if kind == 'zone':
            path_field(group, 'zone.fill', i18n.tr('ui.object_inspector.describe.54f5431ad3'), ('zone', 'fill'), '#C8C8C8')
        field(group, kind + '.color', i18n.tr('ui.object_inspector.describe.5e3f198ad9'))
        path_field(group, 'area.width', i18n.tr('ui.object_inspector.describe.a8e3637209'), (kind, 'border_width'), 1, 'number', 'px', 0, 8, conversion='size')
        field(group, kind + '.opacity', i18n.tr('ui.object_inspector.describe.6fb3f1e4e4'), '%')
        if kind == 'district':
            names('district', 'label.districts')
        else:
            group = section('names', i18n.tr(GROUPS['area']))
            visibility_field(group, 'icons', 'area')
            visibility_field(group, 'names', 'area')

    # 完整只读事实保持可用，不任意截断。
    technical, details = [], []
    detail_names = {'role': 'ui.object_inspector.describe.9c41525ce4', 'semanticLevel': 'ui.object_inspector.describe.b1349d2cb4',
                    'groupMemberCount': 'ui.object_inspector.describe.c3b7111911', 'groupRootCount': 'ui.object_inspector.describe.9f1de8eaf7',
                    'nameSource': 'ui.object_inspector.describe.e522761331', 'meanHeight': 'ui.object_inspector.describe.abd181271c',
                    'priority': 'ui.object_inspector.describe.4a0788a10b', 'drawName': 'ui.object_inspector.describe.198dd9da2d',
                    'iconKey': 'ui.object_inspector.describe.5e85b1c73c', 'iconStatus': 'ui.object_inspector.describe.fa715dea69',
                    'pathKind': 'ui.object_inspector.describe.cfcef8fa7d', 'poiColor': 'ui.object_inspector.describe.f38d46b4f4'}
    for key, value in props.items():
        if key == 'name':
            continue
        target = details if key in detail_names and key not in used else technical
        target.append({'label': i18n.tr(detail_names[key]) if key in detail_names else key,
                       'value': json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else
                       i18n.tr('ui.object_inspector.describe.b5141d3d19') if value is True else i18n.tr('ui.object_inspector.describe.0c70665b6e') if value is False else
                       str(value) if value is not None else i18n.tr('ui.object_inspector.describe.756762e293')})
    for key in ('featureId', 'layerId', 'source', 'geometryType'):
        if frame.get(key):
            technical.append({'label': key, 'value': str(frame[key])})
    if not sections:
        notice.append(i18n.tr('ui.object_inspector.describe.b6d3e078db'))
    for group in sections:
        group['canReset'] = any('field' in bindings[f['key']] or 'path' in bindings[f['key']] for f in group['fields'])
    revision = hashlib.sha256(json.dumps([backend.theme_stem, style, options,
                              backend.poi_visibility_snapshot(), backend.get_poi_profile('preview')], sort_keys=True, default=str).encode()).hexdigest()
    return {'objectType': kind, 'title': title, 'typeLabel': type_label, 'summary': summary,
            'notices': notice, 'sections': sections, 'details': details, 'technical': technical,
            'themeSection': theme_section, 'settingsRevision': revision}, bindings


def validate(field, value):
    kind = field['kind']
    if kind == 'toggle':
        if type(value) is not bool:
            raise ValueError('Expected a boolean')
    elif kind == 'number':
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not field['low'] <= value <= field['high']:
            raise ValueError('Number is outside the supported range')
    elif kind == 'color':
        if not isinstance(value, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?', value):
            raise ValueError('Invalid color')
    elif kind == 'choice':
        if value not in [c['value'] for c in field['choices']]:
            raise ValueError('Unknown choice')
    elif kind == 'font':
        from .fonts import available_font_options
        if value not in {v for v, _ in available_font_options(field['value'])}:
            raise ValueError('Unknown font')
    else:
        raise ValueError('Unsupported inspector control')
    return value


def apply(backend, frame, context, options, key=None, value=None, reset_group=None):
    model, bindings = describe(backend, frame, context, options)
    fields = {f['key']: f for s in model['sections'] for f in s['fields']}
    if reset_group is not None:
        groups = [s for s in model['sections'] if s['key'] == reset_group]
        if not groups:
            raise ValueError('Unknown inspector group')
        keys = [f['key'] for f in groups[0]['fields'] if 'visibility' not in bindings[f['key']] and 'option' not in bindings[f['key']]]
    else:
        if key not in fields or not fields[key]['enabled']:
            raise ValueError('Unavailable inspector field')
        validate(fields[key], value)
        keys = [key]
    defaults = load_style(str(resource_dir('styles') / f'{backend.theme_stem}.yaml'))
    for field_key in keys:
        binding = bindings[field_key]
        if 'field' in binding:
            v = backend.theme_default_field_value(field_key, FIELDS[field_key].default) if reset_group is not None else value
            backend.apply_style_override(field_key, v)
        elif 'path' in binding:
            path = binding['path']
            if path[0] == 'railway':
                from .layers.railway_layer import railway_kind_config
                root = backend._style_data_mut().setdefault('railway', {})
                if not root.get(path[1]):
                    root[path[1]] = deepcopy(railway_kind_config(root, path[1]))
            if reset_group is not None:
                v = defaults.get('.'.join(path), binding['default'])
            elif binding['conversion'] == 'alpha':
                v = round(value * 255 / 100)
            elif binding['conversion'] in ('size', 'label_size'):
                current = read_path(backend.current_style_data(), path)
                if isinstance(current, dict):
                    fixed = min(float(current.get('fixed', 0)), value)
                    v = {**current, 'fixed': fixed, 'scaled': (value - fixed) * (4 if binding['conversion'] == 'label_size' else 1)}
                else:
                    v = value
            else:
                v = value
            write_path(backend._style_data_mut(), path, v)
            backend._schedule_settings_save()
        elif 'visibility' in binding:
            channel, group = binding['visibility']
            snapshot = backend.poi_visibility_snapshot()
            snapshot[channel][group] = value
            backend.set_poi_visibility_snapshot(snapshot)
        elif 'option' in binding:
            backend.update_transit_options(False, {**options, binding['option']: value})
        elif 'profile' in binding:
            profile = backend.get_poi_profile('preview')
            write_path(profile, binding['profile'], value)
            backend.set_poi_profile('preview', profile)
    return model


def capture(backend, frame, context, options, key=None, reset_group=None, targets=None):
    """仅保存一次操作的修改目标，包括原始尺寸公式。"""
    if targets is None:
        model, bindings = describe(backend, frame, context, options)
        keys = [key] if reset_group is None else [f['key'] for s in model['sections']
                if s['key'] == reset_group for f in s['fields']
                if 'path' in bindings[f['key']] or 'field' in bindings[f['key']]]
    else:
        bindings = {t['key']: t['binding'] for t in targets}
        keys = list(bindings)
    changes = []
    style = backend.current_style_data()
    for k in keys:
        if k not in bindings:
            raise ValueError('Unknown inspector field')
        binding = bindings[k]
        if 'field' in binding:
            value = backend.field_value(k, FIELDS[k].default, style_data=style)
            exists = True
        elif 'path' in binding:
            node = style
            exists = True
            for part in binding['path']:
                if not isinstance(node, dict) or part not in node:
                    exists = False
                    break
                node = node[part]
            value = deepcopy(node) if exists else None
        elif 'visibility' in binding:
            channel, group = binding['visibility']
            value, exists = backend.poi_visibility_snapshot()[channel][group], True
        elif 'profile' in binding:
            value, exists = read_path(backend.get_poi_profile('preview'), binding['profile']), True
        else:
            value, exists = options.get(binding['option']), binding['option'] in options
        changes.append({'key': k, 'binding': deepcopy(binding), 'value': value, 'exists': exists})
    return changes


def restore(backend, changes, options):
    """重放服务端定义的目标，不接受客户端提交的任意主题路径。"""
    options = deepcopy(options)
    for change in changes:
        binding, value = change['binding'], change['value']
        if 'field' in binding:
            backend.apply_style_override(binding['field'], value)
        elif 'path' in binding:
            data = backend._style_data_mut()
            if change['exists']:
                write_path(data, binding['path'], value)
            else:
                parents = []
                node = data
                for part in binding['path'][:-1]:
                    parents.append((node, part))
                    node = node.setdefault(part, {})
                node.pop(binding['path'][-1], None)
                for parent, part in reversed(parents):
                    if parent.get(part) == {}:
                        parent.pop(part)
            backend._schedule_settings_save()
        elif 'visibility' in binding:
            snapshot = backend.poi_visibility_snapshot()
            channel, group = binding['visibility']
            snapshot[channel][group] = value
            backend.set_poi_visibility_snapshot(snapshot)
        elif 'profile' in binding:
            profile = backend.get_poi_profile('preview')
            write_path(profile, binding['profile'], value)
            backend.set_poi_profile('preview', profile)
        else:
            if change['exists']:
                options[binding['option']] = value
            else:
                options.pop(binding['option'], None)
            backend.update_transit_options(False, options)
    return options
