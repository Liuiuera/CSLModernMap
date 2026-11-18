"""通过统一标注流程绘制底图文字。

目前接入道路名和行政区名；交通标注及 POI 附属名称使用各自的绘制入口。"""
from __future__ import annotations

from typing import Any
import math

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from ..fonts import load_pillow_font, resolve_text_font
from ..cmm.model import RenderJob
from ..labeling import LabelPipeline, visibility_overrides
from ..style import hex_to_rgba
from .base import Layer, RenderContext


def _text_image(font, text, bbox, stroke, fill, stroke_fill, pad, blur):
    image = Image.new("RGBA", (bbox[2]-bbox[0]+2*pad, bbox[3]-bbox[1]+2*pad))
    pos = (pad-bbox[0], pad-bbox[1])
    fill_rgba, stroke_rgba = hex_to_rgba(fill), hex_to_rgba(stroke_fill)
    if blur == 0 and fill_rgba[3] == stroke_rgba[3] == 255:
        ImageDraw.Draw(image).text(pos, text, font=font, fill=fill_rgba,
                                  stroke_width=stroke, stroke_fill=stroke_rgba)
        return image
    glyph = Image.new("L", image.size)
    ImageDraw.Draw(glyph).text(pos, text, font=font, fill=255)
    if stroke or blur:
        halo = Image.new("L", image.size)
        ImageDraw.Draw(halo).text(pos, text, font=font, fill=255,
                                 stroke_width=stroke, stroke_fill=255)
        # 硬描边排除字身；柔光晕保留完整轮廓，模糊后再叠加清晰文字。
        halo = (halo.filter(ImageFilter.GaussianBlur(blur)) if blur
                else ImageChops.subtract(halo, glyph))
        image.paste(stroke_rgba[:3] + (0,), (0, 0, image.width, image.height))
        image.putalpha(halo.point(lambda value: round(value * stroke_rgba[3] / 255)))
    ink = Image.new("RGBA", image.size, fill_rgba[:3] + (0,))
    ink.putalpha(glyph.point(lambda value: round(value * fill_rgba[3] / 255)))
    image.alpha_composite(ink)
    return image


def _resolve_size(val: Any, render_scale: float) -> float:
    if isinstance(val, dict):
        return float(val.get("fixed", 0.0)) + float(val.get("scaled", 0.0)) * render_scale
    return float(val)


class LabelLayer(Layer):
    """在标注系统完成收集、筛选和放置后绘制底图文字。"""

    name = "label"
    late = True

    def __init__(self, show_road_names: bool | None = None,
                 show_district_names: bool | None = None,
                 *, force_detail: bool = False, rule_overrides=(),
                 name_filters: dict[str, bool] | None = None,
                 render_job: RenderJob | None = None):
        super().__init__()
        # 显式参数转换为同一组标注规则。
        legacy = visibility_overrides(
            road=show_road_names,
            district=show_district_names,
        )
        self.rule_overrides = tuple(dict(item) for item in (rule_overrides or ())) + legacy
        self.name_filters = dict(name_filters or {})
        self.render_job = render_job
        self.force_detail = bool(force_detail)
        self._font_cache = {}
        self.ctx = None

    def _job(self, ctx: RenderContext) -> RenderJob:
        job = self.render_job or getattr(ctx, "render_job", None)
        if job is not None:
            return RenderJob(
                    kind=job.kind,
                    layers=job.layers,
                    canvas=job.canvas,
                    detail_mode=job.detail_mode,
                    derive_params={**job.derive_params,
                                   "use_game_names": bool(ctx.style.get("roads", {}).get("use_game_names", False)),
                                   **self.name_filters},
                    style_override=job.style_override,
                    rule_overrides=tuple(job.rule_overrides) + self.rule_overrides,
                )
        detail_mode = "detailed" if self.force_detail else getattr(
            ctx.detail_mode, "value", ctx.detail_mode)
        return RenderJob(
            kind="normal",
            layers=("label",),
            canvas={"width": ctx.image.width, "height": ctx.image.height,
                    "zoom": ctx.final_zoom},
            detail_mode=detail_mode,
            derive_params={"render_scale": ctx.zoom,
                           "use_game_names": bool(ctx.style.get("roads", {}).get("use_game_names", False)),
                           **self.name_filters},
            rule_overrides=self.rule_overrides,
        )

    def render(self, ctx: RenderContext, document) -> None:
        # 标签管线经 MapDocumentView 从 CMM 惰性取数。
        mapdata = (ctx.session._legacy_view() if ctx.session is not None
                   else document)
        self.ctx = ctx
        self.mapdata = mapdata
        self.render_scale = ctx.zoom
        self._aa_k = ctx.zoom / ctx.final_zoom if ctx.final_zoom else 1.0
        self._font_cache = {}
        pipeline = LabelPipeline()
        pipeline.run(
            mapdata,
            self._job(ctx),
            ctx.style,
            detail_policy=ctx.detail_policy,
            context=ctx,
            measure_text=self._text_width_px,
            painter=self._paint,
        )

    def _paint(self, placements) -> None:
        # 先绘制低优先级再绘制高优先级，保持道路和行政区叠放顺序。
        ordered = sorted(placements, key=lambda item: item.label.priority)
        for placed in ordered:
            label = placed.label
            cfg = dict(placed.style)
            blur = _resolve_size(cfg.get("stroke_blur", 0), self.render_scale)
            if not math.isfinite(blur) or blur < 0:
                raise ValueError("labels stroke_blur must be finite and nonnegative")
            if label.type == "DISTRICT_NAME":
                floor_final = 12.0 * min(self.ctx.final_zoom, 1.0)
                size = max(_resolve_size(cfg.get("size", 20), self.render_scale),
                           floor_final * self._aa_k)
                stroke = _resolve_size(cfg.get("stroke_width", 2), self.render_scale)
                self._render_text(
                    (placed.x, placed.y), label.text, size,
                    cfg.get("color", "#32415A"), stroke,
                    cfg.get("stroke", "#FFFFFF"),
                    font_family=cfg.get("font"), bold=bool(cfg.get("bold", False)),
                    stroke_blur=blur,
                )
            elif label.type == "ROAD_NAME":
                metadata = placed.metadata
                size = float(metadata.get("size", _resolve_size(cfg.get("size", 16), self.render_scale)))
                stroke = float(metadata.get("stroke_width", _resolve_size(cfg.get("stroke_width", 2), self.render_scale)))
                self._render_text_run(
                    metadata["text_run"], placed.bbox, label.text, size,
                    cfg.get("color", "#505564"), stroke,
                    cfg.get("stroke", "#FFFFFF"),
                    font_family=cfg.get("font"),
                    stroke_blur=blur,
                )

    def _text_width_px(self, text: str, size: float,
                       font_family: str | None = None) -> float:
        font = self._get_font(max(int(round(size)), 6), font_family, text=text)
        probe = ImageDraw.Draw(Image.new("RGBA", (4, 4), (0, 0, 0, 0)))
        bbox = probe.textbbox((0, 0), text, font=font)
        return float(bbox[2] - bbox[0])

    def _render_text_run(self, strips, bounds, text, size, fill, stroke_w,
                         stroke_fill, font_family=None, stroke_blur=0.0):
        font = self._get_font(size, font_family, text=text)
        stroke = max(int(round(stroke_w)), 0)
        bbox = font.getbbox(text, stroke_width=stroke)
        tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
        pad = math.ceil(3 * stroke_blur)
        run = _text_image(font, text, bbox, stroke, fill, stroke_fill, pad, stroke_blur)
        bleed = math.ceil(pad * math.sqrt(2))
        left, top = int(np.floor(bounds[0]))-bleed, int(np.floor(bounds[1]))-bleed
        right, bottom = int(np.ceil(bounds[2]))+bleed, int(np.ceil(bounds[3]))+bleed
        yy, xx = np.mgrid[top:bottom, left:right].astype(float)
        best = np.full(xx.shape, np.inf)
        u = np.zeros(xx.shape)
        v = np.zeros(xx.shape)
        # 逆向采样保留已经排版的文字和字距；单张栅格可避免旋转条带之间的接缝。
        for x, y, angle, offset, span in strips:
            dx, dy = xx-x, yy-y
            distance = dx*dx+dy*dy
            chosen = distance < best
            a = np.radians(angle)
            u[chosen] = offset+span/2 + dx[chosen]*np.cos(a)+dy[chosen]*np.sin(a)
            v[chosen] = th/2 - dx[chosen]*np.sin(a)+dy[chosen]*np.cos(a)
            best[chosen] = distance[chosen]
        u += pad
        v += pad
        tw, th = run.size
        valid = (u >= 0) & (v >= 0) & (u < tw-1) & (v < th-1)
        sx, sy = np.clip(u, 0, tw-1), np.clip(v, 0, th-1)
        ix, iy = sx.astype(int), sy.astype(int)
        fx, fy = (sx-ix)[..., None], (sy-iy)[..., None]
        pixels = np.asarray(run, dtype=float)
        # 对预乘透明度插值，使透明边缘保留原色。
        pixels[..., :3] *= pixels[..., 3:4]/255
        jx, jy = np.minimum(ix+1, tw-1), np.minimum(iy+1, th-1)
        out = ((pixels[iy, ix]*(1-fx)+pixels[iy, jx]*fx)*(1-fy)
               +(pixels[jy, ix]*(1-fx)+pixels[jy, jx]*fx)*fy)
        alpha = out[..., 3:4]
        out[..., :3] = np.divide(out[..., :3]*255, alpha,
                                 out=np.zeros_like(out[..., :3]), where=alpha > 0)
        out[~valid] = 0
        patch = Image.fromarray(np.uint8(np.clip(out, 0, 255)))
        if self.ctx.image.mode == "RGB":
            self.ctx.image.paste(patch, (left, top), patch)
        else:
            self.ctx.image.alpha_composite(patch, (left, top))

    def _get_font(self, size: float, font_family: str | None = None,
                  text: str | None = None, bold: bool = False) -> ImageFont.FreeTypeFont:
        size_px = max(int(round(size)), 6)
        path = resolve_text_font(font_family, text=text, bold=bold)
        key = (size_px, str(path) if path is not None else "", bold)
        if key not in self._font_cache:
            if path is not None:
                try:
                    self._font_cache[key] = ImageFont.truetype(str(path), size_px)
                except OSError:
                    self._font_cache[key] = load_pillow_font(size_px, bold=bold)
            else:
                self._font_cache[key] = load_pillow_font(size_px, bold=bold)
        return self._font_cache[key]

    def _render_text(self, pos, text: str, size: float, fill: str,
                     stroke_w: float, stroke_fill: str, angle: float = 0.0,
                     font_family: str | None = None, bold: bool = False,
                     stroke_blur: float = 0.0) -> None:
        font = self._get_font(size, font_family, text=text, bold=bold)
        stroke = max(int(round(stroke_w)), 0)
        probe = ImageDraw.Draw(Image.new("RGBA", (4, 4), (0, 0, 0, 0)))
        bbox = probe.textbbox((0, 0), text, font=font, stroke_width=stroke)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if tw <= 0 or th <= 0:
            return
        pad = stroke + 4 + math.ceil(3 * stroke_blur)
        img = _text_image(font, text, bbox, stroke, fill, stroke_fill, pad, stroke_blur)
        if angle:
            # 双三次插值会使半透明文字的透明度过冲；旧版不透明路径保持原样。
            translucent = hex_to_rgba(fill)[3] < 255 or hex_to_rgba(stroke_fill)[3] < 255
            resample = Image.BILINEAR if translucent or stroke_blur else Image.BICUBIC
            img = img.rotate(-angle, expand=True, resample=resample)
        px, py = pos
        x = int(round(px - img.width / 2))
        y = int(round(py - img.height / 2))
        if self.ctx.image.mode == "RGB":
            self.ctx.image.paste(img, (x, y), img)
        else:
            self.ctx.image.alpha_composite(img, (x, y))
