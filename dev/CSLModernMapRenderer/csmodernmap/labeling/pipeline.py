"""组织标注的收集、筛选、排序、放置和绘制流程。"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from ..cmm import MapIssue, ObjectRef
from ..cmm.model import RenderJob
from ..detail import resolve_detail_policy
from .collectors import DEFAULT_COLLECTORS, collect_labels
from .model import FilteredLabel, Label, PlacedLabel
from .placers import AnchorPlacer, LegacyBaseTextPlacer, PlaceSpace
from .registry import CollectorRegistry, PlacerRegistry
from .rules import RuleDecision, RuleTable, load_rule_table, _policy_source
from .style_resolver import resolve_with_override


@dataclass(frozen=True)
class PipelineResult:
    candidates: tuple[Label, ...]
    filtered: tuple[FilteredLabel, ...]
    placed: tuple[PlacedLabel, ...]
    summary: dict
    issues: tuple[MapIssue, ...] = ()

    @property
    def labels(self) -> tuple[Label, ...]:
        return self.candidates

    @property
    def placed_labels(self) -> tuple[PlacedLabel, ...]:
        return self.placed

    def to_dict(self) -> dict:
        return {
            "summary": self.summary,
            "issues": [issue.to_dict() for issue in self.issues],
            "candidates": [label.to_dict() for label in self.candidates],
            "placed_count": len(self.placed),
        }


def _count_by_kind(labels: Iterable[Label]) -> dict[str, int]:
    return dict(Counter(label.type for label in labels))


def _style_for(label: Label, style: Any, job: RenderJob) -> dict | None:
    return resolve_with_override(label.style_ref, style, job.style_override)


class LabelPipeline:
    """提供带显式登记表和诊断输出的可复用标注流程。"""

    def __init__(self, *, rules: RuleTable | None = None,
                 collectors: CollectorRegistry | Mapping[str, Callable] | None = None,
                 placers: PlacerRegistry | Mapping[str, Callable] | None = None) -> None:
        self.rules = rules or load_rule_table()
        self.collectors = collectors if isinstance(collectors, CollectorRegistry) else CollectorRegistry(
            collectors or DEFAULT_COLLECTORS
        )
        self.placers = placers if isinstance(placers, PlacerRegistry) else PlacerRegistry(placers or {})

    def collect(self, mapdata: Any, params: Mapping[str, Any] | None = None) -> list[Label]:
        return collect_labels(mapdata, params, self.collectors)

    def filter(self, labels: Iterable[Label], job: RenderJob, style: Any,
               detail_policy: Any = None) -> tuple[list[FilteredLabel], dict, list[MapIssue]]:
        if detail_policy is None:
            detail_policy = resolve_detail_policy(job.detail_mode,
                                                   float(job.canvas.get("zoom", 1.0)))
        level = int(getattr(detail_policy, "abstract_level", 0))
        policy_source = _policy_source(job.detail_mode)
        filtered: list[FilteredLabel] = []
        dropped = Counter()
        issues: list[MapIssue] = []
        for label in labels:
            decision: RuleDecision = self.rules.evaluate(
                label,
                view=job.kind,
                detail_level=level,
                policy_source=policy_source,
                job=job,
            )
            if not decision.matched:
                dropped[decision.reason] += 1
                severity = "warn" if decision.reason == "invalid_override" else "info"
                issues.append(MapIssue(
                    severity,
                    "LABEL_VISIBILITY_RULE",
                    f"{label.type} {label.ref_kind}:{label.ref_id} dropped: {decision.reason}",
                    [ObjectRef(label.ref_kind, label.ref_id)],
                ))
                continue
            if decision.value == "OFF":
                dropped[decision.reason] += 1
                continue
            if not label.detail_min <= level <= label.detail_max:
                dropped["detail_level"] += 1
                continue
            resolved = _style_for(label, style, job)
            if resolved is None:
                dropped["missing_style"] += 1
                issues.append(MapIssue(
                    "info", "LABEL_STYLE_MISSING",
                    f"{label.type} {label.ref_kind}:{label.ref_id} has no style {label.style_ref!r}",
                    [ObjectRef(label.ref_kind, label.ref_id)],
                ))
                continue
            if decision.style_gate == "min_zoom":
                if resolved.get("enabled", True) is False:
                    dropped["style_disabled"] += 1
                    continue
                render_scale = float(job.derive_params.get(
                    "render_scale", job.canvas.get("zoom", 1.0)))
                if render_scale < float(resolved.get("min_zoom", 0.0) or 0.0):
                    dropped["style_min_zoom"] += 1
                    continue
            filtered.append(FilteredLabel(label, resolved))
        return filtered, dict(dropped), issues

    @staticmethod
    def order(labels: Iterable[FilteredLabel]) -> list[FilteredLabel]:
        """先按内容优先级排序，再按稳定身份排序。"""
        return sorted(labels, key=lambda item: (-item.label.priority, item.label.id))

    def place(self, labels: Iterable[FilteredLabel], *, job: RenderJob,
              context: Any = None, mapdata: Any = None,
              measure_text: Callable | None = None) -> tuple[list[PlacedLabel], dict]:
        items = list(labels)
        space = PlaceSpace()
        placements: list[PlacedLabel] = []
        dropped: Counter = Counter()
        base = [item for item in items if item.label.collision_group == "base_text"]
        rest = [item for item in items if item.label.collision_group != "base_text"]
        if base:
            if context is not None and mapdata is not None and measure_text is not None:
                placer = LegacyBaseTextPlacer(context, mapdata, measure_text)
                placements.extend(placer.place_all(base, space))
                dropped.update(placer.dropped_by_reason)
            else:
                placer = AnchorPlacer()
                for item in base:
                    placed = placer.place(item.label, space, style=item.style, context=context)
                    if placed is not None:
                        placements.append(placed)
                    else:
                        dropped["no_placement_strategy"] += 1
        if rest:
            placer = AnchorPlacer()
            for item in rest:
                placed = placer.place(item.label, space, style=item.style, context=context)
                if placed is not None:
                    placements.append(placed)
                else:
                    dropped["no_placement_strategy"] += 1
        return placements, dict(dropped)

    @staticmethod
    def paint(placements: Iterable[PlacedLabel], painter: Callable | None = None) -> None:
        """通过输出专属适配器绘制临时标注实例。"""
        if painter is not None:
            painter(list(placements))

    @staticmethod
    def post(job: RenderJob, *, occupancy: Mapping[str, Any] | None = None) -> dict:
        """预留面板占位的后处理入口，当前只记录而不重排。"""
        return {"view": job.kind, "occupancy": dict(occupancy or {})}

    def run(self, mapdata: Any, job: RenderJob, style: Any, *,
            detail_policy: Any = None, context: Any = None,
            measure_text: Callable | None = None,
            painter: Callable | None = None) -> PipelineResult:
        candidates = tuple(self.collect(mapdata, job.derive_params))
        filtered, filter_dropped, issues = self.filter(
            candidates, job, style, detail_policy)
        ordered = self.order(filtered)
        placed, placement_dropped = self.place(
            ordered, job=job, context=context, mapdata=mapdata,
            measure_text=measure_text,
        )
        self.paint(placed, painter)

        collected_by_kind = _count_by_kind(candidates)
        filtered_by_kind = _count_by_kind(item.label for item in filtered)
        placed_by_kind = _count_by_kind(item.label for item in placed)
        kind_summary = {
            kind: {
                "collected": int(collected_by_kind.get(kind, 0)),
                "filtered": int(filtered_by_kind.get(kind, 0)),
                "placed": int(placed_by_kind.get(kind, 0)),
            }
            for kind in sorted(set(collected_by_kind) | set(filtered_by_kind) | set(placed_by_kind))
        }
        dropped_by_reason = dict(Counter(filter_dropped) + Counter(placement_dropped))
        summary = {
            "view": job.kind,
            "counts_by_kind": collected_by_kind,
            "collected_by_kind": collected_by_kind,
            "filtered_by_kind": filtered_by_kind,
            "placed_by_kind": placed_by_kind,
            "kind_summary": kind_summary,
            "filtered_count": len(candidates) - len(filtered),
            "placed_count": len(placed),
            "discarded_count": sum(dropped_by_reason.values()),
            "dropped_by_rule": dict(filter_dropped),
            "dropped_by_reason": dropped_by_reason,
            "filter_reasons": dict(filter_dropped),
            "placement_drops": dict(placement_dropped),
            "space": {},
            "post": self.post(job),
        }
        return PipelineResult(candidates, tuple(filtered), tuple(placed), summary, tuple(issues))


def pipeline_summary(job: RenderJob, result: PipelineResult | None = None) -> dict:
    """返回工具与编辑器预览共用的精简诊断结构。"""
    if result is not None:
        summary = dict(result.summary)
        summary["view"] = job.kind
        return summary
    return {
        "view": job.kind,
        "counts_by_kind": {},
        "filtered_by_kind": {},
        "placed_by_kind": {},
        "kind_summary": {},
        "filtered_count": 0,
        "placed_count": 0,
        "discarded_count": 0,
        "dropped_by_rule": {},
        "dropped_by_reason": {},
    }
