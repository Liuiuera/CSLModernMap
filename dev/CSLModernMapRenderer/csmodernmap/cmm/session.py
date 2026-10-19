"""管理地图文档会话及其派生服务缓存。

DocumentSession 封装 MapDocument，并按服务名和参数指纹缓存昂贵的派生结果。切换地图时释放整个会话，避免跨地图残留引用。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from .model import MapDocument
from .transit_domain import JobParams


def _fingerprint(params: dict[str, Any] | Any) -> str:
    """按键排序后序列化参数以生成稳定指纹；不可序列化值使用其文本表示。"""
    if params is None:
        return "none"
    if isinstance(params, str):
        return params
    try:
        text = json.dumps(params, sort_keys=True, default=repr)
    except (TypeError, ValueError):
        text = repr(params)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class ServiceCache:
    """按服务名和参数指纹缓存派生结果；会话对应单个文档，切换文档时整体释放。"""

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], Any] = {}
        self._hits: int = 0
        self._misses: int = 0

    def get_or_compute(
        self,
        service: str,
        params: dict[str, Any] | Any,
        compute: Callable[[], Any],
    ) -> Any:
        """返回已有服务结果，缺失时按参数计算并缓存。"""
        key = (service, _fingerprint(params))
        result = self._store.get(key)
        if result is not None:
            self._hits += 1
            return result
        self._misses += 1
        result = compute()
        self._store[key] = result
        return result

    def get(self, service: str, params: dict[str, Any] | Any) -> Any | None:
        key = (service, _fingerprint(params))
        return self._store.get(key)

    def put(self, service: str, params: dict[str, Any] | Any, value: Any) -> None:
        key = (service, _fingerprint(params))
        self._store[key] = value

    def invalidate(self, service: str | None = None) -> None:
        """清除全部缓存项或指定服务的缓存项。"""
        if service is None:
            self._store.clear()
        else:
            keys_to_remove = [k for k in self._store if k[0] == service]
            for k in keys_to_remove:
                del self._store[k]

    def keep_only(self, service: str, params: dict[str, Any] | Any) -> None:
        """Keep a single slot for *service* + *params*, dropping variants.

        全图级大对象（山影等）在会话内反复调参时会按参数累积多份
        全网格数组；调用方可在写入新值前用它把该服务限制为 1 槽。
        """
        keep = (service, _fingerprint(params))
        for key in [k for k in self._store
                    if k[0] == service and k != keep]:
            del self._store[key]

    @property
    def stats(self) -> dict[str, int]:
        return {
            "entries": len(self._store),
            "hits": self._hits,
            "misses": self._misses,
        }

    def __contains__(self, key: tuple[str, Any]) -> bool:
        service, params = key
        return (service, _fingerprint(params)) in self._store

    def __len__(self) -> int:
        return len(self._store)


@dataclass
class DocumentSession:
    """A document together with its derived service cache.

    Create one session per open document.  All renders (full, spot,
    tile, SVG, 拼图) share the same session so that expensive derivations
    like road scenes, water geometry and hillshade are computed once.
    """

    document: MapDocument
    service_cache: ServiceCache = field(default_factory=ServiceCache)
    _surface_cache: Any = field(default=None, init=False, repr=False)

    # 常用派生服务的便捷访问入口。

    def road_scene(self, params: dict[str, Any] | None = None) -> Any:
        """返回当前文档缓存的 RoadScene。"""
        from ..road_geometry import build_road_scene
        return self.service_cache.get_or_compute(
            "road_scene", params or {},
            lambda: build_road_scene(self.document))

    def road_order(self, *, accurate: bool = True) -> Any:
        """在预览和 PNG 之间共享特定模式的道路等级和边界。"""
        from ..preview_road_order import VERSION, build_road_order
        return self.service_cache.get_or_compute(
            "road_order", {"version": VERSION, "accurate": accurate},
            lambda: build_road_order(self.road_scene(), accurate=accurate))

    def water_geometry(self, params: dict[str, Any] | None = None) -> Any:
        """返回文档原生的缓存水域轮廓几何。"""
        from ..layers.water_layer import water_geometry_for_document
        return self.service_cache.get_or_compute(
            "water_geometry", params or {},
            lambda: water_geometry_for_document(self.document, **(params or {})))

    def ground_distance(self, params: dict[str, Any] | None = None) -> Any:
        """返回缓存的建成区距离字段。"""
        from ..ground_model import builtup_distance_field
        return self.service_cache.get_or_compute(
            "ground_distance", params or {},
            lambda: builtup_distance_field(self.document, **(params or {})))

    def surface_model(self, params: dict[str, Any] | None = None) -> Any:
        """返回预览和导出共享的渲染器无关字段。"""
        from ..services.surface_compiler import (
            SurfaceCompileOptions,
            SurfaceCompiler,
        )
        allowed = SurfaceCompileOptions.__dataclass_fields__
        options = SurfaceCompileOptions(**{
            key: value for key, value in (params or {}).items() if key in allowed
        })
        if self._surface_cache is None:
            from ..services.surface_cache import SurfaceCache
            self._surface_cache = SurfaceCache()
        return SurfaceCompiler(self._surface_cache).compile(
            self.document, options, fingerprint=self.surface_fingerprint())

    def surface_fingerprint(self) -> str:
        from ..services.surface_compiler import document_surface_fingerprint
        return self.service_cache.get_or_compute(
            "surface_fingerprint", {}, lambda: document_surface_fingerprint(self.document))

    def facility_catalog(self, mapping=None, *, legacy: bool = False) -> Any:
        from ..services.poi.facility import build_facility_groups
        from ..services.poi.mapping_registry import load_mapping_registry
        mapping = mapping if mapping is not None else load_mapping_registry()
        return self.service_cache.get_or_compute(
            "facility_catalog", {"mapping": asdict(mapping), "legacy": legacy},
            # POI 视图的区域锚点与文档原生目录不同。
            lambda: build_facility_groups(
                self._legacy_view() if legacy else self.document, mapping=mapping))

    def hillshade(self, params: dict[str, Any] | None = None) -> Any:
        """返回缓存的地形晕渲数组。"""
        return self.service_cache.get_or_compute(
            "hillshade", params or {},
            lambda: self._compute_hillshade(params or {}))

    def traffic_snapshot(self) -> Any:
        """返回缓存的类型化交通快照，缺失时为 None。"""
        from ..traffic import ensure_snapshot
        return self.service_cache.get_or_compute(
            "traffic_snapshot", {},
            lambda: ensure_snapshot(self.document.traffic))

    def station_name_index(self) -> Any:
        from ..services.transit.name import StationNameIndex
        return self.service_cache.get_or_compute(
            "station_name_index", {}, lambda: StationNameIndex(self._legacy_view()))

    def transit_model(self, params: JobParams | dict[str, Any] | None = None) -> Any:
        """返回缓存的交通渲染上下文。"""
        params = params if isinstance(params, JobParams) else JobParams(**(params or {}))
        return self.service_cache.get_or_compute(
            "transit_model", asdict(params),
            lambda: self._build_transit_model(params))

    def poi_catalog(self, params: dict[str, Any] | None = None, *, override_path: str | None = None) -> Any:
        """返回缓存的 POI 目录。"""
        from ..services.poi.service import produce
        return self.service_cache.get_or_compute(
            "poi_catalog", {"params": params or {}, "override": override_path},
            lambda: produce(self._legacy_view(), params, override_path=override_path,
                            session=self))

    def labels(self, params: dict[str, Any] | None = None) -> Any:
        """返回缓存的标注流程结果。"""
        return self.service_cache.get_or_compute(
            "labels", params or {},
            lambda: self._build_labels(params or {}))

    # 内部辅助入口。

    def _legacy_view(self) -> Any:
        """返回由标准文档支持的延迟 MapData 兼容视图；字段按需读取并在同文档渲染中共享。"""
        from .document_view import MapDocumentView
        return self.service_cache.get_or_compute(
            "_legacy_view", {},
            lambda: MapDocumentView(self.document))

    def _compute_hillshade(self, params: dict) -> Any:
        from ..terrain import hillshade as _hillshade
        terrain = self.document.terrain
        height = terrain.height.values
        if height is None:
            return None
        cell = terrain.cell or 16.0
        return _hillshade(height, cell, **{
            k: v for k, v in params.items()
            if k in ("azimuth", "altitude", "exaggeration")
        })

    def _build_transit_model(self, params: JobParams) -> Any:
        from ..services.transit import build_transit_model
        return build_transit_model(self._legacy_view(), params,
                                   name_index=self.station_name_index())

    def _build_labels(self, params: dict) -> Any:
        from ..labeling import LabelPipeline
        return LabelPipeline().run(self._legacy_view(), params)

    # 会话生命周期管理。

    def release(self) -> None:
        """清除所有缓存的派生结果。"""
        self.service_cache.invalidate()
        if self._surface_cache is not None:
            self._surface_cache.clear()

    def __getstate__(self) -> dict[str, Any]:
        """从分块工作进程载荷中排除进程局部表面缓存及其锁，避免无效且昂贵的序列化。"""
        state = dict(self.__dict__)
        state["_surface_cache"] = None
        return state

    def __del__(self) -> None:
        self.release()
