"""显式更新旧主题；旧单位仅在此读取，提交采用完整目录原子发布。"""
from __future__ import annotations

import copy
from fnmatch import fnmatchcase
import hashlib
import json
import math
import os
import re
from pathlib import Path
import shutil
import tempfile

from . import pixel_sizes as pixels
from .export_region import output_dimensions
from .legacy_theme_sizes import _resolve_size
from .renderer import _build_transform
from .road_hierarchy import RoadLevel, road_style_candidates, road_style_config
from .legacy_theme_sizes import normalize_theme, ROAD_DEFAULTS, ROAD_MIN_FILL, ROAD_WIDTH_SCALE, QUAY_FOOTPRINT_SCALE

UPDATE_VERSION = 1
LEGACY_RESOLVER = 'legacy-sizing-20261117'


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def validate_calibration(value):
    if not isinstance(value, dict) or type(value.get('schemaVersion')) is not int or value['schemaVersion'] != 1:
        raise ValueError('Unsupported theme sizing calibration')
    if value.get('longEdge') != 4096 or value.get('rotation') not in (0, 90, 180, 270):
        raise ValueError('Calibration requires long edge 4096 and an explicit rotation')
    if value.get('padding') != 0:
        raise ValueError('Calibration requires explicit zero padding')
    if value.get('mapKind') not in ('normal', 'transit') or value.get('scope') != 'city':
        raise ValueError('Calibration requires an explicit full-city map kind')
    for key in ('sourceSha256', 'id', 'fonts', 'contentSettings', 'roads'):
        if not value.get(key):
            raise ValueError(f'Calibration is missing {key}')
    for key in ('fonts', 'contentSettings', 'roads'):
        if not isinstance(value[key], dict):
            raise ValueError(f'Calibration {key} must be an object')
    if not isinstance(value['sourceSha256'], str) or not re.fullmatch('[0-9a-f]{64}', value['sourceSha256']):
        raise ValueError('Calibration sourceSha256 must identify the original map')
    bounds = value.get('bounds')
    if (not isinstance(bounds, (list, tuple)) or len(bounds) != 4
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in bounds)
            or bounds[2] <= bounds[0] or bounds[3] <= bounds[1]):
        raise ValueError('Calibration requires finite full-city bounds')
    dims = output_dimensions(bounds, 4096, value['rotation'])
    for level in RoadLevel:
        row = value['roads'].get(level.value)
        if not isinstance(row, dict) or type(row.get('id')) is not int:
            raise ValueError(f'Calibration is missing the registered {level.value} road sample')
        pixels.number(row.get('width'), f'calibration.roads.{level.value}.width', positive=True)
    return dims, _build_transform(*dims, value['bounds'], value['rotation']).zoom


def _legacy_defaults(data):
    from .legacy_theme_sizes import railway_kind_config, _size_parts, TRANSIT_DEFAULTS
    out = normalize_theme(data)
    from .legacy_theme_sizes import SECTION_DEFAULTS, TERRAIN_DEFAULTS
    out.setdefault('canvas', copy.deepcopy(SECTION_DEFAULTS['canvas']))
    out.setdefault('terrain', copy.deepcopy(TERRAIN_DEFAULTS))
    for section in ('buildings', 'procedural_objects', 'district', 'zone', 'watermark'):
        from .legacy_theme_sizes import SECTION_DEFAULTS
        cfg = out.setdefault(section, {})
        for key, value in SECTION_DEFAULTS[section].items():
            cfg.setdefault(key, copy.deepcopy(value))
    labels = out.setdefault('labels', {})
    for kind, size in (('road', 16), ('district', 20)):
        cfg = labels.setdefault(kind, {})
        cfg.setdefault('size', size)
        cfg.setdefault('stroke_width', 2)
        cfg.setdefault('stroke_blur', 0)
    labels.setdefault('road_repeat', 400)
    labels.setdefault('padding', 4)
    poi = out.setdefault('poi_icons', {})
    for key, value in {'icon_size': 22, 'bg_circle_size': 30,
                       'name_size': {'fixed': 11, 'scaled': 2},
                       'name_offset': 3, 'grid_size': 90, 'icon_gap': 3,
                       'symbol_padding': 2, 'name_padding': 4}.items():
        poi.setdefault(key, copy.deepcopy(value))
    transport = out['transports']
    transport.setdefault('enabled', list(TRANSIT_DEFAULTS))
    for kind, defaults in TRANSIT_DEFAULTS.items():
        cfg = transport.setdefault('types', {}).setdefault(kind, {})
        cfg.setdefault('width', copy.deepcopy(transport.get('width', defaults['width'])))
        cfg.setdefault('dash', list(defaults['dash']))
    station = transport.setdefault('station', {})
    for key, value in {'size': {'fixed': 2.5, 'scaled': 5}, 'border_width': 1,
                       'tick_height': {'fixed': 4, 'scaled': 4}, 'transfer_halo': 2}.items():
        station.setdefault(key, copy.deepcopy(value))
    names = station.setdefault('names', station.pop('station_names', {}))
    for key, value in {'size': names.get('font_size', 11), 'gap': 5,
                       'stroke_width': 1 if names.get('halo', True) else 0}.items():
        names.setdefault(key, value)
    badge = transport.setdefault('label', {})
    for key, value in {'size': 9, 'padding': 6, 'border_width': 1.2,
                       'repeat_interval': 600, 'min_gap': 350, 'capture_padding': 3,
                       'title_size': {'fixed': 16, 'scaled': 12},
                       'gap': {'fixed': 6, 'scaled': 4},
                       'column_gap': {'fixed': 18, 'scaled': 12},
                       'swatch_width': {'fixed': 28, 'scaled': 20},
                       'swatch_height': {'fixed': 10, 'scaled': 8},
                       'border_radius': {'fixed': 6, 'scaled': 3},
                       'margin': {'fixed': 14, 'scaled': 14},
                       'shadow_blur': {'fixed': 4, 'scaled': 5},
                       'shadow_offset': {'fixed': 2, 'scaled': 2}}.items():
        badge.setdefault(key, value)
    view = transport.setdefault('transit_view', {})
    for key, value in {'label_size': {'fixed': 0, 'scaled': 60},
                       'label_merge_dist': 40, 'label_candidate_interval': 110,
                       'label_clearance_padding': 14}.items():
        view.setdefault(key, value)
    transport.setdefault('line', {}).setdefault('light_outline', {}).setdefault(
        'width', {'fixed': 1.2, 'scaled': 1.2})
    water = out['watermark']
    for group, values in (('margin', (20, 20)), ('padding', (12, 6))):
        cfg = water.setdefault(group, {})
        for key, value in zip(('x', 'y'), values):
            cfg.setdefault(key, value)
    railway = out.setdefault('railway', {})
    for kind in ('train', 'tram', 'metro', 'monorail', 'cablecar'):
        cfg = copy.deepcopy(railway_kind_config(railway, kind))
        station_width = cfg.get('station_width') or cfg.get('width')
        for key, fixed, scaled in (('width', 2, 1.2), ('station_width', 4.5, 2.7),
                                   ('dash_width', .8, 1.2)):
            raw = station_width if key == 'station_width' else cfg.get(key)
            cfg[key] = _size_parts(raw, fixed, scaled)
        if cfg.get('decoration_span'):
            for key, fixed, scaled in (('decoration_span', 2, 25), ('decoration_length', 1, 1),
                                       ('decoration_width', .6, .6)):
                cfg[key] = _size_parts(cfg.get(key), fixed, scaled)
        railway[kind] = cfg
    return out


def plan_update(source, calibration, *, source_id, overrides=None):
    """预检是纯数据转换；覆盖必须显式传入并记录，不读取活动界面。"""
    if not isinstance(source, dict):
        raise ValueError('Theme data must be an object')
    if pixels.is_pixel_theme(source):
        from .style import _deep_merge
        effective = copy.deepcopy(source)
        _deep_merge(effective, overrides or {})
        pixels.validate(effective)
        return {'status': 'current', 'theme': effective}
    dims, zoom = validate_calibration(calibration)
    from .style import _deep_merge
    effective = copy.deepcopy(source)
    _deep_merge(effective, overrides or {})
    if 'preview_sizing' in effective.get('transports', {}):
        raise ValueError('transports.preview_sizing is transient view data; provide explicit source theme lengths for conversion')
    if 'elementScale' in effective:
        raise ValueError('elementScale belongs to the draft, not the source theme')
    for path, raw in pixels.walk(effective):
        known = pixels.field_rule(path) or path in ('roads.width_scale', 'transports.station.size',
                                                    'transports.station.diameter_per_1k')
        if known:
            if isinstance(raw, dict):
                for key in ('fixed', 'scaled'):
                    pixels.number(raw.get(key, 0), f'{path}.{key}',
                                  signed=any(fnmatchcase(path, p) for p in pixels.SIGNED_FIELDS))
            else:
                pixels.number(raw, path, signed=any(fnmatchcase(path, p) for p in pixels.SIGNED_FIELDS))
    data = _legacy_defaults(effective)
    mappings = []

    def record(path, old, new, rule, target=None):
        pixels.put(data, target or path, new)
        mappings.append({'source': path, 'target': target or path, 'old': copy.deepcopy(old),
                         'value': new, 'rule': rule,
                         'origin': 'effective-input' if _has(effective, path) else 'legacy-default'})

    roads = data.setdefault('roads', {})
    original_roads = copy.deepcopy(roads)
    wscale = pixels.number(roads.get('width_scale', ROAD_WIDTH_SCALE), 'roads.width_scale')
    for level in RoadLevel:
        sample = calibration['roads'][level.value]
        for mode in ('ground', 'elevated', 'tunnel'):
            key = road_style_candidates(level, mode)[0]
            if mode != 'ground' and key not in original_roads:
                continue
            cfg = roads[key] = copy.deepcopy(road_style_config(original_roads, level, mode))
            for field, default in ROAD_DEFAULTS.items():
                cfg.setdefault(field, default)
            minimum = pixels.number(cfg.pop('min_fill_px', ROAD_MIN_FILL[level]), f'roads.{key}.min_fill_px')
            # 与 RoadLayer._fill_width 的旧分支一致，Quay 旧分支不取像素下限。
            ratio = float(original_roads.get('quay', {}).get('footprint_scale', QUAY_FOOTPRINT_SCALE)) if level is RoadLevel.QUAY else 1.0
            width = (sample['width'] * _build_transform(*dims, calibration['bounds'], calibration['rotation']).scale * ratio
                     if level is RoadLevel.QUAY else max(.5 + sample['width'] * wscale * zoom, minimum))
            record(f'roads.{key}.line_width', {'sample': sample, 'width_scale': wscale,
                   'min_fill_px': minimum}, width, 'registered-road-sample')
            record(f'roads.{key}.footprint_scale', ratio, ratio, 'footprint-ratio')
            cfg.pop('width', None)
    roads.pop('width_scale', None)

    transport = data['transports']
    station = transport['station']
    raw = station.pop('size')
    radius = _resolve_size(raw, zoom)
    if 'diameter_per_1k' in station:
        raw = station.pop('diameter_per_1k')
        radius = float(raw) * min(dims) / 2048
    record('transports.station.size', raw, 2 * radius, 'base-radius-to-diameter',
           'transports.station.diameter')
    station['names'].pop('font_size', None)
    station['names'].pop('transfer_size_scale', None)
    for key in ('transfer_height_scale', 'transfer_size_scale'):
        station.pop(key, None)
    removed = {}
    for key in ('transfer_scale', 'transfer_per_line', 'transfer_max_scale', 'transfer_halo',
                'tick_height', 'ticks', 'circle_stroke', 'transfer_design', 'transfer_arrow'):
        if key in station:
            removed[f'transports.station.{key}'] = station.pop(key)
    view = transport.get('transit_view', {})
    transit_kind = calibration['mapKind'] == 'transit'
    width_ratio = float(view.pop('width_scale', 1.7)) if transit_kind else 1
    if not transit_kind:
        view.pop('width_scale', None)
    poi_ratio = max(1.05, min(float(data['poi_icons'].pop('transit_view_scale', 1.15)), 2)) if transit_kind else 1
    data['poi_icons'].pop('transit_view_scale', None)
    transport.pop('fit_preview', None)
    transport.pop('preview_sizing', None)

    for path, raw in list(pixels.walk(data)):
        rule = pixels.field_rule(path)
        if rule is None:
            if isinstance(raw, dict):
                raise ValueError(f'{path}: missing a documented legacy length conversion')
            continue
        if path.endswith('.line_width') or path == 'transports.station.diameter':
            continue
        at = min(dims)/4096 if rule == 'watermark' else .25 if rule in ('decoration', 'badge') else zoom
        value = _resolve_size(raw, at)
        if rule == 'world-distance':
            value *= zoom
        elif rule == 'decoration':
            value *= min(dims)/1024
        elif rule == 'badge':
            value *= 4 if transit_kind else 1
        if path.startswith('transports.types.') and path.endswith('.width'):
            value *= width_ratio
        if transit_kind and path.startswith('poi_icons.') and path.rsplit('.', 1)[-1] in (
                'icon_size', 'bg_circle_size', 'name_size', 'name_offset', 'icon_max_size', 'name_max_size'):
            value *= poi_ratio
        if path == 'labels.district.size':
            value = max(value, 12 * min(zoom, 1))
        if path == 'watermark.font_size':
            value = max(round(value), 6)
        if path == 'transports.station.border_width':
            value = max(int(value*2), 1)
        if transit_kind and path.endswith(('.names.size', '.name_size', '.transit_name_size')):
            value = max(round(value), 1)
        record(path, raw, value, rule)
    road_labels = data['labels']['road']
    multipliers = road_labels.pop('size_z', {})
    for level, alias, default in (('highway', 'highway', 1.6), ('main', 'road_wide', 1.25),
            ('branch', 'road', 1), ('special', 'walk', .75),
            ('beautification', 'walk', .75), ('quay', 'walk', .75)):
        ratio = pixels.number(multipliers.get(level, multipliers.get(alias, default)),
                              f'labels.road.size_z.{level}')
        if ratio == 1 and road_labels['size'] >= 4 and road_labels['stroke_width'] >= .5:
            continue
        for key, minimum in (('size', 4), ('stroke_width', .5)):
            record(f'labels.road.size_z.{level}', ratio, max(road_labels[key]*ratio, minimum),
                   'road-class-length', f'labels.road.by_level.{level}.{key}')
    root_stroke_ratio = data['poi_icons'].get('name_stroke_ratio', .12)
    for owner in (None, 'building', 'transit'):
        cfg = data['poi_icons'] if owner is None else data['poi_icons'].get(owner)
        if not isinstance(cfg, dict):
            continue
        for size_key, cap_key in (('icon_size', 'icon_max_size'), ('name_size', 'name_max_size')):
            if cap_key in cfg:
                cap = cfg.pop(cap_key)
                prefix = 'poi_icons.' + (owner+'.' if owner else '')
                mappings[:] = [row for row in mappings if row['target'] != prefix+cap_key]
                record(prefix+cap_key, cap, min(cfg.get(size_key, data['poi_icons'][size_key]), cap),
                       'effective-size-cap', prefix+size_key)
        ratio = cfg.pop('name_stroke_ratio', root_stroke_ratio)
        size = cfg.get('name_size', data['poi_icons']['name_size'])
        path = 'poi_icons.' + (owner+'.' if owner else '') + 'name_stroke_width'
        record(path, ratio, max(round(size*ratio), 1) if ratio > 0 else 0, 'name-outline')
    data['size_contract'] = pixels.CONTRACT
    pixels.validate(data)
    record_data = {'schemaVersion': 1, 'updateVersion': UPDATE_VERSION,
        'resolverVersion': LEGACY_RESOLVER, 'sourceId': source_id,
        'sourceDigest': digest(source), 'overrides': copy.deepcopy(overrides or {}),
        'targetContract': pixels.CONTRACT, 'targetDigest': digest(data),
        'calibration': copy.deepcopy(calibration), 'calibrationDigest': digest(calibration),
        'effectiveInput': copy.deepcopy(effective),
        'fontDeclarations': {path: value for path, value in pixels.walk(effective)
                             if path.endswith('.font')},
        'fields': mappings, 'retired': removed, 'changes': [
            'Road footprints use the width ratio for their road class.',
            'Centerline road widths use the registered calibration sample for each category.',
            'All station diameters use the base size without corridor or interchange expansion.',
            'Station symbols share an internal outline and fixed diameter box; external halos and growing capsules are retired.']}
    return {'status': 'update', 'theme': data, 'record': record_data}


def _has(data, path):
    for key in path.split('.'):
        if isinstance(data, dict) and key in data:
            data = data[key]
        elif isinstance(data, list) and key.isdigit() and int(key) < len(data):
            data = data[int(key)]
        else:
            return False
    return True


def include_view_sizes(plan, source, calibration, view_sizes):
    """Fold the explicitly captured historic transit view into one personal theme."""
    if not view_sizes:
        return plan
    _, zoom = validate_calibration(calibration)
    result = copy.deepcopy(plan)
    if result['status'] == 'current':
        result = {'status': 'update', 'theme': result['theme'], 'record': {
            'schemaVersion': 1, 'updateVersion': UPDATE_VERSION, 'resolverVersion': LEGACY_RESOLVER,
            'sourceId': 'pixel-theme-with-legacy-view', 'sourceDigest': digest(source),
            'targetContract': pixels.CONTRACT, 'calibration': copy.deepcopy(calibration),
            'calibrationDigest': digest(calibration), 'fields': [], 'changes': []}}
    data, record = result['theme'], result['record']
    record['viewSizes'] = copy.deepcopy(view_sizes)
    if view_sizes.get('legacyDefaults'):
        view_sizes = {'line_width': 3, 'stations.size': 8, 'station_names.size': 12,
                      'facilities.icon_size': 24, 'line_label.size': 12,
                      'line_label.interval': 800, **view_sizes}
    presentation = view_sizes.get('presentation', {})
    def scale(key):
        return max(.5, min(pixels.number(presentation.get(key, 1), key, positive=True), 2))
    icon, label, legend = scale('icon_scale'), scale('label_scale'), scale('legend_scale')
    def assign(path, value, origin):
        pixels.number(value, path)
        pixels.put(data, path, value)
        record['fields'].append({'source': origin, 'target': path, 'value': value,
                                 'rule': 'legacy-transit-view', 'origin': 'view-settings'})
    transport, poi = data['transports'], data['poi_icons']
    line_ratio = pixels.number(source.get('transports', {}).get('transit_view', {}).get('width_scale', 1.7),
                               'transports.transit_view.width_scale')
    for kind, config in transport['types'].items():
        assign(f'transports.types.{kind}.width', config['width']*line_ratio, 'transit-view-width')
    diameter = view_sizes.get('stations.size', transport['station']['diameter'])
    assign('transports.station.diameter', diameter*icon, 'stations.size')
    if 'station_names.size' in view_sizes:
        size = max(round(pixels.number(view_sizes['station_names.size'], 'station_names.size')*4*zoom), 1)
        assign('transports.station.names.size', size, 'station_names.size')
        assign('poi_icons.transit_name_size', size, 'station_names.size')
    badge = view_sizes.get('line_label.size')
    assign('transports.transit_view.label_size',
           (pixels.number(badge, 'line_label.size')*4 if badge is not None else transport['transit_view']['label_size'])*label,
           'line_label.size')
    if 'line_label.interval' in view_sizes:
        assign('transports.label.repeat_interval', pixels.number(view_sizes['line_label.interval'], 'line_label.interval')*zoom,
               'line_label.interval')
    if 'line_width' in view_sizes:
        # The historic renderer gave type widths precedence over the root view width.
        assign('transports.width', pixels.number(view_sizes['line_width'], 'line_width')*1.7, 'line_width')
    if 'facilities.icon_size' in view_sizes:
        diameter = pixels.number(view_sizes['facilities.icon_size'], 'facilities.icon_size')
        ratio = max(1.05, min(float(source.get('poi_icons', {}).get('transit_view_scale', 1.15)), 2))
        for key, value in (('icon_size', diameter*.75), ('bg_circle_size', diameter)):
            assign('poi_icons.'+key, value*icon*ratio, 'facilities.icon_size')
    else:
        for key in ('icon_size', 'bg_circle_size'):
            assign('poi_icons.'+key, poi[key]*icon, 'presentation.icon_scale')
    assign('poi_icons.name_size', poi['name_size']*label, 'presentation.label_scale')
    for key in ('size', 'title_size'):
        assign('transports.label.'+key, transport['label'][key]*legend, 'presentation.legend_scale')
    record['changes'].append('Saved transit view sizes are folded into this personal copy; their separate controls are retired.')
    pixels.validate(data)
    record['targetDigest'] = digest(data)
    return result


def commit_update(source_path, target_directory, plan):
    """目录中同时包含新版、原文件及记录；已有目标一律保留。"""
    import yaml
    source_path, target = Path(source_path).resolve(), Path(target_directory).resolve()
    if plan['status'] == 'current':
        return None
    source_bytes = source_path.read_bytes()
    if digest(yaml.safe_load(source_bytes)) != plan['record']['sourceDigest']:
        raise ValueError('Source theme changed after preflight; run the update again')
    pixels.validate(plan['theme'])
    if digest(plan['theme']) != plan['record']['targetDigest']:
        raise ValueError('Target theme changed after preflight')
    if target == source_path.parent or source_path.is_relative_to(target) or target.exists():
        raise ValueError('Theme update requires a new destination directory')
    from ._paths import resource_dir
    if target.is_relative_to(resource_dir('styles').resolve()):
        raise ValueError('Cannot update built-in themes in place')
    temporary = Path(tempfile.mkdtemp(prefix='.theme-update-', dir=target.parent))
    try:
        (temporary/'source.yaml').write_bytes(source_bytes)
        (temporary/'theme.yaml').write_text(yaml.safe_dump(plan['theme'], allow_unicode=True,
                                                       sort_keys=False), encoding='utf-8')
        record = {**plan['record'], 'sourceFileSha256': hashlib.sha256(source_bytes).hexdigest(),
                  'backup': 'source.yaml'}
        (temporary/'migration.json').write_text(json.dumps(record, ensure_ascii=False,
                                            indent=2, allow_nan=False), encoding='utf-8')
        # 不允许提交预检后被修改的源文件。
        if source_path.read_bytes() != source_bytes:
            raise ValueError('Source theme changed while preparing the update')
        os.rename(temporary, target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return target/'theme.yaml'


def main():
    import argparse
    import yaml
    parser = argparse.ArgumentParser(description='Preflight or explicitly update legacy theme dimensions to pixel-v2.')
    parser.add_argument('source', type=Path)
    parser.add_argument('--calibration', type=Path, required=True)
    parser.add_argument('--overrides', type=Path)
    parser.add_argument('--output-directory', type=Path)
    args = parser.parse_args()
    plan = plan_update(yaml.safe_load(args.source.read_text(encoding='utf-8')),
        json.loads(args.calibration.read_text(encoding='utf-8')), source_id=args.source.stem,
        overrides=yaml.safe_load(args.overrides.read_text(encoding='utf-8')) if args.overrides else None)
    if args.output_directory:
        commit_update(args.source, args.output_directory, plan)
    print(json.dumps(plan, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
