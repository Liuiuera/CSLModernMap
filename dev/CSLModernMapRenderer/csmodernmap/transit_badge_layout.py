"""公共交通线路徽章的像素布局。Viewer 的 JS 由本模块生成。"""


def candidate_points(points, step, limit, width, height):
    if len(points) < 2:
        return []
    if step <= 0:
        raise ValueError('Badge candidate interval must be positive')
    lengths = [0.0]
    for i in range(1, len(points)):
        dx = points[i][0] - points[i - 1][0]
        dy = points[i][1] - points[i - 1][1]
        lengths.append(lengths[i - 1] + (dx * dx + dy * dy) ** 0.5)
    total = lengths[-1]
    if total <= 0:
        return []
    mid = total / 2
    targets = []
    if total > step:
        target = step * 0.5
        while target < total and len(targets) < limit:
            targets.append(target)
            target += step
    if all([abs(t - mid) > step * 0.35 for t in targets]):
        targets.append(mid)
    targets.sort(key=lambda value: value)
    result = []
    i = 0
    for target in targets[:limit]:
        while i < len(lengths) - 2 and lengths[i + 1] < target:
            i += 1
        ratio = (target - lengths[i]) / max(lengths[i + 1] - lengths[i], 1e-9)
        x = points[i][0] + (points[i + 1][0] - points[i][0]) * ratio
        y = points[i][1] + (points[i + 1][1] - points[i][1]) * ratio
        if 0 <= x and x <= width and 0 <= y and y <= height:
            result.append([x, y])
    return result


def group_candidates(candidates, distance):
    """近邻拼接在首个候选点；同名同色在组内只出现一次。"""
    groups = []
    for x, y, key, member in candidates:
        for group in groups:
            dx, dy = group[0] - x, group[1] - y
            if dx * dx + dy * dy < distance * distance and distance > 0:
                break
        else:
            group = [x, y, [], []]
            groups.append(group)
        if key not in group[2]:
            group[2].append(key)
            group[3].append(member)
    return groups


def obstacle_score(box, obstacles, canvas_size, clearance_padding=12.0):
    width, height = canvas_size
    left, top, right, bottom = box
    outside = max(0, -left) + max(0, -top) + max(0, right - width) + max(0, bottom - height)
    safe = max(clearance_padding, (bottom - top) * 0.75, 4.0)
    cost = 0.0
    nearest = max(width, height, 1)
    for px, py in obstacles:
        dx = max(left - px, 0, px - right)
        dy = max(top - py, 0, py - bottom)
        distance = (dx * dx + dy * dy) ** 0.5
        nearest = min(nearest, distance)
        if distance < safe:
            cost += (safe - distance) / safe
    edge = min(left, top, width - right, height - bottom)
    return [outside, cost, min(nearest, max(edge, 0))]


def placement_reason(x, y, box, keys, min_gap, maximum, placed, counts, blocked):
    """按评分顺序调用，接受的位置立即占用间距和配额。"""
    if all([counts.get(key, 0) >= maximum for key in keys]):
        return 'quota'
    for px, py, previous in placed:
        dx, dy = x - px, y - py
        if min_gap > 0 and dx * dx + dy * dy < min_gap * min_gap:
            return 'repeat-gap'
    for px, py, previous in placed:
        if not (box[2] + 4 < previous[0] or previous[2] + 4 < box[0]
                or box[3] + 4 < previous[1] or previous[3] + 4 < box[1]):
            return 'badge-overlap'
    if blocked(box):
        return 'symbol-or-label-overlap'
    placed.append([x, y, box])
    for key in keys:
        counts[key] = counts.get(key, 0) + 1
    return 'placed'


def rank_candidates(items):
    """按外溢、障碍冲突、画面占用、负净空排序；并列时保留采样顺序。"""
    for column in (3, 2, 1, 0):
        items.sort(key=lambda row: row[column])
