"""POI 映射 YAML 的保注释读写与校验。字段均为单行值，用行区间原地重写保留注释。"""
from __future__ import annotations

import re
from pathlib import Path

import yaml
from .i18n import tr

_KIND_RE = re.compile(r"^  (?P<kind>[A-Za-z_][A-Za-z0-9_]*):\s*(#.*)?$")
_FIELD_RE = re.compile(r"^    (?P<field>label_zh|icon_key|priority|min_zoom|display):\s*(?P<rest>.*)$")


def load_poi_mapping(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_poi_mapping_with_override(path: str | Path,
                                   override_path: str | Path | None) -> dict:
    """读取内置映射，并宽容地叠加 poi_kinds 用户配置。"""
    from .services.poi.mapping_registry import load_mapping_registry

    raw = load_poi_mapping(path)
    registry = load_mapping_registry(path, override_path)
    raw["poi_kinds"] = {
        kind: {
            "label_zh": spec.label_zh,
            "icon_key": spec.icon_key,
            "priority": spec.priority,
            "min_zoom": spec.min_zoom,
            "display": spec.display,
        }
        for kind, spec in registry.poi_kinds.items()
    }
    return raw


def write_poi_overrides(path: str | Path, overrides: dict) -> None:
    """将已修改类别合并到稳定的单地图覆盖文件。"""
    p = Path(path)
    current = {}
    if p.is_file():
        try:
            current = load_poi_mapping(p)
        except (OSError, yaml.YAMLError):
            current = {}
    kinds = current.get("poi_kinds", {}) if isinstance(current, dict) else {}
    if not isinstance(kinds, dict):
        kinds = {}
    allowed = ("label_zh", "icon_key", "priority", "min_zoom", "display")
    for kind, fields in overrides.items():
        if not isinstance(fields, dict):
            continue
        kinds[str(kind)] = {
            key: fields[key] for key in allowed if key in fields
        }
    payload = {"schema_version": 1, "poi_kinds": kinds}
    p.parent.mkdir(parents=True, exist_ok=True)
    temporary = p.with_suffix(p.suffix + ".tmp")
    temporary.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8")
    temporary.replace(p)


def _scan_poi_kinds(lines: list[str]) -> tuple[int, dict[str, tuple[int, int]], int]:
    """返回 (kinds起始行, kind->(start,end), 区块结束行)。end 为 kinds 之后首个 0 缩进顶层键所在行。"""
    start = None
    end = len(lines)
    kinds = {}
    cur = None
    cur_start = 0
    pre_first = None
    for i, line in enumerate(lines):
        if line == "poi_kinds:":
            start = i
            continue
        if start is None:
            continue
        if re.match(r"^[A-Za-z_]\w*:", line) and not line.startswith((" ", "\t")):
            end = i
            if cur:
                kinds[cur] = (cur_start, i - 1)
            break
        m = _KIND_RE.match(line)
        if m:
            if cur:
                kinds[cur] = (cur_start, i - 1)
            cur = m.group("kind")
            cur_start = pre_first if pre_first is not None else i
            pre_first = None
        elif cur is None and pre_first is None and line.startswith((" ", "\t")):
            pre_first = i
    else:
        if cur:
            kinds[cur] = (cur_start, len(lines) - 1)
    return start, kinds, end


def _render_line(field: str, value) -> str:
    if field in ("label_zh", "icon_key", "display"):
        return f'    {field}: "{value}"'
    return f"    {field}: {value}"


def write_poi_kinds(path: str | Path, overrides: dict) -> None:
    """把 overrides{kind:{field:value}} 覆盖到 poi_kinds 段；{kind:None} 删除该类别。"""
    p = Path(path)
    lines = p.read_text(encoding="utf-8").split("\n")
    start, kinds, end = _scan_poi_kinds(lines)
    if start is None:
        raise ValueError(tr("gui.error.poi_mapping_no_section"))

    remove = {k for k, v in overrides.items() if v is None}
    updated = {k: v for k, v in overrides.items() if v is not None}

    new_blocks = []
    all_keys = [k for k in kinds.keys() if k not in remove] + \
               [k for k in updated if k not in kinds]
    for k in all_keys:
        if k in kinds and k in updated:
            s, e = kinds[k]
            block = lines[s:e + 1]
            for field, value in updated[k].items():
                for j, ln in enumerate(block):
                    fm = _FIELD_RE.match(ln)
                    if fm and fm.group("field") == field:
                        block[j] = _render_line(field, value)
            new_blocks.append("\n".join(block))
        elif k in kinds:
            new_blocks.append("\n".join(lines[kinds[k][0]:kinds[k][1] + 1]))
        else:
            spec = updated[k]
            new_blocks.append("\n".join([
                f"  {k}:",
                f'    label_zh: "{spec.get("label_zh", k)}"',
                f'    icon_key: "{spec.get("icon_key", k)}"',
                f"    priority: {spec.get('priority', 5)}",
                f"    min_zoom: {spec.get('min_zoom', 0.80)}",
                f'    display: "{spec.get("display", "visible")}"',
            ]))

    head = "\n".join(lines[0:start])
    out = head + "\npoi_kinds:\n" + "\n".join(new_blocks) + "\n" + "\n".join(lines[end:])

    _validate_written(out, {k: v for k, v in updated.items() if k not in kinds})
    p.write_text(out, encoding="utf-8")


def _validate_written(out: str, added: dict) -> None:
    try:
        data = yaml.safe_load(out)
    except yaml.YAMLError as exc:
        raise ValueError(tr("gui.error.poi_mapping_parse", error=exc)) from exc
    if not isinstance(data, dict) or not isinstance(data.get("poi_kinds"), dict):
        raise ValueError(tr("gui.error.poi_mapping_save_no_section"))
    for kind in added:
        if kind not in data["poi_kinds"]:
            raise ValueError(tr("gui.error.poi_mapping_save_no_kind", kind=kind))
