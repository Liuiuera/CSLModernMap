"""处理 WinUI 客户端与渲染进程之间的逐行 JSON 协议。

此进程独占 GuirBackend 和设置文件；标准输出仅用于协议帧，诊断信息和异常堆栈写入标准错误。"""

from __future__ import annotations

from . import i18n

import heapq
import hashlib
import copy
import json
import math
import re
import queue
import sys
import tempfile
import threading
import time
import traceback
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .guir_backend import GuirBackend, STYLE_FIELD_PATHS, THEME_CHOICES
from ._paths import resource_dir
from .export_region import normalize_bounds
from .poi_profiles import POI_GROUPS, default_poi_visibility, canonical_profile_target, default_poi_profile, normalize_poi_visibility_snapshot, validate_poi_profile
from .output_profiles import (
    OUTPUT_PROFILE_TARGETS,
    default_output_profile,
    validate_output_profile,
)
from .i18n import tr
from .map_extent import document_city_bounds, document_game, square_region
from .map_overview import build_map_overview
from .poi_editor_bridge import PoiEditorBridge
from .regions import most_prosperous_window_by_size
from .transit_ui_schema import (option_schema, validate_option, STYLE_FIELDS, LAYER_OPTIONS,
                                station_name_placement, apply_station_name_placement,
                                normalize_transit_presentation)
from .winui_workflows import WinuiWorkflows, METHODS as WORKFLOW_METHODS
from csmodernmap.ui_contracts import (ExportRequest, ContextMode, RenderRegion,
                            export_capabilities)
from .ribbon_schema import FIELDS, FIELD_DEPENDENCIES, LAYER_FIELDS, TRANSIT_TYPES
from .theme_contract import normalize_theme, validate_theme_values

PROTOCOL_VERSION = 1
TEXT_ICON_FIELDS = ("label.roads", "label.districts", "road.game_names", "poi.game_names",
    "transport.stations", "transport.station_names", "poi.set", "poi.size", "poi.font",
    "poi.name_size", "poi.name_stroke", "poi.name_stroke_ratio") + tuple(
        f"label.{attr}.{kind}" for kind in ("road", "district")
        for attr in ("font", "size", "color", "stroke_width", "stroke_color"))
LABEL_CHILDREN = {
    "transit_icon_text": ("poi_names", "transit"),
    "road_text": ("field", "label.roads"),
    "district_text": ("field", "label.districts"),
    "area_icon": ("poi_icons", "area"),
    "area_icon_text": ("poi_names", "area"),
    "transit_icon": ("poi_icons", "transit"),
    "facility_icon": ("poi_icons", "facility"),
    "facility_icon_text": ("poi_names", "facility"),
    "ordinary_icon": ("poi_icons", "ordinary"),
    "ordinary_icon_text": ("poi_names", "ordinary"),
}
THEME_SECTIONS = (
    "canvas", "terrain", "water", "forest", "built_area",
    "zone", "land_use", "district", "roads", "buildings",
    "procedural_objects", "railway", "transports", "poi_icons",
    "labels", "watermark",
)
HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$")
COLOR_PATHS = {tuple(path) for key, path in STYLE_FIELD_PATHS.items()
               if key in FIELDS and FIELDS[key].kind == "color"}
_POI_ICON_PROCESSING_KEYS = {
    "enabled", "merge_enabled", "merge_distance", "facility_hierarchy",
    "grid_size", "max_per_cell", "min_priority", "categories",
    "hidden_categories", "show_name", "name_filters", "visual_merge_enabled",
    "visual_merge_distance", "active_profile", "visibility_snapshot",
    "target", "building", "transit", "use_game_names",
}
def sanitize_theme_data(raw: Any) -> dict:
    """只保留主题外观设置，移除临时状态。"""
    if not isinstance(raw, dict):
        raise ValueError("theme data must be an object")
    from .theme_contract import cleanup_theme
    data = cleanup_theme(raw)
    buildings = data.setdefault("buildings", {})
    if isinstance(buildings, dict):
        for key in ("residential", "commercial", "industrial", "office",
                    "publictransport", "beautification", "public"):
            buildings.pop(key, None)
        buildings.setdefault("color_mode", "poi")
        buildings.setdefault("default", FIELDS["building.default"].default)
        tint = buildings.setdefault("poi_tint", {})
        if isinstance(tint, dict):
            for key in ("enabled", "mix_white", "categories"):
                tint.pop(key, None)
            tint.setdefault("contrast", 0.30)
    data.pop("poi_area", None)
    icons = data.setdefault("poi_icons", {})
    if isinstance(icons, dict):
        for key in _POI_ICON_PROCESSING_KEYS:
            icons.pop(key, None)
        icons.setdefault("font", "SimHei")
    return data


def _polyline_length(points) -> float:
    """水平面 (x, z) 折线长。"""
    points = list(points)
    return sum(math.hypot(float(right.x) - float(left.x), float(right.z) - float(left.z))
               for left, right in zip(points, points[1:]))


def _polygon_area(points) -> float:
    """使用鞋带公式计算水平面面积。"""
    points = list(points)
    if len(points) < 3:
        return 0.0
    return abs(sum(float(left.x) * float(right.z) - float(right.x) * float(left.z)
                   for left, right in zip(points, points[1:] + points[:1]))) / 2.0


class LoopScheduler:
    """在引擎所属线程上运行后端回调。"""

    def __init__(self) -> None:
        self.pending: queue.Queue[tuple[int, Any]] = queue.Queue()
        self.timers: list[tuple[float, int, Any]] = []
        self.cancelled: set[int] = set()
        self.next_token = 0
        self.wakeup = threading.Event()

    def call_soon(self, callback):
        self.next_token += 1
        token = self.next_token
        self.pending.put((token, callback))
        self.wakeup.set()
        return token

    def call_later(self, delay_ms: int, callback):
        self.next_token += 1
        token = self.next_token
        heapq.heappush(self.timers, (time.monotonic() + max(0, delay_ms) / 1000, token, callback))
        self.wakeup.set()
        return token

    def cancel(self, token) -> None:
        if isinstance(token, int):
            self.cancelled.add(token)

    def pump(self) -> None:
        now = time.monotonic()
        while self.timers and self.timers[0][0] <= now:
            _, token, callback = heapq.heappop(self.timers)
            self.pending.put((token, callback))
        while True:
            try:
                token, callback = self.pending.get_nowait()
            except queue.Empty:
                return
            if token in self.cancelled:
                self.cancelled.discard(token)
            else:
                callback()

    def wait_seconds(self) -> float:
        if not self.pending.empty():
            return 0
        return min(max(self.timers[0][0] - time.monotonic(), 0), 0.1) if self.timers else 0.1


@dataclass(slots=True)
class ExportState:
    target: str | None = None
    request_id: str | None = None
    document_epoch: int | None = None
    context: str | None = None
    format: str | None = None
    requested_target: str | None = None


class EngineServer:
    def __init__(self, input_stream=None, output_stream=None, backend=None) -> None:
        self.input = input_stream or sys.stdin
        self.output = output_stream or sys.stdout
        self.scheduler = LoopScheduler()
        self.backend = backend or GuirBackend(self.scheduler)
        self.backend.preview_waits_for_presentation = True
        self.requests: queue.Queue[dict | None] = queue.Queue()
        self.write_lock = threading.Lock()
        self.running = True
        self._clear_export()
        self._saving_export = False
        self.workflows = WinuiWorkflows(self)
        self._document_epoch = 0
        self._stats_cache: dict | None = None
        self._external_busy = False
        self._transit_session_options: dict | None = None
        self._render_mode: str | None = None
        self._metro_request_id: str | None = None
        self._theme_preview_request_id: str | None = None
        self._theme_preview_lock = threading.Lock()
        self._theme_preview_pending: tuple[str, dict, str, int, object] | None = None
        self._theme_preview_busy = False
        self._poi_editor_token: str | None = None
        self._poi_editor_mtime: tuple[int, int | None] | None = None
        self._preview_files = tempfile.TemporaryDirectory(prefix="cslmm-winui-")
        self.callbacks = {
            "progress": self._render_progress,
            "busy": lambda value: self.event("busy", {"value": bool(value) or self._saving_export}),
            "meta": lambda info, source: self.event("meta", {"info": info, "source": source}),
            "error": self._backend_error,
            "scene_error": lambda error: self.event("sceneError", {"message": str(error)}),
            "scene_ready": self._scene_ready,
            "done": self._render_done,
            "web_done": lambda path: self.event("exportDone", {"path": str(path)}),
        }

    def send(self, frame: dict) -> None:
        with self.write_lock:
            self.output.write(json.dumps(frame, ensure_ascii=False, separators=(",", ":")) + "\n")
            self.output.flush()

    def event(self, name: str, payload: dict) -> None:
        self.send({"event": name, "payload": payload})

    def _require_document_epoch(self, params: dict) -> None:
        epoch = params.get("documentEpoch")
        if type(epoch) is not int or epoch != self._document_epoch:
            raise RuntimeError(i18n.tr('ui.winui_engine._require_document_epoch.8e11490207'))

    @staticmethod
    def _profile_paths(profile: dict, prefix: str = "") -> set[str]:
        paths = set()
        for key, value in profile.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, dict):
                paths.update(EngineServer._profile_paths(value, path))
            else:
                paths.add(path)
        return paths

    @staticmethod
    def _set_profile_path(profile: dict, path: str, value: Any) -> None:
        parts = path.split(".")
        node = profile
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                raise ValueError(f"unknown profile field: {path}")
            node = child
        if parts[-1] not in node:
            raise ValueError(f"unknown profile field: {path}")
        node[parts[-1]] = copy.deepcopy(value)

    def _scene_ready(self, bundle, manifest_url: str) -> None:
        self.event("sceneReady", {"manifestUrl": manifest_url,
                                  "sceneId": bundle.scene_id, "revision": bundle.revision,
                                  "completion": bundle.manifest.get("completion", "complete"),
                                  "warnings": self._render_warnings(),
                                  "theme": self.backend.preview_theme(),
                                  "layers": self.backend.map_layer_visibility()})

    def _backend_error(self, error) -> None:
        if self._render_mode == "rmp":
            pending = getattr(self, "_rmp_pending", None)
            self._rmp_pending = None
            self._render_mode = None
            self.event("rmpProjectError", {"requestId": pending[2] if pending else None, "message": str(error)})
            return
        if self._render_mode == "metro":
            print(f"Metro render failed: {error}", file=sys.stderr)
        metadata = self._export_event_metadata()
        if metadata:
            self._export_failed(
                error, metadata["requestId"], metadata["documentEpoch"],
                metadata["context"], metadata["format"], metadata["target"])
            return
        export_id = self._export.request_id
        self._saving_export = False
        self._clear_export()
        self._render_mode = None
        self._metro_request_id = None
        self.event("error", {"message": str(error), "requestId": export_id})
        self.event("busy", {"value": bool(getattr(self.backend, "busy", False))})

    def _clear_export(self) -> None:
        self._export = ExportState()

    def _export_event_metadata(self) -> dict | None:
        if not self._export.request_id:
            return None
        return {
            "requestId": self._export.request_id,
            "documentEpoch": self._export.document_epoch,
            "context": self._export.context,
            "format": self._export.format,
            "target": self._export.requested_target,
        }

    def _export_progress(self, percent, message, request_id: str,
                         document_epoch: int, context: str,
                         output_format: str, target: str | None) -> None:
        if (request_id != self._export.request_id
                or document_epoch != self._document_epoch):
            return
        self.event("progress", {
            "percent": percent, "message": str(message),
            "requestId": request_id, "documentEpoch": document_epoch,
            "context": context, "format": output_format, "target": target,
        })

    def _export_failed(self, error, request_id: str, document_epoch: int,
                       context: str, output_format: str,
                       target: str | None) -> None:
        if request_id != self._export.request_id:
            return
        metadata = {"requestId": request_id, "documentEpoch": document_epoch,
                    "context": context, "format": output_format,
                    "target": target}
        self._saving_export = False
        self._clear_export()
        self._render_mode = None
        self._metro_request_id = None
        self.event("error", {**metadata, "message": str(error)})
        self.event("busy", {"value": bool(getattr(self.backend, "busy", False))})

    def _render_progress(self, percent, message) -> None:
        metadata = self._export_event_metadata()
        if metadata and metadata["documentEpoch"] != self._document_epoch:
            return
        self.event("progress", {"percent": percent, "message": str(message),
                                 **(metadata or {})})

    def _render_done(self, _image, _size, _stats, _elapsed, _region, _thumbnail, _context) -> None:
        if self._render_mode == "rmp":
            pending = getattr(self, "_rmp_pending", None)
            self._rmp_pending = None
            self._render_mode = None
            if pending and pending[3] == self._document_epoch:
                try:
                    self.event("rmpProjectReady", {"requestId": pending[2], "draft": self._rmp_draft(pending[0], pending[1])})
                except Exception as error:
                    self.event("rmpProjectError", {"requestId": pending[2], "message": str(error)})
            else:
                self.event("rmpProjectError", {"requestId": pending[2] if pending else None, "message": "源地图已改变，请重新打开编辑器"})
            return
        if self._render_mode == "metro":
            self._render_mode = None
            request_id = self._metro_request_id
            self._metro_request_id = None
            try:
                image = _thumbnail or _image
                path = Path(self._preview_files.name) / f"metro-{uuid.uuid4().hex}.png"
                image.save(path, format="PNG")
                self.event("metroReady", {"url": path.as_uri(), "requestId": request_id,
                                          "warnings": self._render_warnings(),
                                          "diagnostics": dict(self.backend.last_diagnostics)})
            except Exception as exc:  # noqa: BLE001 - 预览输入输出失败时保留引擎进程
                traceback.print_exc(file=sys.stderr)
                self.event("error", {"message": i18n.tr('ui.winui_engine._render_done.0cdaea5eb6', p0=exc)})
            return
        if (self._export.request_id and self._export.document_epoch is not None
                and self._export.document_epoch != self._document_epoch):
            self.backend.discard_png_result()
            metadata = self._export_event_metadata()
            self._export_failed(
                i18n.tr('ui.winui_engine._render_done.bcd13e5ccc'),
                metadata["requestId"], metadata["documentEpoch"],
                metadata["context"], metadata["format"], metadata["target"])
            return
        if self._export.target:
            target = self._export.target
            self._export.target = None
            export_id = self._export.request_id
            self._saving_export = True
            self.event("busy", {"value": True})
            try:
                started = self.backend.save_png_async(target,
                    on_done=lambda path: self._export_completed(path, export_id))
            except Exception as error:
                self._backend_error(error)
                return
            if not started:
                self._backend_error("PNG result could not be saved")

    def _export_completed(self, path, request_id):
        if request_id != self._export.request_id:
            return
        metadata = self._export_event_metadata()
        self._saving_export = False
        self._clear_export()
        if metadata and metadata["documentEpoch"] != self._document_epoch:
            self.event("error", {**metadata,
                                  "message": i18n.tr('ui.winui_engine._export_completed.bcd13e5ccc')})
            self.event("busy", {"value": bool(getattr(self.backend, "busy", False))})
            return
        self.event("exportDone", {"path": str(path), "warnings": self._render_warnings(),
                                   **(metadata or {"requestId": request_id})})
        self.event("busy", {"value": bool(getattr(self.backend, "busy", False))})

    def _render_warnings(self):
        from .services.poi.direct_mapping import document_warnings
        water = self.backend.last_diagnostics.get("stats", {}).get("transit_layout", {}).get("water", {})
        return (([i18n.tr("gui.transit.water_unverified")] if water.get("status") == "unverified" else [])
                + document_warnings(getattr(self.backend, "document", None)))

    def _export_cancelled(self, metadata):
        if metadata["requestId"] == self._export.request_id:
            self._saving_export = False
            self._clear_export()
        self.event("exportCancelled", metadata)

    def map_statistics(self) -> dict:
        """地图概况页的六项统计，按当前城市缓存。"""
        if self._stats_cache is not None:
            return self._stats_cache
        backend = self.backend
        document = backend.document
        if document is None:
            return {"available": False}
        info = backend.city_info or {}
        networks = list(getattr(document, "networks", ()) or ())
        buildings = list(getattr(document, "buildings", ()) or ())
        road = sum(_polyline_length(getattr(segment.geometry, "points", ()))
                   for segment in networks if bool(getattr(segment, "is_road", False)))
        built = sum(_polygon_area(getattr(building.footprint, "points", ()))
                    for building in buildings
                    if getattr(building, "parent_id", None) is None)
        transit = sum(float(row.get("length_m", 0.0) or 0.0)
                      for row in backend.transit_line_catalog())
        districts = getattr(document, "districts", ()) or ()
        self._stats_cache = {
            "available": True,
            "name": str(info.get("name") or ""),
            "buildings": int(info.get("buildings") or len(buildings)),
            "roadMeters": road,
            "transitMeters": transit,
            "builtArea": built,
            "districts": int(info.get("districts") or len(districts)),
            "regions": int(info.get("parks") or 0),
        }
        return self._stats_cache

    def snapshot(self) -> dict:
        """冻结当前文档和设置，形成可供命令处理的状态快照。"""
        backend = self.backend
        from .fonts import available_font_options
        style_data = backend.current_style_data()
        fields = {key: backend.field_value(key, field.default, style_data=style_data)
                  for key, field in FIELDS.items()}
        transit_options = self.transit_options()
        metro_options = self.metro_options()
        if getattr(backend, "has_document", False):
            poi_visibility = backend.poi_visibility_snapshot()
        else:
            # 城市打开前使用新版默认显示设置。
            poi_visibility = default_poi_visibility()
        visible_layers = dict(backend.layer_visibility)
        visible_layers["poi"] = any(poi_visibility["icons"].values())
        label_children = {}
        for key, (kind, target) in LABEL_CHILDREN.items():
            if kind == "field":
                label_children[key] = bool(fields[target])
            else:
                channel = "icons" if kind == "poi_icons" else "names"
                groups = ("facility", "ordinary") if target == "building" else (target,)
                label_children[key] = all(poi_visibility[channel][group] for group in groups)
        return {
            "fonts": [{"value": value, "label": label} for value, label in available_font_options()],
            "hasDocument": backend.has_document,
            "sourceGame": document_game(backend.document),
            "sourceFile": backend.source_file,
            "cityInfo": dict(backend.city_info),
            "stats": self.map_statistics(),
            "theme": backend.theme_stem,
            "themes": [{"id": stem, "name": tr(name_key)} for name_key, stem in THEME_CHOICES],
            "layers": visible_layers,
            "poiVisibility": poi_visibility,
            "documentEpoch": self._document_epoch,
            "fields": fields,
            "fieldSchema": {key: {"label": field.label, "kind": field.kind,
                                   "choices": [{"value": value, "label": field.option_label(value)}
                                               for value in field.choices],
                                   "low": field.low, "high": field.high}
                            for key, field in FIELDS.items()},
            "layerFields": {key: list(fields) for key, fields in LAYER_FIELDS.items()},
            "fieldDependencies": {key: [parents] if isinstance(parents, str) else list(parents)
                                  for key, parents in FIELD_DEPENDENCIES.items()},
            "labelChildren": label_children,
            "transitTypes": [{"key": key, "label": tr(label)} for key, label in TRANSIT_TYPES],
            "mapTransitTypes": list(backend.transit_types),
            "mapTransitLineIds": transit_options.get("line_ids"),
            "mapStations": bool(fields["transport.stations"]),
            "mapStationNames": bool(fields["transport.station_names"]),
            "transitOptions": transit_options,
            "metroOptions": metro_options,
            "lineSelection": {"transit": self.line_selection_summary(transit_options),
                              "metro": self.line_selection_summary(metro_options)},
            "metroOutline": bool((style_data.get("transports", {})
                                  .get("line", {}).get("light_outline", {})
                                  .get("enabled", True))),
            "merge": {
                "global": bool(backend.merge_enabled),
                "building": bool(backend.building_merge_enabled),
                "transit": bool(backend.transit_merge_enabled),
                "facility": bool(backend.same_facility_merge),
                "facilityMinZoom": float(backend.facility_child_min_zoom),
                "poiDistance": fields["poi.distance"],
                "blockDistance": float(backend.block_gap_m),
                "hubDistance": float(backend.hub_core_m),
                "overrides": copy.deepcopy(backend.merge_overrides),
                "facilityCategories": backend.facility_poi_catalog(),
            },
            "outputDir": backend.output_dir,
            "output": {"size": backend.size, "rotation": backend.rotation,
                       "aa": backend.aa, "rangeN": backend.range_n,
                       "rangeAnchor": backend.range_anchor},
            "recentFiles": list(backend._recent_files),
            "diagnostics": dict(backend.last_diagnostics),
            "busy": backend.busy or self._external_busy or self._saving_export,
        }

    def transit_options(self) -> dict:
        options = copy.deepcopy(self._transit_session_options or self.backend.transit_options(False))
        options.setdefault("layers", {"lines": True, **{key: bool(options.get(value, True)) for key, value in LAYER_OPTIONS.items()}})
        normalize_transit_presentation(options)
        return options

    def metro_options(self) -> dict:
        return self.backend.transit_options(True)

    def line_selection_summary(self, options) -> dict:
        catalog = self.backend.transit_line_catalog()
        types = set(options["types"])
        eligible = [line for line in catalog if line["type"] in types]
        ids = options.get("line_ids")
        selected = eligible if ids is None else [line for line in eligible if line["id"] in ids]
        return {"selected": len(selected), "total": len(eligible),
            "types": [kind for kind, _ in TRANSIT_TYPES if any(line["type"] == kind for line in selected)]}

    def theme_draft(self) -> dict:
        from csmodernmap import theme_metadata as theme_meta
        raw = self.backend.current_style_data()
        data = normalize_theme(self.validate_theme_draft(raw, raw))
        from .style import load_style
        theme_path = resource_dir("styles") / f"{self.backend.theme_stem}.yaml"
        defaults = (normalize_theme(sanitize_theme_data(load_style(str(theme_path)).data))
                    if theme_path.is_file() else copy.deepcopy(data))
        saved = self.backend._settings["values"].get("guir_style_overrides", {}).get(self.backend.theme_stem, defaults)
        fields = theme_meta.describe_theme(data)
        return {"theme": self.backend.theme_stem, "data": data,
                "defaults": defaults,
                "savedData": normalize_theme(sanitize_theme_data(saved)),
                "savePath": str(theme_path),
                "sections": ["__basic__"] + [key for key in THEME_SECTIONS if key in data]
                            + [key for key, value in data.items()
                               if key not in THEME_SECTIONS and isinstance(value, dict)],
                "fields": fields,
                "sectionNames": {key: theme_meta.section_title(key) for key in data
                                 if isinstance(data[key], dict)},
                "scenes": {"__basic__": "overall", "canvas": "overall",
                           "forest": "terrain", "district": "zones",
                           "zone": "zones", "land_use": "zones",
                           "poi_icons": "buildings",
                           "labels": "roads", "built_area": "built_area",
                           "watermark": "overall"}}

    def range_profile(self) -> dict:
        backend = self.backend
        grid = backend.range_tile_grid()
        if not backend.has_document or grid is None:
            return {"available": False}
        bounds = backend.world_rect()
        limit = grid.limit(bounds)
        outside = backend.has_outside_terrain()
        city_bounds = document_city_bounds(backend.document)
        selection = backend.tile_range_selection()
        resolved = self.resolved_range_bounds()
        previews = {}
        for name, area in (("city", city_bounds), ("world", bounds)):
            if name == "world" and area == city_bounds:
                previews[name] = previews["city"]
                continue
            path = Path(self._preview_files.name) / f"range-{uuid.uuid4().hex}.png"
            build_map_overview(backend.document, area,
                               backend.current_style_data()).save(path)
            previews[name] = path.as_uri()
        return {"available": True, "game": grid.game, "grid": grid.grid,
                "tileMeters": grid.tile_m, "limit": limit,
                "presets": [n for n in grid.presets if n <= limit],
                "hasOutside": outside, "worldBounds": bounds,
                "cityBounds": city_bounds, "selection": selection,
                "resolvedBounds": resolved, "legacy": selection is None and backend.range_n > 1,
                "previews": previews}

    def resolved_range_bounds(self) -> tuple:
        backend = self.backend
        bounds = backend.world_rect()
        resolved, _, policy = backend._resolve_range()
        if resolved is None and policy:
            view = SimpleNamespace(
                terrain=SimpleNamespace(width=bounds[2] - bounds[0],
                    height=bounds[3] - bounds[1], cell=1,
                    origin_x=bounds[0], origin_z=bounds[1]),
                buildings=[SimpleNamespace(points=b.footprint.points)
                           for b in getattr(backend.document, "buildings", ())])
            resolved = most_prosperous_window_by_size(view, policy["side_m"])
        return resolved or bounds

    def range_preset(self, tiles: int, anchor: str) -> tuple:
        backend = self.backend
        grid = backend.range_tile_grid()
        bounds = backend.world_rect()
        if grid is None or not 1 <= tiles <= grid.limit(bounds):
            raise ValueError("invalid range tile count")
        side = tiles * grid.tile_m
        if anchor == "center":
            return square_region(bounds, side)
        if anchor != "busiest":
            raise ValueError("invalid range anchor")
        view = SimpleNamespace(
            terrain=SimpleNamespace(width=bounds[2] - bounds[0],
                height=bounds[3] - bounds[1], cell=1,
                origin_x=bounds[0], origin_z=bounds[1]),
            buildings=[SimpleNamespace(points=b.footprint.points)
                       for b in getattr(backend.document, "buildings", ())])
        return most_prosperous_window_by_size(view, side)

    @staticmethod
    def validate_theme_draft(data: Any, baseline: dict | None = None) -> dict:
        data = sanitize_theme_data(data)
        from .theme_metadata import describe_theme, validate_theme_fields
        fields = describe_theme(data)

        def walk(value: Any, path: tuple[str, ...]) -> None:
            if path[:2] == ("terrain", "ground"):
                return
            if isinstance(value, dict):
                for key, child in value.items():
                    walk(child, path + (str(key),))
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    walk(child, path + (str(index),))
            elif (isinstance(value, str)
                  and (path in COLOR_PATHS or path == ("canvas", "background") or fields[".".join(path)]["kind"] == "color")
                  and not HEX_COLOR.fullmatch(value)):
                raise ValueError("invalid color: " + ".".join(path))

        walk(data, ())
        validate_theme_values(data)
        validate_theme_fields(data, baseline)
        return data

    def queue_theme_preview(self, params: dict) -> dict:
        if "documentEpoch" in params:
            self._require_document_epoch(params)
        request_id = str(params["requestId"])
        data = copy.deepcopy(self.validate_theme_draft(params["data"], self.backend.current_style_data()))
        scene = str(params.get("scene") or "overall")
        size = max(256, min(int(params.get("size", 640)), 1024))
        session = self.backend.session
        if session is None:
            return {"started": False, "message": i18n.tr('ui.winui_engine.queue_theme_preview.ae3f8c8a07')}
        with self._theme_preview_lock:
            self._theme_preview_request_id = request_id
            self._theme_preview_pending = (request_id, data, scene, size, session)
            if self._theme_preview_busy:
                return {"started": True}
            self._theme_preview_busy = True
        threading.Thread(target=self._render_theme_previews,
                         name="winui-theme-preview", daemon=True).start()
        return {"started": True}

    def theme_text_sample(self, params: dict) -> dict:
        from PIL import Image
        from .layers.label_layer import LabelLayer, _resolve_size
        from .fonts import _candidate_paths, font_covers, resolve_text_font
        data = self.validate_theme_draft(params["data"], self.backend.current_style_data())
        kind = str(params["type"])
        if kind not in {"road", "district", "building", "park", "stop"}:
            raise ValueError("unknown label type")
        cfg = data["labels"][kind]
        text = str(params.get("text", i18n.tr('ui.winui_engine.theme_text_sample.c1fff780e0')))
        if not 1 <= len(text) <= 120:
            raise ValueError("sample text must contain 1 to 120 characters")
        scale = float(params.get("renderScale", 1))
        if not math.isfinite(scale) or not 0 < scale <= 8:
            raise ValueError("invalid sample render scale")
        sizes = {"road": 16, "district": 20, "park": 12, "building": 16, "stop": 16}
        colors = {"road": "#505564", "district": "#32415A", "park": "#328246",
                  "building": "#505564", "stop": "#505564"}
        size = _resolve_size(cfg.get("size", sizes[kind]), scale)
        if kind == "district":
            size = max(size, 12 * min(scale, 1))
        stroke = _resolve_size(cfg.get("stroke_width", 1.5 if kind == "park" else 2), scale) if kind in {"road", "district", "park"} else 0
        if not 0 <= size <= 1600 or not 0 <= stroke <= 100:
            raise ValueError("sample size or stroke is too large")
        painter = LabelLayer()
        font_name = cfg.get("font")
        bold = kind == "district" and bool(cfg.get("bold", False))
        font = painter._get_font(size, font_name, text=text, bold=bold)
        bbox = font.getbbox(text, stroke_width=round(stroke))
        width, height = bbox[2] - bbox[0] + 24, bbox[3] - bbox[1] + 24
        image = Image.new("RGBA", (max(width, 1), max(height, 1)), (0, 0, 0, 0))
        painter.ctx = SimpleNamespace(image=image)
        painter._render_text((width / 2, height / 2), text, size,
                             cfg.get("color", colors[kind]), stroke,
                             cfg.get("stroke", "#FFFFFF"), font_family=font_name, bold=bold)
        path = Path(self._preview_files.name) / f"text-{uuid.uuid4().hex}.png"
        image.save(path)
        resolved = resolve_text_font(font_name, text=text, bold=bold)
        requested = _candidate_paths(font_name, bold=bold) if font_name else []
        substituted = bool(font_name) and (resolved not in requested or not font_covers(resolved, text))
        return {"url": path.as_uri(), "fontSubstituted": substituted,
                "requestedFont": font_name, "resolvedFont": resolved.stem if resolved else "",
                "size": size, "strokeWidth": stroke}

    def _render_theme_previews(self) -> None:
        while True:
            with self._theme_preview_lock:
                pending = self._theme_preview_pending
                self._theme_preview_pending = None
                if pending is None:
                    self._theme_preview_busy = False
                    return
            request_id, data, scene, size, session = pending
            if session is not self.backend.session:
                continue
            try:
                image = self.backend.style_preview_render(
                    data, size, scene)
                with self._theme_preview_lock:
                    current = (request_id == self._theme_preview_request_id
                               and session is self.backend.session)
                if current:
                    path = Path(self._preview_files.name) / f"theme-{uuid.uuid4().hex}.png"
                    image.save(path, format="PNG")
                    self.event("themePreviewReady", {"requestId": request_id,
                                                     "url": path.as_uri()})
            except Exception as exc:  # noqa: BLE001 - 编辑器预览失败可恢复
                traceback.print_exc(file=sys.stderr)
                with self._theme_preview_lock:
                    current = request_id == self._theme_preview_request_id
                if current:
                    self.event("themePreviewFailed", {"requestId": request_id,
                                                      "message": str(exc)})

    def _current_poi_visibility(self) -> dict:
        if getattr(self.backend, "has_document", False):
            return self.backend.poi_visibility_snapshot()
        return default_poi_visibility()

    @staticmethod
    def _leaf_profile_keys(profile: dict) -> set[str]:
        return EngineServer._profile_paths(profile)

    def _reset_profile_fields(self, params: dict) -> dict:
        self._require_document_epoch(params)
        kind = str(params.get("kind", ""))
        target = str(params.get("target", ""))
        keys = params.get("keys")
        if not isinstance(keys, list) or any(not isinstance(key, str) for key in keys):
            raise ValueError("profile reset keys must be an array of field paths")
        if kind == "poi":
            target = canonical_profile_target(target)
            current = self.backend.get_poi_profile(target)
            defaults = default_poi_profile(target)
            setter = self.backend.set_poi_profile
        elif kind == "output":
            if target not in OUTPUT_PROFILE_TARGETS:
                raise ValueError(f"unknown output profile target: {target!r}")
            current = self.backend.get_output_profile(target)
            defaults = default_output_profile(target)
            setter = self.backend.set_output_profile
        else:
            raise ValueError("profile reset kind must be 'poi' or 'output'")
        allowed = self._leaf_profile_keys(defaults)
        if not set(keys) <= allowed:
            raise ValueError("reset keys include fields outside the visible profile")
        updated = copy.deepcopy(current)
        for key in dict.fromkeys(keys):
            self._set_profile_path(updated, key, self._profile_value(defaults, key))
        normalized = setter(target, updated)
        return {"kind": kind, "target": target, "profile": normalized}

    @staticmethod
    def _profile_value(profile: dict, path: str):
        value = profile
        for part in path.split("."):
            if not isinstance(value, dict) or part not in value:
                raise ValueError(f"unknown profile field: {path}")
            value = value[part]
        return copy.deepcopy(value)

    def _export_profile(self, params: dict) -> dict:
        context = ContextMode(params.get("context", "normal"))
        capabilities = self._format_capabilities(context)
        shared_fingerprint = hashlib.sha256(json.dumps(
            self.backend.current_style_data(), sort_keys=True, ensure_ascii=False
        ).encode("utf-8")).hexdigest() if self.backend.has_document else ""
        if context == ContextMode.METRO:
            selected = self.metro_options()
            visible_layers = dict(selected.get("layers") or {
                "lines": True,
                **{key: bool(selected.get(option, True))
                   for key, option in LAYER_OPTIONS.items()},
            })
            return {
                **export_capabilities(context),
                "formats": list(capabilities),
                "formatCapabilities": capabilities,
                "context": context.value,
                "cityName": self.backend.city_info.get("name", "map"),
                "documentEpoch": self._document_epoch,
                "sharedFingerprint": shared_fingerprint,
                "size": selected.get("size", 0),
                "rotation": self.backend.rotation, "aa": self.backend.aa,
                "rangeN": self.backend.range_n,
                "rangeAnchor": self.backend.range_anchor,
                "outputDir": self.backend.output_dir,
                "theme": self.backend.theme_stem,
                "themeName": next((tr(key) for key, stem in THEME_CHOICES if stem == self.backend.theme_stem), self.backend.theme_stem),
                "bounds": list(self.backend.world_rect()),
                "layers": visible_layers, "types": selected.get("types", []),
                "metroOptions": copy.deepcopy(selected),
                "lineIds": selected.get("line_ids"),
                "lineCount": len(self.backend.transit_line_catalog())
                             if self.backend.has_document else 0,
            }

        if context == ContextMode.TRANSIT:
            output_target = "transit_png"
            selected = self.transit_options()
            visible_layers = dict(selected.get("layers") or {
                "lines": True,
                **{key: bool(selected.get(option, True))
                   for key, option in LAYER_OPTIONS.items()},
            })
        else:
            output_target = "normal_png"
            selected = {}
            visible_layers = self.backend.map_layer_visibility()

        requested_target = params.get("profileTarget", output_target)
        if requested_target == "preview":
            poi_target = "preview"
        else:
            poi_target = canonical_profile_target(str(requested_target))
        poi_profile = (self.backend.get_poi_profile(poi_target)
                       if self.backend.has_document else default_poi_profile(poi_target))
        poi_visibility = self._current_poi_visibility()
        visible_layers["poi"] = any(poi_visibility["icons"].values())
        output = (self.backend.get_output_profile(output_target)
                  if self.backend.has_document else default_output_profile(output_target))
        bounds = list(document_city_bounds(self.backend.document))
        line_ids = (selected if context == ContextMode.TRANSIT else self.transit_options()).get("line_ids")
        line_count = (len(self.backend.transit_line_catalog())
                      if context == ContextMode.TRANSIT and self.backend.has_document else 0)
        output_snapshot = {
            "format": "PNG", **output, "bounds": bounds,
            "rangeSource": "city", "theme": self.backend.theme_stem,
            "layers": visible_layers, "lineIds": line_ids,
            "lineCount": line_count,
        }
        if context == ContextMode.TRANSIT:
            output_snapshot["transitOptions"] = copy.deepcopy(selected)
        return {
            **export_capabilities(context),
            "formats": list(capabilities),
            "formatCapabilities": capabilities,
            "context": context.value,
            "cityName": self.backend.city_info.get("name", "map"),
            "documentEpoch": self._document_epoch,
            "profileTarget": poi_target,
            "sharedFingerprint": shared_fingerprint,
            "output": output_snapshot,
            "themeName": next((tr(key) for key, stem in THEME_CHOICES if stem == self.backend.theme_stem), self.backend.theme_stem),
            "poiProfile": poi_profile,
            "poiVisibility": poi_visibility,
            "size": output["size"], "rotation": output["rotation"],
            "aa": output["aa"], "rangeN": self.backend.range_n,
            "rangeAnchor": self.backend.range_anchor,
            "outputDir": self.backend.output_dir,
            "theme": self.backend.theme_stem,
            "bounds": bounds, "layers": visible_layers,
            "types": selected.get("types", []), "lineIds": line_ids,
            "lineCount": line_count,
        }

    @staticmethod
    def _format_capabilities(context: ContextMode) -> dict[str, dict]:
        context_name = context.value
        if context == ContextMode.METRO:
            formats = ("PNG", "RMG", "RMP")
        elif context == ContextMode.TRANSIT:
            formats = ("PNG",)
        else:
            formats = ("PNG", "HTML")
        result = {}
        for output_format in formats:
            if output_format == "PNG":
                transit = context == ContextMode.TRANSIT
                metro = context == ContextMode.METRO
                target = ("transit_png" if transit else "normal_png")
                result[output_format] = {
                    "targetKind": "file", "context": context_name,
                    "outputProfileTarget": None if metro else target,
                    "poiProfileTarget": None if metro else target,
                    "outputFields": ["size"] if metro else ["size", "rotation"],
                    "showsOutputOptions": True,
                    "showsRange": not metro, "showsPoiProfile": not metro,
                    "showsSharedLayers": not metro,
                    "showsTransitLines": transit or metro,
                    "resultImage": True,
                }
            elif output_format == "HTML":
                result[output_format] = {
                    "targetKind": "file", "context": context_name,
                    "outputProfileTarget": "normal_png",
                    "poiProfileTarget": None,
                    "outputFields": ["rotation"],
                    "showsOutputOptions": True, "showsRange": True,
                    "showsPoiProfile": False, "showsSharedLayers": True,
                    "showsTransitLines": False, "resultImage": False,
                }
            else:  # RMG 和 RMP 属于地铁视图工程包。
                result[output_format] = {
                    "targetKind": "directory", "context": context_name,
                    "outputProfileTarget": None, "poiProfileTarget": None,
                    "outputFields": [],
                    "showsOutputOptions": False, "showsRange": False,
                    "showsPoiProfile": False, "showsSharedLayers": False,
                    "showsTransitLines": True, "resultImage": False,
                }
        return result

    def _normalize_output_region(self, raw_region) -> RenderRegion | None:
        if raw_region is None:
            return None
        region = RenderRegion.from_dict(raw_region)
        aliases = {"map": "city", "full_city": "city"}
        source = aliases.get(region.source, region.source)
        if source not in {"city", "selection", "random_spot", "custom"}:
            raise ValueError(f"unknown export region source: {source!r}")
        bounds = normalize_bounds(region.bounds, self.backend.world_rect())
        return RenderRegion(bounds, source, region.output_size)

    def _capture_output_snapshot(self, context: ContextMode, output_format: str,
                                 params: dict) -> tuple[dict, dict, dict]:
        """缩略图与正式地图导出共用同一校验和快照路径。"""
        backend = self.backend
        capability = self._format_capabilities(context)[output_format]
        region = self._normalize_output_region(params.get("region")) if capability["showsRange"] else None
        theme_data = sanitize_theme_data(backend.current_style_data())
        visibility = backend.poi_visibility_snapshot()
        layers = dict(backend.layer_visibility)
        layers["poi"] = any(visibility["icons"].values())
        if context in {ContextMode.NORMAL, ContextMode.SELECTION, ContextMode.RANDOM_SPOT}:
            layers = backend.map_layer_visibility()
        if context == ContextMode.METRO:
            options = copy.deepcopy(self.metro_options())
            raw_size = (params.get("output") or {}).get("size", options.get("size", 0))
            if type(raw_size) is not int or not 0 <= raw_size <= 32768:
                raise ValueError(i18n.tr('ui.winui_engine._capture_output_snapshot.9e411d9c27'))
            options["size"] = raw_size
            job = backend.capture_png_export_job(context, metro_options=options)
        else:
            target = "transit_png" if context == ContextMode.TRANSIT else "normal_png"
            raw_output = params.get("output") or {}
            if not isinstance(raw_output, dict):
                raise ValueError("output snapshot must be an object")
            if params.get("profileTarget", target) != target:
                raise ValueError("export profile target does not match the selected context")
            output = validate_output_profile({**backend.get_output_profile(target),
                **{key: raw_output[key] for key in capability["outputFields"] if key in raw_output}}, target)
            poi_target = "preview" if output_format == "HTML" else target
            poi = backend.get_poi_profile(poi_target)
            job = backend.capture_png_export_job(context, region,
                transit_options=self.transit_options() if context == ContextMode.TRANSIT else None,
                output_profile=output, poi_profile=poi,
                poi_visibility_snapshot=visibility, poi_profile_target=poi_target,
                full_city_default=region is None)
            job["output_profile"] = copy.deepcopy(output)
            job["poi_profile"] = copy.deepcopy(poi)
            job["poi_visibility_snapshot"] = copy.deepcopy(visibility)
            job["profile_target"] = target
            job["poi_profile_target"] = poi_target
            if context != ContextMode.TRANSIT:
                job["ui"]["transit_line_ids"] = copy.deepcopy(self.transit_options().get("line_ids"))
        job["style"] = backend.theme_stem
        job["ui"].setdefault("style_overrides", {})[backend.theme_stem] = copy.deepcopy(theme_data)
        # capture_png_export_job 已解析共享可见性和配置规则。
        job["document_epoch"] = self._document_epoch
        return job, theme_data, layers

    def _start_external_project_export(self, request: ExportRequest,
                                       params: dict, metadata: dict) -> bool:
        from .external_edit import (ExternalExportRequest, ExportSnapshot,
                                    export_rmg, export_rmp)

        backend = self.backend
        kind = request.format.casefold()
        if kind not in {"rmg", "rmp"}:
            raise ValueError(f"unknown external export format: {request.format}")
        if request.context != ContextMode.METRO:
            raise ValueError(f"{request.format} export requires metro context")
        output_root = request.target or request.output_dir
        if not output_root:
            raise ValueError(i18n.tr('ui.winui_engine._start_external_project_export.fd1e2dca12'))
        if not backend.has_document or backend.session is None:
            raise RuntimeError(i18n.tr('ui.winui_engine._start_external_project_export.ae3f8c8a07'))
        target_dir = Path(output_root)
        options = self.metro_options()
        raw_ids = options.get("line_ids")
        external_request = ExternalExportRequest(
            output_root=target_dir,
            theme_id=backend.theme_stem,
            theme=sanitize_theme_data(backend.current_style_data()),
            visibility={},
            render_job=backend.capture_png_export_job("metro", metro_options=options) if kind == "rmp" else None,
            line_ids=None if raw_ids is None else tuple(raw_ids),
        )
        session = backend.session
        # 接收请求时冻结游戏和会话引用，避免后台写出器观察到后续文档切换。
        export_snapshot = ExportSnapshot.capture(session)
        document_epoch = metadata["documentEpoch"]
        request_id = metadata["requestId"]
        context = metadata["context"]
        output_format = metadata["format"]
        target = metadata["target"]
        self._external_busy = True
        self.event("busy", {"value": True})

        def worker() -> None:
            try:
                self._export_progress(10, tr("gui.external.preparing"),
                    request_id, document_epoch, context, output_format, target)
                self._export_progress(35, tr("gui.external.writing"),
                    request_id, document_epoch, context, output_format, target)
                exporter = {"rmg": export_rmg, "rmp": export_rmp}[kind]
                result = exporter(export_snapshot, external_request)
                if request_id != self._export.request_id:
                    return
                if document_epoch != self._document_epoch:
                    self._export_failed(i18n.tr('ui.winui_engine.worker.bcd13e5ccc'),
                        request_id, document_epoch, context, output_format, target)
                    return
                self.event("externalExportDone", {
                    **metadata, "directory": str(result.directory),
                    "warnings": list(result.warnings), "kind": kind,
                })
                self._clear_export()
            except Exception as exc:  # noqa: BLE001 - 工程导出器会报告可恢复失败
                traceback.print_exc(file=sys.stderr)
                self._export_failed(exc, request_id, document_epoch,
                                    context, output_format, target)
            finally:
                self._external_busy = False
                self.event("busy", {"value": bool(getattr(backend, "busy", False))})
        threading.Thread(target=worker, name=f"external-{kind}", daemon=True).start()
        return True

    def _rmp_context(self):
        import hashlib
        from .external_edit.rmp_graph import RMP_GRAPH_VERSION
        from .view_settings import DEFAULT_VIEW_SETTINGS

        saved = self.metro_options()
        defaults = DEFAULT_VIEW_SETTINGS["metro"]
        # 旧展示界面停用后只保留生成输入。
        options = {key: copy.deepcopy(saved[key]) for key in (
            "types", "line_ids", "merge_enabled", "merge_range",
            "spacing", "schematic_water", "water_smoothing", "facilities_enabled", "metro_edits",
        ) if key in saved}
        options.update(pure=True, abstract=True, stations=True, station_names_enabled=True,
                       station_names_mode="all", station_name_style="all", background="#FFFFFF",
                       size=0, title_enabled=False, legend_enabled=False,
                       station_diameter=defaults["stations"]["diameter"],
                       station_name_size=defaults["station_names"]["size"])
        options["schematic_options"] = {
            key: saved.get("schematic_options", {}).get(key, defaults["diagram"][key])
            for key in ("adaptive_scale", "adaptive_strength")}
        options.update({"ann_" + key: False for key in defaults["annotations"]})
        job = self.backend.capture_png_export_job("metro", metro_options=options)
        # 纯地铁渲染使用内置主题，不读取地图样式覆盖。
        job["ui"].pop("style_overrides", None)
        job["opts"]["capture_rmp"] = True
        inputs = {"rmpGraphVersion": RMP_GRAPH_VERSION, "job": job}
        key = hashlib.sha256(json.dumps(inputs, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return key, job

    def _rmp_draft(self, key, job):
        import hashlib
        from ._paths import writable_dir
        from .external_edit.rmp_graph import build_editor_draft, RMP_COMMIT, RMP_GRAPH_VERSION
        from . import __version__
        scene = self.backend.last_rmp_snapshot
        if scene is None:
            raise ValueError("地铁成图没有可编辑矢量快照")
        project = build_editor_draft(scene)
        source = Path(job["file"]).resolve()
        with source.open("rb") as stream:
            source_hash = hashlib.file_digest(stream, "sha256").hexdigest()
        directory = writable_dir() / "rmp_projects" / source_hash
        return {"projectId": key, "sourceFile": str(source), "defaultPath": str(directory / (key + ".rmp.json")),
                "project": project, "metadata": {
                    "cityName": self.backend.city_info.get("name", "map"),
                    "sourceFingerprint": source_hash, "settingsFingerprint": key,
                    "rmpCommit": RMP_COMMIT, "sourceFile": str(source),
                    "generatorVersion": f"{__version__}/rmp-{RMP_GRAPH_VERSION}",
                    "layoutFingerprint": hashlib.sha256(json.dumps(project["cmmDraft"], sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                }}

    def dispatch(self, method: str, params: dict) -> Any:
        """校验并分派 WinUI 协议命令，统一返回结果或边界错误。"""
        backend = self.backend
        if (self._external_busy or self._saving_export) and method in {
            "openDocument", "setTheme", "setLayerVisibility", "setField", "resetLayer",
            "setLabelChild", "setTransitOption", "setTransitType", "setTransitLines",
            "setMetroOutline", "renderMetro",
            "commitThemeDraft", "setMergeOverride", "setFacilitySelection",
            "savePoiMapping", "renderInterchanges", "setRegionSelection",
            "setOutputOption", "setTextIconSettings", "setPoiDensity", "setPoiVisibility", "resetFields",
            "resetProfileFields", "setRangeSelection", "compilePreview", "refreshPreview",
            "export", "clearCache", "setInspectorField", "resetInspectorGroup", "undoInspector", "redoInspector",
        }:
            raise RuntimeError(i18n.tr('ui.winui_engine.dispatch.dfeec4838c'))
        if method == "hello":
            if params.get("protocolVersion") != PROTOCOL_VERSION:
                raise ValueError("incompatible protocol version")
            return {"protocolVersion": PROTOCOL_VERSION, "viewerUrl": backend.start_preview_server()}
        if method in WORKFLOW_METHODS:
            if method == "setRegionSelection" and "documentEpoch" in params:
                self._require_document_epoch(params)
            return self.workflows.dispatch(method, params)
        if method == "getSnapshot":
            return self.snapshot()
        if method in {"getObjectInspector", "setInspectorField", "resetInspectorGroup", "undoInspector", "redoInspector"}:
            from .object_inspector import describe, apply, capture, restore
            self._require_document_epoch(params)
            if not backend.has_document:
                raise RuntimeError(i18n.tr('ui.winui_engine.dispatch.ae3f8c8a07'))
            frame = params.get("object")
            if not isinstance(frame, dict):
                raise ValueError("Object selection is required")
            context = str(params.get("context", "map"))
            options = self.transit_options() if context == "transit" else {}
            if getattr(self, "_inspector_history_epoch", None) != self._document_epoch:
                self._inspector_history_epoch = self._document_epoch
                self._inspector_undo, self._inspector_redo = [], []
            model, _ = describe(backend, frame, context, options)
            if method != "getObjectInspector":
                if params.get("settingsRevision") != model["settingsRevision"]:
                    raise RuntimeError(i18n.tr('ui.winui_engine.dispatch.80bd243ed5'))
                if method in {"undoInspector", "redoInspector"}:
                    source = self._inspector_undo if method == "undoInspector" else self._inspector_redo
                    destination = self._inspector_redo if method == "undoInspector" else self._inspector_undo
                    if not source:
                        raise ValueError(i18n.tr('ui.winui_engine.dispatch.5f67276ac1'))
                    record = source[-1]
                    record_options = self.transit_options() if record["context"] == "transit" else {}
                    current, _ = describe(backend, record["object"], record["context"], record_options)
                    if current["settingsRevision"] != record["revision"]:
                        raise RuntimeError(i18n.tr('ui.winui_engine.dispatch.8d612deac2'))
                    updated_options = restore(backend, record["before"] if method == "undoInspector" else record["after"], record_options)
                    if record["context"] == "transit":
                        self._transit_session_options = updated_options
                    current, _ = describe(backend, record["object"], record["context"], updated_options)
                    source.pop()
                    record["revision"] = current["settingsRevision"]
                    destination.append(record)
                    for stack in (source, destination):
                        if stack:
                            top = stack[-1]
                            top_options = self.transit_options() if top["context"] == "transit" else {}
                            top_model, _ = describe(backend, top["object"], top["context"], top_options)
                            top["revision"] = top_model["settingsRevision"]
                else:
                    if self._inspector_undo:
                        top = self._inspector_undo[-1]
                        top_options = self.transit_options() if top["context"] == "transit" else {}
                        top_model, _ = describe(backend, top["object"], top["context"], top_options)
                        if top["revision"] != top_model["settingsRevision"]:
                            # 新编辑不能使旧记录覆盖其间发生的外部修改。
                            self._inspector_undo.clear()
                            self._inspector_redo.clear()
                    group = params.get("group") if method == "resetInspectorGroup" else None
                    before = capture(backend, frame, context, options, params.get("key"), group)
                    previous_revision = model["settingsRevision"]
                    apply(backend, frame, context, options,
                          key=params.get("key"), value=params.get("value"), reset_group=group)
                    if context == "transit" and str(params.get("key", "")).startswith("view."):
                        self._transit_session_options = {**options, str(params["key"])[5:]: params["value"]}
                    options = self.transit_options() if context == "transit" else {}
                    after = capture(backend, frame, context, options, targets=before)
                    current, _ = describe(backend, frame, context, options)
                    if current["settingsRevision"] != previous_revision:
                        self._inspector_undo.append({"object": copy.deepcopy(frame), "context": context,
                            "before": before, "after": after, "revision": current["settingsRevision"]})
                        self._inspector_undo = self._inspector_undo[-50:]
                        self._inspector_redo.clear()
                options = self.transit_options() if context == "transit" else {}
                model, _ = describe(backend, frame, context, options)
            from .fonts import available_font_options
            model["fonts"] = [{"value": v, "label": label} for v, label in available_font_options()]
            model["documentEpoch"] = self._document_epoch
            model["canUndo"] = bool(self._inspector_undo)
            model["canRedo"] = bool(self._inspector_redo)
            return model
        if method == "getRangeProfile":
            return self.range_profile()
        if method == "getRangePreset":
            return {"bounds": self.range_preset(int(params["tiles"]), str(params["anchor"]))}
        if method == "setRangeSelection":
            bounds = params.get("bounds")
            self.backend.set_tile_range(int(params["tiles"]), str(params["anchor"]),
                                        bounds=normalize_bounds(bounds, self.backend.world_rect())
                                        if bounds is not None else None)
            return self.range_profile()
        if method == "getPoiEditor":
            editor = PoiEditorBridge(backend, Path(self._preview_files.name))
            result = editor.read()
            self._poi_editor_token = uuid.uuid4().hex
            self._poi_editor_mtime = (editor.mapping_path.stat().st_mtime_ns,
                editor.override_path.stat().st_mtime_ns if editor.override_path.is_file() else None)
            return {**result, "token": self._poi_editor_token}
        if method in {"validatePoiMapping", "savePoiMapping"}:
            if not self._poi_editor_token or params.get("token") != self._poi_editor_token:
                raise RuntimeError(i18n.tr('ui.winui_engine.dispatch.b3dbf7b21a'))
            editor = PoiEditorBridge(backend, Path(self._preview_files.name))
            current_mtime = (editor.mapping_path.stat().st_mtime_ns,
                editor.override_path.stat().st_mtime_ns if editor.override_path.is_file() else None)
            if current_mtime != self._poi_editor_mtime:
                raise RuntimeError(i18n.tr('ui.winui_engine.dispatch.6f6dff2c57'))
            changes = params.get("changes")
            if method == "validatePoiMapping":
                return {"issues": editor.validate(changes)}
            count = editor.save(changes)
            self._poi_editor_token = None
            self._poi_editor_mtime = None
            return {"saved": count}
        if method == "getThemeDraft":
            self.backend.reset_style_preview_regions()
            revision = hashlib.sha256(json.dumps(backend.current_style_data(), sort_keys=True, default=str).encode()).hexdigest()
            return {**self.theme_draft(), "documentEpoch": self._document_epoch, "settingsRevision": revision}
        if method == "describeThemeDraft":
            from .theme_metadata import describe_theme
            data = normalize_theme(self.validate_theme_draft(params["data"], backend.current_style_data()))
            return {"data": data, "fields": describe_theme(data)}
        if method == "renderThemeTextSample":
            self._require_document_epoch(params)
            return self.theme_text_sample(params)
        if method == "renderThemePreview":
            return self.queue_theme_preview(params)
        if method == "cancelThemePreview":
            with self._theme_preview_lock:
                self._theme_preview_request_id = None
                self._theme_preview_pending = None
            return {"cancelled": True}
        if method == "commitThemeDraft":
            self._require_document_epoch(params)
            revision = hashlib.sha256(json.dumps(backend.current_style_data(), sort_keys=True, default=str).encode()).hexdigest()
            if params.get("settingsRevision") is not None and params["settingsRevision"] != revision:
                raise RuntimeError(i18n.tr('ui.winui_engine.dispatch.69d8708938'))
            if str(params["theme"]) != backend.theme_stem:
                raise RuntimeError(i18n.tr('ui.winui_engine.dispatch.43f245906a'))
            data = self.validate_theme_draft(params["data"], backend.current_style_data())
            save_path = params.get("savePath")
            if save_path is not None:
                save_path = str(save_path)
                if Path(save_path).suffix.lower() not in {".yaml", ".yml"}:
                    raise ValueError(i18n.tr('ui.winui_engine.dispatch.73db60d7fc'))
            persist = params.get("persist", False)
            if not isinstance(persist, bool):
                raise ValueError("persist must be a boolean")
            options = {"persist": True} if persist else {}
            if not backend.on_style_applied(data, save_path=save_path, **options):
                raise RuntimeError(i18n.tr('ui.winui_engine.dispatch.837a5ba76c'))
            revision = hashlib.sha256(json.dumps(backend.current_style_data(), sort_keys=True, default=str).encode()).hexdigest()
            return {"applied": True, "saved": persist or save_path is not None,
                    "theme": backend.theme_stem, "settingsRevision": revision}
        if method == "getPreviewTheme":
            return backend.preview_theme()
        if method == "clearCache":
            backend.clear_cache()
            return {"cleared": True}
        if method == "openDocument":
            pending = getattr(self, "_rmp_pending", None)
            if self._render_mode == "rmp" and pending:
                self.event("rmpProjectError", {"requestId": pending[2], "message": "源地图已改变，请重新打开编辑器"})
                self._rmp_pending = None
                self._render_mode = None
            self._document_epoch += 1
            self._stats_cache = None
            self._transit_session_options = None
            self._poi_editor_token = None
            self._poi_editor_mtime = None
            with self._theme_preview_lock:
                self._theme_preview_request_id = None
                self._theme_preview_pending = None
            return {"started": backend.open_file(str(params["path"]), self.callbacks)}
        if method == "getTransitLines":
            metro = bool(params.get("metro", False))
            return {"lines": backend.transit_line_catalog(), "documentEpoch": self._document_epoch,
                    "lineIds": (self.metro_options() if metro
                                else self.transit_options()).get("line_ids")}
        if method == "pickRandomRegion":
            bounds = backend.pick_random_preview_region(**({"scene": params.get("scene", "overall"), "seed": params.get("seed")} if params else {}))
            return {"bounds": None if bounds is None else list(bounds)}
        if method == "setTransitLines":
            if "documentEpoch" in params:
                self._require_document_epoch(params)
            metro = bool(params.get("metro", False))
            known = {int(line["id"]) for line in backend.transit_line_catalog()}
            raw_ids = params.get("lineIds")
            if raw_ids is not None and not isinstance(raw_ids, list):
                raise ValueError("lineIds must be an array or null")
            ids = None if raw_ids is None else sorted({int(value) for value in raw_ids})
            if metro and ids == []:
                raise ValueError(i18n.tr('ui.winui_engine.dispatch.9c2202a4e4'))
            if ids is not None and not set(ids) <= known:
                raise ValueError("unknown transit line id")
            options = self.metro_options() if metro else self.transit_options()
            if ids is None:
                if metro:
                    options["line_ids"] = None
                    options["station_line_ids"] = None
                else:
                    options.pop("line_ids", None)
                    options.pop("station_line_ids", None)
            else:
                options["line_ids"] = ids
                options["station_line_ids"] = list(ids)
            if not metro:
                self._transit_session_options = options
            backend.update_transit_options(metro, options)
            return {"lineIds": ids}
        if method == "setTheme":
            changed = backend.set_theme(str(params["theme"]))
            if changed and backend.has_document:
                backend.compile_preview_scene(self.callbacks)
            return {"changed": changed, "snapshot": self.snapshot()}
        if method == "getTextIconSettings":
            target = canonical_profile_target(str(params["target"]))
            from .view_settings import default_view_settings
            defaults = default_view_settings()["metro" if target == "metro" else "transit"]
            option_defaults = {"stations": defaults["stations"]["enabled"],
                "station_names_enabled": defaults["station_names"]["enabled"],
                "facilities_enabled": defaults["facilities"]["enabled"]}
            if target == "metro":
                names = defaults["station_names"]
                option_defaults.update(station_name_size=names["size"], station_font=names["font"],
                    station_name_color=names["color"], station_name_stroke=names["stroke_width"],
                    station_name_stroke_color=names["stroke_color"], station_diameter=defaults["stations"]["diameter"],
                    merge_enabled=defaults["stations"]["merge"], merge_range=defaults["stations"]["merge_range"])
            else:
                option_defaults["facility_names_enabled"] = defaults["facilities"]["names_enabled"]
                option_defaults["station_name_placement"] = station_name_placement(option_defaults)
                option_defaults["station_name_size"] = defaults["station_names"]["size"]
            return {"theme": backend.theme_stem, "enabled": bool(backend.layer_visibility["label"]),
                "fields": {key: backend.field_value(key, FIELDS[key].default) for key in TEXT_ICON_FIELDS},
                "defaults": {key: backend.theme_default_field_value(key, FIELDS[key].default) for key in TEXT_ICON_FIELDS},
                "profile": backend.get_poi_profile(target), "profileDefaults": default_poi_profile(target),
                "visibility": backend.poi_visibility_snapshot(),
                "visibilityDefaults": default_poi_visibility(),
                "optionDefaults": option_defaults,
                "optionSchema": option_schema(self.metro_options() if target == "metro" else self.transit_options()),
                "options": (self.metro_options() if target == "metro" else self.transit_options())}
        if method in {"setPoiDensity", "setPoiProfileField"}:
            self._require_document_epoch(params)
            if not backend.has_document:
                raise RuntimeError(i18n.tr('ui.winui_engine.dispatch.ae3f8c8a07'))
            target = canonical_profile_target(str(params["target"]))
            key = params["key"]
            allowed_fields = {"density_enabled", "density_tier"}
            if method == "setPoiProfileField":
                allowed_fields.add("png_internal_buildings")
            if key not in allowed_fields:
                raise ValueError(f"unknown POI profile field: {key!r}")
            profile = backend.get_poi_profile(target)
            profile[key] = params["value"]
            return {"profile": backend.set_poi_profile(target, profile)}
        if method == "setTextIconSettings":
            self._require_document_epoch(params)
            if not backend.has_document:
                raise RuntimeError(i18n.tr('ui.winui_engine.dispatch.ae3f8c8a07'))
            if params.get("theme") != backend.theme_stem:
                raise ValueError(i18n.tr('ui.winui_engine.dispatch.7ddd21b8bf'))
            target = canonical_profile_target(str(params["target"]))
            profile = validate_poi_profile(params["profile"], target)
            visibility = normalize_poi_visibility_snapshot(params["visibility"])
            if target == "normal" and "enabled" in params and not isinstance(params["enabled"], bool):
                raise ValueError(i18n.tr('ui.winui_engine.dispatch.96b57316e5'))
            fields = params["fields"]
            if not isinstance(fields, dict) or not set(fields) <= set(TEXT_ICON_FIELDS):
                raise ValueError(i18n.tr('ui.winui_engine.dispatch.c9a482b2c3'))
            for key, value in fields.items():
                field = FIELDS[key]
                if field.kind == "toggle":
                    valid = isinstance(value, bool)
                elif field.kind == "number":
                    parts = list(value.values()) if isinstance(value, dict) and set(value) <= {"fixed", "scaled"} else [value]
                    valid = bool(parts) and all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in parts)
                    valid = valid and field.low <= sum(parts) <= field.high
                elif field.kind == "color":
                    valid = isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?", value)
                elif field.kind == "font":
                    valid = isinstance(value, str) and 0 < len(value) <= 256
                else:
                    valid = value in field.choices
                if not valid:
                    raise ValueError(i18n.tr('ui.winui_engine.dispatch.f7297a7ae6', p0=field.label))
            options = params.get("options", {})
            allowed_options = {"stations", "station_names_enabled", "facilities_enabled", "facility_names_enabled"}
            if target in {"normal", "transit"}:
                allowed_options.update(("station_name_placement", "station_name_size"))
            if target == "metro":
                allowed_options.remove("facility_names_enabled")
                allowed_options.update(("station_name_size", "station_diameter", "station_font", "station_name_color", "station_name_stroke", "station_name_stroke_color", "merge_enabled", "merge_range"))
            if not isinstance(options, dict) or not set(options) <= allowed_options:
                raise ValueError(i18n.tr('ui.winui_engine.dispatch.dd98f132f7'))
            current_options = self.metro_options() if target == "metro" else self.transit_options()
            checked_options = {key: validate_option(current_options, key, value) for key, value in options.items()}
            # 发布前先校验完整草稿。
            for key, value in fields.items():
                backend.apply_style_override(key, value)
            backend.set_poi_profile(target, profile)
            backend.set_poi_visibility_snapshot(visibility)
            if target == "normal" and "enabled" in params:
                backend.set_layer_visibility("label", params["enabled"])
            if target != "normal":
                current = self.metro_options() if target == "metro" else self.transit_options()
                current.update(checked_options)
                if target == "transit" and "station_name_placement" in checked_options:
                    apply_station_name_placement(current, checked_options["station_name_placement"])
                backend.update_transit_options(target == "metro", current)
                if target == "transit":
                    self._transit_session_options = current
            return {"applied": True}
        if method == "previewPresented":
            name = str(params["bundleId"])
            if backend._preview_server.acknowledge_bundle(name):
                if params.get("completion") == "base":
                    backend.acknowledge_preview_base(int(params["revision"]))
            return {}
        if method == "setPoiVisibility":
            self._require_document_epoch(params)
            if not backend.has_document:
                raise RuntimeError(i18n.tr('ui.winui_engine.dispatch.ae3f8c8a07'))
            snapshot = backend.set_poi_visibility_snapshot(params.get("snapshot"))
            layers = dict(backend.layer_visibility)
            layers["poi"] = any(snapshot["icons"].values())
            return {"poiVisibility": snapshot, "layers": layers}
        if method == "setLayerVisibility":
            key = str(params["key"])
            if key not in backend.layer_visibility:
                raise ValueError(f"unknown layer: {key}")
            enabled = bool(params["enabled"])
            if key == "poi":
                if "documentEpoch" in params:
                    self._require_document_epoch(params)
                visibility = self._current_poi_visibility()
                visibility["icons"] = {group: enabled for group in POI_GROUPS}
                visibility = backend.set_poi_visibility_snapshot(visibility)
            else:
                backend.set_layer_visibility(key, enabled)
                visibility = self._current_poi_visibility()
            layers = dict(backend.layer_visibility)
            layers["poi"] = any(visibility["icons"].values())
            return {"layers": layers, "poiVisibility": visibility}
        if method == "setField":
            key = str(params["key"])
            if key not in FIELDS:
                raise ValueError(f"unknown field: {key}")
            backend.apply_style_override(key, params["value"])
            return {"value": backend.field_value(key)}
        if method == "resetLayer":
            layer = str(params["layer"])
            if layer not in LAYER_FIELDS:
                raise ValueError(f"unknown layer: {layer}")
            for key in LAYER_FIELDS[layer]:
                field = FIELDS[key]
                backend.apply_style_override(
                    key, backend.theme_default_field_value(key, field.default))
            return {"fields": {key: backend.field_value(key, FIELDS[key].default)
                               for key in LAYER_FIELDS[layer]}}
        if method == "resetFields":
            self._require_document_epoch(params)
            keys = params.get("keys")
            if not isinstance(keys, list) or any(not isinstance(key, str) for key in keys):
                raise ValueError("reset keys must be an array of visible field keys")
            visible = {key for group in LAYER_FIELDS.values() for key in group}
            if not set(keys) <= visible:
                raise ValueError("reset keys include fields outside the visible pages")
            for key in dict.fromkeys(keys):
                field = FIELDS[key]
                value = backend.theme_default_field_value(key, field.default)
                backend.apply_style_override(key, value)
            return {"fields": {key: backend.field_value(key, FIELDS[key].default)
                               for key in dict.fromkeys(keys)}}
        if method == "resetProfileFields":
            return self._reset_profile_fields(params)
        if method == "setLabelChild":
            if "documentEpoch" in params:
                self._require_document_epoch(params)
            key = str(params["key"])
            if key not in LABEL_CHILDREN:
                raise ValueError(f"unknown label child: {key}")
            kind, target = LABEL_CHILDREN[key]
            enabled = bool(params["enabled"])
            if kind == "field":
                backend.apply_style_override(target, enabled)
            elif kind in {"poi_icons", "poi_names"}:
                visibility = self._current_poi_visibility()
                channel = "icons" if kind == "poi_icons" else "names"
                groups = (("facility", "ordinary") if target == "building"
                          else (target,))
                for group in groups:
                    visibility[channel][group] = enabled
                visibility = backend.set_poi_visibility_snapshot(visibility)
            else:
                backend.set_poi_category_enabled(target, enabled)
            return {"enabled": enabled, "poiVisible": backend.map_layer_visibility()["poi"]}
        if method == "setTransitType":
            key = str(params["key"])
            enabled = bool(params["enabled"])
            if key not in dict(TRANSIT_TYPES):
                raise ValueError(f"unknown transit type: {key}")
            if bool(params.get("metro", False)):
                options = self.metro_options()
                selected = set(options["types"])
                (selected.add if enabled else selected.discard)(key)
                options["types"] = [name for name, _ in TRANSIT_TYPES if name in selected]
                backend.update_transit_options(True, options)
                return {"types": options["types"]}
            if bool(params.get("context", False)):
                options = self.transit_options()
                selected = set(options["types"])
                (selected.add if enabled else selected.discard)(key)
                options["types"] = [name for name, _ in TRANSIT_TYPES if name in selected]
                self._transit_session_options = options
                backend.update_transit_options(False, options)
                return {"types": options["types"]}
            backend.set_transit_type_enabled(key, enabled)
            return {"types": list(backend.transit_types)}
        if method == "getTransitSchema":
            metro = bool(params.get("metro", False))
            return option_schema(self.metro_options() if metro else self.transit_options())
        if method == "setTransitOption":
            metro = bool(params.get("metro", False))
            key = str(params["key"])
            options = self.metro_options() if metro else self.transit_options()
            validate_option(options, key, params["value"])
            if not metro and key in STYLE_FIELDS:
                backend.apply_style_override(STYLE_FIELDS[key], params["value"])
            options[key] = params["value"]
            if not metro and key == "station_name_placement":
                apply_station_name_placement(options, params["value"])
            if not metro and key in {"station_names_enabled", "facility_names_enabled"}:
                placement = "station" if key == "station_names_enabled" else "icon"
                if params["value"]:
                    apply_station_name_placement(options, placement)
                elif options["station_name_placement"] == placement:
                    apply_station_name_placement(options, "none")
            if not metro and key == "line_color_mode":
                options["palette_index"] = {"game": 0, "classic": 1, "soft": 2}[params["value"]]
                options["auto_color"] = params["value"] != "game"
            if not metro:
                if key == "layers":
                    for layer, option in LAYER_OPTIONS.items():
                        if layer in params["value"]: options[option] = bool(params["value"][layer])
                else:
                    for layer, option in LAYER_OPTIONS.items():
                        if key == option: options["layers"][layer] = bool(params["value"])
            if key == "merge_enabled":
                backend.set_merge_options(transit=bool(params["value"]))
            if key == "station_names_mode" and metro:
                options["station_names_enabled"] = params["value"] != "none"
                if params["value"] != "none": options["station_name_style"] = params["value"]
            if key == "station_names_enabled" and metro:
                options["station_names_mode"] = (options.get("station_name_style", "all")
                                                 if params["value"] else "none")
            if not metro:
                self._transit_session_options = options
            backend.update_transit_options(metro, options)
            return {"options": self.metro_options() if metro else self.transit_options()}
        if method == "setMetroOutline":
            data = backend.current_style_data()
            data.setdefault("transports", {}).setdefault("line", {}).setdefault(
                "light_outline", {})["enabled"] = bool(params["enabled"])
            if not backend.on_style_applied(data):
                raise RuntimeError(i18n.tr('ui.winui_engine.dispatch.c2952a0254'))
            return {"enabled": bool(params["enabled"])}
        if method == "setMergeOverride":
            category = str(params["category"]).strip()
            if not category:
                raise ValueError("merge category is required")
            if bool(params["useOverride"]):
                distance = float(params["distance"])
                if not math.isfinite(distance) or not 0 <= distance <= 500:
                    raise ValueError("merge distance must be between 0 and 500")
                backend.set_merge_override(category, enabled=bool(params["enabled"]),
                                           distance=distance)
            else:
                backend.merge_overrides.pop(category, None)
                backend._schedule_settings_save()
            return {"overrides": copy.deepcopy(backend.merge_overrides)}
        if method == "getFacilityCatalog":
            return {"facilities": backend.facility_poi_catalog(), "documentEpoch": self._document_epoch}
        if method == "setFacilitySelection":
            self._require_document_epoch(params)
            if not backend.has_document:
                raise RuntimeError(i18n.tr('ui.winui_engine.dispatch.ae3f8c8a07'))
            catalog = backend.facility_poi_catalog()
            known = {str(item["id"]) for item in catalog}
            raw_ids = params.get("ids")
            if not isinstance(raw_ids, list) or any(not isinstance(value, str) for value in raw_ids):
                raise ValueError("facility ids must be an array of strings")
            ids = set(raw_ids)
            if not ids <= known:
                raise ValueError("unknown facility id")
            # 替换任何覆盖前先校验完整草稿。
            overrides = copy.deepcopy(backend.facility_poi_overrides)
            for key in known:
                overrides.setdefault(key, {})["enabled"] = key in ids
            backend.facility_poi_overrides = overrides
            return {"facilities": backend.facility_poi_catalog()}
        if method == "setOutputOption":
            key = str(params["key"])
            value = params["value"]
            if "profileTarget" in params:
                self._require_document_epoch(params)
                if not backend.has_document:
                    raise RuntimeError(i18n.tr('ui.winui_engine.dispatch.ae3f8c8a07'))
                target = str(params["profileTarget"])
                if target not in OUTPUT_PROFILE_TARGETS:
                    raise ValueError(f"unknown output profile target: {target!r}")
                if key not in {"size", "rotation"}:
                    raise ValueError(f"unknown output profile field: {key}")
                profile = backend.set_output_profile(target, {key: value})
                return {"profileTarget": target, "profile": profile}
            setters = {"size": backend.set_size, "rotation": backend.set_rotation,
                       "rangeN": backend.set_range_n,
                       "rangeAnchor": backend.set_range_anchor}
            if key not in setters:
                raise ValueError(f"unknown output option: {key}")
            setters[key](value)
            return self.snapshot()["output"]
        if method == "compilePreview":
            return {"started": backend.compile_preview_scene(self.callbacks, force=bool(params.get("force", False)))}
        if method == "refreshPreview":
            if not backend.has_document:
                return {"started": False}
            if backend.preview_needs_rebuild():
                return {"started": backend.compile_preview_scene(self.callbacks)}
            return {"started": False, "theme": backend.preview_theme()}
        if method == "prepareRmpProject":
            if not backend.has_document:
                raise ValueError("请先打开地图")
            if backend.busy or backend.local_render_active or self._external_busy:
                raise ValueError("当前正在生成地图，请稍后打开编辑器")
            key, job = self._rmp_context()
            self._rmp_pending = (key, job, str(params["requestId"]), self._document_epoch)
            self._render_mode = "rmp"
            try:
                if not backend._submit_metro_job_in_engine(job, self.callbacks):
                    raise ValueError("无法启动地铁编辑快照生成")
            except Exception:
                self._render_mode = None
                self._rmp_pending = None
                raise
            return {"started": True}
        if method == "renderMetro":
            busy = bool(backend.busy or getattr(backend, "local_render_active", False) or
                        self._export.request_id or self._external_busy)
            if not backend.has_document or busy:
                return {"started": False, "busy": busy}
            request_id = str(params["requestId"])
            options = self.metro_options()
            self._render_mode = "metro"
            self._metro_request_id = request_id
            try:
                started = backend.render_metro_in_engine(options, self.callbacks)
            except Exception:
                self._render_mode = None
                self._metro_request_id = None
                raise
            if not started:
                self._render_mode = None
                self._metro_request_id = None
            return {"started": started}
        if method == "getExportProfile":
            return self._export_profile(params)
        if method == "export":
            self._require_document_epoch(params)
            request = ExportRequest.from_dict(params)
            capabilities = self._format_capabilities(request.context)
            if request.format not in capabilities:
                raise ValueError("unsupported export format")
            if not backend.has_document or backend.busy or self._export.request_id or self._external_busy:
                return {"started": False}
            export_id = str(params.get("requestId") or uuid.uuid4().hex)
            output_format = request.format
            context_name = request.context.value
            requested_target = request.target or request.output_dir
            if not requested_target:
                if output_format in {"RMG", "RMP"}:
                    raise ValueError(i18n.tr('ui.winui_engine.dispatch.fd1e2dca12'))
                extension = ".html" if output_format == "HTML" else ".png"
                requested_target = str(Path(backend.output_dir) / f"map{extension}")
            output_path = Path(requested_target).resolve()
            backend.output_dir = str(output_path if output_format in {"RMG", "RMP"}
                                     else output_path.parent)
            self._export.request_id = export_id
            self._export.document_epoch = self._document_epoch
            self._export.context = context_name
            self._export.format = output_format
            self._export.requested_target = str(requested_target)
            export_epoch = self._document_epoch
            request = replace(request, target=str(requested_target))
            if output_format in {"RMG", "RMP"}:
                try:
                    started = self._start_external_project_export(
                        request, params, self._export_event_metadata())
                except Exception:
                    self._clear_export()
                    raise
                if not started:
                    self._clear_export()
                return {"started": started}

            callbacks = {
                **self.callbacks,
                "cancelled": lambda metadata=self._export_event_metadata(): self._export_cancelled(metadata),
                "progress": lambda percent, message: self._export_progress(
                    percent, message, export_id, export_epoch,
                    context_name, output_format, str(requested_target)),
                "error": lambda error: self._export_failed(
                    error, export_id, export_epoch,
                    context_name, output_format, str(requested_target)),
                "web_done": lambda path: self._export_completed(path, export_id),
            }
            try:
                if request.context == ContextMode.METRO:
                    # 地铁示意图保留独立的旧版配置。
                    self._export.target = str(request.target)
                    started = backend.render_png_export(
                        request, callbacks, transit_options=self.transit_options(),
                        metro_options=self.metro_options(), in_engine=True)
                else:
                    region = self._normalize_output_region(params.get("region"))
                    render_snapshot, theme_data, layer_snapshot = self._capture_output_snapshot(
                        request.context, request.format, params)
                    transit_options = (self.transit_options()
                                       if request.context == ContextMode.TRANSIT else None)
                    render_snapshot["export_context"] = context_name
                    render_snapshot["export_format"] = output_format
                    render_snapshot["export_target"] = str(requested_target)
                    render_snapshot["request_id"] = export_id
                    request = replace(
                        request, region=region,
                        theme_snapshot={"name": backend.theme_stem,
                                        "data": theme_data},
                        layer_snapshot=layer_snapshot,
                        render_snapshot=render_snapshot,
                    )
                    if request.format == "HTML":
                        started = backend.export_web(
                            request.output_dir or backend.output_dir, callbacks,
                            region=region, target=request.target, request=request)
                    else:
                        self._export.target = str(request.target)
                        started = backend.render_png_export(
                            request, callbacks, transit_options=transit_options,
                            metro_options=self.metro_options(), file_result=True)
            except Exception:
                self._clear_export()
                raise
            if not started:
                self._clear_export()
            return {"started": started}

        if method == "cancel":
            if backend._local_png_active:
                backend.cancel()
                return {"cancelled": False, "cancelRequested": True, "message": i18n.tr('ui.winui_engine.dispatch.e8e08b0f61')}
            if self._saving_export:
                return {"cancelled": False, "message": i18n.tr('ui.winui_engine.dispatch.e668d01e2c')}
            if self._external_busy:
                return {"cancelled": False, "message": i18n.tr('ui.winui_engine.dispatch.19bb4eb8a9')}
            backend.cancel()
            backend.cancel_export()
            pending = getattr(self, "_rmp_pending", None)
            if self._render_mode == "rmp" and pending:
                self.event("rmpProjectError", {"requestId": pending[2], "message": "已取消编辑工程生成"})
            self._rmp_pending = None
            self._clear_export()
            self._render_mode = None
            self._metro_request_id = None
            return {"cancelled": True}
        if method == "close":
            self.running = False
            return {"closed": True}
        raise ValueError(f"unknown method: {method}")

    def handle(self, frame: dict) -> None:
        request_id = frame.get("id")
        try:
            if not isinstance(request_id, (str, int)) or not isinstance(frame.get("params", {}), dict):
                raise ValueError("invalid request frame")
            result = self.dispatch(str(frame["method"]), frame.get("params", {}))
            self.send({"id": request_id, "result": result})
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            self.send({"id": request_id, "error": {"code": type(exc).__name__, "message": str(exc)}})

    def _read(self) -> None:
        for line in self.input:
            try:
                frame = json.loads(line)
                if not isinstance(frame, dict):
                    raise ValueError("request must be an object")
                self.requests.put(frame)
            except Exception as exc:  # noqa: BLE001
                self.event("protocolError", {"message": str(exc)})
            self.scheduler.wakeup.set()
        self.requests.put(None)
        self.scheduler.wakeup.set()

    def run(self) -> int:
        threading.Thread(target=self._read, name="winui-engine-input", daemon=True).start()
        try:
            while self.running:
                self.scheduler.pump()
                try:
                    frame = self.requests.get_nowait()
                except queue.Empty:
                    self.scheduler.wakeup.wait(self.scheduler.wait_seconds())
                    self.scheduler.wakeup.clear()
                    continue
                if frame is None:
                    break
                self.handle(frame)
            return 0
        finally:
            with self._theme_preview_lock:
                self._theme_preview_request_id = None
                self._theme_preview_pending = None
            self.backend.close()
            self._preview_files.cleanup()


@contextmanager
def _worker_stdin():
    """避免 Windows 子进程继承 JSON 协议输入管道；引擎读取器保留原流，子进程改接空设备。"""
    if sys.platform != "win32":
        yield
        return

    import ctypes
    import msvcrt
    import os
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.SetStdHandle.argtypes = [wintypes.DWORD, wintypes.HANDLE]
    kernel.SetStdHandle.restype = wintypes.BOOL
    stdin_id = wintypes.DWORD(-10)
    original = kernel.GetStdHandle(stdin_id)
    with open(os.devnull, "rb") as child_input:
        handle = msvcrt.get_osfhandle(child_input.fileno())
        if not kernel.SetStdHandle(stdin_id, handle):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield
        finally:
            kernel.SetStdHandle(stdin_id, original)


def main() -> int:
    with _worker_stdin():
        return EngineServer().run()


if __name__ == "__main__":
    raise SystemExit(main())
