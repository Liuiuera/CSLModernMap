"""定义功能区的展示结构和界面元数据。

**本模块不含任何文案。** 字段名与各选项名都是翻译键，实际文字只存在于

    style.<字段键>.label              字段名      style.terrain.mode.label
    style.<字段键>.value.<选项值>      下拉项      style.terrain.mode.value.shade
    style.<字段键>.desc               字段说明    style.terrain.mode.desc
    gui.layer.title.<图层键>          图层标题    gui.layer.title.terrain
    gui.transit_type.<交通类型>       车别名      gui.transit_type.Metro

这样改文案 = 改 JSON，不碰代码；加第三种语言也不用改本文件。

本模块**只**依赖 ``csmodernmap.i18n.tr`` 取词；其它取值机制（``local(zh, en)``、
按中文原文反查的 ``tr_legacy``、内嵌中英对照表、构造后遍历控件改文字）
均不使用。各语言目录须保持一致的键与占位符。
"""
from dataclasses import dataclass

from csmodernmap.i18n import tr


@dataclass(frozen=True)
class Field:
    """一个字段的展示元数据：叫什么、什么类型、取值范围。值本身不在这里。"""
    key: str              # 字段键；所有翻译键由它派生
    default: object
    kind: str = 'choice'
    choices: tuple = ()   # 选项内部值，顺序即下拉顺序
    low: float = 0
    high: float = 100

    @property
    def label_key(self) -> str:
        return f"style.{self.key}.label"

    @property
    def label(self) -> str:
        """字段显示名（当前语言）。"""
        return tr(self.label_key)

    def option_key(self, value) -> str:
        return f"style.{self.key}.value.{value}"

    def option_label(self, value) -> str:
        """下拉项显示名（当前语言）。"""
        return tr(self.option_key(value))


def choice(key, *values):
    return Field(key, values[0], choices=tuple(values))


def toggle(key, value=True):
    return Field(key, value, 'toggle')


def number(key, value, low, high):
    return Field(key, value, 'number', low=low, high=high)


def color(key, value):
    return Field(key, value, 'color')


# 「地形表现」只有两种画法：只按海拔分层设色，或在色带之上再叠一层山影。
# 曾经存在过的「海拔渐变 / 立体棱角」已下线（后者会把地形切成多边形面片）。
TERRAIN_MODES = ('simple', 'shade')

# 旧取值 → 现取值。老设置文件与老主题里都可能还留着这些词，读到时归一，
# 否则下拉框会读出一个已经不存在的选项、渲染端也会拿到认不出的模式。
_TERRAIN_MODE_ALIASES = {
    'gradient': 'shade',   # 旧「海拔渐变」：本来也是叠山影的那一档
    'linear': 'shade',
    'facets': 'shade',     # 旧「立体棱角」：功能已删除，视觉上并入带山影那档
    'faceted': 'shade',
    'polygon': 'shade',
    'low-poly': 'shade',
    'low_poly': 'shade',
    'smooth': 'shade',     # 旧主题里 hillshade.mode 的词，一并当"带山影"
}


def normalize_terrain_mode(value) -> str:
    """把任意外部取值归一到 ``TERRAIN_MODES`` 之一；认不出来就按"分层设色"。"""
    text = str(value if value is not None else '').strip().lower()
    text = _TERRAIN_MODE_ALIASES.get(text, text)
    return text if text in TERRAIN_MODES else TERRAIN_MODES[0]


FIELDS = {
 'theme': choice('theme', 'modern', 'amap', 'google', 'dark', 'paper'),
 'size': choice('size', '4k', '2k', '8k', 'custom'),
 'range': choice('range', 'city', 'center', 'selection', 'custom'),
 'rotation': choice('rotation', '0', '90', '180', '270'),
 'quality': choice('quality', 'standard', 'high', 'best'),
 'terrain.mode': choice('terrain.mode', *TERRAIN_MODES),
 'terrain.color': color('terrain.color', '#EBF0CF'),
 'terrain.forest': color('terrain.forest', '#BFE89D'),
 'terrain.shade': number('terrain.shade', 35, 0, 100),
 'terrain.sun': number('terrain.sun', 315, 0, 360),
 'water.color': color('water.color', '#8DDBF6'),
 'water.border': color('water.border', '#72C3E2'),
 'water.width': number('water.width', 1, 0, 8),
 'water.smooth': toggle('water.smooth'),
 'road.width': number('road.width', 100, 25, 200),
 'road.color': color('road.color', '#FFFFFF'),
 'road.highway': color('road.highway', '#F4C77A'),
 'road.border': color('road.border', '#C6C5C0'),
 'road.tunnel': toggle('road.tunnel'),
 'road.bridge': toggle('road.bridge'),
 'road.names': toggle('road.names'),
 'road.game_names': toggle('road.game_names', False),
 'road.accurate_intersections': toggle('road.accurate_intersections', True),
 'building.color_mode': choice('building.color_mode', 'poi', 'uniform'),
 'building.default': color('building.default', '#D8D2C6'),
 'building.poi_tint.contrast': number('building.poi_tint.contrast', 30, 0, 100),
 # 旧别名仍可供旧客户端解析，但不显示在页面中。
 'building.scheme': choice('building.scheme', 'original', 'light', 'dark'),
 'building.color': color('building.color', '#D8D2C6'),
 'building.border': color('building.border', '#B9B2A5'),
 'building.shadow': toggle('building.shadow', False),
 'built_area.color': color('built_area.color', '#F1F0EB'),
 'railway.color': color('railway.color', '#707780'),
 'railway.sleepers': toggle('railway.sleepers'),
 'traffic.free': color('traffic.free', '#61A96A'),
 'traffic.slow': color('traffic.slow', '#E6B54D'),
 'traffic.busy': color('traffic.busy', '#D85B4A'),
 'transport.stations': toggle('transport.stations'),
 'transport.names': toggle('transport.names'),
 'transport.station_names': toggle('transport.station_names', False),
 'transport.width': number('transport.width', 3, 1, 12),
 'transport.size': number('transport.size', 8, 4, 24),
 'transport.merge': toggle('transport.merge'),
 'transport.distance': number('transport.distance', 48, 0, 200),
 'transport.mask': number('transport.mask', 70, 0, 100),
 'transport.legend': choice('transport.legend', 'br', 'bl', 'none'),
 'transport.font': number('transport.font', 12, 8, 24),
 'metro.names': choice('metro.names', 'all', 'custom', 'none'),
 'metro.language': choice('metro.language', 'zh', 'bilingual'),
 'metro.palette': choice('metro.palette', 'game', 'classic', 'soft'),
 'metro.title': Field('metro.title', '', 'text'),
 'poi.set': choice('poi.set', 'modern', 'modern-dark'),
 'poi.size': number('poi.size', 24, 12, 48),
 'poi.names': toggle('poi.names'),
 'poi.game_names': toggle('poi.game_names', False),
 'poi.density': choice('poi.density', 'auto', 'sparse', 'full'),
 'poi.merge': toggle('poi.merge'),
 'poi.distance': number('poi.distance', 48, 0, 200),
 'poi.font': Field('poi.font', 'SimHei', 'font'),
 'poi.name_size': number('poi.name_size', 11, 6, 48),
 'poi.name_stroke': color('poi.name_stroke', '#FFFFFF'),
 'poi.name_stroke_ratio': number('poi.name_stroke_ratio', 0.12, 0, 0.5),
 'label.font.road': Field('label.font.road', 'Microsoft YaHei', 'font'),
 'label.font.district': Field('label.font.district', 'Microsoft YaHei', 'font'),
 'label.font.building': Field('label.font.building', 'Microsoft YaHei', 'font'),
 'label.font.park': Field('label.font.park', 'Microsoft YaHei', 'font'),
 'label.font.stop': Field('label.font.stop', 'Microsoft YaHei', 'font'),
 'label.size': number('label.size', 12, 8, 32),
 'label.color': color('label.color', '#39424C'),
 'label.outline': toggle('label.outline'),
 'label.roads': toggle('label.roads'),
 'label.districts': toggle('label.districts'),
 'district.color': color('district.color', '#A6A6AD'),
 'district.width': number('district.width', 1, 0, 8),
 'district.opacity': number('district.opacity', 45, 0, 100),
 'zone.color': color('zone.color', '#A89880'),
 'zone.opacity': number('zone.opacity', 70, 0, 100),
 'land_use.opacity': number('land_use.opacity', 45, 0, 100),
 'land_use.grid': toggle('land_use.grid'),
 'watermark.text': Field('watermark.text', 'CSLModernMap', 'text'),
 'watermark.position': choice('watermark.position', 'br', 'bl'),
 'watermark.opacity': number('watermark.opacity', 40, 0, 100),
 'procedural_objects.color': color('procedural_objects.color', '#D8D2C6'),
 'procedural_objects.border': toggle('procedural_objects.border'),
 'edit.filter': choice('edit.filter', 'all', 'road', 'building', 'icon', 'label'),
 'edit.snap': toggle('edit.snap'),
 'edit.connected': toggle('edit.connected'),
 'edit.lock': toggle('edit.lock', False),
 'edit.name': Field('edit.name', '', 'text'),
 'edit.offset_x': number('edit.offset_x', 0, -200, 200),
 'edit.offset_y': number('edit.offset_y', 0, -200, 200),
 'edit.width': number('edit.width', 3, 1, 24),
 'edit.color': color('edit.color', '#2772E2'),
 'view.sidebar': toggle('view.sidebar'),
 'view.info': toggle('view.info'),
 'view.status': toggle('view.status'),
 'view.grid': toggle('view.grid', False),
 'view.rulers': toggle('view.rulers', False),
}

# 文字类别。主题 YAML 的 ``labels`` 段就是这五类（road / district / building /
# park / stop），导出侧 ``labeling/style_resolver.py`` 也是按类各取一份样式，
# 所以「字体 / 字号 / 颜色 / 描边」都该能按类单独设 —— 之前 GUI 只暴露了道路一份。
# 显示名取 ``style.label.size.<类>.label``（由 FIELDS 派生，不另写一套）。
LABEL_KINDS: tuple = ('road', 'district', 'building', 'park', 'stop',)

# 各类文字的默认字号对齐 Viewer 里的最小字号（labelFontSize 的 minimums），
# 免得"设了 12 却比屏幕上看到的 18 小"这种对不上的观感。
_LABEL_KIND_SIZE = {"road": 12, "district": 18, "building": 11, "park": 14, "stop": 12}
for _kind in LABEL_KINDS:
    FIELDS[f'label.stroke_width.{_kind}'] = number(f'label.stroke_width.{_kind}', 2, 0, 12)
    FIELDS[f'label.stroke_color.{_kind}'] = color(f'label.stroke_color.{_kind}', '#FFFFFF')
    FIELDS['label.size.road'] = number('label.size.road', _LABEL_KIND_SIZE['road'], 8, 32)
    FIELDS['label.color.road'] = color('label.color.road', '#39424C')
    FIELDS['label.outline.road'] = toggle('label.outline.road')
    FIELDS['label.size.district'] = number('label.size.district', _LABEL_KIND_SIZE['district'], 8, 32)
    FIELDS['label.color.district'] = color('label.color.district', '#39424C')
    FIELDS['label.outline.district'] = toggle('label.outline.district')
    FIELDS['label.size.building'] = number('label.size.building', _LABEL_KIND_SIZE['building'], 8, 32)
    FIELDS['label.color.building'] = color('label.color.building', '#39424C')
    FIELDS['label.outline.building'] = toggle('label.outline.building')
    FIELDS['label.size.park'] = number('label.size.park', _LABEL_KIND_SIZE['park'], 8, 32)
    FIELDS['label.color.park'] = color('label.color.park', '#39424C')
    FIELDS['label.outline.park'] = toggle('label.outline.park')
    FIELDS['label.size.stop'] = number('label.size.stop', _LABEL_KIND_SIZE['stop'], 8, 32)
    FIELDS['label.color.stop'] = color('label.color.stop', '#39424C')
    FIELDS['label.outline.stop'] = toggle('label.outline.stop')

# 道路等级。主题 YAML 的 ``roads`` 段本来就是按等级分组的（highway / main /
# branch / special / beautification），导出侧 ``road_hierarchy`` 按同样这套等级
# 挑样式、预览侧 ``preview_scene.road_theme`` 也按它下发 paint tokens。
# 整改前 GUI 只把「普通道路」「高速公路」两个颜色摊出来，而且「普通道路」是
# 一个色刷遍所有等级、「道路描边」连高架和隧道一起刷 —— 等级之间没法分别调。
ROAD_LEVELS: tuple = ('highway', 'main', 'branch', 'special', 'beautification',)

# 各等级默认的最小路面宽度，与导出侧 ``road_layer._LEVEL_MIN_PIX`` 同源。
_ROAD_LEVEL_MIN_PX = {"highway": 1.8, "main": 1.4, "branch": 0.8,
                      "special": 0.8, "beautification": 0.5}
# 兜底配色（只在主题缺该等级的配置时用得上，五个内置主题都带这些键）。
_ROAD_LEVEL_FILL = {"highway": "#CDCFD5", "main": "#ECEEF2", "branch": "#FFFFFF",
                    "special": "#F0F2F5", "beautification": "#F0F2F5"}
_ROAD_LEVEL_BORDER = {"highway": "#AFB1B7", "main": "#BAC0CA", "branch": "#C3C8D2",
                      "special": "#E1E3E9", "beautification": "#E1E3E9"}
for _lvl in ROAD_LEVELS:
    FIELDS['road.level.highway.fill'] = color(
        'road.level.highway.fill', _ROAD_LEVEL_FILL['highway'])
    FIELDS['road.level.highway.border'] = color(
        'road.level.highway.border', _ROAD_LEVEL_BORDER['highway'])
    FIELDS['road.level.highway.width'] = number(
        'road.level.highway.width', _ROAD_LEVEL_MIN_PX['highway'], 0.2, 6.0)
    FIELDS['road.level.main.fill'] = color(
        'road.level.main.fill', _ROAD_LEVEL_FILL['main'])
    FIELDS['road.level.main.border'] = color(
        'road.level.main.border', _ROAD_LEVEL_BORDER['main'])
    FIELDS['road.level.main.width'] = number(
        'road.level.main.width', _ROAD_LEVEL_MIN_PX['main'], 0.2, 6.0)
    FIELDS['road.level.branch.fill'] = color(
        'road.level.branch.fill', _ROAD_LEVEL_FILL['branch'])
    FIELDS['road.level.branch.border'] = color(
        'road.level.branch.border', _ROAD_LEVEL_BORDER['branch'])
    FIELDS['road.level.branch.width'] = number(
        'road.level.branch.width', _ROAD_LEVEL_MIN_PX['branch'], 0.2, 6.0)
    FIELDS['road.level.special.fill'] = color(
        'road.level.special.fill', _ROAD_LEVEL_FILL['special'])
    FIELDS['road.level.special.border'] = color(
        'road.level.special.border', _ROAD_LEVEL_BORDER['special'])
    FIELDS['road.level.special.width'] = number(
        'road.level.special.width', _ROAD_LEVEL_MIN_PX['special'], 0.2, 6.0)
    FIELDS['road.level.beautification.fill'] = color(
        'road.level.beautification.fill', _ROAD_LEVEL_FILL['beautification'])
    FIELDS['road.level.beautification.border'] = color(
        'road.level.beautification.border', _ROAD_LEVEL_BORDER['beautification'])
    FIELDS['road.level.beautification.width'] = number(
        'road.level.beautification.width', _ROAD_LEVEL_MIN_PX['beautification'], 0.2, 6.0)

LAYER_FIELDS = {
 'terrain':('terrain.mode','terrain.color','terrain.forest','terrain.shade','terrain.sun'),
 'water':('water.color','water.border','water.width','water.smooth'),
 # 整体项在前，再按等级排（每级 填色 / 描边 / 宽度 挨在一起）。
 # 旧的 road.color / road.highway / road.border 已被等级分项取代、从面板撤下，
 # 但 FIELDS 与 handler 保留：老配置写进的就是各等级自己的键，仍然生效。
 'road':('road.width','road.tunnel','road.bridge','road.names','road.accurate_intersections') + tuple(
     f'road.level.{_lvl}.{_attr}'
     for _lvl in ROAD_LEVELS
     for _attr in ('fill', 'border', 'width')
 ),
 'building':('building.color_mode','building.default',
             'building.poi_tint.contrast','building.border','building.shadow'),
 'built_area':('built_area.color',),
 'poi_area':(),
 'railway':('railway.color','railway.sleepers'),
 'traffic':('traffic.free','traffic.slow','traffic.busy'),
 'metro':('transport.stations','transport.station_names','transport.width','transport.size','transport.merge','transport.distance'),
 'poi':('poi.set','poi.size','poi.font','poi.name_size',
        'poi.name_stroke','poi.name_stroke_ratio'),
 # 按文字类别排：每类 字体 / 字号 / 颜色 / 描边 四项挨在一起。
 'label':tuple(
     f'label.{_prefix}.{_kind}'
     for _kind in LABEL_KINDS
     for _prefix in ('font', 'size', 'color', 'outline')
 ),
 'district':('district.color','district.width','district.opacity'),
 'zone':('zone.color','zone.opacity'),
 # 共享图层操作已控制可见性，不再展示无法影响实时预览的重复开关。
 'land_use':('land_use.opacity',),
 'watermark':('watermark.text','watermark.position','watermark.opacity'),
 'procedural_objects':('procedural_objects.color','procedural_objects.border'),
}

# 父图层可见性仅决定子控件是否可操作，不额外保存设置值。
FIELD_DEPENDENCIES = {
    "poi.names": ("label", "poi"),
    "transport.names": ("label", "poi"),
    "road.names": "label",
    "label.districts": "label",
    "poi.set": "poi",
    "poi.size": "poi",
    "poi.density": "poi",
    "poi.merge": "poi",
    "poi.distance": "poi",
}
for _kind in LABEL_KINDS:
    for _prefix in ("font", "size", "color", "outline"):
        FIELD_DEPENDENCIES[f"label.{_prefix}.{_kind}"] = "label"

# 图层行保持声明式结构：主按钮切换图层，小型设置入口打开对应面板。
LAYER_SETTINGS = {
    key: f"layer:{key}" for key in LAYER_FIELDS
}

TRANSIT_TYPES: tuple = (
    ('Metro', 'gui.transit_type.Metro'),
    ('Train', 'gui.transit_type.Train'),
    ('Monorail', 'gui.transit_type.Monorail'),
    ('Tram', 'gui.transit_type.Tram'),
    ('Bus', 'gui.transit_type.Bus'),
    ('Trolleybus', 'gui.transit_type.Trolleybus'),
    ('CableCar', 'gui.transit_type.CableCar'),
    ('Ship', 'gui.transit_type.Ship'),
    ('Ferry', 'gui.transit_type.Ferry'),
    ('Airplane', 'gui.transit_type.Airplane'),
    ('Helicopter', 'gui.transit_type.Helicopter'),
    ('Blimp', 'gui.transit_type.Blimp'),
)

# 图层分组的显示名 → 翻译键。侧栏标题与主题编辑器共用同一份，避免两处各写一套。
LAYER_TITLE_KEYS = {
 'terrain': 'gui.layer.title.terrain',
 'water': 'gui.layer.title.water',
 'road': 'gui.layer.title.road',
 'building': 'gui.layer.title.building',
 'built_area': 'gui.layer.title.built_area',
 'poi_area': 'gui.layer.title.poi_area',
 'traffic': 'gui.layer.title.traffic',
 'procedural_objects': 'gui.layer.title.procedural_objects',
 'railway': 'gui.layer.title.railway',
 'poi': 'gui.layer.title.poi',
 'label': 'gui.layer.title.label',
 'watermark': 'gui.layer.title.watermark',
 'district': 'gui.layer.title.district',
 'zone': 'gui.layer.title.zone',
 'land_use': 'gui.layer.title.land_use',
 'metro': 'gui.layer.title.metro',
}


def layer_title(key: str) -> str:
    """图层/分组标题；未收录的键原样返回（便于发现漏配）。"""
    tk = LAYER_TITLE_KEYS.get(key)
    return tr(tk) if tk else str(key)
