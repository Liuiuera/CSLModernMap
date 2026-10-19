"""空间裁剪工具：瓦片渲染时按区域剔除画布外元素（AABB 包围盒）。

背景（6.5.0 优化项）：分块渲染时每个瓦片都会调用各矢量图层 render()。
若图层遍历全量元素（超大存档 20 万+ 路段 / 10 万+ 建筑），
复杂度 = O(元素数 × 瓦片数)，逐瓦片重复处理导致
「卡在 water 下一层（road）/ road 下一层（railway）」。

本模块提供：
1. 元素 AABB（轴对齐包围盒）计算（惰性缓存到对象，解析后只算一次）
2. 与渲染区域（含外扩）的相交判断（4 次比较）

注意：AABB 是保守判定（只可能多算、不可能漏算），
配合 renderer 的 TILE_BLEED 重叠区可无缝拼合，无接缝断裂风险。
"""

from __future__ import annotations

# 区域外扩（米）：瓦片 world 范围已含 bleed，此处外扩覆盖
# 路口填充延伸（30m）、跨区域线段端点外溢等，留足安全余量。
PAD_METERS = 100.0


def _entity_points(entity):
    """取元素形状点（兼容 legacy Segment 与 CMM NetworkSegment）。"""
    pts = getattr(entity, "points", None)
    if pts is None:
        geometry = getattr(entity, "geometry", None)
        pts = getattr(geometry, "points", ()) if geometry is not None else ()
    return pts


def seg_bbox(seg):
    """路段 AABB (x0, z0, x1, z1)。结果缓存到 seg._bbox（只算一次）。"""
    b = getattr(seg, "_bbox", None)
    if b is None:
        pts = _entity_points(seg)
        if not pts:
            b = (0.0, 0.0, 0.0, 0.0)
        else:
            xs = [p.x for p in pts]
            zs = [p.z for p in pts]
            b = (min(xs), min(zs), max(xs), max(zs))
        try:
            seg._bbox = b
        except AttributeError:  # frozen dataclass：放弃缓存仍可正确返回
            pass
    return b


def building_bbox(b):
    """建筑 AABB（轮廓点外包矩形）。结果缓存到 b._bbox。

    兼容 legacy Building（.points）与 CMM Building（.footprint.points）。
    """
    bb = getattr(b, "_bbox", None)
    if bb is None:
        pts = getattr(b, "points", None)
        if pts is None:
            footprint = getattr(b, "footprint", None)
            pts = footprint.points if footprint is not None else ()
        if not pts:
            bb = (0.0, 0.0, 0.0, 0.0)
        else:
            xs = [p.x for p in pts]
            zs = [p.z for p in pts]
            bb = (min(xs), min(zs), max(xs), max(zs))
        try:
            b._bbox = bb
        except AttributeError:
            pass
    return bb


def traffic_bbox(tseg):
    """交通快照路段 AABB（中心线 world_points 外包矩形）。缓存到 tseg._bbox。"""
    b = getattr(tseg, "_bbox", None)
    if b is None:
        pts = tseg.world_points()
        xs = [p.x for p in pts]
        zs = [p.z for p in pts]
        b = (min(xs), min(zs), max(xs), max(zs))
        tseg._bbox = b
    return b


def po_bbox(po):
    """PO 对象 AABB：center±extent（O(1)，无需缓存）。"""
    ex = max(po.extent_x, 0.1)
    ez = max(po.extent_z, 0.1)
    return (po.center_x - ex, po.center_z - ez,
            po.center_x + ex, po.center_z + ez)


def intersects(bbox, wx0, wz0, wx1, wz1, pad: float = PAD_METERS) -> bool:
    """AABB 与区域 [wx0..wx1]×[wz0..wz1]（外扩 pad 米）是否相交。"""
    bx0, bz0, bx1, bz1 = bbox
    if bx1 < wx0 - pad or bx0 > wx1 + pad:
        return False
    if bz1 < wz0 - pad or bz0 > wz1 + pad:
        return False
    return True
