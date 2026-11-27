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
        from ..rmp_generation import generate_rmp
        from .rmg import _color
        options = {"types": ["Metro", "Train", "Monorail"]}
        if request.render_job is not None:
            options.update(request.render_job.get("opts", {}))
        if request.line_ids is not None:
            options["line_ids"] = list(request.line_ids)
        scene = generate_rmp(snapshot.document, options)
        # The standalone Qt writer needs explicit Graphology styles. The embedded
        # RMP editor applies its registered defaults when it consumes cmmDraft.
        colors = {line.id: _color(line) for line in snapshot.document.transit_lines}
        for row in scene["lines"]:
            row.update(color=colors[row["source_line_id"]], width=5)
        for row in scene["stations"]:
            row.update(visible=True, label={"x": row["x"], "y": row["y"]})
        scene.update(water_color=(116, 190, 222), water_alpha=84)
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
