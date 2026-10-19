"""建成区底色图层：把城市建成区铺统一底色（地形层之上、水域层之下）。

常规 PNG 路径采样会话级 ``SurfaceModel.built_area_distance``，以与 WebGL
相同的阈值和 smoothstep 公式合成；不提取轮廓，也不执行 Chaikin。旧的
contourpy/aggdraw 实现保留给显式 ``backend: legacy``、无会话调用和当前
尚未进入 Surface 语义的 ``include_parks`` 兼容模式。
"""
from __future__ import annotations

from ..style import hex_to_rgb

import numpy as np
import aggdraw

from ..built_area import CONTOUR_UPSCALE, DEFAULT_EXCLUDE_SERVICES, compute_built_area_rings
from ..terrain import elevation_rgb
from ..services.surface_renderer import composite_distance_fill
from .base import Layer, RenderContext
from ..i18n import tr

# Chaikin 角点切割迭代次数（越大轮廓越圆润，可在样式 built_area.smooth_iters 覆盖）
SMOOTH_ITERS = 3


def _poly_centroid(poly):
    return (
        float(np.mean(poly[:, 0])),
        float(np.mean(poly[:, 1])),
    )


def _chaikin(points, iterations=SMOOTH_ITERS):
    """Chaikin 角点切割平滑（闭合多边形）。

    numpy 向量化：原纯 Python 逐点拼接在 6 次迭代（点量 ×64）下
    大环（数千~上万个顶点）耗时明显；向量化后每次迭代一次数组运算。
    """
    pts = np.asarray(points, dtype=np.float64)
    for _ in range(iterations):
        n = len(pts)
        nxt = np.roll(pts, -1, axis=0)  # P[i+1]（闭合，末点回绕首点）
        a = 0.75 * pts + 0.25 * nxt
        b = 0.25 * pts + 0.75 * nxt
        out = np.empty((2 * n, 2), dtype=np.float64)
        out[0::2] = a
        out[1::2] = b
        pts = out
    return pts.tolist()


class BuiltAreaLayer(Layer):
    """共享距离场渲染器，带旧矢量实现的受控回退。"""

    name = "built_area"
    # 水面尚未迁入 Surface 前保持连续底图顺序：terrain -> built -> water。
    # Surface Renderer 自身按条带采样，不会创建整幅 float32 中间数组。
    full_canvas = True

    def render(self, ctx: RenderContext, document) -> None:
        """根据建成区模型绘制连续的城市底色。"""
        cfg = ctx.style.get("built_area", {})
        if not cfg or not cfg.get("enabled", True):
            return
        if not document.buildings and not cfg.get("include_parks", False):
            return

        # 新常规路径：只采样共享距离场。直接调用图层而没有 session、显式
        # legacy、或公园并入建成区时，保留旧轮廓路径作为兼容回退。
        use_surface = (
            ctx.session is not None
            and str(cfg.get("backend", "surface")).lower() != "legacy"
            and not bool(cfg.get("include_parks", False))
        )
        if use_surface:
            surface_cell = float(cfg.get("surface_cell_meters", 8.0) or 8.0)
            model = ctx.session.surface_model({
                "include_water": False,
                "include_districts": False,
                "cell_meters": surface_cell,
                "exclude_services": tuple(
                    cfg.get("exclude_services") or DEFAULT_EXCLUDE_SERVICES),
            })
            if model.built_area_distance is not None:
                fill_cfg = cfg.get("color") or ctx.style.get(
                    "canvas.background", "#F6F7F9")
                # 旧版轮廓路径使用 smooth_blur 平滑几何，表面图层不得将其变成透明度羽化。
                edge_meters = float(cfg.get("edge_meters", 0.0) or 0.0)
                composite_distance_fill(
                    ctx.image,
                    model.built_area_distance,
                    model.world_transform,
                    ctx.transform,
                    hex_to_rgb(str(fill_cfg)),
                    buffer_meters=float(cfg.get("buffer_meters", 60.0)),
                    edge_meters=edge_meters,
                    opacity=float(cfg.get("opacity", 1.0)),
                )
                ctx.report(1.0, tr("gui.progress.built_area.surface"))
                return

        # 网格单元按输出密度自适应：小图用大 cell（整图计算快），大图用小 cell 保轮廓精度。
        # ctx.final_zoom = 输出边长/4096（1024→0.25、4096→1、16K→4）；
        # 基准 cell 8m → cell = 8/zoom，clamp [4, 32]（1024→32m、4096→8m、16K→4m）。
        base_cell = float(cfg.get("cell_meters", 8.0) or 8.0)
        zoom = float(getattr(ctx, "final_zoom", 1.0) or 1.0) or 1.0
        cell_m = min(max(base_cell / zoom, 4.0), 32.0)
        regions, rings = compute_built_area_rings(
            document,
            cell_meters=cell_m,
            buffer_meters=float(cfg.get("buffer_meters", 60.0)),
            exclude_services=cfg.get("exclude_services") or None,
            include_parks=bool(cfg.get("include_parks", False)),
            contour_upscale=int(cfg.get("contour_upscale", CONTOUR_UPSCALE) or 1),
            smooth_blur=float(cfg.get("smooth_blur", 1.0) or 0.0),
            world_x0=ctx.world_x0,
            world_z0=ctx.world_z0,
            world_x1=ctx.world_x1,
            world_z1=ctx.world_z1,
        )
        if not rings:
            return
        ctx.report(0.25, tr("gui.progress.built_area.outline"))
        smooth_iters = int(cfg.get("smooth_iters", SMOOTH_ITERS) or 0)

        fill_cfg = cfg.get("color") or ctx.style.get("canvas.background", "#F6F7F9")
        fill = hex_to_rgb(str(fill_cfg))

        hole_idx = set()
        for reg in regions:
            hole_idx.update(reg["holes"])

        # 预取地形色表（孔洞填质心地形色，露出下方地形）
        self._hm = None
        lut, thresholds, gh, gw, sea_level = self._terrain_lut(ctx, document)

        agg = aggdraw.Draw(ctx.image)
        _n_ring = max(len(rings), 1)
        for i, ring in enumerate(rings):
            if i % 4 == 0 or i == _n_ring - 1:
                ctx.report(0.3 + 0.65 * (i + 1) / _n_ring,
                           tr("gui.progress.built_area.fill", i=i + 1, n=_n_ring))
            if i in hole_idx:
                cx, cz = _poly_centroid(ring)
                color = self._terrain_color_at(
                    cx, cz, lut, thresholds, gh, gw, sea_level, fallback=fill
                )
            else:
                color = fill
            pts = [ctx.transform(float(p[0]), float(p[1])) for p in ring]
            pts = _chaikin(pts, iterations=smooth_iters)
            if len(pts) < 3:
                continue
            coords = tuple(v for xy in pts for v in xy)
            agg.polygon(coords, None, aggdraw.Brush(color))
        agg.flush()
        ctx.report(1.0, tr("gui.progress.built_area.done"))

    def _terrain_lut(self, ctx, document):
        """预取地形色表，供孔洞取质心地形色。"""
        colors = ctx.style.get("terrain.elevation_colors", [])
        colors = sorted(colors, key=lambda node: node["threshold"])
        self._terrain_continuous = str(ctx.style.get("terrain.elevation_mode", "stepped")).strip().lower() in {"continuous", "gradient", "linear"}
        thresholds = np.array([c["threshold"] for c in colors], dtype=np.float32)
        lut = np.array([hex_to_rgb(c["color"]) for c in colors], dtype=np.uint8)
        hm = document.terrain.height.values
        if hm is None:
            return lut, thresholds, 0, 0, document.sea_level
        self._hm = np.asarray(hm)
        gh, gw = self._hm.shape
        self._hm_ox = document.terrain.origin.x
        self._hm_oz = document.terrain.origin.z
        self._hm_cell = document.terrain.cell
        return lut, thresholds, gh, gw, document.sea_level

    def _terrain_color_at(self, wx, wz, lut, thresholds, gh, gw, sea_level,
                          fallback):
        """取世界坐标处的海拔色，供建成区孔洞填色。"""
        if gh == 0 or gw == 0 or len(lut) == 0:
            return fallback
        hm = self._hm
        col = int((wx - getattr(self, "_hm_ox", -8640.0)) / getattr(self, "_hm_cell", 17280.0 / 1081.0))
        row = int((wz - getattr(self, "_hm_oz", -8640.0)) / getattr(self, "_hm_cell", 17280.0 / 1081.0))
        col = min(max(col, 0), gw - 1)
        row = min(max(row, 0), gh - 1)
        h = float(hm[row, col]) - sea_level
        color = elevation_rgb(np.asarray(h, dtype=np.float32), thresholds, lut,
                              self._terrain_continuous)
        return tuple(int(v) for v in color)
