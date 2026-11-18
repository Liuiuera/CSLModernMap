"""卫星图合成使用的跨水道路和轨道遮罩。"""
from __future__ import annotations

import math

from PIL import Image, ImageDraw

from .cmm.model import NetworkKind, SegmentMode

_RAIL_KINDS = {NetworkKind.RAIL, NetworkKind.METRO, NetworkKind.TRAM, NetworkKind.MONORAIL}


def bridge_coverage(networks, size: int, bounds) -> tuple[Image.Image, dict]:
    # 小图用两倍遮罩栅格化；8192 原图直接在采集分辨率合成，最后统一缩小。
    scale = 2 if size <= 4096 else 1
    mask = Image.new("L", (size * scale, size * scale))
    draw = ImageDraw.Draw(mask)
    xmin, zmin, xmax, zmax = bounds
    factor = size * scale / (xmax - xmin)

    def project(x, z):
        return ((x - xmin) * factor, (zmax - z) * factor)

    counts = {"footprint": 0, "rail_centerline": 0}
    for segment in networks:
        if segment.mode != SegmentMode.ELEVATED or segment.kind == NetworkKind.STOP_LINE:
            continue
        if segment.footprint and len(segment.footprint.points) >= 3:
            draw.polygon([project(p.x, p.z) for p in segment.footprint.points], fill=255)
            counts["footprint"] += 1
        elif segment.kind in _RAIL_KINDS:
            # 轨道导出契约没有道路占地，使用实际轨道中心线及导出宽度。
            if len(segment.geometry.points) < 2 or not math.isfinite(segment.width) or segment.width <= 0:
                raise ValueError(f"高架轨道 {segment.id} 缺少有效几何或宽度")
            points = [project(p.x, p.z) for p in segment.geometry.points]
            draw.line(points, fill=255, width=max(1, round(segment.width * factor)), joint="curve")
            counts["rail_centerline"] += 1
        elif segment.kind in {NetworkKind.ROAD, NetworkKind.BEAUTIFICATION}:
            raise ValueError(f"高架道路 {segment.id} 缺少占地轮廓，不能准确回贴")
    if scale != 1:
        mask = mask.resize((size, size), Image.Resampling.LANCZOS)
    return mask, counts
