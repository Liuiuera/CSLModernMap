"""从实际地铁生成结果导出原生 Rail Map Painter 工程。"""
from __future__ import annotations

import json
from .model import ExternalExportRequest, ExternalExportResult, ExportSnapshot, safe_name, unique_directory
from .rmp_graph import build_graph

def build_rmp_save(snapshot: ExportSnapshot, request: ExternalExportRequest) -> dict:
    scene = request.rmp_scene
    if scene is None:
        if request.line_ids is not None and not any(
            line.id in request.line_ids for line in snapshot.document.transit_lines
        ):
            return build_graph({"stations": [], "lines": []})
        from ..gui_render_worker import run_transit_task
        if request.render_job is None:
            options = {"types": ["Metro", "Train", "Monorail"]}
            options.update(pure=True, capture_rmp=True, stations=True)
            if request.line_ids is not None:
                options["line_ids"] = list(request.line_ids)
            job = {"file": "rmp-export", "ui": dict(request.render_ui or {"rotation": 0}),
                   "style": request.theme_id, "types": list(options["types"]),
                   "opts": options, "layers": [], "aa": 1}
        else:
            import copy
            job = copy.deepcopy(dict(request.render_job))
            job["opts"]["capture_rmp"] = True
        result = run_transit_task(job, mapdata_cache={job["file"]: snapshot.document})
        scene = result["rmp_snapshot"]
    if scene is None:
        raise ValueError("当前地铁成图没有可转换的矢量快照")
    return build_graph(scene)


def export_rmp(snapshot: ExportSnapshot, request: ExternalExportRequest) -> ExternalExportResult:
    project = build_rmp_save(snapshot, request)
    directory = unique_directory(request.output_root, snapshot.city_name + "-RailMapPainter")
    path = directory / f"{safe_name(snapshot.city_name)}-network.rmp.json"
    path.write_text(json.dumps(project, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    warnings = () if project["graph"]["edges"] else ("没有可导出的轨道线路",)
    return ExternalExportResult(directory, (path,), warnings)
