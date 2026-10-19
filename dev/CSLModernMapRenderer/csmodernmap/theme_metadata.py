"""主题编辑器的字段元数据与纯函数（无 Qt 依赖）。

主题 YAML 是「数据驱动」的：顶层 17 个结构段，每个段里可能是标量、嵌套字典、
列表或 ``{fixed, scaled}`` 组合，合计 150+ 个可编辑叶子。编辑器按数据结构递归
生成控件，本模块只负责回答「这个字段叫什么、范围多少、怎么解释、怎么解析」这类
与界面无关的问题。

字段名与取值范围**优先取自** ``ribbon_schema.FIELDS``（唯一字段源），只有在
schema 未收录该 YAML 路径时才回退到本模块的表。
**本模块不含任何文案**：所有显示名都是翻译键，文字在 resources/i18n/*.json。

本模块曾被命名为 ``theme_meta``；对外只暴露纯函数与只读常量。
"""

from __future__ import annotations

import re
from fnmatch import fnmatchcase

from csmodernmap.guir_backend import STYLE_FIELD_PATHS
from csmodernmap.i18n import catalog_keys, tr
from csmodernmap.ribbon_schema import FIELDS, LAYER_TITLE_KEYS, layer_title

HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$")

# YAML 顶层键 → 侧栏图层键。主题 YAML 的段名与界面图层名个别不同（复数 / 别名），
# 这里对齐一次，标题就统一取 gui.layer.title.*（与侧栏同一套文案）。
SECTION_TO_LAYER = {
    "roads": "road",
    "buildings": "building",
    "transports": "metro",
    "poi_icons": "poi",
    "labels": "label",
}

# 分组显示顺序（与旧版编辑器一致：画布 → … → 水印）。
SECTION_ORDER = (
    "canvas", "terrain", "water", "forest", "built_area",
    "zone", "land_use", "district", "roads", "buildings",
    "procedural_objects", "railway", "transports", "poi_icons",
    "labels", "watermark",
)


# 主题里存在、但渲染器从不读取（编辑无效）的叶子：隐藏以免误导。
# 隐藏只影响展示；``collect()`` 从工作副本深拷贝重建，这些字段仍会原样保留。
HIDDEN_LEAF_KEYS = {
    "poi_icons": {"icons_dir", "sprite"},
    "terrain": {"ground"},  # 旧距离配色仅保留数据，全部主题改用海拔色带。
    "buildings": {"residential", "commercial", "industrial", "office", "shadow"},
    "canvas": {"width", "height"},  # 画布尺寸由渲染分辨率决定，不读主题
    # 「立体棱角」已从产品里删除：山体光影只剩连续晕染一种画法，面片尺寸 /
    # 扰动 / 模式三个键对渲染不再有任何影响。老自建主题里可能还留着它们，
    # 编辑器不再展示（隐藏只影响展示，collect() 仍会原样保留这些键）。
    "hillshade": {"mode", "facet_size_meters", "facet_jitter"},
    # 旧自定义主题的区划分类配色已停用，样式加载器会在进入编辑器前移除。
    "district": {"specialization", "specialization_colors"},
    "zone": {"colors"},
}

# ---- 字段显示名（schema 未收录的 YAML 路径回退到这里）----
# 值是**翻译键**，按叶子名查 —— 理想形态是按完整路径取，但叶子名兜底能覆盖绝大多数情形。
LABELS = {
    "name": "gui.style_editor.field.name",
    "description": "gui.style_editor.field.description",
    "width": "gui.style_editor.field.width",
    "height": "gui.style_editor.field.height",
    "background": "gui.style_editor.field.background",
    "elevation_colors": "gui.style_editor.field.elevation_colors",
    "elevation_mode": "gui.style_editor.field.elevation_mode",
    "threshold": "gui.style_editor.field.threshold",
    "color": "gui.style_editor.field.color",
    "near_green": "gui.style_editor.field.near_green",
    "far_green": "gui.style_editor.field.far_green",
    "ramp_meters": "gui.style_editor.field.ramp_meters",
    "mountain_threshold": "gui.style_editor.field.mountain_threshold",
    "mountain_colors": "gui.style_editor.field.mountain_colors",
    "hillshade": "gui.style_editor.field.hillshade",
    "mode": "gui.style_editor.field.mode",
    "enabled": "gui.style_editor.field.enabled",
    "intensity": "gui.style_editor.field.intensity",
    "azimuth": "gui.style_editor.field.azimuth",
    "altitude": "gui.style_editor.field.altitude",
    "large_scale_meters": "gui.style_editor.field.large_scale_meters",
    "small_scale_meters": "gui.style_editor.field.small_scale_meters",
    "large_scale_weight": "gui.style_editor.field.large_scale_weight",
    "small_scale_weight": "gui.style_editor.field.small_scale_weight",
    "border": "gui.style_editor.field.border",
    "border_width": "gui.style_editor.field.border_width",
    "fill": "gui.style_editor.field.fill",
    "width_scale": "gui.style_editor.field.width_scale",
    "fixed": "gui.style_editor.field.fixed",
    "scaled": "gui.style_editor.field.scaled",
    "dash": "gui.style_editor.field.dash",
    "road": "gui.style_editor.field.road",
    "highway": "gui.style_editor.field.highway",
    "main": "gui.style_editor.field.main",
    "branch": "gui.style_editor.field.branch",
    "special": "gui.style_editor.field.special",
    "beautification": "gui.style_editor.field.beautification",
    "primary": "gui.style_editor.field.primary",
    "secondary": "gui.style_editor.field.secondary",
    "tertiary": "gui.style_editor.field.tertiary",
    "pedestrian": "gui.style_editor.field.pedestrian",
    "road_elevated": "gui.style_editor.field.road_elevated",
    "highway_elevated": "gui.style_editor.field.highway_elevated",
    "beautification_elevated": "gui.style_editor.field.beautification_elevated",
    "road_tunnel": "gui.style_editor.field.road_tunnel",
    "highway_tunnel": "gui.style_editor.field.highway_tunnel",
    "beautification_tunnel": "gui.style_editor.field.beautification_tunnel",
    "tunnel": "gui.style_editor.field.tunnel",
    "bridge": "gui.style_editor.field.bridge",
    "min_fill_px": "gui.style_editor.field.min_fill_px",
    "smooth": "gui.style_editor.field.smooth",
    "label": "gui.style_editor.field.label",
    "font": "gui.style_editor.field.font",
    "size": "gui.style_editor.field.size",
    "padding": "gui.style_editor.field.padding",
    "bg": "gui.style_editor.field.bg",
    "repeat_interval": "gui.style_editor.field.repeat_interval",
    "min_gap": "gui.style_editor.field.min_gap",
    "max_per_line": "gui.style_editor.field.max_per_line",
    "column_gap": "gui.style_editor.field.column_gap",
    "gap": "gui.style_editor.field.gap",
    "residential": "gui.style_editor.field.residential",
    "commercial": "gui.style_editor.field.commercial",
    "industrial": "gui.style_editor.field.industrial",
    "office": "gui.style_editor.field.office",
    "publictransport": "gui.style_editor.field.publictransport",
    "public": "gui.style_editor.field.public",
    "default": "gui.style_editor.field.default",
    "shadow": "gui.style_editor.field.shadow",
    "offset": "gui.style_editor.field.offset",
    "line": "gui.style_editor.field.line",
    "stroke": "gui.style_editor.field.stroke",
    "stroke_width": "gui.style_editor.field.stroke_width",
    "bold": "gui.style_editor.field.bold",
    "district": "gui.style_editor.field.district",
    "park": "gui.style_editor.field.park",
    "building": "gui.style_editor.field.building",
    "stop": "gui.style_editor.field.stop",
    "icon_size": "gui.style_editor.field.icon_size",
    "grid_size": "gui.style_editor.field.grid_size",
    "max_per_cell": "gui.style_editor.field.max_per_cell",
    "min_priority": "gui.style_editor.field.min_priority",
    "categories": "gui.style_editor.field.categories_palette",
    "colors": "gui.style_editor.field.colors",
    "bg_circle": "gui.style_editor.field.bg_circle",
    "bg_circle_size": "gui.style_editor.field.bg_circle_size",
    "sprite": "gui.style_editor.field.sprite",
    "icons_dir": "gui.style_editor.field.icons_dir",
    "hidden_categories": "gui.style_editor.field.hidden_categories",
    "cell_meters": "gui.style_editor.field.cell_meters",
    "buffer_meters": "gui.style_editor.field.buffer_meters",
    "contour_upscale": "gui.style_editor.field.contour_upscale",
    "smooth_iters": "gui.style_editor.field.smooth_iters",
    "smooth_blur": "gui.style_editor.field.smooth_blur",
    "exclude_services": "gui.style_editor.field.exclude_services",
    "include_parks": "gui.style_editor.field.include_parks",
    "detail": "gui.style_editor.field.detail",
    "alpha": "gui.style_editor.field.alpha",
    "fill_alpha": "gui.style_editor.field.fill_alpha",
    "fill_colors": "gui.style_editor.field.fill_colors",
    "auto_color": "gui.style_editor.field.auto_color",
    "octilinear": "gui.style_editor.field.octilinear",
    "schematic": "gui.style_editor.field.schematic",
    "spacing": "gui.style_editor.field.spacing",
    "parallel_gap": "gui.style_editor.field.parallel_gap",
    "transit_view": "gui.style_editor.field.transit_view",
    "mask_intensity": "gui.style_editor.field.mask_intensity",
    "transit_view_scale": "gui.style_editor.field.transit_view_scale",
    "min_zoom": "gui.style_editor.field.min_zoom",
    "merge_enabled": "gui.style_editor.field.merge_enabled",
    "merge_range": "gui.style_editor.field.merge_range",
    "merge_distance": "gui.style_editor.field.merge_distance",
    "merge_bus_tram": "gui.style_editor.field.merge_bus_tram",
    "merge_train_metro": "gui.style_editor.field.merge_train_metro",
    "outline": "gui.style_editor.field.outline",
    "transfer_scale": "gui.style_editor.field.transfer_scale",
    "geometry": "gui.style_editor.field.geometry",
    "prop": "gui.style_editor.field.prop",
    "tree": "gui.style_editor.field.tree",
    "street_furn": "gui.style_editor.field.street_furn",
    "sign": "gui.style_editor.field.sign",
    "unknown": "gui.style_editor.field.unknown",
    "custom_color_alpha": "gui.style_editor.field.custom_color_alpha",
    "dash_width": "gui.style_editor.field.dash_width",
    "pattern": "gui.style_editor.field.pattern",
    "station_width": "gui.style_editor.field.station_width",
    "decoration_span": "gui.style_editor.field.decoration_span",
    "decoration_length": "gui.style_editor.field.decoration_length",
    "decoration_width": "gui.style_editor.field.decoration_width",
    "name_z": "gui.style_editor.field.name_z",
    "road_wide": "gui.style_editor.field.road_wide",
    "walk": "gui.style_editor.field.walk",
    "road_repeat": "gui.style_editor.field.road_repeat",
    "road_repeat_ratio": "gui.style_editor.field.road_repeat_ratio",
    "road_min_gap": "gui.style_editor.field.road_min_gap",
    "show_name": "gui.style_editor.field.show_name",
    "name_size": "gui.style_editor.field.name_size",
    "name_offset": "gui.style_editor.field.name_offset",
    "types": "gui.style_editor.field.types",
    "green": "gui.style_editor.field.green",
    "mod_name": "gui.style_editor.field.mod_name",
    "position": "gui.style_editor.field.position",
    "separator": "gui.style_editor.field.separator",
    "x": "gui.style_editor.field.x",
    "y": "gui.style_editor.field.y",
    "margin": "gui.style_editor.field.margin",
    "border_radius": "gui.style_editor.field.border_radius",
    "time_format": "gui.style_editor.field.time_format",
    "city_color": "gui.style_editor.field.city_color",
    "info_color": "gui.style_editor.field.info_color",
    "meta_color": "gui.style_editor.field.meta_color",
    "content": "gui.style_editor.field.content",
    "city_name": "gui.style_editor.field.city_name",
    "render_time": "gui.style_editor.field.render_time",
    "style_name": "gui.style_editor.field.style_name",
    "resolution": "gui.style_editor.field.resolution",
    "traffic_time": "gui.style_editor.field.traffic_time",
    "custom_text": "gui.style_editor.field.custom_text",
    "poi_tint": "gui.style_editor.field.poi_tint",
    "mix_white": "gui.style_editor.field.mix_white",
    "link_meters": "gui.style_editor.field.link_meters",
    "road_width_scale": "gui.style_editor.field.road_width_scale",
    "road_barrier_extra": "gui.style_editor.field.road_barrier_extra",
    "station": "gui.style_editor.field.station",
    "names": "gui.style_editor.field.names",
    "font_size": "gui.style_editor.field.font_size",
    "adaptive": "gui.style_editor.field.adaptive",
    "dense_gap": "gui.style_editor.field.dense_gap",
    "debug": "gui.style_editor.field.debug",
    "max_cells": "gui.style_editor.field.max_cells",
    "corridor": "gui.style_editor.field.corridor",
}


# 数值单位：决定「范围 x～y 单位」里的单位文案。
FIELD_UNIT_KEYS = {
    "width": "px",
    "height": "px",
    "size": "px",
    "padding": "px",
    "border_width": "px",
    "stroke_width": "px",
    "icon_size": "px",
    "grid_size": "px",
    "bg_circle_size": "px",
    "name_size": "px",
    "font_size": "px",
    "threshold": "meter",
    "large_scale_meters": "meter",
    "small_scale_meters": "meter",
    "ramp_meters": "meter",
    "mountain_threshold": "meter",
    "azimuth": "degree",
    "altitude": "degree",
    "width_scale": "meter_to_pixel",
    "min_fill_px": "px",
    "smooth": "samples",
    "repeat_interval": "px",
    "min_gap": "px",
    "max_per_line": "count",
    "max_per_cell": "count",
    "min_priority": "priority",
    "intensity": "ratio",
    "large_scale_weight": "ratio",
    "small_scale_weight": "ratio",
    "alpha": "alpha255",
    "fill_alpha": "alpha255",
    "mix_white": "ratio",
    "custom_color_alpha": "ratio",
    "mask_intensity": "ratio",
    # 这两个是「合并距离」，渲染侧一律 × pixels-per-metre 当世界米用
    # （preview_scene / poi_icon_layer / poi_density），不是输出像素。
    "merge_distance": "meter",
    "merge_range": "meter",
    "link_meters": "meter",
    "buffer_meters": "meter",
    "cell_meters": "meter",
    "smooth_blur": "px",
    "transit_view_scale": "ratio",
    "border_radius": "px",
    "dash_width": "px",
}

# 数值单位 → 翻译键（gui.style_editor.unit.*）。
_UNIT_KEYS = {
    "px": "gui.style_editor.unit.px",
    "meter": "gui.style_editor.unit.meter",
    "degree": "gui.style_editor.unit.degree",
    "meter_to_pixel": "gui.style_editor.unit.meter_to_pixel",
    "samples": "gui.style_editor.unit.samples",
    "count": "gui.style_editor.unit.count",
    "priority": "gui.style_editor.unit.priority",
    "ratio": "gui.style_editor.unit.ratio",
    "alpha255": "gui.style_editor.unit.alpha255",
}


# 显式语义优先于按名字猜测：值 = ((最小, 最大, 步长), 单位键)
NUMERIC_FIELD_SPECS = {
    "water.border_width": ((0.0, 50.0, 0.1), "px"),
    "roads.width_scale": ((0.0, 5.0, 0.01), "meter_to_pixel"),
    "terrain.hillshade.intensity": ((0.0, 1.0, 0.01), "ratio"),
    "district.fill_alpha": ((0, 255, 1), "alpha255"),
    "zone.fill_alpha": ((0, 255, 1), "alpha255"),
    "land_use.alpha": ((0, 255, 1), "alpha255"),
    "built_area.cell_meters": ((4.0, 64.0, 1.0), "meter"),
    "built_area.contour_upscale": ((1, 8, 1), "count"),
    "built_area.smooth_iters": ((0, 16, 1), "count"),
    "built_area.smooth_blur": ((0.0, 8.0, 0.1), "samples"),
    "built_area.opacity": ((0.0, 1.0, 0.01), "ratio"),
    "buildings.poi_tint.contrast": ((0.0, 1.0, 0.01), "ratio"),
    "procedural_objects.custom_color_alpha": ((0.0, 1.0, 0.01), "ratio"),
    "roads.*.min_fill_px": ((0.0, 20.0, 0.1), "px"),
    "roads.*.border_width": ((0.0, 50.0, 0.1), "px"),
    "roads.*.dash.*": ((0.0, 2000.0, 0.1), "px"),
    "transports.types.*.dash.*": ((0.0, 2000.0, 0.1), "px"),
    "terrain.*colors.*.threshold": ((-12000, 12000, 1), "meter"),
    "transports.schematic.parallel_gap": ((1.05, 3.0, 0.05), "ratio"),
    "transports.schematic.adaptive.dense_gap": ((0, 2, 1), "count"),
    "transports.schematic.debug.max_cells": ((256, 200000, 256), "count"),
    "transports.station.names.gap": ((0.0, 2000.0, 0.5), "px"),
    "transports.label.gap": ((0.0, 2000.0, 0.5), "px"),
    "transports.label.column_gap": ((0.0, 2000.0, 0.5), "px"),
    "shop_area.mix_white": ((0.0, 1.0, 0.02), "ratio"),
    "transports.transit_view.mask_intensity": ((0.0, 1.0, 0.05), "ratio"),
}


def numeric_spec(path: tuple):
    dotted = ".".join(path)
    return next((spec for pattern, spec in NUMERIC_FIELD_SPECS.items()
                 if fnmatchcase(dotted, pattern)), None)


# 这些是主题值，不是功能区的百分比或可见性字段。
FIELD_KINDS = {
    "canvas.background": "color", "water.color": "color", "water.border": "color",
    "water.border_width": "size", "forest.color": "color",
    "terrain.elevation_mode": "choice", "terrain.*colors": "ramp",
    "terrain.*colors.*.color": "color",
    "terrain.ground.enabled": "toggle", "terrain.ground.near_green": "color",
    "terrain.ground.far_green": "color", "terrain.hillshade.enabled": "toggle",
    "roads.width_scale": "number", "roads.*.fill": "color", "roads.*.border": "color",
    "roads.*.min_fill_px": "number", "roads.*.border_width": "size", "roads.*.dash": "numbers",
    "buildings.color_mode": "choice", "buildings.default": "color", "buildings.border": "color",
    "buildings.border_width": "size", "buildings.poi_tint.contrast": "number",
    "district.border": "color", "district.border_width": "size", "district.fill_colors": "colors",
    "zone.border": "color", "zone.fill": "color", "zone.border_width": "size",
    "land_use.detail": "choice", "procedural_objects.border": "color",
    "procedural_objects.border_width": "size", "procedural_objects.custom_color_alpha": "number",
    "railway.station_width": "size",
    "transports.enabled": "types", "transports.auto_color": "toggle", "transports.width": "size",
    "transports.types.*.width": "size", "transports.types.*.dash": "numbers",
    "transports.types.*.color": "color", "transports.palette": "colors",
    "transports.station.size": "size",
    "transports.label.size": "size", "transports.label.padding": "size",
    "transports.label.border_width": "size", "transports.label.font": "font", "transports.label.bg": "color",
    "poi_icons.icon_size": "size", "poi_icons.name_size": "size", "poi_icons.bg_circle_size": "size",
    "poi_icons.bg_circle": "toggle", "poi_icons.font": "font",
    "labels.*.font": "font", "labels.*.size": "size", "labels.*.color": "color",
    "labels.*.stroke": "color", "labels.*.stroke_width": "size", "labels.*.bold": "toggle",
    "watermark.enabled": "toggle", "watermark.position": "choice", "watermark.font": "font",
    "watermark.font_size": "size", "watermark.margin.*": "size", "watermark.padding.*": "size",
    "watermark.city_color": "color", "watermark.info_color": "color", "watermark.meta_color": "color",
}


def field_kind(path: tuple) -> str | None:
    dotted = ".".join(path)
    explicit = next((kind for pattern, kind in FIELD_KINDS.items() if fnmatchcase(dotted, pattern)), None)
    if explicit: return explicit
    field = FIELDS.get(field_key_for(path))
    return field.kind if field else "number" if numeric_spec(path) else None

# 内联说明（key = 字段点分路径）。缺说明时不占位。
# 内联说明（key = 字段点分路径）。值是翻译键；缺说明时不占位。
FIELD_DESC_FALLBACK = {
    "canvas.background": "gui.style_editor.desc.canvas.background",
    "terrain.ground.enabled": "gui.style_editor.desc.terrain.ground.enabled",
    "terrain.ground.near_green": "gui.style_editor.desc.terrain.ground.near_green",
    "terrain.ground.far_green": "gui.style_editor.desc.terrain.ground.far_green",
    "terrain.ground.ramp_meters": "gui.style_editor.desc.terrain.ground.ramp_meters",
    "terrain.ground.mountain_threshold": "gui.style_editor.desc.terrain.ground.mountain_threshold",
    "terrain.ground.mountain_colors": "gui.style_editor.desc.terrain.ground.mountain_colors",
    "terrain.elevation_mode": "gui.style_editor.desc.terrain.elevation_mode",
    "terrain.elevation_colors": "gui.style_editor.desc.terrain.elevation_colors",
    "terrain.hillshade.enabled": "gui.style_editor.desc.terrain.hillshade.enabled",
    "terrain.hillshade.intensity": "gui.style_editor.desc.terrain.hillshade.intensity",
    "terrain.hillshade.azimuth": "gui.style_editor.desc.terrain.hillshade.azimuth",
    "terrain.hillshade.altitude": "gui.style_editor.desc.terrain.hillshade.altitude",
    "terrain.hillshade.large_scale_meters": "gui.style_editor.desc.terrain.hillshade.large_scale_meters",
    "terrain.hillshade.small_scale_meters": "gui.style_editor.desc.terrain.hillshade.small_scale_meters",
    "terrain.hillshade.large_scale_weight": "gui.style_editor.desc.terrain.hillshade.large_scale_weight",
    "terrain.hillshade.small_scale_weight": "gui.style_editor.desc.terrain.hillshade.small_scale_weight",
    "water.color": "gui.style_editor.desc.water.color",
    "water.border": "gui.style_editor.desc.water.border",
    "water.border_width": "gui.style_editor.desc.water.border_width",
    "forest.color": "gui.style_editor.desc.forest.color",
    "built_area.enabled": "gui.style_editor.desc.built_area.enabled",
    "built_area.color": "gui.style_editor.desc.built_area.color",
    "built_area.buffer_meters": "gui.style_editor.desc.built_area.buffer_meters",
    "built_area.cell_meters": "gui.style_editor.desc.built_area.cell_meters",
    "built_area.contour_upscale": "gui.style_editor.desc.built_area.contour_upscale",
    "built_area.smooth_blur": "gui.style_editor.desc.built_area.smooth_blur",
    "built_area.smooth_iters": "gui.style_editor.desc.built_area.smooth_iters",
    "built_area.include_parks": "gui.style_editor.desc.built_area.include_parks",
    "built_area.exclude_services": "gui.style_editor.desc.built_area.exclude_services",
    "land_use.enabled": "gui.style_editor.desc.land_use.enabled",
    "land_use.detail": "gui.style_editor.desc.land_use.detail",
    "land_use.alpha": "gui.style_editor.desc.land_use.alpha",
    "land_use.colors": "gui.style_editor.desc.land_use.colors",
    "district.enabled": "gui.style_editor.desc.district.enabled",
    "district.border": "gui.style_editor.desc.district.border",
    "district.fill_alpha": "gui.style_editor.desc.district.fill_alpha",
    "district.fill_colors": "gui.style_editor.desc.district.fill_colors",
    "zone.enabled": "gui.style_editor.desc.zone.enabled",
    "zone.border": "gui.style_editor.desc.zone.border",
    "zone.fill_alpha": "gui.style_editor.desc.zone.fill_alpha",
    "zone.fill": "gui.style_editor.desc.zone.fill",
    "roads.width_scale": "gui.style_editor.desc.roads.width_scale",
    "roads.highway.fill": "gui.style_editor.desc.roads.highway.fill",
    "transports.auto_color": "gui.style_editor.desc.transports.auto_color",
    "transports.smooth": "gui.style_editor.desc.transports.smooth",
    "transports.octilinear": "gui.style_editor.desc.transports.octilinear",
    "transports.schematic.spacing": "gui.style_editor.desc.transports.schematic.spacing",
    "transports.schematic.parallel_gap": "gui.style_editor.desc.transports.schematic.parallel_gap",
    "transports.transit_view.mask_intensity": "gui.style_editor.desc.transports.transit_view.mask_intensity",
    "transports.label.enabled": "gui.style_editor.desc.transports.label.enabled",
    "transports.label.size": "gui.style_editor.desc.transports.label.size",
    "transports.label.bg": "gui.style_editor.desc.transports.label.bg",
    "transports.label.repeat_interval": "gui.style_editor.desc.transports.label.repeat_interval",
    "transports.station.enabled": "gui.style_editor.desc.transports.station.enabled",
    "transports.station.size": "gui.style_editor.desc.transports.station.size",
    "transports.station.merge_enabled": "gui.style_editor.desc.transports.station.merge_enabled",
    "transports.station.merge_range": "gui.style_editor.desc.transports.station.merge_range",
    "transports.station.transfer_scale": "gui.style_editor.desc.transports.station.transfer_scale",
    "buildings.border": "gui.style_editor.desc.buildings.border",
    "buildings.shadow.enabled": "gui.style_editor.desc.buildings.shadow.enabled",
    "poi_icons.enabled": "gui.style_editor.desc.poi_icons.enabled",
    "poi_icons.icon_size": "gui.style_editor.desc.poi_icons.icon_size",
    "poi_icons.icons_dir": "gui.style_editor.desc.poi_icons.icons_dir",
    "poi_icons.show_name": "gui.style_editor.desc.poi_icons.show_name",
    "poi_icons.bg_circle": "gui.style_editor.desc.poi_icons.bg_circle",
    "poi_icons.grid_size": "gui.style_editor.desc.poi_icons.grid_size",
    "poi_icons.hidden_categories": "gui.style_editor.desc.poi_icons.hidden_categories",
    "labels.road_repeat": "gui.style_editor.desc.labels.road_repeat",
    "labels.road.color": "gui.style_editor.desc.labels.road.color",
    "labels.district.color": "gui.style_editor.desc.labels.district.color",
    "watermark.enabled": "gui.style_editor.desc.watermark.enabled",
    "watermark.mod_name": "gui.style_editor.desc.watermark.mod_name",
    "watermark.position": "gui.style_editor.desc.watermark.position",
    "watermark.content": "gui.style_editor.desc.watermark.content",
    "procedural_objects.border": "gui.style_editor.desc.procedural_objects.border",
    "procedural_objects.custom_color_alpha": "gui.style_editor.desc.procedural_objects.custom_color_alpha",

    # ---- 《03》G 组续补（2026-09-15 第二批）----
    # 说明挂在「父路径」上时，`.fixed` / `.scaled` 两行共用同一条文案；
    # 挂在分组（dict）路径上时，由 theme_editor 的分组标题 tooltip 显示。
    "canvas": "gui.style_editor.desc.canvas",
    "buildings.default": "gui.style_editor.desc.buildings.default",
    "buildings.border_width": "gui.style_editor.desc.buildings.border_width",
    "buildings.shadow": "gui.style_editor.desc.buildings.shadow",
    "district.border_width": "gui.style_editor.desc.district.border_width",
    "zone.border_width": "gui.style_editor.desc.zone.border_width",
    "poi_icons.bg_circle_size": "gui.style_editor.desc.poi_icons.bg_circle_size",
    "poi_icons.name_size": "gui.style_editor.desc.poi_icons.name_size",
    "poi_icons.merge_distance": "gui.style_editor.desc.poi_icons.merge_distance",
    "labels.district": "gui.style_editor.desc.labels.district",
    "labels.road": "gui.style_editor.desc.labels.road",
    "labels.building": "gui.style_editor.desc.labels.building",
    "labels.park": "gui.style_editor.desc.labels.park",
    "labels.stop": "gui.style_editor.desc.labels.stop",
    "transports.label": "gui.style_editor.desc.transports.label",
    "watermark.margin": "gui.style_editor.desc.watermark.margin",
    "watermark.padding": "gui.style_editor.desc.watermark.padding",
}


# 预览场景下拉：auto = 跟随当前分类。
# 预览场景下拉：auto = 跟随当前分类。显示名 = gui.style_editor.preview_scene_<key>。
SCENE_OPTIONS = (
    "auto", "overall", "terrain", "water", "roads", "railway",
    "transports", "buildings", "zones",
)


# 分组 → 典型预览场景（未收录的分组回落到 overall）。
CATEGORY_SCENE = {
    "terrain": "terrain",
    "water": "water",
    "forest": "terrain",
    "roads": "roads",
    "railway": "railway",
    "transports": "transports",
    "buildings": "buildings",
    "district": "zones",
    "zone": "zones",
    "land_use": "zones",
    "poi_icons": "buildings",
    "labels": "roads",
}

# YAML 路径 → 字段键（_STYLE_FIELD_PATHS 的反向查询，用于借 schema 的中文名/范围）
_PATH_TO_FIELD = {".".join(path): key for key, path in STYLE_FIELD_PATHS.items()}


def section_title(key: str) -> str:
    """分组标题：能对应到侧栏图层的就复用它的标题，保证两处文案一致；
    侧栏没有的段（画布 / 森林）走 gui.style_editor.section.*。"""
    layer = SECTION_TO_LAYER.get(key, key)
    if layer in LAYER_TITLE_KEYS:
        return layer_title(layer)
    return tr(f"gui.style_editor.section.{key}")


def field_key_for(path: tuple) -> str | None:
    return _PATH_TO_FIELD.get(".".join(path))


def field_label(key: str, path: tuple = ()) -> str:
    """字段显示名：schema 的字段键优先（style.<key>.label），回退到叶子名兜底表。"""
    field_key = field_key_for(path)
    if field_key and field_key in FIELDS:
        return tr(f"style.{field_key}.label")
    tk = LABELS.get(key)
    return tr(tk) if tk else key


def field_desc(path: tuple) -> str:
    """字段说明：schema 路径上的说明（style.<key>.desc，随 `03` 文档落表后生效）
    优先，否则取内置说明表。两处都没有就返回空串，界面不占位。"""
    dotted = ".".join(path)
    field_key = _PATH_TO_FIELD.get(dotted)
    if field_key:
        dk = f"style.{field_key}.desc"
        if dk in catalog_keys():
            return tr(dk)
    tk = FIELD_DESC_FALLBACK.get(dotted)
    return tr(tk) if tk else ""


def format_number(value) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def num_range(key: str, value, path: tuple = ()) -> tuple:
    """数值调节范围 (最小, 最大, 步长)：显式规格 > schema 范围 > 名字启发式。"""
    dotted = ".".join(path)
    spec = numeric_spec(path)
    if spec is not None:
        return spec[0]
    field_key = _PATH_TO_FIELD.get(dotted)
    if field_key:
        field = FIELDS.get(field_key)
        if field is not None and field.low is not None and field.high is not None:
            step = 1 if isinstance(value, int) else 0.01
            return (float(field.low), float(field.high), step)
    if key == "azimuth":
        return (0, 360, 1)
    if key == "altitude":
        return (0, 90, 1)
    if "priority" in key:
        return (0, 100, 1)
    if key == "threshold":
        return (0, 3000, 1)
    if key in ("large_scale_meters", "small_scale_meters"):
        return (0, 5000, 10)
    if key in ("large_scale_weight", "small_scale_weight"):
        return (0.0, 1.0, 0.05)
    if "repeat" in key or "gap" in key:
        return (10, 20000, 10)
    if "grid" in key:
        return (10, 500, 10)
    if key in ("max_per_line", "max_per_cell", "smooth", "min_area",
               "upscale", "smooth_iters", "contour_upscale"):
        return (0, 200, 1)
    if key in ("height", "width") and isinstance(value, (int, float)) and value >= 500:
        return (0, 32768, 256)
    if "size" in key:
        return (0, 200, 1)
    if key in ("fixed", "scaled"):
        return (0.0, 50.0, 0.1)
    if key in ("intensity", "width_scale", "rdp_epsilon"):
        return (0.0, 5.0, 0.01)
    if key in ("alpha", "fill_alpha"):
        return (0, 255, 1)
    if isinstance(value, float):
        return (0.0, 50.0, 0.1)
    return (0, 2000, 1)


def pair_range(key: str, path: tuple = ()) -> tuple:
    """``{fixed, scaled}`` 组合字段的取值范围。"""
    dotted = ".".join(path)
    spec = numeric_spec(path)
    if spec is not None:
        return spec[0]
    field_key = _PATH_TO_FIELD.get(dotted)
    if field_key:
        field = FIELDS.get(field_key)
        if field is not None and field.low is not None and field.high is not None:
            return (float(field.low), float(field.high), 0.01)
    if "grid" in key:
        return (10, 500, 10)
    if "size" in key:
        return (0, 200, 1)
    if "repeat" in key or "gap" in key:
        return (10, 20000, 10)
    return (0.0, 50.0, 0.1)


def numeric_hint(key: str, lo, hi, path: tuple = ()) -> str:
    dotted = ".".join(path)
    spec = numeric_spec(path)
    unit_key = spec[1] if spec is not None else FIELD_UNIT_KEYS.get(key)
    unit = tr(_UNIT_KEYS[unit_key]) if unit_key in _UNIT_KEYS else ""
    suffix = f" {unit}" if unit else ""
    return tr("gui.style_editor.range",
              lo=format_number(lo), hi=format_number(hi), unit=suffix)


def parse_list(text: str) -> list:
    """把逗号/空格分隔文本解析为数值或字符串列表（支持中文逗号）。"""
    parts = [p.strip() for p in str(text).replace("，", ",").split(",") if p.strip()]
    out: list = []
    for part in parts:
        for caster in (int, float):
            try:
                out.append(caster(part))
                break
            except ValueError:
                continue
        else:
            out.append(part)
    return out


def list_to_text(value) -> str:
    if not isinstance(value, list):
        return str(value)
    return ", ".join(
        format_number(item) if isinstance(item, (int, float)) else str(item)
        for item in value
    )


def is_elevation_list(key: str, value) -> bool:
    """海拔颜色分级：阈值 + 颜色的可增删行列表。"""
    if not isinstance(value, list):
        return False
    if key in ("elevation_colors", "mountain_colors"):
        return True
    return bool(value) and all(
        isinstance(item, dict) and set(item.keys()) == {"threshold", "color"}
        for item in value
    )


def changed_leaf_count(before, after) -> int:
    """统计一段数据里发生变化的叶子数量（用于分组上的「· N 项」标记）。"""
    if isinstance(before, dict) and isinstance(after, dict):
        keys = set(before) | set(after)
        return sum(changed_leaf_count(before.get(k), after.get(k)) for k in keys)
    if isinstance(before, list) and isinstance(after, list):
        if len(before) != len(after):
            return 1
        return sum(changed_leaf_count(a, b) for a, b in zip(before, after))
    return 0 if before == after else 1


def get_path(data, path: tuple):
    cur = data
    for part in path:
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def set_path(data: dict, path: tuple, value) -> None:
    cur = data
    for part in path[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[path[-1]] = value


def describe_theme(data: dict) -> dict:
    """为每条实际主题路径提供类型化编辑元数据，包括数组元素。"""
    from .fonts import available_font_options
    from .ribbon_schema import TRANSIT_TYPES
    from .theme_contract import field_default

    fields = {}
    options = {
        "terrain.elevation_mode": ("continuous", "stepped"),
        "watermark.position": ("left_top", "right_top", "left_bottom", "right_bottom"),
        "land_use.detail": ("base", "detailed"),
        "buildings.color_mode": ("uniform", "poi"),
    }

    def describe(value, path):
        """按主题路径生成编辑器字段的类型、范围与展示信息。"""
        key = path[-1]
        dotted = ".".join(path)
        field_key = field_key_for(path)
        schema = FIELDS.get(field_key) if field_key else None
        declared_kind = field_kind(path)
        condition = ("terrain.hillshade.enabled == true" if dotted.startswith("terrain.hillshade.") and key != "enabled"
                     else "buildings.color_mode != uniform" if dotted.startswith("buildings.poi_tint.") else None)
        default = field_default(path)
        required = default is not None
        details = {"label": field_label(key, path), "description": field_desc(path),
                   "kind": "text", "optional": True,
                   "condition": condition, "defaultSource": "renderer" if required else "theme",
                   "supported": declared_kind is not None or (path[0] in SECTION_ORDER and key in LABELS),
                   "valueKind": declared_kind,
                   "hidden": key in HIDDEN_LEAF_KEYS.get(path[-2] if len(path) > 1 else "", set())}
        if isinstance(value, bool):
            details["kind"] = "toggle"
        elif isinstance(value, (int, float)):
            low, high, step = num_range(key, value, path)
            spec = numeric_spec(path)
            unit_key = spec[1] if spec else FIELD_UNIT_KEYS.get(key)
            details.update(kind="number", low=low, high=high, step=step,
                           unit=unit_key or "", unitLabel=tr(_UNIT_KEYS[unit_key]) if unit_key in _UNIT_KEYS else "",
                           hint=numeric_hint(key, low, high, path))
            if key in ("fixed", "scaled") and len(path) > 1:
                parent = fields.get(".".join(path[:-1]), {})
                if parent.get("kind") == "pair":
                    details.update(low=0, high=parent["high"], step=parent["step"], unit="px")
        elif isinstance(value, dict):
            details["kind"] = "object"
            if {"fixed", "scaled"} <= value.keys():
                low, high, step = pair_range(key, path)
                details.update(kind="pair", low=low, high=high, step=step,
                               unit="px", unitLabel=tr(_UNIT_KEYS["px"]), hint=numeric_hint(key, low, high, path))
        elif isinstance(value, list):
            if is_elevation_list(key, value):
                details["kind"] = "ramp"
            elif dotted == "transports.enabled":
                details.update(kind="types", choices=[{"value": kind, "label": tr(label)}
                                                      for kind, label in TRANSIT_TYPES])
            elif (declared_kind == "colors" or key in ("fill_colors", "colors", "palette")) and all(isinstance(v, str) and HEX_RE.fullmatch(v) for v in value):
                details["kind"] = "colors"
            elif all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
                details["kind"] = "numbers"
            elif all(isinstance(v, str) for v in value):
                details["kind"] = "strings"
            else:
                details["kind"] = "array"
        elif isinstance(value, str):
            if HEX_RE.fullmatch(value) or declared_kind == "color":
                details["kind"] = "color"
                details["alpha"] = len(value) == 9
            elif key == "font":
                details.update(kind="font", choices=[{"value": v, "label": label}
                                                      for v, label in available_font_options(value)])
            elif dotted in options:
                details.update(kind="choice", choices=[{"value": v, "label": tr(f"gui.style_editor.value.{v}")}
                                                        for v in options[dotted]])
            elif schema and schema.choices:
                details.update(kind="choice", choices=[{"value": v, "label": schema.option_label(v)}
                                                       for v in schema.choices])
        fields[dotted] = details
        if len(path) > 1 and fields.get(".".join(path[:-1]), {}).get("hidden", False):
            details["hidden"] = True
        details["optional"] = not required
        if key in {"enabled", "detail", "backend", "color_mode"}:
            details["optional"] = True
        if key in {"fixed", "scaled"}:
            details["supported"] = fields.get(".".join(path[:-1]), {}).get("supported", False)
        if default is not None:
            details["default"] = default
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                describe(child_value, path + (child_key,))
        elif isinstance(value, list):
            for index, child_value in enumerate(value):
                describe(child_value, path + (str(index),))
            if details["kind"] == "ramp":
                describe({"threshold": 0, "color": "#FFFFFF"}, path + ("*",))
            elif details["kind"] in {"colors", "numbers", "strings"}:
                describe("#FFFFFF" if details["kind"] == "colors" else 0.0 if details["kind"] == "numbers" else "", path + ("*",))
    for key, value in data.items():
        describe(value, (key,))
    return fields


def validate_theme_fields(data: dict, baseline: dict | None = None) -> None:
    """提交时使用编辑器显式字段范围；旧值在修改前保持有效，未知属性和枚举值属于兼容数据。"""
    fields = describe_theme(data)
    def walk(value, path):
        if path[:2] == ("terrain", "ground"):
            return
        dotted = ".".join(path)
        spec = numeric_spec(path)
        schema = FIELDS.get(field_key_for(path))
        field = fields[dotted]
        kind = field["valueKind"]
        valid = {
            "toggle": lambda: isinstance(value, bool),
            "number": lambda: isinstance(value, (int, float)) and not isinstance(value, bool),
            "size": lambda: not isinstance(value, bool) and (isinstance(value, (int, float)) or isinstance(value, dict) and bool({"fixed", "scaled"} & value.keys())),
            "color": lambda: isinstance(value, str) and HEX_RE.fullmatch(value),
            "choice": lambda: isinstance(value, (str, int)),
            "font": lambda: value is None or isinstance(value, str),
            "ramp": lambda: isinstance(value, list),
            "numbers": lambda: isinstance(value, list) and all(isinstance(n, (int, float)) and not isinstance(n, bool) for n in value),
            "colors": lambda: isinstance(value, list) and all(isinstance(c, str) and HEX_RE.fullmatch(c) for c in value),
            "types": lambda: isinstance(value, list) and all(isinstance(v, str) for v in value),
        }
        if kind in valid and not valid[kind]():
            raise ValueError(f"{dotted}: expected {kind}")
        if field["supported"] and field["kind"] == "number" and (spec or schema or path[-1] in ("fixed", "scaled")):
            old = baseline
            for part in path:
                old = old.get(part) if isinstance(old, dict) else old[int(part)] if isinstance(old, list) and int(part) < len(old) else None
            if value != old and not field["low"] <= value <= field["high"]:
                raise ValueError(f"{dotted}: expected {field['low']} to {field['high']}")
        if spec and (isinstance(value, bool) or not isinstance(value, (int, float, dict))
                     or isinstance(value, dict) and not ({"fixed", "scaled"} & value.keys())):
            raise ValueError(f"{dotted}: expected a number")
        if isinstance(value, dict):
            for key, child in value.items(): walk(child, path + (key,))
        elif isinstance(value, list):
            for index, child in enumerate(value): walk(child, path + (str(index),))
    for key, value in data.items():
        if key in SECTION_ORDER and not isinstance(value, dict):
            raise ValueError(f"{key}: expected an object")
        walk(value, (key,))
