"""定义 POI 服务参数和可序列化的数据容器。

POI 实体由 CMM 定义，本模块只保存服务输入、目录和报告容器。"""
from __future__ import annotations

from .settings import normalized_facility_hierarchy

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ...cmm import FacilityGroup, POI, TransitKind, normalize_transit_kind


@dataclass(frozen=True)
class POIJobParams:
    """保存纯 POI 派生参数；min_priority 由渲染器筛选，不在生成世界坐标目录时删减候选对象。"""

    scope: str = "normal"
    min_priority: int = 15
    station_name_distance_m: float = 90.0
    dedupe_overrides: Mapping[str, Any] = field(default_factory=dict)
    priority_overrides: Mapping[str, Any] = field(default_factory=dict)
    icon_overrides: Mapping[str, str] = field(default_factory=dict)
    min_zoom_overrides: Mapping[str, float] = field(default_factory=dict)
    categories: Sequence[str] = ()
    detail_gate: bool = True
    custom_pois: Sequence[Mapping[str, Any]] | None = None
    building_merge_enabled: bool = True
    transit_merge_enabled: bool = True
    block_gap_m: float = 24.0
    hub_core_m: float = 80.0
    transit_types: Sequence[str] | None = None
    # None 表示保留全部线路，空元组表示明确关闭全部线路。
    transit_line_ids: Sequence[int] | None = None
    facility_hierarchy_enabled: bool = True
    children_min_zoom: float = 1.0
    # 不可变的作业局部预览或 PNG 处理快照，与共享样式分离并参与目录缓存键。
    active_profile: Mapping[str, Any] | None = None
    allow_different_station_names: bool = False

    def __post_init__(self) -> None:
        scope = str(self.scope or "normal").strip().casefold()
        if scope == "transport":
            scope = "transit"
        if scope not in {"normal", "transit", "svg", "metro"}:
            raise ValueError(f"unsupported POI scope: {self.scope!r}")
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "min_priority", int(self.min_priority))
        object.__setattr__(self, "station_name_distance_m", float(self.station_name_distance_m))
        object.__setattr__(self, "dedupe_overrides", dict(self.dedupe_overrides or {}))
        object.__setattr__(self, "priority_overrides", dict(self.priority_overrides or {}))
        object.__setattr__(self, "icon_overrides", {
            str(key): str(value) for key, value in (self.icon_overrides or {}).items()
        })
        object.__setattr__(self, "min_zoom_overrides", {
            str(key): float(value) for key, value in (self.min_zoom_overrides or {}).items()
        })
        object.__setattr__(self, "categories", tuple(str(item) for item in (self.categories or ())))
        object.__setattr__(self, "building_merge_enabled", bool(self.building_merge_enabled))
        object.__setattr__(self, "transit_merge_enabled", bool(self.transit_merge_enabled))
        object.__setattr__(self, "block_gap_m", float(self.block_gap_m))
        object.__setattr__(self, "hub_core_m", float(self.hub_core_m))
        object.__setattr__(self, "facility_hierarchy_enabled",
                           bool(self.facility_hierarchy_enabled))
        object.__setattr__(self, "children_min_zoom",
                           max(float(self.children_min_zoom), 0.0))
        object.__setattr__(self, "active_profile",
                           deepcopy(dict(self.active_profile or {})))
        object.__setattr__(self, "allow_different_station_names",
                           bool(self.allow_different_station_names))
        if self.transit_types is not None:
            normalized_types = []
            for item in self.transit_types:
                kind = normalize_transit_kind(item)
                # 交通选择使用 CMM 枚举，旧调用方传字符串；内部统一保存稳定字符串并保留未知旧值。
                value = (kind.value if kind is not TransitKind.OTHER
                         else str(getattr(item, "value", item)))
                if value not in normalized_types:
                    normalized_types.append(value)
            object.__setattr__(self, "transit_types", tuple(normalized_types))
        if self.transit_line_ids is not None:
            object.__setattr__(self, "transit_line_ids", tuple(
                dict.fromkeys(int(item) for item in self.transit_line_ids)))
        if self.custom_pois is not None:
            object.__setattr__(self, "custom_pois", tuple(dict(item) for item in self.custom_pois))

    @classmethod
    def from_style(cls, style: Mapping[str, Any] | None, *, scope: str = "normal") -> "POIJobParams":
        """从既有 style.poi_icons 结构构造参数。"""
        raw_style = style or {}
        cfg = dict(raw_style.get("poi_icons", raw_style) or {})
        transport_cfg = raw_style.get("transports", {}) or {}
        if not isinstance(transport_cfg, Mapping):
            transport_cfg = {}
        enabled_types = transport_cfg.get("enabled")
        if not isinstance(enabled_types, (list, tuple)):
            enabled_types = cfg.get("transit_types")
        enabled_line_ids = transport_cfg.get("enabled_line_ids")
        if enabled_line_ids is None:
            enabled_line_ids = transport_cfg.get("line_ids", cfg.get("transit_line_ids"))
        hierarchy = normalized_facility_hierarchy(cfg)
        # same_facility_merge 是旧界面名称；两种结构并存时以新版对象为准。
        legacy_enabled = cfg.get(
            "same_facility_merge",
            cfg.get("facility_hierarchy_enabled", True),
        )
        active_profile = cfg.get("active_profile")
        if not isinstance(active_profile, Mapping):
            active_profile = raw_style.get("active_profile")
        if not isinstance(active_profile, Mapping):
            active_profile = None
        profile_groups = (active_profile.get("groups", {})
                          if active_profile is not None else {})
        if not isinstance(profile_groups, Mapping):
            profile_groups = {}
        transit_group = profile_groups.get("transit", {})
        ordinary_group = profile_groups.get("ordinary", {})
        if not isinstance(transit_group, Mapping):
            transit_group = {}
        if not isinstance(ordinary_group, Mapping):
            ordinary_group = {}

        def profile_or_legacy(profile_key: str, hierarchy_key: str,
                              config_key: str, default: Any) -> Any:
            # 作业局部输出配置是权威渲染协议；旧版及主题层级值仅供未提供配置的调用方兼容。
            if active_profile is not None:
                return active_profile.get(profile_key, default)
            return hierarchy.get(hierarchy_key, cfg.get(config_key, default))

        return cls(
            scope=scope,
            min_priority=cfg.get("min_priority", 15),
            dedupe_overrides=cfg.get("merge_overrides", {}),
            priority_overrides=cfg.get("priority_overrides", {}),
            icon_overrides=cfg.get("icon_overrides", {}),
            min_zoom_overrides=cfg.get("min_zoom_overrides", {}),
            categories=cfg.get("categories", ()),
            building_merge_enabled=(
                bool(active_profile.get("merge_enabled", True))
                and bool(ordinary_group.get("merge_enabled", True))
                if active_profile is not None else cfg.get("building_merge_enabled", True)),
            transit_merge_enabled=(
                bool(active_profile.get("merge_enabled", True))
                and bool(transit_group.get("merge_enabled", True))
                if active_profile is not None else cfg.get("transit_merge_enabled", True)),
            block_gap_m=(ordinary_group.get("distance_m", 24.0)
                         if active_profile is not None else cfg.get("block_gap_m", 24.0)),
            hub_core_m=(transit_group.get("distance_m", 80.0)
                        if active_profile is not None else cfg.get("hub_core_m", 80.0)),
            facility_hierarchy_enabled=(
                True if active_profile is not None
                else hierarchy.get("enabled", legacy_enabled)),
            children_min_zoom=profile_or_legacy(
                "children_min_zoom", "children_min_zoom",
                "children_min_zoom", 1.0),
            transit_types=enabled_types,
            transit_line_ids=enabled_line_ids,
            active_profile=active_profile,
            allow_different_station_names=(
                active_profile.get("allow_different_station_names", False)
                if active_profile is not None else False),
        )

    @property
    def fingerprint(self) -> str:
        payload = {
            "scope": self.scope,
            "min_priority": self.min_priority,
            "station_name_distance_m": self.station_name_distance_m,
            "dedupe_overrides": self.dedupe_overrides,
            "priority_overrides": self.priority_overrides,
            "icon_overrides": self.icon_overrides,
            "min_zoom_overrides": self.min_zoom_overrides,
            "categories": self.categories,
            "detail_gate": self.detail_gate,
            "custom_pois": self.custom_pois,
            "building_merge_enabled": self.building_merge_enabled,
            "transit_merge_enabled": self.transit_merge_enabled,
            "block_gap_m": self.block_gap_m,
            "hub_core_m": self.hub_core_m,
            "facility_hierarchy_enabled": self.facility_hierarchy_enabled,
            "children_min_zoom": self.children_min_zoom,
            "active_profile": self.active_profile,
            "allow_different_station_names": self.allow_different_station_names,
            "transit_types": self.transit_types,
            "transit_line_ids": self.transit_line_ids,
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PoiCatalog:
    """保存使用期间不可变的世界坐标 POI 结果。"""

    pois: tuple[POI, ...] = ()
    kinds: tuple[str, ...] = ()
    # 新版新增派生设施分组目录及层级字段，同时仍可读取带普通建筑默认值的旧版 JSON。
    schema_version: int = 2
    scope: str = "normal"
    params_fingerprint: str = ""
    issues: tuple[str, ...] = ()
    facility_groups: tuple[FacilityGroup, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "pois", tuple(sorted(self.pois, key=lambda item: str(item.id))))
        object.__setattr__(self, "kinds", tuple(dict.fromkeys(str(item) for item in self.kinds)))
        object.__setattr__(self, "schema_version", int(self.schema_version))
        object.__setattr__(self, "scope", str(self.scope or "normal"))
        object.__setattr__(self, "issues", tuple(str(item) for item in self.issues))
        object.__setattr__(self, "facility_groups", tuple(sorted(
            (item if isinstance(item, FacilityGroup)
             else FacilityGroup.from_dict(item)
             for item in (self.facility_groups or ())),
            key=lambda item: str(item.id))))

    def by_kind(self, kind: str) -> tuple[POI, ...]:
        wanted = str(kind)
        return tuple(item for item in self.pois if item.kind == wanted or item.category == wanted)

    def pois_of(self, source_kind: str, source_id: int) -> tuple[POI, ...]:
        return tuple(
            item for item in self.pois
            if item.source.kind == str(source_kind) and item.source.id == int(source_id)
        )

    def visible(self, *, min_priority: int | None = None, categories: Sequence[str] = ()) -> tuple[POI, ...]:
        threshold = self._priority_threshold(min_priority)
        allowed = {str(item) for item in categories}
        return tuple(
            item for item in self.pois
            if item.priority >= threshold and (not allowed or item.kind in allowed)
        )

    @staticmethod
    def _priority_threshold(value: int | None) -> int:
        return -2**31 if value is None else int(value)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "scope": self.scope,
            "params_fingerprint": self.params_fingerprint,
            "kinds": list(self.kinds),
            "pois": [item.to_dict() for item in self.pois],
            "issues": list(self.issues),
            "facility_groups": [item.to_dict() for item in self.facility_groups],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "PoiCatalog":
        value = value or {}
        return cls(
            pois=tuple(POI.from_dict(item) for item in value.get("pois", ())),
            kinds=tuple(value.get("kinds", ())),
            schema_version=value.get("schema_version", 1),
            scope=value.get("scope", "normal"),
            params_fingerprint=value.get("params_fingerprint", ""),
            issues=tuple(value.get("issues", ())),
            facility_groups=tuple(FacilityGroup.from_dict(item)
                                  for item in value.get("facility_groups", ())),
        )
