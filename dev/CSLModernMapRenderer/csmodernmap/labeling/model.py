"""定义标注候选对象和渲染作业数据结构。

这些对象承载标注身份，不包含 Pillow 对象、字体或放置状态；渲染器仍使用兼容的 MapData。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from ..cmm.model import (
    Label, LabelAnchor, LabelKind, LabelSource,
    RenderJob as CanonicalRenderJob,
    derive_label_id, label_id, stable_label_id,
)

# 旧版兼容别名计划于 2027 年 6 月 30 日移除；新调用方应从 CMM 或 render_jobs 导入 RenderJob。
RenderJob = CanonicalRenderJob


@dataclass(frozen=True)
class FilteredLabel:
    """保存一次渲染中已解析主题样式的标注候选对象。"""

    label: Label
    style: Mapping[str, Any]


@dataclass(frozen=True)
class PlacedLabel:
    """放置阶段的临时输出，不作为地图身份序列化。"""

    label: Label
    x: float
    y: float
    angle: float = 0.0
    bbox: tuple[float, float, float, float] | None = None
    style: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def position(self) -> tuple[float, float]:
        return (self.x, self.y)
