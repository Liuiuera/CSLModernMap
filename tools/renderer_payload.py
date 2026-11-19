"""Shared WinUI directory and verified renderer payload format for both game mods."""
from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WINUI_DIST_DIR = ROOT / "dev/CSLModernMapRenderer" / "dist" / "winui" / "CSLModernMapRenderer"
RENDERER_EXE = "CSLModernMapRenderer.exe"
RENDERER_FOLDER = "CSLModernMapRenderer"
RENDERER_PAYLOAD = "CSLModernMapRenderer.zip"
RENDERER_PAYLOAD_HASH = RENDERER_PAYLOAD + ".sha256"
RENDERER_MANIFEST = "renderer-manifest.sha256"
RENDERER_RESERVED_NAMES = {RENDERER_MANIFEST, ".installed.sha256"}


def renderer_folder(version: str) -> str:
    if re.fullmatch(r"\d+\.\d+\.\d+(?:f(?:[1-9]\d*)?)?", version) is None:
        raise ValueError("Invalid renderer version")
    return f"{RENDERER_FOLDER}-{version}"


def renderer_source_dir() -> Path | None:
    """Windows 两代 Mod 共用已组装的 WinUI 查看器，缺失时明确要求重新构建。"""
    required = (
        RENDERER_EXE, "engine/CSLModernMapEngine.exe", "engine/viewer/dist/index.html",
        "engine/styles", "engine/resources/i18n",
    )
    return WINUI_DIST_DIR if all((WINUI_DIST_DIR / name).exists() for name in required) else None


def _sha256(path: Path) -> str:
    """流式计算文件 SHA-256，避免把 renderer 大文件一次读入内存。"""
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def build_renderer_payload(renderer_src: Path, payload_path: Path, *, version: str,
                           files: list[Path] | None = None) -> str:
    """将 standalone 封装在带版本号的文件夹内，并返回载荷 SHA-256。"""
    folder = renderer_folder(version)
    files = sorted(files if files is not None else
                   (path for path in renderer_src.rglob("*") if path.is_file()))
    if not files or not (renderer_src / RENDERER_EXE).is_file():
        raise RuntimeError(f"渲染器目录不完整：{renderer_src}")

    manifest_lines: list[str] = []
    for path in files:
        relative = path.relative_to(renderer_src).as_posix()
        if "\t" in relative or "\n" in relative or "\r" in relative:
            raise RuntimeError(f"渲染器文件名不能包含制表符或换行：{relative}")
        if relative.lower() in RENDERER_RESERVED_NAMES:
            raise RuntimeError(f"渲染器目录占用了安装器保留文件名：{relative}")
        manifest_lines.append(f"{_sha256(path)}\t{path.stat().st_size}\t{relative}")
    manifest = ("\n".join(manifest_lines) + "\n").encode("utf-8")

    payload_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        payload_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6
    ) as archive:
        archive.writestr(f"{folder}/{RENDERER_MANIFEST}", manifest)
        for path in files:
            archive.write(path, f"{folder}/{path.relative_to(renderer_src).as_posix()}")
    return _sha256(payload_path)
