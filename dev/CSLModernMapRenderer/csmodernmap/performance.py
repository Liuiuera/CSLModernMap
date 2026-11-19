"""低开销、默认关闭的性能诊断记录器。

性能优化项目需要统一记录墙钟时间、进程 CPU 时间、峰值内存、阶段耗时
和处理数量。正常用户运行时不启用诊断，粗粒度步骤只做上下文检查，
避免把计时和日志开销带入普通路径。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import threading
import math
from contextvars import ContextVar
from functools import wraps
from contextlib import contextmanager, nullcontext
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


SCHEMA_VERSION = 2
_active_recorder = ContextVar("performance_recorder", default=None)
_active_step = ContextVar("performance_step", default=None)
PERFORMANCE_ENV = "CSLM_PERF"
DEBUG_PERFORMANCE_ENV = "CSLM_DEBUG_PERF"


def enabled_from_environment() -> bool:
    """返回是否通过环境变量开启诊断。"""
    return os.environ.get(PERFORMANCE_ENV, "").strip().lower() in {
        "1", "true", "yes", "on", "诊断", "开启",
    }


def debug_performance_enabled() -> bool:
    """返回是否开启自动性能日志模式。"""
    values = {
        os.environ.get(PERFORMANCE_ENV, ""),
        os.environ.get(DEBUG_PERFORMANCE_ENV, ""),
    }
    return any(str(value).strip().lower() in {
        "1", "true", "yes", "on", "诊断", "开启",
    } for value in values)


def default_log_path(label: str = "performance") -> Path:
    """返回一个不会覆盖旧文件的自动日志路径。"""
    from ._paths import writable_dir

    safe_label = re.sub(r"[^0-9A-Za-z_-]+", "_", str(label or "performance"))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return (
        writable_dir() / "performance_logs"
        / f"{safe_label}-{stamp}-pid{os.getpid()}.json"
    )


def _peak_rss_bytes() -> int | None:
    """返回进程峰值工作集/常驻内存，检测失败时返回 ``None``。"""
    try:
        if sys.platform == "win32":
            import ctypes

            class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("cb", ctypes.c_ulong),
                    ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = _PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(counters)
            try:
                import ctypes.wintypes as wintypes

                query = ctypes.windll.kernel32.K32GetProcessMemoryInfo
            except AttributeError:
                import ctypes.wintypes as wintypes

                query = ctypes.windll.psapi.GetProcessMemoryInfo
            query.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(_PROCESS_MEMORY_COUNTERS),
                wintypes.DWORD,
            ]
            query.restype = wintypes.BOOL
            ok = query(
                ctypes.windll.kernel32.GetCurrentProcess(),
                ctypes.byref(counters),
                counters.cb,
            )
            if ok:
                return int(counters.PeakWorkingSetSize)
        else:
            import resource

            value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            return value * 1024 if sys.platform.startswith("linux") else value
    except Exception:  # noqa: BLE001 - 诊断不能影响正式渲染
        return None
    return None


class PerformanceRecorder:
    """一次渲染/导出任务的结构化性能记录。"""

    def __init__(
        self,
        *,
        label: str = "",
        metadata: dict[str, Any] | None = None,
        enabled: bool = True,
        max_steps: int = 4096,
    ) -> None:
        self.enabled = bool(enabled)
        self.label = str(label or "")
        self.metadata: dict[str, Any] = dict(metadata or {})
        self._wall_start = time.perf_counter()
        self._cpu_start = time.process_time()
        self.spans: dict[str, dict[str, float | int]] = {}
        self.counters: dict[str, int | float] = {}
        self._lock = threading.RLock()
        self.steps: list[dict[str, Any]] = []
        self.max_steps = max(0, int(max_steps))
        self.dropped_steps = 0
        self._next_id = 0

    @contextmanager
    def span(self, name: str) -> Iterator[None]:
        """记录一个可重复执行的阶段；同名阶段自动聚合。"""
        if not self.enabled:
            yield
            return
        wall_start = time.perf_counter()
        cpu_start = time.thread_time()
        with self._lock:
            step_id = self._next_id
            self._next_id += 1
        parent = _active_step.get() if _active_recorder.get() is self else None
        recorder_token = _active_recorder.set(self)
        step_token = _active_step.set(step_id)
        outcome = "ok"
        try:
            yield
        except BaseException:
            outcome = "error"
            raise
        finally:
            wall = time.perf_counter() - wall_start
            cpu = time.thread_time() - cpu_start
            self.record_duration(name, wall, cpu)
            self.add_step({"id": step_id, "parent_id": parent, "name": str(name),
                           "runtime": "python", "thread": threading.current_thread().name,
                           "start_s": wall_start - self._wall_start,
                           "wall_s": wall, "cpu_s": cpu, "outcome": outcome})
            _active_step.reset(step_token)
            _active_recorder.reset(recorder_token)

    def add_step(self, step: dict[str, Any]) -> None:
        """保存有限长度的操作时间线，不记录逐要素样本。"""
        if not self.enabled:
            return
        with self._lock:
            if len(self.steps) < self.max_steps:
                self.steps.append(dict(step))
            else:
                self.dropped_steps += 1

    def merge_browser(self, browser: dict[str, Any]) -> None:
        """浏览器时间戳使用相对于场景加载的独立时钟。"""
        steps = browser.get("steps")
        if not isinstance(steps, list):
            steps = []
        for step in steps[:self.max_steps]:
            try:
                start = float(step["startMs"]) / 1000
                duration = float(step["durationMs"]) / 1000
                if not all(math.isfinite(v) and v >= 0 for v in (start, duration)):
                    continue
                name = "viewer." + str(step["name"])
                self.record_duration(name, duration)
                self.add_step({"name": name, "runtime": "browser", "start_s": start,
                               "wall_s": duration, "outcome": step.get("outcome", "ok")})
            except (KeyError, TypeError, ValueError):
                continue
        self.update_metadata(browser_completion=browser.get("completion", "legacy-two-raf"))
        if "workerCount" in browser:
            self.update_metadata(browser_worker_count=browser["workerCount"])

    def record_duration(
        self, name: str, wall_s: float, cpu_s: float = 0.0, *, count: int = 1,
    ) -> None:
        """记录由其他进程/运行时测得的阶段耗时。"""
        if not self.enabled:
            return
        with self._lock:
            item = self.spans.setdefault(
                str(name), {"count": 0, "wall_s": 0.0, "cpu_s": 0.0})
            item["count"] = int(item["count"]) + int(count)
            item["wall_s"] = float(item["wall_s"]) + float(wall_s)
            item["cpu_s"] = float(item["cpu_s"]) + float(cpu_s)

    def add_counter(self, name: str, value: int | float = 1) -> None:
        """累加一个数量型指标。"""
        if not self.enabled:
            return
        with self._lock:
            current = self.counters.get(str(name), 0)
            self.counters[str(name)] = current + value

    def set_counter(self, name: str, value: int | float) -> None:
        """设置一个数量型指标。"""
        if self.enabled:
            with self._lock:
                self.counters[str(name)] = value

    def update_metadata(self, **values: Any) -> None:
        """安全地补充任务生命周期中稍后才知道的元数据。"""
        if self.enabled:
            with self._lock:
                self.metadata.update(values)

    def finish(self) -> dict[str, Any]:
        """生成当前快照。"""
        if not self.enabled:
            return {
                "schema_version": SCHEMA_VERSION,
                "label": self.label,
                "diagnostics_enabled": False,
                "metadata": dict(self.metadata),
                "spans": {},
                "counters": {},
                "steps": [],
            }
        with self._lock:
            return {
                "schema_version": SCHEMA_VERSION,
                "label": self.label,
                "diagnostics_enabled": True,
                "metadata": dict(self.metadata),
                "wall_s": time.perf_counter() - self._wall_start,
                "process_cpu_s": time.process_time() - self._cpu_start,
                "peak_memory_bytes": _peak_rss_bytes(),
                "spans": {
                    name: {
                        "count": int(value["count"]),
                        "wall_s": float(value["wall_s"]),
                        "cpu_s": float(value["cpu_s"]),
                    }
                    for name, value in sorted(self.spans.items())
                },
                "counters": dict(sorted(self.counters.items())),
                "steps": sorted((dict(step) for step in self.steps),
                                key=lambda step: (step["runtime"], step["start_s"])),
                "dropped_steps": self.dropped_steps,
                "timing_notes": "Steps are inclusive and may overlap; do not sum them. "
                                "Python/browser clocks are separate. Step CPU is thread CPU; "
                                "process CPU and peak memory cover the Python process only.",
            }

    def write_json(self, path: str | os.PathLike[str]) -> dict[str, Any]:
        """以 UTF-8 JSON 写出当前快照，并返回该快照。"""
        result = self.finish()
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result


def perf_span(recorder: PerformanceRecorder | None, name: str):
    """返回诊断阶段上下文；未启用时返回空上下文。"""
    recorder = recorder if recorder is not None else _active_recorder.get()
    return recorder.span(name) if recorder is not None else nullcontext()


def current_recorder() -> PerformanceRecorder | None:
    return _active_recorder.get()


def measured(name: str):
    """仅使用当前任务的记录器对粗粒度操作计时。"""
    def decorate(function):
        @wraps(function)
        def wrapper(*args, **kwargs):
            recorder = current_recorder()
            if recorder is None or not recorder.enabled:
                return function(*args, **kwargs)
            with recorder.span(name):
                return function(*args, **kwargs)
        return wrapper
    return decorate


__all__ = [
    "DEBUG_PERFORMANCE_ENV",
    "PERFORMANCE_ENV",
    "PerformanceRecorder",
    "SCHEMA_VERSION",
    "debug_performance_enabled",
    "default_log_path",
    "enabled_from_environment",
    "perf_span",
    "measured",
    "current_recorder",
]
