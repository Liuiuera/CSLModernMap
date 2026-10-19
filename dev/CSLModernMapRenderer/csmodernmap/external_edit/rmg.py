"""写出 Rail Map Generator 单线路工程。

字段结构参考 Rail Map Generator 6.0.4 的常量和默认参数，不捆绑其源代码。"""
from __future__ import annotations

import json
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

from .. import __version__
from .model import ExternalExportRequest, ExternalExportResult, ExportSnapshot, safe_name, unique_directory

RMG_TARGET_VERSION = "6.0.4"
RAIL_KINDS = {"METRO", "TRAIN", "MONORAIL"}


def _color(line) -> str:
    color = getattr(line, "user_color", None)
    if color and len(color) >= 3:
        return "#%02X%02X%02X" % tuple(max(0, min(255, int(v))) for v in color[:3])
    return "#E2231A"


def _station_info(name: str, number: int = 0) -> dict:
    return {
        "localisedName": {"zh": name, "en": name},
        "num": f"{number:02d}", "services": ["local"],
        "parents": [], "children": [],
        "transfer": {"groups": [{}], "tick_direc": "r", "paid_area": True},
        "loop_pivot": False, "one_line": True,
        "int_padding": 355, "character_spacing": 75,
    }


def _route_graph(snapshot: ExportSnapshot, line_id: int) -> tuple[dict[str, set[str]], bool, list[str]]:
    topology = snapshot.transit.physical_topologies[line_id]
    graph: dict[str, set[str]] = defaultdict(set)
    for left, right in topology.edges:
        graph[left].add(right)
        graph[right].add(left)
    sequence = list(topology.simple_sequence or topology.sequence)
    if len(sequence) > 1 and sequence[0] == sequence[-1]:
        sequence.pop()
    return dict(graph), topology.kind == "cycle", sequence


def _orient_graph(graph: dict[str, set[str]], closed: bool,
                  preferred: list[str] | None = None) -> tuple[list[tuple[str, str]], str, bool]:
    if len(graph) < 2 or any(len(neighbors) > 3 for neighbors in graph.values()):
        raise ValueError("线路无有效站点或存在不支持的复杂分支")
    edges = sum(map(len, graph.values())) // 2
    loop = closed or (edges == len(graph) and all(len(v) == 2 for v in graph.values()))
    if loop:
        if edges != len(graph) or not all(len(v) == 2 for v in graph.values()):
            raise ValueError("无法确定环线拓扑")
        pivot = preferred[0] if preferred and preferred[0] in graph else min(graph)
        current, previous = pivot, None
        ordered = []
        while True:
            choices = sorted(graph[current] - ({previous} if previous else set()))
            if previous is None and preferred and len(preferred) > 1 and preferred[1] in choices:
                choices.remove(preferred[1])
                choices.insert(0, preferred[1])
            following = choices[0] if choices else pivot
            ordered.append((current, following))
            if following == pivot:
                break
            previous, current = current, following
            if len(ordered) > len(graph):
                raise ValueError("无法确定环线拓扑")
        return ordered, pivot, True
    if edges != len(graph) - 1:
        raise ValueError("无法确定线路拓扑")
    endpoints = sorted(key for key, neighbors in graph.items() if len(neighbors) == 1)
    if not endpoints:
        raise ValueError("无法确定线路端点")
    root = preferred[0] if preferred and preferred[0] in endpoints else endpoints[0]
    result: list[tuple[str, str]] = []
    seen = {root}
    queue = deque([root])
    while queue:
        parent = queue.popleft()
        children = sorted(graph[parent] - seen)
        if len(children) > 2:
            raise ValueError("不支持的复杂分支")
        for child in children:
            seen.add(child)
            result.append((parent, child))
            queue.append(child)
    if len(seen) != len(graph):
        raise ValueError("线路拓扑不连通")
    return result, root, False


def build_rmg_param(snapshot: ExportSnapshot, line) -> dict:
    """把线路和站点转换为 RMG 工程参数。"""
    graph, closed, sequence = _route_graph(snapshot, line.id)
    edges, root, loop = _orient_graph(graph, closed, sequence)
    station_by_id = {str(station.id): station for station in snapshot.stations}
    lines_by_id = {other.id: other for other in snapshot.document.transit_lines}
    stations = {}
    for index, station_id in enumerate(sorted(graph), 1):
        source = station_by_id[station_id]
        info = _station_info(source.name.text or f"Station {index}", index)
        other_lines = [lines_by_id[value] for value in source.serving_line_ids
                       if value != line.id and value in lines_by_id]
        if other_lines:
            info["transfer"]["groups"] = [{"lines": [
                {"theme": ["other", "other", _color(other), "#FFFFFF"],
                 "name": [other.name or str(other.id), other.name or str(other.id)]}
                for other in other_lines]}]
        stations[station_id] = info
    # RMG 的图遍历总从线路起点开始并在线路终点结束，环线也如此。
    if loop:
        edges = edges[:-1]
    for parent, child in edges:
        stations[parent]["children"].append(child)
        stations[child]["parents"].append(parent)
    if loop:
        stations[root]["loop_pivot"] = True
    start = _station_info("LEFT END")
    end = _station_info("RIGHT END")
    start["children"] = [root]
    stations[root]["parents"].insert(0, "linestart")
    leaves = sorted(station_id for station_id, value in stations.items()
                    if not value["children"])
    if len(leaves) > 2:
        raise ValueError("超过两个支线终点，无法可靠映射到 RMG 拓扑")
    for leaf in leaves:
        stations[leaf]["children"].append("lineend")
    end["parents"] = leaves
    stations = {"linestart": start, **stations, "lineend": end}
    for info in stations.values():
        if len(info["children"]) == 2:
            info["branch"] = {"right": ["through", info["children"][1]]}
        if len(info["parents"]) == 2:
            info.setdefault("branch", {})["left"] = ["through", info["parents"][1]]
    name = line.name or f"Line {line.id}"
    return {
        "svgWidth": {key: 1200 for key in
                     ("destination", "runin", "railmap", "indoor", "platform")},
        "svg_height": 300, "style": "shmetro" if loop else "mtr", "y_pc": 50, "padding": 10,
        "branchSpacingPct": 33, "direction": "l", "platform_num": "1",
        "theme": ["other", "other", _color(line), "#FFFFFF"],
        "line_name": [name, name], "current_stn_idx": root,
        "stn_list": stations,
        "namePosMTR": {"isStagger": True, "isFlip": True},
        "customiseMTRDest": {"isLegacy": False, "terminal": False},
        "line_num": str(line.id), "spanLineNum": True,
        "psd_num": "1", "psdLabel": "platform", "coachNum": "1",
        "info_panel_type": "sh" if loop else "gz1", "direction_gz_x": 40,
        "direction_gz_y": 70, "coline": {}, "loop": loop,
        "loop_info": {"bank": True, "left_and_right_factor": 1,
                      "bottom_factor": 1},
        "version": RMG_TARGET_VERSION,
    }


def export_rmg(snapshot: ExportSnapshot, request: ExternalExportRequest) -> ExternalExportResult:
    directory = unique_directory(request.output_root, snapshot.city_name + "-RailMapGen")
    files: list[Path] = []
    warnings: list[str] = []
    records: list[dict] = []
    allowed = set(request.line_ids) if request.line_ids is not None else None
    for line in sorted(snapshot.document.transit_lines, key=lambda item: item.id):
        if line.kind.value not in RAIL_KINDS or (allowed is not None and line.id not in allowed):
            continue
        try:
            param = build_rmg_param(snapshot, line)
        except ValueError as exc:
            warnings.append(f"{line.name or line.id}: {exc}")
            continue
        filename = f"{safe_name(line.name or str(line.id))}-{line.id}.rmg.json"
        path = directory / filename
        path.write_text(json.dumps(param, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        files.append(path)
        records.append({"line_id": line.id, "name": line.name, "file": filename})
    if not records:
        warnings.append("没有可导出的轨道线路；查看 manifest.json 中的逐线原因")
    manifest = {
        "schema_version": "cmm-external-v1", "generator": "CSLModernMap",
        "generator_version": __version__, "source_game": snapshot.source_game,
        "city_name": snapshot.city_name,
        "cmm_schema_version": snapshot.document.schema_version,
        "rmg_target_version": RMG_TARGET_VERSION,
        "export_time": datetime.now(timezone.utc).isoformat(),
        "lines": records, "warnings": warnings,
    }
    manifest_path = directory / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    files.append(manifest_path)
    return ExternalExportResult(directory, tuple(files), tuple(warnings))
