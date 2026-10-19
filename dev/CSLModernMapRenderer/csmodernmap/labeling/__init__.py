"""公开地图标注系统的基础模型与流程。"""

from .collectors import (
    CollectionResult,
    DEFAULT_COLLECTORS,
    collect_base_text,
    collect_district_names,
    collect_labels,
    collect_road_names,
    default_collector_registry,
)
from .model import (
    FilteredLabel,
    Label,
    LabelAnchor,
    LabelKind,
    LabelSource,
    PlacedLabel,
    RenderJob,
    derive_label_id,
    label_id,
    stable_label_id,
)
# RenderJob 仅为旧版标注调用方保留导出，标准值对象位于 CMM；此别名计划于 2027 年 6 月 30 日移除。
from .pipeline import LabelPipeline, PipelineResult, pipeline_summary
from .placers import (
    AnchorPlacer,
    LegacyBaseTextPlacer,
    PlaceSpace,
    normalize_angle,
    sample_along,
)
from .registry import (
    COLLECTOR_REGISTRY, PAINTER_REGISTRY, PLACER_REGISTRY, CollectorRegistry,
    PainterRegistry, PlacerRegistry, Registry, register_collector,
    register_painter, register_placer,
)
from .rules import (
    RuleDecision,
    RuleSet,
    RuleTable,
    load_rule_table,
    load_rules,
    rules_path,
    visibility_overrides,
)
from .style_resolver import (
    STYLE_REFERENCES,
    resolve,
    resolve_for_type,
    resolve_style,
    resolve_with_override,
    style_ref_for,
    validate_style_references,
)

__all__ = [
    "CollectionResult", "DEFAULT_COLLECTORS", "collect_base_text",
    "collect_district_names", "collect_labels",
    "collect_road_names", "default_collector_registry", "FilteredLabel",
    "Label", "LabelAnchor", "LabelKind", "LabelSource", "PlacedLabel",
    "RenderJob", "derive_label_id", "label_id", "stable_label_id",
    "LabelPipeline", "PipelineResult", "pipeline_summary", "AnchorPlacer",
    "LegacyBaseTextPlacer", "PlaceSpace", "normalize_angle", "sample_along",
    "CollectorRegistry", "PainterRegistry", "PlacerRegistry", "Registry",
    "COLLECTOR_REGISTRY", "PAINTER_REGISTRY", "PLACER_REGISTRY",
    "register_collector", "register_painter", "register_placer",
    "RuleDecision", "RuleSet", "RuleTable", "load_rule_table", "load_rules",
    "rules_path", "visibility_overrides", "STYLE_REFERENCES", "resolve",
    "resolve_for_type", "resolve_with_override", "style_ref_for",
    "validate_style_references", "resolve_style",
]
