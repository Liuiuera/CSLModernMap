"""POI 映射注册表：区分导出的 ItemClass/Service 与 BuildingAI 类型。"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Mapping, Sequence

import warnings
import sys

import yaml


_OVERRIDE_FIELDS = {"label_zh", "icon_key", "priority", "min_zoom", "display"}


@dataclass(frozen=True)
class MappingEntry:
    """单条映射记录。"""
    poi_kind: str
    display: str = "visible"
    parent: str = "standalone"
    ai_type: str = ""


@dataclass(frozen=True)
class AreaOverride:
    """仅在确有区域归属时应用限定范围的名称规则。"""

    area_kind: str
    name_exact: str
    poi_kind: str
    display: str = "visible"
    parent: str = "standalone"


@dataclass(frozen=True)
class PoiKindDef:
    """POI 语义类型定义。"""
    label_zh: str
    icon_key: str
    priority: int
    min_zoom: float
    display: str = "visible"


@dataclass(frozen=True)
class PoiMappingRegistry:
    """完整的 POI 映射注册表，从 poi_mapping.yaml 加载。"""
    poi_kinds: Mapping[str, PoiKindDef] = field(default_factory=dict)
    mappings: tuple[MappingEntry, ...] = ()
    item_classes: Mapping[str, str | None] = field(default_factory=dict)
    sub_services: Mapping[str, str] = field(default_factory=dict)
    services: Mapping[str, str] = field(default_factory=dict)
    area_overrides: tuple[AreaOverride, ...] = ()

    # ---- 查找索引 ----
    _by_ai_type: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        by_ai = {}
        for entry in self.mappings:
            if entry.ai_type:
                by_ai[entry.ai_type] = entry
        object.__setattr__(self, "_by_ai_type", by_ai)
        object.__setattr__(self, "_folded_item_classes", {k.casefold(): v for k, v in self.item_classes.items()})
        object.__setattr__(self, "_folded_sub_services", {k.casefold(): v for k, v in self.sub_services.items()})
        object.__setattr__(self, "_folded_services", {k.casefold(): v for k, v in self.services.items()})

    def lookup(self, *, ai_type: str) -> MappingEntry | None:
        return self._by_ai_type.get(ai_type)

    def lookup_building(self, item_class: str, service: str,
                        sub_service: str) -> MappingEntry | None:
        # 精确的设施分类优先于服务枚举；显式 null 表示装饰/轨道等不显示。
        if item_class.casefold() in self._folded_item_classes:
            kind = self._folded_item_classes[item_class.casefold()]
            return MappingEntry(kind or "other", display="visible" if kind else "hidden")
        entry = self.lookup(ai_type=item_class)
        if entry is not None:
            return entry
        kind = self._folded_sub_services.get(sub_service.casefold()) or self._folded_services.get(service.casefold())
        return MappingEntry(kind) if kind else None

    def lookup_area_override(self, area_kind: str, name: str) -> AreaOverride | None:
        wanted_area = str(area_kind or "").strip().casefold()
        wanted_name = " ".join(str(name or "").strip().casefold().split())
        for override in self.area_overrides:
            if (override.area_kind.casefold() == wanted_area
                    and override.name_exact.casefold() == wanted_name):
                return override
        return None

    def get_kind(self, poi_kind: str) -> PoiKindDef | None:
        return self.poi_kinds.get(poi_kind)

    def require_kind(self, poi_kind: str) -> PoiKindDef:
        value = self.get_kind(poi_kind)
        if value is None:
            raise KeyError(f"unknown POI kind: {poi_kind!r}")
        return value

    def display_status(self, poi_kind: str, *, icon_key: str | None = None,
                       available_icon_keys: Sequence[str] | None = None) -> str:
        """区分隐藏、缺失图标和孤立类别的诊断状态。"""
        kind = str(poi_kind or "")
        if any(entry.poi_kind == kind and entry.display.casefold() == "hidden"
               for entry in self.mappings):
            return "hidden"
        if kind not in self.poi_kinds:
            return "orphan"
        if available_icon_keys is not None:
            wanted = str(icon_key or self.poi_kinds[kind].icon_key)
            if wanted not in {str(item) for item in available_icon_keys}:
                return "missing"
        return "visible"


def _mapping_path() -> Path:
    return Path(__file__).resolve().parent / "poi_mapping.yaml"


def poi_mapping_path() -> Path:
    """用户目录中的完整 POI 映射，首次迁移前读取内置资源。"""
    return _mapping_path()


def _load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _merged_mapping(base: dict, override_path: Path | None) -> dict:
    """将仅含 poi_kinds 的用户覆盖宽容地合入内置数据。"""
    if override_path is None or not override_path.is_file():
        return base
    try:
        override = _load_yaml(override_path)
        if not isinstance(override, dict) or int(override.get("schema_version", 1)) != 1:
            raise ValueError("unsupported POI override schema")
        overrides = override.get("poi_kinds", {})
        if not isinstance(overrides, dict):
            raise ValueError("poi_kinds must be a mapping")
        merged = dict(base)
        kinds = {key: dict(value or {}) for key, value in
                 (base.get("poi_kinds", {}) or {}).items()}
        for kind, fields in overrides.items():
            # 用户覆盖不能新增语义类别，也不能替换新版本附带的分类表。
            if kind not in kinds or not isinstance(fields, dict):
                continue
            for field, value in fields.items():
                if field in _OVERRIDE_FIELDS:
                    kinds[kind][field] = value
        merged["poi_kinds"] = kinds
        return merged
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        warnings.warn(f"Ignoring invalid POI mapping override {override_path}: {exc}")
        return base


def _parse_kinds(raw: dict) -> dict[str, PoiKindDef]:
    result = {}
    for kind, spec in (raw or {}).items():
        result[kind] = PoiKindDef(
            label_zh=str(spec.get("label_zh", kind)),
            icon_key=str(spec.get("icon_key", kind)),
            priority=int(spec.get("priority", 5)),
            min_zoom=float(spec.get("min_zoom", 0.8)),
            display=str(spec.get("display", "visible")),
        )
    return result


def _parse_mappings(raw: list) -> tuple[MappingEntry, ...]:
    entries = []
    for item in (raw or ()):
        entries.append(MappingEntry(
            poi_kind=str(item.get("poi_kind", "other")),
            display=str(item.get("display", "visible")),
            parent=str(item.get("parent", "standalone")),
            ai_type=str(item.get("ai_type", "")),
        ))
    return tuple(entries)


def _parse_area_overrides(raw: list) -> tuple[AreaOverride, ...]:
    entries = []
    for item in (raw or ()):
        if not isinstance(item, Mapping):
            continue
        area_kind = str(item.get("area_kind", "")).strip()
        name_exact = str(item.get("name_exact", "")).strip()
        poi_kind = str(item.get("poi_kind", "other")).strip() or "other"
        if area_kind and name_exact:
            entries.append(AreaOverride(
                area_kind=area_kind,
                name_exact=name_exact,
                poi_kind=poi_kind,
                display=str(item.get("display", "visible")),
                parent=str(item.get("parent", "standalone")),
            ))
    return tuple(entries)


@lru_cache(maxsize=8)
def _load_registry_cached(path_text: str, override_text: str) -> PoiMappingRegistry:
    path = Path(path_text)
    raw = _merged_mapping(_load_yaml(path), Path(override_text) if override_text else None)
    return PoiMappingRegistry(
        poi_kinds=_parse_kinds(raw.get("poi_kinds", {})),
        mappings=_parse_mappings(raw.get("mappings", [])),
        item_classes=raw.get("item_classes", {}),
        sub_services=raw.get("sub_services", {}),
        services=raw.get("services", {}),
        area_overrides=_parse_area_overrides(raw.get("area_overrides", [])),
    )


def load_mapping_registry(path: str | Path | None = None,
                          override_path: str | Path | None = None) -> PoiMappingRegistry:
    """加载 POI 映射注册表。"""
    resolved = Path(path) if path is not None else _mapping_path()
    override = (str(Path(override_path).resolve())
                if override_path is not None else "")
    return _load_registry_cached(str(resolved.resolve()), override)


def clear_cache() -> None:
    _load_registry_cached.cache_clear()
