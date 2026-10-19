"""实现标注位置选择策略。

旧版基础文字放置算法被移入统一流程，其采样常量和碰撞规则保持一致。"""
from __future__ import annotations

from ..path_model import normalize_angle

import math
from collections import defaultdict
from typing import Any, Callable, Iterable

from ..road_hierarchy import classify_segment, visible_at_abstract_level
from .model import FilteredLabel, Label, LabelKind, PlacedLabel


def sample_along(points: Iterable[tuple[float, float]], spacing: float):
    """按等像素弧长间隔采样折线，保持旧版基础文字放置算法的行为。"""
    pts = [(p[0], p[1]) for p in points]
    if len(pts) < 2:
        return []
    segs = []
    total = 0.0
    for i in range(len(pts) - 1):
        length = math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
        segs.append((pts[i], pts[i + 1], length))
        total += length
    if total < 1e-6:
        return []

    def make_sample(a, b, t):
        x = a[0] + (b[0] - a[0]) * t
        y = a[1] + (b[1] - a[1]) * t
        return (x, y, a, b)

    samples = []
    acc = 0.0
    target = spacing
    for a, b, seg_len in segs:
        if seg_len < 1e-6:
            continue
        while target <= acc + seg_len + 1e-6:
            samples.append(make_sample(a, b, (target - acc) / seg_len))
            target += spacing
        acc += seg_len
    if not samples:
        half = total / 2.0
        acc2 = 0.0
        for a, b, seg_len in segs:
            if seg_len < 1e-6:
                continue
            if acc2 + seg_len >= half:
                samples.append(make_sample(a, b, (half - acc2) / seg_len))
                break
            acc2 += seg_len
    return samples


class PlaceSpace:
    """保存一次标注流程中的共享占位信息。"""

    def __init__(self) -> None:
        self._points: dict[str, list[tuple[float, float]]] = defaultdict(list)
        self._bboxes: dict[str, list[tuple[float, float, float, float]]] = defaultdict(list)

    def add_point(self, group: str, x: float, y: float) -> None:
        self._points[str(group)].append((float(x), float(y)))

    def points(self, group: str) -> tuple[tuple[float, float], ...]:
        return tuple(self._points.get(str(group), ()))

    def report_occupancy(self, group: str, bbox: tuple[float, float, float, float]) -> None:
        self._bboxes[str(group)].append(tuple(float(v) for v in bbox))

    def overlaps(self, bbox) -> bool:
        return any(bbox[0] < other[2] and other[0] < bbox[2]
                   and bbox[1] < other[3] and other[1] < bbox[3]
                   for boxes in self._bboxes.values() for other in boxes)

    @property
    def occupancy(self) -> dict[str, dict[str, int]]:
        groups = set(self._points) | set(self._bboxes)
        return {
            group: {
                "points": len(self._points.get(group, ())),
                "bboxes": len(self._bboxes.get(group, ())),
            }
            for group in sorted(groups)
        }


def _resolve_size(value: Any, render_scale: float) -> float:
    if isinstance(value, dict):
        return float(value.get("fixed", 0.0)) + float(value.get("scaled", 0.0)) * render_scale
    return float(value)


def _segment(mapdata: Any, segment_id: int) -> Any | None:
    try:
        value = mapdata.segment(segment_id)
    except (AttributeError, KeyError):
        value = None
    if value is not None:
        return value
    return next((item for item in getattr(mapdata, "segments", ()) or ()
                 if item.id == segment_id), None)


class AnchorPlacer:
    """为协议和预览候选对象提供确定性放置策略。"""

    def place(self, label: Label, space: PlaceSpace, *, style=None,
              context=None) -> PlacedLabel | None:
        if label.position.x is None or label.position.z is None or context is None:
            return None
        x, y = context.transform(label.position.x, label.position.z)
        width, height = context.image.size
        if not (0 <= x <= width and 0 <= y <= height):
            return None
        return PlacedLabel(label, x, y, style=style or {})


class LegacyBaseTextPlacer:
    """将旧版基础文字布局作为已登记的放置策略使用。"""

    def __init__(self, context: Any, mapdata: Any,
                 measure_text: Callable[[str, float, str | None], float]):
        self.ctx = context
        self.mapdata = mapdata
        self.measure_text = measure_text
        self.dropped_by_reason: dict[str, int] = defaultdict(int)

    def _in_canvas(self, x: float, y: float) -> bool:
        width, height = self.ctx.image.size
        return 0 <= x <= width and 0 <= y <= height

    def _style(self, item: FilteredLabel) -> dict:
        return dict(item.style)

    def _point(self, label: Label) -> tuple[float, float] | None:
        if label.position.x is None or label.position.z is None:
            self.dropped_by_reason["invalid_anchor"] += 1
            return None
        return self.ctx.transform(label.position.x, label.position.z)

    def _place_district(self, item: FilteredLabel, space: PlaceSpace) -> PlacedLabel | None:
        point = self._point(item.label)
        if point is None or not self._in_canvas(*point):
            self.dropped_by_reason["out_of_canvas"] += 1
            return None
        space.add_point("base_text", *point)
        return PlacedLabel(item.label, point[0], point[1], style=self._style(item))

    def _place_park(self, item: FilteredLabel, space: PlaceSpace) -> PlacedLabel | None:
        point = self._point(item.label)
        if point is None or not self._in_canvas(*point):
            self.dropped_by_reason["out_of_canvas"] += 1
            return None
        if any(math.hypot(point[0] - x, point[1] - y) < 150.0
               for x, y in space.points("base_text")):
            self.dropped_by_reason["collision"] += 1
            return None
        space.add_point("base_text", *point)
        return PlacedLabel(item.label, point[0], point[1], style=self._style(item))

    def _road_level(self, segment: Any, segment_type: Any) -> str:
        return classify_segment(segment, segment_type).value

    def _place_road(self, item: FilteredLabel, space: PlaceSpace) -> list[PlacedLabel]:
        label = item.label
        segment = _segment(self.mapdata, label.ref_id)
        if segment is None:
            self.dropped_by_reason["missing_reference"] += 1
            return []
        segment_types = getattr(self.mapdata, "segment_types", {}) or {}
        segment_type = (segment_types.get(getattr(segment, "name", ""))
                        or segment_types.get(getattr(segment, "icls", "")))
        if segment_type is None:
            self.dropped_by_reason["missing_reference"] += 1
            return []
        level = self._road_level(segment, segment_type)
        has_highways = any(bool(getattr(value, "highway", False))
                           for value in segment_types.values())
        abstract_level = int(getattr(self.ctx.detail_policy, "abstract_level", 0))
        if not visible_at_abstract_level(level, abstract_level, has_highways=has_highways):
            self.dropped_by_reason["road_hierarchy"] += 1
            return []

        cfg = item.style
        base_size = _resolve_size(cfg.get("size", 16), self.ctx.zoom)
        base_stroke = _resolve_size(cfg.get("stroke_width", 2), self.ctx.zoom)
        size_z = dict(cfg.get("size_z", {}) or {})
        size_z.setdefault("highway", 1.6)
        size_z.setdefault("main", size_z.get("road_wide", 1.25))
        size_z.setdefault("branch", size_z.get("road", 1.0))
        size_z.setdefault("special", size_z.get("walk", 0.75))
        size_z.setdefault("beautification", size_z.get("walk", 0.75))
        # road_repeat 和 road_min_gap 在 YAML 中与 labels.road 同级。
        labels_cfg = getattr(self.ctx.style, "get", lambda *_: {})
        spacing_base = float(labels_cfg("labels.road_repeat", 400.0))
        repeat_ratio = float(labels_cfg("labels.road_repeat_ratio", 3.0))
        scale_step = spacing_base * self.ctx.zoom
        mult = float(size_z.get(level, 1.0))
        size = max(base_size * mult, 4.0)
        stroke = max(base_stroke * mult, 0.5)
        text_width = self.measure_text(label.text, size, cfg.get("font")) + 2 * round(stroke)
        spacing = max(scale_step, text_width * repeat_ratio)
        from .road_paths import street_chains, text_path
        if not hasattr(self, "_street_chains"):
            self._street_chains = {chain.id: chain for chain in street_chains(self.mapdata)}
        chain = self._street_chains[label.ref_id]
        pixel_points = [self.ctx.transform(point.x, point.z) for point in chain.points]
        result: list[PlacedLabel] = []
        for middle, strips, box in text_path(pixel_points, text_width, spacing, stroke, max(size, 6) * 1.5):
            x, y, angle = middle
            if box[0] < 0 or box[1] < 0 or box[2] > self.ctx.image.width or box[3] > self.ctx.image.height:
                continue
            if space.overlaps(box):
                self.dropped_by_reason["collision"] += 1
                continue
            if any(math.hypot(x-qx, y-qy) < max(text_width, size) for qx, qy in space.points("base_text")):
                self.dropped_by_reason["collision"] += 1
                continue
            space.report_occupancy("base_text", box)
            result.append(PlacedLabel(label, x, y, angle=normalize_angle(angle), bbox=box, style=self._style(item),
                metadata={"size": size, "stroke_width": stroke, "road_level": level,
                          "text_run": strips, "run_width": text_width, "chain_ids": chain.members}))
        if not result:
            self.dropped_by_reason["no_sample"] += 1
        return result

    def place(self, label: Label, space: PlaceSpace, *, style=None,
              context=None) -> PlacedLabel | None:
        # 单项放置入口；批量道路重复采样由 place_all 提供。
        item = FilteredLabel(label, style or {})
        if label.type == LabelKind.DISTRICT_NAME:
            return self._place_district(item, space)
        if label.type == LabelKind.PARK_NAME:
            return self._place_park(item, space)
        placements = self._place_road(item, space) if label.type == LabelKind.ROAD_NAME else []
        return placements[0] if placements else None

    def place_all(self, items: Iterable[FilteredLabel], space: PlaceSpace) -> list[PlacedLabel]:
        items = list(items)
        # 先为行政区、再为公园、最后为道路预留空间；绘制阶段仍可按显式层级排序。
        order = {LabelKind.DISTRICT_NAME: 0, LabelKind.PARK_NAME: 1, LabelKind.ROAD_NAME: 2}
        ordered = sorted(enumerate(items), key=lambda pair: (order.get(pair[1].label.type, 9), pair[0]))
        result: list[PlacedLabel] = []
        for _index, item in ordered:
            if item.label.type == LabelKind.DISTRICT_NAME:
                placed = self._place_district(item, space)
                if placed is not None:
                    result.append(placed)
            elif item.label.type == LabelKind.PARK_NAME:
                placed = self._place_park(item, space)
                if placed is not None:
                    result.append(placed)
            elif item.label.type == LabelKind.ROAD_NAME:
                result.extend(self._place_road(item, space))
        return result


BASE_TEXT_PLACER = "legacy_a"
