"""新版主题长度清单；存储只含基础像素，解析不依赖城市或画布。"""
from __future__ import annotations

import copy
from fnmatch import fnmatchcase
import math

CONTRACT = 'pixel-v2'
SIGNED_FIELDS = ('buildings.shadow.offset.*', 'transports.label.shadow_offset')

# 路径、旧单位解析方式。通配符只展开同一契约中的类别或数组索引。
LENGTH_FIELDS = {
    'water.border_width': 'world',
    'roads.*.line_width': 'pixel', 'roads.*.border_width': 'world',
    'roads.*.dash.*': 'world',
    'buildings.border_width': 'world', 'buildings.shadow.offset.*': 'pixel',
    'procedural_objects.border_width': 'world',
    'district.border_width': 'world', 'zone.border_width': 'world',
    'labels.*.size': 'world', 'labels.*.stroke_width': 'world',
    'labels.*.stroke_blur': 'world', 'labels.road_repeat': 'world-distance',
    'labels.padding': 'pixel',
    'poi_icons.icon_size': 'world', 'poi_icons.icon_max_size': 'world',
    'poi_icons.bg_circle_size': 'world', 'poi_icons.name_size': 'world',
    'poi_icons.name_max_size': 'world', 'poi_icons.transit_name_size': 'world',
    'poi_icons.name_offset': 'world', 'poi_icons.grid_size': 'world',
    'poi_icons.name_stroke_width': 'pixel', 'poi_icons.*.name_stroke_width': 'pixel',
    'poi_icons.icon_gap': 'pixel', 'poi_icons.symbol_padding': 'pixel', 'poi_icons.name_padding': 'pixel',
    'poi_icons.building.icon_size': 'world', 'poi_icons.building.icon_max_size': 'world',
    'poi_icons.building.bg_circle_size': 'world', 'poi_icons.building.name_size': 'world',
    'poi_icons.building.name_max_size': 'world', 'poi_icons.building.name_offset': 'world',
    'poi_icons.building.grid_size': 'world',
    'poi_icons.transit.icon_size': 'world', 'poi_icons.transit.icon_max_size': 'world',
    'poi_icons.transit.bg_circle_size': 'world', 'poi_icons.transit.name_size': 'world',
    'poi_icons.transit.name_max_size': 'world', 'poi_icons.transit.transit_name_size': 'world',
    'poi_icons.transit.name_offset': 'world', 'poi_icons.transit.grid_size': 'world',
    'railway.*.width': 'railway', 'railway.*.station_width': 'railway',
    'railway.*.dash_width': 'railway', 'railway.*.decoration_span': 'world',
    'railway.*.decoration_length': 'world', 'railway.*.decoration_width': 'world',
    'transports.width': 'world', 'transports.types.*.width': 'world',
    'transports.types.*.dash.*': 'world',
    'transports.station.diameter': 'pixel',
    'transports.station.border_width': 'world', 'transports.station.circle_stroke': 'world',
    'transports.station.tick_height': 'world', 'transports.station.transfer_halo': 'world',
    'transports.station.names.size': 'world', 'transports.station.names.gap': 'world',
    'transports.station.names.stroke_width': 'world',
    'transports.label.size': 'world', 'transports.label.padding': 'world',
    'transports.label.border_width': 'world',
    'transports.label.capture_padding': 'pixel',
    'transports.label.repeat_interval': 'world-distance', 'transports.label.min_gap': 'world-distance',
    'transports.label.title_size': 'decoration', 'transports.label.gap': 'decoration',
    'transports.label.column_gap': 'decoration', 'transports.label.swatch_width': 'decoration',
    'transports.label.swatch_height': 'decoration', 'transports.label.border_radius': 'decoration',
    'transports.label.radius': 'decoration',
    'transports.label.margin': 'decoration', 'transports.label.shadow_blur': 'decoration',
    'transports.label.shadow_offset': 'decoration',
    'transports.transit_view.label_size': 'badge',
    'transports.transit_view.label_repeat_interval': 'world-distance',
    'transports.transit_view.label_padding': 'world',
    'transports.transit_view.label_border_width': 'world',
    'transports.transit_view.label_seam_width': 'world',
    'transports.transit_view.label_min_gap': 'pixel',
    'transports.transit_view.label_merge_dist': 'pixel',
    'transports.transit_view.label_candidate_interval': 'pixel',
    'transports.transit_view.label_clearance_padding': 'pixel',
    'transports.transit_view.composite_min_stripe': 'pixel',
    'transports.line.light_outline.width': 'world',
    'watermark.font_size': 'watermark', 'watermark.margin.*': 'watermark',
    'watermark.padding.*': 'watermark', 'watermark.border_radius': 'watermark',
}

RETIRED_FIELDS = (
    'roads.width_scale', 'roads.*.min_fill_px', 'roads.*.width',
    'transports.station.size', 'transports.station.diameter_per_1k',
    'transports.station.names.font_size',
    'transports.station.station_names', 'transports.station.names.transfer_size_scale',
    'transports.station.transfer_height_scale', 'transports.station.transfer_size_scale',
    'transports.station.transfer_scale', 'transports.station.transfer_per_line',
    'transports.station.transfer_max_scale', 'transports.station.transfer_halo',
    'transports.station.tick_height', 'transports.station.ticks',
    'transports.station.circle_stroke', 'transports.station.transfer_design',
    'transports.station.transfer_arrow',
    'transports.fit_preview', 'transports.preview_sizing',
    'transports.transit_view.width_scale', 'poi_icons.transit_view_scale',
    'labels.road.size_z', 'poi_icons.name_stroke_ratio', 'poi_icons.*.name_stroke_ratio',
    'poi_icons.icon_max_size', 'poi_icons.name_max_size', 'poi_icons.*.icon_max_size', 'poi_icons.*.name_max_size',
    'labels.artwork_scale', 'transports.artwork_scale', 'transports.artwork_lengths',
    'poi_icons.artwork_lengths', 'poi_icons.artwork_scale',
    'poi_icons.*.artwork_lengths', 'poi_icons.*.artwork_scale',
    'elementScale',
)


def field_rule(path):
    return next((rule for pattern, rule in LENGTH_FIELDS.items()
                 if fnmatchcase(path, pattern)), None)


def walk(data, path=''):
    if isinstance(data, dict):
        if {'fixed', 'scaled'} & data.keys():
            yield path, data
        else:
            for key, value in data.items():
                yield from walk(value, f'{path}.{key}' if path else str(key))
    elif isinstance(data, list):
        for index, value in enumerate(data):
            yield from walk(value, f'{path}.{index}')
    else:
        yield path, data


def put(data, path, value):
    parts = path.split('.')
    for part in parts[:-1]:
        data = data[int(part)] if isinstance(data, list) else data.setdefault(part, {})
    if isinstance(data, list):
        data[int(parts[-1])] = value
    else:
        data[parts[-1]] = value


def number(value, path, *, positive=False, signed=False):
    if type(value) not in (int, float) or not math.isfinite(value) or (
            value <= 0 if positive else value < 0 and not signed):
        raise ValueError(f'{path}: expected a finite {"positive" if positive else "nonnegative"} number')
    return float(value)


def is_pixel_theme(data):
    version = data.get('size_contract')
    if version not in (None, CONTRACT):
        raise ValueError(f'Unsupported theme size contract: {version}')
    return version == CONTRACT


def retired(path):
    parts = path.split('.')
    return any(fnmatchcase('.'.join(parts[:end]), pattern)
               for end in range(1, len(parts)+1) for pattern in RETIRED_FIELDS)


def validate(data):
    if not isinstance(data, dict) or not is_pixel_theme(data):
        raise ValueError('Theme requires an explicit pixel-v2 size contract; update the old theme first')
    def check_nodes(value, path=''):
        if retired(path):
            raise ValueError(f'{path}: retired theme setting')
        if isinstance(value, dict):
            for key, child in value.items():
                check_nodes(child, f'{path}.{key}' if path else str(key))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                check_nodes(child, f'{path}.{index}')
    check_nodes(data)
    for path, value in walk(data):
        if isinstance(value, dict):
            raise ValueError(f'{path}: fixed/scaled is retired; update the theme first')
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f'{path}: expected a finite number')
        if field_rule(path):
            number(value, path, positive=path.endswith(('.diameter', '.line_width')),
                   signed=any(fnmatchcase(path, pattern) for pattern in SIGNED_FIELDS))
        elif fnmatchcase(path, 'roads.*.footprint_scale'):
            number(value, path, positive=True)
    for key, cfg in data.get('roads', {}).items():
        if isinstance(cfg, dict) and ('fill' in cfg or 'line_width' in cfg):
            for field in ('line_width', 'footprint_scale', 'border_width'):
                if field not in cfg:
                    raise ValueError(f'roads.{key}.{field}: missing pixel-v2 road setting')
    station = data.get('transports', {}).get('station')
    if station is not None and 'diameter' not in station:
        raise ValueError('transports.station.diameter: missing pixel-v2 station diameter')


def resolve(data, element_scale, *, output_scale=1.0):
    """一次解析基础长度；源主题与画布尺寸均不改变。"""
    validate(data)
    scale = number(element_scale, 'elementScale', positive=True)
    output_scale = number(output_scale, 'outputScale', positive=True)
    out = copy.deepcopy(data)
    # Optional scalar defaults join the same resolution pass as explicit lengths.
    from .theme_contract import _defaults
    def defaults(target, template, prefix=''):
        for key, value in template.items():
            path = f'{prefix}.{key}' if prefix else key
            if isinstance(value, dict):
                if isinstance(target.get(key), dict):
                    defaults(target[key], value, path)
            elif key not in target and field_rule(path):
                target[key] = copy.deepcopy(value)
    defaults(out, _defaults())
    lengths, footprints = {}, {}
    for path, value in walk(out):
        if field_rule(path):
            actual = value * scale * output_scale
            if not math.isfinite(actual):
                raise ValueError(f'{path}: resolved pixel length is not finite')
            put(out, path, actual)
            lengths[path] = {'base': value, 'resolved': actual}
        elif fnmatchcase(path, 'roads.*.footprint_scale'):
            footprints[path] = {'base': value, 'resolved': value}
    return out, {'resolverVersion': 2, 'inputUnit': CONTRACT, 'scale': scale,
                 'outputScale': output_scale, 'lengths': lengths, 'footprints': footprints}


def prepare_style(style, element_scale=1.0, *, output_scale=1.0):
    """从基础主题解析，重复调用不在已经解析的长度上累乘。"""
    base = getattr(style, '_pixel_base', style.data)
    resolved, record = resolve(base, element_scale, output_scale=output_scale)
    style._pixel_base = copy.deepcopy(base)
    style.data = resolved
    return record


def footprint_factors(roads):
    """按道路类别及模式读取足迹横向宽度倍率。"""
    from .road_hierarchy import RoadLevel, road_style_config
    return {(level.value, mode): road_style_config(roads, level, mode)['footprint_scale']
            for level in RoadLevel for mode in ('ground', 'elevated', 'tunnel')}
