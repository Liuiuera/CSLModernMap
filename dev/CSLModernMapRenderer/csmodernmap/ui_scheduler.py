"""定义独立于界面框架的任务调度约定。"""

from __future__ import annotations

from typing import Callable, Protocol, TypeAlias


ScheduleToken: TypeAlias = object


class UIScheduler(Protocol):
    """将回调派发到所属界面线程。"""

    def call_soon(self, callback: Callable[[], None]) -> ScheduleToken: ...

    def call_later(
        self, delay_ms: int, callback: Callable[[], None]
    ) -> ScheduleToken: ...

    def cancel(self, token: ScheduleToken) -> None: ...
