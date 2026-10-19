"""登记标注流程使用的收集器与放置器。"""
from __future__ import annotations

from collections.abc import Callable, Mapping


class Registry:
    def __init__(self, values: Mapping[str, Callable] | None = None) -> None:
        self._values = dict(values or {})

    def register(self, key: str, value: Callable, *, replace: bool = False) -> Callable:
        key = str(key)
        if key in self._values and not replace:
            raise KeyError(f"registry already contains {key!r}")
        self._values[key] = value
        return value

    def get(self, key: str, default=None):
        return self._values.get(str(key), default)

    def require(self, key: str) -> Callable:
        value = self.get(key)
        if value is None:
            raise KeyError(f"registry has no entry for {key!r}")
        return value

    def keys(self) -> tuple[str, ...]:
        return tuple(self._values)

    def items(self):
        return self._values.items()

    def as_dict(self) -> dict[str, Callable]:
        return dict(self._values)


CollectorRegistry = Registry
PlacerRegistry = Registry
PainterRegistry = Registry


# 默认登记表保持可修改，新增标注类别只需登记，无须修改流程循环。
COLLECTOR_REGISTRY = CollectorRegistry()
PLACER_REGISTRY = PlacerRegistry()
PAINTER_REGISTRY = PainterRegistry()


def register_collector(key: str, value: Callable, *, replace: bool = False) -> Callable:
    return COLLECTOR_REGISTRY.register(key, value, replace=replace)


def register_placer(key: str, value: Callable, *, replace: bool = False) -> Callable:
    return PLACER_REGISTRY.register(key, value, replace=replace)


def register_painter(key: str, value: Callable, *, replace: bool = False) -> Callable:
    return PAINTER_REGISTRY.register(key, value, replace=replace)
