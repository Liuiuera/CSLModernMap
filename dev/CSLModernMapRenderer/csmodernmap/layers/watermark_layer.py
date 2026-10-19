"""水印图层：左下角单行紧凑水印（宋体小字，横排）。

绘制顺序：始终 late=True（抗锯齿后于最终画布绘制，清晰不糊）。

单行布局示例：
    泉都 · 2026-08-09 14:30:25 · 高德风格 · 4096×4096 · 路况 04:38
    (城市名)   (渲染时间)        (主题名)   (分辨率)   (交通时间，可选)

字体：宋体（SimSun / simsun.ttc），小字。
背景：极简半透明条（可选，默认带一点半透明白底保证可读性）。
"""
from __future__ import annotations

from ..style import hex_to_rgb

import datetime
from typing import List, Tuple

from PIL import Image, ImageDraw, ImageFont

from ..fonts import load_pillow_font, resolve_text_font
from ..i18n import tr_or as _T
from .base import Layer, RenderContext

# 宋体字体候选（按优先级）


def _parse_rgba(s: str) -> Tuple[int, int, int, int]:
    """解析 #RRGGBB 或 rgba(r,g,b,a) 字符串为 (R,G,B,A)。"""
    s = s.strip()
    if s.startswith("rgba(") and s.endswith(")"):
        inner = s[5:-1]
        parts = [p.strip() for p in inner.split(",")]
        if len(parts) == 4:
            r, g, b = int(parts[0]), int(parts[1]), int(parts[2])
            a = int(float(parts[3]) * 255) if "." in parts[3] else int(parts[3])
            return (r, g, b, a)
    rgb = hex_to_rgb(s)
    return (rgb[0], rgb[1], rgb[2], 255)


def _resolve_size(val, render_scale: float) -> float:
    if isinstance(val, dict):
        return val.get("fixed", 0.0) + val.get("scaled", 0.0) * render_scale
    return float(val)


class WatermarkLayer(Layer):
    """左下角单行水印（宋体小字，横排）。"""

    name = "watermark"
    late = True

    def __init__(self):
        self._font_cache: dict = {}

    # ------------------------------------------------------------------
    # 对外静态工具：不走 Renderer 管线时，直接在 PIL Image 上画水印
    # （用于立交拼图等特殊场景）
    # ------------------------------------------------------------------
    @classmethod
    def draw_on_image(
        cls,
        image: "Image.Image",
        style,
        document,
        *,
        render_scale: float = 1.0,
        custom_text: str | None = None,
    ) -> None:
        """直接在给定 PIL Image 上绘制水印（原地修改 image）。

        document: MapDocument 或 legacy MapData（读取
        city_name/generated/traffic 属性，两者同形）。
        render_scale: 画布相对 4096 的缩放（水印边距/字号自适应）。
        custom_text: 自定义附加文字（优先级高于 style 内嵌）。
        """
        cfg = style.get("watermark", {})
        if not cfg.get("enabled", True):
            return

        # 暂存自定义文字（用临时 style dict 避免污染原 style）
        if custom_text:
            cfg = dict(cfg)
            cfg["_custom_text"] = custom_text

        segments = cls._build_segments(cfg, document, style)
        if not segments:
            return
        draw = ImageDraw.Draw(image)
        layer = cls()
        layer._draw_impl(draw, image, cfg, segments, render_scale=render_scale)

    # ------------------------------------------------------------------
    # 主入口：Layer.render() 协议
    # ------------------------------------------------------------------
    def render(self, ctx: RenderContext, document) -> None:
        cfg = ctx.style.get("watermark", {})
        if not cfg.get("enabled", True):
            return

        render_scale = min(ctx.image.size) / 4096.0  # 成品尺寸，不随选区地理放大倍数膨胀
        custom = getattr(self, "_custom_text", None)
        segments = self._build_segments(cfg, document, ctx.style, custom_text=custom)
        if not segments:
            return
        self._draw_impl(ctx.draw, ctx.image, cfg, segments, render_scale=render_scale)

    # ------------------------------------------------------------------
    # 内部：组装文字片段（所有场景共用）
    # ------------------------------------------------------------------
    @staticmethod
    def _build_segments(
        cfg: dict,
        document,
        style,
        *,
        custom_text: str | None = None,
    ) -> List[Tuple[str, str]]:
        segments: List[Tuple[str, str]] = []
        content = cfg.get("content", {})

        # 模组名称（置首；发布要求水印标注模组名，yaml 可覆盖，缺省 CSLModernMap）
        mod_name = cfg.get("mod_name", "") or "CSLModernMap"
        if content.get("mod_name", True) and mod_name:
            segments.append((mod_name, "meta_color"))

        # 城市名
        city_name = getattr(document, "city_name", "") or ""
        if content.get("city_name", True) and city_name:
            segments.append((city_name, "city_color"))

        # 渲染时间
        if content.get("render_time", True):
            fmt = cfg.get("time_format", "%Y-%m-%d %H:%M:%S")
            t = datetime.datetime.now().strftime(fmt)
            segments.append((t, "info_color"))

        # 存档生成时间（默认关）
        generated = getattr(document, "generated", None)
        if content.get("map_generated_time", False) and generated:
            label = _T("watermark.save_timestamp", "Save timestamp")
            segments.append((f"{label} {generated}", "meta_color"))

        # 主题名（风格）
        if content.get("style_name", True):
            sname = style.get("name", "") or ""
            if sname:
                segments.append((sname, "meta_color"))

        # 分辨率（在绘制时根据实际画布尺寸补）
        if content.get("resolution", True):
            pass

        # 交通快照时间
        if content.get("traffic_time", True):
            t_str = WatermarkLayer._static_traffic_time_str(document)
            if t_str:
                label = _T("watermark.traffic_at", "Traffic at")
                segments.append((f"{label} {t_str}", "info_color"))

        # 自定义文字
        custom_txt = cfg.get("_custom_text") or custom_text or ""
        if custom_txt:
            segments.append((custom_txt, "meta_color"))

        return segments

    @staticmethod
    def _static_traffic_time_str(document) -> str | None:
        snap = getattr(document, "traffic", None)
        if snap is None:
            return None
        ts = getattr(snap, "snapshot_time", None) or getattr(snap, "snapshotTime", None)
        if not ts and isinstance(snap, dict):
            ts = snap.get("snapshot_time") or snap.get("snapshotTime")
        if not ts:
            # 类型化 TrafficSnapshot 的时间戳字段
            ts = getattr(snap, "timestamp", None)
        if not ts:
            return None
        if isinstance(ts, datetime.datetime):
            return ts.strftime("%m-%d %H:%M")
        return str(ts)[: len("2026-01-01 00:00:00")]

    # ------------------------------------------------------------------
    # 内部：实际绘制逻辑（所有场景共用）
    # ------------------------------------------------------------------
    def _draw_impl(
        self,
        draw: ImageDraw.ImageDraw,
        image: Image.Image,
        cfg: dict,
        segments: List[Tuple[str, str]],
        *,
        render_scale: float,
    ) -> None:
        """在 draw/image 上绘制单行水印。segments 已经是 _build_segments 的结果。"""
        # 分辨率在最后补（拿到实际 image.size 才知道）
        content = cfg.get("content", {})
        if content.get("resolution", True):
            w, h = image.size
            segments = list(segments)  # copy 避免修改原 list
            segments.append((f"{w}×{h}", "meta_color"))
        if not segments:
            return

        # 样式参数
        font_size = max(int(round(_resolve_size(
            cfg.get("font_size", {"fixed": 11, "scaled": 1.5}),
            render_scale,
        ))), 6)
        sep = cfg.get("separator", " · ")
        margin_x = int(_resolve_size(cfg.get("margin", {}).get("x", 20), render_scale))
        margin_y = int(_resolve_size(cfg.get("margin", {}).get("y", 20), render_scale))
        bg_raw = str(cfg.get("background", "transparent")).strip().lower()
        bg = None
        if bg_raw not in ("", "none", "transparent"):
            bg = _parse_rgba(str(cfg.get("background")))
        pad_x = int(_resolve_size(cfg.get("padding", {}).get("x", 12), render_scale))
        pad_y = int(_resolve_size(cfg.get("padding", {}).get("y", 6), render_scale))
        radius = int(_resolve_size(cfg.get("border_radius", 6), render_scale))

        color_map = {
            "city_color": cfg.get("city_color", "#1A1A1A"),
            "info_color": cfg.get("info_color", "#3A3A3A"),
            "meta_color": cfg.get("meta_color", "#6A6A6A"),
        }

        # 逐段按字符覆盖选字体：主题指定字体不含某段字符时（如拉丁子集
        # 字体遇中文），该段自动回退 CJK 字体，其余段保持原字体。
        font_family = cfg.get("font")
        seg_fonts = [
            self._get_font(font_size, font_family, text=txt)
            for txt, _ck in segments
        ]

        # 计算文字总宽高
        probe = ImageDraw.Draw(Image.new("RGBA", (4, 4), (0, 0, 0, 0)))
        sep_font = self._get_font(font_size, font_family, text=sep)
        sep_bbox = probe.textbbox((0, 0), sep, font=sep_font)
        sep_w = sep_bbox[2] - sep_bbox[0]

        piece_sizes: List[Tuple[int, int, int]] = []
        total_w = 0
        th = 0
        for i, (txt, _ck) in enumerate(segments):
            bbox = probe.textbbox((0, 0), txt, font=seg_fonts[i])
            tw = bbox[2] - bbox[0]
            th = max(th, bbox[3] - bbox[1])
            if i > 0:
                total_w += sep_w
            piece_sizes.append((i, total_w, tw))
            total_w += tw

        ref_bbox = probe.textbbox((0, 0), "Ag", font=seg_fonts[0])
        total_h = max(th, ref_bbox[3] - ref_bbox[1])
        if total_w <= 0 or total_h <= 0:
            return

        # 位置：左下角
        _, H = image.size
        box_w = total_w + pad_x * 2
        box_h = total_h + pad_y * 2
        x0 = margin_x
        y0 = H - margin_y - box_h

        # 背景：用临时 RGBA overlay 画半透明圆角矩形，再 paste 到主图
        # （主图通常是 RGB，直接 draw.rounded_rectangle 会忽略 alpha 变成不透明）
        # 发布要求「不带遮罩」：background 配置为 transparent / none 时跳过背景绘制
        if bg is not None and bg[3] > 0:
            overlay = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
            overlay_draw = ImageDraw.Draw(overlay)
            self._rounded_rect(
                overlay_draw,
                (0, 0, box_w - 1, box_h - 1),
                radius=radius,
                fill=bg,
            )
            image.paste(overlay, (x0, y0), overlay)

        # 逐段画文字（不同颜色，各自用按覆盖选出的字体）
        text_x = x0 + pad_x
        text_y_baseline = y0 + pad_y
        for seg_idx, offset_x, tw in piece_sizes:
            txt, ck = segments[seg_idx]
            fill = hex_to_rgb(color_map.get(ck, color_map["meta_color"])) + (255,)
            bb = probe.textbbox((0, 0), txt, font=seg_fonts[seg_idx])
            draw.text(
                (text_x + offset_x, text_y_baseline - bb[1]),
                txt,
                font=seg_fonts[seg_idx],
                fill=fill,
            )

    # ------------------------------------------------------------------
    # 工具函数
    # ------------------------------------------------------------------

    def _get_font(self, size_px: int, font_family: str | None = None,
                  text: str | None = None) -> ImageFont.FreeTypeFont:
        """按（字号，实际字体文件）缓存；给定 text 时按字符覆盖选字体。"""
        size = max(int(round(size_px)), 6)
        path = resolve_text_font(font_family, text=text)
        key = (size, str(path) if path is not None else "")
        if key not in self._font_cache:
            if path is not None:
                try:
                    self._font_cache[key] = ImageFont.truetype(str(path), size)
                except OSError:
                    self._font_cache[key] = load_pillow_font(size)
            else:
                self._font_cache[key] = load_pillow_font(size)
        return self._font_cache[key]

    @staticmethod
    def _rounded_rect(draw: ImageDraw.ImageDraw, xy, radius: int, fill):
        """绘制圆角矩形（兼容 Pillow 旧版）。"""
        try:
            draw.rounded_rectangle(xy, radius=radius, fill=fill)
        except AttributeError:
            draw.rectangle(xy, fill=fill)
