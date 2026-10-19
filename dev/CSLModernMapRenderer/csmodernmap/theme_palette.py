"""提供原生界面共享的主题图库配色。"""
from functools import lru_cache
from PIL import ImageColor

_COLOR_PATHS = {
    "ground": "canvas.background",
    "green": "forest.color",
    "water": "water.color",
    "water_edge": "water.border",
    "road": "roads.highway.fill",
    "road_edge": "roads.highway.border",
    "road2": "roads.main.fill",
    "road2_edge": "roads.main.border",
    "building": "buildings.residential",
    "building_edge": "buildings.border",
}

# 中性兜底：只保证画得出来，不保证好看（真实主题都能命中上面大部分键）。
_FALLBACK = {
    "ground": "#F2F2F0",
    "green": "#C8DEB4",
    "water": "#9FD5E8",
    "water_edge": "#7FBAD0",
    "road": "#FFFFFF",
    "road_edge": "#C3C8D2",
    "road2": "#F4F5F7",
    "road2_edge": "#C3C8D2",
    "building": "#E6E8EE",
    "building_edge": "#D2D6DE",
}


@lru_cache(maxsize=16)
def theme_palette(stem: str) -> dict:
    """读一套主题的缩略图配色；键见 ``_COLOR_PATHS``。

    主题文件缺失、PyYAML 不可用或某个色值写坏时，逐项退回 ``_FALLBACK``，
    绝不让缩略图拖垮窗口构造。
    """
    colors = dict(_FALLBACK)
    try:
        from csmodernmap._paths import resource_dir
        from csmodernmap.style import load_style

        style = load_style(str(resource_dir("styles") / f"{stem}.yaml"))
    except Exception:  # noqa: BLE001 - 缩略图不许把异常抛到 GUI 构造链上
        return colors
    for name, path in _COLOR_PATHS.items():
        value = style.get(path)
        if not value:
            continue
        try:
            rgb = ImageColor.getrgb(str(value))
            colors[name] = "#{:02x}{:02x}{:02x}".format(*rgb[:3])
        except ValueError:
            pass
    # 主干路色缺失的主题（如 apple_maps 没有 roads.main）用高速色顶上，
    # 否则次干道会掉成兜底灰，与整体配色打架。
    for name, source in (("road2", "road"), ("road2_edge", "road_edge")):
        if colors[name] == _FALLBACK[name] and colors[source] != _FALLBACK[source]:
            colors[name] = colors[source]
    return colors
