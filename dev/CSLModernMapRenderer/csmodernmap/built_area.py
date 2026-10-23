"""城市建成区底色：由建筑轮廓推导建成区范围。

用途：在地形层之上、水域层之下铺设统一底色，让城市无论位于平原还是
高原都有一致底座（对齐高德「建成区与周边地形区分」的观感，避免建筑
直接落在山区绿色地形上显得突兀）。

「房子」判定：只把真正的房屋类建筑纳入建成区。默认排除基础设施类
service（电力/供水/道路设施、景观道具等，如风力发电机、抽水站、
岩石装饰），避免把非建筑设施误判为建成区。

算法（对齐 water_layer 的平滑水体思路）：
1. 世界 [-8640,8640]² 栅格化为 cell_meters 精度的整数掩膜图像
2. 逐建筑多边形用 PIL 填充到掩膜（跳过 exclude_services）
3. 掩膜做形态学膨胀（MaxFilter）实现 buffer_meters 外扩，弥合建筑间隙
4. 可选把公园多边形本体填入掩膜
5. contourpy 提取掩膜等值面轮廓（含外环与内部孔洞环）
6. 按包含关系分组：外环 + 其内部孔洞环（偶数层=填充区，奇数层=孔洞）
   → 渲染端对外环做 Chaikin 平滑 + aggdraw 抗锯齿填充，孔洞填质心
   地形色（与水体图层处理岛屿的方式一致）
"""
from __future__ import annotations

import numpy as np
import contourpy
from PIL import Image, ImageDraw, ImageFilter

# CSL 标准 9 格地图尺寸（单位：游戏内米）
WORLD_MIN = -8640.0
WORLD_SIZE = 17280.0

# 忽略面积小于该值的多边形（平方米，过滤孤立小碎片）
MIN_POLY_AREA = 400.0

# 掩膜 BILINEAR 放大倍数：放大后提取 50% 过渡带等值线，轮廓更平滑
# （对齐 water_layer 的网格放大思路；8m 格 × 2 = 4m 精度）
CONTOUR_UPSCALE = 2

# 默认排除的非房屋类 service（PascalCase，对应 Building.service）：
# 电力设施（风力发电机/太阳能电厂/变电站）、供水设施（抽水站）、
# 道路设施、景观美化（岩石/巨石等装饰道具）都不是"房子"，不构成建成区。
DEFAULT_EXCLUDE_SERVICES = ("Electricity", "Water", "Road", "Beautification")


def _poly_area(poly) -> float:
    """多边形面积（世界坐标 m²）。"""
    points = np.asarray(poly, dtype=np.float64).reshape(-1, 2)
    if not len(points):
        return 0.0
    nxt = np.roll(points, -1, axis=0)
    cross = points[:, 0] * nxt[:, 1] - nxt[:, 0] * points[:, 1]
    # 保持与原标量循环相同的累加顺序；成对求和会改变面积相近的环的排序和身份。
    return abs(np.add.accumulate(cross)[-1]) / 2.0


def _poly_centroid(poly):
    return (
        float(np.mean(poly[:, 0])),
        float(np.mean(poly[:, 1])),
    )


def _point_in_poly(px: float, pz: float, poly) -> bool:
    """射线法判断点 (px,pz) 是否在多边形内。poly = (N,2) 的 (x,z)。

    numpy 向量化：逐边测试一次完成（原纯 Python 逐边循环是 _group_regions
    O(N²) 的耗时大头，大图下可达数秒）。
    """
    x = poly[:, 0]
    z = poly[:, 1]
    xj = np.roll(x, -1)
    zj = np.roll(z, -1)
    dz = zj - z
    dz = np.where(dz == 0.0, 1e-12, dz)  # 原 `(zj - zi) or 1e-12`
    cond1 = (z > pz) != (zj > pz)
    cond2 = px < (xj - x) * (pz - z) / dz + x
    return bool(np.count_nonzero(cond1 & cond2) & 1)


def _dilate_binary(arr: np.ndarray, r: int) -> np.ndarray:
    """二值掩膜方形膨胀 r 格（3×3 最大值迭代 r 次）。

    数学上：3×3 结构元素自卷积（Minkowski 和）r 次 ≡ 一次 (2r+1)×(2r+1)
    最大值滤波，与 PIL MaxFilter(2r+1) 像素级等价（已实测 0 差异）；
    但代价从 O(像素×窗口²) 降为 O(像素×r)，cell=4m 下 4320² 掩膜由
    ~15s 降到 ~1.2s。越界按 0 处理（与 MaxFilter 的零填充一致）。
    """
    out = arr
    for _ in range(r):
        t = out
        top = np.zeros_like(t); top[1:] = t[:-1]
        bot = np.zeros_like(t); bot[:-1] = t[1:]
        left = np.zeros_like(t); left[:, 1:] = t[:, :-1]
        right = np.zeros_like(t); right[:, :-1] = t[:, 1:]
        tl = np.zeros_like(t); tl[1:, 1:] = t[:-1, :-1]
        tr = np.zeros_like(t); tr[1:, :-1] = t[:-1, 1:]
        bl = np.zeros_like(t); bl[:-1, 1:] = t[1:, :-1]
        br = np.zeros_like(t); br[:-1, :-1] = t[1:, 1:]
        out = np.maximum.reduce([t, top, bot, left, right, tl, tr, bl, br])
    return out


def _source_polys(source):
    """归一化输入：返回 (建筑多边形迭代器, 公园多边形列表)。

    支持 :class:`MapDocument`（首选）与 legacy ``MapData``（过渡）。
    建筑多边形为 ``(resolved_service, [(x, z), ...])``；
    公园多边形为 ``[(x, z), ...]``。
    """
    from .cmm.model import MapDocument

    if isinstance(source, MapDocument):
        def building_polys():
            for b in source.buildings:
                service = (b.service or str(getattr(
                    b.role, "value", b.role)).lower())
                yield service, [(p.x, p.z) for p in b.footprint.points]
        park_polys = [
            [(p.x, p.z) for p in district.boundary.points]
            for district in source.park_zones if district.boundary
        ]
        return building_polys(), park_polys

    def building_polys():
        for b in source.buildings:
            yield b.service, [(p.x, p.z) for p in b.points]

    park_polys = [
        [(p.x, p.z) for p in park.points]
        for park in getattr(source, "parks", ()) or ()
    ]
    return building_polys(), park_polys


def _build_area_mask(
    source,
    cell_meters: float,
    buffer_meters: float,
    exclude_services: tuple[str, ...],
    include_parks: bool,
    world_x0: float, world_z0: float, world_x1: float, world_z1: float,
):
    """构建掩膜（可裁剪到可见世界范围）。

    返回 (mask_img, origin_x, origin_z) 或 None：
    - mask_img: L 模式图像，尺寸 (gw, gh)，像素 (col=gx, row=gz)，
      255=建成区；原点 (0,0) 对应世界坐标 (origin_x, origin_z)。
    """
    grid_n = int(round(WORLD_SIZE / cell_meters))
    wx0 = max(world_x0, WORLD_MIN)
    wz0 = max(world_z0, WORLD_MIN)
    wx1 = min(world_x1, WORLD_MIN + WORLD_SIZE)
    wz1 = min(world_z1, WORLD_MIN + WORLD_SIZE)
    if wx1 <= wx0 or wz1 <= wz0:
        return None
    gx0 = max(0, min(grid_n - 1, int(np.floor((wx0 - WORLD_MIN) / cell_meters))))
    gz0 = max(0, min(grid_n - 1, int(np.floor((wz0 - WORLD_MIN) / cell_meters))))
    gx1 = max(0, min(grid_n - 1, int(np.ceil((wx1 - WORLD_MIN) / cell_meters))))
    gz1 = max(0, min(grid_n - 1, int(np.ceil((wz1 - WORLD_MIN) / cell_meters))))
    if gx1 <= gx0 or gz1 <= gz0:
        return None
    gw = gx1 - gx0 + 1
    gh = gz1 - gz0 + 1

    building_polys, park_polys = _source_polys(source)
    mask = Image.new("L", (gw, gh), 0)
    d = ImageDraw.Draw(mask)
    for service, pts_world in building_polys:
        if service in exclude_services or len(pts_world) < 3:
            continue
        pts = [
            ((x - WORLD_MIN) / cell_meters - gx0,
             (z - WORLD_MIN) / cell_meters - gz0)
            for x, z in pts_world
        ]
        if len(pts) >= 3:
            d.polygon(pts, fill=255)

    # 形态学膨胀：外扩 buffer_meters 弥合建筑间隙。
    # 用 numpy 迭代 3×3 膨胀（等价一次 MaxFilter(2r+1)，但快一个数量级）。
    if buffer_meters > 0:
        r = max(1, int(round(buffer_meters / cell_meters)))
        arr = np.asarray(mask, dtype=np.uint8)
        arr = _dilate_binary(arr, r)
        mask = Image.fromarray(arr, "L")

    # 可选：公园本体在建筑膨胀之后独立并入，不受建成区 buffer 影响。
    if include_parks and (park_polys or getattr(source, "park_zones", None)):
        d2 = ImageDraw.Draw(mask)
        for pts_world in park_polys:
            if len(pts_world) < 3:
                continue
            pts = [
                ((x - WORLD_MIN) / cell_meters - gx0,
                 (z - WORLD_MIN) / cell_meters - gz0)
                for x, z in pts_world
            ]
            if len(pts) >= 3:
                d2.polygon(pts, fill=255)
        for district in getattr(source, "park_zones", ()):
            if district.is_background or district.boundary is not None:
                continue
            radius = max(float(district.grid_cell or cell_meters) * 0.5, 0.5)
            for point in district.grid_points:
                x0 = (point.x - radius - WORLD_MIN) / cell_meters - gx0
                z0 = (point.z - radius - WORLD_MIN) / cell_meters - gz0
                x1 = (point.x + radius - WORLD_MIN) / cell_meters - gx0
                z1 = (point.z + radius - WORLD_MIN) / cell_meters - gz0
                d2.rectangle((x0, z0, x1, z1), fill=255)

    return mask, WORLD_MIN + gx0 * cell_meters, WORLD_MIN + gz0 * cell_meters


def _extract_rings(arr: np.ndarray, cell: float, origin_x: float,
                   origin_z: float, upscale: int = 1) -> list[np.ndarray]:
    """从（可放大的）掩膜提取等值线环并转世界坐标。

    arr: 行=gz（南→北），列=gx（西→东），0..255 渐变（BILINEAR 放大后
    边缘为过渡带，非 0=建成区）；(origin_x, origin_z) 为 (0,0) 对应的
    世界坐标。
    返回 [(N,2) (x,z) 世界坐标, ...]，含外环与内部孔洞环。
    """
    # 归一化 0..1，filled 提取 50% 过渡带等值线（几何位置与硬边一致）
    z = arr.astype(np.float32) / 255.0
    res = contourpy.contour_generator(z=z).filled(0.5, 1.5)
    lines = res[0] if isinstance(res[0], (list, tuple)) else (res[0],)
    pointss = res[1] if isinstance(res[1], (list, tuple)) else (res[1],)
    rings: list[np.ndarray] = []
    for ln, offs in zip(lines, pointss):
        offs = np.asarray(offs)
        for i in range(len(offs) - 1):
            ring = ln[offs[i]:offs[i + 1]]
            if len(ring) >= 3:
                rings.append(ring)
    step = cell / upscale  # 放大后每格的世界边长
    out: list[np.ndarray] = []
    for ring in rings:
        # numpy 向量化坐标转换（原逐点 Python 循环，大环点数上万时耗时明显）
        pts = np.empty((len(ring), 2), dtype=np.float64)
        pts[:, 0] = origin_x + (ring[:, 0] + 0.5) * step
        pts[:, 1] = origin_z + (ring[:, 1] + 0.5) * step
        out.append(pts)
    return out


def _group_regions(rings: list[np.ndarray]) -> list[dict]:
    """按包含关系把环分组为区域：外环 + 内部孔洞环。

    返回 [{"outer": idx, "holes": [idx, ...]}, ...]（idx 为 rings 下标）。
    外环/岛环（偶数层）为填充区；孔洞环（奇数层）挂到包含它的、
    面积最小的偶数环下。

    性能：预计算全部环的 AABB 与质心（O(N)），每次点内判定先用 4 次比较
    跳过明显不相交的环，再走 numpy 向量化射线法——原纯 Python 逐边射线
    在 8192 输出（cell=4m、41 环、10 万+ 顶点）时占 ~7s，本实现降到亚秒。
    """
    # 调用方必须先完成唯一排序，并把同一份列表继续向下返回。
    # 这里不能再次生成局部排序副本，否则 regions 中的索引会与调用方
    # 持有的 rings 脱节，外环可能被渲染成孔洞。
    n = len(rings)
    bxs0 = np.array([r[:, 0].min() for r in rings])
    bzs0 = np.array([r[:, 1].min() for r in rings])
    bxs1 = np.array([r[:, 0].max() for r in rings])
    bzs1 = np.array([r[:, 1].max() for r in rings])
    cents = np.array([_poly_centroid(r) for r in rings])
    areas = np.array([_poly_area(r) for r in rings])

    def contains(cx: float, cz: float, j: int) -> bool:
        """质心 (cx,cz) 是否在环 j 内（先 AABB 粗筛，再精确射线法）。"""
        if not (bxs0[j] <= cx <= bxs1[j] and bzs0[j] <= cz <= bzs1[j]):
            return False
        return _point_in_poly(cx, cz, rings[j])

    depth = [0] * n
    for i in range(n):
        cx, cz = cents[i]
        depth[i] = sum(1 for j in range(i) if contains(cx, cz, j))
    regions = [{"outer": i, "holes": []} for i in range(n) if depth[i] % 2 == 0]
    for i in range(n):
        if depth[i] % 2 == 1:
            cx, cz = cents[i]
            best = -1
            for j in range(n):
                if depth[j] % 2 == 0 and contains(cx, cz, j):
                    if best < 0 or areas[j] < areas[best]:
                        best = j
            if best >= 0:
                for reg in regions:
                    if reg["outer"] == best:
                        reg["holes"].append(i)
                        break
    return regions


def compute_built_area_rings(
    source,
    cell_meters: float = 8.0,
    buffer_meters: float = 60.0,
    exclude_services: list[str] | tuple[str, ...] | None = None,
    include_parks: bool = False,
    contour_upscale: int = CONTOUR_UPSCALE,
    smooth_blur: float = 1.0,
    world_x0: float = WORLD_MIN,
    world_z0: float = WORLD_MIN,
    world_x1: float = WORLD_MIN + WORLD_SIZE,
    world_z1: float = WORLD_MIN + WORLD_SIZE,
) -> tuple[list[dict], list[np.ndarray]]:
    """计算建成区底色环（世界坐标）。

    source: :class:`MapDocument`（首选）或 legacy ``MapData``（过渡）。
    exclude_services: 不参与底色的建筑 service 列表（PascalCase，缺省用
                      DEFAULT_EXCLUDE_SERVICES）。
    contour_upscale: 掩膜 BILINEAR 放大倍数（1=不放大；放大只加密采样点，
                     不改变形状，越大计算越慢）。
    smooth_blur: 掩膜高斯模糊半径（格）。二值掩膜直接提等值线是"格子阶梯"；
                 先模糊成连续场，50% 等值线才成为平滑曲线——这是决定轮廓
                 圆滑度的关键参数（0 = 不平滑，保持阶梯）。
    返回 (regions, rings)：
    - rings: 全部环（含外环与孔洞环），按面积降序
    - regions: 区域分组，["outer"] 为填充区环下标，["holes"] 为其孔洞环
    """
    if not getattr(source, "buildings", None) and not include_parks:
        return [], []
    excl = DEFAULT_EXCLUDE_SERVICES if exclude_services is None else tuple(exclude_services)
    got = _build_area_mask(
        source, cell_meters, buffer_meters, excl, include_parks,
        world_x0, world_z0, world_x1, world_z1,
    )
    if got is None:
        return [], []
    mask, ox, oz = got
    up = max(1, int(contour_upscale or 1))
    # 高斯模糊：把二值阶梯转成连续场，50% 等值线平滑（核心平滑手段）
    if smooth_blur is not None and smooth_blur > 0.05:
        mask = mask.filter(ImageFilter.GaussianBlur(smooth_blur))
    gw, gh = mask.size
    # BILINEAR 放大：加密等值线采样点
    if up > 1:
        mask = mask.resize((gw * up, gh * up), Image.BILINEAR)
    arr = np.asarray(mask, dtype=np.uint8)
    if not (arr > 0).any():
        return [], []

    rings = _extract_rings(arr, cell_meters, ox, oz, up)
    rings = [r for r in rings if _poly_area(r) >= MIN_POLY_AREA]
    if not rings:
        return [], []
    # 轮廓身份契约：regions 的索引和返回的 rings 必须来自同一个已排序列表。
    # 不要把排序藏在 _group_regions() 内部，否则渲染层拿到的环顺序会错位。
    rings = sorted(rings, key=_poly_area, reverse=True)
    return _group_regions(rings), rings


def _build_mask(
    source,
    cell_meters: float,
    exclude_services: tuple[str, ...],
    world_x0: float, world_z0: float, world_x1: float, world_z1: float,
):
    """构建掩膜（可裁剪到可见世界范围）。

    返回 (mask_img, origin_x, origin_z) 或 None：
    - mask_img: L 模式图像，尺寸 (gw, gh)，像素 (col=gx, row=gz)，
      255=建成区；原点 (0,0) 对应世界坐标 (origin_x, origin_z)。
    """
    grid_n = int(round(WORLD_SIZE / cell_meters))
    wx0 = max(world_x0, WORLD_MIN)
    wz0 = max(world_z0, WORLD_MIN)
    wx1 = min(world_x1, WORLD_MIN + WORLD_SIZE)
    wz1 = min(world_z1, WORLD_MIN + WORLD_SIZE)
    if wx1 <= wx0 or wz1 <= wz0:
        return None
    gx0 = max(0, min(grid_n - 1, int(np.floor((wx0 - WORLD_MIN) / cell_meters))))
    gz0 = max(0, min(grid_n - 1, int(np.floor((wz0 - WORLD_MIN) / cell_meters))))
    gx1 = max(0, min(grid_n - 1, int(np.ceil((wx1 - WORLD_MIN) / cell_meters))))
    gz1 = max(0, min(grid_n - 1, int(np.ceil((wz1 - WORLD_MIN) / cell_meters))))
    if gx1 <= gx0 or gz1 <= gz0:
        return None
    gw = gx1 - gx0 + 1
    gh = gz1 - gz0 + 1

    building_polys, _park_polys = _source_polys(source)
    mask = Image.new("L", (gw, gh), 0)
    d = ImageDraw.Draw(mask)
    for service, pts_world in building_polys:
        if service in exclude_services or len(pts_world) < 3:
            continue
        pts = [
            ((x - WORLD_MIN) / cell_meters - gx0,
             (z - WORLD_MIN) / cell_meters - gz0)
            for x, z in pts_world
        ]
        if len(pts) >= 3:
            d.polygon(pts, fill=255)

    return mask, WORLD_MIN + gx0 * cell_meters, WORLD_MIN + gz0 * cell_meters
