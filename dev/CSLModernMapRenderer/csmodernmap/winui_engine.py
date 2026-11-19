"""处理 WinUI 客户端与渲染进程之间的逐行 JSON 协议。

此进程独占 GuirBackend 和设置文件；标准输出仅用于协议帧，诊断信息和异常堆栈写入标准错误。"""

from __future__ import annotations

from . import i18n

import heapq
import hashlib
import copy
import errno
import json
import math
import os
import re
import queue
import sys
import tempfile
import threading
import time
from .task_error import error_payload
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
from .transit_ui_schema import option_schema, validate_option, STYLE_FIELDS, LAYER_OPTIONS
from .winui_workflows import WinuiWorkflows, METHODS as WORKFLOW_METHODS
from csmodernmap.ui_contracts import (ExportRequest, ContextMode, RenderRegion,
                            export_capabilities)
from .ribbon_schema import FIELDS, FIELD_DEPENDENCIES, LAYER_FIELDS, TRANSIT_TYPES
from .theme_contract import normalize_theme, sanitize_theme_data, validate_theme_values

PROTOCOL_VERSION = 1
TEXT_ICON_FIELDS = ("label.roads", "label.districts", "road.game_names", "poi.game_names",
    "transport.stations", "transport.station_names", "poi.set", "poi.size", "poi.font",
    "poi.name_size", "poi.name_stroke", "poi.name_stroke_width") + tuple(
        f"label.{attr}.{kind}" for kind in ("road", "district")
        for attr in ("font", "size", "color", "stroke_width", "stroke_color"))
TEXT_ICON_OPTIONS = {
    "normal": ("stations", "facilities_enabled", "station_name_placement", "station_name_size"),
    "transit": ("stations", "facilities_enabled", "station_name_placement", "station_name_size"),
    "metro": ("facilities_enabled", "merge_enabled", "merge_range"),
}
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
    "canvas", "satellite", "terrain", "water", "forest", "built_area",
    "zone", "land_use", "district", "roads", "buildings",
    "procedural_objects", "railway", "traffic", "transports", "poi_icons",
    "labels", "watermark",
)
HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$")
COLOR_PATHS = {tuple(path) for key, path in STYLE_FIELD_PATHS.items()
               if key in FIELDS and FIELDS[key].kind == "color"}

def _polyline_length(points) -> float:
    """与 Qt 版 guir/main_window.py::_polyline_length 同口径：水平面 (x, z) 折线长。"""
    points = list(points)
    return sum(math.hypot(float(right.x) - float(left.x), float(right.z) - float(left.z))
               for left, right in zip(points, points[1:]))


def _polygon_area(points) -> float:
    """与 Qt 版 guir/main_window.py::_polygon_area 同口径的鞋带公式。"""
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
        self.backend.preview_enabled = False
        self.requests: queue.Queue[dict | None] = queue.Queue()
        self.write_lock = threading.Lock()
        self._output_failed = False
        self.running = True
        self._clear_export()
        self._saving_export = False
        self.workflows = WinuiWorkflows(self)
        self._document_epoch = 0
        from .artwork import ArtworkState
        self.artwork_state = ArtworkState(self._document_epoch)
        self._artwork_jobs = []
        self._artwork_active = False
        self._artwork_stop = threading.Event()
        self.artwork_diagnostics = {}
        self._artwork_started = {}
        self._city_draft = None
        self._city_edit_timer = None
        self._city_waiting_drafts = {}
        self._artwork_scene = None
        self._stats_cache: dict | None = None
        self._external_busy = False
        self._render_mode: str | None = None
        self._theme_preview_request_id: str | None = None
        self._theme_import: tuple[str, int, str, str] | None = None
        self._theme_preview_lock = threading.Lock()
        self._theme_preview_pending: tuple[str, dict, str, int, object] | None = None
        self._theme_preview_busy = False
        self._theme_thumbnail_pending = {}
        self._theme_thumbnail_busy = False
        self._theme_thumbnail_thread = None
        self._poi_editor_token: str | None = None
        self._poi_editor_mtime: tuple[int, int | None] | None = None
        self._preview_files = tempfile.TemporaryDirectory(prefix="cslmm-winui-")
        self._compatibility_files: dict[str, Path] = {}
        self.callbacks = {
            "progress": self._render_progress,
            "busy": lambda value: self.event("busy", {"value": bool(value) or self._saving_export}),
            "meta": lambda info, source: self.event("meta", {"info": info, "source": source}),
            "error": self._backend_error,
            "scene_error": lambda error: self.event("sceneError", error_payload(error, operation="preview", stage="preview", documentEpoch=self._document_epoch)),
            "scene_detail_error": lambda base, error: self.event("sceneDetailError", error_payload(
                error, operation="preview", stage="enrichment", documentEpoch=self._document_epoch,
                sceneId=base.scene_id, revision=base.revision, notice=tr("gui.file.preview_details_skipped"))),
            "scene_ready": self._scene_ready,
            "scene_compiled": lambda bundle: self.event("sceneCompiled", {
                "sceneId": bundle.scene_id, "revision": bundle.revision,
                "documentEpoch": self._document_epoch}),
            "done": self._render_done,
            "rmp_done": self._rmp_done,
            "web_done": lambda path: self.event("exportDone", {"path": str(path)}),
            "web_result": lambda path, report: self.event("exportDone", {
                "path": str(path), "warnings": self._render_warnings(road_order=report)}),
        }

    def send(self, frame: dict) -> None:
        with self.write_lock:
            if self._output_failed:
                return
            try:
                self.output.write(json.dumps(frame, ensure_ascii=False, separators=(",", ":")) + "\n")
                self.output.flush()
            except OSError as error:
                # Windows 的已断开重定向管道也可能返回 EINVAL。
                if error.errno not in (errno.EPIPE, errno.EINVAL):
                    raise
                self._output_failed = True
                self.running = False
                self.scheduler.wakeup.set()
                print(f"WinUI protocol output disconnected: {error}", file=sys.stderr)

    def event(self, name: str, payload: dict) -> None:
        self.send({"event": name, "payload": payload})

    def _require_document_epoch(self, params: dict) -> None:
        epoch = params.get("documentEpoch")
        if type(epoch) is not int or epoch != self._document_epoch:
            raise RuntimeError(i18n.tr('ui.winui_engine._require_document_epoch.8e11490207'))

    def _check_satellite_texture(self, style, limit) -> None:
        from .satellite_filter import satellite_image
        from PIL import Image
        import io
        if self.backend.document is None or self.backend.document._satellite_info is None:
            raise ValueError(tr("gui.satellite.unavailable"))
        if limit is not None and (type(limit) is not int or limit <= 0):
            raise ValueError("Invalid texture size limit")
        png = satellite_image(self.backend.document, style, progress=self._render_progress)
        if limit is not None:
            with Image.open(io.BytesIO(png)) as image:
                if max(image.size) > limit:
                    raise ValueError(tr("satellite_texture_limit", size=max(image.size), limit=limit))

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
        self._artwork_scene = (self._document_epoch, bundle)
        self.event("sceneReady", {"manifestUrl": manifest_url,
                                  "sceneId": bundle.scene_id, "revision": bundle.revision,
                                  "completion": bundle.manifest.get("completion", "complete"),
                                  "warnings": self._render_warnings(road_order=bundle.manifest.get("roadOrder", {}), include_read_info=False),
                                  "theme": self.backend.preview_theme(),
                                  "layers": self.backend.map_layer_visibility()})
        waiting = getattr(self, '_city_waiting_drafts', {})
        handled_current = any(config is self._city_draft for config in waiting.values())
        for identity, config in list(waiting.items()):
            if config is not self._city_draft and not self.artwork_state.is_pinned(identity):
                waiting.pop(identity)
                continue
            try:
                self._start_city_draft(config)
            except Exception as exc:
                waiting.pop(identity, None)
                self.artwork_state.fail(identity, str(exc))
                if config is self._city_draft:
                    self.event('artworkFailed', {'documentEpoch': identity[0], 'artworkRevision': identity[1],
                        'operationId': config.get('operationId'), 'message': str(exc)})
        if self._city_draft is not None and not handled_current and self._city_edit_timer is None:
            try:
                if not self._city_draft['waiting']:
                    self._city_draft['revision'] = 0
                self._start_city_draft()
            except Exception as exc:
                config = self._city_draft
                config['waiting'] = False
                self.artwork_state.fail((self._document_epoch, self.artwork_state.revision), str(exc))
                self.event('artworkFailed', {'documentEpoch': self._document_epoch,
                    'operationId': config.get('operationId'),
                    'artworkRevision': config.get('revision', 0), 'message': str(exc)})

    def _render_compatibility_preview(self, params: dict) -> dict:
        self._require_document_epoch(params)
        backend = self.backend
        if backend.preview_enabled:
            raise RuntimeError("Stop the interactive preview before rendering an image")
        if not backend.has_document:
            raise RuntimeError("No map is loaded")
        request_id = params.get("requestId")
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("A preview requestId is required")
        context = ContextMode(params.get("context", "normal"))
        if context not in {ContextMode.NORMAL, ContextMode.TRANSIT}:
            raise ValueError("Image preview supports normal and transit maps")
        if backend.busy or backend.local_render_active or self._saving_export:
            return {"started": False}
        job, _, _ = self._capture_output_snapshot(context, "PNG", {
            "output": {"size": 2048}, "quickPng": True})
        # 图片预览沿用预览的 POI 配置，正式 PNG 的独立配置保持不变。
        backend._attach_poi_render_config(job, "preview")
        if context == ContextMode.NORMAL and not job["ui"]["text_enabled"]:
            job["ui"]["poi_visibility_snapshot"]["icons"] = {
                group: False for group in job["ui"]["poi_visibility_snapshot"]["icons"]}
        job["final_png"] = True
        metadata = {"requestId": request_id, "documentEpoch": self._document_epoch}
        path = Path(self._preview_files.name) / f"map-{uuid.uuid4().hex}.png"

        def failed(error):
            self.event("compatibilityPreviewFailed", error_payload(
                error, operation="preview", stage="imagePreview", **metadata))

        def done(*_args):
            try:
                Path(backend.result_png_path).replace(path)
                backend.discard_png_result()
                self._compatibility_files[request_id] = path
                self.event("compatibilityPreviewReady", {**metadata, "url": path.as_uri()})
            except Exception as error:
                failed(error)

        callbacks = {**self.callbacks, "done": done, "error": failed,
                     "cancelled": lambda: self.event("compatibilityPreviewCancelled", metadata)}
        request = ExportRequest(context=context, target=str(path), render_snapshot=job)
        return {"started": backend.render_png_export(request, callbacks, file_result=True)}

    def _backend_error(self, error) -> None:
        if self._render_mode == "rmp":
            pending = getattr(self, "_rmp_pending", None)
            self._rmp_pending = None
            self._render_mode = None
            self.event("rmpProjectError", error_payload(error, operation="metro", stage="rmp", requestId=pending[2] if pending else None, documentEpoch=pending[3] if pending else self._document_epoch))
            return
        metadata = self._export_event_metadata()
        if metadata:
            self._export_failed(
                error, metadata["requestId"], metadata["documentEpoch"],
                metadata["context"], metadata["format"], metadata["target"])
            return
        operation = "preview"
        export_id = self._export.request_id
        self._saving_export = False
        self._clear_export()
        self._render_mode = None
        self.event("error", error_payload(error, operation=operation, stage=operation, requestId=export_id, documentEpoch=self._document_epoch))
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
        self.event("error", error_payload(error, operation="export", stage="export", **metadata))
        self.event("busy", {"value": bool(getattr(self.backend, "busy", False))})

    def _render_progress(self, percent, message) -> None:
        metadata = self._export_event_metadata()
        if metadata and metadata["documentEpoch"] != self._document_epoch:
            return
        self.event("progress", {"percent": percent, "message": str(message),
                                 **(metadata or {})})

    def _rmp_done(self) -> None:
        pending = getattr(self, "_rmp_pending", None)
        self._rmp_pending = None
        self._render_mode = None
        if pending and pending[3] == self._document_epoch:
            try:
                self.event("rmpProjectReady", {"requestId": pending[2], "draft": self._rmp_draft(pending[0], pending[1])})
            except Exception as error:
                self.event("rmpProjectError", error_payload(error, operation="metro", stage="rmpDraft", requestId=pending[2], documentEpoch=pending[3]))
        else:
            self.event("rmpProjectError", {"requestId": pending[2] if pending else None, "message": "源地图已改变，请重新打开编辑器"})

    def _render_done(self, _image, _size, _stats, _elapsed, _region, _thumbnail, _context) -> None:
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

    def _export_completed(self, path, request_id, *, road_order=None):
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
        self.event("exportDone", {"path": str(path), "warnings": self._render_warnings(road_order=road_order),
                                   **(metadata or {"requestId": request_id})})
        self.event("busy", {"value": bool(getattr(self.backend, "busy", False))})

    def _render_warnings(self, *, road_order=None, include_read_info=True):
        from .cmm.file_format import read_information
        from .services.poi.direct_mapping import document_warnings
        from .schematic_water import water_warning
        from .preview_road_order import road_warning
        water = self.backend.last_diagnostics.get("stats", {}).get("transit_layout", {}).get("water", {})
        warning = water_warning(water)
        if road_order is None:
            road_order = self.backend.last_diagnostics.get("stats", {}).get("road_order", {})
        road_message = road_warning(road_order)
        document = getattr(self.backend, "document", None)
        return (([warning] if warning else [])
                + ([road_message] if road_message else [])
                + document_warnings(document)
                + (read_information(document) if include_read_info and document is not None else []))

    def _export_cancelled(self, metadata):
        if metadata["requestId"] == self._export.request_id:
            self._saving_export = False
            self._clear_export()
        self.event("exportCancelled", metadata)

    def map_statistics(self) -> dict:
        """地图概况页的六项统计。

        Qt 版把这些算在界面层（``guir/main_window.py::_map_statistics``）；
        WinUI 侧不该碰游戏几何，所以由引擎算好并按下城市缓存一次。
        """
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
        # 当前主题损坏时仍允许进入工作区重新选择；绘图路径继续报告原始读取错误。
        if backend.theme_load_error:
            style_data = {}
            fields = {key: field.default for key, field in FIELDS.items()}
        else:
            style_data = backend.current_style_data()
            fields = {key: backend.field_value(key, field.default, style_data=style_data)
                      for key, field in FIELDS.items()}
        transit_options = self.transit_options()
        transit_draft = self._city_draft is not None and self._city_draft.get('job', {}).get('kind') == 'transit'
        if transit_draft:
            transit_options = self._draft_transit_options()
        map_options = backend.map_transit_options()
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
            "satelliteAvailable": getattr(backend.document, "_satellite_info", None) is not None,
            "sourceGame": document_game(backend.document),
            "sourceFile": backend.source_file,
            "cityInfo": dict(backend.city_info),
            "stats": self.map_statistics(),
            "theme": backend.theme_stem,
            "themes": backend.theme_choices(),
            "themeMigrationIssues": list(backend.theme_migration_issues),
            "themeLoadError": backend.theme_load_error,
            "themeUpdate": self.theme_update_notice(),
            "layers": visible_layers,
            "poiVisibility": poi_visibility,
            "showDecorativePois": self.backend.show_decorative_pois,
            "documentEpoch": self._document_epoch,
            "fields": fields,
            "fieldSchema": {key: {"label": field.label, "kind": field.kind,
                                   "description": tr("gui.style_editor.desc.road_footprint" if key.startswith("road.level.") and
                                                     key.endswith(".footprint_scale") else f"style.{key}.desc"),
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
            "mapTransitLineIds": map_options.get("line_ids"),
            "mapTransitOptions": map_options,
            "transitDraftChanged": bool(transit_draft and self._city_draft["params"].get("transitChanges")),
            "mapStations": bool(fields["transport.stations"]),
            "mapStationNames": bool(fields["transport.station_names"]),
            "transitOptions": transit_options,
            "metroOptions": metro_options,
            "lineSelection": {"map": self.line_selection_summary(map_options),
                              "transit": self.line_selection_summary(transit_options),
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
        return self.backend.transit_options(False)

    def _draft_transit_options(self):
        draft = self._city_draft
        if draft is None or draft['job'].get('kind') != 'transit':
            raise RuntimeError('No public transit draft is active')
        return copy.deepcopy(draft['job']['opts'])

    def _stage_transit_option(self, key, value):
        changes = self._city_draft['params'].setdefault('transitChanges', {})
        saved = self.backend.transit_options(theme=self._city_draft.get('themeId'))
        if saved.get(key) == value:
            changes.pop(key, None)
        else:
            changes[key] = copy.deepcopy(value)
        # A pending revert also has to be visible before the next layout starts.
        self._city_draft['job']['opts'][key] = copy.deepcopy(value)

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

    def theme_update_notice(self, *, include_handled=False, identity=None) -> dict | None:
        notice = self.backend.pending_preset_update(identity, include_handled=include_handled)
        if not notice:
            return None
        from .theme_metadata import field_label, field_kind, section_title, field_key_for, SECTION_ORDER, format_number
        from .ribbon_schema import FIELDS
        def row(change):
            path = change["path"]
            def display(item):
                spec = FIELDS.get(field_key_for(tuple(path)))
                if spec and spec.choices and item in spec.choices:
                    return spec.option_label(item)
                if path == ["terrain", "elevation_mode"] and isinstance(item, str):
                    return tr("gui.style_editor.value." + item)
                if type(item) in (int, float):
                    suffix = " m" if path[-1].endswith("_meters") else ""
                    return format_number(item) + suffix
                return None
            return {"section": section_title(path[0]) if path[0] in SECTION_ORDER else path[0],
                    "label": field_label(path[-1], tuple(path)),
                    "before": change["before"], "after": change["after"],
                    "beforeLabel": display(change["before"]), "afterLabel": display(change["after"]),
                    "kind": field_kind(tuple(path)), "conflict": change["conflict"]}

        order = {section: index for index, section in enumerate(SECTION_ORDER)}
        changes = sorted(notice["changes"], key=lambda change: order.get(change["path"][0], len(order)))
        return {"theme": notice["theme"], "name": notice["name"],
                "revision": notice["revision"], "sourceDigest": notice["sourceDigest"],
                "fromVersion": notice["fromVersion"], "toVersion": notice["toVersion"],
                "changes": [row(change) for change in changes]}

    def theme_revision(self) -> str:
        return hashlib.sha256(json.dumps({"theme": self.backend.theme_stem,
            "data": self.backend.current_style_data(), "themes": self.backend._saved_theme_records()},
            sort_keys=True, default=str).encode()).hexdigest()

    def theme_draft(self, *, data=None, base_id=None, new=False, theme_id=None) -> dict:
        from csmodernmap import theme_metadata as theme_meta
        backend = self.backend
        theme_id = theme_id or backend.theme_stem
        raw = (backend.current_style_data() if theme_id == backend.theme_stem else backend.theme_data(theme_id)) if data is None else data
        data = normalize_theme(self.validate_theme_draft(raw, raw))
        record = backend.user_themes.get(theme_id)
        if not new:
            base_id = (record.get("legacyBase") or record["base"]) if record else theme_id
        defaults = normalize_theme(sanitize_theme_data(backend.theme_defaults(base_id))) if base_id else copy.deepcopy(data)
        saved = backend.theme_data(theme_id)
        fields = theme_meta.describe_theme(data)
        return {"theme": theme_id, "data": data,
                "defaults": defaults, "baseTheme": base_id,
                "isNew": new, "isBuiltin": record is None and not new,
                "suggestedName": backend.suggest_theme_name(backend.theme_display() + " · " + tr("gui.theme.personal")),
                "themes": backend.theme_choices(),
                "migrationIssues": list(backend.theme_migration_issues),
                "savedData": normalize_theme(sanitize_theme_data(saved)),
                "savePath": "", "documentEpoch": self._document_epoch,
                "settingsRevision": self.theme_revision(),
                "sections": ["__basic__"] + [key for key in THEME_SECTIONS if key in data]
                            + [key for key, value in data.items()
                               if key not in THEME_SECTIONS and isinstance(value, dict)],
                "fields": fields,
                "sectionNames": {key: theme_meta.section_title(key) for key in data
                                 if isinstance(data[key], dict)},
                "scenes": {"__basic__": "overall", "canvas": "overall",
                           "satellite": "satellite",
                           "forest": "terrain", "district": "zones",
                           "zone": "zones", "land_use": "zones",
                           "poi_icons": "buildings",
                           "labels": "roads", "built_area": "built_area",
                           "watermark": "overall"}}


    def range_profile(self) -> dict:
        backend = self.backend
        grid = backend.range_tile_grid()
        if not backend.has_document:
            return {"available": False}
        if grid is None:
            return {"available": False, "worldBounds": backend.world_rect(),
                    "cityBounds": document_city_bounds(backend.document)}
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

    def queue_theme_thumbnail(self, identity):
        backend = self.backend
        if backend.session is None:
            return
        with self._theme_preview_lock:
            self._theme_thumbnail_pending[identity] = (
                copy.deepcopy(backend.user_themes[identity]['data']), backend.session,
                dict(backend.layer_visibility), backend.rotation, str(backend.poi_mapping_override))
            if self._theme_thumbnail_busy:
                return
            self._theme_thumbnail_busy = True
        self._theme_thumbnail_thread = threading.Thread(target=self._render_theme_thumbnails, name='theme-thumbnail', daemon=True)
        self._theme_thumbnail_thread.start()

    def _render_theme_thumbnails(self):
        from . import theme_thumbnail
        while True:
            with self._theme_preview_lock:
                if not self._theme_thumbnail_pending:
                    self._theme_thumbnail_busy = False
                    return
                identity = next(iter(self._theme_thumbnail_pending))
                job = self._theme_thumbnail_pending.pop(identity)
            data = job[0]
            target = theme_thumbnail.cache_path(self.backend._settings_dir, identity, data)
            temporary = target.with_suffix('.tmp')
            try:
                image = theme_thumbnail.render(*job)
                with self._theme_preview_lock:
                    record = self.backend.user_themes.get(identity)
                    if not self.running or not record or record['data'] != data or identity in self._theme_thumbnail_pending:
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    image.save(temporary, format='PNG')
                    os.replace(temporary, target)
                    for old in target.parent.glob(theme_thumbnail.cache_prefix(identity) + '*.png'):
                        if old != target:
                            old.unlink()
                self.event('themeThumbnailReady', {'theme': identity, 'url': target.resolve().as_uri()})
            except Exception as error:  # 缩略图任务边界：主题已保存，报告独立的预览失败。
                traceback.print_exc(file=sys.stderr)
                self.event('themeThumbnailFailed', {'theme': identity, 'message': str(error)})
            finally:
                temporary.unlink(missing_ok=True)

    def theme_text_sample(self, params: dict) -> dict:
        from PIL import Image
        from .layers.label_layer import LabelLayer, _resolve_size
        from .fonts import _candidate_paths, font_covers, resolve_text_font
        data = self.validate_theme_draft(params["data"], self.backend.current_style_data())
        kind = str(params["type"])
        if kind not in {"road", "district", "building", "park", "stop"}:
            raise ValueError("unknown label type")
        from .pixel_sizes import is_pixel_theme, resolve
        pixel = is_pixel_theme(data)
        if pixel:
            data = resolve(data, params.get('elementScale', 1))[0]
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
        if kind == "district" and not pixel:
            size = max(size, 12 * min(scale, 1))
        stroke = _resolve_size(cfg.get("stroke_width", 1.5 if kind == "park" else 2), scale) if kind in {"road", "district", "park"} else 0
        blur = _resolve_size(cfg.get("stroke_blur", 0), scale) if kind in {"road", "district"} else 0
        if not 0 <= size <= 1600 or not 0 <= stroke <= 100 or not 0 <= blur <= 100:
            raise ValueError("sample size or stroke is too large")
        painter = LabelLayer()
        font_name = cfg.get("font")
        bold = kind == "district" and bool(cfg.get("bold", False))
        font = painter._get_font(size, font_name, text=text, bold=bold)
        bbox = font.getbbox(text, stroke_width=round(stroke))
        padding = 24 + 2 * math.ceil(blur * 3)
        width, height = bbox[2] - bbox[0] + padding, bbox[3] - bbox[1] + padding
        image = Image.new("RGBA", (max(width, 1), max(height, 1)), (0, 0, 0, 0))
        from .style import Style
        painter.ctx = SimpleNamespace(image=image, shared={}, style=Style(data))
        painter._aa_k = scale
        painter._render_text((width / 2, height / 2), text, size,
                             cfg.get("color", colors[kind]), stroke,
                             cfg.get("stroke", "#FFFFFF"), font_family=font_name, bold=bold,
                             stroke_blur=blur)
        path = Path(self._preview_files.name) / f"text-{uuid.uuid4().hex}.png"
        image.save(path)
        resolved = resolve_text_font(font_name, text=text, bold=bold)
        requested = _candidate_paths(font_name, bold=bold) if font_name else []
        substituted = bool(font_name) and (resolved not in requested or not font_covers(resolved, text))
        return {"url": path.as_uri(), "fontSubstituted": substituted,
                "requestedFont": font_name, "resolvedFont": resolved.stem if resolved else "",
                "size": size, "strokeWidth": stroke, "strokeBlur": blur}

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
                "themeName": self.backend.theme_display(),
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
        line_ids = (selected if context == ContextMode.TRANSIT else self.backend.map_transit_options()).get("line_ids")
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
            "themeName": self.backend.theme_display(),
            "poiProfile": poi_profile,
            "poiVisibility": poi_visibility,
            "showDecorativePois": self.backend.show_decorative_pois,
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
            formats = ("PNG", "HTML")
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
                    "outputFields": ["size"] if metro else ["size", "rotation", "elementScale"],
                    "showsOutputOptions": True,
                    "showsRange": not metro, "showsPoiProfile": not metro,
                    "showsSharedLayers": not metro,
                    "showsTransitLines": transit or metro,
                    "resultImage": True,
                }
            elif output_format == "HTML":
                result[output_format] = {
                    "targetKind": "file", "context": context_name,
                    "outputProfileTarget": "transit_png" if context == ContextMode.TRANSIT else "normal_png",
                    "poiProfileTarget": None,
                    "outputFields": ["rotation", "htmlQuality", "elementScale"],
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
            from .output_profiles import update_output_profile
            output = update_output_profile(backend.get_output_profile(target),
                {key: raw_output[key] for key in capability["outputFields"] if key in raw_output}, target)
            quick_png = params.get("quickPng", False)
            if not isinstance(quick_png, bool) or quick_png and output_format != "PNG":
                raise ValueError("quickPng requires a PNG export and a boolean value")
            # 导出窗口可能仍持有旧快照；任务方向只取当前地图，随后随任务冻结。
            output["rotation"] = backend.rotation
            poi_target = "preview" if output_format == "HTML" else target
            poi = backend.get_poi_profile(poi_target)
            job = backend.capture_png_export_job(context, region,
                transit_options=self.transit_options() if context == ContextMode.TRANSIT else None,
                output_profile=output, poi_profile=poi,
                poi_visibility_snapshot=visibility, poi_profile_target=poi_target,
                full_city_default=region is None)
            if quick_png:
                output["aa"] = False
                job["aa"] = 1 if context == ContextMode.TRANSIT else False
            job["output_profile"] = copy.deepcopy(output)
            job["poi_profile"] = copy.deepcopy(poi)
            job["poi_visibility_snapshot"] = copy.deepcopy(visibility)
            job["profile_target"] = target
            job["poi_profile_target"] = poi_target
        job["style"] = backend.theme_stem
        job["ui"].setdefault("style_overrides", {})[backend.theme_stem] = copy.deepcopy(theme_data)
        if 'previewSizing' in params:
            raise ValueError('previewSizing is retired; use elementScale')
        # capture_png_export_job 已解析共享可见性和配置规则。
        job["document_epoch"] = self._document_epoch
        return job, theme_data, layers

    def _start_external_project_export(self, request: ExportRequest,
                                       params: dict, metadata: dict) -> bool:
        from .external_edit import (ExternalExportRequest, ExportSnapshot,
                                    export_rmg)

        backend = self.backend
        kind = request.format.casefold()
        if kind == "rmp":
            raise ValueError("请从 RMP 工程编辑器导出已编辑工程")
        if kind != "rmg":
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
                result = export_rmg(export_snapshot, external_request)
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
        options = {key: copy.deepcopy(saved[key]) for key in (
            "types", "line_ids", "merge_enabled", "merge_range", "spacing",
            "schematic_water", "water_smoothing", "facilities_enabled", "metro_edits",
        ) if key in saved}
        options["schematic_options"] = {
            key: saved.get("schematic_options", {}).get(key, defaults["diagram"][key])
            for key in ("adaptive_scale", "adaptive_strength")}
        job = {"file": self.backend.source_file, "opts": options}
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
            raise ValueError("尚未生成 RMP 工程草稿")
        warnings = self._render_warnings()
        project = build_editor_draft(scene, warnings=warnings)
        source = Path(job["file"]).resolve()
        with source.open("rb") as stream:
            source_hash = hashlib.file_digest(stream, "sha256").hexdigest()
        directory = writable_dir() / "rmp_projects" / source_hash
        return {"projectId": key, "sourceFile": str(source), "defaultPath": str(directory / (key + ".rmp.json")),
                "project": project, "metadata": {
                    "cityName": self.backend.city_info.get("name", "map"),
                    "warnings": warnings,
                    "sourceFingerprint": source_hash, "settingsFingerprint": key,
                    "rmpCommit": RMP_COMMIT, "sourceFile": str(source),
                    "generatorVersion": f"{__version__}/rmp-{RMP_GRAPH_VERSION}",
                    "needsVisualAdjustment": bool(self.backend.last_diagnostics.get("stats", {})
                        .get("transit_layout", {}).get("needs_visual_adjustment", False)),
                    "layoutFingerprint": hashlib.sha256(json.dumps(project["cmmDraft"], sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                }}

    def submit_artwork(self, job, *, sizing=None, draft=False, operation=None, identity=None):
        """后端入口：接收冻结作业，复用本地工作线程串行派生会话数据。"""
        import copy
        from .render_jobs import build_artwork_job
        from .gui_render_worker import _build_transit_renderer, _build_normal_renderer
        from .gui_render_worker import _load_ui_style
        from .artwork import MAX_EDGE
        backend = self.backend
        if self._artwork_active and self._artwork_stop.is_set():
            raise RuntimeError('Artwork cancellation is still completing')
        if backend.session is None or backend.session.document is not backend.document:
            raise RuntimeError('Artwork requires the current document session')
        if not self._artwork_active and (backend.busy or backend.local_render_active or self._external_busy):
            raise RuntimeError('Another document task is running')
        frozen = copy.deepcopy(job)
        frozen.setdefault('kind', 'normal')
        if type(frozen['size']) is not int or not 1 <= frozen['size'] <= MAX_EDGE:
            raise ValueError('Invalid artwork base long edge')
        if frozen['kind'] not in {'normal', 'transit'} or (
                frozen['kind'] == 'transit' and (frozen['opts'].get('abstract') or frozen['opts'].get('pure'))):
            raise ValueError('Artwork requires a geographic normal or transit job')
        if frozen.get('region') is None or frozen.get('region_policy'):
            raise ValueError('Resolve the draft region before accepting artwork')
        checked = build_artwork_job(frozen['kind'], frozen['region'],
            long_edge=frozen['size'], rotation=frozen['ui']['rotation'],
            element_scale=frozen.get('elementScale', frozen['size'] / 4096))
        frozen['elementScale'] = checked.derive_params['elementScale']
        frozen['ui']['elementScale'] = frozen['elementScale']
        ui = copy.deepcopy(frozen['ui'])
        frozen['ui']['style_overrides'][frozen['style']] = copy.deepcopy(_load_ui_style(ui, frozen['style']).data)
        from .services.poi.direct_mapping import load_mapping, SCHEMA_VERSION
        frozen['_poi_mapping_snapshot'] = {'schema_version': SCHEMA_VERSION,
            'entries': copy.deepcopy(list(load_mapping(ui.get('poi_mapping_override') or None).values()))}
        ui = copy.deepcopy(frozen['ui'])
        if frozen['kind'] == 'normal':
            from .city_artwork_sizing import prepare_style
            renderer = _build_normal_renderer(ui, frozen['style'], frozen['layers'],
                (checked.canvas['width'], checked.canvas['height']), False, 'normal')
        else:
            from .transit_artwork_sizing import prepare_style
            renderer = _build_transit_renderer(ui, frozen['style'], frozen['types'],
                (checked.canvas['width'], checked.canvas['height']),
                stations=bool(frozen['opts']['stations']), opts=frozen['opts'])
        prepare_style(renderer.style, backend.document,
            (checked.canvas['width'], checked.canvas['height']), sizing=sizing,
            element_scale=frozen['elementScale'])
        if identity is None:
            identity = self.artwork_state.request()
        retained = []
        for task in self._artwork_jobs:
            if task[0] != 'layout' or self.artwork_state.is_pinned(task[1]):
                retained.append(task)
            else:
                self._artwork_started.pop(task[1], None)
        self._artwork_jobs = retained
        live = {task[1] for task in retained if task[0] == 'layout'}
        running = getattr(self, '_artwork_running', None)
        if running is not None and running[0] == 'layout':
            live.add(running[1])
        self._artwork_started = {key: value for key, value in self._artwork_started.items() if key in live}
        self._artwork_started[identity] = time.perf_counter()
        self._artwork_jobs.append(('layout', identity, frozen, backend.session, copy.deepcopy(sizing), draft,
                                  copy.deepcopy(operation or {})))
        self._start_artwork_tasks()
        return identity

    def _artwork_progress(self, identity, operation_id, stage, percent, message=None):
        config = self._city_draft
        if (identity != (self._document_epoch, self.artwork_state.revision) or config is None
                or config.get('operationId', operation_id) != operation_id):
            return
        payload = {'documentEpoch': identity[0], 'artworkRevision': identity[1],
                   'operationId': operation_id, 'stage': stage, 'percent': percent}
        if message is not None:
            payload['message'] = str(message)
        self.event('artworkProgress', payload)

    def _cancel_city_edit_timer(self):
        if self._city_edit_timer is not None:
            self.scheduler.cancel(self._city_edit_timer)
            self._city_edit_timer = None

    def _schedule_city_edit(self):
        config = self._city_draft
        identity = self.artwork_state.request()
        config['revision'] = identity[1]
        config['waiting'] = False
        self._city_edit_timer = self.scheduler.call_later(350, self._run_scheduled_city_edit)
        return {'started': False, 'scheduled': True, 'documentEpoch': identity[0], 'artworkRevision': identity[1]}

    def _run_scheduled_city_edit(self):
        self._city_edit_timer = None
        config = self._city_draft
        try:
            self._start_city_draft(config)
        except Exception as error:
            identity = config['epoch'], config['revision']
            config['waiting'] = False
            self.artwork_state.fail(identity, str(error))
            self.event('artworkFailed', {'documentEpoch': identity[0], 'artworkRevision': identity[1],
                'operationId': config.get('operationId'), 'message': str(error)})

    def _start_city_draft(self, config=None):
        from .city_artwork import required_sources
        config = self._city_draft if config is None else config
        if config['epoch'] != self._document_epoch:
            self._city_draft = None
            return {'started': False}
        job = copy.deepcopy(config['job'])
        if not config['revision']:
            config['revision'] = self.artwork_state.request()[1]
        identity = self._document_epoch, config['revision']
        scene = self._artwork_scene
        from .terrain import resolve_terrain_config
        terrain = resolve_terrain_config(job['ui']['style_overrides'][job['style']].get('terrain', {}), job['ui'].get('terrain'))
        required = set(required_sources(job['layers'], terrain['mode'])) if job.get('kind') != 'transit' else set()
        available = set(scene[1].manifest['sources']) if scene is not None else set()
        if scene is not None and scene[1].manifest.get('surfaceFields', {}).get('builtArea'):
            available.add('builtArea')
        if scene is None or scene[0] != self._document_epoch:
            raise RuntimeError('The preview does not belong to the current document')
        bundle = scene[1]
        if required and (required - available or self._city_base_needs_rebuild(bundle, required, config)):
            if not config['waiting']:
                config['baseWaitStarted'] = time.perf_counter()
            config['waiting'] = True
            self._city_waiting_drafts[identity] = config
            self._artwork_progress(identity, config.get('operationId'), 'waitingBase', None)
            compiling = self.backend._preview_compile_thread
            if compiling is None or not compiling.is_alive():
                if not self.backend.compile_preview_scene(self.callbacks, self._city_render_theme(config)):
                    raise RuntimeError('Required editor base sources could not be prepared')
            return {'started': False, 'waiting': True}
        job['_preview_binding'] = {'sceneId': bundle.scene_id, 'revision': bundle.revision}
        base_wait = time.perf_counter()-config['baseWaitStarted'] if config['waiting'] else 0
        self._city_waiting_drafts.pop(identity, None)
        identity = self.submit_artwork(job, draft=True, identity=identity, operation={
            'operationId': config.get('operationId'), 'baseWaitSeconds': base_wait,
            'operationKind': config['kind']})
        self._artwork_started[identity] = config['started']
        config['revision'] = identity[1]
        config['waiting'] = False
        self.event('artworkRequested', {'documentEpoch': identity[0], 'artworkRevision': identity[1],
            'operationId': config.get('operationId'),
            **job['_preview_binding']})
        return {'started': True, 'documentEpoch': identity[0], 'artworkRevision': identity[1]}

    def _city_draft_job(self, params, previous=None):
        from .artwork import MAX_EDGE
        from .render_jobs import build_artwork_job
        config = copy.deepcopy(previous['params']) if previous is not None else {
            'output': {'size': 4096}, 'region': None}
        old_edge = config['output']['size']
        if 'longEdge' in params:
            edge = params['longEdge']
            if type(edge) is not int or not 1 <= edge <= MAX_EDGE:
                raise ValueError('Invalid artwork base long edge')
            config['output']['size'] = edge
        from .pixel_sizes import number
        edge = config['output']['size']
        scale = (config['output'].get('elementScale', old_edge / 4096) * edge / old_edge
                 if previous is not None else edge / 4096)
        config['output']['elementScale'] = number(params.get('elementScale', scale),
                                                 'elementScale', positive=True)
        if 'region' in params:
            config['region'] = copy.deepcopy(params['region'])
        rotation = params.get('rotation', previous['rotation'] if previous else self.backend.rotation)
        if type(rotation) is not int or rotation not in (0, 90, 180, 270):
            raise ValueError('Invalid artwork rotation')
        kind = previous['job'].get('kind', 'normal') if previous else params.get('kind', 'normal')
        if kind not in ('normal', 'transit'):
            raise ValueError('Invalid geographic draft kind')
        job, _, _ = self._capture_output_snapshot(ContextMode(kind), 'PNG', config)
        density = params.get('poiDensity', config.get('poiDensity'))
        if density is not None:
            if not isinstance(density, dict) or set(density) != {'density_enabled', 'density_tier'}:
                raise ValueError('Invalid artwork POI density')
            profile = validate_poi_profile({**job['poi_profile'], **density}, kind)
            config['poiDensity'] = copy.deepcopy(density)
            job['poi_profile'] = profile
            self.backend._attach_poi_render_config(job, job['poi_profile_target'],
                profile=profile, visibility_snapshot=job['poi_visibility_snapshot'])
        job['ui']['rotation'] = rotation
        theme_id = params.get('theme', previous.get('themeId') if previous else None)
        if theme_id is not None:
            if theme_id not in {choice['id'] for choice in self.backend.theme_choices()}:
                raise ValueError('Unknown editor theme')
            job['style'] = theme_id
        if previous is not None and previous.get('themeData') is not None:
            job['ui']['style_overrides'][job['style']] = copy.deepcopy(previous['themeData'])
        if 'data' in params:
            job['ui']['style_overrides'][job['style']] = self.validate_theme_draft(
                params['data'], self.backend.theme_data(job['style']))
        if kind == 'transit':
            theme_data = job['ui']['style_overrides'][job['style']]
            saved = self.backend.theme_data(job['style'])
            sizes = self.backend._transport_sizes(theme_data)
            base_sizes = self.backend._transport_sizes(saved)
            scoped_sizes = self.backend._view_section(False).get('themes', {}).get(job['style'], {})
            job['opts'].update({key: scoped_sizes.get(key, value) if value == base_sizes[key] else value
                                for key, value in sizes.items()})
            job['opts'].update(config.get('transitChanges', {}))
            job['types'] = list(job['opts']['types'])
        elif 'data' in params or (previous and previous.get('themeData') is not None):
            # Use unsaved city theme sizes when they differ from the stored theme.
            data = job['ui']['style_overrides'][job['style']]
            saved_sizes = self.backend._transport_sizes(self.backend.theme_data(job['style']), city=True)
            for key, value in self.backend._transport_sizes(data, city=True).items():
                if value != saved_sizes[key]:
                    job['ui']['map_transport'][key] = value
        checked = build_artwork_job(kind, job['region'], long_edge=job['size'], rotation=rotation,
                                    element_scale=config['output']['elementScale'])
        # 固定已解析范围，不让后续浏览范围设置改变活动草稿。
        source = config['region'].get('source', 'selection') if config['region'] else 'city'
        config['region'] = {'bounds': list(checked.derive_params['world_bounds']), 'source': source}
        return config, rotation, job, checked.canvas

    def _city_draft_state(self):
        config = self._city_draft
        if config is None:
            return {'active': False, 'documentEpoch': self._document_epoch}
        return {'active': True, 'documentEpoch': self._document_epoch,
            'revision': config['revision'], 'status': 'preparing' if config['waiting'] else self.artwork_state.status,
            'config': {'kind': config['job'].get('kind', 'normal'), 'region': copy.deepcopy(config['params']['region']),
                'longEdge': config['params']['output']['size'], 'rotation': config['rotation'],
                'elementScale': config['params']['output']['elementScale'],
                'poiDensity': {key: config['job']['poi_profile'][key] for key in ('density_enabled', 'density_tier')},
                **config['canvas']}}

    def _city_base_needs_rebuild(self, bundle, required, config=None):
        from .preview_scene import preview_source_signatures
        config = self._city_draft if config is None else config
        style = self._city_render_theme(config) if config else None
        signatures = preview_source_signatures(self.backend._preview_style(style), self.backend._preview_source_options(style))
        previous = bundle.manifest.get('sourceSignatures', {})
        return any(previous.get(source) != signatures.get(source) for source in required)

    def _city_render_theme(self, config):
        from .pixel_sizes import is_pixel_theme, resolve
        job = config['job']
        data = job['ui']['style_overrides'].get(job['style'])
        if data is None:
            data = self.backend.theme_data(job['style'])
        return resolve(data, job['elementScale'])[0] if is_pixel_theme(data) else data

    def export_artwork(self, target, *, long_edge=None, aa=1):
        """固定接受时的最新修订；等待其布局，不依赖网页呈现。"""
        from concurrent.futures import Future
        from .artwork import MAX_EDGE
        if long_edge is not None and (type(long_edge) is not int or not 1 <= long_edge <= MAX_EDGE):
            raise ValueError('Invalid artwork output long edge')
        if type(aa) is not int or aa not in (1, 2, 4):
            raise ValueError('Invalid artwork output AA')
        if not self._artwork_active and (self.backend.busy or self.backend.local_render_active):
            raise RuntimeError('Another document task is running')
        if self._city_edit_timer is not None:
            self._cancel_city_edit_timer()
            self._run_scheduled_city_edit()
        pinned = self.artwork_state.pin_export()
        result = Future()
        def release_pin(done, references=[pinned]):
            reference = references.pop()
            if done.cancelled():
                reference.cancel()
        result.add_done_callback(release_pin)
        self._artwork_jobs.append(('export', (self._document_epoch, self.artwork_state.revision),
            pinned, result, str(target), long_edge, aa))
        self._start_artwork_tasks()
        return result

    def cancel_artwork(self):
        self._cancel_city_edit_timer()
        self._artwork_stop.set()
        self.artwork_state.cancel_exports()
        self._city_waiting_drafts.clear()
        self.artwork_state.fail((self._document_epoch, self.artwork_state.revision), 'Artwork task cancelled')
        if self._city_draft is not None:
            self._city_draft['waiting'] = False
            self.event('artworkFailed', {'documentEpoch': self._document_epoch,
                'artworkRevision': self.artwork_state.revision, 'message': 'Artwork task cancelled'})
        for task in self._artwork_jobs:
            if task[0] == 'export':
                task[3].cancel()
        self._artwork_jobs.clear()
        running = getattr(self, '_artwork_running', None)
        self._artwork_started = {key: value for key, value in self._artwork_started.items()
                                 if running is not None and running[0] == 'layout' and key == running[1]}
        if running is not None and running[0] == 'export':
            running[3].cancel()

    def _start_artwork_tasks(self):
        if self._artwork_active:
            return
        if not any(task[0] == 'layout' or task[2].done() for task in self._artwork_jobs):
            return
        self._artwork_active = True
        self._artwork_stop = threading.Event()
        backend = self.backend
        self._artwork_preview_thread = backend._suspend_preview_compile(backend._callbacks)
        backend._busy = backend._local_png_active = True
        backend._emit('busy', True)
        self._next_artwork_task()

    def _next_artwork_task(self):
        backend = self.backend
        task = None
        for candidate in list(self._artwork_jobs):
            if candidate[0] == 'layout' and candidate[1] != (self._document_epoch, self.artwork_state.revision) and not self.artwork_state.is_pinned(candidate[1]):
                self._artwork_jobs.remove(candidate)
                self._artwork_started.pop(candidate[1], None)
                continue
            # 等底图的导出不能阻塞其布局或暂停准备底图的线程。
            if candidate[0] == 'export' and not candidate[2].done():
                continue
            self._artwork_jobs.remove(candidate)
            task = candidate
            break
        if task is None:
            self._artwork_active = False
            self._artwork_running = None
            backend._local_render_thread = None
            backend._busy = backend._local_png_active = False
            backend._emit('busy', False)
            pending, backend._pending_preview = backend._pending_preview, None
            if pending is not None:
                cb, style, force, diagnostics, opening = pending
                backend.compile_preview_scene(cb, style, force=force, _diagnostics=diagnostics, _opening=opening)
            return
        self._artwork_running = task
        stop = self._artwork_stop
        session = backend.session
        def check_cancel():
            if stop.is_set() or session is not backend.session:
                raise RuntimeError('Artwork task cancelled or document changed')
            if task[0] == 'layout' and task[1] != (self._document_epoch, self.artwork_state.revision) and not self.artwork_state.is_pinned(task[1]):
                raise RuntimeError('Artwork layout superseded')
        def progress(percent, message, stage='layout'):
            check_cancel()
            if task[0] == 'layout' and task[5]:
                def notify():
                    if not stop.is_set():
                        self._artwork_progress(task[1], task[6].get('operationId'), stage, percent, message)
                self.scheduler.call_soon(notify)
        progress.supports_stage_progress = True
        def worker():
            value, error, diagnostics = None, None, {}
            temporary = None
            try:
                preview = self._artwork_preview_thread
                while preview is not None and preview.is_alive():
                    check_cancel()
                    preview.join(.05)
                check_cancel()
                if task[0] == 'layout':
                    from .transit_artwork import build_transit_artwork
                    from .city_artwork import build_city_artwork
                    import copy
                    with tempfile.TemporaryDirectory(prefix='csl-artwork-mapping-') as mapping_dir:
                        job = copy.deepcopy(task[2])
                        mapping_path = Path(mapping_dir) / 'poi_direct_mapping.json'
                        mapping_path.write_text(json.dumps(job['_poi_mapping_snapshot']), encoding='utf-8')
                        job['ui']['poi_mapping_override'] = str(mapping_path)
                        build_artwork = build_city_artwork if job['kind'] == 'normal' else build_transit_artwork
                        extra = {'draft': True} if task[5] else {}
                        compute_started = time.perf_counter()
                        value = build_artwork(job, task[3].document, session=task[3],
                            document_epoch=task[1][0], revision=task[1][1], sizing=task[4],
                            diagnostics=diagnostics, progress=progress, **extra)
                        diagnostics['request'] = {'waitSeconds': compute_started-self._artwork_started[task[1]],
                            'readySeconds': time.perf_counter()-self._artwork_started[task[1]], **task[6]}
                elif not task[3].cancelled():
                    from .artwork import render_artwork
                    from .export import export_png
                    snapshot = task[2].result()
                    edge = max(snapshot.to_dict()['canvas'][key] for key in ('width', 'height'))
                    image = render_artwork(snapshot, scale=(task[5] or edge)/edge, aa=task[6])
                    try:
                        check_cancel()
                        parent = Path(task[4]).parent
                        parent.mkdir(parents=True, exist_ok=True)
                        with tempfile.NamedTemporaryFile(prefix='.csl-artwork-', suffix='.png', dir=parent, delete=False) as staged:
                            temporary = Path(staged.name)
                        export_png(image, str(temporary))
                        value = task[4]
                    finally:
                        image.close()
                check_cancel()
            except Exception as exc:
                error = exc
            def finish():
                if task[1][0] == self._document_epoch and not stop.is_set():
                    if task[0] == 'layout':
                        previous = self.artwork_state.snapshot
                        if error is not None:
                            current = self.artwork_state.fail(task[1], str(error))
                            if current and task[5] and self._city_draft is not None:
                                self.event('artworkFailed', {'documentEpoch': task[1][0],
                                    'operationId': task[6].get('operationId'),
                                    'artworkRevision': task[1][1], 'message': str(error)})
                        elif self.artwork_state.accept(value):
                            self.artwork_diagnostics = diagnostics
                            if task[5] and self._city_draft is not None:
                                self._artwork_progress(task[1], task[6].get('operationId'), 'delivering', 0)
                                delivery_started = time.perf_counter()
                                payload = value.to_dict()
                                if (self._city_draft.get('incremental') and previous is not None
                                        and self.artwork_state.presented == previous.identity):
                                    old = previous.to_dict()
                                    payload['baseRevision'] = old['revision']
                                    payload['resources'] = {key: resource for key, resource in payload['resources'].items()
                                                            if key not in old['resources']}
                                    if payload['objects'] == old['objects']:
                                        del payload['objects']
                                diagnostics['delivery'] = {'payloadDictMs': (time.perf_counter()-delivery_started)*1000,
                                                           'artworkBytes': len(value._encoded)}
                                emitting = time.perf_counter()
                                self._artwork_progress(task[1], task[6].get('operationId'), 'delivering', 100)
                                self.event('artworkReady', {'artwork': payload,
                                    'operationId': task[6].get('operationId'),
                                    'diagnostics': {key: diagnostics[key] for key in
                                        ('timings', 'request', 'payload', 'delivery', 'reusedLayout') if key in diagnostics},
                                    **task[2]['_preview_binding']})
                                diagnostics['delivery']['emitMs'] = (time.perf_counter()-emitting)*1000
                    elif not task[3].done():
                        if error is not None:
                            task[3].set_exception(error)
                        else:
                            try:
                                temporary.replace(task[4])
                            except OSError as exc:
                                task[3].set_exception(exc)
                            else:
                                task[3].set_result(value)
                elif task[0] == 'export':
                    task[3].cancel()
                if task[0] == 'layout':
                    if error is not None or task[1] != (self._document_epoch, self.artwork_state.revision):
                        self._artwork_started.pop(task[1], None)
                    if task[5] and self._city_draft is None:
                        for service in ('city.artwork.draft', 'city.artwork.label-placement', 'transit.artwork.base', 'transit.artwork.symbols'):
                            task[3].service_cache.invalidate(service)
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
                self._next_artwork_task()
            self.scheduler.call_soon(finish)
        backend._local_render_thread = threading.Thread(target=worker, name='winui-artwork', daemon=True)
        backend._local_render_thread.start()

    def dispatch(self, method: str, params: dict) -> Any:
        """校验并分派 WinUI 协议命令，统一返回结果或边界错误。"""
        backend = self.backend
        if method == 'restoreCityArtworkPreview':
            self._require_document_epoch(params)
            if self._city_draft is None:
                raise RuntimeError('No city draft is active')
            bundle = getattr(backend, '_preview_bundle', None)
            if bundle is None or getattr(backend, '_preview_bundle_session', None) is not backend.session:
                raise RuntimeError('The city draft preview is no longer available')
            self._scene_ready(bundle, backend._preview_server.manifest_url)
            return {'restored': True}
        if method == 'getCityArtworkCloseup':
            self._require_document_epoch(params)
            config = self._city_draft
            if (config is None or config['epoch'] != self._document_epoch
                    or backend.document is None or backend.session is None):
                raise RuntimeError('No city draft is active')
            if not isinstance(params.get('section'), str):
                raise ValueError('Invalid closeup section')
            for key in ('roadLevel', 'labelType'):
                if params.get(key) is not None and not isinstance(params[key], str):
                    raise ValueError(f'Invalid closeup {key}')
            from .city_artwork_closeup import city_artwork_closeup
            bounds = city_artwork_closeup(backend.session, config['job'], params['section'],
                road_level=params.get('roadLevel'), label_type=params.get('labelType'))
            return {'documentEpoch': self._document_epoch,
                    'bounds': list(bounds) if bounds is not None else None}
        if method == 'getCityArtworkState':
            self._require_document_epoch(params)
            return self._city_draft_state()
        if method == 'getCityArtworkSnapshot':
            self._require_document_epoch(params)
            snapshot = self.artwork_state.snapshot
            if (not self._city_draft or snapshot is None or
                    snapshot.identity != (self._document_epoch, params['artworkRevision'])):
                return None
            return snapshot.to_dict()
        if method in {'beginCityArtwork', 'refreshCityArtwork', 'updateCityArtwork', 'previewCityArtworkTheme'}:
            self._require_document_epoch(params)
            operation_id = params.get('operationId')
            if operation_id is not None and (type(operation_id) is not int or operation_id <= 0):
                raise ValueError('Invalid artwork operation identity')
            if method == 'beginCityArtwork':
                if self._artwork_scene is None:
                    raise RuntimeError('Start the map preview before entering the editor')
                config, rotation, job, canvas = self._city_draft_job(params)
                self._city_draft = {'epoch': self._document_epoch, 'started': time.perf_counter(),
                    'params': config, 'rotation': rotation, 'job': job, 'canvas': canvas,
                    'waiting': False, 'kind': 'initial', 'incremental': params.get('incremental') is True}
            elif self._city_draft is None:
                raise RuntimeError('No city draft is active')
            else:
                if params.get('expectedRevision', self._city_draft['revision']) != self._city_draft['revision']:
                    raise RuntimeError('The city draft has changed; refresh its configuration')
                if method == 'previewCityArtworkTheme' and 'data' not in params:
                    raise ValueError('Missing editor theme data')
                config, rotation, job, canvas = self._city_draft_job(params, self._city_draft)
                self._city_draft = copy.deepcopy(self._city_draft)
                self._city_draft.update(params=config, rotation=rotation, job=job, canvas=canvas)
                if method == 'previewCityArtworkTheme':
                    self._city_draft['themeData'] = copy.deepcopy(job['ui']['style_overrides'][job['style']])
                    self._city_draft['themeId'] = job['style']
                self._city_draft['started'] = time.perf_counter()
                self._city_draft['kind'] = 'update'
                if self._city_draft['waiting']:
                    self._city_draft['baseWaitStarted'] = self._city_draft['started']
            self._city_draft['operationId'] = operation_id
            self._city_draft['revision'] = 0
            self._cancel_city_edit_timer()
            if method != 'beginCityArtwork' and params.get('debounce') is True:
                return self._schedule_city_edit()
            try:
                return self._start_city_draft()
            except Exception as exc:
                self._city_draft['waiting'] = False
                self.artwork_state.fail((self._document_epoch, self.artwork_state.revision), str(exc))
                raise
        if method == 'leaveCityArtwork':
            self._require_document_epoch(params)
            self._cancel_city_edit_timer()
            self._city_draft = None
            self._city_waiting_drafts = {identity: config for identity, config in self._city_waiting_drafts.items()
                                         if self.artwork_state.is_pinned(identity)}
            if backend.session is not None:
                for service in ('city.artwork.draft', 'city.artwork.label-placement', 'transit.artwork.base', 'transit.artwork.symbols'):
                    backend.session.service_cache.invalidate(service)
            self.artwork_state.request()
            self.artwork_state.snapshot = None
            self.artwork_state.presented = None
            self.artwork_diagnostics = {}
            live_layouts = {task[1] for task in self._artwork_jobs if task[0] == 'layout'}
            running = getattr(self, '_artwork_running', None)
            if running is not None and running[0] == 'layout':
                live_layouts.add(running[1])
            self._artwork_started = {key: value for key, value in self._artwork_started.items() if key in live_layouts}
            return {'left': True}
        if method == 'getArtworkDiagnostics':
            return {'status': self.artwork_state.status, 'details': copy.deepcopy(self.artwork_diagnostics)}
        if method == 'artworkPresented':
            self._require_document_epoch(params)
            identity = (params['documentEpoch'], params['artworkRevision'])
            operation_id = self.artwork_diagnostics.get('request', {}).get('operationId')
            if params.get('operationId') is not None and operation_id is not None and params['operationId'] != operation_id:
                return {'accepted': False}
            accepted = self.artwork_state.acknowledge(identity)
            if accepted and identity in self._artwork_started:
                started = self._artwork_started.pop(identity)
                self.artwork_diagnostics['presentation'] = {
                    'requestToPresentedSeconds': time.perf_counter()-started,
                    'operationId': params.get('operationId'), 'operationKind': params.get('operationKind'),
                    'viewer': params.get('performance'), 'hostElapsedMs': params.get('hostElapsedMs'),
                    'hostDelivery': params.get('hostDelivery')}
            return {'accepted': accepted}
        if method == 'exportCityArtwork':
            self._require_document_epoch(params)
            if self._city_draft is None:
                raise RuntimeError('No city draft is active')
            if params.get('expectedRevision', self._city_draft['revision']) != self._city_draft['revision']:
                raise RuntimeError('The city draft has changed; export its latest revision')
            result = self.export_artwork(params['target'], long_edge=params.get('longEdge'), aa=params.get('aa', 1))
            epoch = self._document_epoch
            metadata = {'documentEpoch': epoch}
            if 'requestId' in params:
                metadata.update(requestId=params['requestId'], context=self._city_draft['job'].get('kind', 'normal'),
                                format='PNG', target=params['target'])
            def completed(future):
                if epoch != self._document_epoch:
                    return
                if future.cancelled() or future.exception() is not None:
                    message = 'Export cancelled' if future.cancelled() else str(future.exception())
                    self.event('artworkExportFailed', {**metadata, 'category': 'export', 'message': message})
                else:
                    self.event('artworkExported', {**metadata, 'path': future.result()})
            result.add_done_callback(completed)
            return {'started': True}
        if (self._external_busy or self._saving_export or
                (self._export.request_id and self._export.format == "HTML")) and method in {
            "openDocument", "setTheme", "setLayerVisibility", "applySatelliteRecommendations", "setField", "resetLayer",
            "setLabelChild", "setTransitOption", "setTransitType", "setTransitLines",
            "setMetroOutline", "renderMetro", "resetMetroGenerationOptions",
            "commitThemeDraft", "setMergeOverride", "setFacilitySelection",
            "savePoiMapping", "renderInterchanges", "setRegionSelection",
            "setOutputOption", "setTextIconSettings", "setPoiDensity", "setPoiVisibility", "setDecorativePoiVisibility", "resetFields",
            "resetProfileFields", "setRangeSelection", "compilePreview", "refreshPreview",
            "export", "openBrowserPreview", "clearCache", "setInspectorField", "resetInspectorGroup", "undoInspector", "redoInspector",
        }:
            raise RuntimeError(i18n.tr('ui.winui_engine.dispatch.dfeec4838c'))
        if method == "hello":
            if params.get("protocolVersion") != PROTOCOL_VERSION:
                raise ValueError("incompatible protocol version")
            return {"protocolVersion": PROTOCOL_VERSION}
        if method == "startPreview":
            return {"viewerUrl": backend.start_preview_server()}
        if method == "renderCompatibilityPreview":
            return self._render_compatibility_preview(params)
        if method == "releaseCompatibilityPreview":
            request_id = params.get("requestId")
            if not isinstance(request_id, str):
                raise ValueError("A preview requestId is required")
            path = self._compatibility_files.pop(request_id, None)
            if path is not None:
                path.unlink(missing_ok=True)
            return {"released": path is not None}
        if method == "setPreviewEnabled":
            enabled = params["enabled"]
            if not isinstance(enabled, bool):
                raise ValueError("preview enabled must be a boolean")
            if "documentEpoch" in params:
                self._require_document_epoch(params)
            backend.preview_enabled = enabled
            if not enabled:
                backend.invalidate_preview_compile()
                backend._pending_preview = None
            return {"enabled": enabled}
        if method in WORKFLOW_METHODS:
            if method == "setRegionSelection" and "documentEpoch" in params:
                self._require_document_epoch(params)
            return self.workflows.dispatch(method, params)
        if method == "getSnapshot":
            return self.snapshot()
        if method in {"getBuildingCatalog", "getBuildingSelection", "getBuildingHighlight"}:
            from .building_catalog import BuildingCatalog
            self._require_document_epoch(params)
            if not backend.has_document:
                raise RuntimeError(i18n.tr('ui.winui_engine.dispatch.ae3f8c8a07'))
            use_names = params.get("useGameNames", bool(backend.poi_game_names))
            if type(use_names) is not bool:
                raise ValueError("useGameNames must be a boolean")
            catalog = getattr(self, "_building_catalog", None)
            if (catalog is None or catalog.document is not backend.document
                    or catalog.use_game_names != use_names):
                catalog = self._building_catalog = BuildingCatalog(backend.document, use_names)
            if method == "getBuildingCatalog":
                return {"items": catalog.rows(backend.current_style_data()), "useGameNames": use_names}
            if method == "getBuildingSelection":
                return catalog.selection(params.get("buildingId"), backend.current_style_data())
            return catalog.highlight(params.get("category"))
        if method in {"getObjectInspector", "setInspectorField", "resetInspectorGroup", "undoInspector", "redoInspector"}:
            from .object_inspector import describe, apply, capture, restore
            self._require_document_epoch(params)
            if not backend.has_document:
                raise RuntimeError(i18n.tr('ui.winui_engine.dispatch.ae3f8c8a07'))
            frame = params.get("object")
            if not isinstance(frame, dict):
                raise ValueError("Object selection is required")
            context = str(params.get("context", "map"))
            options = self.transit_options() if context == "transit" else backend.map_transit_options()
            if getattr(self, "_inspector_history_epoch", None) != self._document_epoch:
                self._inspector_history_epoch = self._document_epoch
                self._inspector_undo, self._inspector_redo = [], []
            style_data = self._city_draft.get('themeData') if method == 'getObjectInspector' and self._city_draft is not None else None
            model, _ = describe(backend, frame, context, options, style_data=style_data)
            if method != "getObjectInspector":
                if params.get("settingsRevision") != model["settingsRevision"]:
                    raise RuntimeError(i18n.tr('ui.winui_engine.dispatch.80bd243ed5'))
                if method in {"undoInspector", "redoInspector"}:
                    source = self._inspector_undo if method == "undoInspector" else self._inspector_redo
                    destination = self._inspector_redo if method == "undoInspector" else self._inspector_undo
                    if not source:
                        raise ValueError(i18n.tr('ui.winui_engine.dispatch.5f67276ac1'))
                    record = source[-1]
                    record_options = self.transit_options() if record["context"] == "transit" else backend.map_transit_options()
                    current, _ = describe(backend, record["object"], record["context"], record_options)
                    if current["settingsRevision"] != record["revision"]:
                        raise RuntimeError(i18n.tr('ui.winui_engine.dispatch.8d612deac2'))
                    updated_options = restore(backend, record["before"] if method == "undoInspector" else record["after"], record_options)
                    current, _ = describe(backend, record["object"], record["context"], updated_options)
                    source.pop()
                    record["revision"] = current["settingsRevision"]
                    destination.append(record)
                    for stack in (source, destination):
                        if stack:
                            top = stack[-1]
                            top_options = self.transit_options() if top["context"] == "transit" else backend.map_transit_options()
                            top_model, _ = describe(backend, top["object"], top["context"], top_options)
                            top["revision"] = top_model["settingsRevision"]
                else:
                    if self._inspector_undo:
                        top = self._inspector_undo[-1]
                        top_options = self.transit_options() if top["context"] == "transit" else backend.map_transit_options()
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
                    options = self.transit_options() if context == "transit" else backend.map_transit_options()
                    after = capture(backend, frame, context, options, targets=before)
                    current, _ = describe(backend, frame, context, options)
                    if current["settingsRevision"] != previous_revision:
                        self._inspector_undo.append({"object": copy.deepcopy(frame), "context": context,
                            "before": before, "after": after, "revision": current["settingsRevision"]})
                        self._inspector_undo = self._inspector_undo[-50:]
                        self._inspector_redo.clear()
                options = self.transit_options() if context == "transit" else backend.map_transit_options()
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
            theme_id = params.get('theme', backend.theme_stem)
            if theme_id not in {choice['id'] for choice in backend.theme_choices()}:
                raise ValueError('Unknown theme')
            return self.theme_draft(theme_id=theme_id)
        if method == "newThemeDraft":
            base_id = params.get("baseTheme")
            if base_id not in {stem for _, stem in THEME_CHOICES}:
                raise ValueError("Unknown base theme")
            data = backend.theme_data(base_id)
            data["name"] = backend.suggest_theme_name(tr("gui.theme.personal"))
            return self.theme_draft(data=data, base_id=base_id, new=True)
        if method == "copyThemeDraft":
            data = self.validate_theme_draft(params["data"], backend.current_style_data())
            record = backend.user_themes.get(backend.theme_stem)
            base_id = params.get("baseTheme", record["base"] if record else backend.theme_stem)
            data["name"] = backend.suggest_theme_name(str(data.get("name", "")) + " · " + tr("gui.theme.copy"))
            return self.theme_draft(data=data, base_id=base_id, new=True)
        if method == "validateThemeName":
            from .theme_library import validate_name
            return {"name": validate_name(params["name"], [item["name"] for item in backend.theme_choices() if not item["builtin"] and item["id"] != params.get("theme")])}
        if method == "importThemeDraft":
            import yaml
            path = Path(params["path"]).resolve()
            if path.suffix.lower() not in {".yaml", ".yml"}:
                raise ValueError(tr("gui.theme.error.file"))
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            origin = str(path)
            imported = backend._theme_file_imports.get(origin, {})
            existing = imported.get("theme") if imported.get("sha256") == digest else None
            if existing in backend.user_themes:
                data = backend.theme_data(existing)
            else:
                try:
                    data = yaml.safe_load(raw.decode("utf-8-sig"))
                except (UnicodeError, yaml.YAMLError) as error:
                    raise ValueError(tr("gui.theme.error.file")) from error
                if not isinstance(data, dict):
                    raise ValueError(tr("gui.theme.error.file"))
                from .theme_migration import convert_theme
                data = normalize_theme(self.validate_theme_draft(convert_theme(data, origin)))
                from .theme_library import validate_name
                data["name"] = backend.suggest_theme_name(validate_name(data.get("name", path.stem)))
            token = uuid.uuid4().hex
            self._theme_import = (token, self._document_epoch, origin, digest)
            return {**self.theme_draft(data=data, new=True), "importToken": token}
        if method == "exportThemeDraft":
            from .theme_library import export_file, validate_name
            data = self.validate_theme_draft(params["data"], backend.current_style_data())
            data["name"] = validate_name(data.get("name"))
            export_file(str(params["path"]), data)
            return {"exported": True}
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
            if params.get("settingsRevision") is not None and params["settingsRevision"] != self.theme_revision():
                raise RuntimeError(i18n.tr('ui.winui_engine.dispatch.69d8708938'))
            selected_theme = str(params['theme'])
            embedded = params.get('artwork') is True and self._city_draft is not None
            if selected_theme != backend.theme_stem and not (embedded and selected_theme in {choice['id'] for choice in backend.theme_choices()}):
                raise RuntimeError(i18n.tr('ui.winui_engine.dispatch.43f245906a'))
            data = self.validate_theme_draft(params["data"], backend.theme_data(selected_theme) if embedded else backend.current_style_data())
            if params.get("savePath") is not None:
                from .theme_library import export_file
                export_file(str(params["savePath"]), data)
                return {"applied": False, "saved": False, "exported": True,
                        "theme": backend.theme_stem, "settingsRevision": self.theme_revision()}
            record = backend.user_themes.get(selected_theme)
            creating = params.get("new", False)
            if not isinstance(creating, bool):
                raise ValueError("new must be a boolean")
            name = params.get("name", data.get("name"))
            identity = None if creating or record is None else selected_theme
            base_id = params.get("baseTheme", record.get("legacyBase") if record else selected_theme)
            source = None
            if (token := params.get("importToken")) is not None:
                pending = self._theme_import
                if not creating or pending is None or pending[:2] != (token, self._document_epoch):
                    raise ValueError("Theme import preview expired")
                origin, digest = pending[2:]
                if hashlib.sha256(Path(origin).read_bytes()).hexdigest() != digest:
                    raise ValueError(f"Theme file changed after preview: {origin}")
                imported = backend._theme_file_imports.get(origin, {})
                if imported.get("sha256") == digest and imported.get("theme") in backend.user_themes:
                    identity = imported["theme"]
                source = (origin, digest)
                base_id = None
            if not creating and record is None:
                current = backend.theme_data(selected_theme)
                if name == current.get("name") and normalize_theme(current) == normalize_theme(data):
                    return {"applied": False, "saved": False, "theme": backend.theme_stem,
                            "settingsRevision": self.theme_revision(), "draft": self.theme_draft(theme_id=selected_theme)}
            if record is None and name == backend.theme_display():
                name = backend.suggest_theme_name(name + " · " + tr("gui.theme.personal"))
            with self._theme_preview_lock:
                backend.save_personal_theme(data, identity=identity, base_id=base_id, name=name, source=source)
            self.queue_theme_thumbnail(backend.theme_stem)
            if source is not None:
                self._theme_import = None
            if self._city_draft is not None:
                self._city_draft.pop('themeData', None)
                self._city_draft.pop('themeId', None)
            return {"applied": True, "saved": True, "theme": backend.theme_stem,
                    "settingsRevision": self.theme_revision(), "draft": self.theme_draft()}
        if method == 'deleteTheme':
            from .theme_thumbnail import cache_prefix
            self._require_document_epoch(params)
            if params['settingsRevision'] != self.theme_revision():
                raise ValueError('Theme changed after the deletion confirmation')
            identity = params['theme']
            with self._theme_preview_lock:
                backend.delete_personal_theme(identity)
                self._theme_thumbnail_pending.pop(identity, None)
                try:
                    for path in (backend._settings_dir / 'theme-previews').glob(cache_prefix(identity) + '*.png'):
                        path.unlink()
                except OSError as error:
                    self.event('themeThumbnailFailed', {'theme': identity, 'message': str(error)})
            if self._city_draft is not None:
                self._city_draft.pop('themeData', None)
                self._city_draft.pop('themeId', None)
            return {'deleted': identity, 'draft': self.theme_draft()}
        if method == "getPreviewTheme":
            return backend.preview_theme()
        if method == "clearCache":
            backend.clear_cache()
            return {"cleared": True}
        if method == "openDocument":
            self._city_draft = None
            self._city_waiting_drafts.clear()
            self._artwork_scene = None
            self.cancel_artwork()
            pending = getattr(self, "_rmp_pending", None)
            if self._render_mode == "rmp" and pending:
                self.event("rmpProjectError", {"requestId": pending[2], "message": "源地图已改变，请重新打开编辑器"})
                self._rmp_pending = None
                self._render_mode = None
            self._document_epoch += 1
            self.artwork_state.set_document(self._document_epoch)
            self.event("documentOpening", {"documentEpoch": self._document_epoch})
            self._stats_cache = None
            self._building_catalog = None
            backend._map_line_ids = backend._transit_line_ids = None
            backend._preview_map_transport = True
            self._poi_editor_token = None
            self._poi_editor_mtime = None
            with self._theme_preview_lock:
                self._theme_preview_request_id = None
                self._theme_preview_pending = None
            return {"started": backend.open_file(str(params["path"]), self.callbacks)}
        if method == "saveDraftTransitOptions":
            self._require_document_epoch(params)
            self._draft_transit_options()
            changes = self._city_draft["params"].get("transitChanges", {})
            if changes:
                theme = self._city_draft.get("themeId")
                options = {**backend.transit_options(theme=theme), **changes}
                backend.update_transit_options(False, options, theme=theme)
                changes.clear()
            return {"saved": True}
        if method == "getTransitLines":
            metro = bool(params.get("metro", False))
            options = (self.metro_options() if metro else self._draft_transit_options() if params.get("draft")
                       else backend.map_transit_options() if params.get("map") else self.transit_options())
            style = None
            if params.get('draft'):
                job = self._city_draft['job']
                style = job['ui']['style_overrides'][job['style']]
            lines = backend.transit_line_catalog(color_mode=options.get("line_color_mode"), style=style)
            return {"lines": lines, "documentEpoch": self._document_epoch, "lineIds": options.get("line_ids")}
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
            if params.get("draft"):
                self._draft_transit_options()
                self._stage_transit_option("line_ids", ids)
                return {"lineIds": ids}
            if params.get("map") and not metro:
                backend._map_line_ids = ids
                return {"lineIds": ids}
            options = self.metro_options() if metro else self.transit_options()
            options["line_ids"] = ids
            backend.update_transit_options(metro, options)
            return {"lineIds": ids}
        if method == "setTheme":
            changed = backend.set_theme(str(params["theme"]))
            return {"changed": changed, "snapshot": self.snapshot()}
        if method == "applySatelliteRecommendations":
            self._require_document_epoch(params)
            self._check_satellite_texture(backend.theme_data("satellite"), params.get("maxTextureSize"))
            backend.apply_satellite_recommendations()
            return {"snapshot": self.snapshot(), "mapLayers": backend.map_layer_visibility()}
        if method == "getThemeUpdate":
            return {"notice": self.theme_update_notice(include_handled=True, identity=params.get("theme"))}
        if method == "acknowledgeThemeUpdate":
            self._require_document_epoch(params)
            apply = params["apply"]
            if not isinstance(apply, bool):
                raise ValueError("apply must be a boolean")
            identity = str(params["theme"])
            backend.acknowledge_theme_update(apply, identity, str(params["revision"]), str(params["sourceDigest"]))
            if apply and self._city_draft is not None and self._city_draft.get('themeId') == identity:
                self._city_draft['themeData'] = backend.theme_data(identity)
            return {"snapshot": self.snapshot()}
        if method == "getTextIconSettings":
            target = canonical_profile_target(str(params["target"]))
            from .view_settings import default_view_settings, view_options
            defaults = default_view_settings()["metro" if target == "metro" else "transit"]
            projected = view_options(defaults, target == "metro")
            option_defaults = {key: projected[key] for key in TEXT_ICON_OPTIONS[target]}
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
                    parts = [value]
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
            allowed_options = set(TEXT_ICON_OPTIONS[target])
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
                backend.update_transit_options(target == "metro", current)
            return {"applied": True}
        if method == "getPreviewSettings":
            return {"documentEpoch": self._document_epoch,
                    "performanceDiagnostics": backend.performance_diagnostics_enabled}
        if method == "previewPresented":
            name = str(params["bundleId"])
            if backend._preview_server.acknowledge_bundle(name):
                if params.get("completion") == "base":
                    backend.acknowledge_preview_base(int(params["revision"]))
                if params.get("completion") == "complete":
                    backend.complete_preview_diagnostics(int(params["revision"]), params.get("performance"))
                elif int(params["revision"]) in backend._preview_diagnostics:
                    recorder, _path = backend._preview_diagnostics[int(params["revision"])]
                    recorder.update_metadata(base_viewer=params.get("performance"))
            return {}
        if method == "setPreviewSettings":
            self._require_document_epoch(params)
            enabled = params.get("performanceDiagnostics")
            if not isinstance(enabled, bool):
                raise ValueError(i18n.tr('ui.winui_engine.dispatch.115c909476'))
            backend.set_performance_diagnostics(enabled)
            return {"performanceDiagnostics": enabled}
        if method == "setPoiVisibility":
            self._require_document_epoch(params)
            if not backend.has_document:
                raise RuntimeError(i18n.tr('ui.winui_engine.dispatch.ae3f8c8a07'))
            snapshot = backend.set_poi_visibility_snapshot(params.get("snapshot"))
            layers = dict(backend.layer_visibility)
            layers["poi"] = any(snapshot["icons"].values())
            return {"poiVisibility": snapshot, "layers": layers}
        if method == "setDecorativePoiVisibility":
            self._require_document_epoch(params)
            if not backend.has_document:
                raise RuntimeError(i18n.tr('ui.winui_engine.dispatch.ae3f8c8a07'))
            enabled = params.get("enabled")
            if not isinstance(enabled, bool):
                raise ValueError("enabled must be a boolean")
            backend.set_decorative_poi_visibility(enabled)
            return {"showDecorativePois": backend.show_decorative_pois}
        if method == "setLayerVisibility":
            key = str(params["key"])
            if key not in backend.layer_visibility:
                raise ValueError(f"unknown layer: {key}")
            enabled = bool(params["enabled"])
            if key == "satellite" and enabled:
                self._check_satellite_texture(backend.current_style_data(), params.get("maxTextureSize"))
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
            return {"layers": layers, "mapLayers": backend.map_layer_visibility(), "poiVisibility": visibility}
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
            if params.get("draft"):
                selected = set(self._draft_transit_options()["types"])
                (selected.add if enabled else selected.discard)(key)
                types = [name for name, _ in TRANSIT_TYPES if name in selected]
                self._stage_transit_option("types", types)
                return {"types": types}
            if bool(params.get("context", False)):
                options = self.transit_options()
                selected = set(options["types"])
                (selected.add if enabled else selected.discard)(key)
                options["types"] = [name for name, _ in TRANSIT_TYPES if name in selected]
                backend.update_transit_options(False, options)
                return {"types": options["types"]}
            backend.set_transit_type_enabled(key, enabled)
            return {"types": list(backend.transit_types)}
        if method == "getTransitSchema":
            metro = bool(params.get("metro", False))
            return option_schema(self.metro_options() if metro else backend.map_transit_options() if params.get("map") else self.transit_options())
        if method == "resetMetroGenerationOptions":
            from .view_settings import default_view_settings, view_options
            defaults = view_options(default_view_settings()["metro"], True)
            options = self.metro_options()
            for key in ("merge_enabled", "merge_range", "spacing", "schematic_water",
                        "water_smoothing", "facilities_enabled", "schematic_options"):
                options[key] = defaults[key]
            backend.update_transit_options(True, options)
            return {"options": self.metro_options()}
        if method == "setTransitOption":
            metro = bool(params.get("metro", False))
            key = str(params["key"])
            if params.get("draft"):
                options = self._draft_transit_options()
                value = validate_option(options, key, params["value"])
                self._stage_transit_option(key, value)
                return {"options": self._draft_transit_options()}
            if params.get("map") and not metro:
                options = backend.map_transit_options()
                validate_option(options, key, params["value"])
                backend.set_map_transit_option(key, params["value"])
                return {"options": backend.map_transit_options()}
            options = self.metro_options() if metro else self.transit_options()
            validate_option(options, key, params["value"])
            options[key] = params["value"]
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
                if key not in {"size", "rotation", "htmlQuality", "elementScale"}:
                    raise ValueError(f"unknown output profile field: {key}")
                changes = {key: value}
                if key == 'size' and params.get('elementScale') is not None:
                    changes['elementScale'] = params['elementScale']
                profile = backend.set_output_profile(target, changes)
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
            if "map" in params:
                backend._preview_map_transport = bool(params["map"])
            if not backend.has_document:
                return {"started": False}
            if backend.preview_needs_rebuild():
                return {"started": backend.compile_preview_scene(self.callbacks),
                        "theme": backend.preview_theme()}
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
                if not backend.generate_rmp_in_engine(job, self.callbacks):
                    raise ValueError("无法启动地铁编辑快照生成")
            except Exception:
                self._render_mode = None
                self._rmp_pending = None
                raise
            return {"started": True}
        if method == "renderMetro":
            raise ValueError("原生地铁 PNG 已退役，请使用 RMP 工程编辑器生成与输出")
        if method == "getExportProfile":
            return self._export_profile(params)
        if method in {"export", "openBrowserPreview"}:
            self._require_document_epoch(params)
            if method == "openBrowserPreview":
                if params.get("context", "normal") not in {"normal", "transit"}:
                    raise ValueError("Browser preview supports normal and transit maps")
                params = {"documentEpoch": params["documentEpoch"],
                          "requestId": params.get("requestId"),
                          "context": params.get("context", "normal"), "format": "HTML",
                          "target": str(Path(self._preview_files.name) / f"map-{uuid.uuid4().hex}.html")}
            request = ExportRequest.from_dict(params)
            capabilities = self._format_capabilities(request.context)
            if request.format not in capabilities:
                raise ValueError("unsupported export format")
            if request.context == ContextMode.METRO and request.format != "RMG":
                raise ValueError("请在 RMP 工程编辑器中输出已编辑工程")
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
            if method == "export":
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
                "web_result": lambda path, report: self._export_completed(path, export_id, road_order=report),
            }
            try:
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
            if self._artwork_active:
                self.cancel_artwork()
                return {'cancelled': False, 'cancelRequested': True}
            if self._export.request_id and self._export.format == "HTML":
                backend.cancel_export()
                return {"cancelled": False, "cancelRequested": True}
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
            return {"cancelled": True}
        if method == "close":
            self.cancel_artwork()
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
            method = str(frame.get("method", ""))
            operation = "metro" if method in {"prepareRmpProject", "renderMetro"} else "export" if method == "export" else "open" if method in {"open", "openDocument"} else "operation"
            params = frame.get("params", {})
            metadata = {"documentEpoch": self._document_epoch, "rpcId": request_id}
            if isinstance(params, dict):
                for key in ("requestId", "documentEpoch", "context", "format", "target"):
                    if key in params:
                        metadata[key] = params[key]
                if "documentEpoch" in params:
                    metadata["currentDocumentEpoch"] = self._document_epoch
            self.send({"id": request_id, "error": {"code": type(exc).__name__,
                **error_payload(exc, operation=operation, stage=method, **metadata)}})

    def _read(self) -> None:
        for line in self.input:
            try:
                frame = json.loads(line)
                if not isinstance(frame, dict):
                    raise ValueError("request must be an object")
                self.requests.put(frame)
            except Exception as exc:  # noqa: BLE001
                self.event("protocolError", error_payload(exc, stage="readProtocol"))
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
            return 1 if self._output_failed else 0
        finally:
            with self._theme_preview_lock:
                self.running = False
                self._theme_thumbnail_pending.clear()
                self._theme_preview_request_id = None
                self._theme_preview_pending = None
            self.cancel_artwork()
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
