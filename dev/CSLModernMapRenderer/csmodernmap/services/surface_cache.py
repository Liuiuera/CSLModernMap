"""按内存预算缓存编译后的表面模型。"""
from __future__ import annotations

from collections import OrderedDict
from threading import RLock

from ..cmm.surface_model import SurfaceModel


DEFAULT_SURFACE_CACHE_BYTES = 256 * 1024 * 1024


class SurfaceCache:
    def __init__(self, max_bytes: int = DEFAULT_SURFACE_CACHE_BYTES) -> None:
        self.max_bytes = max(0, int(max_bytes))
        self._items: OrderedDict[str, SurfaceModel] = OrderedDict()
        self._bytes = 0
        self._lock = RLock()

    def get(self, key: str) -> SurfaceModel | None:
        with self._lock:
            model = self._items.get(str(key))
            if model is not None:
                self._items.move_to_end(str(key))
            return model

    def put(self, model: SurfaceModel) -> SurfaceModel:
        with self._lock:
            old = self._items.pop(model.cache_key, None)
            if old is not None:
                self._bytes -= old.byte_size
            if model.byte_size > self.max_bytes:
                return model
            self._items[model.cache_key] = model
            self._bytes += model.byte_size
            self._evict()
        return model

    def _evict(self) -> None:
        while self._bytes > self.max_bytes and self._items:
            _key, model = self._items.popitem(last=False)
            self._bytes -= model.byte_size

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._bytes = 0

    @property
    def byte_size(self) -> int:
        with self._lock:
            return self._bytes

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)
