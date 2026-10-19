"""定义数据驱动的标注可见性规则。

可见性规则集中在此处；每条规则保留旧版条件来源，便于诊断对应关系。"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from .model import Label


VISIBILITY_VALUES = {"AUTO", "ON", "OFF"}
KNOWN_LABEL_KINDS = {
    "TITLE", "STATION_NAME", "LINE_NAME", "DISTRICT_NAME", "PARK_NAME",
    "ROAD_NAME", "BUILDING_NAME", "POI_NAME", "LEGEND", "ANNOTATION",
}


@dataclass(frozen=True)
class RuleDecision:
    """记录一种模式、类别及细节等级组合的规则结果。"""

    value: str
    reason: str = "rule"
    matched: bool = True
    legacy_source: str = ""
    style_gate: str = ""

    @property
    def allowed(self) -> bool:
        return self.value in {"ON", "AUTO"}


def _norm(value: Any) -> str:
    # YAML 1.1 会把未加引号的 ON 和 OFF 解析为布尔值，此处兼容两种写法。
    if value is True:
        return "ON"
    if value is False:
        return "OFF"
    return str(getattr(value, "value", value) or "").strip().upper()


def _policy_source(detail_mode: Any) -> str:
    raw = str(getattr(detail_mode, "value", detail_mode) or "auto").strip().lower()
    return "auto" if raw == "auto" else "manual"


def _merge_style(base: Any, override: Mapping[str, Any] | None) -> Any:
    """深度合并作业样式覆盖，不修改原主题。"""
    if not override:
        return base
    if not isinstance(base, Mapping):
        return copy.deepcopy(override)
    result = copy.deepcopy(dict(base))
    for key, value in override.items():
        if isinstance(result.get(key), Mapping) and isinstance(value, Mapping):
            result[key] = _merge_style(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


class RuleTable:
    """保存经校验且可序列化的模式规则表，供筛选阶段使用。"""

    def __init__(self, schema_version: str, views: tuple[str, ...], rules: tuple[dict, ...]):
        self.schema_version = str(schema_version)
        self.views = tuple(str(view) for view in views)
        self.rules = tuple(copy.deepcopy(dict(row)) for row in rules)
        self._rows = {(str(row.get("view", "")).lower(),
                      str(row.get("policy_source", "auto")).lower()): row
                      for row in self.rules}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RuleTable":
        value = value or {}
        return cls(
            value.get("schema_version", "label-rules-v1"),
            tuple(value.get("views", ())),
            tuple(value.get("rules", ())),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "views": list(self.views),
            "rules": copy.deepcopy(list(self.rules)),
        }

    def validate(self) -> tuple[str, ...]:
        errors: list[str] = []
        if self.schema_version != "label-rules-v1":
            errors.append(f"unsupported schema_version: {self.schema_version}")
        if not self.views:
            errors.append("rules table has no views")
        seen_rows: set[tuple[str, str]] = set()
        for row_index, row in enumerate(self.rules):
            view = str(row.get("view", "")).lower()
            source = str(row.get("policy_source", "auto")).lower()
            key = (view, source)
            if view not in self.views:
                errors.append(f"rules[{row_index}]: unknown view {view!r}")
            if source not in {"auto", "manual"}:
                errors.append(f"rules[{row_index}]: unknown policy_source {source!r}")
            if key in seen_rows:
                errors.append(f"rules[{row_index}]: duplicate view/policy_source {key}")
            seen_rows.add(key)
            for item_index, item in enumerate(row.get("items", ())):
                kind = str(item.get("type", "")).upper()
                if kind not in KNOWN_LABEL_KINDS:
                    errors.append(f"rules[{row_index}].items[{item_index}]: unknown type {kind!r}")
                levels = item.get("levels")
                entries = levels if levels is not None else [item]
                for level_index, level in enumerate(entries):
                    value = _norm(level.get("value", "AUTO"))
                    if value not in VISIBILITY_VALUES:
                        errors.append(f"rules[{row_index}].items[{item_index}].levels[{level_index}]: invalid value")
                    lo = int(level.get("detail_min", 0))
                    hi = int(level.get("detail_max", 3))
                    if not 0 <= lo <= hi <= 3:
                        errors.append(f"rules[{row_index}].items[{item_index}].levels[{level_index}]: invalid detail range")
                    if not level.get("legacy_source", item.get("legacy_source", "")):
                        errors.append(f"rules[{row_index}].items[{item_index}]: missing legacy_source")
        return tuple(errors)

    def row(self, view: str, policy_source: str = "auto") -> Mapping[str, Any] | None:
        return self._rows.get((str(view or "normal").lower(),
                               str(policy_source or "auto").lower()))

    def _item(self, view: str, policy_source: str, label_type: str,
              detail_level: int) -> Mapping[str, Any] | None:
        row = self.row(view, policy_source)
        if row is None:
            return None
        wanted = _norm(label_type)
        for item in row.get("items", ()):
            if _norm(item.get("type")) != wanted:
                continue
            levels = item.get("levels")
            if levels is None:
                return item
            for level in levels:
                lo = int(level.get("detail_min", 0))
                hi = int(level.get("detail_max", 3))
                if lo <= int(detail_level) <= hi:
                    merged = dict(item)
                    merged.update(level)
                    return merged
            return None
        return None

    def override_for(self, job: Any, label_type: str) -> Mapping[str, Any] | None:
        wanted = _norm(label_type)
        for override in getattr(job, "rule_overrides", ()) or ():
            override_view = str(override.get("view", "") or "").lower()
            if override_view and override_view != str(job.kind).lower():
                continue
            if _norm(override.get("type")) == wanted:
                return override
        return None

    def evaluate(self, label: Label | str, *, view: str = "normal",
                 detail_level: int = 0, policy_source: str = "auto",
                 job: Any = None) -> RuleDecision:
        label_type = label.type if isinstance(label, Label) else str(label)
        if isinstance(label, Label) and label.visibility == "OFF":
            return RuleDecision("OFF", "label_visibility", legacy_source="Label.visibility=OFF")
        explicit_on = isinstance(label, Label) and label.visibility == "ON"
        item = self._item(view, policy_source, label_type, detail_level)
        if item is None:
            return RuleDecision("OFF", "missing_rule", matched=False)
        table_value = _norm(item.get("value", "AUTO"))
        # 显式可见性不能放宽规则表中的关闭决定。
        value = ("OFF" if table_value == "OFF" else "ON") if explicit_on else table_value
        override = self.override_for(job, label_type) if job is not None else None
        if override is not None:
            requested = _norm(override.get("value", "AUTO"))
            order = {"ON": 2, "AUTO": 1, "OFF": 0}
            if order[requested] > order[value]:
                return RuleDecision("OFF", "invalid_override", matched=False,
                                    legacy_source=str(item.get("legacy_source", "")))
            value = requested
            reason = "override" if requested != _norm(item.get("value", "AUTO")) else "rule"
        else:
            reason = "rule"
        return RuleDecision(
            value,
            reason,
            legacy_source=str(item.get("legacy_source", "")),
            style_gate=str(item.get("style_gate", "")),
        )


def rules_path() -> Path:
    return Path(__file__).with_name("rules.yaml")


def load_rule_table(path: str | Path | None = None) -> RuleTable:
    source = Path(path) if path is not None else rules_path()
    with source.open("r", encoding="utf-8") as handle:
        table = RuleTable.from_dict(yaml.safe_load(handle) or {})
    errors = table.validate()
    if errors:
        raise ValueError("invalid label rules: " + "; ".join(errors))
    return table


# 为使用规范中简称的调用方提供别名。
load_rules = load_rule_table
RuleSet = RuleTable


def visibility_overrides(*, view: str = "normal", road: bool | None = None,
                         district: bool | None = None,
                         park: bool | None = None) -> tuple[dict[str, str], ...]:
    """把旧界面的布尔选项转换为正式可见性覆盖结构。"""
    values = (
        ("ROAD_NAME", road),
        ("DISTRICT_NAME", district),
        ("PARK_NAME", park),
    )
    return tuple(
        {"view": view, "type": kind, "value": "ON" if enabled else "OFF"}
        for kind, enabled in values if enabled is not None and not enabled
    )
