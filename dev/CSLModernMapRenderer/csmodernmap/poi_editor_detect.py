"""POI 映射未映射与一致性检测（纯逻辑，无 GUI 依赖）。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Issue:
    rule: str
    kind: str
    message: str
    level: str = "warn"


def _kind(spec) -> str:
    return spec.get("icon_key") if isinstance(spec, dict) else getattr(spec, "icon_key", None)


def detect_issues(registry, icons: dict, inventory: list) -> list:
    """registry 需具备 .poi_kinds/.mappings/.item_classes/.sub_services/.services。
    icons: icon_key -> {…}；inventory: [{"name": ai_type}, …]"""
    issues = []
    kinds = registry.poi_kinds or {}
    mappings = list(registry.mappings or [])
    item = registry.item_classes or {}
    subs = registry.sub_services or {}
    svc = registry.services or {}

    def attr(o, n):
        return o.get(n) if isinstance(o, dict) else getattr(o, n, None)

    # 1) 缺图标
    for kind, spec in kinds.items():
        key = _kind(spec)
        if key not in icons:
            issues.append(Issue("missing_icon", kind, f"icon_key={key!r} 未存在于图标清单", "warn"))
        display = attr(spec, "display")
        if display in {"hidden", "inherit_parent"}:
            issues.append(Issue("hidden_category", kind,
                                f"显示策略为 {display}，渲染时主动隐藏或继承",
                                "info"))

    # 2) 未映射：inventory 有、mappings 无
    mapped_ais = {attr(m, "ai_type") for m in mappings if attr(m, "ai_type")}
    for inv in inventory or []:
        name = attr(inv, "name")
        if name and name not in mapped_ais:
            issues.append(Issue("unmapped", "", f"AI 类型 {name} 未映射", "error"))

    # 3) 悬空映射：mapping 指向未定义 kind
    for m in mappings:
        pk = attr(m, "poi_kind")
        if pk and pk not in kinds:
            issues.append(Issue("dangling_mapping", pk or "", f"映射引用未定义类别 {pk}", "error"))

    # 4) 孤儿类别：三种引用表都不出现
    refs = set()
    for m in mappings:
        if attr(m, "poi_kind"):
            refs.add(attr(m, "poi_kind"))
    refs |= set(s or () for s in item.values())
    refs |= set(subs.values())
    refs |= set(svc.values())
    refs.discard(None)
    for kind in kinds:
        if kind not in refs and kind != "other":
            issues.append(Issue("orphan_kind", kind, "未被任何映射/分类表引用", "warn"))

    # 5) 图标被多个类别共用
    key_to = {}
    for kind, spec in kinds.items():
        key_to.setdefault(_kind(spec), []).append(kind)
    for key, ks in key_to.items():
        if len(ks) > 1:
            issues.append(Issue("mount_duplicate_icon", key,
                                f"图标 {key} 被多个类别使用: {', '.join(ks)}", "warn"))

    return issues
