"""地形处理：保留浮点精度的高斯平滑与多尺度山体阴影。"""
from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np


def elevation_rgb(values, thresholds: np.ndarray, colors: np.ndarray,
                  continuous: bool) -> np.ndarray:
    """海拔色带共用取色：连续插值或阈值分段，端点钳制。"""
    if not continuous or len(colors) < 2:
        indices = np.clip(np.searchsorted(thresholds, values, side="left") - 1,
                          0, len(colors) - 1)
        return colors[indices]
    hi = np.searchsorted(thresholds, values, side="right")
    lo = np.clip(hi - 1, 0, len(colors) - 1)
    hi = np.clip(hi, 0, len(colors) - 1)
    span = thresholds[hi] - thresholds[lo]
    fraction = np.divide(values - thresholds[lo], span,
                         out=np.zeros_like(values, dtype=np.float32), where=span > 0)
    blended = (colors[lo].astype(np.float32)
               + (colors[hi].astype(np.float32) - colors[lo].astype(np.float32))
               * np.asarray(fraction)[..., None])
    return np.clip(blended, 0, 255).astype(np.uint8)


def hillshade(
    heightmap: List[List[float]],
    azimuth: float = 315.0,
    altitude: float = 45.0,
    intensity: float = 1.0,
    cell_size: float | Tuple[float, float] = 1.0,
) -> Optional[List[List[float]]]:
    """计算山体阴影亮度图（0~1），用于叠加在地形底色上。

    算法：计算每个网格点的梯度 → 法向量 → 与光源方向点积。
    ``cell_size`` 是网格在世界坐标中的间距（米）。旧调用不传时仍按
    1 个单位的像素间距计算；真实地形应传入 ``Terrain.cell``，否则
    16 米网格会被误当成 1 米网格，坡度会夸大约 16 倍。

    ``cell_size`` 也可传 ``(cell_y, cell_x)``，用于非方形网格。
    """

    arr = np.asarray(heightmap, dtype=np.float64)
    if arr.size == 0 or arr.ndim != 2:
        return None
    if arr.shape[0] < 2 or arr.shape[1] < 2:
        return np.ones(arr.shape, dtype=np.float64).tolist()

    if isinstance(cell_size, (tuple, list)):
        if len(cell_size) != 2:
            return None
        cell_y, cell_x = (float(cell_size[0]), float(cell_size[1]))
    else:
        cell_y = cell_x = float(cell_size)
    cell_y = max(abs(cell_y), 1e-6)
    cell_x = max(abs(cell_x), 1e-6)

    # 高度单位与 cell_size 同为米，得到无量纲的 dz/dx、dz/dy。
    gy, gx = np.gradient(arr, cell_y, cell_x)

    # 光源方向（方位角 az、高度角 alt → 单位向量指向光源）
    az_rad = np.deg2rad(azimuth)
    alt_rad = np.deg2rad(altitude)
    lx = np.cos(alt_rad) * np.sin(az_rad)
    ly = np.cos(alt_rad) * np.cos(az_rad)
    lz = np.sin(alt_rad)

    # 法向量：(-gx, -gy, 1) 归一化
    norm = np.sqrt(gx ** 2 + gy ** 2 + 1.0)
    nx = -gx / norm
    ny = -gy / norm
    nz = 1.0 / norm

    shade = nx * lx + ny * ly + nz * lz
    shade = np.clip(shade, 0.0, 1.0)

    # intensity 控制对比度：0=无晕渲，1=标准
    intensity = float(np.clip(intensity, 0.0, 1.0))
    shade = 1.0 - intensity * (1.0 - shade)
    return shade.tolist()


def _gaussian_heightmap(heightmap, sigma: float):
    """对高度图做高斯低通，返回 numpy 数组。

    使用 NumPy 分离卷积保留米制浮点高度，边缘按最近值填充。
    sigma 的单位是网格单元。
    """

    arr = np.asarray(heightmap, dtype=np.float32)
    if sigma <= 0.5:
        return arr.copy()
    radius = max(1, int(round(2.5 * sigma)))
    x = np.arange(-radius, radius + 1, dtype=np.float64)
    kernel = np.exp(-(x * x) / (2.0 * sigma * sigma))
    kernel /= kernel.sum()
    padded = np.pad(arr, ((0, 0), (radius, radius)), mode="edge")
    blurred = np.apply_along_axis(
        lambda row: np.convolve(row, kernel, mode="valid"),
        1, padded,
    )
    padded = np.pad(blurred, ((radius, radius), (0, 0)), mode="edge")
    return np.apply_along_axis(
        lambda col: np.convolve(col, kernel, mode="valid"),
        0, padded,
    ).astype(np.float32, copy=False)


def multiscale_hillshade(
    heightmap: List[List[float]],
    *,
    cell_size: float = 1.0,
    azimuth: float = 315.0,
    altitude: float = 45.0,
    intensity: float = 1.0,
    large_scale_meters: float = 320.0,
    small_scale_meters: float = 0.0,
    large_scale_weight: float = 0.35,
    small_scale_weight: float = 0.65,
) -> Optional[List[List[float]]]:
    """组合大尺度山脉阴影与小尺度沟谷阴影。

    两个尺度都在原始高度网格上计算，之后才由图层插值到输出画布，因而
    整图渲染与区域/分块渲染使用完全相同的坡面法线和边缘条件。
    ``large_scale_meters`` / ``small_scale_meters`` 是高斯半径（米）。
    """

    arr = np.asarray(heightmap, dtype=np.float32)
    if arr.size == 0 or arr.ndim != 2:
        return None

    cell = max(abs(float(cell_size)), 1e-6)
    large_sigma = max(float(large_scale_meters), 0.0) / cell
    small_sigma = max(float(small_scale_meters), 0.0) / cell
    large = hillshade(
        _gaussian_heightmap(arr, large_sigma),
        azimuth=azimuth, altitude=altitude, intensity=1.0,
        cell_size=cell,
    )
    small = hillshade(
        _gaussian_heightmap(arr, small_sigma),
        azimuth=azimuth, altitude=altitude, intensity=1.0,
        cell_size=cell,
    )
    if large is None or small is None:
        return None

    w_large = max(float(large_scale_weight), 0.0)
    w_small = max(float(small_scale_weight), 0.0)
    total = w_large + w_small
    if total <= 0.0:
        w_large, w_small, total = 1.0, 0.0, 1.0
    shade = (
        w_large * np.asarray(large, dtype=np.float32)
        + w_small * np.asarray(small, dtype=np.float32)
    ) / total
    final_intensity = float(np.clip(intensity, 0.0, 1.0))
    shade = 1.0 - final_intensity * (1.0 - np.clip(shade, 0.0, 1.0))
    return shade.tolist()
