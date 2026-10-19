"""定义预览与导出后端共享的栅格表面数据。

SurfaceModel 属于文档会话，不保存主题颜色、透明度、输出尺寸或图形后端对象；各渲染器自行采样并应用展示参数。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


SURFACE_SCHEMA_VERSION = "surface-v1"


@dataclass(frozen=True)
class WorldTransform:
    """描述行优先网格与游戏世界坐标之间的映射。"""

    origin_x: float
    origin_z: float
    cell_meters: float
    rows: int
    cols: int
    axis: str = "rows=north;columns=east"

    def world_at(self, row: int, col: int) -> tuple[float, float]:
        return (
            self.origin_x + (float(col) + 0.5) * self.cell_meters,
            self.origin_z + (float(row) + 0.5) * self.cell_meters,
        )

    def grid_at(self, x: float, z: float) -> tuple[float, float]:
        return (
            (float(z) - self.origin_z) / self.cell_meters - 0.5,
            (float(x) - self.origin_x) / self.cell_meters - 0.5,
        )


@dataclass(frozen=True)
class SurfaceField:
    """定义使用模型世界坐标变换的类型化表面字段。"""

    data: np.ndarray
    semantic: str
    sampling: str
    units: str = ""

    def __post_init__(self) -> None:
        array = np.ascontiguousarray(self.data)
        if array.ndim != 2:
            raise ValueError(f"surface field must be 2-D, got {array.shape!r}")
        if self.sampling not in {"linear", "nearest"}:
            raise ValueError(f"invalid surface sampling mode: {self.sampling!r}")
        array.setflags(write=False)
        object.__setattr__(self, "data", array)

    @property
    def byte_size(self) -> int:
        return int(self.data.nbytes)


@dataclass(frozen=True)
class SurfaceModel:
    """保存供两个渲染后端共用的不可变表面字段。"""

    world_transform: WorldTransform
    cache_key: str
    water_field: SurfaceField | None = None
    building_occupancy: SurfaceField | None = None
    built_area_distance: SurfaceField | None = None
    district_labels: SurfaceField | None = None
    district_boundary_field: SurfaceField | None = None
    zone_labels: SurfaceField | None = None
    land_use_labels: SurfaceField | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    lod_levels: tuple["SurfaceModel", ...] = ()
    schema_version: str = SURFACE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        shape = (self.world_transform.rows, self.world_transform.cols)
        for name in (
            "water_field", "building_occupancy", "built_area_distance",
            "district_labels", "district_boundary_field", "zone_labels",
            "land_use_labels",
        ):
            value = getattr(self, name)
            if value is not None and value.data.shape != shape:
                raise ValueError(
                    f"{name} shape {value.data.shape!r} does not match {shape!r}"
                )

    @property
    def byte_size(self) -> int:
        own = sum(
            value.byte_size
            for value in (
                self.water_field, self.building_occupancy,
                self.built_area_distance, self.district_labels,
                self.district_boundary_field, self.zone_labels,
                self.land_use_labels,
            )
            if value is not None
        )
        return own + sum(level.byte_size for level in self.lod_levels)
