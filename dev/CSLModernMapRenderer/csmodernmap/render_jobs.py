"""为各输出入口装配统一的渲染作业。"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .cmm.model import RenderJob
from .layers.registry import canonical_layer_key, default_layer_keys


VIEW_KINDS = ("normal", "transit", "metro", "web_tile")

# 区域规则替代作业内的动态 Python 回调，使各类局部视图都能跨进程序列化。
REGION_POLICY_KINDS = ("building_density", "most_prosperous", "congestion")


@dataclass(frozen=True)
class RegionPolicy:
    """Serializable rule for picking a render region inside the worker.

    kind:
      - ``building_density``: 按建筑密度加权随机选区（随机特写）；
      - ``most_prosperous``: 建筑总量最大的 tiles×tiles 窗口（最繁华）；
      - ``congestion``: 拥堵值最高的区域（堵车特写，需要交通快照）。
    seed: 随机数种子；None = 每次不同。
    avoid_centers: 最近选过的区域中心 (x, z)，新选区需与它们保持至少
        一个跨度的距离（随机特写避免连续落在同一块）。
    max_attempts: 避让重试次数上限。
    tiles: most_prosperous 的窗口边长（格）。
    side_m: most_prosperous 的窗口边长（米）。> 0 时优先于 ``tiles``。
        ``tiles`` 只兼容旧 CS1 的 1920m 网格。新 GUI 按源游戏地块尺寸
        计算 side_m，保证二代和扩展地图不误用一代网格。
    """

    kind: str
    seed: int | None = None
    avoid_centers: tuple[tuple[float, float], ...] = ()
    max_attempts: int = 6
    tiles: int = 3
    side_m: float = 0.0

    def __post_init__(self) -> None:
        kind = str(self.kind or "").strip().lower()
        if kind not in REGION_POLICY_KINDS:
            raise ValueError(f"unknown region policy kind: {self.kind!r}")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "avoid_centers", tuple(
            (float(x), float(z)) for x, z in (self.avoid_centers or ())))
        object.__setattr__(self, "max_attempts", max(1, int(self.max_attempts)))
        object.__setattr__(self, "tiles", max(1, int(self.tiles)))
        try:
            side = float(self.side_m)
        except (TypeError, ValueError):
            side = 0.0
        object.__setattr__(self, "side_m", max(0.0, side))

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "seed": self.seed,
            "avoid_centers": [list(center) for center in self.avoid_centers],
            "max_attempts": self.max_attempts,
            "tiles": self.tiles,
            "side_m": self.side_m,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RegionPolicy":
        return cls(
            kind=value.get("kind", ""),
            seed=value.get("seed"),
            avoid_centers=tuple(
                tuple(item) for item in (value.get("avoid_centers") or ())),
            max_attempts=value.get("max_attempts", 6),
            tiles=value.get("tiles", 3),
            side_m=value.get("side_m", 0.0),
        )

# 图层选择属于作业数据；各前端可显式指定，默认值统一从此表装配。
VIEW_PRESETS = {
    "normal": {"layers": default_layer_keys("normal")},
    "transit": {"layers": default_layer_keys("transit")},
    "metro": {"layers": default_layer_keys("metro")},
    "web_tile": {"layers": default_layer_keys("web_tile")},
}


def build_render_job(
    kind: str,
    *,
    layers: Sequence[str] | None = None,
    width: int | float = 4096,
    height: int | float | None = None,
    zoom: float | None = None,
    detail_mode: str = "auto",
    detail: str | None = None,
    derive_params: Mapping[str, Any] | None = None,
    style_override: Mapping[str, Any] | None = None,
    rule_overrides: Sequence[Mapping[str, Any]] | None = None,
) -> RenderJob:
    """根据共享视图预设表构造完整渲染作业。"""
    normalized_kind = str(kind or "normal").strip().lower()
    if normalized_kind not in VIEW_KINDS:
        raise ValueError(f"unknown render job kind: {kind!r}")
    if height is None:
        height = width
    if zoom is None:
        zoom = float(width) / 4096.0
    preset = VIEW_PRESETS[normalized_kind]
    selected = (tuple(canonical_layer_key(item) for item in layers)
                if layers is not None else tuple(preset["layers"]))
    params = dict(derive_params or {})
    params.setdefault("render_scale", float(zoom))
    return RenderJob(
        kind=normalized_kind,
        layers=selected,
        canvas={"width": int(width), "height": int(height), "zoom": float(zoom)},
        detail_mode=detail if detail is not None else detail_mode,
        derive_params=params,
        style_override=dict(style_override or {}),
        rule_overrides=tuple(dict(item) for item in (rule_overrides or ())),
    )


def build_transit_jobs(
    *,
    width: int | float,
    height: int | float | None = None,
    zoom: float | None = None,
    detail_mode: str = "auto",
    base_layers: Sequence[str] | None = None,
    overlay_layers: Sequence[str] = ("transit", "poi"),
    rule_overrides: Sequence[Mapping[str, Any]] | None = None,
    style_override: Mapping[str, Any] | None = None,
) -> tuple[RenderJob, RenderJob]:
    """返回交通合成所需的底图作业和透明覆盖作业。"""
    shared_rules = tuple(dict(item) for item in (rule_overrides or ()))
    base = build_render_job(
        "transit", width=width, height=height, zoom=zoom,
        detail_mode=detail_mode,
        layers=base_layers if base_layers is not None else default_layer_keys("transit"),
        derive_params={"phase": "base"},
        style_override=style_override,
        rule_overrides=shared_rules,
    )
    overlay = build_render_job(
        "transit", width=width, height=height, zoom=zoom,
        detail_mode="detailed",
        layers=overlay_layers,
        derive_params={"phase": "overlay", "transparent": True},
        style_override=style_override,
        rule_overrides=shared_rules,
    )
    return base, overlay
