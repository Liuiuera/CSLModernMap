"""个人主题的名称、预设差异与旧版快照迁移。"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unicodedata
import uuid

from .i18n import tr
from .style import Style, strip_retired_region_palette_fields


class ThemeReadError(ValueError):
    """当前主题不可用；不能用其他外观代替它进行预览或导出。"""


def comparison_data(data: dict) -> dict:
    """忽略展示信息和等价写法，保留未知字段。"""
    from .theme_contract import normalize_theme
    from .theme_metadata import field_kind
    from .style import hex_to_rgba
    data = normalize_theme(Style(strip_retired_region_palette_fields(copy.deepcopy(data))).data)
    data.pop("name", None)
    data.pop("description", None)

    def canonical(value, path=()):
        if isinstance(value, dict):
            return {key: canonical(child, (*path, key)) for key, child in value.items()}
        if isinstance(value, list):
            return [canonical(child, (*path, str(index))) for index, child in enumerate(value)]
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str) and value.startswith("#") and field_kind(path) == "color":
            return list(hex_to_rgba(value))
        return value

    return canonical(data)


def content_digest(data: dict) -> str:
    encoded = json.dumps(comparison_data(data), ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def preset_update(previous: dict, personal: dict, current: dict) -> tuple[dict, list, list]:
    """只应用发行方相对旧基准的修改；同项冲突采用新版，列表整体更新。"""
    from .theme_contract import normalize_theme
    before, user, after = (comparison_data(value) for value in (previous, personal, current))
    result = normalize_theme(Style(copy.deepcopy(personal)).data)
    new_values = normalize_theme(Style(copy.deepcopy(current)).data)
    missing = object()
    changes, preserved = [], []

    def walk(old, edited, new, raw_new, path=()):
        if isinstance(old, dict) and isinstance(new, dict):
            keys = old.keys() | new.keys() | (edited.keys() if isinstance(edited, dict) else set())
            for key in sorted(keys):
                walk(old.get(key, missing), edited.get(key, missing) if isinstance(edited, dict) else missing,
                     new.get(key, missing), raw_new.get(key, missing), (*path, key))
            return
        if old == new:
            if old != edited:
                preserved.append(list(path))
            return
        if edited == new:
            return
        parent = result
        for key in path[:-1]:
            if not isinstance(parent.get(key), dict):
                parent[key] = {}
            parent = parent[key]
        if new is missing:
            parent.pop(path[-1], None)
        else:
            parent[path[-1]] = copy.deepcopy(raw_new)
        changes.append({"path": list(path), "conflict": edited != old})

    walk(before, user, after, new_values)
    return result, changes, preserved


def validate_name(value, names=()) -> str:
    if not isinstance(value, str):
        raise ValueError(tr("gui.theme.error.name"))
    name = value.strip()
    if not name or len(name) > 64 or any(unicodedata.category(c).startswith("C") for c in name):
        raise ValueError(tr("gui.theme.error.name"))
    if name.casefold() in {n.casefold() for n in names}:
        raise ValueError(tr("gui.theme.error.duplicate"))
    return name


def unique_name(value: str, names) -> str:
    name = "".join(c for c in value if not unicodedata.category(c).startswith("C")).strip()[:64] or tr("gui.theme.personal")
    used = {n.casefold() for n in names}
    candidate = name
    index = 2
    while candidate.casefold() in used:
        suffix = f" ({index})"
        candidate = name[:64 - len(suffix)] + suffix
        index += 1
    return candidate


def new_id() -> str:
    return "user_" + uuid.uuid4().hex


def difference(base: dict, data: dict, path=()) -> tuple[dict, list]:
    """列表整体覆盖；删除单独记录，避免把 null 与删除混为一谈。"""
    changed, removed = {}, []
    for key in base.keys() - data.keys():
        removed.append([*path, key])
    for key, value in data.items():
        if key in base and isinstance(value, dict) and isinstance(base[key], dict):
            child, deleted = difference(base[key], value, (*path, key))
            if child:
                changed[key] = child
            removed.extend(deleted)
        elif key not in base or value != base[key]:
            changed[key] = copy.deepcopy(value)
    return changed, removed


def resolve(record: dict, base: dict | None) -> dict:
    if base is None:
        data = copy.deepcopy(record["data"])
    else:
        data = Style(base).merged(record["overrides"]).data
        for path in record["removed"]:
            parent = data
            for key in path[:-1]:
                parent = parent.get(key)
                if not isinstance(parent, dict):
                    break
            else:
                if isinstance(parent, dict):
                    parent.pop(path[-1], None)
    data["name"] = record["name"]
    return data


def make_record(name: str, data: dict, base_id: str | None, base: dict | None) -> dict:
    if base is None:
        return {"name": name, "base": None, "data": copy.deepcopy(data)}
    changes, removed = difference(base, data)
    changes.pop("name", None)
    return {"name": name, "base": base_id, "overrides": changes, "removed": removed}


def export_file(path: str, data: dict) -> None:
    import yaml
    from ._paths import resource_dir
    target = Path(path).resolve()
    if target.suffix.lower() not in {".yaml", ".yml"}:
        raise ValueError(tr("gui.theme.error.file"))
    if target.is_relative_to(resource_dir("styles").resolve()):
        raise ValueError(tr("gui.theme.error.builtin"))
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                         prefix=target.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            yaml.safe_dump(data, stream, allow_unicode=True, sort_keys=False)
        os.replace(temporary, target)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
