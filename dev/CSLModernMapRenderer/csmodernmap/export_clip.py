"""裁剪内嵌预览场景而不修改已发布的预览资源。"""
from __future__ import annotations

import base64
import copy
import io
import math
from urllib.parse import urlsplit

import numpy as np
from PIL import Image
from shapely import make_valid
from shapely.geometry import box, mapping, shape

from .export_region import normalize_bounds

METERS = 111195.0


def _name(url):
    return urlsplit(url).path.rsplit("/", 1)[-1]


def _image(uri):
    return Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1]))).convert("RGB")


def _uri(image):
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode("ascii")


def _crop_image(uri, fractions, encoding=None):
    image = _image(uri)
    w, h = image.size
    a, b, c, d = fractions
    extent = (a*w, b*h, c*w, d*h)
    size = (max(1, math.ceil((c-a)*w)), max(1, math.ceil((d-b)*h)))
    if encoding == "distance":
        rgb = np.asarray(image).astype(np.uint16)
        scalar = (rgb[..., 0]*256 + rgb[..., 1]).astype(np.float32)
        sampled = Image.fromarray(scalar).transform(
            size, Image.Transform.EXTENT, extent, Image.Resampling.BILINEAR)
        values = np.clip(np.rint(np.asarray(sampled)), 0, 65535).astype(np.uint16)
        packed = np.zeros((*values.shape, 3), dtype=np.uint8)
        packed[..., 0], packed[..., 1] = values >> 8, values & 255
        result = Image.fromarray(packed)
    else:
        result = image.transform(size, Image.Transform.EXTENT, extent, Image.Resampling.BILINEAR)
    return _uri(result)


def clip_scene_payload(payload, bounds):
    """返回仅包含指定范围内几何与纹理的深拷贝；源坐标保持地理坐标，纹理方向不变。"""
    result = copy.deepcopy(payload)
    manifest = result["manifest"]
    x0, z0, x1, z1 = normalize_bounds(bounds, manifest.get("world", {}).get("bounds"))
    region = [x0, z0, x1, z1]
    geo = [[-z1/METERS, x0/METERS], [-z0/METERS, x1/METERS]]
    clip = box(geo[0][0], geo[0][1], geo[1][0], geo[1][1])
    for source in result["embedded"]["sources"].values():
        if source.get("type") != "FeatureCollection":
            continue
        kept = []
        for feature in source.get("features", []):
            if not feature.get("geometry"):
                continue
            geometry = shape(feature["geometry"])
            if not geometry.is_valid:
                geometry = make_valid(geometry)
            clipped = geometry.intersection(clip)
            if clipped.is_empty:
                continue
            # 切线接触不构成线或多边形的可见部分。
            family = feature["geometry"]["type"]
            if "Polygon" in family:
                parts = [g for g in getattr(clipped, "geoms", [clipped]) if g.geom_type in {"Polygon", "MultiPolygon"}]
            elif "LineString" in family:
                parts = [g for g in getattr(clipped, "geoms", [clipped]) if g.geom_type in {"LineString", "MultiLineString"}]
            else:
                parts = [clipped]
            if not parts:
                continue
            from shapely.ops import unary_union
            feature["geometry"] = mapping(unary_union(parts))
            feature.pop("bbox", None)
            kept.append(feature)
        source["features"] = kept
        source.pop("bbox", None)

    images = result["embedded"]["images"]
    fields = manifest.get("surfaceFields", {})
    for key, descriptor in list(fields.items()):
        bx0, bz0, bx1, bz1 = descriptor["worldBounds"]
        try:
            a, b, c, d = normalize_bounds(region, (bx0, bz0, bx1, bz1))
        except ValueError:
            fields.pop(key)
            if descriptor.get("url"):
                images.pop(_name(descriptor["url"]), None)
            continue
        fractions = ((a-bx0)/(bx1-bx0), (b-bz0)/(bz1-bz0),
                     (c-bx0)/(bx1-bx0), (d-bz0)/(bz1-bz0))
        if descriptor.get("url"):
            name = _name(descriptor["url"])
            images[name] = _crop_image(images[name], fractions, "distance")
        descriptor["worldBounds"] = [a, b, c, d]

    terrain = manifest.get("sources", {}).get("terrain")
    corners = manifest.get("terrainCorners")
    if terrain and corners:
        lng0, lng1 = min(p[0] for p in corners), max(p[0] for p in corners)
        lat0, lat1 = min(p[1] for p in corners), max(p[1] for p in corners)
        left, right = max(lng0, geo[0][0]), min(lng1, geo[1][0])
        bottom, top = max(lat0, geo[0][1]), min(lat1, geo[1][1])
        name = _name(terrain)
        if left >= right or bottom >= top:
            manifest["sources"].pop("terrain")
            manifest["terrainCorners"] = None
            images.pop(name, None)
        else:
            fractions = ((left-lng0)/(lng1-lng0), (lat1-top)/(lat1-lat0),
                         (right-lng0)/(lng1-lng0), (lat1-bottom)/(lat1-lat0))
            images[name] = _crop_image(images[name], fractions)
            manifest["terrainCorners"] = [[left, top], [right, top], [right, bottom], [left, bottom]]
    manifest["world"] = {**manifest.get("world", {}), "bounds": region, "geoBounds": geo}
    manifest["initialRegion"] = region
    manifest["exportRegion"] = region
    result.update(manifest)
    return result
