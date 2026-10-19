"""POI 编辑器内存态：单一可变数据源，GUI 与磁盘解耦。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EditorState:
    """编辑器工作区。变更经 touch() 标记，保存时才落盘。"""
    categories: dict = field(default_factory=dict)   # 类别映射保存中文名称、图标键、优先级和最小缩放级别。
    icons: dict = field(default_factory=dict)        # 图标映射保存标识、名称和文件路径。
    mapping_ai: dict = field(default_factory=dict)   # ai_type -> poi_kind（只读）
    groups: dict = field(default_factory=dict)       # 板块 -> [kind]
    _dirty: bool = False
    _dirty_categories: set = field(default_factory=set)

    def touch(self, kind: str) -> None:
        self._dirty = True
        self._dirty_categories.add(kind)

    def mark_clean(self) -> None:
        self._dirty = False
        self._dirty_categories.clear()
