"""异步预览请求控制器。

主题编辑器和首次运行向导都需要同样的预览行为：输入变化后延迟启动，
只让最后一次请求的结果回填界面，旧请求即使在线程中完成也必须丢弃。
本模块不依赖 Tk 控件，只依赖调用方提供的 ``after`` 风格调度函数，
因此可以被不同的窗口和测试复用。
"""
from __future__ import annotations

import threading
from typing import Callable, Hashable, Optional


class PreviewRequestController:
    """管理可取消、防抖、过期丢弃的后台预览请求。

    ``schedule`` 应接受 ``(delay_ms, callback)``，返回可传给
    ``cancel_schedule`` 的任务标识。Tk 的 ``after`` / ``after_cancel``
    可以直接传入。

    同一控制器始终最多运行一个渲染线程；运行期间同一目标的新请求只保留
    最后一个，不同目标按提交顺序继续渲染。旧结果会在回调前检查令牌，
    因此既不并发抢占 CPU，也不会覆盖新预览。
    """

    def __init__(
        self,
        schedule: Callable[[int, Callable[[], None]], object],
        cancel_schedule: Optional[Callable[[object], None]] = None,
    ) -> None:
        self._schedule = schedule
        self._cancel_schedule = cancel_schedule
        self._lock = threading.Lock()
        self._serial = 0
        self._latest: dict[Hashable, int] = {}
        self._pending: dict[Hashable, object] = {}
        self._running = False
        # 不同目标（例如首次向导里的多张主题卡片）都必须得到渲染机会；
        # 同一目标反复变化时则只保留它自己的最后一次请求。
        self._queued: dict[Hashable, tuple] = {}
        self._closed = False

    def request(
        self,
        target: Hashable,
        render: Callable[[], object],
        on_success: Callable[[object], None],
        on_error: Callable[[Exception], None],
        *,
        delay_ms: int = 300,
    ) -> int:
        """提交一个预览请求并返回请求令牌。"""
        with self._lock:
            if self._closed:
                return -1
            self._serial += 1
            token = self._serial
            self._latest[target] = token
            pending = self._pending.pop(target, None)
            if pending is not None:
                self._cancel_pending(pending)

        def start() -> None:
            with self._lock:
                if self._closed or self._latest.get(target) != token:
                    return
                self._pending.pop(target, None)
            self._launch_or_queue(
                (target, token, render, on_success, on_error))

        try:
            job = self._schedule(max(0, int(delay_ms)), start)
        except Exception:
            return token
        with self._lock:
            if self._closed or self._latest.get(target) != token:
                self._cancel_pending(job)
            else:
                self._pending[target] = job
        return token

    def invalidate(self, target: Hashable) -> None:
        """使某个预览目标的在途结果全部失效。"""
        with self._lock:
            self._serial += 1
            self._latest[target] = self._serial
            pending = self._pending.pop(target, None)
            if pending is not None:
                self._cancel_pending(pending)
            self._queued.pop(target, None)

    def cancel(self, target: Hashable | None = None) -> None:
        """取消等待中的任务，并使已启动任务的回调失效。"""
        with self._lock:
            targets = list(self._latest) if target is None else [target]
            for item in targets:
                self._serial += 1
                self._latest[item] = self._serial
                pending = self._pending.pop(item, None)
                if pending is not None:
                    self._cancel_pending(pending)
                self._queued.pop(item, None)

    def close(self) -> None:
        """关闭控制器；关闭后提交请求和回填回调都会被忽略。"""
        with self._lock:
            self._closed = True
            self._queued.clear()
            pending = list(self._pending.values())
            self._pending.clear()
            self._latest.clear()
            for job in pending:
                self._cancel_pending(job)

    def _launch_or_queue(self, request) -> None:
        """全控制器只运行一个渲染；在途时仅保留最后一个请求。"""
        with self._lock:
            if self._closed:
                return
            if self._running:
                target = request[0]
                self._queued[target] = request
                return
            self._running = True
        self._start_worker(request)

    def _start_worker(self, request) -> None:
        target, token, render, on_success, on_error = request

        def worker() -> None:
            try:
                result = render()
            except Exception as exc:  # noqa: BLE001
                self._dispatch(target, token,
                               lambda error=exc: on_error(error))
            else:
                self._dispatch(target, token, lambda: on_success(result))
            finally:
                self._worker_finished()

        threading.Thread(target=worker, name="style-preview",
                         daemon=True).start()

    def _worker_finished(self) -> None:
        next_request = None
        with self._lock:
            self._running = False
            if self._closed:
                self._queued.clear()
                return
            while self._queued:
                target, queued = next(iter(self._queued.items()))
                self._queued.pop(target, None)
                token = queued[1]
                if self._latest.get(target) == token:
                    self._running = True
                    next_request = queued
                    break
        if next_request is not None:
            self._start_worker(next_request)

    def _dispatch(
        self,
        target: Hashable,
        token: int,
        callback: Callable[[], None],
    ) -> None:
        def apply() -> None:
            with self._lock:
                if self._closed or self._latest.get(target) != token:
                    return
            try:
                callback()
            except Exception:
                # UI 回填失败（例如窗口刚关闭）不能让后台线程产生未处理异常。
                return

        try:
            self._schedule(0, apply)
        except Exception:
            return

    def _cancel_pending(self, job: object) -> None:
        if self._cancel_schedule is None:
            return
        try:
            self._cancel_schedule(job)
        except Exception:
            return
