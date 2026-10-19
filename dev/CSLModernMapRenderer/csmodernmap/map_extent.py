"""计算游戏地块范围及其与地形采样无关的尺寸。

CS1 的地块边长依据 Paradox 的 Maps & Themes 开发日志；CS2 的发布地图为 14336 米和 23 个地块。这些尺寸描述图像覆盖范围，不代表土地所有权或购买边界。"""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class TileGrid:
    game: str
    tile_m: float
    grid: int
    presets: tuple[int, ...]

    def limit(self, bounds) -> int:
        x0, z0, x1, z1 = bounds
        return max(0, min(self.grid, math.floor(
            min(x1 - x0, z1 - z0) / self.tile_m + 1e-8)))


TILE_GRIDS = {
    "cs1": TileGrid("cs1", 1920.0, 9, (1, 3, 5, 9)),
    "cs2": TileGrid("cs2", 14336.0 / 23.0, 23, (3, 9, 15, 21, 23)),
}


def square_region(bounds, side, center=(.5, .5)):
    """在指定范围内保留固定尺寸的正方形，并按世界坐标 X/Z 比例定位中心。"""
    x0, z0, x1, z1 = bounds
    half = min(side, x1 - x0, z1 - z0) / 2
    cx = min(max(x0 + center[0] * (x1 - x0), x0 + half), x1 - half)
    cz = min(max(z0 + center[1] * (z1 - z0), z0 + half), z1 - half)
    return cx - half, cz - half, cx + half, cz + half


def document_game(document) -> str:
    origin = getattr(getattr(document, "metadata", None), "origin", None)
    return str(getattr(origin, "game", "")).strip().lower()


def document_tile_grid(document) -> TileGrid | None:
    default = TILE_GRIDS.get(document_game(document))
    if default is None:
        return None
    extensions = getattr(document, "extensions", {})
    raw = extensions.get("map_tile_grid") if isinstance(extensions, dict) else None
    if not isinstance(raw, dict):
        return default
    try:
        cols, rows, size = raw["columns"], raw["rows"], float(raw["tile_size_m"])
        if (type(cols) is not int or type(rows) is not int
                or not 1 <= cols <= 256 or not 1 <= rows <= 256
                or not math.isfinite(size) or size <= 0):
            return default
    except (KeyError, TypeError, ValueError):
        return default
    grid = min(cols, rows)
    presets = tuple(sorted({n for n in default.presets if n <= grid} | {grid}))
    return TileGrid(default.game, size, grid, presets)


def document_city_bounds(document):
    """城市地块范围不随 CS2 外围地形扩大。"""
    from .export_region import document_bounds
    bounds = document_bounds(document)
    grid = document_tile_grid(document)
    if grid is not None and grid.game == "cs2":
        x0, z0, x1, z1 = bounds
        cx, cz = (x0 + x1) / 2, (z0 + z1) / 2
        half = grid.grid * grid.tile_m / 2
        return max(x0, cx-half), max(z0, cz-half), min(x1, cx+half), min(z1, cz+half)
    return bounds
