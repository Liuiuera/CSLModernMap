"""导出模块：将渲染结果保存为 PNG（可选 SVG）。

独立子功能模块，负责图像文件的写出。
"""
from __future__ import annotations

from pathlib import Path


def export_png(image, path: str) -> str:
    """保存 PIL Image 为 PNG，返回实际路径。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    image.save(p, format="PNG", compress_level=3)
    return str(p)


def export_svg(svg_string: str, path: str) -> str:
    """保存 SVG 文本，返回实际路径（P2 功能，预留）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(svg_string, encoding="utf-8")
    return str(p)
