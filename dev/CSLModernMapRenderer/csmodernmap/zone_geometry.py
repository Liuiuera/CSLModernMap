"""计算设施导出分区多边形的精确并集。"""
from __future__ import annotations

from collections import defaultdict

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainterPath


import contourpy
import numpy as np
from PIL import Image, ImageFilter


def grid_rings(arr, val: int, cell: float, ox: float, oz: float, upscale: int, smooth_blur: float):
    """按掩膜值提取世界坐标轮廓，保留外环、孔洞和半格偏移。"""
    m = (arr == val).astype(np.uint8) * 255
    if not m.any():
        return []
    img = Image.fromarray(m, mode='L')
    if smooth_blur is not None and smooth_blur > 0.05:
        img = img.filter(ImageFilter.GaussianBlur(smooth_blur))
    if upscale > 1:
        img = img.resize((img.width * upscale, img.height * upscale), Image.BILINEAR)
    z = np.asarray(img, dtype=np.float32) / 255.0
    res = contourpy.contour_generator(z=z).filled(0.5, 1.5)
    lines = res[0] if isinstance(res[0], (list, tuple)) else (res[0],)
    pointss = res[1] if isinstance(res[1], (list, tuple)) else (res[1],)
    rings = []
    for ln, offs in zip(lines, pointss):
        offs = np.asarray(offs)
        for i in range(len(offs) - 1):
            ring = ln[offs[i]:offs[i + 1]]
            if len(ring) >= 3:
                rings.append(ring)
    step = cell / upscale
    return [(ox, oz) + (ring + 0.5) * step for ring in rings]


def functional_zone_geometries(zones):
    """返回代表区域、含孔洞的多边形及全部源标识；仅处理边界区域，保留分离区域和孔洞。"""
    from .services.poi.facility import _point_in_ring

    groups = defaultdict(list)
    for zone in zones:
        if zone.grid_points or not zone.boundary:
            continue
        key = ("owner", zone.owner_building_id) if zone.owner_building_id else ("zone", zone.id)
        groups[key].append(zone)
    for _key, members in sorted(groups.items()):
        members.sort(key=lambda item: int(item.id))
        path = QPainterPath()
        for zone in members:
            points = zone.boundary.points
            if len(points) < 3:
                continue
            part = QPainterPath()
            part.setFillRule(Qt.FillRule.WindingFill)
            part.moveTo(points[0].x, points[0].z)
            for point in points[1:]:
                part.lineTo(point.x, point.z)
            part.closeSubpath()
            path = path.united(part)
        rings = [[(point.x(), point.y()) for point in polygon]
                 for polygon in path.toSubpathPolygons() if len(polygon) >= 4]
        def area(ring):
            return abs(sum(a[0]*b[1] - b[0]*a[1]
                           for a, b in zip(ring, ring[1:])))
        rings.sort(key=area, reverse=True)
        parents, depths, polygons, outer_indices = [], [], [], {}
        for index, ring in enumerate(rings):
            candidates = [i for i in range(index)
                          if _point_in_ring(*ring[0], rings[i])]
            parent = max(candidates) if candidates else None
            depth = depths[parent] + 1 if parent is not None else 0
            parents.append(parent)
            depths.append(depth)
            if depth % 2 == 0:
                outer_indices[index] = len(polygons)
                polygons.append([ring])
            else:
                polygons[outer_indices[parent]].append(ring)
        if polygons:
            yield members[0], polygons, [int(item.id) for item in members]
