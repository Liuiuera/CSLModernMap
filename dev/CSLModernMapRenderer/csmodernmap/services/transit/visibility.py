"""定义交通图层共享的可见性规则。"""
from __future__ import annotations

from ...cmm import JobParams, TransitVisibility, normalize_transit_kind
from ...detail import OVERVIEW_TRANSIT_TYPES, STANDARD_TRANSIT_TYPES


def build_visibility(params: JobParams | None = None, available=()) -> TransitVisibility:
    params = params or JobParams()
    enabled = params.normalized_enabled(available)
    raw_mode = params.detail.value if hasattr(params.detail, "value") else params.detail
    mode = str(raw_mode or "auto").casefold()
    if mode == "overview":
        allowed = {normalize_transit_kind(item).value for item in OVERVIEW_TRANSIT_TYPES}
        gate = {kind.value: 2 for kind in enabled if kind.value in allowed}
    elif mode == "standard":
        allowed = {normalize_transit_kind(item).value for item in STANDARD_TRANSIT_TYPES}
        gate = {kind.value: 1 for kind in enabled if kind.value in allowed}
    elif mode == "detailed":
        gate = {kind.value: 0 for kind in enabled}
    else:
        # 自动消费者自行根据缩放选取具体级别；策略仅允许已启用类型。
        gate = {kind.value: 3 for kind in enabled}
    svg = {kind.value: int(params.svg_lod_mask) for kind in enabled}
    return TransitVisibility(
        enabled_kinds=tuple(enabled),
        detail_gate=gate,
        label_min_zoom=params.label_min_zoom,
        station_min_zoom=params.station_min_zoom,
        svg_visibility=svg,
    )


class VisibilityService:
    build = staticmethod(build_visibility)
