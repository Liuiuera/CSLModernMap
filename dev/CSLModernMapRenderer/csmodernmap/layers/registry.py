"""统一登记 GUI、命令行、渲染器和网页输出使用的图层。

图层类延迟导入，便于不同入口安全使用；metro 保留为界面和设置名称，transit 是协议与命令行的标准键。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LayerRegistration:
    key: str
    title: str
    content: str
    z: int
    modes: tuple[str, ...]
    layer_cls: type
    default_on: bool = True
    gui_default_on: bool = True
    aliases: tuple[str, ...] = ()
    gui_key: str | None = None

    @property
    def settings_key(self) -> str:
        return self.gui_key or self.key


def _directory() -> tuple[LayerRegistration, ...]:
    # 在此导入图层类，避免登记表与样式或模型代码形成循环依赖。
    from .building_layer import BuildingLayer
    from .built_area_layer import BuiltAreaLayer
    from .district_layer import DistrictLayer
    from .label_layer import LabelLayer
    from .land_use_layer import LandUseLayer
    from .poi_area_layer import POIAreaLayer
    from .poi_icon_layer import POIIconLayer
    from .po_layer import POLayer
    from .railway_layer import RailwayLayer
    from .road_layer import RoadLayer
    from .terrain_layer import TerrainLayer
    from .traffic_layer import TrafficLayer
    from .transport_layer import TransportLayer
    from .water_layer import WaterLayer
    from .watermark_layer import WatermarkLayer
    from .zone_layer import ZoneLayer

    rows = (
        ("terrain", "gui.layer.terrain", "terrain", True, True, TerrainLayer, ("地形",)),
        ("built_area", "gui.layer.built_area", "built_area", True, True, BuiltAreaLayer, ("建成区",)),
        ("poi_area", "gui.layer.poi_area", "poi_area", True, False, POIAreaLayer, ("POI足迹",)),
        ("land_use", "gui.layer.land_use", "land_use", False, False, LandUseLayer, ("用地类型",)),
        ("water", "gui.layer.water", "water", True, True, WaterLayer, ("水域",)),
        ("zone", "gui.layer.zone", "zone", False, False, ZoneLayer, ("功能区",)),
        ("building", "gui.layer.building", "building", True, True, BuildingLayer, ("建筑",)),
        ("road", "gui.layer.road", "road", True, True, RoadLayer, ("道路",)),
        ("railway", "gui.layer.railway", "railway", True, False, RailwayLayer, ("铁路",)),
        ("traffic", "gui.layer.traffic", "traffic", True, False, TrafficLayer, ("交通",)),
        ("procedural_objects", "gui.layer.procedural_objects", "procedural_objects", True, False, POLayer, ("PO魔物", "po")),
        ("transit", "gui.layer.transit", "transit", True, False, TransportLayer, ("公共交通", "交通线路", "地铁", "transport", "metro")),
        ("district", "gui.layer.district", "district", False, False, DistrictLayer, ("区划", "区划边界")),
        ("poi", "gui.layer.poi", "poi", True, True, POIIconLayer, ("POI图标", "兴趣点（POI）彩色图标", "poi_icons")),
        ("label", "gui.layer.label", "label", True, False, LabelLayer, ("文字", "区划名、道路名")),
        ("watermark", "gui.layer.watermark", "watermark", True, False, WatermarkLayer, ("水印",)),
    )
    result: list[LayerRegistration] = []
    for z, (key, title, content, cli_default, gui_default, cls, aliases) in enumerate(rows):
        result.append(LayerRegistration(
            key=key,
            title=title,
            content=content,
            z=z,
            modes=("normal", "svg", "web_tile", "transit", "metro"),
            layer_cls=cls,
            default_on=cli_default,
            gui_default_on=gui_default,
            aliases=tuple(aliases),
            gui_key=("metro" if key == "transit" else key),
        ))
    return tuple(result)


LAYER_DIRECTORY = _directory()


def layer_directory() -> tuple[LayerRegistration, ...]:
    return LAYER_DIRECTORY


def painter_order_keys() -> tuple[str, ...]:
    """返回各渲染后端共用的有效 PNG 绘制顺序，保留普通和延后图层的分组。"""
    early = tuple(item.key for item in LAYER_DIRECTORY
                  if not getattr(item.layer_cls, "late", False))
    late = tuple(item.key for item in LAYER_DIRECTORY
                 if getattr(item.layer_cls, "late", False))
    return early + late


def legacy_layer_registry() -> list[tuple[str, str, type, bool]]:
    """为迁移中的调用方返回旧版命令行元组结构。"""
    return [(_LEGACY_CN.get(item.key, item.key), item.key,
             item.layer_cls, item.default_on)
            for item in LAYER_DIRECTORY]


def gui_layer_registry() -> list[tuple[str, type, bool, str]]:
    """为旧界面调用方返回兼容元组，不复制图层事实。"""
    return [(item.settings_key, item.layer_cls, item.gui_default_on, item.title)
            for item in LAYER_DIRECTORY if item.key != "procedural_objects"]


def layer_aliases() -> dict[str, str]:
    result: dict[str, str] = {}
    for item in LAYER_DIRECTORY:
        result[item.key] = item.key
        result[item.key.lower()] = item.key
        result[item.settings_key] = item.key
        for alias in item.aliases:
            result[alias] = item.key
            result[alias.lower()] = item.key
    return result


def layer_class(key: str) -> type | None:
    canonical = layer_aliases().get(str(key).strip())
    for item in LAYER_DIRECTORY:
        if item.key == canonical:
            return item.layer_cls
    return None


def canonical_layer_key(value: Any) -> str:
    """将命令行、设置或图层实例名称规范化为协议键。"""
    text = str(value or "").strip()
    return layer_aliases().get(text, layer_aliases().get(text.lower(), text))


_WEB_TILE_KEYS = (
    "terrain", "water", "road", "building", "procedural_objects",
    "transit", "poi", "label",
)


_LEGACY_CN = {
    "terrain": "地形", "built_area": "建成区", "poi_area": "POI足迹",
    "land_use": "用地类型", "water": "水域", "building": "建筑",
    "road": "道路", "railway": "铁路", "traffic": "交通",
    "procedural_objects": "PO魔物", "transit": "公共交通",
    "district": "区划", "zone": "功能区", "poi": "POI图标",
    "label": "文字", "watermark": "水印",
}


def default_layer_keys(mode: str = "normal") -> tuple[str, ...]:
    mode = str(mode or "normal").lower()
    if mode == "web_tile":
        return _WEB_TILE_KEYS
    if mode == "transit":
        return ("terrain", "built_area", "water", "building", "road", "railway",
                "traffic", "procedural_objects", "district", "zone")
    if mode == "metro":
        return ("transit",)
    return tuple(item.key for item in LAYER_DIRECTORY if item.default_on)


def instantiate(keys: tuple[str, ...] | list[str] | None = None,
                *, mode: str = "normal") -> list[Any]:
    wanted = tuple(keys) if keys is not None else default_layer_keys(mode)
    classes = {item.key: item.layer_cls for item in LAYER_DIRECTORY}
    aliases = layer_aliases()
    return [classes[aliases.get(str(key), str(key))]() for key in wanted
            if aliases.get(str(key), str(key)) in classes]
