"""收集首批迁移的地图标注候选对象。

收集器只读取 MapData，并在进入渲染器专属的放置及绘制流程前统一处理名称回退。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .model import Label, LabelAnchor, LabelKind, LabelSource, stable_label_id
from .registry import COLLECTOR_REGISTRY, CollectorRegistry
from .style_resolver import style_ref_for
from ..layers.district_layer import visible_administrative_districts


INVALID_POS = -4915.2


@dataclass(frozen=True)
class CollectionResult:
    labels: tuple[Label, ...]
    counts_by_kind: Mapping[str, int]
    dropped_by_reason: Mapping[str, int]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _valid_point(point: Any) -> bool:
    if point is None:
        return False
    try:
        x, z = float(point.x), float(point.z)
    except (AttributeError, TypeError, ValueError):
        return False
    return x != INVALID_POS or z != INVALID_POS


def _first_point(entity: Any) -> Any | None:
    points = getattr(entity, "points", ()) or ()
    point = points[0] if points else None
    return point if _valid_point(point) else None


def _make(kind: str, source: str, text: str, ref_kind: str, ref_id: int,
          position: LabelAnchor, *, priority: int, detail_max: int = 3) -> Label:
    return Label(
        id=stable_label_id(kind, ref_kind, ref_id, text),
        type=kind,
        source=source,
        text=text,
        ref_kind=ref_kind,
        ref_id=ref_id,
        position=position,
        priority=priority,
        layer="label",
        visibility="AUTO",
        detail_min=0,
        detail_max=detail_max,
        collision_group="base_text",
        style_ref=style_ref_for(kind),
    )


def collect_district_names(mapdata: Any, params: Mapping[str, Any] | None = None) -> list[Label]:
    labels: list[Label] = []
    for district in visible_administrative_districts(mapdata):
        text = _text(getattr(district, "name", ""))
        point = _first_point(district)
        if not text or point is None:
            continue
        labels.append(_make(
            LabelKind.DISTRICT_NAME, LabelSource.GAME, text, "district",
            district.id, LabelAnchor("POINT", point.x, point.z), priority=60,
        ))
    return labels


def collect_road_names(mapdata: Any, params: Mapping[str, Any] | None = None) -> list[Label]:
    labels: list[Label] = []
    params = params or {}
    show_player = bool(params.get("road_player_names", True))
    from .road_paths import street_chains
    for chain in street_chains(mapdata):
        if (chain.source == "CUSTOM" and not show_player) or (
                chain.source == "GAME" and not params.get("use_game_names", False)):
            continue
        labels.append(_make(LabelKind.ROAD_NAME, chain.source, chain.text, "segment", chain.id,
                            LabelAnchor("LINE"), priority=40))
    return labels


DEFAULT_COLLECTORS: dict[str, Callable] = {
    LabelKind.DISTRICT_NAME: collect_district_names,
    LabelKind.ROAD_NAME: collect_road_names,
}


def default_collector_registry() -> CollectorRegistry:
    return CollectorRegistry(DEFAULT_COLLECTORS)


for _kind, _collector in DEFAULT_COLLECTORS.items():
    if COLLECTOR_REGISTRY.get(_kind) is None:
        COLLECTOR_REGISTRY.register(_kind, _collector)


def collect_base_text(mapdata: Any, params: Mapping[str, Any] | None = None) -> CollectionResult:
    """按统一公开筛选规则收集道路和行政区的底图文字。"""
    labels: list[Label] = []
    # 此顺序是稳定的候选目录顺序；放置器可以另设占位顺序，绘制仍按层级和优先级执行。
    for kind in (LabelKind.DISTRICT_NAME, LabelKind.ROAD_NAME):
        collector = DEFAULT_COLLECTORS[kind]
        produced = collector(mapdata, params)
        labels.extend(produced)
    labels.sort(key=lambda item: (item.type, item.ref_id, item.id))
    counts: dict[str, int] = {}
    for label in labels:
        counts[label.type] = counts.get(label.type, 0) + 1
    return CollectionResult(tuple(labels), counts, {})


def collect_labels(mapdata: Any, params: Mapping[str, Any] | None = None,
                   registry: CollectorRegistry | None = None) -> list[Label]:
    """收集全部已登记的标注类型；未知项由流程跳过。"""
    active = registry or default_collector_registry()
    labels: list[Label] = []
    for kind in active.keys():
        labels.extend(active.require(kind)(mapdata, params))
    return sorted(labels, key=lambda item: (item.type, item.ref_id, item.id))
