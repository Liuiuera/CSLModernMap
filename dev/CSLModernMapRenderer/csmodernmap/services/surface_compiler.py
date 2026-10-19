"""将独立于主题的 CMM 数据编译为共享表面模型。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Iterable

import numpy as np
from PIL import Image, ImageDraw

from ..built_area import (
    DEFAULT_EXCLUDE_SERVICES,
    WORLD_MIN,
    WORLD_SIZE,
    _build_mask,
)
from ..cmm.model import MapDocument
from ..cmm.surface_model import (
    SURFACE_SCHEMA_VERSION,
    SurfaceField,
    SurfaceModel,
    WorldTransform,
)
from ..ground_model import _chamfer_distance
from .surface_cache import SurfaceCache


BUILDING_RULES_VERSION = "building-rules-v2-parks-unbuffered"
DISTRICT_RULES_VERSION = "district-rules-v1"


@dataclass(frozen=True)
class SurfaceCompileOptions:
    cell_meters: float = 8.0
    distance_clamp_meters: float = 32767.0
    include_water: bool = True
    include_buildings: bool = True
    include_parks: bool = False
    include_districts: bool = True
    building_rules_version: str = BUILDING_RULES_VERSION
    district_rules_version: str = DISTRICT_RULES_VERSION
    exclude_services: tuple[str, ...] = DEFAULT_EXCLUDE_SERVICES
    built_area_buffer_meters: float = 0.0

    def normalized(self) -> "SurfaceCompileOptions":
        cell = min(max(float(self.cell_meters), 4.0), 64.0)
        clamp = min(max(float(self.distance_clamp_meters), cell), 32767.0)
        return SurfaceCompileOptions(
            cell_meters=cell,
            distance_clamp_meters=clamp,
            include_water=bool(self.include_water),
            include_buildings=bool(self.include_buildings),
            include_parks=bool(self.include_parks),
            include_districts=bool(self.include_districts),
            building_rules_version=str(self.building_rules_version),
            district_rules_version=str(self.district_rules_version),
            exclude_services=tuple(str(v) for v in self.exclude_services),
            built_area_buffer_meters=max(0.0, float(self.built_area_buffer_meters)),
        )


def _update_array(digest, value: np.ndarray | None) -> None:
    if value is None:
        digest.update(b"none")
        return
    array = np.ascontiguousarray(value)
    digest.update(str(array.shape).encode("ascii"))
    digest.update(array.dtype.str.encode("ascii"))
    digest.update(array.view(np.uint8))


def document_surface_fingerprint(document: MapDocument) -> str:
    """计算影响表面编译结果的数据指纹。"""
    digest = hashlib.sha256()
    digest.update(document.schema_version.encode("utf-8"))
    digest.update(str(document.metadata.origin.source_name).encode("utf-8"))
    digest.update(str(document.metadata.exported_at).encode("utf-8"))
    _update_array(digest, document.terrain.water_depth.values)
    for building in document.buildings:
        digest.update(str(building.id).encode("ascii"))
        digest.update(str(building.service).encode("utf-8"))
        for point in building.footprint.points:
            digest.update(np.asarray((point.x, point.z), dtype="<f8").tobytes())
    for district in document.districts:
        if district.is_background:
            continue
        digest.update(str(district.id).encode("ascii"))
        digest.update(str(district.kind).encode("utf-8"))
        digest.update(str(district.zone_kind).encode("utf-8"))
        digest.update(np.asarray((district.grid_cell,), dtype="<f8").tobytes())
        for point in district.grid_points:
            digest.update(np.asarray((point.x, point.z), dtype="<f8").tobytes())
        if district.boundary is not None:
            for point in district.boundary.points:
                digest.update(np.asarray((point.x, point.z), dtype="<f8").tobytes())
    return digest.hexdigest()


def surface_cache_key(document: MapDocument, options: SurfaceCompileOptions, *, fingerprint: str | None = None) -> str:
    options = options.normalized()
    shape_params = {
        "schema": SURFACE_SCHEMA_VERSION,
        "document": fingerprint if fingerprint is not None else document_surface_fingerprint(document),
        "cell": options.cell_meters,
        "distance_clamp": options.distance_clamp_meters,
        "water": options.include_water,
        "buildings": options.include_buildings,
        "parks": options.include_parks,
        "districts": options.include_districts,
        "building_rules": options.building_rules_version,
        "district_rules": options.district_rules_version,
        "exclude_services": options.exclude_services,
        "built_area_buffer_meters": options.built_area_buffer_meters,
    }
    payload = json.dumps(shape_params, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _resample_scalar(document: MapDocument, transform: WorldTransform) -> np.ndarray | None:
    grid = document.terrain.water_depth
    source = grid.values
    if source is None or source.size == 0 or grid.cell <= 0:
        return None
    source = np.asarray(source, dtype=np.float32)
    # 在目标网格中心进行双线性采样，数组轴对应 CMM 的正 Z 和正 X 方向。
    x = transform.origin_x + (np.arange(transform.cols) + 0.5) * transform.cell_meters
    z = transform.origin_z + (np.arange(transform.rows) + 0.5) * transform.cell_meters
    sx = (x - grid.origin.x) / grid.cell
    sz = (z - grid.origin.z) / grid.cell
    x0 = np.clip(np.floor(sx).astype(np.int32), 0, source.shape[1] - 1)
    z0 = np.clip(np.floor(sz).astype(np.int32), 0, source.shape[0] - 1)
    x1 = np.minimum(x0 + 1, source.shape[1] - 1)
    z1 = np.minimum(z0 + 1, source.shape[0] - 1)
    fx = np.clip(sx - x0, 0.0, 1.0).astype(np.float32)
    fz = np.clip(sz - z0, 0.0, 1.0).astype(np.float32)
    top = source[z0[:, None], x0[None, :]] * (1.0 - fx)[None, :]
    top += source[z0[:, None], x1[None, :]] * fx[None, :]
    bottom = source[z1[:, None], x0[None, :]] * (1.0 - fx)[None, :]
    bottom += source[z1[:, None], x1[None, :]] * fx[None, :]
    return (top * (1.0 - fz)[:, None] + bottom * fz[:, None]).astype(np.float16)


def _building_fields(document: MapDocument, transform: WorldTransform,
                     options: SurfaceCompileOptions) -> tuple[np.ndarray, np.ndarray]:
    got = _build_mask(
        document, transform.cell_meters, options.exclude_services,
        WORLD_MIN, WORLD_MIN, WORLD_MIN + WORLD_SIZE, WORLD_MIN + WORLD_SIZE,
    )
    occupancy = np.zeros((transform.rows, transform.cols), dtype=np.uint8)
    if got is not None:
        mask, _ox, _oz = got
        source = np.asarray(mask, dtype=np.uint8)
        rows = min(source.shape[0], transform.rows)
        cols = min(source.shape[1], transform.cols)
        occupancy[:rows, :cols] = (source[:rows, :cols] > 127).astype(np.uint8)
    if occupancy.any():
        inside = occupancy.astype(bool)
        outside_distance = _chamfer_distance(
            inside, transform.cell_meters, options.distance_clamp_meters)
        inside_distance = _chamfer_distance(
            ~inside, transform.cell_meters, options.distance_clamp_meters)
        signed = np.where(inside, -inside_distance, outside_distance)
    else:
        signed = np.full(occupancy.shape, options.distance_clamp_meters, dtype=np.float32)
    if options.include_parks:
        signed = signed - options.built_area_buffer_meters
        park_mask = Image.new("L", (transform.cols, transform.rows), 0)
        park_draw = ImageDraw.Draw(park_mask)
        for district in document.park_zones:
            if district.is_background:
                continue
            if district.boundary is not None:
                points = [((point.x - transform.origin_x) / transform.cell_meters,
                           (point.z - transform.origin_z) / transform.cell_meters)
                          for point in district.boundary.points]
                if len(points) >= 3:
                    park_draw.polygon(points, fill=1)
                continue
            radius = max(float(district.grid_cell or transform.cell_meters) * 0.5, 0.5)
            for point in district.grid_points:
                x0 = (point.x - radius - transform.origin_x) / transform.cell_meters
                z0 = (point.z - radius - transform.origin_z) / transform.cell_meters
                x1 = (point.x + radius - transform.origin_x) / transform.cell_meters
                z1 = (point.z + radius - transform.origin_z) / transform.cell_meters
                park_draw.rectangle((x0, z0, x1, z1), fill=1)
        park_inside = np.asarray(park_mask, dtype=np.uint8) > 0
        if park_inside.any():
            park_outside = _chamfer_distance(
                park_inside, transform.cell_meters,
                options.distance_clamp_meters)
            park_in = _chamfer_distance(
                ~park_inside, transform.cell_meters,
                options.distance_clamp_meters)
            park_signed = np.where(park_inside, -park_in, park_outside)
            signed = np.minimum(signed, park_signed)
    return occupancy, np.rint(signed).clip(-32767, 32767).astype(np.int16)


def _draw_polygon(draw: ImageDraw.ImageDraw, points: Iterable, transform: WorldTransform,
                  fill: int) -> None:
    pixels = [
        ((point.x - transform.origin_x) / transform.cell_meters,
         (point.z - transform.origin_z) / transform.cell_meters)
        for point in points
    ]
    if len(pixels) >= 3:
        draw.polygon(pixels, fill=fill)


def _district_field(document: MapDocument, transform: WorldTransform
                    ) -> tuple[np.ndarray, dict[int, int]]:
    image = Image.new("I", (transform.cols, transform.rows), 0)
    draw = ImageDraw.Draw(image)
    label_to_id: dict[int, int] = {}
    for label, district in enumerate(document.districts, start=1):
        if district.is_background:
            continue
        if label > 65535:
            break
        label_to_id[label] = district.id
        if district.boundary is not None:
            _draw_polygon(draw, district.boundary.points, transform, label)
            continue
        radius = max(float(district.grid_cell or transform.cell_meters) * 0.5, 0.5)
        for point in district.grid_points:
            x0 = (point.x - radius - transform.origin_x) / transform.cell_meters
            z0 = (point.z - radius - transform.origin_z) / transform.cell_meters
            x1 = (point.x + radius - transform.origin_x) / transform.cell_meters
            z1 = (point.z + radius - transform.origin_z) / transform.cell_meters
            draw.rectangle((x0, z0, x1, z1), fill=label)
    return np.asarray(image, dtype=np.uint16), label_to_id


class SurfaceCompiler:
    """同步编译表面模型，调用方可安排到会话工作线程。"""

    def __init__(self, cache: SurfaceCache | None = None) -> None:
        self.cache = cache

    def compile(self, document: MapDocument,
                options: SurfaceCompileOptions | None = None, *, fingerprint: str | None = None) -> SurfaceModel:
        options = (options or SurfaceCompileOptions()).normalized()
        fingerprint = fingerprint if fingerprint is not None else document_surface_fingerprint(document)
        key = surface_cache_key(document, options, fingerprint=fingerprint)
        cached = self.cache.get(key) if self.cache is not None else None
        if cached is not None:
            return cached
        side = int(round(WORLD_SIZE / options.cell_meters))
        transform = WorldTransform(
            WORLD_MIN, WORLD_MIN, options.cell_meters, side, side)
        water = _resample_scalar(document, transform) if options.include_water else None
        occupancy = distance = None
        if options.include_buildings:
            occupancy, distance = _building_fields(document, transform, options)
        districts = label_to_id = None
        if options.include_districts:
            districts, label_to_id = _district_field(document, transform)
        model = SurfaceModel(
            world_transform=transform,
            cache_key=key,
            water_field=(None if water is None else SurfaceField(
                water, "water_depth", "linear", "source-depth")),
            building_occupancy=(None if occupancy is None else SurfaceField(
                occupancy, "building_occupancy", "nearest", "boolean")),
            built_area_distance=(None if distance is None else SurfaceField(
                distance, "built_area_signed_distance", "linear", "meters")),
            district_labels=(None if districts is None else SurfaceField(
                districts, "district_labels", "nearest", "label-id")),
            metadata={
                "document_fingerprint": fingerprint,
                "building_rules_version": options.building_rules_version,
                "district_rules_version": options.district_rules_version,
                "district_label_to_id": label_to_id or {},
            },
        )
        return self.cache.put(model) if self.cache is not None else model
