"""计算交通线路的自动颜色和最终颜色。"""
from __future__ import annotations

from typing import Iterable, Mapping

from ...cmm import JobParams, LineGroup, Stop, TransitLine
from ...transit import DEFAULT_PALETTE


def _pick_color(used: set[tuple[int, int, int]], palette, usage):
    free = [color for color in palette if color not in used]
    values = free or list(palette)
    # min 的选择稳定，相同使用次数时保持调色板原顺序，与旧版颜色决策一致。
    return min(values, key=lambda color: usage.get(color, 0))


def resolve_auto_colors(lines: Iterable[TransitLine], groups: Iterable[LineGroup],
                        stops: Iterable[Stop], params: JobParams | None = None
                        ) -> dict[int, tuple[int, int, int]]:
    """返回无色线路的推荐颜色，不修改线路或站点。"""
    params = params or JobParams()
    lines = tuple(lines)
    group_values = tuple(groups)
    stops = tuple(stops)
    palette = list(params.palette or DEFAULT_PALETTE)
    if not params.auto_color or not palette:
        return {}
    line_values = {item.id: item for item in lines}
    stop_sets = {
        group.id: {stop.stop_key for stop in stops
                   if any(line_id in group.member_line_ids for line_id in stop.serving_line_ids)}
        for group in group_values
    }
    usage: dict[tuple[int, int, int], int] = {}
    output: dict[int, tuple[int, int, int]] = {}
    group_color: dict[int, tuple[int, int, int]] = {}
    ordered = sorted(
        group_values,
        key=lambda group: (
            -sum(len(stop_sets[group.id] & stop_sets[other.id])
                 for other in group_values if other.id != group.id),
        ),
    )
    for group in ordered:
        user = next((tuple(line_values[line_id].user_color[:3])
                     for line_id in group.member_line_ids
                     if line_id in line_values and line_values[line_id].user_color), None)
        if user is not None:
            group_color[group.id] = user
            for line_id in group.member_line_ids:
                if line_id in line_values and line_values[line_id].user_color is None:
                    output[line_id] = user
            continue
        used = {
            group_color[other.id]
            for other in group_values
            if other.id in group_color and stop_sets[group.id] & stop_sets[other.id]
        }
        color = _pick_color(used, palette, usage)
        group_color[group.id] = color
        usage[color] = usage.get(color, 0) + 1
        for line_id in group.member_line_ids:
            if line_id in line_values and line_values[line_id].user_color is None:
                output[line_id] = color
    return dict(sorted(output.items()))


def effective_color(line: TransitLine, auto_colors: Mapping[int, tuple[int, int, int]] | None = None,
                    *, fallback: tuple[int, int, int] = (128, 128, 128)) -> tuple[int, int, int]:
    if line.user_color:
        return tuple(line.user_color[:3])
    if auto_colors and line.id in auto_colors:
        return tuple(auto_colors[line.id])
    return fallback


assign_auto_colors = resolve_auto_colors


class AutoColorService:
    resolve = staticmethod(resolve_auto_colors)
    effective = staticmethod(effective_color)


def hex_palette(values: list[str]) -> list[tuple[int, int, int]]:
    """YAML 十六进制色板 → RGB 三元组列表（非法条目跳过）。"""
    out: list[tuple[int, int, int]] = []
    for v in values or ():
        try:
            c = str(v).strip().lstrip("#")
            if len(c) == 3:
                c = "".join(ch * 2 for ch in c)
            out.append((int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)))
        except (ValueError, IndexError):
            continue
    return out
