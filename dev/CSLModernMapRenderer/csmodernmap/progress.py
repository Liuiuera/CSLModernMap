"""定义统一的进度事件协议和阶段计时。

ProgressEvent 取代旧的两参数和三参数回调混用协议；StageTimer 记录加载、派生、渲染和输出阶段的耗时。"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ProgressEvent:
    """表示从工作进程传往界面的单个进度事件，包含当前阶段、阶段进度和总进度。"""

    phase: str
    phase_fraction: float | None
    overall_fraction: float
    message: str


class ProgressEmitter:
    """将渲染器旧式进度回调转换为 ProgressEvent 并转发给接收端。"""

    def __init__(
        self,
        sink: Callable[[ProgressEvent], None] | Any,
        *,
        phase: str = "render",
    ) -> None:
        self._sink = sink
        self._phase = phase
        # 保留 supports_stage_progress 供仍检查该属性的渲染代码兼容。
        self.supports_stage_progress = True

    @property
    def phase(self) -> str:
        return self._phase

    @phase.setter
    def phase(self, value: str) -> None:
        self._phase = value

    def __call__(
        self,
        pct: float,
        msg: str,
        stage: tuple[str, float] | None = None,
    ) -> None:
        """向接收端发送进度事件；stage 可携带图层名和图层内部进度。"""
        phase_frac = None
        if stage is not None:
            phase_frac = max(0.0, min(1.0, float(stage[1])))
        event = ProgressEvent(
            phase=self._phase,
            phase_fraction=phase_frac,
            overall_fraction=max(0.0, min(1.0, float(pct))),
            message=msg,
        )
        self._sink(event)


class StageTimer:
    """按阶段记录任务耗时，并以 task_total 覆盖计时器的完整生命周期。"""

    def __init__(self) -> None:
        self._task_start = time.perf_counter()
        self._open = {}
        self._durations = {}

    def start(self, name):
        """开始记录指定阶段的耗时。"""
        self._open[name] = time.perf_counter()

    def end(self, name):
        """结束指定阶段计时并保存耗时。"""
        start = self._open.pop(name, None)
        if start is None:
            return
        elapsed = time.perf_counter() - start
        existing = self._durations.get(name, 0.0)
        self._durations[name] = existing + elapsed

    def record(self, name, duration_s):
        """手动记录一个阶段的耗时。"""
        existing = self._durations.get(name, 0.0)
        self._durations[name] = existing + duration_s

    def finish(self):
        """返回各阶段耗时和任务总耗时。"""
        for name in list(self._open):
            self.end(name)
        result = dict(self._durations)
        result["task.total"] = time.perf_counter() - self._task_start
        return result

    def merge_into(self, stats):
        """将阶段耗时合并到已有统计字典。"""
        stats.update(self.finish())


__all__ = [
    "ProgressEvent",
    "ProgressEmitter",
    "StageTimer",
]
