"""GUIR 控制器 — GUI 无关的后端。

``GuirBackend`` 把文档会话、常驻渲染子进程、作业装配、输出、样式覆盖与
设置持久化集中到一处，供 GUIR 复用；本模块不依赖任何 GUI 控件，通过
``UIScheduler`` 调度 + 回调与 UI 通信（与 ``preview_controller`` 同风格）。

- 文档：``cmm.load_document`` + ``DocumentSession``（按文件路径缓存）
- 渲染：multiprocessing spawn + 双队列 + UI scheduler 轮询；消息协议复用
  ``gui_render_worker.worker_loop``，不做任何修改
- 样式：``style_overrides[theme_stem]`` 合并主题 yaml；图层侧栏参数写入覆盖
- 选区：图像像素 → 游戏坐标换算 + 输出边长自适应
"""
from __future__ import annotations

from . import i18n

from .style import style_scalar

import atexit
import copy
import math
import logging
import os
import queue
import random
import shutil
import subprocess
import sys
import threading
import tempfile
import time
import traceback
from pathlib import Path

from PIL import Image

from ._paths import application_settings_dir, poi_mapping_override_path, resource_dir, user_data_dir, writable_dir
from .cmm import load_document
from .cmm.session import DocumentSession
from .export import export_png
from .i18n import tr
from . import gui_render_worker as _render_worker
from .layers.building_layer import BuildingLayer
from .layers.built_area_layer import BuiltAreaLayer
from .layers.district_layer import DistrictLayer, visible_administrative_districts
from .layers.poi_density import POI_DENSITY_PRESETS, poi_density_params
from .layers.railway_layer import RailwayLayer
from .layers.registry import gui_layer_registry, layer_directory
from .layers.road_layer import RoadLayer
from .road_hierarchy import RoadLevel, road_style_candidates
from .layers.terrain_layer import TerrainLayer
from .layers.transport_layer import TransportLayer
from .layers.water_layer import WaterLayer
from .layers.zone_layer import ZoneLayer
from .preview_controller import PreviewRequestController
from .preview_scene import (PreviewSceneCompiler, PreviewSceneServer,
                            preview_theme)
from .export_region import normalize_bounds, output_dimensions
from .map_extent import document_city_bounds, document_tile_grid, square_region
from .regions import closeup_region
from .render_jobs import RegionPolicy
from .renderer import Renderer
from .ribbon_schema import normalize_terrain_mode
from .settings_store import load_settings, save_settings
from .poi_profiles import PROFILE_SETTING_KEY, canonical_profile_target, normalize_poi_profiles, normalize_poi_visibility_snapshot, validate_poi_profile
from .system_info import automatic_antialias
from .output_profiles import (
    OUTPUT_PROFILE_SETTING_KEY,
    normalize_output_profiles,
    validate_output_profile,
)
from .style import (Style, load_style,
                    strip_retired_region_palette_fields)
from .transit import TYPE_REGISTRY
from .view_settings import (default_view_settings, load_view_settings,
                             migrate_legacy_settings, save_view_settings,
                             views_path)

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------

PREVIEW_SIZE = 1024
RECENT_MAX = 6


WORLD_HALF = 8640.0

# 主题显示名（**翻译键**，取值处再 tr()）→ stem（与 guir 下拉一致；
# metro_map.yaml 不作为可选主题）。模块级常量在导入期求值，不能直接放译文。
THEME_CHOICES = (
    ("gui.theme.choice.apple_maps", "apple_maps"),
    ("gui.theme.choice.amap", "amap"),
    ("gui.theme.choice.google", "google"),
    ("gui.theme.choice.dark", "dark"),
    ("gui.theme.choice.vintage", "vintage"),
)

#: 主题显示名（**各语言的译值**）与旧 stem → 当前 stem。只用于**读回旧值**，
#: 不参与显示。
#:
#: 为什么要连英文显示名一起列：设置项与导出配置里存的是「当时界面上的主题名」，
#: 用户在英文界面下选的主题，切回中文后仍要认得出来。所以这里的解析**与当前
#: 语言无关**，不能靠 tr() 现查 —— 那会让同一份数据在不同语言下解析出不同结果。
#: 改 ``gui.theme.choice.*`` 的译值时必须同步这张表。
_LEGACY_THEME_NAMES = {
    "Apple Maps": "apple_maps",
    "高德地图": "amap", "Amap": "amap",
    "Google": "google",
    "深色地图": "dark", "Dark": "dark",
    "纸质地图": "vintage", "Paper": "vintage",
    # 早期主题名（`gui.theme.<stem>.name` 那一代的措辞）。当前
    # ``gui.theme.choice.*`` 的译值已改回这一套，所以中文侧同时就是「当前译值」。
    "轻简现代": "apple_maps", "明亮地图": "amap", "暖色自然": "google",
    "暗黑风格": "dark", "复古风格": "vintage",
    # 同一套名字的英文译值（`gui.theme.<stem>.name` 的 en 侧）。
    "Minimal Modern": "apple_maps", "Bright Map": "amap",
    "Warm Natural": "google", "Dark Style": "dark",
    "Vintage Style": "vintage",
    # 早期 stem
    "modern": "apple_maps", "paper": "vintage",
}


def _theme_stem_for(value: str) -> str | None:
    """把 stem / 翻译键 / 任何语言的显示名 / 历史名统一解析成 stem（与当前语言无关）。

    解析不出来返回 None：调用方按失败处理，不静默改主题。
    """
    for name_key, stem in THEME_CHOICES:
        if value in (stem, name_key):
            return stem
    return _LEGACY_THEME_NAMES.get(value)


def _baseline_1024(value, default: float = 12.0) -> float:
    """把主题里的字号换算成「长边 1024px 成图」的等效像素值。

    主题里同一项有两种写法：``{fixed, scaled}``（``scaled`` = 期望值 × 4，
    见 styles/*.yaml 里 transit_view 段的注释）与直接写标量。这里统一成
    「@1024 标量」语义，与 ``preview_scene._preview_baseline_size`` 同式，
    否则同一份主题在预览与视图契约里会解析出两个不同的字号。
    """
    if isinstance(value, dict):
        try:
            value = (float(value.get("fixed", 0.0) or 0.0)
                     + float(value.get("scaled", 0.0) or 0.0) * 0.25)
        except (TypeError, ValueError):
            return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)

#: 旧版「范围」下拉的显示值（中英都列 —— 历史配置里两种语言都可能存过）
#: → ``(n, anchor)``。新版只有一个数字 n（全图的 1/n 见方）加一个定位方式。
#: 这张表**只用于读旧配置**、不参与显示，因此必须与当前语言无关：早先的实现
#: 直接拿界面文字当键，英文界面下匹配不上就会静默回落成「整个城市」。
_LEGACY_RANGE_PRESETS = {
    "整个城市": (1, "center"),
    "Entire city": (1, "center"),
    "9*9整个地图": (1, "center"),
    "9×9 完整地图": (1, "center"),
    "9×9 全图": (1, "center"),
    "城市中心 5×5": (2, "center"),
    "City centre 5×5": (2, "center"),
    "5×5 地图中心": (2, "center"),
    "繁华区域 3×3": (3, "busiest"),
    "Busiest area 3×3": (3, "busiest"),
    "3×3 繁华地带": (3, "busiest"),
}

#: 旧版内部模式名 → ``(n, anchor)``（更早的配置把模式名直接存过）。
_LEGACY_RANGE_MODES = {
    "full": (1, "center"),
    "center5": (2, "center"),
    "best3": (3, "busiest"),
    "custom": (1, "center"),
}

#: 范围大小 n 的取值范围。n = 全图的 1/n 见方，1 即整幅。
RANGE_N_MIN = 1
RANGE_N_MAX = 9

#: 范围定位方式。``center`` 居中；``busiest`` 取建筑最密集的那一块。
RANGE_ANCHORS = ("center", "busiest")


def _range_from_legacy(value) -> tuple[int, str]:
    """把旧配置里的范围值解析成 ``(n, anchor)``；认不出来就整幅居中。"""
    text = str(value or "").strip()
    if text in _LEGACY_RANGE_PRESETS:
        return _LEGACY_RANGE_PRESETS[text]
    return _LEGACY_RANGE_MODES.get(text.casefold(), (1, "center"))

# 字段键 → style.data 路径（部分字段需特殊映射，见 _UI_FIELD_HANDLERS）
_STYLE_FIELD_PATHS = {
    "terrain.color": ("terrain", "elevation_colors", "0", "color"),
    "terrain.forest": ("forest", "color"),
    "water.color": ("water", "color"),
    "water.border": ("water", "border"),
    "water.width": ("water", "border_width"),
    "road.highway": ("roads", "highway", "fill"),
    "road.tunnel": ("roads", "tunnel", "enabled"),
    "road.bridge": ("roads", "bridge", "enabled"),
    "building.color_mode": ("buildings", "color_mode"),
    "building.default": ("buildings", "default"),
    # 兼容别名；通过此键写入时使用统一填充色。
    "building.color": ("buildings", "default"),
    "building.poi_tint.contrast": ("buildings", "poi_tint", "contrast"),
    "building.border": ("buildings", "border"),
    "building.shadow": ("buildings", "shadow", "enabled"),
    "built_area.color": ("built_area", "color"),
    "built_area.smooth": ("built_area", "smooth_blur"),
    "railway.color": ("railway", "line"),
    "railway.width": ("railway", "width"),
    "railway.sleepers": ("railway", "sleepers"),
    "traffic.free": ("traffic", "free"),
    "traffic.slow": ("traffic", "slow"),
    "traffic.busy": ("traffic", "busy"),
    "transport.width": ("transports", "width"),
    "transport.size": ("transports", "station", "size"),
    "transport.station_names": ("transports", "station", "names", "enabled"),
    "transport.merge": ("transports", "station", "merge_enabled"),
    "transport.distance": ("transports", "station", "merge_range"),
    "transport.mask": ("transports", "transit_view", "mask_intensity"),
    "transport.label.enabled": ("transports", "label", "enabled"),
    "transport.label.size": ("transports", "label", "size"),
    "transport.label.interval": ("transports", "label", "repeat_interval"),
    # 公共交通视图的线路标签字号。它与 ``transport.label.size``（常规视图的
    # 线路标签）是**两条独立规范**：公交视图的标签更密更大，几何比例也自成
    # 一套（见 styles/*.yaml 的 ``transports.transit_view.label_*``）。侧栏
    # 「线路名称」区本来就属于公共交通视图，所以写的是这一条。
    "transit_view.label_size": ("transports", "transit_view", "label_size"),
    "poi.size": ("poi_icons", "icon_size"),
    "poi.font": ("poi_icons", "font"),
    "poi.name_size": ("poi_icons", "name_size"),
    "poi.name_stroke": ("poi_icons", "name_stroke"),
    "poi.name_stroke_ratio": ("poi_icons", "name_stroke_ratio"),
    "poi.merge": ("poi_icons", "merge_enabled"),
    "poi.distance": ("poi_icons", "merge_distance"),
    # label.font 保留为旧版道路字体快捷项的兼容别名；GUIR 分别提供五个主题字体路径。
    "label.font": ("labels", "road", "font"),
    "label.font.road": ("labels", "road", "font"),
    "label.font.district": ("labels", "district", "font"),
    "label.font.building": ("labels", "building", "font"),
    "label.font.park": ("labels", "park", "font"),
    "label.font.stop": ("labels", "stop", "font"),
    # label.size / color / outline 是"道路那一份"的历史别名：整改前只有道路
    # 文字可调，老配置与老代码都还认这三个键，保留。新代码用 label.<属性>.<类别>。
    "label.size": ("labels", "road", "size"),
    "label.color": ("labels", "road", "color"),
    "label.outline": ("labels", "road", "stroke"),
    # 五类文字各自的 字号 / 颜色 / 描边（主题 labels 段本来就有这些键，导出侧
    # labeling/style_resolver.py 也按类各取一份，之前 GUI 只接了道路）。
    **{f"label.{attr}.{kind}": ("labels", kind, path)
       for kind in ("road", "district", "building", "park", "stop")
       for attr, path in (("size", "size"), ("color", "color"),
                          ("outline", "stroke"))},
    "district.color": ("district", "border"),
    "district.width": ("district", "border_width"),
    "district.opacity": ("district", "fill_alpha"),
    "zone.color": ("zone", "border"),
    "zone.opacity": ("zone", "fill_alpha"),
    "land_use.opacity": ("land_use", "alpha"),
    "land_use.grid": ("land_use", "enabled"),
    "procedural_objects.color": ("procedural_objects", "border"),
    "procedural_objects.border": ("procedural_objects", "border_width"),
}

# 公开别名：主题编辑器的递归字段渲染需要把 YAML 路径反查回字段键，
# 以便复用 ribbon_schema.FIELDS 的中文名与取值范围。语义与上面完全一致。
STYLE_FIELD_PATHS = _STYLE_FIELD_PATHS
_STYLE_FIELD_PATHS.update({f"label.{attr}.{kind}": ("labels", kind, path)
    for kind in ("road", "district", "building", "park", "stop")
    for attr, path in (("stroke_width", "stroke_width"), ("stroke_color", "stroke"))})


def _width_enabled(value) -> bool:
    """渲染宽度可以是标量或固定值加缩放值，不能是颜色。"""
    if isinstance(value, dict):
        return any(_width_enabled(value.get(key, 0)) for key in ("fixed", "scaled"))
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _outline_field_value(data, field_key):
    if field_key == "procedural_objects.border":
        return _width_enabled(data.get("procedural_objects", {}).get("border_width", 0))
    if field_key == "label.outline" or field_key.startswith("label.outline."):
        kind = field_key.rsplit(".", 1)[-1] if field_key.startswith("label.outline.") else "road"
        return _width_enabled(data.get("labels", {}).get(kind, {}).get("stroke_width", 0))
    return None


def _label_kind_outline_handler(kind: str):
    """生成"按文字类别开关描边"的字段处理器。

    ``_UI_FIELD_HANDLERS`` 的约定是 ``handler(self, value)``，而类体里定义的
    lambda **捕获不到**体外的名字（类体不是闭包作用域），所以工厂要放在模块级。
    """
    def handler(backend, value) -> None:
        backend._handler_label_kind_outline(kind, value)
    return handler


# 面板上的道路等级 → ``road_hierarchy`` 的语义等级。两边共用一套口径，
# 免得多出一张"另一份等级表"。
_ROAD_LEVEL_ENUM = {
    "highway": RoadLevel.HIGHWAY,
    "main": RoadLevel.MAIN,
    "branch": RoadLevel.BRANCH,
    "special": RoadLevel.SPECIAL,
    "beautification": RoadLevel.BEAUTIFICATION,
}

# 主题里"这一等级最细画到多少像素"的键名，与 ``road_layer._fill_width`` 一致。
_ROAD_LEVEL_WIDTH_KEY = "min_fill_px"


def road_level_style_key(roads: dict, level_key: str) -> str:
    """该等级在当前主题 ``roads`` 段里实际使用的键。

    主题之间叫法不同：amap / google / dark / vintage 用 ``main`` / ``branch``，
    apple_maps 用 ``primary`` / ``secondary`` / ``tertiary``。一律以主题里真实
    存在的那个键为准（顺序即 ``road_style_candidates`` 的别名表），这样写回去的
    值渲染侧一定读得到；主题没配这个等级时回退到规范名，此时渲染侧的候选表也会
    优先命中它。
    """
    candidates = road_style_candidates(_ROAD_LEVEL_ENUM[level_key], "ground")
    for key in candidates:
        cfg = roads.get(key)
        if isinstance(cfg, dict) and cfg:
            return key
    return candidates[0]


def road_level_read(roads: dict, level_key: str, attr: str, default=None):
    """读回某个道路等级的一个属性（读不到就返回 ``default``）。

    注意面板上的 ``width`` 在主题里叫 ``min_fill_px``（渲染侧读的是同一个键）。
    """
    cfg = roads.get(road_level_style_key(roads, level_key))
    if not isinstance(cfg, dict):
        return default
    key = _ROAD_LEVEL_WIDTH_KEY if attr == "width" else attr
    value = cfg.get(key, default)
    if attr == "width":
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    return value


def _road_level_handler(level_key: str, attr: str):
    """生成"道路等级 × 属性"的写入处理器。

    必须放模块级：``_UI_FIELD_HANDLERS`` 的约定是 ``handler(self, value)``，
    而类体里写的 lambda 捕获不到 self（类体不是闭包作用域）。
    """
    def handler(backend, value) -> None:
        roads = backend._style_data_mut().setdefault("roads", {})
        cfg = roads.setdefault(road_level_style_key(roads, level_key), {})
        if attr == "width":
            try:
                cfg[_ROAD_LEVEL_WIDTH_KEY] = max(0.2, min(6.0, float(value)))
            except (TypeError, ValueError):
                return
        else:
            cfg[attr] = str(value)
    return handler


# 水印位置：ribbon 值 → 主题 yaml 值
_WATERMARK_POS = {"br": "right_bottom", "bl": "left_bottom"}
_PERCENT_STYLE_FIELDS = {
    "building.poi_tint.contrast",
    "district.opacity", "zone.opacity",
    "land_use.opacity", "watermark.opacity",
}

# 这些字段的主题消费端（layers/*_layer.py 的 aggdraw opacity、
# preview_scene.py 的 /255）均为 0-255 alpha，而不是 0-1 小数。
_ALPHA255_STYLE_FIELDS = {"district.opacity", "zone.opacity", "land_use.opacity"}

# 样式编辑器场景 → 固定相关图层
_STYLE_SCENE_LAYERS = {
    "terrain": (TerrainLayer,),
    "water": (TerrainLayer, WaterLayer),
    "roads": (TerrainLayer, WaterLayer, RoadLayer),
    "railway": (TerrainLayer, WaterLayer, RoadLayer, RailwayLayer),
    "transports": (TerrainLayer, WaterLayer, RoadLayer,
                   RailwayLayer, TransportLayer),
    "buildings": (TerrainLayer, WaterLayer, RoadLayer, BuildingLayer),
    "built_area": (TerrainLayer, WaterLayer, RoadLayer,
                   BuiltAreaLayer, BuildingLayer),
    "zones": (TerrainLayer, WaterLayer, RoadLayer,
              DistrictLayer, ZoneLayer),
}


def _resolution_to_pixels(val: str) -> int:
    """从下拉选中值反解像素值（兼容纯数字/「4K · 4096」/历史 4k/8k）。"""
    val = (val or "").strip()
    if not val:
        return PREVIEW_SIZE
    if "(" in val and ")" in val:
        inner = val[val.find("(") + 1: val.find(")")].strip()
        try:
            return int(inner)
        except ValueError:
            pass
    if "·" in val:
        try:
            return max(512, min(32768, int(val.rsplit("·", 1)[1].strip())))
        except ValueError:
            pass
    s = val.lower()
    legacy_k = {"4k": 3840, "8k": 7680, "16k": 16384, "32k": 32768}
    if s in legacy_k:
        return legacy_k[s]
    if s.endswith("k"):
        try:
            return max(512, min(32768, int(float(s[:-1]) * 1024)))
        except ValueError:
            pass
    try:
        return max(512, min(32768, int(s)))
    except ValueError:
        return PREVIEW_SIZE


def _open_with_system_viewer(path) -> None:
    """用系统默认查看器打开文件（跨平台）。"""
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    elif sys.platform.startswith("linux"):
        subprocess.Popen(["xdg-open", str(path)])
    else:
        os.startfile(str(path))


def open_with_system_viewer(path) -> bool:
    """三平台统一的"用系统默认程序打开"，失败返回 False。

    界面侧（导出后自动打开图片 / 打开输出文件夹）只需要一个布尔结果，
    不需要自己再抄一遍 os.startfile / open / xdg-open 的分支。
    """
    target = os.path.expanduser(str(path))
    if not os.path.exists(target):
        return False
    try:
        _open_with_system_viewer(target)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def _copy_image_to_clipboard(img) -> tuple[bool, str]:
    """把 PIL 图像以 DIB 格式写入 Windows 剪贴板（纯 ctypes）。"""
    if sys.platform != "win32":
        return False, tr("gui.error.clipboard_windows_only")
    import struct
    import ctypes
    from ctypes import wintypes

    CLIP_MAX = 8192
    note = ""
    if max(img.size) > CLIP_MAX:
        img = img.copy()
        img.thumbnail((CLIP_MAX, CLIP_MAX), Image.LANCZOS)
        note = tr("gui.status.copied_scaled", w=img.size[0], h=img.size[1])
    w, h = img.size
    raw = img.convert("RGBA").tobytes("raw", "BGRA")
    stride = w * 4
    data = b"".join(
        raw[y * stride:(y + 1) * stride] for y in range(h - 1, -1, -1))
    header = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = header + data
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
    except (AttributeError, OSError, ImportError):
        return False, tr("gui.error.clipboard_api")
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HGLOBAL]
    user32.SetClipboardData.restype = wintypes.HGLOBAL
    user32.CloseClipboard.restype = wintypes.BOOL
    GMEM_MOVEABLE, CF_DIB = 0x0002, 8
    h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(buf))
    if not h_mem:
        return False, tr("gui.error.clipboard_alloc")
    ptr = kernel32.GlobalLock(h_mem)
    if not ptr:
        kernel32.GlobalFree(h_mem)
        return False, tr("gui.error.clipboard_lock")
    try:
        ctypes.memmove(ptr, buf, len(buf))
    finally:
        kernel32.GlobalUnlock(h_mem)
    try:
        if not user32.OpenClipboard(None):
            kernel32.GlobalFree(h_mem)
            return False, tr("gui.error.clipboard_busy")
        try:
            user32.EmptyClipboard()
            if not user32.SetClipboardData(CF_DIB, h_mem):
                kernel32.GlobalFree(h_mem)
                return False, tr("gui.error.clipboard_write")
        finally:
            user32.CloseClipboard()
    except Exception:
        try:
            kernel32.GlobalFree(h_mem)
        except Exception:
            pass
        return False, tr("gui.error.clipboard_error")
    return True, note


def _extract_city_info(document) -> dict:
    """从已解析文档直接提取城市信息（无需等 worker 回传）。"""
    name = (getattr(document, "city_name", "") or "").strip()
    metadata = getattr(document, "metadata", None)
    generated = (getattr(metadata, "exported_at", "") or ""
                 if metadata is not None else "")
    try:
        counters = dict(getattr(metadata, "counters", {}) or {})
    except (TypeError, ValueError):
        counters = {}
    buildings = len(getattr(document, "buildings", ()) or ())
    districts = len(getattr(document, "districts", ()) or ())
    transports = len(getattr(document, "transit_lines", ()) or ())
    forests = len(getattr(document, "forests", ()) or ())
    nodes = len(getattr(document, "nodes", ()) or ())
    networks = len(getattr(document, "networks", ()) or ())
    from .traffic import ensure_snapshot
    traffic = ensure_snapshot(getattr(document, "traffic", None))
    got_traffic = bool(traffic and getattr(traffic, "segments", None))
    return {
        "name": name or i18n.tr('ui.guir_backend._extract_city_info.ec7227d6cd'),
        "generated": generated or "—",
        "buildings": buildings or int(counters.get("buildings", 0) or 0),
        "districts": districts or int(counters.get("districts", 0) or 0),
        "parks": int(counters.get("parks", 0) or 0),
        "transports": transports or int(counters.get("transit_lines", 0) or 0),
        "forests": forests,
        "nodes": nodes or int(counters.get("nodes", 0) or 0),
        "segments": networks or int(counters.get("networks", 0) or 0),
        "traffic": len(traffic.segments) if got_traffic else -1,
    }


def _build_transit_line_catalog(session) -> list[dict]:
    """加载文档时在 Tk 线程之外推导界面线路信息。"""
    from .services.transit.geometry import resolve_route_geometry
    from .services.transit.native import cs2_document, source_transports
    from .transit import line_display_color, normalize_type

    mapdata = session._legacy_view()
    document = cs2_document(mapdata)
    lines = source_transports(document) if document is not None else (getattr(mapdata, "transports", ()) or ())
    geometry_cache = {}
    result = []
    for source_line in lines:
        type_key = normalize_type(getattr(source_line, "type", ""))
        if type_key not in TYPE_REGISTRY:
            continue
        geometry = resolve_route_geometry(
            mapdata, source_line, type_key, cache=geometry_cache)
        parts = geometry.parts or (geometry.points,)
        length_m = sum(
            math.hypot(float(right.x) - float(left.x),
                       float(right.z) - float(left.z))
            for points in parts
            for left, right in zip(points, points[1:])
        )
        rgb = line_display_color(source_line)
        color = ("#%02X%02X%02X" % tuple(rgb)) if rgb else "#8A8F99"
        stop_ids = {int(stop.node_id)
                    for stop in (getattr(source_line, "stops", ()) or ())}
        line_id = int(source_line.id)
        result.append({
            "id": line_id,
            "type": type_key,
            "name": (str(getattr(source_line, "name", "") or "").strip()
                     or f"{type_key} {line_id}"),
            "color": color,
            "stops": len(stop_ids),
            "length_m": float(length_m),
        })
    order = {key: index for index, key in enumerate(TYPE_REGISTRY)}
    result.sort(key=lambda item: (
        order.get(item["type"], len(order)),
        item["name"].casefold(), item["id"]))
    return result


def _int_line_ids(raw) -> list[int]:
    """设置里存下来的线路 id 列表 → int 列表（容忍字符串 / 浮点 / 脏值）。"""
    if not isinstance(raw, list):
        return []
    result: list[int] = []
    for item in raw:
        if isinstance(item, bool) or not isinstance(item, (int, float, str)):
            continue
        text = str(item).strip()
        if not text:
            continue
        try:
            result.append(int(float(text)))
        except (TypeError, ValueError):
            continue
    return result


def _unique_output_path(path: str | os.PathLike[str]) -> Path:
    """目标文件已存在时自动加序号（``名字-2.png``），不覆盖上一份产物。

    导出对话框的默认名里已经带了时间戳（见 ``guir.main_window`` 的命名 helper），
    这里只兜两种残留情况：同一秒里连导两次，以及用户手动把名字改回同名。

    ⚠️ 「另存为」那条路刻意**不用**它：文件名是用户在系统弹窗里敲的、覆盖也已
    确认过，再改一次名字只会让人困惑。它只服务导出对话框的产物。
    """
    target = Path(path)
    if not target.exists():
        return target
    index = 2
    while True:
        candidate = target.with_name(f"{target.stem}-{index}{target.suffix}")
        if not candidate.exists():
            return candidate
        index += 1


class GuirBackend:
    """GUI 无关的控制器：文档会话 / 渲染子进程 / 作业装配 / 输出 / 样式。"""

    def __init__(self, scheduler, scale: float = 1.0, settings_dir=None):
        self.scheduler = scheduler
        self.scale = scale

        # 设置与最近文件
        self._settings_dir_explicit = settings_dir is not None or sys.platform == "win32"
        self._settings_dir = (Path(settings_dir).resolve()
                              if settings_dir is not None else user_data_dir())
        self.poi_mapping_override = poi_mapping_override_path(self._settings_dir)
        self._settings = load_settings(self._settings_dir)
        # 交通和地铁视图设置单独持久化，切换视图不能覆盖普通地图图层快照。
        # 迁移使用上方已加载的设置，避免二次读取及过早生成 views.json。
        if views_path(self._settings_dir).is_file():
            self._view_settings = load_view_settings(self._settings_dir)
        else:
            self._view_settings = migrate_legacy_settings(
                self._settings, base=default_view_settings())
        self._recent_files = self._load_recent_files()
        self._settings_save_job = None
        self._output_dir = str(writable_dir())

        # 文档 / 会话
        self.document = None
        self.session = None
        self.source_file = None
        self.city_info = {}

        # 样式状态（主题 / 覆盖 / 字体 / 画质）
        self.theme_stem = "apple_maps"
        self.style_overrides: dict[str, dict] = {}
        self.font_scale = 1.0
        self.contrast = "standard"
        self.rotation = 0
        # 「范围」= n（全图的 1/n 见方）+ 定位方式。早先是一组下拉模式
        # （full / center5 / best3 / custom），那套语言以 CS1 的 17280 米为
        # 前提，且在英文界面下认不出自己存的值。
        self.range_n = RANGE_N_MIN
        self.range_anchor = "center"
        self.tile_ranges = {}
        self.size = 4096
        self.aa = False
        self.watermark_text = ""
        self.terrain = {"mode": "simple", "shade": 35.0, "sun": 315.0}
        self.road_names = True
        self.accurate_intersections = True
        self.road_player_names = True
        self.road_asset_names = False
        self.district_names = True
        self.poi_names = True
        self.poi_game_names = False
        self.road_game_names = False
        self.transport_names = True
        self.poi_player_names = True
        self.poi_important_names = True
        self.poi_density = "auto"
        self.poi_building = True
        self.poi_transit = True
        self.poi_hidden_categories: set[str] = set()
        self.merge_enabled = True
        self.merge_distance = 48.0
        self.merge_overrides: dict = {}
        self.same_facility_merge = True
        # same_facility_merge 保留为旧版界面和设置别名。
        self.facility_poi_overrides: dict[str, dict] = {}
        self.facility_hierarchy_enabled = True
        self.facility_child_min_zoom = 1.0
        self.building_merge_enabled = True
        self.transit_merge_enabled = True
        self.block_gap_m = 24.0
        self.hub_core_m = 80.0
        self.transit_types = list(TYPE_REGISTRY)
        self._transit_line_catalog_cache: list[dict] | None = None
        # 区域筛选仅属于当前城市会话，不持久化跨城市无意义的区划标识。
        self.district_ids: set[int] | None = None
        self.park_ids: set[int] | None = None

        # 图层显隐（settings_key → bool，与 gui_layer_registry 一致）
        self.layer_visibility = {
            key: default for key, _cls, default, _title in gui_layer_registry()
        }
        # GUIR 提供程序化对象渲染器，旧版界面仍不显示对应复选框。
        self.layer_visibility.setdefault("procedural_objects", False)

        # 常驻渲染子进程
        self._job_q = None
        self._result_q = None
        self._result_inbox = None
        self._result_reader_stop = None
        self._proc = None
        self._local_render_thread = None
        self._local_render_stop = threading.Event()
        self._local_png_active = False
        self._pending_preview = None
        self._poll_job = None
        self._serial = 0
        self._busy = False
        self._cancelled = False
        self._export_cancelled = threading.Event()
        self._callbacks: dict = {}
        self._last_progress_at = 0.0

        # 结果
        self.result_image = None
        self.result_thumbnail = None
        self.result_png_path = None
        self._png_tempdir = None
        self.last_rmp_snapshot = None
        self.last_diagnostics: dict = {}

        # 选区
        self._sel_game = None
        self._sel_scale = None

        # 样式预览（防抖 / 过期丢弃）
        self._preview_controller = PreviewRequestController(
            lambda delay, callback: self.scheduler.call_later(delay, callback),
            self.scheduler.cancel)
        self._style_editor_regions: dict = {}

        # 打开解析线程（防重复启动）
        self._open_thread = None

        # 中央 WebGL Preview Scene。revision 只增不减，编译线程提交前再次
        # 比对会话与 revision，避免旧地图或旧宽度结果覆盖新场景。
        self._preview_revision = 0
        self._preview_compile_thread = None
        self.preview_waits_for_presentation = False
        self._preview_base_presentation: tuple[int, threading.Event] | None = None
        self._preview_server = PreviewSceneServer(
            resource_dir("viewer") / "dist")

        # GUIR 与旧界面共用 settings.json，在创建控件前只加载一次。
        self._load_persisted_state()

    # ------------------------------------------------------------------
    # 通用
    # ------------------------------------------------------------------

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def has_document(self) -> bool:
        return self.document is not None

    def _emit(self, name: str, *args) -> None:
        """把回调调度到主线程执行（跨线程安全）。"""
        cb = self._callbacks or {}
        fn = cb.get(name)
        if fn is None:
            return
        if name == "progress" and args:
            now = time.time()
            pct = args[0]
            if now - self._last_progress_at < 0.05 and pct < 100:
                return
            self._last_progress_at = now

        self.scheduler.call_soon(lambda: fn(*args))

    def _post(self, fn) -> None:
        self.scheduler.call_soon(fn)

    def _record_recent(self, path: str) -> None:
        path = str(path)
        recents = [p for p in self._recent_files if p != path]
        recents.insert(0, path)
        self._recent_files = recents[:RECENT_MAX]
        vals = dict(self._settings.get("values", {}) or {})
        for key in [k for k in vals if str(k).startswith("recent_")]:
            vals.pop(key, None)
        for i, p in enumerate(self._recent_files):
            vals[f"recent_{i}"] = str(p)
        self._settings["values"] = vals
        save_settings(vals, self._settings_dir)
        self._emit("recent", list(self._recent_files))

    def remove_recent(self, path: str) -> bool:
        """移除一条最近文件路径并立即保存清理后的列表。"""
        path = str(path)
        filtered = [p for p in self._recent_files if p != path]
        if len(filtered) == len(self._recent_files):
            return False
        self._recent_files = filtered
        vals = dict(self._settings.get("values", {}) or {})
        for key in [k for k in vals if str(k).startswith("recent_")]:
            vals.pop(key, None)
        for i, recent_path in enumerate(self._recent_files):
            vals[f"recent_{i}"] = recent_path
        self._settings["values"] = vals
        save_settings(vals, self._settings_dir)
        self._emit("recent", list(self._recent_files))
        return True

    def _load_recent_files(self) -> list[str]:
        values = self._settings.get("values", {}) or {}
        recents: list[str] = []
        for i in range(RECENT_MAX):
            v = values.get(f"recent_{i}")
            if isinstance(v, str) and v.strip() and v.strip() not in recents:
                recents.append(v.strip())
        return recents

    def _setting(self, name: str, default):
        """仅当保存值的类型与默认值匹配时才读取。"""
        value = (self._settings.get("values", {}) or {}).get(name)
        if isinstance(default, bool):
            return value if isinstance(value, bool) else default
        if isinstance(default, (int, float)) and not isinstance(default, bool):
            return (value if isinstance(value, (int, float))
                    and not isinstance(value, bool) else default)
        if isinstance(default, list):
            return value if isinstance(value, list) else default
        if isinstance(default, dict):
            return value if isinstance(value, dict) else default
        return value if isinstance(value, str) and value else default

    def _load_persisted_state(self) -> None:
        """把旧界面设置应用到控制器的标准状态。"""
        style_value = str(self._setting("style_var", "apple_maps"))
        style_stem = style_value.split("·", 1)[0].strip()
        # 旧 stem（modern / paper）与旧显示名由 _theme_stem_for 统一解析。
        self.set_theme(style_stem, persist=False)
        self.set_size(self._setting("size_var", "4K · 4096"), persist=False)
        # 旧配置里存的是下拉显示值或老模式名，统一解析成 (n, anchor)。解析
        # 与当前语言无关 —— 用户可能在英文界面下存过值，切回中文后仍要认得出来。
        self.range_n, self.range_anchor = _range_from_legacy(
            self._setting("range_var", "full"))
        saved_n = self._setting("range_n", 0)
        if (isinstance(saved_n, int) and not isinstance(saved_n, bool)
                and saved_n > 0):
            self.range_n = max(RANGE_N_MIN, min(RANGE_N_MAX, saved_n))
        saved_anchor = str(self._setting("range_anchor", ""))
        if saved_anchor in RANGE_ANCHORS:
            self.range_anchor = saved_anchor
        self.tile_ranges = copy.deepcopy(self._setting("tile_ranges", {}))
        self.set_rotation(self._setting("rotation_var", "0°"), persist=False)
        self.aa = automatic_antialias()
        self.contrast = "standard"
        self.font_scale = 1.0
        self.poi_names = bool(self._setting("poi_names_var", False))
        self.poi_game_names = bool(self._setting("poi_game_names_var", False))
        # 旧开关同时控制道路和建筑的游戏名称。
        self.road_game_names = bool(self._setting("road_game_names_var", self.poi_game_names))
        self.transport_names = bool(self._setting("transport_names_var", True))
        self.poi_player_names = bool(self._setting("poi_player_names_var", True))
        self.poi_important_names = bool(self._setting("poi_important_names_var", True))
        self.poi_density = str(self._setting("guir_poi_density", "auto"))
        self.poi_building = bool(self._setting("poi_building_var", True))
        self.poi_transit = bool(self._setting("poi_transit_var", True))
        raw_hidden_categories = self._setting("poi_hidden_categories", [])
        self.poi_hidden_categories = {
            str(value).strip() for value in raw_hidden_categories
            if str(value).strip()
        } if isinstance(raw_hidden_categories, list) else set()
        self.road_names = bool(self._setting("road_names_var", False))
        self.accurate_intersections = bool(self._setting("accurate_intersections", True))
        self.road_player_names = bool(self._setting("road_player_names_var", True))
        # 道路标注只使用自定义名称；读取旧值仅供兼容，不重新启用资源名称。
        self.road_asset_names = False
        self.district_names = bool(self._setting("district_names_var", False))
        self.merge_enabled = bool(self._setting("merge_enabled_var", True))
        self.merge_distance = self._saved_float("merge_distance_var", 48.0)
        self.same_facility_merge = bool(self._setting(
            "same_facility_merge_var", True))
        self.facility_hierarchy_enabled = bool(self._setting(
            "facility_hierarchy_enabled_var", self.same_facility_merge))
        self.facility_child_min_zoom = self._saved_float(
            "facility_child_min_zoom_var", 1.0)
        # 旧版复选框继续作为可见的兼容控件。
        self.same_facility_merge = self.facility_hierarchy_enabled
        self.building_merge_enabled = bool(self._setting("building_merge_var", True))
        self.transit_merge_enabled = bool(self._setting("transit_merge_var", True))
        self.block_gap_m = self._saved_float("building_merge_dist_var", 24.0)
        self.hub_core_m = self._saved_float("transit_merge_dist_var", 80.0)
        raw_overrides = self._setting("poi_merge_overrides_var", {})
        self.merge_overrides = {}
        for category, raw in raw_overrides.items():
            if not isinstance(raw, dict) or not raw.get("enabled", True):
                continue
            try:
                distance = float(raw.get(
                    "distance", raw.get("merge_distance", self.merge_distance)))
            except (TypeError, ValueError):
                distance = self.merge_distance
            self.merge_overrides[str(category)] = {
                "enabled": bool(raw.get(
                    "merge_enabled", raw.get("enabled", self.merge_enabled))),
                "distance": distance,
            }
        raw_styles = self._setting("guir_style_overrides", {})
        self.style_overrides = copy.deepcopy(raw_styles)
        if isinstance(self.style_overrides, dict):
            from .theme_contract import cleanup_theme
            self.style_overrides = {
                key: cleanup_theme(value) if isinstance(value, dict) else value
                for key, value in self.style_overrides.items()
            }
        # 老设置里可能存着已下线的地形模式（gradient / facets），统一归一到现取值，
        # 否则下拉框会读出一个不存在的选项。
        self.terrain["mode"] = normalize_terrain_mode(
            self._setting("terrain_mode_var", "simple"))
        self.terrain["shade"] = self._saved_float("terrain_intensity_var", 35.)
        if 0 < self.terrain["shade"] <= 1:
            self.terrain["shade"] *= 100.
        self.terrain["sun"] = self._saved_float("terrain_azimuth_var", 315.0)
        for key, _cls, default, _title in gui_layer_registry():
            self.layer_visibility[key] = bool(self._setting("layer_" + key, default))
        self.layer_visibility["procedural_objects"] = bool(self._setting(
            "layer_procedural_objects", False))
        self.transit_types = [
            key for key in TYPE_REGISTRY
            if bool(self._setting("transit_normal_" + key, key in self.transit_types))
        ]

    def _reload_view_settings(self) -> None:
        if views_path(self._settings_dir).is_file():
            self._view_settings = load_view_settings(self._settings_dir)
        else:
            self._view_settings = migrate_legacy_settings(
                self._settings, base=default_view_settings())

    def _save_view_settings(self) -> None:
        save_view_settings(self._view_settings, self._settings_dir)

    def _view_section(self, metro: bool) -> dict:
        section = self._view_settings.get("metro" if metro else "transit", {})
        return section if isinstance(section, dict) else {}


    def _saved_float(self, name: str, default: float) -> float:
        try:
            return float(self._setting(name, str(default)))
        except (TypeError, ValueError):
            return float(default)

    def _collect_settings_values(self) -> dict:
        from .theme_contract import cleanup_theme
        values = dict(self._settings.get("values", {}) or {})
        # POI 来源可见性由 poi_visibility.icons 推导，不另设持久化图层开关。
        values.pop("layer_poi", None)
        for key in [k for k in values if str(k).startswith("recent_")]:
            values.pop(key, None)
        # 已停用的产品选项不得继续写入 settings.json。
        values.pop("district_specialization_var", None)
        values.pop("detail_var", None)
        values.pop("water_smooth_var", None)
        values.pop("built_smooth_var", None)
        # 旧「范围」键同理：显示值不再作为存储口径，改存 n 与定位方式。
        values.pop("range_var", None)
        values.pop("font_tier_var", None)
        values.pop("contrast_level_var", None)
        values.update({
            "style_var": self.theme_stem,
            "size_var": str(self.size),
            "range_n": int(self.range_n),
            "range_anchor": str(self.range_anchor),
            "tile_ranges": copy.deepcopy(self.tile_ranges),
            "rotation_var": f"{self.rotation}°",
            "poi_names_var": bool(self.poi_names),
            "poi_game_names_var": bool(self.poi_game_names),
            "road_game_names_var": bool(self.road_game_names),
            "transport_names_var": bool(self.transport_names),
            "poi_player_names_var": bool(self.poi_player_names),
            "poi_important_names_var": bool(self.poi_important_names),
            "guir_poi_density": self.poi_density,
            "poi_building_var": bool(self.poi_building),
            "poi_transit_var": bool(self.poi_transit),
            "poi_hidden_categories": sorted(self.poi_hidden_categories),
            "road_names_var": bool(self.road_names),
            "accurate_intersections": bool(self.accurate_intersections),
            "road_player_names_var": bool(self.road_player_names),
            "road_asset_names_var": bool(self.road_asset_names),
            "district_names_var": bool(self.district_names),
            "merge_enabled_var": bool(self.merge_enabled),
            "merge_distance_var": f"{self.merge_distance:g}",
            "same_facility_merge_var": bool(self.same_facility_merge),
            "facility_hierarchy_enabled_var": bool(
                self.facility_hierarchy_enabled),
            "facility_child_min_zoom_var": f"{self.facility_child_min_zoom:g}",
            "building_merge_var": bool(self.building_merge_enabled),
            "transit_merge_var": bool(self.transit_merge_enabled),
            "building_merge_dist_var": f"{self.block_gap_m:g}",
            "transit_merge_dist_var": f"{self.hub_core_m:g}",
            "poi_merge_overrides_var": {
                key: {"enabled": True,
                      "merge_enabled": bool(raw.get("enabled", True)),
                      "merge_distance": f"{float(raw.get('distance', self.merge_distance)):g}"}
                for key, raw in self.merge_overrides.items()
            },
            "guir_style_overrides": {
                key: cleanup_theme(value)
                for key, value in self.style_overrides.items()
                if isinstance(value, dict)
            },
            "terrain_mode_var": self.terrain["mode"],
            "terrain_intensity_var": str(self.terrain["shade"]),
            "terrain_azimuth_var": str(self.terrain["sun"]),
        })
        for key, enabled in self.layer_visibility.items():
            if key == "poi":
                continue
            values["layer_" + key] = bool(enabled)
        selected = set(self.transit_types)
        for key in TYPE_REGISTRY:
            values["transit_normal_" + key] = key in selected
        for i, path in enumerate(self._recent_files):
            values[f"recent_{i}"] = path
        values = normalize_poi_profiles(values)
        values = normalize_output_profiles(values)
        values["poi_visibility"] = self.poi_visibility_snapshot()
        return values

    def _schedule_settings_save(self) -> None:
        if self._settings_save_job is not None:
            self.scheduler.cancel(self._settings_save_job)
        self._settings_save_job = self.scheduler.call_later(
            800, self.save_settings_now)

    def save_settings_now(self) -> None:
        self._settings_save_job = None
        values = self._collect_settings_values()
        self._settings["values"] = values
        save_settings(values, self._settings_dir)

    def set_setting(self, name: str, value) -> None:
        """立即写入任意持久化设置项。settings.json 无键白名单，未知键会原样保留。"""
        values = dict(self._settings.get("values", {}) or {})
        values[str(name)] = value
        self._settings["values"] = values
        self.save_settings_now()

    def get_poi_profile(self, target: str) -> dict:
        values = dict(self._settings.get("values", {}) or {})
        normalized = normalize_poi_profiles(values)
        if values.get(PROFILE_SETTING_KEY) != normalized.get(PROFILE_SETTING_KEY):
            self._settings["values"] = normalized
            self._schedule_settings_save()
        return copy.deepcopy(normalized[PROFILE_SETTING_KEY][
            canonical_profile_target(target)])

    def set_poi_profile(self, target: str, profile) -> dict:
        values = normalize_poi_profiles(self._settings.get("values", {}) or {})
        canonical = canonical_profile_target(target)
        normalized = validate_poi_profile(profile, canonical)
        values[PROFILE_SETTING_KEY][canonical] = normalized
        self._settings["values"] = values
        self._schedule_settings_save()
        return copy.deepcopy(normalized)

    def get_output_profile(self, target: str) -> dict:
        if target not in {"normal_png", "transit_png"}:
            raise ValueError(f"unknown output profile target: {target!r}")
        values = dict(self._settings.get("values", {}) or {})
        normalized = normalize_output_profiles(values)
        if values.get(OUTPUT_PROFILE_SETTING_KEY) != normalized.get(
                OUTPUT_PROFILE_SETTING_KEY):
            values[OUTPUT_PROFILE_SETTING_KEY] = normalized[
                OUTPUT_PROFILE_SETTING_KEY]
            self._settings["values"] = values
            self._schedule_settings_save()
        return copy.deepcopy(normalized[OUTPUT_PROFILE_SETTING_KEY][target])

    def set_output_profile(self, target: str, profile) -> dict:
        values = normalize_output_profiles(self._settings.get("values", {}) or {})
        current = values[OUTPUT_PROFILE_SETTING_KEY][target]
        if not isinstance(profile, dict):
            raise ValueError("output profile must be an object")
        updated = validate_output_profile({**current, **profile}, target)
        values[OUTPUT_PROFILE_SETTING_KEY][target] = updated
        self._settings["values"] = values
        self.set_rotation(updated["rotation"])
        return copy.deepcopy(updated)

    def poi_visibility_snapshot(self) -> dict:
        values = dict(self._settings.get("values", {}) or {})
        raw = values.get("poi_visibility")
        normalized = normalize_poi_visibility_snapshot({} if raw is None else raw)
        if values.get("poi_visibility") != normalized:
            values["poi_visibility"] = copy.deepcopy(normalized)
            self._settings["values"] = values
            self._schedule_settings_save()
        self.layer_visibility["poi"] = any(normalized["icons"].values())
        return copy.deepcopy(normalized)

    def set_poi_visibility_snapshot(self, raw) -> dict:
        normalized = normalize_poi_visibility_snapshot(raw)
        values = dict(self._settings.get("values", {}) or {})
        values["poi_visibility"] = copy.deepcopy(normalized)
        self._settings["values"] = values
        self.layer_visibility["poi"] = any(normalized["icons"].values())
        self._schedule_settings_save()
        return copy.deepcopy(normalized)

    def _attach_poi_render_config(self, job: dict, target: str,
                                  profile=None,
                                  visibility_snapshot=None) -> dict:
        ui = job.setdefault("ui", {})
        normalized = (self.get_poi_profile(target) if profile is None
                      else validate_poi_profile(profile, target))
        visibility = (self.poi_visibility_snapshot()
                      if visibility_snapshot is None
                      else normalize_poi_visibility_snapshot(visibility_snapshot))
        view = canonical_profile_target(target)
        render_target = "preview" if target == "preview" else {
            "normal": "normal_png", "transit": "transit_png", "metro": "metro"}[view]
        ui["poi_profile"] = {**copy.deepcopy(normalized), "target": render_target, "view": view}
        ui["poi_visibility_snapshot"] = copy.deepcopy(visibility)
        return job

    @property
    def output_dir(self) -> str:
        return str(self._setting("output_dir", self._output_dir))

    @output_dir.setter
    def output_dir(self, path: str) -> None:
        self._output_dir = str(path)
        self.set_setting("output_dir", self._output_dir)

    def set_language(self, language: str) -> bool:
        """在更改当前设置前保存应用偏好。"""
        from .i18n import AVAILABLE_LANGS

        if language not in AVAILABLE_LANGS:
            return False
        values = dict(self._settings.get("values", {}) or {})
        values["language"] = language
        directory = application_settings_dir()
        preferences = dict(load_settings(directory)["values"])
        preferences["language"] = language
        if not save_settings(preferences, directory):
            return False
        self._settings["values"] = values
        return True

    def set_layer_visibility(self, key: str, enabled: bool) -> None:
        if key in self.layer_visibility:
            self.layer_visibility[key] = bool(enabled)
            self._schedule_settings_save()

    def map_layer_visibility(self) -> dict:
        visibility = dict(self.layer_visibility)
        # 总开关隐藏 POI 时不覆盖类别选择。
        visibility["poi"] = bool(visibility["label"]) and any(
            self.poi_visibility_snapshot()["icons"].values())
        return visibility

    def set_poi_category_enabled(self, category: str, enabled: bool) -> bool:
        """设置 POI 图层拥有的两类内容筛选之一。"""
        attr = {"building": "poi_building", "transit": "poi_transit"}.get(
            str(category))
        if attr is None:
            return False
        setattr(self, attr, bool(enabled))
        self._schedule_settings_save()
        return True

    def poi_type_visible(self, category: str) -> bool:
        return str(category).strip() not in self.poi_hidden_categories

    def set_poi_type_visible(self, category: str, enabled: bool) -> bool:
        """切换所有匹配对象的 POI 类别映射。"""
        category = str(category).strip()
        if not category:
            return False
        if enabled:
            self.poi_hidden_categories.discard(category)
        else:
            self.poi_hidden_categories.add(category)
        self._schedule_settings_save()
        return True

    def set_label_filter(self, key: str, enabled: bool) -> bool:
        """更新标注细节筛选而不改变图层可见性。"""
        attr = {
            "poi_player": "poi_player_names",
            "poi_important": "poi_important_names",
            "road_player": "road_player_names",
            "road_asset": "road_asset_names",
        }.get(str(key))
        if attr is None:
            return False
        setattr(self, attr, bool(enabled))
        self._schedule_settings_save()
        return True

    def set_transit_type_enabled(self, type_key: str, enabled: bool) -> bool:
        """以 TYPE_REGISTRY 为依据更新普通地图的交通子类型。"""
        if type_key not in TYPE_REGISTRY:
            return False
        selected = set(self.transit_types)
        (selected.add if enabled else selected.discard)(type_key)
        self.transit_types = [key for key in TYPE_REGISTRY if key in selected]
        preview = copy.deepcopy(getattr(
            self, "_preview_transit_options", {}) or {})
        preview["types"] = list(self.transit_types)
        self._preview_transit_options = preview
        self._schedule_settings_save()
        return True

    def transit_line_catalog(self) -> list[dict]:
        """按标准交通类型分组返回可供界面显示的线路信息，长度沿用渲染器的三级几何解析。"""
        if self.document is None or self.session is None:
            return []
        if self._transit_line_catalog_cache is not None:
            return [dict(item) for item in self._transit_line_catalog_cache]

        result = _build_transit_line_catalog(self.session)
        self._transit_line_catalog_cache = result
        return [dict(item) for item in result]

    @staticmethod
    def _district_name(item, fallback: str) -> str:
        name = getattr(item, "name", None)
        text = getattr(name, "text", name)
        return (str(text or "").strip() or fallback)

    @staticmethod
    def _region_renderable(item) -> bool:
        if getattr(item, "grid_points", None):
            return True
        boundary = getattr(item, "boundary", None)
        points = getattr(boundary, "points", boundary or ())
        return len(points or ()) >= 3

    def facility_poi_catalog(self) -> list[dict]:
        if self.document is None:
            return []
        from .services.poi import build_facility_groups
        from .cmm import SemanticLevel
        groups = build_facility_groups(self.document).groups
        return sorted([
            {"id": group.id, "name": group.name.text or group.kind, "kind": group.kind,
             "kindLabel": {"airport": i18n.tr('ui.guir_backend.facility_poi_catalog.6a5347ac5f'), "campus": i18n.tr('ui.guir_backend.facility_poi_catalog.cf3e1046a3'), "university": i18n.tr('ui.guir_backend.facility_poi_catalog.4513bba543'),
                           "park": i18n.tr('ui.guir_backend.facility_poi_catalog.e852dca995'), "zoo": i18n.tr('ui.guir_backend.facility_poi_catalog.0fd9e19000'), "amusement": i18n.tr('ui.guir_backend.facility_poi_catalog.82be57d9af'),
                           "nature_reserve": i18n.tr('ui.guir_backend.facility_poi_catalog.b2c3ea6ae5'), "ship": i18n.tr('ui.guir_backend.facility_poi_catalog.a0cc394994'),
                           "cargo_harbor": i18n.tr('ui.guir_backend.facility_poi_catalog.163ee5a292')}.get(group.kind, group.kind),
             "enabled": self.facility_poi_overrides.get(group.id, {}).get("enabled", True)}
            for group in groups if group.semantic_level is not SemanticLevel.COMPOSITE_BUILDING
            and (group.semantic_level is SemanticLevel.FACILITY or group.source_zone_ids)
            and not group.parent_group_id
        ], key=lambda item: (item["name"], item["id"]))

    def region_catalog(self) -> dict:
        """返回当前城市区域弹出面板所需的元数据。"""
        document = self.document
        if document is None:
            return {"districts": [], "parks": [],
                    "district_available": False, "park_available": False}
        districts = list(visible_administrative_districts(document))
        parks = [item for item in
                 list(getattr(document, "park_zones", ()) or ())
                 if str(getattr(item, "type_label", "") or "").strip().lower()
                 not in ("", "none", "0")]

        def entry(item, prefix: str) -> dict:
            item_id = int(getattr(item, "id", 0))
            type_label = str(getattr(item, "type_label", "") or "").strip()
            return {
                "id": item_id,
                "name": self._district_name(item, f"{prefix} {item_id}"),
                "type": type_label,
                "available": self._region_renderable(item),
            }

        district_entries = [entry(item, i18n.tr('ui.guir_backend.region_catalog.4f56683e73')) for item in districts]
        park_entries = [entry(item, i18n.tr('ui.guir_backend.region_catalog.06f15683dd')) for item in parks]
        district_entries.sort(key=lambda item: (item["name"].casefold(), item["id"]))
        park_entries.sort(key=lambda item: (item["name"].casefold(), item["id"]))
        return {
            "districts": district_entries,
            "parks": park_entries,
            "district_available": any(item["available"] for item in district_entries),
            "park_available": any(item["available"] for item in park_entries),
        }

    def reset_region_selection(self) -> None:
        """为新城市会话选中当前文档的全部区域。"""
        self.facility_poi_overrides = {}
        catalog = self.region_catalog()
        self.district_ids = {item["id"] for item in catalog["districts"]}
        self.park_ids = {item["id"] for item in catalog["parks"]}

    def set_region_selection(self, group: str, ids) -> bool:
        """设置当前会话的行政区或功能区筛选。"""
        attr = {"districts": "district_ids", "parks": "park_ids"}.get(str(group))
        if attr is None:
            return False
        valid = {item["id"] for item in self.region_catalog()[group]}
        setattr(self, attr, {int(item_id) for item_id in ids if int(item_id) in valid})
        return True

    # ------------------------------------------------------------------
    # 设置 / 状态
    # ------------------------------------------------------------------

    def set_theme(self, value: str, persist: bool = True) -> bool:
        stem = _theme_stem_for((value or "").strip())
        if stem is None:
            return False
        self.theme_stem = stem
        if persist:
            self._schedule_settings_save()
        return True

    def theme_display(self) -> str:
        """当前主题的显示名（按当前语言翻译过）；持久化一律存 ``theme_stem``。"""
        from .i18n import tr

        for name_key, stem in THEME_CHOICES:
            if stem == self.theme_stem:
                return tr(name_key)
        return self.theme_stem

    def set_size(self, value, persist: bool = True) -> None:
        pixels = _resolution_to_pixels(str(value))
        if pixels:
            self.size = int(pixels)
            if persist:
                self._schedule_settings_save()

    def set_rotation(self, value, persist: bool = True) -> None:
        try:
            from .export_region import output_rotation
            rotation = output_rotation(int(str(value).strip().rstrip("°")))
        except (TypeError, ValueError) as exc:
            if persist:
                raise ValueError("rotation must be 0/90/180/270") from exc
            return
        self.rotation = rotation
        self._settings.setdefault("values", {})["rotation_var"] = f"{rotation}°"
        self._settings["values"] = normalize_output_profiles(self._settings["values"])
        if persist:
            self._schedule_settings_save()

    def set_range_n(self, value, persist: bool = True) -> None:
        """兼容旧配置的内部范围除数；界面按文档幅面换算为实际地理尺寸。"""
        try:
            n = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(tr("gui.error.range_n_number")) from exc
        if not RANGE_N_MIN <= n <= RANGE_N_MAX:
            raise ValueError(tr("gui.error.range_n_range",
                                 min=RANGE_N_MIN, max=RANGE_N_MAX))
        self.range_n = n
        if persist:
            self._schedule_settings_save()

    def set_range_anchor(self, value, persist: bool = True) -> None:
        """范围定位方式：``center`` 居中 / ``busiest`` 取建筑最密集的那一块。"""
        anchor = str(value or "").strip().casefold()
        if anchor not in RANGE_ANCHORS:
            raise ValueError(f"unknown range anchor: {value!r}")
        self.range_anchor = anchor
        if persist:
            self._schedule_settings_save()

    def range_tile_grid(self):
        return document_tile_grid(self.document)

    def has_outside_terrain(self) -> bool:
        grid = self.range_tile_grid()
        if grid is None or grid.game != "cs2":
            return False
        x0, z0, x1, z1 = self.world_rect()
        return min(x1 - x0, z1 - z0) > grid.tile_m * grid.grid * 1.01

    def tile_range_selection(self) -> dict | None:
        """返回 None 可在用户确认新对话框前保持旧版除数范围不变。"""
        grid = self.range_tile_grid()
        value = self.tile_ranges.get(grid.game) if grid else None
        if not isinstance(value, dict):
            return None
        n, anchor = value.get("tiles"), value.get("anchor", "center")
        if (type(n) is not int or not 0 <= n <= 256
                or anchor not in (*RANGE_ANCHORS, "manual")):
            return None
        # 裁剪后的存档可能缺少完整地块；无法容纳一块时改用全范围，不缩小地块单位。
        result = {"tiles": min(n, grid.limit(self.world_rect())), "anchor": anchor}
        if anchor == "manual":
            try:
                bounds = normalize_bounds(value.get("bounds", ()))
            except (TypeError, ValueError):
                return None
            if any(v < 0 or v > 1 for v in bounds):
                return None
            result["bounds"] = bounds
        return result

    def set_tile_range(self, tiles: int, anchor: str, persist: bool = True,
                       *, bounds=None) -> None:
        grid = self.range_tile_grid()
        if (grid is None or type(tiles) is not int
                or not 0 <= tiles <= grid.limit(self.world_rect())):
            raise ValueError("tile range exceeds this document's available extent")
        if anchor not in (*RANGE_ANCHORS, "manual"):
            raise ValueError(f"unknown range anchor: {anchor!r}")
        value = {"tiles": tiles, "anchor": anchor}
        if anchor == "manual":
            x0, z0, x1, z1 = self.world_rect()
            a, b, c, d = normalize_bounds(bounds, (x0, z0, x1, z1))
            value["bounds"] = ((a-x0)/(x1-x0), (b-z0)/(z1-z0),
                               (c-x0)/(x1-x0), (d-z0)/(z1-z0))
        self.tile_ranges[grid.game] = value
        if persist:
            self._schedule_settings_save()

    def set_aa(self, enabled: bool) -> None:
        self.aa = bool(enabled)
        self._schedule_settings_save()

    def set_font_tier(self, value) -> None:
        """已停用的兼容方法；全局字体缩放不再影响渲染。"""
        self.font_scale = 1.0

    def set_contrast(self, value) -> None:
        """已停用的兼容方法；色调对比度由主题管理。"""
        self.contrast = "standard"

    def set_merge_options(self, *, building=None, transit=None,
                          building_distance=None, transit_distance=None) -> None:
        if building is not None:
            self.building_merge_enabled = bool(building)
        if transit is not None:
            self.transit_merge_enabled = bool(transit)
        if building_distance is not None:
            try:
                self.block_gap_m = float(building_distance)
            except (TypeError, ValueError):
                pass
        if transit_distance is not None:
            try:
                self.hub_core_m = float(transit_distance)
            except (TypeError, ValueError):
                pass
        self._schedule_settings_save()

    def set_merge_override(self, category: str, *, enabled=None,
                           distance=None) -> None:
        current = self.merge_overrides.setdefault(str(category), {
            "enabled": self.merge_enabled, "distance": self.merge_distance})
        if enabled is not None:
            current["enabled"] = bool(enabled)
        if distance is not None:
            try:
                current["distance"] = float(distance)
            except (TypeError, ValueError):
                pass
        self._schedule_settings_save()

    # ------------------------------------------------------------------
    # 文档 / 打开
    # ------------------------------------------------------------------

    def open_file(self, path, callbacks: dict | None = None) -> bool:
        """打开存档：后台解析 → DocumentSession → 编译道路 Preview Scene。

        callbacks 还支持 scene_ready(bundle, manifest_url) / scene_error(exc)。
        """
        path = str(path)
        if not Path(path).exists():
            return False
        if self._local_png_active:
            self._local_render_stop.set()
        if self._open_thread is not None and self._open_thread.is_alive():
            return False
        if callbacks is not None:
            self._callbacks = callbacks
        self.invalidate_preview_compile()
        if not self._settings_dir_explicit and self.source_file is None:
            # 单独运行时，地图选定后将用户数据写在地图旁，而非渲染器版本目录。
            self._settings_dir = Path(path).resolve().parent
            self.poi_mapping_override = poi_mapping_override_path(self._settings_dir)
            self._reload_view_settings()

        def p_parse(pct: float, msg: str) -> None:
            self._emit("progress", pct * 60, tr("gui.status.parsing", msg=msg))

        def worker() -> None:
            try:
                document = load_document(
                    path, progress=p_parse, validate=False)
                session = DocumentSession(document=document)
                transit_catalog = _build_transit_line_catalog(session)
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()

                def on_error(error=exc) -> None:
                    self._emit("error", error)
                self._post(on_error)
                return

            def on_parsed() -> None:
                self._switch_settings_dir_for_city(path)
                self.source_file = path
                self._output_dir = str(Path(path).resolve().parent)
                self.document = document
                self.session = session
                self._transit_line_catalog_cache = transit_catalog
                # 公共交通视图的线路勾选不落盘（会话级），地铁白名单则要跟
                # 当前存档对齐；settings.json 与存档同目录共享，上一座城市
                # 留下的 id 会让线路被整批过滤掉（PM 2026-09-11）。
                if self._reconcile_transit_line_selection(transit_catalog):
                    self._schedule_settings_save()
                    self._save_view_settings()
                self.result_image = None
                self.result_thumbnail = None
                self.result_png_path = None
                self._sel_game = None
                self._sel_scale = None
                self._style_editor_regions = {}
                self.city_info = _extract_city_info(document)
                self.reset_region_selection()
                self._record_recent(path)
                self._emit("meta", dict(self.city_info), "open")
                # 解析与 WebGL 场景编译是两个独立作业，第二个作业开始前重置共享进度条。
                # 状态栏也是给用户看的：这里不写 WebGL 之类的内部叫法。
                self._emit("progress", 0, tr("gui.progress.preview"))
                self.compile_preview_scene(
                    callbacks, )
            self._post(on_parsed)

        self._open_thread = threading.Thread(
            target=worker, name="guir-open", daemon=True)
        self._open_thread.start()
        return True

    def _switch_settings_dir_for_city(self, path: str) -> None:
        """打开新城市时重新加载旁边的设置，避免跨城市串用。"""
        if self._settings_dir_explicit:
            return
        target = Path(path).resolve().parent
        if target == self._settings_dir:
            return
        if self._settings_save_job is not None:
            self.scheduler.cancel(self._settings_save_job)
            self.save_settings_now()
        self._settings_dir = target
        self.poi_mapping_override = poi_mapping_override_path(target)
        self._settings = load_settings(target)
        self._recent_files = self._load_recent_files()
        self._output_dir = str(target)
        self._load_persisted_state()
        self._reload_view_settings()
        self._transit_line_catalog_cache = None
        self._transit_session_options = None

    def _enabled_layers(self) -> list[str]:
        """按注册顺序返回勾选图层的 settings_key 列表。"""
        return [item.settings_key for item in layer_directory()
                if (item.settings_key == "poi"
                    or self.layer_visibility.get(item.settings_key, False))]

    # ------------------------------------------------------------------
    # 渲染子进程（移植 gui.py 三段式）
    # ------------------------------------------------------------------

    def _ensure_worker(self) -> bool:
        if self._proc is not None and self._proc.is_alive():
            return True
        if self._proc is not None:
            self._stop_worker()
        try:
            import multiprocessing
            ctx = multiprocessing.get_context("spawn")
            # 语言是进程级全局，spawn 出来的子进程不会自己重新判定 —— 不把当前
            # 语言带过去，它就会退回模块默认的中文，英文界面下渲染进度全是中文。
            # 走环境变量而不是 settings.json：GUIR 的设置是按存档目录存的，
            # 子进程不知道那个目录，detect_lang() 会读到另一份（或读不到）。
            from .i18n import LANG
            os.environ["CSLMM_LANG"] = str(LANG)
            self._job_q = ctx.Queue()
            self._result_q = ctx.Queue()
            self._proc = ctx.Process(
                target=_render_worker.worker_loop,
                args=(self._job_q, self._result_q),
                # 瓦片进程池需要创建子进程，因此导出 worker 不能是 daemon。
                # 退出时由 close()/atexit 显式清理 worker 和瓦片进程树。
                daemon=False,
            )
            self._proc.start()
            self._result_inbox = queue.Queue()
            self._result_reader_stop = threading.Event()
            threading.Thread(
                target=self._read_worker_results,
                args=(self._result_q, self._result_inbox, self._result_reader_stop),
                name="guir-worker-results", daemon=True,
            ).start()
            atexit.register(self._stop_worker)
            return True
        except Exception:
            traceback.print_exc(file=sys.stderr)
            self._proc = None
            return False

    @staticmethod
    def _read_worker_results(result_q, inbox, stop) -> None:
        # 多进程队列若在发送者写入中途退出，get_nowait 可能阻塞；在引擎请求线程外等待。
        while not stop.is_set():
            try:
                inbox.put(result_q.get(timeout=0.1))
            except queue.Empty:
                continue
            except (EOFError, OSError, ValueError) as exc:
                if not stop.is_set():
                    inbox.put(("reader_error", str(exc)))
                return

    def _stop_worker(self) -> None:
        atexit.unregister(self._stop_worker)
        if self._poll_job is not None:
            self.scheduler.cancel(self._poll_job)
            self._poll_job = None
        proc = self._proc
        self._proc = None
        if self._result_reader_stop is not None:
            self._result_reader_stop.set()
        if proc is not None and proc.is_alive():
            try:
                if self._job_q is not None:
                    self._job_q.put(None)
            except Exception:
                pass
            proc.join(timeout=2)
            if proc.is_alive():
                if sys.platform == "win32":
                    # terminate() 只结束父 worker；先清理整个树，避免取消导出
                    # 后 ProcessPoolExecutor 的瓦片进程继续占用 CPU/内存。
                    try:
                        subprocess.run(
                            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NO_WINDOW,
                            timeout=5, check=False,
                        )
                    except (OSError, subprocess.SubprocessError):
                        pass
                # terminate() 只是递信号，不代表已经退出。不等它落定就返回，
                # 残留进程会带着渲染中的子进程活到主程序退出之后。
                proc.terminate()
                proc.join(timeout=2)
                if proc.is_alive():
                    proc.kill()
                    proc.join(timeout=1)
        # Queue 自带 feeder 线程与管道；不显式关闭，它们会被 GC 留到进程末尾。
        for queue_obj in (self._job_q, self._result_q):
            if queue_obj is None:
                continue
            try:
                queue_obj.cancel_join_thread()
                queue_obj.close()
            except Exception:
                pass
        self._job_q = None
        self._result_q = None
        self._result_inbox = None
        self._result_reader_stop = None
        if not self._local_png_active:
            self.discard_png_result()

    def discard_png_result(self) -> None:
        self.result_png_path = None
        directory = self._png_tempdir
        self._png_tempdir = None
        if directory is not None:
            try:
                shutil.rmtree(directory)
            except OSError:
                logging.getLogger(__name__).warning(i18n.tr('ui.guir_backend.discard_png_result.850166ad36'), directory, exc_info=True)

    def submit(self, job: dict, callbacks: dict | None = None) -> bool:
        """提交渲染任务；单任务互斥——渲染中提交返回 False。"""
        if self._busy:
            return False
        self._busy = True
        self._cancelled = False
        self._worker_exit_at = None
        if callbacks is not None:
            self._callbacks = callbacks
        self._serial += 1
        job["serial"] = self._serial
        if not self._ensure_worker():
            self._busy = False
            self._emit("error", RuntimeError(tr("gui.error.worker_start_failed")))
            return False
        try:
            if "png_output_dir" in job:
                self.discard_png_result()
                parent = Path(job["png_output_dir"])
                parent.mkdir(parents=True, exist_ok=True)
                self._png_tempdir = Path(tempfile.mkdtemp(prefix=".csl-png-", dir=parent))
                job["png_path"] = str(self._png_tempdir / "result.png")
            self._job_q.put(job)
        except Exception as exc:  # noqa: BLE001
            self.discard_png_result()
            self._busy = False
            self._emit("error", RuntimeError(tr("gui.error.task_submit_failed", error=exc)))
            return False
        self._emit("busy", True)
        if self._poll_job is None:
            self._poll_job = self.scheduler.call_later(50, self._poll)
        return True

    def _poll(self) -> None:
        self._poll_job = None
        q = self._result_inbox
        if q is None:
            return
        serial = self._serial
        settled = False
        latest_preview = None
        try:
            while True:
                try:
                    msg = q.get_nowait()
                except queue.Empty:
                    break
                except Exception as exc:
                    raise RuntimeError(i18n.tr('ui.guir_backend._poll.28e5fa4dbd')) from exc
                # 过期结果丢弃（serial 不匹配）
                if serial != self._serial:
                    continue
                kind = msg[0]
                if kind == "preview":
                    latest_preview = msg[1:]
                elif kind == "progress_event":
                    event = msg[1]
                    self._emit("progress", event.overall_fraction * 100,
                               event.message)
                elif kind == "progress":
                    self._emit("progress", msg[1], msg[2])
                elif kind == "done":
                    if msg[1].get("serial", serial) != serial:
                        continue
                    self._on_worker_done(msg[1])
                    settled = True
                    break
                elif kind == "error":
                    self._on_worker_error(msg[1])
                    settled = True
                    break
                elif kind == "reader_error":
                    self._on_worker_error(i18n.tr('ui.guir_backend._poll.effd01f5d1', p0=msg[1]))
                    settled = True
                    break
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._on_worker_error(str(exc))
            settled = True
        if not settled:
            if latest_preview is not None:
                self._emit("preview", *latest_preview)
            proc = self._proc
            if proc is not None and not proc.is_alive():
                if self._worker_exit_at is None:
                    self._worker_exit_at = time.monotonic()
                elif time.monotonic() - self._worker_exit_at >= 1.0:
                    print(f"Render worker exited: pid={proc.pid}, code={proc.exitcode}", file=sys.stderr)
                    self._on_worker_error(i18n.tr('ui.guir_backend._poll.528c98ff25', p0=proc.exitcode))
                    return
            else:
                self._worker_exit_at = None
            self._poll_job = self.scheduler.call_later(50, self._poll)

    def _on_worker_done(self, payload: dict) -> None:
        image = payload.get("image")
        self.result_png_path = payload.get("png_path")
        image_width = payload["image_size"][0] if self.result_png_path else image.width
        stats = payload.get("stats") or {}
        context = payload.get("context") or {}
        is_region = bool(payload.get("is_region"))
        self.result_image = image
        self.result_thumbnail = payload.get("thumbnail")
        self.last_rmp_snapshot = payload.get("rmp_snapshot")
        self.last_diagnostics = {
            "elapsed": payload.get("elapsed", 0.0),
            "size": payload.get("size", 0),
            "is_region": is_region,
            "stats": stats,
            "city": context.get("city_info") or {},
        }
        if is_region and self._sel_game:
            span = max(self._sel_game[2] - self._sel_game[0],
                       self._sel_game[3] - self._sel_game[1]) or 1
            self._sel_scale = image_width / span
        else:
            self._sel_scale = image_width / WORLD_HALF / 2.0  # 17280
        city_info = context.get("city_info") or {}
        if city_info:
            self.city_info = city_info
            name = (city_info.get("name") or "").strip()
            if name and self.source_file:
                self._record_recent(self.source_file)
            self._emit("meta", city_info, "render")
        self._busy = False
        self._cancelled = False
        self._emit("done", image, payload.get("size", 0), stats,
                   payload.get("elapsed", 0.0), is_region,
                   self.result_thumbnail, context)
        self._emit("busy", False)

    def _on_worker_error(self, text: str) -> None:
        self.discard_png_result()
        self._busy = False
        self._cancelled = False
        self._emit("error", RuntimeError(text))
        self._emit("busy", False)

    def cancel(self) -> None:
        """取消当前任务并停止 worker，防止旧结果冒充新任务。"""
        if self._local_png_active:
            self._local_render_stop.set()
            return
        self._cancelled = True
        self.invalidate_preview_compile()
        self._local_render_stop.set()
        self._serial += 1
        self._busy = False
        self._emit("busy", False)
        self._stop_worker()

    def cancel_export(self) -> None:
        """在下一个进度边界协作停止 SVG 或 HTML 导出。"""
        self._export_cancelled.set()

    # ------------------------------------------------------------------
    # 作业装配
    # ------------------------------------------------------------------

    def _capture_ui_state(self) -> dict:
        return {
            "accurate_intersections": bool(self.accurate_intersections),
            "style_overrides": copy.deepcopy(self.style_overrides),
            "rotation": self.rotation,
            "terrain": dict(self.terrain),
            "poi_names": bool(self.poi_names),
            "poi_game_names": bool(self.poi_game_names),
            "road_game_names": bool(self.road_game_names),
            "transport_names": bool(self.transport_names),
            "poi_player_names": bool(self.poi_player_names),
            "poi_important_names": bool(self.poi_important_names),
            "poi_building": bool(self.poi_building),
            "poi_transit": bool(self.poi_transit),
            "poi_hidden_categories": sorted(self.poi_hidden_categories),
            "poi_density": self.poi_density,
            "text_enabled": bool(self.layer_visibility.get("label", False)),
            "merge_enabled": bool(self.merge_enabled),
            "merge_distance": float(self.merge_distance),
            "merge_overrides": copy.deepcopy(self.merge_overrides),
            "same_facility_merge": bool(self.same_facility_merge),
            "facility_poi_overrides": dict(self.facility_poi_overrides),
            "facility_hierarchy_enabled": bool(
                self.facility_hierarchy_enabled),
            "facility_child_min_zoom": float(self.facility_child_min_zoom),
            "building_merge_enabled": bool(self.building_merge_enabled),
            "transit_merge_enabled": bool(self.transit_merge_enabled),
            "block_gap_m": float(self.block_gap_m),
            "hub_core_m": float(self.hub_core_m),
            "transit_types": list(self.transit_types),
            "district_ids": (None if self.district_ids is None
                             else sorted(self.district_ids)),
            "park_ids": (None if self.park_ids is None
                         else sorted(self.park_ids)),
            "road_names": bool(self.road_names),
            "road_player_names": bool(self.road_player_names),
            "road_asset_names": False,
            "district_names": bool(self.district_names),
            "watermark_text": (self.watermark_text or "").strip() or None,
            "poi_mapping_override": str(self.poi_mapping_override),
        }

    def poi_mapping_saved(self) -> None:
        """编辑器保存后使进程内和工作进程缓存全部失效。"""
        from .services.poi.catalog import clear_catalog_cache
        from .services.poi.direct_mapping import clear_cache as clear_direct_cache
        from .services.poi.mapping_registry import clear_cache as clear_mapping_cache

        self._poi_mapping_revision = getattr(self, "_poi_mapping_revision", 0) + 1
        clear_mapping_cache()
        clear_direct_cache()
        clear_catalog_cache()
        if self.session is not None:
            self.session.service_cache.invalidate()
        self._stop_worker()
        if self.source_file and not self._busy:
            self.quick_preview()

    def world_rect(self) -> tuple[float, float, float, float]:
        """当前文档的实测世界范围 ``(x0, z0, x1, z1)``，取不到时回落 CS1 全图。

        幅面必须读导出文件里的 terrain 网格（``origin`` / ``cell`` / ``rows``
        / ``cols``），不能按代数硬编码：CS1 是 17.28 km、CS2 只有 14.336 km，
        写死常量会让「整个城市」在 CS2 上多出一圈黑边，也会让范围 n 的边长
        算错。CS1 的旧 XML 里没有 terrain 块，那时才回落到 17280 常量。
        """
        terrain = getattr(getattr(self, "document", None), "terrain", None)
        if terrain is not None:
            try:
                rows = int(getattr(terrain, "rows", 0) or 0)
                cols = int(getattr(terrain, "cols", 0) or 0)
                cell = float(getattr(terrain, "cell", 0.0) or 0.0)
                origin = getattr(terrain, "origin", None)
                origin_x = float(origin.x)
                origin_z = float(origin.z)
            except (TypeError, ValueError, AttributeError):
                pass
            else:
                if rows > 0 and cols > 0 and cell > 0:
                    return (origin_x, origin_z,
                            origin_x + cols * cell, origin_z + rows * cell)
        return (-WORLD_HALF, -WORLD_HALF, WORLD_HALF, WORLD_HALF)

    def _resolve_range(self) -> tuple:
        """按当前范围设置解析 ``(region, auto_size, region_policy)``。

        新设置使用游戏地块边长 × n；旧除数设置在应用新弹窗前保持原样。
        定位选「最繁华」时，把窗口边长（米）交给 worker 的密度滑窗。
        """
        x0, z0, x1, z1 = self.world_rect()
        span = min(x1 - x0, z1 - z0)
        if span <= 0:
            return None, False, None
        selection = self.tile_range_selection()
        if selection is not None:
            n, anchor = selection["tiles"], selection["anchor"]
            if anchor == "manual":
                a, b, c, d = selection["bounds"]
                return ((x0+a*(x1-x0), z0+b*(z1-z0),
                         x0+c*(x1-x0), z0+d*(z1-z0)), False, None)
            if n == 0:
                return ((x0, z0, x1, z1), False, None)
            side = min(span, n * self.range_tile_grid().tile_m)
        else:
            # 旧版保存范围是除数而非地块数，在用户接受新对话框前保持其物理覆盖范围。
            n = max(RANGE_N_MIN, min(RANGE_N_MAX, int(self.range_n or 1)))
            if n == RANGE_N_MIN:
                return (document_city_bounds(self.document), False, None)
            side, anchor = span / n, self.range_anchor
        if anchor == "busiest":
            return None, False, RegionPolicy(
                kind="most_prosperous", tiles=n, side_m=side).to_dict()
        return square_region((x0, z0, x1, z1), side), False, None

    def _build_job(self, size: int, preview: bool) -> dict:
        region, auto_size, policy = self._resolve_range()
        job = {
            "file": self.source_file,
            "style": self.theme_stem,
            "layers": self._enabled_layers(),
            "size": int(size),
            "sel_size": self.size,
            "aa": bool(self.aa),
            "preview": bool(preview),
            "job_kind": "normal",
            "region": region,
            "region_policy": policy,
            "ui": self._capture_ui_state(),
        }
        if auto_size:
            job["auto_size"] = True
        self._attach_poi_render_config(job, "preview" if preview else "normal_png")
        return job

    def quick_preview(self, callbacks: dict | None = None) -> bool:
        """兼容旧调用名：中央预览现在重新编译完整 Scene。"""
        return self.compile_preview_scene(callbacks)

    def start_preview_server(self) -> str:
        """启动仅监听 loopback 的 Viewer/Scene 同源服务。"""
        return self._preview_server.start()


    def preview_theme(self, style_data: dict | None = None) -> dict:
        result = preview_theme(
            self._preview_style(style_data), self._preview_source_options())
        if self.watermark_text.strip():
            result["watermark"]["text"] = self.watermark_text.strip()
        return result

    def _preview_style(self, style_data=None):
        data = copy.deepcopy(style_data or self.current_style_data())
        data.setdefault("roads", {})["accurate_intersections"] = self.accurate_intersections
        data["roads"]["use_game_names"] = self.road_game_names
        ui = self._capture_ui_state()
        cfg = data.setdefault("poi_icons", {})
        self._attach_poi_render_config(
            {"ui": ui}, "preview")
        cfg["active_profile"] = copy.deepcopy(ui["poi_profile"])
        cfg["transit_profile"] = {**self.get_poi_profile("transit"), "view": "transit"}
        cfg["facility_selection"] = copy.deepcopy(self.facility_poi_overrides)
        cfg["visibility_snapshot"] = copy.deepcopy(
            ui["poi_visibility_snapshot"])
        cfg["use_game_names"] = self.poi_game_names
        cfg["show_name"] = True
        cfg["name_filters"] = {"player": True, "important": True}
        cfg["hidden_categories"] = sorted(self.poi_hidden_categories)
        for owner in ("building", "transit"):
            cfg.setdefault(owner, {})["enabled"] = True
        # 名字始终随 POI 要素进入预览数据；是否显示由 Viewer 即时控制。
        # 否则切一个文字开关就会让整个 pois 数据源失效并重新加载场景。
        cfg.setdefault("building", {})["show_name"] = True
        cfg.setdefault("transit", {})["show_name"] = True
        for key in ("visual_merge_enabled", "visual_merge_distance",
                    "grid_size", "max_per_cell"):
            cfg.pop(key, None)
        # 同一场景供普通/公交视图使用，运输模式由 Viewer 筛选。
        data.setdefault("transports", {})["enabled"] = list(TYPE_REGISTRY)
        data["_poi_mapping_override"] = str(self.poi_mapping_override)
        built = data.setdefault("built_area", {})
        built.setdefault("contour_upscale", 4)
        built.setdefault("smooth_iters", 6)
        built.setdefault("smooth_blur", 2.0)
        return data

    def _preview_source_options(self) -> dict:
        station = self.current_style_data().get(
            "transports", {}).get("station", {}) or {}


        transit_options = getattr(self, "_preview_transit_options", None) or self.transit_options()
        return {
            "bounds": document_city_bounds(self.document),
            "poi_transit_types": list(self.transit_types),
            "poi_transit_line_ids": copy.deepcopy(transit_options.get("line_ids")),
            "transit_view_types": copy.deepcopy(transit_options.get("types")),
            "transit_view_line_ids": copy.deepcopy(transit_options.get("line_ids")),
            "terrain": dict(self.terrain),
            "poi_mapping_revision": getattr(self, "_poi_mapping_revision", 0),
            "poi_hidden_categories": sorted(self.poi_hidden_categories),
            "poi_player_names": bool(self.poi_player_names),
            "poi_important_names": bool(self.poi_important_names),
            # 文字开关随轻量主题下发给 Viewer，不参与数据源签名。
            "road_names": bool(self.road_names),
            "district_names": bool(self.district_names),
            "poi_building_names": bool(self.poi_names),
            "poi_transit_names": bool(self.transport_names),
            "road_player_names": bool(self.road_player_names),
            "road_asset_names": False,
            "station_merge_enabled": bool(station.get("merge_enabled", True)),
            "station_merge_range": style_scalar(station.get("merge_range"), 48.0),
            "facility_poi_overrides": dict(self.facility_poi_overrides),
            "facility_hierarchy_enabled": bool(
                self.facility_hierarchy_enabled),
            "facility_child_min_zoom": float(self.facility_child_min_zoom),
            "district_ids": (None if self.district_ids is None
                             else sorted(self.district_ids)),
            "park_ids": (None if self.park_ids is None
                         else sorted(self.park_ids)),
        }

    def preview_needs_rebuild(self, style_data: dict | None = None) -> bool:
        """判断几何或地形是否与已发布预览资源不同。"""
        if self.session is None:
            return False
        previous = getattr(self, "_preview_bundle", None)
        if (previous is None
                or getattr(self, "_preview_bundle_session", None) is not self.session):
            return True
        style = self._preview_style(style_data)
        options = self._preview_source_options()
        from .preview_scene import preview_source_signatures
        return (previous.manifest.get("sourceSignatures")
                != preview_source_signatures(style, options))

    def invalidate_preview_compile(self) -> None:
        """阻止进行中的预览构建发布过期结果。"""
        self._preview_revision += 1
        if self._preview_base_presentation is not None:
            self._preview_base_presentation[1].set()
            self._preview_base_presentation = None

    def acknowledge_preview_base(self, revision: int) -> None:
        pending = self._preview_base_presentation
        if pending is not None and pending[0] == revision == self._preview_revision:
            self._preview_base_presentation = None
            pending[1].set()


    def compile_preview_scene(self, callbacks: dict | None = None,
                              style_data: dict | None = None,
                              *, force: bool = False,
                              ) -> bool:
        """从当前 DocumentSession 异步编译版本化完整 Scene。"""
        if self._local_png_active:
            self._pending_preview = (callbacks, copy.deepcopy(style_data), force)
            return True
        if self.session is None:
            return False
        session = self.session
        style = self._preview_style(style_data)
        visibility = self.map_layer_visibility()
        previous = getattr(self, "_preview_bundle", None)
        if getattr(self, "_preview_bundle_session", None) is not session:
            previous = None
        options = self._preview_source_options()
        from .preview_scene import preview_source_signatures
        source_signatures = preview_source_signatures(style, options)
        if (not force and previous
                and previous.manifest.get("sourceSignatures")
                == source_signatures):
            return True
        options["force"] = bool(force)
        self.invalidate_preview_compile()
        revision = self._preview_revision
        base_presented = (threading.Event()
                          if previous is None and self.preview_waits_for_presentation else None)
        if base_presented is not None:
            self._preview_base_presentation = (revision, base_presented)
        self._preview_compile_signatures = source_signatures
        callback_map = callbacks or self._callbacks or {}

        def emit(name: str, *args) -> None:
            fn = callback_map.get(name)
            if fn is not None:
                fn(*args)

        def report(pct: float, message: str) -> None:
            self._post(lambda: emit("progress", pct, message)
                       if revision == self._preview_revision
                       and session is self.session else None)

        options["progress"] = report
        class CompileCancelled(Exception):
            pass

        def check_cancel():
            if revision != self._preview_revision or session is not self.session:
                raise CompileCancelled()
        options["check_cancel"] = check_cancel

        def publish(bundle) -> None:
            if (revision != self._preview_revision
                    or session is not self.session):
                bundle.close()
                return
            try:
                self._preview_server.replace_bundle(bundle)
                self._preview_bundle = bundle
                self._preview_bundle_session = session
                emit("scene_ready", bundle,
                     self._preview_server.manifest_url)
            except Exception as exc:  # noqa: BLE001
                self.invalidate_preview_compile()
                bundle.close()
                emit("scene_error", exc)


        if previous is not None:
            previous.retain()
        def worker() -> None:
            base = None
            bundle = None
            try:
                if previous is None:
                    base = PreviewSceneCompiler.compile_full(
                        session, style, revision, visibility=visibility,
                        options={**options, "phase": "base"})
                    base.retain()
                    self._post(lambda stage=base: publish(stage))
                    check_cancel()
                bundle = PreviewSceneCompiler.compile_full(
                    session, style, revision, visibility=visibility,
                    options={**options, "phase": "full"}, previous=base or previous)
                if base_presented is not None:
                    base_presented.wait()
                check_cancel()
                self._post(lambda stage=bundle: publish(stage))
            except CompileCancelled:
                if bundle is not None:
                    bundle.close()
                if base is not None and base is not getattr(self, "_preview_bundle", None):
                    base.close()
            except Exception as exc:
                if bundle is not None:
                    bundle.close()
                if base is not None and base is not getattr(self, "_preview_bundle", None):
                    base.close()
                traceback.print_exc()
                self._post(lambda error=exc: emit("scene_error", error)
                           if revision == self._preview_revision and session is self.session else None)
            finally:
                if base is not None:
                    base.release()
                if previous is not None:
                    previous.release()

        self._preview_compile_thread = threading.Thread(
            target=worker, name=f"preview-scene-{revision}", daemon=True)
        self._preview_compile_thread.start()
        return True

    def render_map(self, callbacks: dict | None = None, *, png_poi_options=None) -> bool:
        if not self.source_file:
            return False
        job = self._build_job(self.size, preview=False)
        if png_poi_options:
            job["ui"]["png_poi_options"] = copy.deepcopy(png_poi_options)
        return self.submit(job, callbacks)

    def transit_options(self, metro: bool = False) -> dict:
        """根据已保存的界面值构造既有工作进程选项协议。"""
        values = self._settings.get("values", {}) or {}
        prefix = "metro_view" if metro else "transit_view"
        # 地铁示意图默认画轨道三类（旧版 6.6.3：
        # 「地铁/火车/单轨——经典地铁图范围」）；早先这里只放 Metro，
        # 一个只有单轨或火车线的城市会直接报"没有可渲染的线路"。
        default_types = (["Metro", "Train", "Monorail"] if metro
                         else list(TYPE_REGISTRY))
        types = values.get(prefix + "_types", default_types)
        if not isinstance(types, list) or (metro and not types):
            types = default_types
        opts = {
            "types": [t for t in types if t in TYPE_REGISTRY],
            "stations": bool(values.get(prefix + "_stations", True)),
            "label_scale": float(values.get(prefix + "_label_scale", 1.0) or 1.0),
            "icon_scale": float(values.get(prefix + "_icon_scale", 1.0) or 1.0),
            "palette_index": int(values.get(prefix + "_palette_index", 0) or 0),
            "auto_color": bool(values.get(prefix + "_auto_color", False)),
            "title_scale": float(values.get(prefix + "_title_scale", 1.0) or 1.0),
            "legend_scale": float(values.get(prefix + "_legend_scale", 1.0) or 1.0),
        }
        # line_ids / station_line_ids 只有地铁示意图读回。公共交通视图的
        # 「选择线路」是**当前会话的临时筛选**：不落盘、也不读回，重开即回到
        # 全选（PM 2026-09-11）；那边只有外层的大类（types）跟着设置走。
        # 地铁早期把读回关在 ``not metro`` 后面，写进 metro_view_line_ids 却
        # 永远读不回来 → 勾掉线路一退出就恢复全选（PM 2026-09-10）。
        # 未保存过时不设键，保持 worker 侧 ``if "line_ids" in opts`` 的判据
        # （有键才过滤），避免把"没选过"误当成"选了空集"。
        if metro:
            for option, suffix in (("line_ids", "line_ids"),
                                   ("station_line_ids", "station_line_ids")):
                raw = values.get(f"{prefix}_{suffix}")
                if isinstance(raw, list):
                    opts[option] = _int_line_ids(raw)
        if metro:
            legacy_name_mode = values.get(
                "metro_view_station_names_mode", "all")
            station_names_enabled = bool(values.get(
                "metro_view_station_names_enabled",
                legacy_name_mode != "none"))
            station_name_style = values.get(
                "metro_view_station_name_style",
                legacy_name_mode if legacy_name_mode in ("all", "custom")
                else "all")
            opts.update({
                "pure": True,
                "abstract": True,
                "size": int(values.get("metro_view_output_size", 0) or 0),
                # 可见性和展示方式分别是标准输入；station_names_mode 只在此处推导为工作进程兼容字段。
                "station_names_enabled": station_names_enabled,
                "station_name_style": station_name_style,
                "station_names_mode": (station_name_style
                                       if station_names_enabled else "none"),
                "station_style": values.get(
                    "metro_view_station_style", "rmg"),
                "station_size": float(values.get(
                    "metro_view_station_size", 8.0) or 8.0),
                "station_label_position": values.get(
                    "metro_view_station_label_position", "auto"),
                "line_width": float(values.get(
                    "metro_view_line_width", 3.0) or 3.0),
                "language": values.get("metro_view_language", "zh"),
                "schematic_options": {
                    "adaptive_scale": bool(values.get(
                        "metro_view_adaptive_scale", True)),
                    "adaptive_strength": float(values.get(
                        "metro_view_adaptive_strength", .85) or .85),

                },
                "legend_enabled": bool(values.get(
                    "metro_view_legend_enabled", True)),
                "legend_position": values.get(
                    "metro_view_legend_position", "right_top"),
                # 画布背景留空 = 跟随主题（worker 侧只在非空且可解析时才覆盖），
                # 与旧版 6.6.3 对话框的默认值一致；早先默认 "#FFFFFF" 会把
                # 每张地铁图都刷成纯白，脱离主题。
                "background": str(values.get("metro_view_background", "") or ""),
                "title_enabled": bool(values.get("metro_view_title_enabled", True)),
                "title_template": values.get("metro_view_title_template", ""),
                "schematic_water": bool(values.get("metro_view_water", True)),
                "water_smoothing": float(values.get("metro_view_water_smoothing", 8)),
                "facilities_enabled": bool(values.get(
                    "metro_view_facilities_enabled", False)),
                "facilities_scale": float(values.get(
                    "metro_view_facilities_scale", 1.0) or 1.0),
                "spacing": int(values.get("metro_view_spacing", 281) or 281),
                "parallel_gap": float(values.get(
                    "metro_view_parallel_gap", 1.55) or 1.55),
                "metro_edits": copy.deepcopy(values.get("metro_view_metro_edits")),
            })
            for name, default in {
                "disclaimer": True, "scale_info": True, "data_notice": True,
                "generated_at": True, "north_arrow": False, "credit": True,
            }.items():
                opts[f"ann_{name}"] = bool(values.get(
                    f"metro_view_ann_{name}", default))
        else:
            style = self.current_style_data().get("transports", {}) or {}
            label = style.get("label", {}) or {}
            # 公共交通线路选择仅属于当前会话，不读取落盘白名单。
            opts.update({
                "transit_mask": float(values.get(
                    "transit_view_mask_strength", .6) or 0.0),
                "base_water": bool(values.get("transit_view_base_water", True)),
                "base_road": bool(values.get("transit_view_base_road", True)),
                "base_district": bool(values.get(
                    "transit_view_base_district", True)),
                "facility_names_enabled": bool(values.get(
                    "transit_view_facility_names_enabled",
                    values.get("transit_view_station_names_enabled", True))),
                "facilities_enabled": bool(values.get(
                    "transit_view_facilities_enabled", True)),
                "facility_icon_size": float(values.get(
                    "transit_view_facility_icon_size", 24) or 24),
                "facility_density": str(values.get(
                    "transit_view_facility_density", "all") or "all"),
                "facility_min_zoom": float(values.get(
                    "transit_view_facility_min_zoom", 8) or 8),
                "ordinary_pois": str(values.get(
                    "transit_view_ordinary_pois", "hidden") or "hidden"),
                "line_width": style_scalar(self.field_value("transport.width", 3), 3),
                "station_size": style_scalar(self.field_value("transport.size", 8), 8),
                "merge_enabled": bool(self.field_value("transport.merge", True)),
                "merge_range": float(self.field_value("transport.distance", 48)),
                "line_label_enabled": bool(label.get("enabled", True)),
                "line_label_size": 12.0,
                "station_name_size": 12.0,
                "line_label_interval": style_scalar(self.field_value(
                    "transport.label.interval", 600), 600),
            })
        # 嵌套的 views.json 是视图设置的权威来源；旧扁平值仅供回退兼容。
        view = self._view_section(metro)
        raw_types = view.get("types")
        if isinstance(raw_types, list):
            opts["types"] = [item for item in raw_types if item in TYPE_REGISTRY]
        if metro:
            raw_line_ids = view.get("line_ids")
            if isinstance(raw_line_ids, list):
                opts["line_ids"] = _int_line_ids(raw_line_ids)
            else:
                opts.pop("line_ids", None)
            station_line_ids = view.get("station_line_ids")
            if isinstance(station_line_ids, list):
                opts["station_line_ids"] = _int_line_ids(station_line_ids)
            else:
                opts.pop("station_line_ids", None)
            stations = view.get("stations", {})
            names = view.get("station_names", {})
            if isinstance(stations, dict):
                if "enabled" in stations:
                    opts["stations"] = bool(stations["enabled"])
                opts["station_diameter"] = float(stations["diameter"])
                opts["merge_enabled"] = bool(stations["merge"])
                opts["merge_range"] = float(stations["merge_range"])
                opts.pop("station_size", None)
            if isinstance(names, dict):
                for key in ("enabled", "mode", "style", "size", "font", "color", "stroke_width", "stroke_color"):
                    if key in names:
                        opts[{"enabled": "station_names_enabled",
                              "mode": "station_names_mode",
                              "style": "station_name_style",
                              "size": "station_name_size", "font": "station_font", "color": "station_name_color",
                              "stroke_width": "station_name_stroke", "stroke_color": "station_name_stroke_color"}[key]] = names[key]
                opts["station_names_mode"] = (
                    opts.get("station_name_style", "all")
                    if opts.get("station_names_enabled", True) else "none")
            diagram = view.get("diagram", {})
            if isinstance(diagram, dict):
                for key in ("adaptive_scale", "adaptive_strength", ):
                    if key in diagram:
                        opts.setdefault("schematic_options", {})[key] = diagram[key]
                for key in ("spacing", "parallel_gap"):
                    if key in diagram:
                        opts[key] = diagram[key]
            legend = view.get("legend", {})
            if isinstance(legend, dict):
                if "enabled" in legend:
                    opts["legend_enabled"] = bool(legend["enabled"])
                if "position" in legend:
                    opts["legend_position"] = legend["position"]
            opts["schematic_water"] = bool(view.get("schematic_water", opts.get("schematic_water", True)))
            opts["water_smoothing"] = float(view.get("water_smoothing", opts.get("water_smoothing", 8)))
            facilities = view.get("facilities", {})
            if isinstance(facilities, dict):
                if "enabled" in facilities:
                    opts["facilities_enabled"] = bool(facilities["enabled"])
                if "scale" in facilities:
                    opts["facilities_scale"] = float(facilities["scale"])
        else:
            layers = view.get("layers", {})
            if isinstance(layers, dict):
                opts["layers"] = {
                    str(key): bool(value) for key, value in layers.items()
                    if key not in {"stations", "facilities", "facility_names", "station_names"}
                }
            names = view.get("station_names", {})
            opts["station_names_enabled"] = bool(names.get("enabled", False))
            opts["station_name_size"] = float(names.get("size", 12.0))
            base = view.get("base_layers", {})
            stations = view.get("stations", {})
            facilities = view.get("facilities", {})
            if "mask" in view:
                opts["transit_mask"] = float(view["mask"])
            if isinstance(base, dict):
                for key in ("water", "road", "district"):
                    if key in base:
                        opts["base_" + key] = bool(base[key])
            if isinstance(stations, dict):
                if "enabled" in stations:
                    opts["stations"] = bool(stations["enabled"])
                if "size" in stations:
                    opts["station_size"] = float(stations["size"])
                if "merge" in stations:
                    opts["merge_enabled"] = bool(stations["merge"])
                if "merge_range" in stations:
                    opts["merge_range"] = float(stations["merge_range"])
            if isinstance(facilities, dict):
                for old, new in (("enabled", "facilities_enabled"),
                                 ("names_enabled", "facility_names_enabled"),
                                 ("icon_size", "facility_icon_size"),
                                 ("density", "facility_density"),
                                 ("min_zoom", "facility_min_zoom")):
                    if old in facilities:
                        opts[new] = facilities[old]
            if "ordinary_pois" in view:
                opts["ordinary_pois"] = str(view["ordinary_pois"])
            label = view.get("line_label", {})
            if isinstance(label, dict):
                for old, new in (("enabled", "line_label_enabled"),
                                 ("size", "line_label_size"),
                                 ("interval", "line_label_interval")):
                    if old in label:
                        opts[new] = label[old]
            if "line_width" in view:
                opts["line_width"] = float(view["line_width"])
        if metro:
            for retired in ("palette_index", "auto_color", "label_scale", "icon_scale"):
                opts.pop(retired, None)
        else:
            from .transit_ui_schema import normalize_transit_presentation
            normalize_transit_presentation(opts)
        return opts

    def _reconcile_transit_line_selection(self, catalog) -> bool:
        """清理设置里不成立的线路白名单，返回是否改动了设置。

        两条规矩：

        1. **公共交通视图**的线路勾选是当前会话的临时筛选 —— 不持久化、
           也不读回（PM 2026-09-11），所以历史落盘的
           ``transit_view_line_ids`` / ``transit_view_station_line_ids``
           一律删掉，留着只会被别的代码误当成配置读。
        2. **地铁示意图**的白名单仍跟着设置走，但 ``settings.json`` 与存档
           同目录、多座城市共用，很容易是**另一座城市**留下的 id —— 对不上
           当前存档时，pure 分支的 ``enabled_line_ids`` 会把线路整批过滤掉。
           这里按当前存档线路目录求交集，并保持既有的三态约定：缺键 = 没筛过、
           ``[]`` = 显式清空，两者都不动；未知 ID 只移除无效项，零命中保持
           空集，不能擅自恢复全部线路。
        """
        values = dict(self._settings.get("values", {}) or {})
        changed = False
        for stale in ("transit_view_line_ids",
                      "transit_view_station_line_ids"):
            if stale in values:
                values.pop(stale, None)
                changed = True
        known = {int(item["id"]) for item in catalog
                 if item.get("id") is not None}
        if known:
            for key in ("metro_view_line_ids",
                        "metro_view_station_line_ids"):
                raw = values.get(key)
                if not isinstance(raw, list) or not raw:
                    continue
                current = _int_line_ids(raw)
                kept = [line_id for line_id in current if line_id in known]
                if kept != current:
                    values[key] = kept
                    changed = True
        transit_view = self._view_section(False)
        for stale in ("line_ids", "station_line_ids"):
            if stale in transit_view:
                transit_view.pop(stale)
                changed = True
        metro_view = self._view_section(True)
        if known:
            for key in ("line_ids", "station_line_ids"):
                raw = metro_view.get(key)
                if not isinstance(raw, list) or not raw:
                    continue
                current = _int_line_ids(raw)
                kept = [line_id for line_id in current if line_id in known]
                if kept != raw:
                    metro_view[key] = kept
                    changed = True
        if changed:
            self._settings["values"] = values
        return changed

    def render_transit(self, metro: bool = False, opts: dict | None = None,
                       callbacks: dict | None = None, *, png_poi_options=None) -> bool:
        """按旧界面协议提交交通或地铁工作进程作业。"""
        if not self.source_file:
            return False
        return self.submit(self._build_transit_job(metro, opts, png_poi_options), callbacks)

    @property
    def local_render_active(self) -> bool:
        return self._local_render_thread is not None and self._local_render_thread.is_alive()

    def render_metro_in_engine(self, opts: dict, callbacks: dict | None = None) -> bool:
        """在不额外启动嵌套进程的情况下渲染 WinUI 地铁预览。"""
        return self._submit_metro_job_in_engine(
            self._build_transit_job(True, opts), callbacks)

    def _submit_metro_job_in_engine(self, job: dict,
                                    callbacks: dict | None = None) -> bool:
        if not self.source_file or self.document is None or self._busy or self.local_render_active:
            return False
        if callbacks is not None:
            self._callbacks = callbacks
        self._serial += 1
        serial = self._serial
        self._local_render_stop = threading.Event()
        stop = self._local_render_stop
        document = self.document
        self._busy = True
        self._emit("busy", True)

        def progress(fraction, message, _stage=None):
            if stop.is_set():
                raise RuntimeError(i18n.tr('ui.guir_backend.progress.ab09202a2b'))
            self._emit("progress", float(fraction) * 100, message)

        def run() -> None:
            try:
                result = _render_worker.run_transit_task(
                    job, progress=progress,
                    mapdata_cache={job["file"]: document})
                error = None
            except _render_worker.TransitSelectionError as exc:
                result, error = None, str(exc)
            except Exception:  # noqa: BLE001
                result, error = None, traceback.format_exc()

            def finish() -> None:
                self._local_render_thread = None
                if serial == self._serial and not stop.is_set():
                    if error is None:
                        self._on_worker_done(result)
                    else:
                        self._on_worker_error(error)
                else:
                    self._emit("busy", False)

            self._post(finish)

        self._local_render_thread = threading.Thread(
            target=run, name="winui-metro-render", daemon=True)
        self._local_render_thread.start()
        return True

    def _build_transit_job(self, metro=False, opts=None, png_poi_options=None) -> dict:
        options = dict(opts or self.transit_options(metro))
        region, _auto_size, policy = self._resolve_range()
        job = {
            "kind": "transit",
            "file": self.source_file,
            "style": self.theme_stem,
            "types": list(options.get("types") or []),
            "opts": options,
            "layers": self._enabled_layers(),
            "size": 0 if metro else self.size,
            "sel_size": 0 if metro else self.size,
            "aa": 2 if automatic_antialias() else 1,
            "region": region,
            "region_policy": policy,
            "ui": self._capture_ui_state(),
            "watermark_text": (self.watermark_text or "").strip() or None,
        }
        if png_poi_options and not metro:
            job["ui"]["png_poi_options"] = copy.deepcopy(png_poi_options)
        self._attach_poi_render_config(job, "metro" if metro else "transit_png")
        return job

    def capture_png_export_job(self, context, region=None, *, transit_options=None,
                               metro_options=None, output_profile=None,
                               poi_profile=None, poi_visibility_snapshot=None,
                               poi_profile_target=None,
                               full_city_default=False) -> dict:
        """冻结全部渲染输入，包括视图局部配置和 POI 配置。"""
        context = getattr(context, "value", context)
        if context == "metro":
            job = self._build_transit_job(True, metro_options)
            return copy.deepcopy(job)
        elif context == "transit":
            job = self._build_transit_job(False, transit_options)
            profile_target = "transit_png"
        else:
            job = self._build_job(self.size, preview=False)
            profile_target = "normal_png"

        profile = (self.get_output_profile(profile_target)
                   if output_profile is None
                   else validate_output_profile(output_profile, profile_target))
        size = int(profile["size"])
        rotation = int(profile["rotation"])
        job["size"] = size
        job["sel_size"] = size
        aa = automatic_antialias()
        job["aa"] = (2 if aa else 1) if context == "transit" else aa
        job["ui"]["rotation"] = rotation

        if region is not None:
            source = getattr(region, "source", "map")
            bounds = normalize_bounds(getattr(region, "bounds", region), self.world_rect())
            # 旧 Qt 导出对话框把重新应用的范围标记为 map，旧全城矩形只是哨兵；此处解析该显式选择。
            # WinUI 用 city 或空区域表示新的全城默认值，不能隐式继承旧范围。
            if source == "map" and not full_city_default:
                resolved, _auto, policy = self._resolve_range()
                if resolved is not None:
                    bounds = normalize_bounds(resolved, self.world_rect())
                elif policy and policy.get("side_m", 0) > 0:
                    side = float(policy["side_m"])
                    bounds = (0.0, 0.0, side, side)
            job["region"] = bounds
            job["region_policy"] = None
            job["auto_size"] = False
            job["region_source"] = source
            job["output_size"] = output_dimensions(bounds, size, rotation)
        elif full_city_default:
            bounds = document_city_bounds(self.document)
            job["region"] = bounds
            job["region_policy"] = None
            job.pop("auto_size", None)
            job["region_source"] = "city"
            job["output_size"] = output_dimensions(bounds, size, rotation)
        elif job.get("region") is not None:
            job["output_size"] = output_dimensions(job["region"], size, rotation)

        render_profile_target = profile_target if poi_profile_target is None else poi_profile_target
        self._attach_poi_render_config(
            job, render_profile_target, profile=poi_profile,
            visibility_snapshot=poi_visibility_snapshot)
        if context != "transit" and not job["ui"]["text_enabled"]:
            job["ui"]["poi_visibility_snapshot"]["icons"] = {
                group: False for group in job["ui"]["poi_visibility_snapshot"]["icons"]}
        return copy.deepcopy(job)

    def render_png_export(self, request, callbacks=None, *, transit_options=None,
                          metro_options=None, in_engine=False, file_result=False) -> bool:
        job = copy.deepcopy(request.render_snapshot) if request.render_snapshot else (
            self.capture_png_export_job(request.context, request.region,
                                        transit_options=transit_options,
                                        metro_options=metro_options))
        if not job.get("file"):
            return False
        theme = request.theme_snapshot
        if theme.get("name"):
            job["style"] = theme["name"]
        if "data" in theme:
            job["ui"]["style_overrides"][job["style"]] = copy.deepcopy(theme["data"])
        if request.layer_snapshot:
            selected = {item.settings_key for item in layer_directory()
                        if request.layer_snapshot.get(item.settings_key, False)}
            # POI 可见性由六字段快照确定，父级来源不能再成为第二道开关。
            selected.add("poi")
            job["layers"] = [item.settings_key for item in layer_directory()
                             if item.settings_key in selected]
            job["ui"]["text_enabled"] = bool(request.layer_snapshot.get("label", False))
        if request.png_poi_options and getattr(request.context, "value", request.context) != "metro":
            job["ui"]["png_poi_options"] = dict(request.png_poi_options)
        job["final_png"] = True
        if in_engine and getattr(request.context, "value", request.context) == "metro":
            return self._submit_metro_job_in_engine(job, callbacks)
        if file_result and job.get("kind", "normal") == "normal":
            return self._submit_png_job_in_engine(job, request.target, callbacks)
        if file_result:
            job["png_output_dir"] = str(Path(request.target).parent if request.target else Path(self.output_dir))
        return self.submit(job, callbacks)

    def _submit_png_job_in_engine(self, job, target, callbacks):
        if self._busy or self.local_render_active or self.session is None:
            return False
        callbacks = dict(callbacks or {})
        preview_callbacks = dict(self._callbacks)
        self._callbacks = callbacks
        self._serial += 1
        serial, session = self._serial, self.session
        preview_thread = self._preview_compile_thread
        stop = self._local_render_stop = threading.Event()
        self.discard_png_result()
        parent = Path(target).parent
        parent.mkdir(parents=True, exist_ok=True)
        self._png_tempdir = Path(tempfile.mkdtemp(prefix=".csl-png-", dir=parent))
        job["png_path"] = str(self._png_tempdir / "result.png")
        self._local_png_active = self._busy = True
        self._emit("busy", True)

        def check_cancel():
            if stop.is_set():
                raise RuntimeError(i18n.tr('ui.guir_backend.check_cancel.e4267bce68'))

        def progress(fraction, message):
            check_cancel()
            fn = callbacks.get("progress")
            if fn is not None:
                self._post(lambda: fn(float(fraction) * 100, message) if not stop.is_set() else None)

        def run():
            result, error = None, None
            try:
                while preview_thread is not None and preview_thread.is_alive():
                    check_cancel()
                    preview_thread.join(0.05)
                result = _render_worker.run_render_task(
                    job, session=session, progress=progress, check_cancel=check_cancel)
                check_cancel()
                image = result.pop("image")
                result["image_size"] = image.size
                result["png_path"] = export_png(image, job["png_path"])
                result.pop("thumbnail", None)
                check_cancel()
            except Exception as exc:
                error = exc

            def finish():
                self._local_render_thread = None
                self._local_png_active = False
                self._busy = False
                if stop.is_set() or serial != self._serial or session is not self.session:
                    self.discard_png_result()
                    fn = callbacks.get("cancelled")
                    if fn is not None:
                        fn()
                elif error is not None:
                    self.discard_png_result()
                    fn = callbacks.get("error")
                    if fn is not None:
                        fn(error)
                else:
                    self._callbacks = callbacks
                    self._on_worker_done(result)
                self._callbacks = preview_callbacks
                self._emit("busy", False)
                pending, self._pending_preview = self._pending_preview, None
                if pending is not None:
                    cb, style_data, force = pending
                    self.compile_preview_scene(cb, style_data, force=force)

            self._post(finish)

        self._local_render_thread = threading.Thread(target=run, name="winui-png-render", daemon=True)
        self._local_render_thread.start()
        return True

    def pick_random_preview_region(self, *, scene="overall", seed=None) -> tuple[float, float, float, float] | None:
        """只选择一次局部范围，让 WebGL 和导出共用相同边界。"""
        if self.session is None:
            return None
        view = self.session._legacy_view()
        region = closeup_region(view, scene, rng=random.Random(seed))
        return region

    def _submit_local_image_task(self, build, callbacks=None) -> bool:
        """运行复用的进程内合成器，并返回工作进程形式的结果。"""
        if self._busy or self.document is None or self.session is None:
            return False
        if callbacks is not None:
            self._callbacks = callbacks
        self._busy = True
        self._emit("busy", True)

        def worker():
            started = time.time()
            try:
                image, stats = build()
                payload = {
                    "image": image,
                    "thumbnail": _render_worker._make_thumbnail(image),
                    "size": image.width,
                    "stats": stats or {},
                    "elapsed": time.time() - started,
                    "is_region": True,
                    "context": {"city_info": dict(self.city_info)},
                }
                self._post(lambda: self._on_worker_done(payload))
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                self._post(lambda err=exc: self._on_worker_error(str(err)))

        threading.Thread(target=worker, name="guir-compose", daemon=True).start()
        return True

    def _render_region_for_composer(self, region, size, layers=None, style=None, *, ui=None, aa=None):
        selected = list(self._enabled_layers() if layers is None else layers)
        aa = self.aa if aa is None else aa
        selected = [key for key in selected if key != "watermark"]
        renderer = _render_worker._build_normal_renderer(  # 共用的界面工作进程路径。
            (self._capture_ui_state() if ui is None else ui), style or self.theme_stem, selected,
            int(size) * (2 if aa else 1), preview=False,
             job_kind="normal")
        image = renderer.render(
            self.document, region=region, final_size=int(size),
            session=self.session)
        if aa and image.size != (int(size), int(size)):
            image = image.resize((int(size), int(size)), Image.LANCZOS)
        return image

    def render_interchanges(self, callbacks: dict | None = None,
                            request: dict | None = None) -> bool:
        """通过结构化适配器渲染一组交通枢纽。"""
        request = copy.deepcopy(request or {})
        captured_ui = self._capture_ui_state()
        captured_ui["poi_names"] = bool(request.get("poi_names", captured_ui.get("poi_names", True)))
        captured_ui["text_enabled"] = bool(request.get("labels", captured_ui.get("text_enabled", True)))
        captured_layers = request.get("layers", self._enabled_layers())
        captured_style = self.theme_stem
        captured_aa = automatic_antialias()
        def render_region(region, size):
            return self._render_region_for_composer(region, size, captured_layers, captured_style,
                                                   ui=captured_ui, aa=captured_aa)
        def build():
            from .interchanges import render_interchange_request
            mapdata = self.session._legacy_view()

            def progress(pct, msg):
                self._emit("progress", float(pct) * 100, msg)

            image, interchanges = render_interchange_request(
                mapdata, render_region, request,
                progress=progress)
            if not interchanges:
                raise RuntimeError(tr("gui.error.no_interchange"))
            return image, {"interchange_count": len(interchanges)}

        return self._submit_local_image_task(build, callbacks)

    def update_transit_options(self, metro: bool, opts: dict) -> None:
        """持久化共用的交通对话框选项协议。"""
        if not metro:
            self._preview_transit_options = copy.deepcopy(opts)
        prefix = "metro_view" if metro else "transit_view"
        values = dict(self._settings.get("values", {}) or {})
        common = {
            "types": "types", "stations": "stations",
            "label_scale": "label_scale", "icon_scale": "icon_scale",
            "palette_index": "palette_index", "auto_color": "auto_color",
            "title_scale": "title_scale", "legend_scale": "legend_scale",
        }
        for option, suffix in common.items():
            if option in opts:
                values[f"{prefix}_{suffix}"] = copy.deepcopy(opts[option])
        if metro:
            mapping = {
                # 只有地铁示意图的线路勾选落盘（公共交通视图那份是会话级，
                # 见下方 else 分支）。
                "line_ids": "line_ids",
                "station_line_ids": "station_line_ids",
                "size": "output_size", "station_names_mode": "station_names_mode",
                "station_names_enabled": "station_names_enabled",
                "station_name_style": "station_name_style",
                "language": "language", "station_style": "station_style",
                "station_size": "station_size",
                "station_label_position": "station_label_position",
                "line_width": "line_width",
                "legend_enabled": "legend_enabled",
                "legend_position": "legend_position", "background": "background",
                "title_enabled": "title_enabled", "title_template": "title_template",
                "schematic_water": "water", "water_smoothing": "water_smoothing",
                "facilities_enabled": "facilities_enabled",
                "facilities_scale": "facilities_scale", "spacing": "spacing",
                "parallel_gap": "parallel_gap", "metro_edits": "metro_edits",
            }
        else:
            # 公共交通视图的「选择线路」是当前会话的临时筛选：不落盘、不读回，
            # 重开存档即回到全选（PM 2026-09-11）。外层大类（types）照旧保留。
            # 历史版本落过盘的旧键顺手删掉，别留在文件里被别的代码误读。
            for stale in ("transit_view_line_ids",
                          "transit_view_station_line_ids",
                          "transit_view_station_names_enabled",
                          "transit_view_station_name_size"):
                values.pop(stale, None)
            mapping = {
                "transit_mask": "mask_strength",
                "base_water": "base_water", "base_road": "base_road",
                "base_district": "base_district",
                "facility_names_enabled": "facility_names_enabled",
                "facilities_enabled": "facilities_enabled",
                "facility_icon_size": "facility_icon_size",
                "facility_density": "facility_density",
                "facility_min_zoom": "facility_min_zoom",
                "ordinary_pois": "ordinary_pois",
            }
        for option, suffix in mapping.items():
            if option in opts:
                values[f"{prefix}_{suffix}"] = copy.deepcopy(opts[option])
        if metro:
            scale = opts.get("schematic_options") or {}
            values["metro_view_adaptive_scale"] = bool(
                scale.get("adaptive_scale", True))
            values["metro_view_adaptive_strength"] = float(
                scale.get("adaptive_strength", .85))
            for name in ("disclaimer", "scale_info", "data_notice",
                         "generated_at", "north_arrow", "credit"):
                if f"ann_{name}" in opts:
                    values[f"metro_view_ann_{name}"] = bool(opts[f"ann_{name}"])
        # 把面向工作进程的选项同步到版本化视图文档，保留旧扁平键供回退。
        mode_key = "metro" if metro else "transit"
        section = copy.deepcopy(self._view_section(metro))
        if "types" in opts:
            section["types"] = list(opts.get("types") or [])
        if metro and "line_ids" in opts:
            section["line_ids"] = (None if opts.get("line_ids") is None
                                    else list(opts.get("line_ids") or []))
        elif not metro:
            # 线路标识只在当前城市有效，交通筛选属于会话，不得持久化到 views.json。
            section.pop("line_ids", None)
        if metro and "station_line_ids" in opts:
            section["station_line_ids"] = (
                None if opts.get("station_line_ids") is None
                else list(opts.get("station_line_ids") or []))
        if metro:
            section.setdefault("stations", {})["enabled"] = bool(
                opts.get("stations", True))
            if "station_diameter" in opts:
                section["stations"]["diameter"] = float(opts["station_diameter"])
            for source, key in (("merge_enabled", "merge"), ("merge_range", "merge_range")):
                if source in opts:
                    section["stations"][key] = opts[source]
            section.setdefault("station_names", {}).update({
                target: opts[source] for source, target in (
                    ("station_names_enabled", "enabled"),
                    ("station_names_mode", "mode"),
                    ("station_name_style", "style"),
                    ("station_name_size", "size"),
                    ("station_font", "font"), ("station_name_color", "color"),
                    ("station_name_stroke", "stroke_width"), ("station_name_stroke_color", "stroke_color"),
                ) if source in opts
            })
            section.setdefault("presentation", {}).update({
                key: opts[key] for key in ("label_scale", "icon_scale",
                                            "palette_index", "auto_color")
                if key in opts
            })
            section.setdefault("diagram", {}).update({
                key: opts[key] for key in ("spacing", "parallel_gap") if key in opts
            })
            if "schematic_options" in opts:
                section["diagram"].update(opts["schematic_options"] or {})
            if "schematic_water" in opts:
                section["schematic_water"] = bool(opts["schematic_water"])
            if "water_smoothing" in opts:
                section["water_smoothing"] = float(opts["water_smoothing"])
            if "legend_enabled" in opts or "legend_position" in opts:
                section.setdefault("legend", {}).update({
                    "enabled": bool(opts["legend_enabled"])
                    if "legend_enabled" in opts else section["legend"].get("enabled", True),
                    "position": opts["legend_position"]
                    if "legend_position" in opts else section["legend"].get("position", "right_top"),
                })
            if "facilities_enabled" in opts or "facilities_scale" in opts:
                section.setdefault("facilities", {}).update({
                    "enabled": bool(opts.get("facilities_enabled", False)),
                    "scale": float(opts.get("facilities_scale", 1.0)),
                })
        else:
            section.setdefault("station_names", {})["enabled"] = bool(opts.get("station_names_enabled", False))
            if "station_name_size" in opts:
                section["station_names"]["size"] = float(opts["station_name_size"])
            if isinstance(section.get("layers"), dict):
                section["layers"].pop("station_names", None)
            if "transit_mask" in opts:
                section["mask"] = float(opts["transit_mask"])
            if isinstance(opts.get("layers"), dict):
                section["layers"] = {
                    str(key): bool(value)
                    for key, value in opts["layers"].items()
                }
            section.setdefault("base_layers", {}).update({
                key.removeprefix("base_"): bool(opts[key]) for key in ("base_water", "base_road",
                                                  "base_district") if key in opts
            })
            section.setdefault("stations", {}).update({
                "enabled": bool(opts.get("stations", True)),
                **({"size": float(opts["station_size"])}
                   if "station_size" in opts else {}),
                **({"merge": bool(opts["merge_enabled"])}
                   if "merge_enabled" in opts else {}),
                **({"merge_range": float(opts["merge_range"])}
                   if "merge_range" in opts else {}),
            })
            facility_section = section.setdefault("facilities", {})
            for option_key, view_key in (
                ("facilities_enabled", "enabled"),
                ("facility_names_enabled", "names_enabled"),
                ("facility_icon_size", "icon_size"),
                ("facility_density", "density"),
                ("facility_min_zoom", "min_zoom"),
            ):
                if option_key in opts:
                    facility_section[view_key] = opts[option_key]
            if "ordinary_pois" in opts:
                section["ordinary_pois"] = str(opts["ordinary_pois"])
            if "line_width" in opts:
                section["line_width"] = float(opts["line_width"])
            section.setdefault("line_label", {}).update({
                view_key: opts[option_key] for option_key, view_key in (
                    ("line_label_enabled", "enabled"), ("line_label_size", "size"),
                    ("line_label_interval", "interval")) if option_key in opts
            })
        self._view_settings[mode_key] = section
        self._save_view_settings()
        self._settings["values"] = values
        self._schedule_settings_save()

    def render_region(self, rect, callbacks: dict | None = None) -> bool:
        """渲染选区：rect 为图像像素 (x0, y0, x1, y1)，换算游戏坐标。"""
        if self.result_image is None or self._sel_scale is None:
            return False
        from .renderer import _build_transform
        x0, y0, x1, y1 = rect
        tx = _build_transform(*self.result_image.size, self._sel_game or self.world_rect(), self.rotation)
        points = [tx.inverse(x, y) for x, y in ((x0,y0), (x1,y0), (x0,y1), (x1,y1))]
        return self.render_world_region((min(p[0] for p in points), min(p[1] for p in points),
                                        max(p[0] for p in points), max(p[1] for p in points)), callbacks)

    def render_world_region(self, bounds, callbacks: dict | None = None,
                            *, png_poi_options=None) -> bool:
        """按 Viewer 返回的游戏米制 (x0,z0,x1,z1) 渲染选区。"""
        if not self.source_file:
            return False
        from csmodernmap.ui_contracts import RenderRegion
        try:
            region = RenderRegion(normalize_bounds(bounds, self.world_rect()), source="selection")
        except (TypeError, ValueError):
            return False
        job = self.capture_png_export_job("normal", region)
        if png_poi_options:
            job["ui"]["png_poi_options"] = copy.deepcopy(png_poi_options)
        self._sel_game = region.bounds
        return self.submit(job, callbacks)


    # ------------------------------------------------------------------
    # 输出闭环
    # ------------------------------------------------------------------

    def save_png(self, path: str):
        """保存当前渲染结果为 PNG；无结果返回 None。"""
        if self.result_image is None:
            return None
        export_png(self.result_image, path)
        return path

    def save_png_async(self, path: str, on_done=None,
                       *, avoid_overwrite: bool = False) -> bool:
        """已编码的 PNG 直接移入目标；图像结果在后台编码保存。

        高分辨率 PNG 的压缩和写盘在 GUI 线程上会造成明显卡顿，因此与
        SVG / HTML 导出保持一致，放到独立线程，完成后再回主线程通知。
        返回 False 表示没有可保存的结果。

        ``avoid_overwrite`` 给导出对话框用：目标已存在时落到 ``名字-2.png``，
        不把上一份产物顶掉。「另存为」保持 False —— 那条路用户自己敲名字，
        覆盖是他确认过的。
        """
        image = self.result_image
        png_path = self.result_png_path
        if image is None and png_path is None:
            return False
        target = str(_unique_output_path(path) if avoid_overwrite else Path(path))

        if png_path is not None:
            try:
                if self._png_tempdir is not None:
                    os.replace(png_path, target)
                elif Path(png_path).resolve() != Path(target).resolve():
                    shutil.copyfile(png_path, target)
            finally:
                self.discard_png_result()
            self.result_png_path = target
            if on_done is not None:
                self._post(lambda: on_done(target))
            return True

        def worker() -> None:
            try:
                export_png(image, target)
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                self._emit("error", exc)
                return
            if on_done is not None:
                self._post(lambda: on_done(target))

        threading.Thread(target=worker, name="guir-png", daemon=True).start()
        return True

    def copy_image(self) -> tuple[bool, str]:
        if self.result_png_path is not None:
            with Image.open(self.result_png_path) as image:
                return _copy_image_to_clipboard(image)
        if self.result_image is None:
            return False, tr("gui.error.copy_no_image")
        return _copy_image_to_clipboard(self.result_image)

    def export_web(self, out_dir: str | None = None,
                   callbacks: dict | None = None, *, region=None,
                   target: str | Path | None = None, request=None) -> bool:
        """把中央实时预览的当前快照直接保存成单文件 HTML。"""
        bundle = getattr(self, "_preview_bundle", None)
        if (not self.source_file or not Path(self.source_file).exists()
                or bundle is None
                or getattr(self, "_preview_bundle_session", None) is not self.session):
            return False
        if callbacks is not None:
            self._callbacks = callbacks
        self._export_cancelled.clear()
        target_dir = out_dir or self._output_dir
        # 这几项都可能在场景编译完成后被实时修改，必须与屏幕当前状态对齐
        # （旋转角度只在「导出成什么方向」上生效，所以跟着导出走，不重编译场景）。
        theme = (self.preview_theme(request.theme_snapshot.get("data")) if request else self.preview_theme())
        visibility = dict(request.layer_snapshot) if request and request.layer_snapshot else self.map_layer_visibility()
        rotation = int(request.render_snapshot.get("ui", {}).get("rotation", self.rotation) if request else getattr(self, "rotation", 0) or 0)
        export_region = (tuple(region.bounds if hasattr(region, "bounds") else region)
                         if region is not None else None)
        range_snapshot = None
        if region is None or getattr(region, "source", "selection") == "map":
            if request and request.render_snapshot:
                range_snapshot = copy.deepcopy(request.render_snapshot)
            elif hasattr(self, "range_n"):
                bounds, _auto, policy = self._resolve_range()
                range_snapshot = {"region": bounds, "region_policy": policy, "size": self.size}
        gui_cb = dict(self._callbacks or {})
        export_document = getattr(self.session, "document", self.document)
        export_session = self.session
        from .preview_scene import PreviewSceneBundle, preview_source_signatures
        needs_complete = getattr(bundle, "manifest", {}).get("completion") == "base"
        source_style = source_options = None
        if isinstance(bundle, PreviewSceneBundle):
            source_style = self._preview_style(request.theme_snapshot.get("data") if request else None)
            source_options = self._preview_source_options()
            source_options["bounds"] = (export_region or
                (range_snapshot or {}).get("region") or self.world_rect())
            needs_complete = (needs_complete or bundle.manifest.get("sourceSignatures") !=
                              preview_source_signatures(source_style, source_options))
        if hasattr(bundle, "retain"):
            bundle.retain()

        def worker() -> None:
            export_bundle = bundle
            try:
                from .web_export_v2 import export_preview_bundle

                if self._export_cancelled.is_set():
                    raise RuntimeError(tr("gui.error.export_cancelled"))
                if needs_complete:
                    def check_cancel():
                        if self._export_cancelled.is_set():
                            raise RuntimeError(tr("gui.error.export_cancelled"))
                    export_bundle = PreviewSceneCompiler.compile_full(export_session, source_style,
                        bundle.revision, visibility=visibility,
                        options={**source_options, "phase": "full", "check_cancel": check_cancel}, previous=bundle)
                document = export_document
                selected_region = export_region
                if range_snapshot is not None:
                    from .cmm.document_view import MapDocumentView
                    selected_region = _render_worker._resolve_region(
                        range_snapshot, MapDocumentView(document))[0]
                city = getattr(document, "city_name", "") or ""
                name = (city or "map").strip() or "map"
                safe_name = "".join(
                    ch if ch not in '<>:"/\\|?*' else "_" for ch in name)
                out = Path(target) if target else _unique_output_path(
                    Path(target_dir) / f"{safe_name}_map.html")
                saved = export_preview_bundle(
                    export_bundle, city, str(out), theme=theme,
                    visibility=visibility, region=selected_region,
                    rotation=rotation)
                if self._export_cancelled.is_set():
                    raise RuntimeError(tr("gui.error.export_cancelled"))
                done = gui_cb.get("web_done")
                if done is not None:
                    self._post(lambda: done(saved))
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                err = gui_cb.get("error")
                if err is not None:
                    self._post(lambda error=exc: err(error))
                else:
                    self._emit("error", exc)
            finally:
                if export_bundle is not bundle:
                    export_bundle.close()
                if hasattr(bundle, "release"):
                    bundle.release()

        threading.Thread(target=worker, name="guir-web", daemon=True).start()
        return True

    # ------------------------------------------------------------------
    # 样式覆盖 / 样式编辑器
    # ------------------------------------------------------------------

    def _style_data_mut(self) -> dict:
        """返回当前主题的可变覆盖字典（缺省时以 yaml 为基础）。"""
        if self.theme_stem not in self.style_overrides:
            style_path = resource_dir("styles") / f"{self.theme_stem}.yaml"
            base = load_style(str(style_path)).data
            self.style_overrides[self.theme_stem] = copy.deepcopy(base)
        data = strip_retired_region_palette_fields(self.style_overrides[self.theme_stem])
        self.style_overrides[self.theme_stem] = Style(data).data
        return self.style_overrides[self.theme_stem]

    def apply_style_override(self, field_key: str, value) -> None:
        """把图层侧栏参数写入当前主题覆盖（供渲染与样式编辑器共用）。"""
        field_key = str(field_key or "").strip()
        if not field_key:
            return
        handler = self._UI_FIELD_HANDLERS.get(field_key)
        if handler is not None:
            handler(self, value)
            self._schedule_settings_save()
            return
        data = self._style_data_mut()
        if field_key == "terrain.color":
            terrain = data.setdefault("terrain", {})
            colors = terrain.setdefault("elevation_colors", [{"threshold": 0, "color": value}])
            if colors:
                min(colors, key=lambda stop: stop["threshold"])["color"] = value
            self._schedule_settings_save()
            return
        path = _STYLE_FIELD_PATHS.get(field_key)
        if path is None:
            section, _, key = field_key.partition(".")
            path = (section, key)
        node = data
        for step in path[:-1]:
            node = node.setdefault(step, {})
        if field_key in _ALPHA255_STYLE_FIELDS:
            try:
                value = round(max(0.0, min(100.0, float(value))) / 100.0 * 255.0)
            except (TypeError, ValueError):
                return
        elif field_key in _PERCENT_STYLE_FIELDS:
            try:
                value = max(0.0, min(100.0, float(value))) / 100.0
            except (TypeError, ValueError):
                return
        node[path[-1]] = value
        self._schedule_settings_save()

    def field_value(self, field_key: str, default=None, *, style_data=None):
        """返回功能区图层编辑器当前显示的实时值。"""
        data = self.current_style_data() if style_data is None else style_data
        if field_key == "terrain.color":
            colors = data.get("terrain", {}).get("elevation_colors", [])
            return min(colors, key=lambda stop: stop["threshold"])["color"] if colors else default
        if field_key == "procedural_objects.border" or field_key.startswith("label.outline"):
            return _outline_field_value(data, field_key)
        if field_key == "poi.set":
            return "modern-dark" if data.get("poi_icons", {}).get("appearance") == "dark" else "modern"
        if field_key == "road.width":
            return self._road_width_percent(data)
        if field_key == "transport.width":
            return self._transport_width_value(default, data)
        roads_now = data.get("roads", {}) or {}
        special = {
            "terrain.mode": self.terrain["mode"],
            "terrain.shade": self.terrain["shade"],
            "terrain.sun": self.terrain["sun"],
            "road.names": self.road_names,
            "road.accurate_intersections": self.accurate_intersections,
            # 旧的「普通道路 / 道路描边」已被等级分项取代、从面板撤下，这里只作
            # 兼容保留：它们当初一把写进 main/primary/branch/... 各等级，读回就按
            # 主干道取。（原先固定读 ``roads.secondary``，而 amap/google/dark/vintage
            # 主题根本没有这个等级 → 面板显示的颜色和地图上画的长期对不上。）
            "road.color": road_level_read(roads_now, "main", "fill", default),
            "road.border": road_level_read(roads_now, "main", "border", default),
            "label.roads": self.road_names,
            "label.districts": self.district_names,
            "label.outline": bool(data.get(
                "labels", {}).get("road", {}).get("stroke_width", 0)),
            "transport.names": self.transport_names,
            "poi.names": self.poi_names,
            "poi.game_names": self.poi_game_names,
            "road.game_names": self.road_game_names,
            "poi.density": self.poi_density,
            "poi.merge": self.merge_enabled,
            "poi.distance": self.merge_distance,
            "watermark.text": self.watermark_text,
        }
        # 道路等级分项：一条推导式补 15 个键，读的是主题里该等级真实使用的键。
        special.update({
            f"road.level.{level}.{attr}": road_level_read(
                roads_now, level, attr, default)
            for level in _ROAD_LEVEL_ENUM
            for attr in ("fill", "border", "width")
        })
        if field_key in special:
            return special[field_key]
        path = _STYLE_FIELD_PATHS.get(field_key)
        if path is None:
            return default
        node = data
        for step in path:
            if not isinstance(node, dict) or step not in node:
                return default
            node = node[step]
        if field_key in _ALPHA255_STYLE_FIELDS:
            try:
                node = float(node) / 255.0 * 100.0
            except (TypeError, ValueError):
                return default
        elif field_key in _PERCENT_STYLE_FIELDS:
            try:
                node = float(node) * 100.0
            except (TypeError, ValueError):
                return default
        return copy.deepcopy(node)

    def _road_width_percent(self, style_data=None) -> float:
        data = self.current_style_data() if style_data is None else style_data
        current = float(data.get(
            "roads", {}).get("width_scale", 0.15) or 0.15)
        style_path = resource_dir("styles") / f"{self.theme_stem}.yaml"
        base = float(load_style(str(style_path)).get(
            "roads.width_scale", 0.15) or 0.15)
        return current / base * 100.0

    def _transport_width_value(self, default=3, style_data=None) -> float:
        data = self.current_style_data() if style_data is None else style_data
        width = data.get("transports", {}).get("width", default)
        if isinstance(width, dict):
            width = width.get("scaled", default)
        try:
            return float(width)
        except (TypeError, ValueError):
            return float(default)

    def theme_default_field_value(self, field_key: str, default=None):
        """返回当前主题原始 YAML 中的字段值。"""
        style_path = resource_dir("styles") / f"{self.theme_stem}.yaml"
        node = load_style(str(style_path)).data
        if field_key == "terrain.color":
            return self.field_value(field_key, default, style_data=node)
        outline = _outline_field_value(node, field_key)
        if outline is not None:
            return outline
        if field_key == "poi.set":
            return self.field_value(field_key, default, style_data=node)
        if field_key == "transport.width":
            width = node.get("transports", {}).get("width", default)
            if isinstance(width, dict):
                width = width.get("scaled", default)
            return width
        if field_key.startswith("road.level."):
            # 各等级的"恢复默认值"要回到**当前主题出厂**的那组值，而不是
            # ribbon_schema 里的兜底色 —— 后者只在主题缺这个等级时才用得上。
            _prefix, _scope, level, attr = field_key.split(".", 3)
            return road_level_read(node.get("roads", {}) or {}, level, attr, default)
        path = _STYLE_FIELD_PATHS.get(field_key)
        if path is None:
            return default
        for step in path:
            if not isinstance(node, dict) or step not in node:
                return default
            node = node[step]
        if field_key in _ALPHA255_STYLE_FIELDS:
            try:
                node = float(node) / 255.0 * 100.0
            except (TypeError, ValueError):
                return default
        elif field_key in _PERCENT_STYLE_FIELDS:
            try:
                node = float(node) * 100.0
            except (TypeError, ValueError):
                return default
        return copy.deepcopy(node)

    def current_style_data(self) -> dict:
        """当前主题 yaml + 覆盖合并结果（传给样式编辑器）。"""
        if self.theme_stem in self.style_overrides:
            data = copy.deepcopy(self.style_overrides[self.theme_stem])
            return Style(strip_retired_region_palette_fields(data)).data
        style_path = resource_dir("styles") / f"{self.theme_stem}.yaml"
        return load_style(str(style_path)).data

    def style_preview_render(self, style_data: dict, size: int,
                             scene: str = "overall"):
        """样式编辑器场景加权特写预览渲染（后台线程调用）。"""
        if self.document is None or self.session is None:
            raise RuntimeError(tr("gui.error.preview_need_map"))
        data = strip_retired_region_palette_fields(copy.deepcopy(style_data))
        style = Style(data)
        style.data["_poi_mapping_override"] = str(self.poi_mapping_override)
        renderer = Renderer(style, width=size, height=size,
                            rotation=self.rotation)
        scene_layers = _STYLE_SCENE_LAYERS.get(scene)
        if scene_layers is None:  # overall：全部注册图层（除建成区）
            scene_layers = [
                cls for _key, cls, _default, _title in gui_layer_registry()
                if cls is not BuiltAreaLayer
            ]
        else:
            scene_layers = list(scene_layers)
        for cls in scene_layers:
            if cls is WaterLayer:
                renderer.add_layer(WaterLayer(
                    upscale=1, smooth_iters=2, rdp_epsilon=6.0, min_area=250))
            else:
                renderer.add_layer(cls())
        region = self._style_editor_region(scene)
        return renderer.render(
            self.document, region=region, final_size=size,
            session=self.session)

    def reset_style_preview_regions(self) -> None:
        """丢弃主题编辑器特写区域缓存。

        区域是按场景「加权随机」抽的（见 ``regions.closeup_region``），
        缓存保证同一场景在编辑过程中取景稳定；打开编辑器时清一次，
        下次进入每个场景都会重新随机一处特写。
        """
        self._style_editor_regions.clear()

    def _style_editor_region(self, scene: str) -> tuple:
        """主题编辑器特写区域：场景键缓存，换场景才重新随机。"""
        region = self._style_editor_regions.get(scene)
        if region is None:
            view = self.session._legacy_view() if self.session else None
            region = closeup_region(view, scene, rng=random.Random())
            self._style_editor_regions[scene] = region
        return region

    def on_style_applied(self, data: dict, save_path: str | None = None,
                         new_name: str | None = None, *, persist: bool = False) -> bool:
        """样式编辑器回调：应用覆盖、持久化个人设置或导出 YAML。"""
        from .theme_contract import cleanup_theme
        data = cleanup_theme(data)
        data = strip_retired_region_palette_fields(copy.deepcopy(data))
        saved_stem = None
        if save_path:
            try:
                import yaml
                with open(save_path, "w", encoding="utf-8") as f:
                    yaml.safe_dump(data, f, allow_unicode=True,
                                   sort_keys=False)
            except OSError as exc:
                self._emit("error", RuntimeError(tr("gui.error.theme_save_failed", error=exc)))
                return False
            saved_stem = Path(save_path).stem
        if new_name:
            self._emit("styles_changed", new_name)
        name = self.theme_stem
        previous = self.style_overrides.get(name)
        built_in_path = resource_dir("styles") / f"{name}.yaml"
        if saved_stem == name and Path(save_path).resolve() == built_in_path.resolve():
            self.style_overrides.pop(name, None)
        else:
            self.style_overrides[name] = copy.deepcopy(data)
        if persist:
            values = self._collect_settings_values()
            if not save_settings(values, self._settings_dir):
                if previous is None:
                    self.style_overrides.pop(name, None)
                else:
                    self.style_overrides[name] = previous
                return False
            self._settings["values"] = values
        return True

    @staticmethod
    def performance_log_dir() -> Path:
        return writable_dir() / "performance_logs"

    def open_performance_log_dir(self) -> tuple[bool, str]:
        """创建并打开性能日志目录。"""
        target = self.performance_log_dir()
        try:
            target.mkdir(parents=True, exist_ok=True)
            _open_with_system_viewer(target)
            return True, str(target)
        except (OSError, subprocess.SubprocessError) as exc:
            return False, str(exc)

    def clear_cache(self) -> None:
        """清理解析/渲染缓存：重启常驻子进程（其内存缓存随之清空）。"""
        self._stop_worker()

    def close(self) -> None:
        """关闭：停止子进程、取消轮询、关闭预览控制器。"""
        self._local_render_stop.set()
        self._serial += 1
        self._pending_preview = None
        if self._settings_save_job is not None:
            self.scheduler.cancel(self._settings_save_job)
        self.save_settings_now()
        try:
            self._preview_controller.close()
        except Exception:  # noqa: BLE001
            pass
        self.invalidate_preview_compile()
        self._preview_server.close()
        if self._local_png_active and self._local_render_thread is not None:
            self._local_render_thread.join()
            self._local_png_active = False
        self._stop_worker()

    # ------------------------------------------------------------------
    # 字段 → UI 状态的特殊处理器（不写入 style.data 的字段）
    # ------------------------------------------------------------------

    def _handler_terrain_mode(self, value) -> None:
        self.terrain["mode"] = normalize_terrain_mode(value)

    def _handler_terrain_shade(self, value) -> None:
        try:
            self.terrain["shade"] = float(value)
        except (TypeError, ValueError):
            pass

    def _handler_terrain_sun(self, value) -> None:
        try:
            self.terrain["sun"] = float(value)
        except (TypeError, ValueError):
            pass

    def _handler_road_names(self, value) -> None:
        self.road_names = bool(value)

    def _handler_accurate_intersections(self, value) -> None:
        if type(value) is not bool:
            raise ValueError("accurate intersections must be a boolean")
        self.accurate_intersections = value
        self.invalidate_preview_compile()

    def _handler_label_roads(self, value) -> None:
        self.road_names = bool(value)

    def _handler_label_districts(self, value) -> None:
        self.district_names = bool(value)

    def _handler_label_outline(self, value) -> None:
        """兼容入口：``label.outline`` = 道路文字的描边开关。"""
        self._handler_label_kind_outline("road", value)

    def _handler_procedural_border(self, value) -> None:
        section = self._style_data_mut().setdefault("procedural_objects", {})
        if not bool(value):
            section["border_width"] = 0
        elif not _width_enabled(section.get("border_width", 0)):
            path = resource_dir("styles") / f"{self.theme_stem}.yaml"
            width = load_style(str(path)).data.get("procedural_objects", {}).get("border_width", 0.2)
            section["border_width"] = copy.deepcopy(width) if _width_enabled(width) else 0.2

    def _handler_label_kind_outline(self, kind: str, value) -> None:
        """按文字类别开关描边。

        描边在主题里不是一个 bool，而是 ``stroke_width``（0 = 关）；
        所以这里沿用原道路开关的写法：打开时补一个 2，关掉时置 0，
        已配过更粗的描边就原样留着。
        """
        data = self._style_data_mut()
        section = data.setdefault("labels", {}).setdefault(str(kind), {})
        if bool(value):
            section["stroke_width"] = section.get("stroke_width") or 2
        else:
            section["stroke_width"] = 0

    def _handler_poi_names(self, value) -> None:
        self.poi_names = bool(value)

    def _handler_poi_game_names(self, value) -> None:
        self.poi_game_names = bool(value)

    def _handler_road_game_names(self, value) -> None:
        self.road_game_names = bool(value)

    def _handler_poi_density(self, value) -> None:
        value = str(value)
        self.poi_density = value if value in POI_DENSITY_PRESETS else "auto"
        grid, count = poi_density_params(self.poi_density)
        data = self._style_data_mut().setdefault("poi_icons", {})
        data["grid_size"] = grid
        data["max_per_cell"] = count

    def _handler_poi_set(self, value) -> None:
        cfg = self._style_data_mut().setdefault("poi_icons", {})
        cfg["icons_dir"] = "cn_icons"
        cfg["appearance"] = "dark" if value == "modern-dark" else "light"

    def _handler_poi_merge(self, value) -> None:
        self.merge_enabled = bool(value)

    def _handler_poi_distance(self, value) -> None:
        try:
            self.merge_distance = float(value)
        except (TypeError, ValueError):
            pass

    def _handler_transport_merge(self, value) -> None:
        self.transit_merge_enabled = bool(value)
        metro = self.merge_overrides.setdefault("metro", {})
        metro["enabled"] = bool(value)

    def _handler_transport_distance(self, value) -> None:
        try:
            distance = float(value)
        except (TypeError, ValueError):
            return
        self.hub_core_m = distance
        metro = self.merge_overrides.setdefault("metro", {})
        metro["distance"] = distance

    def _handler_watermark_text(self, value) -> None:
        self.watermark_text = str(value)

    def _handler_watermark_position(self, value) -> None:
        data = self._style_data_mut()
        wm = data.setdefault("watermark", {})
        wm["position"] = _WATERMARK_POS.get(str(value), str(value))

    def _handler_watermark_opacity(self, value) -> None:
        data = self._style_data_mut()
        wm = data.setdefault("watermark", {})
        try:
            wm["opacity"] = max(0.0, min(100.0, float(value))) / 100.0
        except (TypeError, ValueError):
            return

    def _handler_transport_stations(self, value) -> None:
        data = self._style_data_mut()
        st = data.setdefault("transports", {}).setdefault("station", {})
        st["enabled"] = bool(value)

    def _handler_transport_names(self, value) -> None:
        self.transport_names = bool(value)

    def _handler_transport_width(self, value) -> None:
        try:
            scaled = float(value)
        except (TypeError, ValueError):
            return
        transports = self._style_data_mut().setdefault("transports", {})
        width = transports.get("width")
        if not isinstance(width, dict):
            width = {"fixed": 0.5, "scaled": scaled}
        else:
            width = dict(width)
            width["scaled"] = scaled
        transports["width"] = width

    def _handler_building_scheme(self, value) -> None:
        mode = str(value)
        data = self._style_data_mut()
        b = data.setdefault("buildings", {})
        if mode == "original":
            b["color_mode"] = "poi"
            return
        color = {"light": "#D8D2C6", "dark": "#5A5F66"}.get(mode)
        if color is not None:
            b["color_mode"] = "uniform"
            b["default"] = color

    def _handler_building_color(self, value) -> None:
        data = self._style_data_mut()
        buildings = data.setdefault("buildings", {})
        buildings["color_mode"] = "uniform"
        buildings["default"] = str(value)

    def _handler_road_width(self, value) -> None:
        try:
            percent = max(25.0, min(200.0, float(value))) / 100.0
        except (TypeError, ValueError):
            return
        style_path = resource_dir("styles") / f"{self.theme_stem}.yaml"
        base = float(load_style(str(style_path)).get(
            "roads.width_scale", 0.15) or 0.15)
        self._style_data_mut().setdefault("roads", {})["width_scale"] = (
            base * percent)

    def _handler_road_color(self, value) -> None:
        roads = self._style_data_mut().setdefault("roads", {})
        for key in ("main", "primary", "road", "branch", "tertiary",
                    "secondary", "special", "beautification", "pedestrian"):
            if key in roads or key in ("primary", "tertiary", "secondary"):
                roads.setdefault(key, {})["fill"] = str(value)

    def _handler_road_border(self, value) -> None:
        roads = self._style_data_mut().setdefault("roads", {})
        for key, config in roads.items():
            if isinstance(config, dict) and key not in ("tunnel", "bridge"):
                config["border"] = str(value)

    _UI_FIELD_HANDLERS = {
        "terrain.mode": _handler_terrain_mode,
        "terrain.shade": _handler_terrain_shade,
        "terrain.sun": _handler_terrain_sun,
        "road.names": _handler_road_names,
        "road.accurate_intersections": _handler_accurate_intersections,
        "road.width": _handler_road_width,
        "road.color": _handler_road_color,
        "road.border": _handler_road_border,
        # 道路等级分项（填色 / 描边 / 宽度），一条推导式注册 15 个键。
        # 只能引用**模块级**名字：类体里的推导式看不到类体自己的作用域。
        **{f"road.level.{level}.{attr}": _road_level_handler(level, attr)
           for level in _ROAD_LEVEL_ENUM
           for attr in ("fill", "border", "width")},
        "label.roads": _handler_label_roads,
        "label.districts": _handler_label_districts,
        "label.outline": _handler_label_outline,
        "procedural_objects.border": _handler_procedural_border,
        # 各类文字的描边开关（handler 优先于 _STYLE_FIELD_PATHS，见 apply_style_override）。
        # 注意这里**不能**写 lambda 捕获 self：类体作用域对嵌套函数不可见，
        # 所以用模块级工厂生成 (self, value) 形态的处理器。
        **{f"label.outline.{kind}": _label_kind_outline_handler(kind)
           for kind in ("road", "district", "building", "park", "stop")},
        "poi.names": _handler_poi_names,
        "poi.game_names": _handler_poi_game_names,
        "road.game_names": _handler_road_game_names,
        "poi.set": _handler_poi_set,
        "poi.density": _handler_poi_density,
        "poi.merge": _handler_poi_merge,
        "poi.distance": _handler_poi_distance,
        "transport.merge": _handler_transport_merge,
        "transport.distance": _handler_transport_distance,
        "watermark.text": _handler_watermark_text,
        "watermark.position": _handler_watermark_position,
        "watermark.opacity": _handler_watermark_opacity,
        "transport.stations": _handler_transport_stations,
        "transport.names": _handler_transport_names,
        "transport.width": _handler_transport_width,
        "building.scheme": _handler_building_scheme,
        "building.color": _handler_building_color,
    }
