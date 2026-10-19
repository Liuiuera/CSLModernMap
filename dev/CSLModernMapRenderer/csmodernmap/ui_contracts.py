"""定义界面与渲染边界共享的轻量数据约定。

界面迁移期间，各阶段通过显式值对象传递数据，避免 Qt 控件与渲染子进程相互依赖；值对象可转换为字典以通过现有进程协议传输。"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


# 旧请求仍可识别 SVG 格式，但产品暂不开放该输出。
AVAILABLE_EXPORT_FORMATS = ("PNG", "HTML")


class ContextMode(str, Enum):
    """表示界面当前显示的地图视图上下文。"""

    NORMAL = "normal"
    TRANSIT = "transit"
    METRO = "metro"
    RANDOM_SPOT = "random_spot"
    SELECTION = "selection"

    @classmethod
    def _missing_(cls, value: object) -> "ContextMode | None":
        aliases = {
            "normal": cls.NORMAL,
            "transit": cls.TRANSIT,
            "metro": cls.METRO,
            "random_spot": cls.RANDOM_SPOT,
            "selection": cls.SELECTION,
            "map": cls.NORMAL,
            "default": cls.NORMAL,
            "spot": cls.RANDOM_SPOT,
            "random": cls.RANDOM_SPOT,
        }
        if isinstance(value, str):
            return aliases.get(value.strip().lower())
        return None


MAP_OUTPUT_SIZES = (1024, 1536, 2048, 3072, 4096, 6144, 7680, 12288, 16384, 32768)
METRO_OUTPUT_SIZES = (0, 1024, 2048, 4096, 8192, 16384, 32768)


def export_capabilities(context: ContextMode | str) -> dict:
    mode = ContextMode(context)
    metro = mode == ContextMode.METRO
    return {"formats": list(("PNG",) if mode in (ContextMode.TRANSIT, ContextMode.METRO) else AVAILABLE_EXPORT_FORMATS),
            "sizes": list(METRO_OUTPUT_SIZES if metro else MAP_OUTPUT_SIZES),
            "customSize": not metro, "region": not metro, "pngPoi": not metro}


class PreviewState(str, Enum):
    """记录常驻地图预览的生命周期状态。"""

    NO_MAP = "no_map"
    LOADING_SCENE = "loading_scene"
    READY = "ready"
    ERROR = "error"

    # 为无需区分场景加载与其他加载操作的调用方提供简短别名。
    LOADING = "loading_scene"

    @classmethod
    def _missing_(cls, value: object) -> "PreviewState | None":
        aliases = {
            "no_map": cls.NO_MAP,
            "loading_scene": cls.LOADING_SCENE,
            "ready": cls.READY,
            "error": cls.ERROR,
            "none": cls.NO_MAP,
            "loading": cls.LOADING_SCENE,
            "scene_loading": cls.LOADING_SCENE,
        }
        if isinstance(value, str):
            return aliases.get(value.strip().lower())
        return None


def _copy_value(value: Any) -> Any:
    """复制适合 JSON 传输的值，而不引入通用序列化器。"""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _copy_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_copy_value(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _as_mapping(value: Mapping[str, Any] | None, *, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    return _copy_value(value)


def _as_enum(value: Any, enum_type: type[Enum], *, name: str) -> Enum:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {name}: {value!r}") from exc


@dataclass(frozen=True, slots=True)
class RenderRegion:
    """表示预览和导出共用的世界坐标范围，并在构造时规范化拖动方向。"""

    bounds: tuple[float, float, float, float]
    source: str = "map"
    output_size: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        try:
            raw = tuple(float(value) for value in self.bounds)
        except (TypeError, ValueError) as exc:
            raise ValueError("bounds must contain four numbers") from exc
        if len(raw) != 4 or not all(math.isfinite(value) for value in raw):
            raise ValueError("bounds must contain four finite numbers")
        x0, z0, x1, z1 = raw
        object.__setattr__(self, "bounds", (min(x0, x1), min(z0, z1),
                                              max(x0, x1), max(z0, z1)))

        source = str(self.source or "map").strip().lower()
        if not source:
            source = "map"
        object.__setattr__(self, "source", source)

        if self.output_size is None:
            return
        try:
            size = tuple(int(value) for value in self.output_size)
        except (TypeError, ValueError) as exc:
            raise ValueError("output_size must contain two integers") from exc
        if len(size) != 2 or any(value < 1 for value in size):
            raise ValueError("output_size must contain two positive integers")
        object.__setattr__(self, "output_size", size)

    @property
    def x0(self) -> float:
        return self.bounds[0]

    @property
    def z0(self) -> float:
        return self.bounds[1]

    @property
    def x1(self) -> float:
        return self.bounds[2]

    @property
    def z1(self) -> float:
        return self.bounds[3]

    @property
    def kind(self) -> str:
        """兼容将来源字段称为类别的旧调用方。"""
        return self.source

    @classmethod
    def from_bounds(cls, bounds: Sequence[float], *, source: str = "map",
                    output_size: Sequence[int] | None = None) -> "RenderRegion":
        return cls(bounds=tuple(bounds), source=source,
                   output_size=None if output_size is None
                   else tuple(output_size))

    def to_dict(self) -> dict[str, Any]:
        return {
            "bounds": list(self.bounds),
            "source": self.source,
            "output_size": (None if self.output_size is None
                             else list(self.output_size)),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | Sequence[float]) -> "RenderRegion":
        if isinstance(value, Mapping):
            bounds = value.get("bounds")
            if bounds is None:
                bounds = (value.get("x0"), value.get("z0"),
                          value.get("x1"), value.get("z1"))
            source = value.get("source", value.get("kind", "map"))
            output_size = value.get("output_size", value.get("size"))
            return cls(bounds=bounds, source=source,
                       output_size=output_size)
        return cls(bounds=value)


@dataclass(frozen=True, slots=True)
class ExportRequest:
    """保存一次地图导出所需的全部输入。"""

    format: str = "PNG"
    target: str | Path | None = None
    output_dir: str | Path | None = None
    region: RenderRegion | None = None
    context: ContextMode = ContextMode.NORMAL
    theme_snapshot: Mapping[str, Any] = field(default_factory=dict)
    layer_snapshot: Mapping[str, Any] = field(default_factory=dict)
    auto_open: bool = True
    png_poi_options: Mapping[str, Any] = field(default_factory=dict)
    render_snapshot: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        fmt = str(self.format or "").strip().lower().lstrip(".")
        formats = {
            "png": "PNG", "svg": "SVG", "html": "HTML",
            "rmg": "RMG", "rmp": "RMP",
        }
        if fmt not in formats:
            raise ValueError(f"unsupported export format: {self.format!r}")
        object.__setattr__(self, "format", formats[fmt])

        for field_name in ("target", "output_dir"):
            value = getattr(self, field_name)
            if value in (None, ""):
                normalized = None
            else:
                normalized = str(value)
            object.__setattr__(self, field_name, normalized)

        if self.region is not None and not isinstance(self.region, RenderRegion):
            object.__setattr__(self, "region", RenderRegion.from_dict(self.region))
        object.__setattr__(self, "context", _as_enum(
            self.context, ContextMode, name="context"))
        object.__setattr__(self, "theme_snapshot",
                           _as_mapping(self.theme_snapshot,
                                       name="theme_snapshot"))
        object.__setattr__(self, "layer_snapshot",
                           _as_mapping(self.layer_snapshot,
                                       name="layer_snapshot"))
        object.__setattr__(self, "auto_open", bool(self.auto_open))
        object.__setattr__(self, "png_poi_options", _as_mapping(
            self.png_poi_options, name="png_poi_options"))
        object.__setattr__(self, "render_snapshot", _as_mapping(
            self.render_snapshot, name="render_snapshot"))

    @property
    def context_mode(self) -> ContextMode:
        return self.context

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "target": self.target,
            "output_dir": self.output_dir,
            "region": None if self.region is None else self.region.to_dict(),
            "context": self.context.value,
            "theme_snapshot": _copy_value(self.theme_snapshot),
            "layer_snapshot": _copy_value(self.layer_snapshot),
            "auto_open": self.auto_open,
            "png_poi_options": _copy_value(self.png_poi_options),
            "render_snapshot": _copy_value(self.render_snapshot),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExportRequest":
        if not isinstance(value, Mapping):
            raise TypeError("ExportRequest data must be a mapping")
        raw_region = value.get("region")
        return cls(
            format=value.get("format", "PNG"),
            target=value.get("target"),
            output_dir=value.get("output_dir"),
            region=(None if raw_region is None
                    else RenderRegion.from_dict(raw_region)),
            context=value.get("context", value.get("context_mode", "normal")),
            theme_snapshot=value.get("theme_snapshot") or {},
            layer_snapshot=value.get("layer_snapshot") or {},
            auto_open=value.get("auto_open", True),
            png_poi_options=value.get("png_poi_options") or {},
            render_snapshot=value.get("render_snapshot") or {},
        )


__all__ = [
    "ContextMode",
    "ExportRequest",
    "PreviewState",
    "RenderRegion",
]
