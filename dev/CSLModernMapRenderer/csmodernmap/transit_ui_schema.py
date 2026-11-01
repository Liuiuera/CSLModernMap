"""定义可编辑的公交选项及其界面约束。"""
import math
from .i18n import tr

LABELS = {'station_size': 'style.transport.size.label',
 'spacing': 'gui.editor.spacing',
 'line_width': 'style.transport.width.label',
 'line_label_interval': 'gui.transit_view.label_interval',
 'merge_range': 'gui.style_editor.field.merge_distance',
 'facility_icon_size': 'style.poi.size.label',
 'transit_mask': 'style.transport.mask.label'}
CHOICES = {'station_name_placement': ('hidden', 'station', 'icon'), 'line_color_mode': ('game', 'classic', 'soft')}
BOUNDS = {'station_name_size': (6, 48),
 'station_size': (4, 24),
 'line_width': (1, 12),
 'spacing': (200, 800),
 'label_scale': (0.5, 2),
 'icon_scale': (0.5, 2),
 'title_scale': (0.5, 2),
 'legend_scale': (0.5, 2),
 'adaptive_strength': (0, 1),
 'line_label_size': (6, 36),
 'line_label_interval': (80, 5000),
 'merge_range': (0, 200),
 'facility_icon_size': (12, 48),
 'transit_mask': (0, 1),
 'water_smoothing': (0, 16)}
FALLBACK_LABELS = {'station_name_placement': '站名标注',
 'line_color_mode': '线路颜色',
 'station_name_size': '字号',
 'label_scale': '站名字号比例',
 'icon_scale': '图标比例',
 'title_scale': '标题比例',
 'legend_scale': '图例比例',
 'stations': '显示站点',
 'schematic_options': '自适应比例',
 'schematic_water': '示意水域',
 'water_smoothing': '岸线平滑强度',
 'facilities_enabled': '接驳设施',
 'base_water': '保留水域',
 'base_road': '保留道路',
 'base_district': '保留行政区划',
 'merge_enabled': '合并站点',
 'ordinary_pois': '普通地点显示',
 'line_label_enabled': '线路标签',
 'line_label_size': '线路标签字号',
 'layers': '视图图层',
 'adaptive_scale': '动态比例',
 'adaptive_strength': '自适应强度'}
# 导出面板的只读图层快照，不作为可编辑状态写回。
LAYER_OPTIONS = {'stations': 'stations',
 'line_labels': 'line_label_enabled',
 'facilities': 'facilities_enabled',
 'base_water': 'base_water',
 'base_road': 'base_road',
 'base_district': 'base_district'}
RESERVED = {"types", "line_ids", "station_line_ids", "metro_edits", "pure", "abstract"}
STYLE_FIELDS = {'line_width': 'transport.width',
 'station_size': 'transport.size',
 'line_label_interval': 'transport.label.interval',
 'merge_range': 'transport.distance',
 'merge_enabled': 'transport.merge'}


def option_schema(options):
    result = {}
    for key, value in options.items():
        if key in RESERVED or not isinstance(value, (str, int, float, bool, dict)):
            continue
        if isinstance(value, dict) and key not in {"schematic_options", "layers"}:
            continue
        label = tr(LABELS[key]) if key in LABELS else FALLBACK_LABELS.get(key, tr("gui.transit.ann." + key[4:]) if key.startswith("ann_") else key.replace("_", " "))
        result[key] = {"label": label, "value": value,
                       "kind": "object" if isinstance(value, dict) else "toggle" if isinstance(value, bool)
                       else "choice" if key in CHOICES else "number" if isinstance(value, (int, float)) else "text",
                       "choices": list(CHOICES.get(key, ())),
                       "low": BOUNDS.get(key, (0, 100))[0], "high": BOUNDS.get(key, (0, 100))[1]}
    return result


def validate_option(options, key, value):
    schema = option_schema(options)
    if key not in schema:
        raise ValueError(f"unknown transit option: {key}")
    if key in CHOICES and value not in CHOICES[key]:
        raise ValueError(f"invalid transit option: {key}")
    old = options[key]
    if isinstance(old, bool):
        if not isinstance(value, bool):
            raise ValueError(f"{key} needs a boolean")
    elif isinstance(old, (int, float)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{key} needs a finite number")
        if key in BOUNDS and not BOUNDS[key][0] <= value <= BOUNDS[key][1]:
            raise ValueError(f"{key} is out of range")
    elif isinstance(old, dict):
        if not isinstance(value, dict) or not set(value) <= set(old):
            raise ValueError("unknown schematic option")
        for child, item in value.items():
            validate_option({child: old[child]}, child, item)
    elif not isinstance(value, str):
        raise ValueError(f"{key} needs text")
    return value
