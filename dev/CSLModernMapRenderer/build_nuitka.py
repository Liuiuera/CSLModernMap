# -*- coding: utf-8 -*-
"""Use standard Nuitka to package the shared Windows engine.

Release builds use dist/nuitka/*.dist. WinUI is published and assembled
separately; onefile remains available for diagnostics.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent.parent
DIST_ROOT = ROOT / "dist" / "nuitka"
REPORT_ROOT = ROOT / "build" / "nuitka"
MAIN = ROOT / "main.py"
OUTPUT_NAME = "CSLModernMapEngine.exe"
VIEWER_BUILD = PROJECT_ROOT / "tools/renderer/build_preview_viewer.py"

def _version() -> str:
    version_file = PROJECT_ROOT / "version.json"
    try:
        return str(json.loads(version_file.read_text(encoding="utf-8"))["version"])
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法读取版本号：{version_file}") from exc


def _nuitka_command() -> list[str]:
    # 构建和依赖探测使用同一个标准 Python 环境。
    import importlib.util

    if importlib.util.find_spec("nuitka") is None:
        raise RuntimeError("当前 Python 未安装 Nuitka：python -m pip install Nuitka")
    return [sys.executable, "-m", "nuitka"]


def _data_option(source: Path, target: str) -> str:
    return f"--include-data-dir={source}={target}"


def _build_command(mode: str, console: bool = False,
                   keep_onefile_temp: bool = False) -> list[str]:
    cn_icons = PROJECT_ROOT / "assets" / "cn_icons"
    rmp_icons = ROOT / "resources" / "icons" / "rmp"
    command = _nuitka_command() + [
        f"--mode={mode}",
        "--enable-plugin=pyside6",
        # Qt 只用于 QtCore/QtGui 路径几何，不创建窗口或用 Qt 解码图片。
        *[f"--noinclude-qt-plugins={family}" for family in (
            "imageformats", "iconengines", "mediaservice", "printsupport",
            "platforms", "platformthemes", "styles", "tls",
        )],
        "--nofollow-import-to=PySide6.QtWidgets,PySide6.QtNetwork,PySide6.QtSvg,PySide6.QtPdf,PySide6.QtPrintSupport",
        f"--windows-console-mode={'force' if console else 'disable'}",
        "--assume-yes-for-downloads",
        f"--output-dir={DIST_ROOT}",
        f"--output-filename={OUTPUT_NAME}",
        # 无窗口引擎不设置 exe 图标：Nuitka 会为 Qt 图标注入 QtWidgets。
        "--remove-output",
        f"--report={REPORT_ROOT / (mode + '-compilation.xml')}",
        # main.py 通过延迟导入和 CLI 子命令访问这些模块，显式收集避免漏包。
        "--include-package=csmodernmap",
        "--include-package=shapely",
        "--nofollow-import-to=shapely.tests,shapely.conftest",
        "--noinclude-pytest-mode=nofollow",
        "--include-distribution-metadata=shapely",
        "--nofollow-import-to=webview,pythonnet",
        "--nofollow-import-to=lxml.objectify,lxml.builder,lxml.sax,lxml.html,lxml.isoschematron",
        "--include-module=lxml.etree",
        "--include-module=poi_icon_utils",
        "--nofollow-import-to=tkinter",
        # cn_icons 不是 Python package，运行时由资源路径工具定位；
        # 编译阶段同时加入 PYTHONPATH，确保显式模块可以被解析。
        f"--include-plugin-directory={cn_icons}",
        _data_option(ROOT / "styles", "styles"),
        _data_option(ROOT / "viewer" / "dist", "viewer/dist"),
        # resources 只打包运行时必需部分：i18n / RMP PNG / Tabler 字体；
        # 示例存档、参考截图等开发素材不入包（预览均基于用户导入的地图）。
        # 地图文字字体仍由用户放入 custom_fonts/；这里只分发 MIT 许可的
        # Tabler UI 图标字体及其码点表。
        _data_option(ROOT / "resources" / "i18n", "resources/i18n"),
        *[f"--include-data-file={path}=resources/icons/rmp/{path.name}"
          for path in sorted(rmp_icons.glob("*.png"))],
        f"--include-data-file={ROOT / 'resources' / 'fonts' / 'tabler-icons.ttf'}=resources/fonts/tabler-icons.ttf",
        f"--include-data-file={ROOT / 'resources' / 'fonts' / 'tabler-icons.css'}=resources/fonts/tabler-icons.css",
        f"--include-data-file={ROOT / 'resources' / 'app.ico'}=resources/app.ico",
        f"--include-data-file={ROOT / 'csmodernmap' / 'services' / 'poi' / 'poi_mapping.yaml'}=csmodernmap/services/poi/poi_mapping.yaml",
        f"--include-data-file={ROOT / 'csmodernmap' / 'services' / 'poi' / 'poi_direct_mapping.json'}=csmodernmap/services/poi/poi_direct_mapping.json",
        f"--include-data-file={ROOT / 'csmodernmap' / 'services' / 'poi' / 'building_type_inventory.yaml'}=csmodernmap/services/poi/building_type_inventory.yaml",
        f"--include-data-file={ROOT / 'csmodernmap' / 'labeling' / 'rules.yaml'}=csmodernmap/labeling/rules.yaml",
        _data_option(cn_icons, "cn_icons"),
        _data_option(PROJECT_ROOT / "assets" / "map_icons_source", "map_icons_source"),
        *[f"--include-data-file={PROJECT_ROOT / name}=licenses/{name}"
          for name in ("LICENSE", "LICENSING.md", "THIRD_PARTY_NOTICES.md")],
        f"--include-data-file={ROOT / 'resources' / 'fonts' / 'Tabler-LICENSE.txt'}=licenses/Tabler-LICENSE.txt",
        str(MAIN),
    ]
    if mode == "onefile":
        # Windows 环境不一定定义 HOME；Nuitka 默认模板在此环境下会导致
        # onefile 启动时报 "couldn't runtime expand spec '{HOME}'"。
        if keep_onefile_temp:
            # 诊断 onefile 资源问题时保留解压目录，不用于正式发布。
            command.insert(-1, "--onefile-cache-mode=cached")
            command.insert(-1, "--onefile-tempdir-spec={CACHE_DIR}/CSLModernMap/CSLModernMapRenderer/diagnostic")
        else:
            command.insert(-1, "--onefile-tempdir-spec={TEMP}/CSLModernMapRenderer_{PID}")
    return command


def _clean(mode: str) -> None:
    if not DIST_ROOT.exists():
        return
    targets = list(DIST_ROOT.glob("*.dist")) + list(DIST_ROOT.glob("*.build"))
    if mode == "onefile":
        targets.append(DIST_ROOT / OUTPUT_NAME)
    for target in targets:
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()


def _running_process_count() -> int:
    """返回正在运行的程次数（按输出文件名匹配）。

    上一步构建出来的渲染器如果是启动状态会锁住 dist 里的 .pyd/.dll，
    导致 _clean() 删目录时 PermissionError，从而让 onefile 构建失败。
    这里主动探测并提示，而不是让用户面对晦涩的 WinError 5。
    """
    try:
        # 中文 Windows 的 tasklist 输出是本地代码页（GBK），按 UTF-8 严格解码会抛
        # UnicodeDecodeError，subprocess 内部读线程挂掉后 stdout 变成 None，
        # 整条构建就死在这里（2026-09-17 实测）。errors="replace" 就够了 ——
        # 这里只需要认出输出里有没有那行 CSV 文件名。
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {OUTPUT_NAME}",
             "/FO", "CSV", "/NH"],
            capture_output=True, text=True, errors="replace",
        ).stdout or ""
    except OSError:
        return 0
    count = 0
    for line in out.splitlines():
        if not line.strip():
            continue
        # tasklist 无匹配时输出 "INFO: No tasks are running..."，
        # 该行不含 CSV 分隔符，直接跳过。
        parts = line.split(",")
        if len(parts) < 2:
            continue
        if parts[0].strip('"').lower() == OUTPUT_NAME.lower():
            count += 1
    return count


def _write_report(mode: str, command: list[str], started: float,
                  returncode: int) -> None:
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    output = DIST_ROOT / OUTPUT_NAME
    dist_dirs = list(DIST_ROOT.glob("*.dist")) if mode == "standalone" else []
    files = []
    if output.is_file():
        files.append({"path": str(output), "bytes": output.stat().st_size})
    for dist_dir in dist_dirs:
        for path in dist_dir.rglob("*"):
            if path.is_file():
                files.append({"path": str(path), "bytes": path.stat().st_size})
    report = {
        "mode": mode,
        "version": _version(),
        "returncode": returncode,
        "elapsed_seconds": round(time.time() - started, 2),
        "command": command,
        "files": files,
        "total_bytes": sum(item["bytes"] for item in files),
    }
    (REPORT_ROOT / f"report_{mode}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _packaged_executable() -> Path | None:
    """构建产物里的可执行文件位置（standalone 在 *.dist 目录内）。"""
    direct = DIST_ROOT / OUTPUT_NAME
    if direct.is_file():
        return direct
    for dist_dir in sorted(DIST_ROOT.glob("*.dist")):
        candidate = dist_dir / OUTPUT_NAME
        if candidate.is_file():
            return candidate
    return None


def _run_packaged_self_check(mode: str) -> int:
    """Validate engine imports and resources in the built package."""
    exe = _packaged_executable()
    if exe is None:
        print("错误：未找到构建产物。")
        return 1
    report = REPORT_ROOT / f"selfcheck-{mode}.json"
    command = [str(exe), "self-check", "--report", str(report)]
    print(f"引擎自检：{' '.join(command)}")
    try:
        completed = subprocess.run(
            command, cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=300,
        )
    except subprocess.TimeoutExpired:
        print("错误：引擎自检超时（300 秒），产物可能在启动阶段卡住。")
        return 1
    except OSError as exc:
        print(f"错误：无法启动自检：{exc}")
        return 1

    for stream in (completed.stdout, completed.stderr):
        if stream and stream.strip():
            print(stream)
    if completed.returncode == 0:
        print(f"引擎自检通过。报告：{report}")
        return 0

    print(f"错误：引擎自检失败，退出码 {completed.returncode}。")
    try:
        print(report.read_text(encoding="utf-8"))
    except OSError:
        print(f"（无法读取自检报告 {report}）")
    return 1


def main() -> int:
    # Windows 中文控制台可能使用 GBK，Nuitka 的下载/编译日志含有 Unicode
    # 字符时不能让日志转发本身中断构建。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description="构建 CSLModernMap 渲染器 Nuitka 版本")
    parser.add_argument("--mode", choices=("standalone", "onefile"),
                        default="standalone", help="构建模式，默认 standalone")
    parser.add_argument("--clean", action="store_true", help="清理本次模式的旧产物")
    parser.add_argument("--console", action="store_true",
                        help="保留控制台窗口，用于诊断 引擎启动异常")
    parser.add_argument("--keep-onefile-temp", action="store_true",
                        help="诊断用：保留 onefile 解压目录，不用于正式发布")
    parser.add_argument("--skip-self-check", action="store_true",
                        help="跳过构建后的 引擎自检（仅在无图形界面的机器上使用）")
    parser.add_argument("--skip-viewer", action="store_true",
                        help="复用现有的 viewer/dist，不重建前端（只改了 Python 侧时用）")
    args = parser.parse_args()

    if not MAIN.is_file():
        print(f"错误：入口不存在：{MAIN}")
        return 1
    try:
        subprocess.run([sys.executable, str(PROJECT_ROOT / "tools/renderer/compile_locales.py")],
                       cwd=PROJECT_ROOT, check=True)
        # Viewer 构建是 standalone 的强制前置：运行包不依赖 Node，也不允许
        # 因前端缺失而静默回退到旧 PIL 中央预览。
        # --skip-viewer 只放行「产物已存在」这一种情况：只改 Python 侧时，
        # 为了重建载荷而整片重装 node_modules 既慢又没必要。
        if args.skip_viewer:
            if not (ROOT / "viewer" / "dist" / "index.html").is_file():
                print("错误：--skip-viewer 要求 viewer/dist 已构建，请先去掉该参数跑一次")
                return 1
            print("已按 --skip-viewer 复用现有 viewer/dist（未重建前端）。")
        else:
            subprocess.run(
                [sys.executable, str(VIEWER_BUILD)], cwd=ROOT, check=True)
        command = _build_command(
            args.mode,
            args.console,
            keep_onefile_temp=args.keep_onefile_temp,
        )
        version = _version()
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"错误：{exc}")
        return 1

    DIST_ROOT.mkdir(parents=True, exist_ok=True)
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    # 上次构建的渲染器若还在运行，会锁住 dist 里的 .pyd/.dll，
    # 导致清理/覆盖失败（WinError 5）。先探测并友好提示。
    running = _running_process_count()
    if running:
        print(
            f"错误：检测到 {running} 个正在运行的 {OUTPUT_NAME} 实例。\n"
            "请先关闭它再构建，否则清理输出目录会因文件被占用而失败。"
        )
        return 1
    if args.clean:
        _clean(args.mode)
    log_file = REPORT_ROOT / f"build_{args.mode}.log"
    started = time.time()
    print(f"开始构建 Nuitka {args.mode}：v{version}")
    print(f"输出目录：{DIST_ROOT}")
    print(f"日志文件：{log_file}")
    print("命令：", " ".join(command))
    with log_file.open("w", encoding="utf-8") as log:
        build_env = os.environ.copy()
        cache_dir = REPORT_ROOT / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        build_env["NUITKA_CACHE_DIR"] = str(cache_dir)
        cn_icons = PROJECT_ROOT / "assets" / "cn_icons"
        build_env["PYTHONPATH"] = os.pathsep.join(
            [str(cn_icons), build_env.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep)
        process = subprocess.Popen(
            command,
            cwd=str(ROOT),
            env=build_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="")
            log.write(line)
        returncode = process.wait()
    _write_report(args.mode, command, started, returncode)
    if returncode != 0:
        print(f"Nuitka 构建失败，退出码：{returncode}。详细日志：{log_file}")
        return returncode
    if args.mode == "onefile":
        # 诊断 onefile 时仍同步到旧契约路径，便于与 standalone 结果对比；
        # 正式发版组装与本地部署只读取 dist/nuitka/*.dist。
        legacy = ROOT / "dist" / OUTPUT_NAME
        try:
            legacy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(DIST_ROOT / OUTPUT_NAME, legacy)
            print(f"已同步发行产物：{legacy}")
        except OSError as exc:
            print(f"警告：同步发行产物失败（{exc}），请手动复制 {DIST_ROOT / OUTPUT_NAME} 到 {legacy}")
    if args.skip_self_check:
        print("已按 --skip-self-check 跳过 引擎自检。")
    elif _run_packaged_self_check(args.mode) != 0:
        print("构建产物未通过 引擎自检，不作为可发布版本。"
              f"完整日志：{REPORT_ROOT / f'build_{args.mode}.log'}")
        return 1
    print(f"Nuitka {args.mode} 构建完成。报告：{REPORT_ROOT / f'report_{args.mode}.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
