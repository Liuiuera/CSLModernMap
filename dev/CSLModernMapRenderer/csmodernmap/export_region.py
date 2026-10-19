"""统一地理导出使用的世界范围和栅格尺寸。"""
from __future__ import annotations

import math

DEFAULT_WORLD = (-8640.0, -8640.0, 8640.0, 8640.0)
OUTPUT_ROTATIONS = (0, 90, 180, 270)


def output_rotation(value):
    rotation = int(value or 0) % 360
    if rotation not in OUTPUT_ROTATIONS:
        raise ValueError("rotation must be 0/90/180/270")
    return rotation


def normalize_bounds(bounds, world=None):
    values = tuple(float(v) for v in getattr(bounds, "bounds", bounds))
    if len(values) != 4 or not all(math.isfinite(v) for v in values):
        raise ValueError("Selection must contain four finite coordinates")
    a, b, c, d = values
    x0, z0, x1, z1 = min(a, c), min(b, d), max(a, c), max(b, d)
    if world is not None:
        wx0, wz0, wx1, wz1 = normalize_bounds(world)
        x0, z0, x1, z1 = max(x0, wx0), max(z0, wz0), min(x1, wx1), min(z1, wz1)
    if x1 - x0 <= 1e-6 or z1 - z0 <= 1e-6:
        raise ValueError("Selection has no area inside this map")
    return x0, z0, x1, z1


def output_dimensions(bounds, long_edge, rotation=0):
    x0, z0, x1, z1 = normalize_bounds(bounds or DEFAULT_WORLD)
    width, height = z1-z0, x1-x0
    if output_rotation(rotation) in (90, 270):
        width, height = height, width
    edge = int(long_edge)
    if edge < 1:
        raise ValueError("Output long edge must be positive")
    scale = edge / max(width, height)
    return max(1, round(width*scale)), max(1, round(height*scale))


def size_pair(size):
    if isinstance(size, (tuple, list)):
        width, height = (int(v) for v in size)
    else:
        width = height = int(size)
    if min(width, height) < 1:
        raise ValueError("Output dimensions must be positive")
    return width, height


def document_bounds(document):
    terrain = getattr(document, "terrain", None)
    if terrain is not None and terrain.rows > 0 and terrain.cols > 0 and terrain.cell > 0:
        return (float(terrain.origin.x), float(terrain.origin.z),
                float(terrain.origin.x + terrain.cols * terrain.cell),
                float(terrain.origin.z + terrain.rows * terrain.cell))
    return DEFAULT_WORLD
