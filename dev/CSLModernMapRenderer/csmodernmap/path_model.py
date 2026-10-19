"""结构化路径模型（移植自 railmapgen path.ts）。

将裸点列升级为 MoveTo/LineTo/CubicTo 命令序列，支持路径语义分类
（直线/尖角折线/圆角折线/复杂路径）。

核心设计：
- PathCommand 基类：M/L/C/Z 四种 SVG 命令
- OpenPath 类：LinearPath / SharpTurnPath / RoundedTurnPath / ComplexOpenPath
- 每个路径对象包含 kind（语义分类）、commands（命令序列）、d（SVG 序列化）
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np


def move_towards(moving, target, amount):
    dx = target[0] - moving[0]
    dy = target[1] - moving[1]
    dist = math.hypot(dx, dy)
    if dist < 1e-09:
        return (target[0], target[1])
    frac = min(1.0, amount / dist)
    return (moving[0] + dx * frac, moving[1] + dy * frac)


def normalize_angle(deg: float) -> float:
    while deg > 90:
        deg -= 180
    while deg < -90:
        deg += 180
    return deg


def chaikin(points, iterations=3):
    """闭合环角点切割，保留输入点顺序。"""
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    for _ in range(iterations):
        if len(pts) < 3:
            break
        nxt = np.roll(pts, -1, axis=0)
        out = np.empty((len(pts) * 2, 2), dtype=np.float64)
        out[0::2] = 0.75 * pts + 0.25 * nxt
        out[1::2] = 0.25 * pts + 0.75 * nxt
        pts = out
    return [tuple(point) for point in pts.tolist()]


@dataclass
class PathPoint:
    x: float
    y: float

    def __add__(self, other):
        return PathPoint(self.x + other.x, self.y + other.y)

    def __sub__(self, other):
        return PathPoint(self.x - other.x, self.y - other.y)

    def __mul__(self, scalar):
        return PathPoint(self.x * scalar, self.y * scalar)

    def distance_to(self, other: PathPoint) -> float:
        return math.hypot(self.x - other.x, self.y - other.y)

    def to_tuple(self):
        return (self.x, self.y)


@dataclass
class MoveTo:
    cmd: str = 'M'
    to: PathPoint = field(default_factory=lambda: PathPoint(0, 0))


@dataclass
class LineTo:
    cmd: str = 'L'
    to: PathPoint = field(default_factory=lambda: PathPoint(0, 0))


@dataclass
class CubicTo:
    cmd: str = 'C'
    c1: PathPoint = field(default_factory=lambda: PathPoint(0, 0))
    c2: PathPoint = field(default_factory=lambda: PathPoint(0, 0))
    to: PathPoint = field(default_factory=lambda: PathPoint(0, 0))


@dataclass
class ClosePath:
    cmd: str = 'Z'


PathCommand = MoveTo | LineTo | CubicTo | ClosePath


def make_point(x: float, y: float) -> PathPoint:
    return PathPoint(x, y)


def move_to(to: PathPoint) -> MoveTo:
    return MoveTo(to=to)


def line_to(to: PathPoint) -> LineTo:
    return LineTo(to=to)


def cubic_to(c1: PathPoint, c2: PathPoint, to: PathPoint) -> CubicTo:
    return CubicTo(c1=c1, c2=c2, to=to)


def close_path() -> ClosePath:
    return ClosePath()


def _serialize_command(cmd: PathCommand) -> str:
    if isinstance(cmd, MoveTo):
        return f"M {cmd.to.x} {cmd.to.y}"
    elif isinstance(cmd, LineTo):
        return f"L {cmd.to.x} {cmd.to.y}"
    elif isinstance(cmd, CubicTo):
        return f"C {cmd.c1.x} {cmd.c1.y} {cmd.c2.x} {cmd.c2.y} {cmd.to.x} {cmd.to.y}"
    elif isinstance(cmd, ClosePath):
        return 'Z'
    return ''


class OpenPath:
    """结构化开放路径。

    Attributes:
        kind: 语义分类 ('ml'/'mll'/'mlcl'/'complex-open')
        commands: 命令序列
        d: SVG path d 属性字符串
    """

    def __init__(self, kind: str, commands: list[PathCommand]):
        self.kind = kind
        self.commands = commands
        self.d = ' '.join(_serialize_command(c) for c in commands)

    @classmethod
    def linear(cls, start: PathPoint, end: PathPoint) -> OpenPath:
        return cls('ml', [move_to(start), line_to(end)])

    @classmethod
    def sharp_turn(cls, start: PathPoint, corner: PathPoint,
                   end: PathPoint) -> OpenPath:
        return cls('mll', [move_to(start), line_to(corner), line_to(end)])

    @classmethod
    def rounded_turn(cls, start: PathPoint, line_end: PathPoint,
                     c1: PathPoint, c2: PathPoint,
                     curve_end: PathPoint, end: PathPoint) -> OpenPath:
        return cls('mlcl', [
            move_to(start), line_to(line_end),
            cubic_to(c1, c2, curve_end),
            line_to(end)
        ])

    @classmethod
    def from_commands(cls, commands: list[PathCommand]) -> OpenPath:
        kind = _classify_commands(commands)
        return cls(kind, commands)

    @property
    def points(self) -> list[PathPoint]:
        """提取所有端点（M/L/C 的终点），用于需要裸点列的操作。"""
        pts = []
        for cmd in self.commands:
            if isinstance(cmd, (MoveTo, LineTo, CubicTo)):
                pts.append(cmd.to)
        return pts

    @property
    def start(self) -> Optional[PathPoint]:
        for cmd in self.commands:
            if isinstance(cmd, MoveTo):
                return cmd.to
        return None

    @property
    def end(self) -> Optional[PathPoint]:
        pts = self.points
        return pts[-1] if pts else None

    def to_polyline(self) -> list[tuple]:
        """转换为 (x,y) 点列（折线/曲线采样）。"""
        pts = self.points
        if len(pts) < 2:
            return [p.to_tuple() for p in pts]
        return [p.to_tuple() for p in pts]

    def to_sampled_points(self, samples_per_cubic: int = 8) -> list[tuple]:
        """将所有 Bézier 曲线采样为密集点列（用于平滑渲染）。"""
        out = []
        prev = None
        for cmd in self.commands:
            if isinstance(cmd, MoveTo):
                out.append((cmd.to.x, cmd.to.y))
                prev = cmd.to
            elif isinstance(cmd, LineTo):
                out.append((cmd.to.x, cmd.to.y))
                prev = cmd.to
            elif isinstance(cmd, CubicTo):
                if prev is not None:
                    out.extend(_sample_cubic(prev, cmd.c1, cmd.c2,
                                             cmd.to, samples_per_cubic))
                else:
                    out.append((cmd.to.x, cmd.to.y))
                prev = cmd.to
        return out

    def apply_offset(self, d: float) -> OpenPath:
        """对路径进行法向偏移（纯 Python 实现，无外部依赖）。"""
        raw = self.to_polyline()
        if len(raw) < 2 or abs(d) < 1e-6:
            return OpenPath.from_commands(list(self.commands))
        segs = []
        for i in range(len(raw) - 1):
            ax, ay = raw[i]
            bx, by = raw[i + 1]
            dx, dy = bx - ax, by - ay
            L = math.hypot(dx, dy)
            if L < 1e-9:
                continue
            ux, uy = dx / L, dy / L
            nx, ny = -uy, ux
            segs.append(((ax + nx * d, ay + ny * d),
                         (bx + nx * d, by + ny * d),
                         (ux, uy)))
        if not segs:
            return OpenPath.from_commands(list(self.commands))
        out = [segs[0][0]]
        for i in range(len(segs) - 1):
            a1, b1, u1 = segs[i]
            _a2, b2, u2 = segs[i + 1]
            ip = _line_intersect(a1, u1, b2, u2)
            dot = u1[0] * u2[0] + u1[1] * u2[1]
            angle = math.acos(max(-1.0, min(1.0, dot)))
            if angle > math.pi * 0.80:
                out.append(b1)
            elif ip is not None:
                out.append(ip)
            else:
                out.append(b1)
        out.append(segs[-1][1])
        return from_point_list(out, round_factor=0)

    def apply_smooth(self, round_factor: float = 3.0,
                     use_fractional: bool = False) -> OpenPath:
        """对路径进行圆角平滑。"""
        raw = self.to_polyline()
        smoothed = _smooth_points(raw, round_factor, use_fractional)
        return from_point_list(smoothed, round_factor=0)


def _classify_commands(commands: list[PathCommand]) -> str:
    n = len(commands)
    if n == 0:
        return 'empty-open'
    has_cubic = any(isinstance(c, CubicTo) for c in commands)
    if not has_cubic:
        if n == 2:
            return 'ml'
        elif n == 3:
            return 'mll'
        return 'complex-open'
    types = tuple(
        'M' if isinstance(c, MoveTo) else
        'L' if isinstance(c, LineTo) else
        'C' if isinstance(c, CubicTo) else '?'
        for c in commands
    )
    if types == ('M', 'L', 'C', 'L'):
        return 'mlcl'
    return 'complex-open'


def _sample_cubic(p0: PathPoint, c1: PathPoint, c2: PathPoint,
                   p1: PathPoint, samples: int) -> list[tuple]:
    """对三次 Bézier 曲线进行均匀参数采样。"""
    out = []
    for i in range(1, samples + 1):
        t = i / samples
        mt = 1 - t
        t2, t3 = t * t, t * t * t
        mt2, mt3 = mt * mt, mt * mt * mt
        x = (mt3 * p0.x + 3 * mt2 * t * c1.x
             + 3 * mt * t2 * c2.x + t3 * p1.x)
        y = (mt3 * p0.y + 3 * mt2 * t * c1.y
             + 3 * mt * t2 * c2.y + t3 * p1.y)
        out.append((x, y))
    return out


def from_point_list(pts: list[tuple],
                    round_factor: float = 0.0,
                    use_fractional: bool = False) -> OpenPath:
    """从裸点列构建 OpenPath（可选圆角）。

    Args:
        pts: (x, y) 点列
        round_factor: 圆角半径，0 = 无圆角
        use_fractional: True=按段长比例

    Returns:
        结构化 OpenPath 对象
    """
    if len(pts) < 2:
        if len(pts) == 1:
            return OpenPath('ml', [move_to(PathPoint(*pts[0])),
                                   line_to(PathPoint(*pts[0]))])
        return OpenPath('empty-open', [])

    if round_factor <= 0:
        commands = [move_to(PathPoint(*pts[0]))]
        for p in pts[1:]:
            commands.append(line_to(PathPoint(*p)))
        return OpenPath.from_commands(commands)

    smoothed = _smooth_points(pts, round_factor, use_fractional)
    commands = []
    for i, (x, y) in enumerate(smoothed):
        pt = PathPoint(x, y)
        if i == 0:
            commands.append(move_to(pt))
        else:
            commands.append(line_to(pt))
    return OpenPath.from_commands(commands)


def _smooth_points(pts: list[tuple], round_factor: float,
                    use_fractional: bool) -> list[tuple]:
    """点列圆角（内部实现，避免循环依赖）。"""
    if len(pts) < 3 or round_factor <= 0:
        return list(pts)


    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        prev_pt = pts[i - 1]
        cur_pt = pts[i]
        next_pt = pts[i + 1]

        v1x, v1y = cur_pt[0] - prev_pt[0], cur_pt[1] - prev_pt[1]
        v2x, v2y = next_pt[0] - cur_pt[0], next_pt[1] - cur_pt[1]
        L1 = math.hypot(v1x, v1y)
        L2 = math.hypot(v2x, v2y)

        if L1 < 1e-6 or L2 < 1e-6:
            continue

        cross = (v1x / L1) * (v2y / L2) - (v1y / L1) * (v2x / L2)
        if abs(cross) < 1e-6:
            out.append(cur_pt)
            continue

        if use_fractional:
            d1 = round_factor * L1 * 0.5
            d2 = round_factor * L2 * 0.5
        else:
            d1 = min(round_factor, L1 * 0.5)
            d2 = min(round_factor, L2 * 0.5)

        curve_start = move_towards(cur_pt, prev_pt, d1)
        curve_end = move_towards(cur_pt, next_pt, d2)

        out.append(curve_start)

        cp1 = move_towards(curve_start, curve_end, 0.5)
        cp2 = move_towards(curve_end, curve_start, 0.5)

        n_samples = max(4, int(max(d1, d2) / 2))
        for k in range(1, n_samples + 1):
            t = k / (n_samples + 1)
            t2, t3 = t * t, t * t * t
            mt = 1 - t
            x = (mt ** 3) * curve_start[0] + 3 * mt * mt * t * cp1[0] \
                + 3 * mt * t2 * cp2[0] + t3 * curve_end[0]
            y = (mt ** 3) * curve_start[1] + 3 * mt * mt * t * cp1[1] \
                + 3 * mt * t2 * cp2[1] + t3 * curve_end[1]
            out.append((x, y))

    out.append(pts[-1])
    return out


def _line_intersect(p1: tuple, d1: tuple, p2: tuple, d2: tuple):
    """求两直线交点（向量形式：p + t * d）。"""
    x1, y1 = p1
    dx1, dy1 = d1
    x2, y2 = p2
    dx2, dy2 = d2
    det = dx1 * (-dy2) - dy1 * (-dx2)
    if abs(det) < 1e-8:
        return None
    s = ((x2 - x1) * (-dy2) - (y2 - y1) * (-dx2)) / det
    return (x1 + s * dx1, y1 + s * dy1)
