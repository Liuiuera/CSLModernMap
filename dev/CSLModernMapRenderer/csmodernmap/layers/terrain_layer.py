"""地形图层：按海拔分层设色生成高度伪彩底色图。

对应 SPEC F002（地形渲染）。

实现思路：
1. 地形网格 1081x1081，文本为有序 "陆地高度:水域深度" 对
2. 水域判定：WaterArray > 0.2；陆地显示高度 = LandArray - SeaLevel
3. 高度场/水域深度插值到画布分辨率，逐像素判色
   → 水陆边界平滑（连续场采样，无 1081 网格硬锯齿），画布级清晰
4. 直接粘贴到画布（质量优先，不做性能妥协）
"""
from __future__ import annotations

from ..style import hex_to_rgb

import math
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
from PIL import Image

from ..system_info import total_memory_gb
from ..terrain import elevation_rgb, multiscale_hillshade
from .base import Layer, RenderContext
from ..i18n import tr

# CSL 标准 9 格地图尺寸（单位：游戏内米）
WORLD_SIZE = 17280.0
WORLD_MIN = -8640.0
WATER_THRESHOLD = 0.2
_HILLSHADE_CACHE_KEY = "terrain.hillshade"


def _parallel_workers(h_px: int) -> int:
    """地形逐像素设色的线程数。

    numpy 内核（digitize/searchsorted/ufunc）执行时释放 GIL，因此行带
    设色可以用线程占满多核，无需进程。低配按内存收敛，避免 16K AA 下
    多块 float32/int64 临时数组同时驻留超出可用内存。
    """
    if h_px < 1024:
        return 1
    n = max(1, min((os.cpu_count() or 2), 16))
    mem_gb = total_memory_gb()
    if mem_gb is not None:
        if mem_gb < 8:
            n = min(n, 4)
        elif mem_gb < 16:
            n = min(n, 8)
    return n


class TerrainLayer(Layer):
    """地形渲染器（海拔色带 + 多尺度山体阴影）。"""

    name = "terrain"
    full_canvas = True  # 整图连续场：分块时作为底图整图渲染，保证无缝

    def prepare(self, session, job) -> None:
        """在裁剪前对完整原始高度图计算一次山影，供所有渲染上下文复用。

        样式由 Renderer 在调用前置入 ``session._prepare_style``。
        """
        document = session.document
        style = getattr(session, "_prepare_style", None)
        if style is None:
            return
        config = style.get("terrain.hillshade", {})
        if not isinstance(config, dict) or not bool(config.get("enabled", False)):
            return
        hm = document.terrain.height.values
        if hm is None:
            return
        # 全图山影按主题参数指纹做会话缓存：参数未变时跨渲染复用，
        # 微调其他图层不会再触发整张原始网格的山影重算；参数变化时
        # 新指纹计算并裁掉旧变体，会话内该服务始终只保留 1 槽。
        cache = session.service_cache
        cache.keep_only(_HILLSHADE_CACHE_KEY, config)
        if cache.get(_HILLSHADE_CACHE_KEY, config) is None:
            cache.put(
                _HILLSHADE_CACHE_KEY, config,
                self._compute_hillshade(hm, document.terrain.cell, config))

    @staticmethod
    def _compute_hillshade(heightmap, cell: float, config: dict):
        """读取主题参数并在原始网格上生成多尺度山影。"""
        try:
            return multiscale_hillshade(
                heightmap,
                cell_size=max(abs(float(cell)), 1e-6),
                azimuth=float(config.get("azimuth", 315.0)),
                altitude=float(config.get("altitude", 45.0)),
                intensity=float(config.get("intensity", 0.3)),
                large_scale_meters=float(
                    config.get("large_scale_meters", 320.0)),
                small_scale_meters=float(
                    config.get("small_scale_meters", 0.0)),
                large_scale_weight=float(
                    config.get("large_scale_weight", 0.35)),
                small_scale_weight=float(
                    config.get("small_scale_weight", 0.65)),
            )
        except (TypeError, ValueError, OverflowError):
            # 主题文件可由用户直接编辑；坏参数不能让整张地图无法打开。
            return None

    def render(self, ctx: RenderContext, document) -> None:
        """按当前范围和主题绘制地形底色与晕渲。"""
        hm = document.terrain.height.values
        if hm is None:
            return
        # 先按原始网格坐标裁剪，再转置/翻转。旧实现先对整张网格做
        # transpose + flip，随后仍用原始索引裁剪，非正方形区域会发生轴
        # 错位，导致局部地形与道路/建筑不对齐（6.5.3 修复）。
        land_raw = np.asarray(hm, dtype=np.float32)
        gh_raw, gw_raw = land_raw.shape

        # 1. 只采样画布可见的世界范围（区域渲染时全图像素会膨胀到十几万，
        #    必须裁剪网格，否则插值出的图像内存爆炸）
        _ter = document.terrain
        _ox, _oz, _cell = _ter.origin.x, _ter.origin.z, _ter.cell
        if _cell <= 0:
            _cell = WORLD_SIZE / 1081.0
        # 坐标变换是权威依据；预览调用方可能保留全图范围，不能把整张地形栅格拉伸进局部画布。
        bounds = getattr(ctx.transform, "region_tile", None)
        if bounds is None:
            bounds = getattr(ctx.transform, "region", (
                ctx.world_x0, ctx.world_z0, ctx.world_x1, ctx.world_z1))
        wx0, wz0 = max(bounds[0], _ox), max(bounds[1], _oz)
        wx1 = min(bounds[2], _ox + gw_raw * _cell)
        wz1 = min(bounds[3], _oz + gh_raw * _cell)
        if wx1 <= wx0 or wz1 <= wz0:
            return
        water_raw = None
        water_source = _ter.water_depth.values
        if water_source is not None:
            candidate_water = np.asarray(water_source, dtype=np.float32)
            if candidate_water.shape == land_raw.shape:
                water_raw = candidate_water
        gx0 = (wx0 - _ox) / _cell - 0.5
        gx1 = (wx1 - _ox) / _cell - 0.5
        gy0 = (wz0 - _oz) / _cell - 0.5
        gy1 = (wz1 - _oz) / _cell - 0.5
        i0 = max(int(np.floor(gx0)), 0)
        i1 = min(int(np.ceil(gx1)), gw_raw - 1)
        j0 = max(int(np.floor(gy0)), 0)
        j1 = min(int(np.ceil(gy1)), gh_raw - 1)
        if i1 <= i0 or j1 <= j0:
            return
        sub = np.flipud(np.fliplr(
            land_raw[j0:j1 + 1, i0:i1 + 1].T)).copy()

        # 山影必须从完整原始网格裁剪，而不是从当前区域重新求梯度。
        # 这样区域渲染/瓦片渲染会共享相同的坡面法线，不在边缘产生接缝。
        config = ctx.style.get("terrain.hillshade", {})
        shade_raw = None
        if isinstance(config, dict) and bool(config.get("enabled", False)):
            shade_raw = None
            if ctx.session is not None:
                shade_raw = ctx.session.service_cache.get(
                    _HILLSHADE_CACHE_KEY, config)
            if shade_raw is None:
                shade_raw = ctx.shared.get(_HILLSHADE_CACHE_KEY)
            if shade_raw is None:
                shade_raw = self._compute_hillshade(
                    land_raw, _cell, config)
                ctx.shared[_HILLSHADE_CACHE_KEY] = shade_raw
                if ctx.session is not None:
                    ctx.session.service_cache.put(
                        _HILLSHADE_CACHE_KEY, config, shade_raw)
            if shade_raw is not None:
                shade_raw = np.asarray(shade_raw, dtype=np.float32)
                if shade_raw.shape != land_raw.shape:
                    shade_raw = None

        def grid_to_world(gx: float, gy: float):
            x = _ox + (gx + 0.5) * _cell
            z = _oz + (gy + 0.5) * _cell
            return x, z

        corners = [
            grid_to_world(gx, gy)
            for gx in (i0, i1)
            for gy in (j0, j1)
        ]
        px = [ctx.transform(x, z)[0] for x, z in corners]
        py = [ctx.transform(x, z)[1] for x, z in corners]
        px0, px1 = int(round(min(px))), int(round(max(px))) + 1
        py0, py1 = int(round(min(py))), int(round(max(py))) + 1
        w_px, h_px = max(px1 - px0, 1), max(py1 - py0, 1)
        # 沿未旋转的栅格轴插值，随后旋转非正方形裁剪块时不得交换目标尺寸。
        if int(getattr(ctx, "rotation", 0) or 0) % 360 in (90, 270):
            w_px, h_px = h_px, w_px

        # 2. 高度场插值到画布分辨率（连续场 → 颜色边界平滑、画布级清晰）
        sea_level = document.sea_level
        land_h = sub - sea_level

        def upscale(arr: np.ndarray, h: int, w: int) -> np.ndarray:
            if arr.shape == (h, w):
                return np.asarray(arr, dtype=np.float32).copy()
            img = Image.fromarray(np.asarray(arr, dtype=np.float32), mode="F")
            return np.asarray(img.resize((w, h), Image.BILINEAR), dtype=np.float32)

        land_big = upscale(land_h, h_px, w_px)
        shade_big = None
        if shade_raw is not None:
            shade_sub = np.flipud(np.fliplr(
                shade_raw[j0:j1 + 1, i0:i1 + 1].T)).copy()
            shade_big = np.clip(upscale(shade_sub, h_px, w_px), 0.0, 1.0)

            # 水深网格与高度图同轴；在地形层先清掉水面的山影，避免水面
            # 在 WaterLayer 之外单独使用时仍出现陆地的明暗纹理。
            if water_raw is not None:
                water_sub = np.flipud(np.fliplr(
                    water_raw[j0:j1 + 1, i0:i1 + 1].T)).copy()
                water_big = upscale(water_sub, h_px, w_px)
                shade_big[water_big > WATER_THRESHOLD] = 1.0
                del water_big, water_sub
            del shade_sub
        ctx.report(0.35, tr("gui.progress.terrain.interpolate"))

        # 3. 逐像素判色（水面由 WaterLayer 矢量绘制覆盖，这里保持陆地色）
        # 内存要点：不用全图 int64 索引（8B/px）+ lut[idx] 副本，
        # 分块 np.digitize → uint8 索引 → 直接取色。峰值数组 ≈ float32 高度
        # + 小块 int64 索引 + uint8 色表 + 色表结果，比逐色带布尔掩码（每档
        # 一次全图布尔掩码 + 花式索引回写）更快且峰值内存相近
        # （16K 实测：逐带掩码 ~16s / 分块 digitize ~5s，结果逐像素一致）。
        colors = ctx.style.get("terrain.elevation_colors", [])
        colors = [c for c in colors if isinstance(c, dict)
                  and isinstance(c.get("threshold"), (int, float))
                  and isinstance(c.get("color"), str)]
        colors.sort(key=lambda c: float(c["threshold"]))
        if not colors:
            colors = [{"threshold": 0.0, "color": "#F6F7F9"}]
        thresholds = np.asarray(
            [c["threshold"] for c in colors], dtype=np.float32)
        lut = np.asarray([hex_to_rgb(c["color"]) for c in colors], dtype=np.uint8)
        elevation_mode = ctx.style.get("terrain.elevation_mode", "stepped")
        continuous = str(elevation_mode).strip().lower() in {
            "continuous", "gradient", "linear",
        }

        color_arr = np.empty((h_px, w_px, 3), dtype=np.uint8)

        # 行带并行设色：每块只写 color_arr 自己的行范围，只读 land_big /
        # dist_big 的独立行视图，结果与串行逐像素一致（numpy 内核释放
        # GIL，线程即可占满多核）。块数 = 2×线程数，保证负载均衡。
        n_workers = _parallel_workers(h_px)
        n_chunks = max(1, min(n_workers * 2, math.ceil(h_px / 512)))
        chunk_rows = math.ceil(h_px / n_chunks)
        ranges = [(r0, min(r0 + chunk_rows, h_px))
                  for r0 in range(0, h_px, chunk_rows)]

        def _fill_chunk(r0: int, r1: int) -> None:
            values = land_big[r0:r1]
            color_arr[r0:r1] = elevation_rgb(values, thresholds, lut, continuous)

        if n_workers <= 1 or len(ranges) <= 1:
            for i, (r0, r1) in enumerate(ranges):
                _fill_chunk(r0, r1)
                if i % 2 == 0 or i == len(ranges) - 1:
                    ctx.report(0.4 + 0.4 * (i + 1) / len(ranges),
                               tr("gui.progress.terrain.color", i=i + 1, n=len(ranges)))
        else:
            # 进度回调只在主线程调用（as_completed 循环），不跨线程。
            with ThreadPoolExecutor(max_workers=n_workers) as ex:
                futures = {ex.submit(_fill_chunk, r0, r1): (r0, r1)
                           for r0, r1 in ranges}
                done = 0
                for _fut in as_completed(futures):
                    _fut.result()
                    done += 1
                    ctx.report(0.4 + 0.4 * done / len(ranges),
                               tr("gui.progress.terrain.color", i=done, n=len(ranges)))
        del land_big
        ctx.report(0.85, tr("gui.progress.terrain.colored"))

        if shade_big is not None:
            # 山影约以主题 intensity（默认 25%~40%）乘到地形颜色上。
            # 分行处理，避免在大画布上额外创建一个 3D float64 数组；
            # 各行带读写互不重叠，可线程并行。
            def _shade_chunk(r0: int, r1: int) -> None:
                shaded = color_arr[r0:r1].astype(np.float32)
                shaded *= shade_big[r0:r1, :, None]
                color_arr[r0:r1] = np.clip(shaded, 0, 255).astype(np.uint8)

            if n_workers <= 1 or len(ranges) <= 1:
                for r0, r1 in ranges:
                    _shade_chunk(r0, r1)
            else:
                with ThreadPoolExecutor(max_workers=n_workers) as ex:
                    for _fut in [ex.submit(_shade_chunk, r0, r1)
                                 for r0, r1 in ranges]:
                        _fut.result()
            del shade_big
            ctx.report(0.93, tr("gui.progress.terrain.relief"))

        # 输出旋转：色块数组按 transform 同一角度顺时针重排，
        # 否则地形会与道路/水域等其他经 transform 旋转的图层错位 90°。
        rot = int(getattr(ctx, "rotation", 0) or 0) % 360
        if rot:
            k = {90: -1, 180: 2, 270: 1}.get(rot, 0)
            if k:
                color_arr = np.rot90(color_arr, k)

        # 4. 粘贴到画布
        color_img = Image.fromarray(color_arr, "RGB")
        ctx.image.paste(color_img, (px0, py0))

        ctx.report(1.0, tr("gui.progress.terrain.done"))
