"""为 WinUI 提供不依赖 Qt 的工作流适配。"""
from __future__ import annotations

from . import i18n

import copy
import uuid



METHODS = {"getRegionCatalog", "setRegionSelection", "getDiagnostics",
           "renderInterchanges", "getUiMetadata", "setLanguage", "getThemePalettes"}


class WinuiWorkflows:
    def __init__(self, server):
        self.server = server

    def dispatch(self, method, params):
        backend = self.server.backend
        if method == "getRegionCatalog":
            return {**backend.region_catalog(), "district_ids": sorted(backend.district_ids or ()), "park_ids": sorted(backend.park_ids or ())}
        if method == "setRegionSelection":
            if not backend.set_region_selection(params["group"], params["ids"]):
                raise ValueError("unknown region group")
            return {"updated": True}
        if method == "getDiagnostics":
            return {"details": copy.deepcopy(backend.last_diagnostics)}
        if method == "renderInterchanges":
            if backend.busy or self.server._external_busy or self.server._export.target or not backend.has_document:
                return {"started": False}
            request = dict(params.get("request") or {})
            if not 1 <= int(request.get("top_count", 3)) <= 24 or not 256 <= int(request.get("tile_size", 1024)) <= 4096:
                raise ValueError(i18n.tr('ui.winui_workflows.dispatch.155590bba2'))
            self.server._export.request_id = str(params.get("requestId") or uuid.uuid4().hex)
            self.server._export.target = str(params["target"])
            try:
                started = backend.render_interchanges(self.server.callbacks, request=request)
            except Exception:
                self.server._clear_export()
                raise
            if not started:
                self.server._clear_export()
            return {"started": started}
        if method == "getThemePalettes":
            from .guir_backend import THEME_CHOICES
            from .theme_palette import theme_palette
            return {stem: theme_palette(stem) for _, stem in THEME_CHOICES}
        if method in {"setLanguage", "getUiMetadata"}:
            from csmodernmap.ui_palette import LIGHT, DARK
            from csmodernmap.command_metadata import CANONICAL_IDS, COMMANDS
            if method == "setLanguage":
                if not backend.set_language(params["language"]):
                    raise RuntimeError(i18n.tr('ui.winui_workflows.dispatch.2712805913'))
                i18n.set_lang(params["language"])
            return {"language": i18n.LANG, "languages": list(i18n.AVAILABLE_LANGS),
                    "appearancePalettes": {"light": dict(LIGHT.colors), "dark": dict(DARK.colors)},
                    "actions": CANONICAL_IDS,
                    "commands": {key: {"label": i18n.tr(value["label_key"]), "shortcut": value["shortcut"]} for key, value in COMMANDS.items()}}
        raise ValueError("unknown workflow")
