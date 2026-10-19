"""在 CPU 上采样并合成共享表面字段。"""
from __future__ import annotations

import numpy as np
from PIL import Image

from ..cmm.surface_model import SurfaceField, WorldTransform


def smooth_coverage(distance: np.ndarray, buffer_meters: float,
                    edge_meters: float) -> np.ndarray:
    """计算与着色器一致的距离阈值平滑覆盖率。"""
    distance = np.asarray(distance, dtype=np.float32)
    edge = max(float(edge_meters), 0.0)
    threshold = float(buffer_meters)
    if edge <= 1e-6:
        return (distance <= threshold).astype(np.float32)
    t = np.clip((threshold + edge - distance) / (2.0 * edge), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _inverse_field_affine(transform, world: WorldTransform) -> tuple[float, ...]:
    """返回输出像素到表面字段索引的仿射变换系数。"""
    cell = world.cell_meters
    p00 = transform(world.origin_x + 0.5 * cell,
                    world.origin_z + 0.5 * cell)
    p10 = transform(world.origin_x + 1.5 * cell,
                    world.origin_z + 0.5 * cell)
    p01 = transform(world.origin_x + 0.5 * cell,
                    world.origin_z + 1.5 * cell)
    forward = np.asarray((
        (p10[0] - p00[0], p01[0] - p00[0]),
        (p10[1] - p00[1], p01[1] - p00[1]),
    ), dtype=np.float64)
    determinant = float(np.linalg.det(forward))
    if abs(determinant) < 1e-12:
        raise ValueError("surface transform is singular")
    inverse = np.linalg.inv(forward)
    offset = -inverse @ np.asarray(p00, dtype=np.float64)
    return (
        float(inverse[0, 0]), float(inverse[0, 1]), float(offset[0]),
        float(inverse[1, 0]), float(inverse[1, 1]), float(offset[1]),
    )


def composite_distance_fill(
    image: Image.Image,
    field: SurfaceField,
    world: WorldTransform,
    transform,
    color: tuple[int, int, int],
    *,
    buffer_meters: float,
    edge_meters: float,
    opacity: float = 1.0,
) -> None:
    """当有符号距离字段达到阈值时合成指定颜色。"""
    source = Image.fromarray(np.asarray(field.data, dtype=np.float32), mode="F")
    affine = _inverse_field_affine(transform, world)
    resampling = (Image.Resampling.BILINEAR
                  if field.sampling == "linear" else Image.Resampling.NEAREST)
    width, height = image.size
    # 每次只为一个条带分配采样、覆盖率和透明度中间数据，避免高分辨率输出额外占用整图浮点画布。
    strip_height = min(1024, height)
    for top in range(0, height, strip_height):
        bottom = min(top + strip_height, height)
        local_affine = (
            affine[0], affine[1], affine[1] * top + affine[2],
            affine[3], affine[4], affine[4] * top + affine[5],
        )
        sampled_image = source.transform(
            (width, bottom - top), Image.Transform.AFFINE, local_affine,
            resample=resampling, fillcolor=32767.0)
        sampled = np.asarray(sampled_image, dtype=np.float32)
        coverage = smooth_coverage(sampled, buffer_meters, edge_meters)
        alpha = np.clip(
            coverage * float(opacity) * 255.0, 0, 255).astype(np.uint8)
        if not alpha.any():
            continue
        mask = Image.fromarray(alpha, mode="L")
        size = (width, bottom - top)
        if image.mode == "RGBA":
            fill = Image.new("RGBA", size, (*color, 255))
        else:
            fill = Image.new(image.mode, size, color)
        image.paste(fill, (0, top), mask)
