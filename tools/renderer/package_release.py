"""Package the assembled Windows release and enforce its download budget."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DIST = REPO / "dev" / "CSLModernMapRenderer" / "dist"
MAX_BYTES = 80_000_000


def package(seven_zip: str) -> Path:
    source = DIST / "winui" / "CSLModernMapRenderer"
    for relative in (
        "CSLModernMapRenderer.exe", "CSLModernMapRenderer.pri",
        "engine/CSLModernMapEngine.exe", "engine/viewer/dist/index.html",
        "Assets/Rmp/embed.html",
    ):
        if not (source / relative).is_file():
            raise FileNotFoundError(f"装配目录缺少 {relative}；请先运行 winui/assemble.py --publish")
    version = json.loads((REPO / "version.json").read_text(encoding="utf-8"))["version"]
    output = DIST / "releases"
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"CSLModernMapRenderer-{version}-win-x64.7z"
    # 每次创建全新压缩包，避免更新已有归档时保留退役文件。
    with tempfile.TemporaryDirectory(prefix="package-", dir=output) as temporary:
        candidate = Path(temporary) / archive.name
        subprocess.run([
            seven_zip, "a", str(candidate), source.name,
            "-t7z", "-m0=LZMA2", "-mx=9", "-md=64m", "-ms=on", "-mmt=2",
        ], cwd=source.parent, check=True)
        subprocess.run([seven_zip, "t", str(candidate)], check=True)
        size = candidate.stat().st_size
        if size >= MAX_BYTES:
            raise ValueError(f"发行包 {size:,} 字节，必须小于 {MAX_BYTES:,} 字节")
        with candidate.open("rb") as stream:
            checksum = hashlib.file_digest(stream, "sha256").hexdigest()
        candidate.replace(archive)
    archive.with_suffix(".7z.sha256").write_text(
        f"{checksum}  {archive.name}\n", encoding="utf-8")
    print(f"发行包：{archive}\n体积：{size / 1_000_000:.2f} MB（上限 80 MB）")
    return archive


def main() -> int:
    parser = argparse.ArgumentParser(description="生成小于 80 MB 的 Windows 7z 发行包")
    parser.add_argument("--seven-zip", default="7z", help="7-Zip 命令行程序路径，默认从 PATH 查找 7z")
    args = parser.parse_args()
    package(args.seven_zip)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
