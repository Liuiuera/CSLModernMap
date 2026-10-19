"""Build the Windows WinUI viewer with its Python engine."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
DIST = HERE.parent / "dist"


def source_engine() -> Path:
    root = REPO / "dev" / "CSLModernMapRenderer" / "dist" / "nuitka"
    choices = [item for item in root.glob("*.dist") if complete_engine(item)]
    if not choices:
        raise FileNotFoundError("请先运行 dev/CSLModernMapRenderer/build_nuitka.py --mode standalone")
    return max(choices, key=lambda item: item.stat().st_mtime)


def complete_engine(directory: Path) -> bool:
    if not all((directory / relative).exists() for relative in (
        "CSLModernMapEngine.exe", "viewer/dist/index.html", "styles",
        "resources/i18n/catalogs.json", "resources/i18n/languages.json",
    )):
        return False
    expected = json.loads((HERE.parent / "resources/i18n/languages.json").read_text(encoding="utf-8"))
    options = json.loads((directory / "resources/i18n/languages.json").read_text(encoding="utf-8"))
    languages = {option["code"] for option in expected}
    if languages != {option["code"] for option in options}:
        return False
    catalogs = json.loads((directory / "resources/i18n/catalogs.json").read_text(encoding="utf-8"))
    return languages <= catalogs.keys() and all(
        (directory / f"resources/i18n/{language}/LC_MESSAGES/cslmodernmap.mo").is_file()
        for language in languages)


def assemble(*, publish: bool, engine_source: Path | None) -> Path:
    published = DIST / "publish"
    if publish:
        # publish 不会移除已退役依赖，避免旧 DLL 混入新发行目录。
        if published.exists():
            if not published.resolve().is_relative_to(DIST.resolve()):
                raise RuntimeError("publish 目标超出 WinUI dist 目录")
            shutil.rmtree(published)
        subprocess.run([
            "dotnet", "publish", str(HERE / "CSLModernMapWinUI.csproj"),
            "-c", "Release", "-r", "win-x64", "--self-contained", "true",
            "-o", str(published),
        ], check=True)
    if not (published / "CSLModernMapRenderer.exe").is_file():
        raise FileNotFoundError("缺少 WinUI publish 产物；先运行 assemble.py --publish")
    legacy = [path.relative_to(published).as_posix() for path in published.rglob("*")
              if path.is_file() and (path.name.lower() == "syncfusion.license"
                  or (path.name.lower().startswith("syncfusion.") and path.suffix.lower() == ".dll"))]
    if legacy:
        raise ValueError(f"publish 目录含旧 Syncfusion 文件；清理后重新 publish：{', '.join(legacy)}")
    if not (published / "CSLModernMapRenderer.pri").is_file():
        raise FileNotFoundError("缺少 WinUI 本地化资源 CSLModernMapRenderer.pri；请重新 publish")
    for relative in ("embed.html", "build-info.json", "licenses/dependencies.json", "licenses/RMP-LICENSE.txt"):
        if not (published / "Assets" / "Rmp" / relative).is_file():
            raise FileNotFoundError(f"缺少内嵌 RMP 资源 {relative}；请重新 publish")
    engine = engine_source or source_engine()
    if not complete_engine(engine):
        raise FileNotFoundError(f"引擎目录不完整：{engine}")
    target = DIST / "winui" / "CSLModernMapRenderer"
    # Target is fixed under this script's dist/. Never accept a caller-supplied
    # deletion path, and leave the Qt/Nuitka source directory untouched.
    if target.exists():
        if not target.resolve().is_relative_to(DIST.resolve()):
            raise RuntimeError("装配目标超出 WinUI dist 目录")
        shutil.rmtree(target)
    shutil.copytree(published, target)
    shutil.copytree(engine, target / "engine")
    print(f"WinUI 装配目录：{target}")
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--publish", action="store_true", help="先构建 WinUI Release 产物")
    parser.add_argument("--engine-source", type=Path, help="已有 Nuitka standalone 引擎目录")
    args = parser.parse_args()
    assemble(publish=args.publish, engine_source=args.engine_source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
