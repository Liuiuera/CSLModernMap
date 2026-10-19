"""管理地铁示意图布局的轻量编辑覆盖层。

源存档始终是权威数据；此处只保存用户的手动修正，以便在重新自动布局后重新应用。"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping


SCHEMA = "cslmodernmap.metro-edits"
VERSION = 1
STATION_TYPES = {"auto", "normal", "interchange", "terminal"}


def station_key(node_ids: Iterable[int]) -> str:
    """从源网络节点构造稳定的站点引用。"""
    values = sorted({int(value) for value in node_ids})
    if not values:
        raise ValueError("a station reference needs at least one source node")
    return "nodes:" + ",".join(str(value) for value in values)


def snap_cell(x: float, y: float) -> tuple[int, int]:
    """将指针位置吸附到最近的示意图网格。"""
    if not math.isfinite(float(x)) or not math.isfinite(float(y)):
        raise ValueError("grid coordinates must be finite")
    return int(round(float(x))), int(round(float(y)))


@dataclass
class EditIssue:
    code: str
    message: str
    severity: str = "warning"
    station_keys: tuple[str, ...] = ()
    line_ids: tuple[int, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "station_keys": list(self.station_keys),
            "line_ids": list(self.line_ids),
        }


@dataclass
class StationEdit:
    source_nodes: tuple[int, ...]
    cell: tuple[int, int] | None = None
    station_type: str = "auto"

    @property
    def key(self) -> str:
        return station_key(self.source_nodes)

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "source_nodes": list(self.source_nodes),
            "station_type": self.station_type,
        }
        if self.cell is not None:
            value["cell"] = list(self.cell)
        return value


@dataclass
class LineEdit:
    line_id: int
    loop: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"line_id": self.line_id}
        if self.loop is not None:
            value["loop"] = self.loop
        return value


@dataclass
class SharedSegmentEdit:
    start: str
    end: str
    line_ids: tuple[int, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end,
                "line_ids": list(self.line_ids)}


@dataclass
class CompiledEdits:
    station_cells: dict[Any, tuple[int, int]] = field(default_factory=dict)
    station_types: dict[Any, str] = field(default_factory=dict)
    line_loops: dict[int, bool] = field(default_factory=dict)
    shared_segments: list[tuple[Any, Any, tuple[int, ...]]] = field(default_factory=list)
    issues: list[EditIssue] = field(default_factory=list)


@dataclass
class MetroEditDocument:
    """只保存手动地铁布局决定的版本化覆盖数据。"""

    stations: dict[str, StationEdit] = field(default_factory=dict)
    lines: dict[int, LineEdit] = field(default_factory=dict)
    shared_segments: list[SharedSegmentEdit] = field(default_factory=list)
    source: str = ""

    def move_station(self, source_nodes: Iterable[int], x: float, y: float) -> None:
        key = station_key(source_nodes)
        edit = self.stations.get(key) or StationEdit(
            tuple(sorted({int(value) for value in source_nodes})))
        edit.cell = snap_cell(x, y)
        self.stations[key] = edit

    def set_station_type(self, source_nodes: Iterable[int], value: str) -> None:
        kind = str(value).strip().lower()
        if kind not in STATION_TYPES:
            raise ValueError(f"unknown station type: {value!r}")
        key = station_key(source_nodes)
        edit = self.stations.get(key) or StationEdit(
            tuple(sorted({int(item) for item in source_nodes})))
        edit.station_type = kind
        self.stations[key] = edit

    def set_line_loop(self, line_id: int, value: bool | None) -> None:
        self.lines[int(line_id)] = LineEdit(int(line_id), value)

    def set_shared_segment(self, start_nodes: Iterable[int], end_nodes: Iterable[int],
                           line_ids: Iterable[int]) -> None:
        start, end = station_key(start_nodes), station_key(end_nodes)
        ids = tuple(sorted({int(value) for value in line_ids}))
        replacement = SharedSegmentEdit(start, end, ids)
        pair = frozenset((start, end))
        self.shared_segments = [item for item in self.shared_segments
                                if frozenset((item.start, item.end)) != pair]
        if ids:
            self.shared_segments.append(replacement)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "version": VERSION,
            "source": self.source,
            "stations": [self.stations[key].to_dict()
                         for key in sorted(self.stations)],
            "lines": [self.lines[key].to_dict() for key in sorted(self.lines)],
            "shared_segments": [item.to_dict() for item in self.shared_segments],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "MetroEditDocument":
        value = value or {}
        if value and value.get("schema", SCHEMA) != SCHEMA:
            raise ValueError("not a CSLModernMap metro edit document")
        if int(value.get("version", VERSION)) != VERSION:
            raise ValueError(f"unsupported metro edit version: {value.get('version')}")
        result = cls(source=str(value.get("source", "") or ""))
        for raw in value.get("stations", ()):
            nodes = tuple(sorted({int(item) for item in raw.get("source_nodes", ())}))
            key = station_key(nodes)
            raw_cell = raw.get("cell")
            cell = snap_cell(*raw_cell) if raw_cell is not None else None
            kind = str(raw.get("station_type", "auto")).strip().lower()
            if kind not in STATION_TYPES:
                raise ValueError(f"unknown station type: {kind!r}")
            result.stations[key] = StationEdit(nodes, cell, kind)
        for raw in value.get("lines", ()):
            line_id = int(raw["line_id"])
            loop = raw.get("loop")
            if loop is not None and not isinstance(loop, bool):
                raise ValueError("line loop override must be true, false, or null")
            result.lines[line_id] = LineEdit(line_id, loop)
        for raw in value.get("shared_segments", ()):
            result.shared_segments.append(SharedSegmentEdit(
                str(raw["start"]), str(raw["end"]),
                tuple(sorted({int(item) for item in raw.get("line_ids", ())})),
            ))
        return result

    def compile(self, merged_stations: Iterable[Any], transports: Iterable[Any]) -> CompiledEdits:
        """针对新加载的存档解析源引用；缺失或过期引用只报告问题，不阻止地图渲染。"""
        result = CompiledEdits()
        station_ids: dict[str, Any] = {}
        station_by_node: dict[int, Any] = {}
        for station in merged_stations:
            keys = getattr(station, "member_stop_keys", None) or ()
            if keys:
                nodes = [int(str(key).split(":", 1)[0]) for key in keys]
            else:
                nodes = [int(stop.node_id) for stop in getattr(station, "stops", ())]
            if nodes:
                station_ids[station_key(nodes)] = station.id
                for node in nodes:
                    station_by_node[int(node)] = station.id
        transport_rows = {int(line.id): line for line in transports}
        line_ids = set(transport_rows)
        line_edges: dict[int, set[frozenset[Any]]] = {}
        for line_id, line in transport_rows.items():
            groups = []
            for stop in getattr(line, "stops", ()):
                group = station_by_node.get(int(stop.node_id))
                if group is not None and (not groups or groups[-1] != group):
                    groups.append(group)
            line_edges[line_id] = {
                frozenset((a, b)) for a, b in zip(groups, groups[1:])
                if a != b
            }
        for key, edit in self.stations.items():
            group_id = station_ids.get(key)
            if group_id is None:
                result.issues.append(EditIssue(
                    "station_missing", "编辑引用的站点已不在当前地图中",
                    station_keys=(key,)))
                continue
            if edit.cell is not None:
                # 自动合并未识别的换乘站可手动共址；保留全部锚点，路由器支持同格站点间零长度连接。
                result.station_cells[group_id] = edit.cell
            if edit.station_type != "auto":
                result.station_types[group_id] = edit.station_type
        for line_id, edit in self.lines.items():
            if line_id not in line_ids:
                result.issues.append(EditIssue(
                    "line_missing", "编辑引用的线路已不在当前地图中",
                    line_ids=(line_id,)))
            elif edit.loop is not None:
                result.line_loops[line_id] = edit.loop
        for item in self.shared_segments:
            start, end = station_ids.get(item.start), station_ids.get(item.end)
            pair = frozenset((start, end))
            valid_lines = tuple(line for line in item.line_ids
                                if line in line_ids
                                and pair in line_edges.get(line, set()))
            if start is None or end is None or start == end or len(valid_lines) < 2:
                result.issues.append(EditIssue(
                    "shared_segment_stale", "共线段必须由至少两条真实相邻线路经过，已保留但本次不应用",
                    station_keys=(item.start, item.end), line_ids=item.line_ids))
                continue
            result.shared_segments.append((start, end, valid_lines))
        return result


def load_edit_document(path: str | Path) -> MetroEditDocument:
    with Path(path).open("r", encoding="utf-8") as stream:
        return MetroEditDocument.from_dict(json.load(stream))


def save_edit_document(document: MetroEditDocument, path: str | Path) -> None:
    """原子保存编辑覆盖层，不修改源地图。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(document.to_dict(), stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    os.replace(temporary, target)


def build_editor_scene(layout: Any, merged_stations: Iterable[Any],
                       transports: Iterable[Any],
                       issues: Iterable[EditIssue] = ()) -> dict[str, Any]:
    """返回供 SVG 或 Canvas 编辑器使用的紧凑矢量场景，保留布局坐标和稳定源节点引用。"""
    stations = list(merged_stations)
    lines = list(transports)
    source_mode = getattr(layout, "fallback_strategy", "requested")
    coordinate_scale = (max(float(getattr(layout, "spacing_m", 1.0)), 1.0)
                        if source_mode == "geographic" else 1.0)

    def editor_point(point: Iterable[float]) -> list[float]:
        x, y = point
        return [float(x) / coordinate_scale, float(y) / coordinate_scale]
    group_keys: dict[Any, str] = {}
    station_rows = []
    for station in stations:
        raw_keys = getattr(station, "member_stop_keys", None) or ()
        nodes = ([int(str(key).split(":", 1)[0]) for key in raw_keys]
                 if raw_keys else
                 [int(stop.node_id) for stop in getattr(station, "stops", ())])
        if not nodes or station.id not in layout.node_grid:
            continue
        key = station_key(nodes)
        group_keys[station.id] = key
        display_name = getattr(station, "name", "")
        name = str(getattr(display_name, "text", display_name) or "")
        if not name:
            name = next((str(getattr(stop, "name", "") or "")
                         for stop in getattr(station, "stops", ())
                         if getattr(stop, "name", "")), "")
        name_is_default = (not name or name == key
                           or name.lower().startswith("nodes:"))
        serving = getattr(station, "serving_line_ids", None)
        if serving is None:
            serving = [int(line.id) for line in getattr(station, "lines", ())]
        serving = sorted({int(value) for value in serving})
        station_rows.append({
            "key": key,
            "source_nodes": sorted(set(nodes)),
            "name": name,
            "name_is_default": name_is_default,
            "cell": editor_point(layout.node_grid[station.id]),
            "station_type": "interchange" if len(serving) >= 2 else "normal",
            "line_ids": serving,
        })

    line_rows = []
    for line in lines:
        line_object_id = id(line)
        parts = [layout.routes.get(line_object_id, ()),
                 *layout.route_parts.get(line_object_id, ())]
        part_indices = [layout.station_indices.get(line_object_id, ()),
                        *layout.part_station_indices.get(line_object_id, ())]
        chains = []
        for chain in layout.source_chains.get(int(line.id), ()):
            chains.append([group_keys[group] for group in chain
                           if group in group_keys])
        color = getattr(line, "color", None)
        if isinstance(color, (tuple, list)):
            color = list(color)
        line_rows.append({
            "id": int(line.id),
            "name": str(getattr(line, "name", "") or ""),
            "type": str(getattr(line, "type", "") or ""),
            "color": color,
            "loop": any(len(chain) > 2 and chain[0] == chain[-1]
                        for chain in chains),
            "chains": chains,
            "paths": [
                {
                    "points": [editor_point(point) for point in part],
                    "anchors": [
                        {"index": int(index), "station": chain[pos]}
                        for pos, index in enumerate(
                            part_indices[part_number]
                            if part_number < len(part_indices) else ())
                        if pos < len(chain) and int(index) < len(part)
                    ],
                }
                for part_number, part in enumerate(parts)
                for chain in [chains[part_number]
                              if part_number < len(chains) else []]
                if part
            ],
        })
    diagnostics = [item.to_dict() for item in issues]
    diagnostics.extend({
        "code": "layout_fallback",
        "message": str(message),
        "severity": "warning",
        "station_keys": [],
        "line_ids": [],
    } for message in getattr(layout, "layout_failures", ()))
    return {
        "schema": "cslmodernmap.metro-editor-scene",
        "version": 1,
        "coordinate_space": "grid",
        "source_layout_mode": source_mode,
        "stations": station_rows,
        "lines": line_rows,
        "diagnostics": diagnostics,
    }
