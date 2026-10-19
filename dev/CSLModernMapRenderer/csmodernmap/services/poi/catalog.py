"""从标准映射表生成渲染使用的 POI 定义。"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class DedupeSpec:
    enabled: bool = True
    distance_m: float = 48.0
    same_name: bool = True
    same_icon: bool = True

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "DedupeSpec":
        value = value or {}
        return cls(
            enabled=bool(value.get("enabled", True)),
            distance_m=max(float(value.get("distance_m", value.get("distance", 48.0))), 0.0),
            same_name=bool(value.get("same_name", True)),
            same_icon=bool(value.get("same_icon", True)),
        )

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "distance_m": self.distance_m,
            "same_name": self.same_name,
            "same_icon": self.same_icon,
        }


@dataclass(frozen=True)
class PoiKindSpec:
    kind: str
    label_zh: str
    title_zh: str
    icon_key: str
    priority: int
    min_zoom: float
    pool_key: str = ""
    grid_multiplier: float = 1.0
    draw_name: bool = True
    scopes: tuple[str, ...] = ("normal", "svg")
    dedupe: DedupeSpec = field(default_factory=DedupeSpec)
    deprecated: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", str(self.kind))
        object.__setattr__(self, "label_zh", str(self.label_zh or self.kind))
        object.__setattr__(self, "title_zh", str(self.title_zh or self.label_zh))
        object.__setattr__(self, "icon_key", str(self.icon_key or ""))
        object.__setattr__(self, "priority", int(self.priority))
        object.__setattr__(self, "min_zoom", float(self.min_zoom))
        object.__setattr__(self, "pool_key", str(self.pool_key or ""))
        object.__setattr__(self, "grid_multiplier", float(self.grid_multiplier))
        object.__setattr__(self, "draw_name", bool(self.draw_name))
        object.__setattr__(self, "scopes", tuple(str(item) for item in self.scopes))
        if not isinstance(self.dedupe, DedupeSpec):
            object.__setattr__(self, "dedupe", DedupeSpec.from_dict(self.dedupe))
        object.__setattr__(self, "deprecated", bool(self.deprecated))

    @classmethod
    def from_dict(cls, kind: str, value: Mapping[str, Any] | None) -> "PoiKindSpec":
        value = value or {}
        return cls(
            kind=kind,
            label_zh=value.get("label_zh", kind),
            title_zh=value.get("title_zh", value.get("label_zh", kind)),
            icon_key=value.get("icon_key", kind),
            priority=value.get("priority", 5),
            min_zoom=value.get("min_zoom", 0.8),
            pool_key=value.get("pool_key", ""),
            grid_multiplier=value.get("grid_multiplier", 1.0),
            draw_name=value.get("draw_name", True),
            scopes=tuple(value.get("scopes", ("normal", "svg"))),
            dedupe=DedupeSpec.from_dict(value.get("dedupe")),
            deprecated=value.get("deprecated", False),
        )

    def to_dict(self) -> dict:
        return {
            "label_zh": self.label_zh,
            "title_zh": self.title_zh,
            "icon_key": self.icon_key,
            "priority": self.priority,
            "min_zoom": self.min_zoom,
            "pool_key": self.pool_key,
            "grid_multiplier": self.grid_multiplier,
            "draw_name": self.draw_name,
            "scopes": list(self.scopes),
            "dedupe": self.dedupe.to_dict(),
            "deprecated": self.deprecated,
        }


@dataclass(frozen=True)
class PoiCatalogDefinition:
    """提供标准 POI 映射定义的渲染视图。"""

    schema_version: int
    kinds: Mapping[str, PoiKindSpec]
    path: str = ""

    def __post_init__(self) -> None:
        normalized = {
            str(key): value if isinstance(value, PoiKindSpec)
            else PoiKindSpec.from_dict(str(key), value)
            for key, value in (self.kinds or {}).items()
        }
        object.__setattr__(self, "schema_version", int(self.schema_version))
        object.__setattr__(self, "kinds", normalized)
        object.__setattr__(self, "path", str(self.path or ""))

    @property
    def kind_keys(self) -> tuple[str, ...]:
        return tuple(self.kinds)

    @property
    def active_kind_keys(self) -> tuple[str, ...]:
        return tuple(key for key, value in self.kinds.items() if not value.deprecated)

    @property
    def deprecated_kind_keys(self) -> tuple[str, ...]:
        return tuple(key for key, value in self.kinds.items() if value.deprecated)

    @property
    def priorities(self) -> dict[str, int]:
        return {
            key: value.priority for key, value in self.kinds.items()
            if not value.deprecated and key != "other"
        }

    @property
    def titles_zh(self) -> dict[str, str]:
        return {key: value.title_zh for key, value in self.kinds.items()}

    def get(self, kind: str) -> PoiKindSpec | None:
        return self.kinds.get(str(kind))

    def require(self, kind: str) -> PoiKindSpec:
        value = self.get(kind)
        if value is None:
            raise KeyError(f"unknown POI catalog kind: {kind!r}")
        return value

    def validate(self, *, icon_keys: Sequence[str] | None = None) -> tuple[str, ...]:
        errors: list[str] = []
        if self.schema_version != 1:
            errors.append(f"unsupported schema_version: {self.schema_version}")
        if not self.kinds:
            errors.append("catalog has no kinds")
        allowed_scopes = {"normal", "transit", "svg"}
        icon_set = set(icon_keys or ())
        for kind, spec in self.kinds.items():
            if spec.priority < 0 or spec.priority > 100:
                errors.append(f"{kind}: priority outside 0..100")
            if spec.min_zoom < 0:
                errors.append(f"{kind}: min_zoom must be non-negative")
            if spec.grid_multiplier <= 0:
                errors.append(f"{kind}: grid_multiplier must be positive")
            unknown_scopes = set(spec.scopes) - allowed_scopes
            if unknown_scopes:
                errors.append(f"{kind}: unknown scopes {sorted(unknown_scopes)}")
            if spec.dedupe.distance_m < 0:
                errors.append(f"{kind}: dedupe distance must be non-negative")
            if icon_set and spec.icon_key and spec.icon_key not in icon_set:
                errors.append(f"{kind}: missing icon_key {spec.icon_key!r}")
        return tuple(errors)


def catalog_path() -> Path:
    from .mapping_registry import _mapping_path

    return _mapping_path()


@lru_cache(maxsize=8)
def _load_catalog_cached(path_text: str, override_text: str) -> PoiCatalogDefinition:
    from .mapping_registry import load_mapping_registry

    registry = load_mapping_registry(path_text, override_text or None)
    from .ownership import TRANSIT_POI_KINDS
    transit = TRANSIT_POI_KINDS
    no_names = {"highway_exit", "highway_entrance", "traffic_light"}
    large = {"airport", "park", "amusement"}
    kinds = {}
    for kind, item in registry.poi_kinds.items():
        scopes = ["normal", "svg"]
        if kind in transit:
            scopes.insert(1, "transit")
        distance = 150.0 if kind in large else (32.0 if kind == "bus" else 48.0)
        kinds[kind] = PoiKindSpec(
            kind=kind,
            label_zh=item.label_zh,
            title_zh=item.label_zh,
            icon_key=item.icon_key,
            priority=item.priority,
            min_zoom=item.min_zoom,
            pool_key="",
            draw_name=kind not in no_names,
            scopes=tuple(scopes),
            dedupe=DedupeSpec(distance_m=distance),
        )
    return PoiCatalogDefinition(
        schema_version=1,
        kinds=kinds,
        path=path_text,
    )


def load_catalog(path: str | Path | None = None,
                 override_path: str | Path | None = None) -> PoiCatalogDefinition:
    """从标准 POI 映射文件加载渲染定义。"""
    resolved = Path(path) if path is not None else catalog_path()
    override = (str(Path(override_path).resolve())
                if override_path is not None else "")
    return _load_catalog_cached(str(resolved.resolve()), override)


def clear_catalog_cache() -> None:
    _load_catalog_cached.cache_clear()


def validate_catalog(path: str | Path | None = None, *,
                     override_path: str | Path | None = None,
                     icon_keys: Sequence[str] | None = None) -> tuple[str, ...]:
    return load_catalog(path, override_path).validate(icon_keys=icon_keys)
