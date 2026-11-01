"""保留源轮廓直岸和地图闭合边的 Chaikin 平滑。"""
import numpy as np
from shapely.geometry import LineString


def _canonical_ring(points):
    points = np.asarray(points, dtype=np.float64)
    if np.array_equal(points[0], points[-1]):
        points = points[:-1]
    following = np.roll(points, -1, axis=0)
    if np.sum(points[:, 0]*following[:, 1]-following[:, 0]*points[:, 1]) < 0:
        points = points[::-1]
    left = np.flatnonzero(points[:, 0] == points[:, 0].min())
    start = left[np.argmin(points[left, 1])]
    return np.roll(points, -int(start), axis=0)


def prepare_shoreline(points, epsilon, unit, bounds=None):
    """在源采样尺度拟合直岸；返回无重复闭合点的轮廓和固定端点。"""
    points = _canonical_ring(points)
    if len(points) < 3:
        return points, np.ones(len(points), dtype=bool)
    # GEOS 在 XY 上简化并保留顶点 Z，用 Z 携带源索引，避免逐点反查。
    closed = np.vstack((points, points[0]))
    line = LineString(np.column_stack((closed, np.arange(len(closed)))))
    fine = np.asarray(line.simplify(epsilon, preserve_topology=False).coords)[:-1]
    if len(fine) < 3:
        return points, np.ones(len(points), dtype=bool)
    coarse = np.asarray(line.simplify(unit, preserve_topology=False).coords)[:-1]
    starts = coarse[:, 2].astype(np.intp)
    following = np.roll(coarse[:, :2], -1, axis=0)
    lengths = np.linalg.norm(following-coarse[:, :2], axis=1)
    straight = lengths >= 32*unit
    # 一个单元内的缓弯也能通过 RDP；检查分段平均偏差，区分栅格噪声与持续弯曲。
    for index in np.flatnonzero(straight):
        end = starts[index+1] if index+1 < len(starts) else len(points)
        samples = closed[starts[index]:end+1]-coarse[index, :2]
        direction = (following[index]-coarse[index, :2])/lengths[index]
        along = samples @ direction
        residual = samples[:, 0]*direction[1]-samples[:, 1]*direction[0]
        if np.ptp(residual) < unit/4:
            continue
        bins = np.clip((along/lengths[index]*8).astype(np.intp), 0, 7)
        counts = np.bincount(bins, minlength=8)
        occupied = counts > 0
        x = np.bincount(bins, weights=along, minlength=8)[occupied]/counts[occupied]
        y = np.bincount(bins, weights=residual, minlength=8)[occupied]/counts[occupied]
        x, y = x-x.mean(), y-y.mean()
        variance = x @ x
        if variance and np.max(abs(y-x*(x @ y)/variance)) > unit/4:
            straight[index] = False
    boundary = np.zeros(len(coarse), dtype=bool)
    if bounds is not None:
        for axis, value in ((0, bounds[0]), (1, bounds[1]), (0, bounds[2]), (1, bounds[3])):
            boundary |= (abs(coarse[:, axis]-value) < 1e-8) & (abs(following[:, axis]-value) < 1e-8)
    fixed = straight | np.roll(straight, 1) | boundary | np.roll(boundary, 1)
    if not np.any(fixed):
        return fine[:, :2], np.zeros(len(fine), dtype=bool)
    indices = fine[:, 2].astype(np.intp)
    interval = np.searchsorted(starts, indices, side='right')-1
    indices = indices[~(straight | boundary)[interval]]
    # epsilon 可以大于 unit；固定点不一定在细简化结果中，必须显式合并。
    indices = np.union1d(indices, starts[fixed])
    if len(indices) < 3:
        return points, np.ones(len(points), dtype=bool)
    pinned = np.zeros(len(indices), dtype=bool)
    pinned[np.searchsorted(indices, starts[fixed])] = True
    return points[indices], pinned


def simplify_fixed_ring(points, fixed_points, epsilon):
    """仅压缩固定端点之间的曲线采样，保留后续倒圆需要的约束。"""
    points = np.asarray(points)[:-1, :2]
    anchors = [index for index, point in enumerate(points) if tuple(point) in fixed_points]
    if not anchors:
        return np.asarray(LineString(np.vstack((points, points[0]))).simplify(epsilon).coords)
    pieces = []
    for index, start in enumerate(anchors):
        end = anchors[(index+1) % len(anchors)]
        chain = points[start:end+1] if end > start else np.vstack((points[start:], points[:end+1]))
        pieces.append(np.asarray(LineString(chain).simplify(epsilon).coords)[:-1])
    return np.concatenate(pieces)


def smooth_shoreline(points, pinned, iterations):
    """固定点分隔开曲线；纯直段直接保留，不生成重复的共线采样点。"""
    anchors = np.flatnonzero(pinned)
    if len(anchors) == len(points) or iterations == 0:
        return points
    if not len(anchors):
        for _ in range(iterations):
            following = np.roll(points, -1, axis=0)
            new = np.empty((len(points)*2, 2))
            new[0::2] = .75*points+.25*following
            new[1::2] = .25*points+.75*following
            points = new
        return points
    pieces = []
    for index, start in enumerate(anchors):
        end = anchors[(index+1) % len(anchors)]
        chain = points[start:end+1] if end > start else np.vstack((points[start:], points[:end+1]))
        if len(chain) > 2:
            for _ in range(iterations):
                new = np.empty((len(chain)*2, 2))
                new[0], new[-1] = chain[0], chain[-1]
                new[1:-1:2] = .75*chain[:-1]+.25*chain[1:]
                new[2:-1:2] = .25*chain[:-1]+.75*chain[1:]
                chain = new
        pieces.append(chain[:-1])
    return np.concatenate(pieces)
