"""定义以站点为锚点的附属数据协议。

附属文件只保存规则，不保存派生站点标识或快照；每次都根据当前地图标准化的 StopKey 集合解析引用。"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable, Mapping

from ...cmm import DisplayName, Station, StationMergeParams, Stop, StopKey, TransitKind, normalize_stop_kind, normalize_transit_kind


SCHEMA_VERSION = "station-fix/v1"
SCHEMA_SCOPE = "stop-anchored"
EDIT_KINDS = frozenset({
    "merge_stops",
    "keep_separate",
    "set_transfers",
    "rename_stop",
})


@dataclass(frozen=True)
class StationSidecarIssue:
    """保存便于阅读的校验或应用诊断。"""

    code: str
    message: str
    edit_id: str = ""
    severity: str = "error"

    def __str__(self) -> str:
        prefix = f"{self.edit_id}: " if self.edit_id else ""
        return f"[{self.code}] {prefix}{self.message}"


class StationSidecarError(ValueError):
    """附属文件无法原子应用时抛出的错误。"""

    def __init__(self, message: str, issues: Iterable[StationSidecarIssue] = ()):
        self.issues = tuple(issues)
        detail = "; ".join(str(issue) for issue in self.issues)
        super().__init__(f"{message}{(': ' + detail) if detail else ''}")


@dataclass(frozen=True)
class StationFix:
    id: str
    kind: str
    stop_keys: tuple[str, ...] = ()
    name: str | None = None
    add_group_ids: tuple[int, ...] = ()
    remove_group_ids: tuple[int, ...] = ()
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"id": self.id, "kind": self.kind}
        if self.kind == "rename_stop":
            result["stop_key"] = self.stop_keys[0]
        else:
            result["stop_keys"] = list(self.stop_keys)
        if self.name is not None:
            result["name"] = self.name
        if self.add_group_ids:
            result["add_group_ids"] = list(self.add_group_ids)
        if self.remove_group_ids:
            result["remove_group_ids"] = list(self.remove_group_ids)
        if self.note:
            result["note"] = self.note
        return result


@dataclass(frozen=True)
class StationSidecar:
    schema_version: str
    scope: str
    source_map: str
    edits: tuple[StationFix, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scope": self.scope,
            "source_map": self.source_map,
            "edits": [edit.to_dict() for edit in self.edits],
        }

    @property
    def blocked_pairs(self) -> tuple[tuple[str, str], ...]:
        pairs = {
            tuple(sorted(pair))
            for edit in self.edits
            if edit.kind == "keep_separate"
            for pair in combinations(edit.stop_keys, 2)
        }
        return tuple(sorted(pairs))

    @property
    def forced_merges(self) -> tuple[tuple[str, ...], ...]:
        groups = {
            tuple(sorted(edit.stop_keys))
            for edit in self.edits if edit.kind == "merge_stops"
        }
        return tuple(sorted(groups))


@dataclass(frozen=True)
class StationFixApplication:
    stations: tuple[Station, ...]
    issues: tuple[StationSidecarIssue, ...] = ()


def _issue(code: str, message: str, edit_id: str = "",
           *, severity: str = "error") -> StationSidecarIssue:
    return StationSidecarIssue(
        code=code, message=message, edit_id=edit_id, severity=severity,
    )


def _canonical_stop_key(value: Any, *, edit_id: str = "") -> str:
    if not isinstance(value, str):
        raise StationSidecarError(
            "侧车中的 Stop 引用必须是字符串",
            (_issue("INVALID_STOP_KEY", repr(value), edit_id),),
        )
    raw = value.strip()
    node_text, separator, kind_text = raw.partition(":")
    if not separator or not node_text.strip() or not kind_text.strip():
        raise StationSidecarError(
            "侧车中的 StopKey 格式应为 node_id:Kind",
            (_issue("INVALID_STOP_KEY", repr(value), edit_id),),
        )
    try:
        node_id = int(node_text)
    except (TypeError, ValueError):
        raise StationSidecarError(
            "侧车中的 StopKey 节点 ID 必须是整数",
            (_issue("INVALID_STOP_KEY", repr(value), edit_id),),
        ) from None
    kind = normalize_transit_kind(kind_text)
    if kind is TransitKind.OTHER:
        raise StationSidecarError(
            "侧车中的 StopKey 使用了未知交通类型",
            (_issue("UNKNOWN_STOP_KIND", repr(value), edit_id),),
        )
    return str(StopKey(node_id, normalize_stop_kind(kind)))


def _int_tuple(value: Any, field_name: str, edit_id: str) -> tuple[int, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise StationSidecarError(
            f"侧车字段 {field_name} 必须是整数数组",
            (_issue("INVALID_FIELD", field_name, edit_id),),
        )
    result: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            raise StationSidecarError(
                f"侧车字段 {field_name} 必须是整数数组",
                (_issue("INVALID_FIELD", repr(item), edit_id),),
            )
        result.append(item)
    if len(result) != len(set(result)):
        raise StationSidecarError(
            f"侧车字段 {field_name} 不能包含重复组 ID",
            (_issue("DUPLICATE_REFERENCE", field_name, edit_id),),
        )
    return tuple(sorted(result))


def _stop_tuple(raw: Mapping[str, Any], kind: str, edit_id: str) -> tuple[str, ...]:
    if kind == "rename_stop":
        if "stop_key" not in raw:
            raise StationSidecarError(
                "rename_stop 必须提供 stop_key",
                (_issue("MISSING_FIELD", "stop_key", edit_id),),
            )
        values: Any = [raw.get("stop_key")]
    else:
        values = raw.get("stop_keys")
        if not isinstance(values, list):
            raise StationSidecarError(
                f"{kind} 必须提供 stop_keys 数组",
                (_issue("MISSING_FIELD", "stop_keys", edit_id),),
            )
    result = tuple(sorted(_canonical_stop_key(item, edit_id=edit_id) for item in values))
    if len(result) != len(set(result)):
        raise StationSidecarError(
            "同一条侧车规则不能重复引用同一个 Stop",
            (_issue("DUPLICATE_REFERENCE", "stop_keys", edit_id),),
        )
    minimum = 1 if kind in {"rename_stop", "set_transfers"} else 2
    if len(result) < minimum:
        raise StationSidecarError(
            f"{kind} 至少需要 {minimum} 个 Stop 引用",
            (_issue("INVALID_ARITY", "stop_keys", edit_id),),
        )
    return result


def _parse_edit(raw: Any, index: int) -> StationFix:
    if not isinstance(raw, Mapping):
        raise StationSidecarError(
            "侧车 edits 必须是对象数组",
            (_issue("INVALID_EDIT", f"第 {index + 1} 条不是对象"),),
        )
    edit_id = raw.get("id")
    if not isinstance(edit_id, str) or not edit_id.strip():
        raise StationSidecarError(
            "每条侧车规则必须有非空字符串 id",
            (_issue("MISSING_FIELD", "id", str(edit_id or "")),),
        )
    edit_id = edit_id.strip()
    unknown_fields = sorted(set(raw) - {
        "id", "kind", "note", "stop_key", "stop_keys", "name",
        "add_group_ids", "remove_group_ids",
    })
    if unknown_fields:
        raise StationSidecarError(
            "侧车规则包含不支持的字段",
            (_issue("UNKNOWN_FIELD", ", ".join(unknown_fields), edit_id),),
        )
    kind = raw.get("kind")
    if kind not in EDIT_KINDS:
        raise StationSidecarError(
            "侧车 kind 必须是 merge_stops、keep_separate、set_transfers 或 rename_stop",
            (_issue("UNKNOWN_EDIT_KIND", repr(kind), edit_id),),
        )
    stop_keys = _stop_tuple(raw, kind, edit_id)
    if kind == "rename_stop" and "stop_keys" in raw:
        raise StationSidecarError(
            "rename_stop 只能使用 stop_key",
            (_issue("INVALID_FIELD", "stop_keys", edit_id),),
        )
    if kind != "rename_stop" and "stop_key" in raw:
        raise StationSidecarError(
            f"{kind} 只能使用 stop_keys",
            (_issue("INVALID_FIELD", "stop_key", edit_id),),
        )
    name = raw.get("name")
    if kind == "rename_stop":
        if not isinstance(name, str):
            raise StationSidecarError(
                "rename_stop 必须提供字符串 name（空字符串表示恢复自动名称）",
                (_issue("MISSING_FIELD", "name", edit_id),),
            )
    elif name is not None and not isinstance(name, str):
        raise StationSidecarError(
            "侧车 name 必须是字符串",
            (_issue("INVALID_FIELD", "name", edit_id),),
        )
    add = _int_tuple(raw.get("add_group_ids"), "add_group_ids", edit_id)
    remove = _int_tuple(raw.get("remove_group_ids"), "remove_group_ids", edit_id)
    if kind != "set_transfers" and (add or remove):
        raise StationSidecarError(
            "只有 set_transfers 可以提供 add_group_ids/remove_group_ids",
            (_issue("INVALID_FIELD", "group_ids", edit_id),),
        )
    note = raw.get("note", "")
    if not isinstance(note, str):
        raise StationSidecarError(
            "侧车 note 必须是字符串",
            (_issue("INVALID_FIELD", "note", edit_id),),
        )
    return StationFix(
        id=edit_id,
        kind=kind,
        stop_keys=stop_keys,
        name=name,
        add_group_ids=add,
        remove_group_ids=remove,
        note=note,
    )


def _validate_rule_conflicts(edits: tuple[StationFix, ...]) -> tuple[StationSidecarIssue, ...]:
    issues: list[StationSidecarIssue] = []
    seen_ids: set[str] = set()
    seen_rules: set[tuple[Any, ...]] = set()
    blocked: dict[tuple[str, str], str] = {}
    forced: dict[tuple[str, ...], str] = {}
    rename_names: dict[str, tuple[str, str]] = {}
    for edit in edits:
        if edit.id in seen_ids:
            issues.append(_issue("DUPLICATE_EDIT_ID", "规则 id 重复", edit.id))
        seen_ids.add(edit.id)
        signature = (
            edit.kind,
            edit.stop_keys,
            edit.name,
            edit.add_group_ids,
            edit.remove_group_ids,
        )
        if signature in seen_rules:
            issues.append(_issue("DUPLICATE_RULE", "语义完全相同的规则重复", edit.id))
        seen_rules.add(signature)
        if edit.kind == "keep_separate":
            for pair in combinations(edit.stop_keys, 2):
                blocked[tuple(sorted(pair))] = edit.id
        elif edit.kind == "merge_stops":
            forced[tuple(sorted(edit.stop_keys))] = edit.id
        elif edit.kind == "rename_stop":
            key = edit.stop_keys[0]
            previous = rename_names.get(key)
            if previous and previous[0] != edit.name:
                issues.append(_issue(
                    "CONFLICTING_RULE",
                    f"同一 Stop 的改名规则冲突（{previous[1]} 与 {edit.id}）",
                    edit.id,
                ))
            rename_names[key] = (edit.name or "", edit.id)
    # 允许重叠的强制分组，但要检查传递闭包，防止两条单独有效的规则重新合并保留对。
    forced_keys = sorted({key for group in forced for key in group})
    parent = {key: key for key in forced_keys}

    def find(key: str) -> str:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for group in forced:
        for key in group[1:]:
            left, right = find(group[0]), find(key)
            if left != right:
                parent[right] = left
    for pair, blocked_id in blocked.items():
        if pair[0] in parent and pair[1] in parent and find(pair[0]) == find(pair[1]):
            merge_id = next(
                (edit.id for edit in edits
                 if edit.kind == "merge_stops"
                 and pair[0] in edit.stop_keys and pair[1] in edit.stop_keys),
                "",
            )
            issues.append(_issue(
                "CONFLICTING_RULE",
                f"强制合并与禁止配对冲突（{blocked_id}）",
                merge_id,
            ))
    return tuple(issues)


def parse_station_sidecar(value: Mapping[str, Any]) -> StationSidecar:
    """解析并结构性校验已解码的附属对象。"""
    issues: list[StationSidecarIssue] = []
    if not isinstance(value, Mapping):
        raise StationSidecarError("站点侧车根对象必须是 JSON object")
    unknown_fields = sorted(set(value) - {
        "schema_version", "scope", "source_map", "edits",
    })
    if unknown_fields:
        issues.append(_issue("UNKNOWN_FIELD", ", ".join(unknown_fields)))
    version = value.get("schema_version")
    if version != SCHEMA_VERSION:
        issues.append(_issue(
            "SCHEMA_VERSION",
            f"不支持的 schema_version {version!r}，需要 {SCHEMA_VERSION!r}",
        ))
    scope = value.get("scope")
    if scope != SCHEMA_SCOPE:
        issues.append(_issue(
            "SCHEMA_SCOPE",
            f"不支持的 scope {scope!r}，需要 {SCHEMA_SCOPE!r}",
        ))
    source_map = value.get("source_map", "")
    if not isinstance(source_map, str):
        issues.append(_issue("INVALID_FIELD", "source_map 必须是字符串"))
        source_map = ""
    raw_edits = value.get("edits")
    if not isinstance(raw_edits, list):
        issues.append(_issue("INVALID_FIELD", "edits 必须是数组"))
        raw_edits = []
    edits: list[StationFix] = []
    for index, raw in enumerate(raw_edits):
        try:
            edits.append(_parse_edit(raw, index))
        except StationSidecarError as exc:
            issues.extend(exc.issues or (_issue("INVALID_EDIT", str(exc)),))
    edits_tuple = tuple(edits)
    issues.extend(_validate_rule_conflicts(edits_tuple))
    if issues:
        raise StationSidecarError("站点侧车校验失败", issues)
    return StationSidecar(
        schema_version=version,
        scope=scope,
        source_map=source_map,
        edits=edits_tuple,
    )


def load_station_sidecar(path: str | Path) -> StationSidecar:
    """加载附属文件，并将文件或 JSON 错误转换为清晰消息。"""
    sidecar_path = Path(path)
    try:
        text = sidecar_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise StationSidecarError(
            f"无法读取站点侧车 {sidecar_path}: {exc}",
            (_issue("READ_ERROR", str(exc)),),
        ) from exc
    try:
        value = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StationSidecarError(
            f"站点侧车不是有效 JSON：{sidecar_path}",
            (_issue("INVALID_JSON", str(exc)),),
        ) from exc
    try:
        return parse_station_sidecar(value)
    except StationSidecarError as exc:
        raise StationSidecarError(
            f"站点侧车无效：{sidecar_path}", exc.issues
        ) from exc


def resolve_station_sidecar_path(
    source_map: str | Path | None,
    explicit: str | Path | bool | None = None,
) -> Path | None:
    """解析显式附属文件路径或地图旁的自动发现路径。"""
    if explicit is False:
        return None
    if explicit is not None:
        return Path(explicit)
    if not source_map:
        return None
    candidate = Path(source_map)
    if not candidate.name:
        return None
    adjacent = Path(str(candidate) + ".stations.json")
    return adjacent if adjacent.is_file() else None


def load_station_sidecar_if_present(
    source_map: str | Path | None,
    explicit: str | Path | bool | None = None,
) -> StationSidecar | None:
    path = resolve_station_sidecar_path(source_map, explicit)
    if path is None:
        return None
    return load_station_sidecar(path)


def validate_station_sidecar(
    sidecar: StationSidecar,
    stops: Iterable[Stop] | Mapping[str, Stop] | None = None,
    group_ids: Iterable[int] | None = None,
    *,
    strict: bool = True,
) -> tuple[StationSidecarIssue, ...]:
    """不应用编辑即可校验站点与线路组的可达性；正式渲染默认严格模式，宽松模式只供未来编辑器诊断。"""
    if not isinstance(sidecar, StationSidecar):
        if isinstance(sidecar, Mapping):
            sidecar = parse_station_sidecar(sidecar)
        else:
            raise TypeError("sidecar must be StationSidecar or a mapping")
    known_stops: set[str] | None = None
    if stops is not None:
        values = stops.values() if isinstance(stops, Mapping) else stops
        known_stops = {item.stop_key if isinstance(item, Stop) else str(item) for item in values}
    known_groups = None if group_ids is None else {int(item) for item in group_ids}
    issues: list[StationSidecarIssue] = []
    for edit in sidecar.edits:
        if known_stops is not None:
            for key in edit.stop_keys:
                if key not in known_stops:
                    issues.append(_issue(
                        "HANGING_STOP",
                        f"引用的 Stop 不存在：{key}", edit.id,
                    ))
        if known_groups is not None:
            for group_id in (*edit.add_group_ids, *edit.remove_group_ids):
                if group_id not in known_groups:
                    issues.append(_issue(
                        "HANGING_GROUP",
                        f"引用的 LineGroup 不存在：{group_id}", edit.id,
                    ))
    if strict and issues:
        raise StationSidecarError("站点侧车存在悬挂引用", issues)
    return tuple(issues)


def merge_params_with_sidecar(
    params: StationMergeParams,
    sidecar: StationSidecar | None,
) -> StationMergeParams:
    """把附属约束加入当前作业的站点参数。"""
    if sidecar is None:
        return params
    blocked = tuple(sorted({
        tuple(sorted((str(a), str(b))))
        for pair in (*params.blocked_pairs, *sidecar.blocked_pairs)
        for a, b in (pair,)
    }))
    forced = tuple(sorted({
        tuple(sorted(map(str, group)))
        for group in (*params.forced_merges, *sidecar.forced_merges)
    }))
    return replace(params, blocked_pairs=blocked, forced_merges=forced)


def _station_for_keys(stations: Iterable[Station], keys: Iterable[str]) -> Station | None:
    wanted = set(keys)
    return next((station for station in stations
                 if wanted.intersection(station.member_stop_keys)), None)


def apply_station_fixes(
    stations: Iterable[Station],
    sidecar: StationSidecar,
    *,
    strict: bool = True,
    return_issues: bool = False,
) -> tuple[Station, ...] | StationFixApplication:
    """确定性地应用分组后的重命名和服务线路组修改。"""
    if not isinstance(sidecar, StationSidecar):
        if isinstance(sidecar, Mapping):
            sidecar = parse_station_sidecar(sidecar)
        else:
            raise TypeError("sidecar must be StationSidecar or a mapping")
    current = tuple(sorted(stations, key=lambda item: str(item.id)))
    issues: list[StationSidecarIssue] = []

    # 分组阶段保证每个 StopKey 对应一个站点；筛选预览仍可能缺键，严格模式须原子报告。
    by_edit: dict[str, Station] = {}
    for edit in sidecar.edits:
        station = _station_for_keys(current, edit.stop_keys)
        if station is None:
            issues.append(_issue(
                "HANGING_STOP", "规则没有对应的已聚合 Station", edit.id,
            ))
        else:
            by_edit[edit.id] = station
    if strict and issues:
        raise StationSidecarError("站点侧车无法完整应用", issues)

    updated: dict[str, Station] = {str(item.id): item for item in current}
    # 按稳定顺序应用换乘编辑，不受 JSON 列表顺序影响。
    for edit in sorted(sidecar.edits, key=lambda item: item.id):
        if edit.kind != "set_transfers" or edit.id not in by_edit:
            continue
        original = by_edit[edit.id]
        key = str(original.id)
        station = updated[key]
        values = [group_id for group_id in station.serving_group_ids
                  if group_id not in edit.remove_group_ids]
        for group_id in edit.add_group_ids:
            if group_id not in values:
                values.append(group_id)
        updated[key] = replace(station, serving_group_ids=values)
        by_edit[edit.id] = updated[key]

    # 名称解析最后执行；rename_stop 优先于 merge_stops，冲突以最小 StopKey 决定并报告。
    names: dict[str, list[tuple[int, str, str, str]]] = {}
    for edit in sidecar.edits:
        if edit.kind not in {"merge_stops", "rename_stop"} or edit.id not in by_edit:
            continue
        station = by_edit[edit.id]
        names.setdefault(str(station.id), []).append((
            0 if edit.kind == "rename_stop" else 1,
            edit.stop_keys[0],
            edit.name or "",
            edit.id,
        ))
    for station_id, candidates in names.items():
        candidates.sort()
        winner = candidates[0]
        if any(item[2] != winner[2] for item in candidates[1:]):
            issues.append(_issue(
                "CONFLICTING_NAME",
                "同一 Station 命中了多个不同名称，已按 StopKey 稳定选择",
                winner[3],
                severity="warn",
            ))
        station = updated[station_id]
        if winner[2].strip():
            updated[station_id] = replace(
                station,
                name=DisplayName(winner[2].strip(), "custom"),
            )

    result = tuple(sorted(updated.values(), key=lambda item: str(item.id)))
    if return_issues:
        return StationFixApplication(result, tuple(issues))
    return result


def validate_station_invariants(
    stations: Iterable[Station],
    params: StationMergeParams | None = None,
) -> tuple[StationSidecarIssue, ...]:
    """校验全部站点编辑完成后仍必须满足的不变量。"""
    values = tuple(stations)
    issues: list[StationSidecarIssue] = []
    seen: dict[str, str] = {}
    blocked = {
        tuple(sorted((str(a), str(b)))) for a, b in (params.blocked_pairs if params else ())
    }
    for station in values:
        keys = tuple(station.member_stop_keys)
        if len(keys) != len(set(keys)):
            issues.append(_issue("DUPLICATE_STATION_MEMBER", str(station.id)))
        for key in keys:
            previous = seen.get(key)
            if previous is not None and previous != str(station.id):
                issues.append(_issue(
                    "OVERLAPPING_STATION_MEMBER",
                    f"Stop {key} 同时属于 {previous} 与 {station.id}",
                ))
            seen[key] = str(station.id)
        for pair in combinations(keys, 2):
            if tuple(sorted(pair)) in blocked:
                issues.append(_issue(
                    "BLOCKED_PAIR_VIOLATION",
                    f"Station {station.id} 包含禁止配对 {pair}",
                ))
        if len(station.serving_group_ids) != len(set(station.serving_group_ids)):
            issues.append(_issue(
                "DUPLICATE_SERVING_GROUP", f"Station {station.id} 的 serving_group_ids 重复",
            ))
    return tuple(issues)


def station_sidecar_to_json(sidecar: StationSidecar, *, indent: int = 2) -> str:
    """为编辑器或测试数据序列化已校验的附属文件。"""
    return json.dumps(sidecar.to_dict(), ensure_ascii=False, indent=indent) + "\n"


__all__ = [
    "EDIT_KINDS",
    "SCHEMA_SCOPE",
    "SCHEMA_VERSION",
    "StationFix",
    "StationFixApplication",
    "StationSidecar",
    "StationSidecarError",
    "StationSidecarIssue",
    "apply_sidecar",
    "apply_station_fixes",
    "load_sidecar",
    "load_station_sidecar",
    "load_station_sidecar_if_present",
    "merge_params_with_sidecar",
    "parse_station_sidecar",
    "resolve_station_sidecar_path",
    "station_sidecar_to_json",
    "validate_station_invariants",
    "validate_sidecar",
    "validate_station_sidecar",
]


# 为以三种操作呈现协议的前端提供短名称，显式名称仍是首选公开接口。
load_sidecar = load_station_sidecar
validate_sidecar = validate_station_sidecar
apply_sidecar = apply_station_fixes
