"""bundle 感知的资源路径定位。

PyInstaller/Nuitka 打包后资源位于只读的 bundle，源码目录下的
styles/ resources/ viewer/ cn_icons/ 等资源会被一并收集进 bundle。
本模块统一提供资源根目录，同时兼容源码运行与打包运行两种场景。
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


MAP_FILE_PATTERNS = (
    "*.mmap", "*.mmap.gz", "*.cslmap", "*.cslmap.gz",
    "*.cmm.gz", "*.cmm", "*.cmm.json",
)


def _is_bundled() -> bool:
    """是否运行在打包产物中（PyInstaller 或 Nuitka）。

    PyInstaller 设 ``sys.frozen``；Nuitka 的 ``__compiled__`` 是每个编译
    模块中的模块级特殊值，并不是 ``sys.__compiled__``。此前检查后者会
    把 Nuitka onefile 误判成源码运行，进而把图标路径拼到
    ``AppData/Local/assets``。
    """
    return bool(getattr(sys, "frozen", False) or
                globals().get("__compiled__") is not None)


def is_bundled() -> bool:
    """公开的只读运行模式判断，供 GUI 决定是否启用写时复制。"""
    return _is_bundled()


def bundle_root() -> Path:
    """资源根目录。

    - PyInstaller onefile：sys._MEIPASS（解压出的临时目录）
    - PyInstaller onedir：exe 所在目录下的 _internal
    - Nuitka standalone：exe 旁的目录
    - Nuitka onefile：Nuitka 解压后的目录（若可见），否则 exe 目录
    - 源码运行：csmodernmap/ 包的上级目录（即渲染器根目录）
    """
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    if _is_bundled():
        exe_dir = Path(sys.executable).resolve().parent
        # standalone 的资源通常在 exe 旁；onefile 的 sys.executable 可能
        # 仍指向外层 exe，因此只有发现资源哨兵时才采用该目录。
        if (exe_dir / "styles").is_dir() or (exe_dir / "tcl").is_dir():
            return exe_dir
        # Nuitka onefile 中，编译模块的 __file__ 指向临时解包根下的
        # csmodernmap/_paths.py；其上级的上级就是数据文件根目录。
        try:
            candidate = Path(__file__).resolve().parent.parent
            if ((candidate / "styles").is_dir() or
                    (candidate / "cn_icons").is_dir()):
                return candidate
        except (OSError, RuntimeError):
            pass
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_dir(name: str) -> Path:
    """定位资源子目录，如 styles / resources / web / cn_icons。

    注意：cn_icons 在源码运行位于渲染器上级目录，打包后收集进 bundle 根，
    与 styles/resources/web 的布局不同，请使用 cn_icons_dir()。
    """
    if name == "styles" and sys.platform == "win32" and not _is_macos():
        styles = application_settings_dir() / "styles"
        if styles.is_dir():
            return styles
    return bundle_root() / name


def cn_icons_dir() -> Path:
    """定位内置 POI 图标工具目录。

    此目录包含 ``poi_icon_utils.py``，只用于导入工具代码，不应指向
    用户可写副本。图标数据请通过 :func:`icons_library_dir` 解析。

    - 打包运行：bundle 内的 cn_icons/（Nuitka --include-data-dir 收集）
    - 源码运行：项目 assets\\cn_icons
    """
    if _is_bundled():
        return bundle_root() / "cn_icons"
    return bundle_root().parent.parent / "assets" / "cn_icons"


#: 已移除的内置图标库 -> 替代库，保证旧主题/设置里的库名仍可解析。
_RETIRED_ICON_LIBRARIES = {"cn_hd_icons": "cn_icons", "poi_icons": "cn_icons"}


def built_in_icons_library_dir(name: str) -> Path:
    """定位发行包/源码树中的只读内置图标库。"""
    name = _RETIRED_ICON_LIBRARIES.get(name, name)
    if _is_bundled():
        return bundle_root() / name
    return bundle_root().parent.parent / "assets" / name


def user_icons_root() -> Path:
    """用户可持久化图标库的根目录。"""
    return user_data_dir() / "user_icon_libraries"


def poi_mapping_override_path(settings_dir: str | Path | None = None) -> Path:
    """所有城市和主题共享 POI 映射。"""
    return user_data_dir() / "poi_direct_mapping.json"


def user_icons_library_dir(name: str) -> Path:
    """定位指定用户图标库（路径可能尚未创建）。"""
    return user_icons_root() / name


def _is_complete_icon_library(path: Path) -> bool:
    return (path / "icons_map.json").is_file()


def icons_library_dir(name: str) -> Path:
    """所有地图都使用内置 CSLModernMap 图稿，包括旧版主题。"""
    return built_in_icons_library_dir("cn_icons")


def icon_library_names() -> tuple[str, ...]:
    """返回唯一受支持的 POI 素材库。"""
    return ("cn_icons",)


def _outer_program_dir() -> Path:
    """外层程序（引导器/真实二进制）所在目录。

    Nuitka onefile 模式下 ``sys.executable`` 指向临时解包目录（每次运行
    不同、退出即删），不能作为可写位置；``__compiled__.containing_dir``
    是 Nuitka 官方提供的“程序真实位置”：onefile 时为外层二进制目录，
    standalone 时为二进制目录本身。老版本 Nuitka 没有该属性时回退
    ``NUITKA_ONEFILE_DIRECTORY`` 环境变量（onefile 引导器设置）。
    """
    compiled = globals().get("__compiled__")
    containing = getattr(compiled, "containing_dir", None)
    if containing:
        return Path(containing).resolve()
    onefile_dir = os.environ.get("NUITKA_ONEFILE_DIRECTORY")
    if onefile_dir:
        return Path(onefile_dir).resolve()
    return Path(sys.executable).resolve().parent


def _is_macos() -> bool:
    return sys.platform == "darwin"


def app_support_dir() -> Path:
    """macOS 标准的可写应用数据目录（~/Library/Application Support/CSLModernMap）。

    .app 的父目录可能是 /Applications 这类系统目录，往那里写
    settings.json / cache/ 会污染应用目录；.app 内部受签名保护且每次
    更新整体替换，都不能作为数据落点，因此 macOS 统一走系统标准位置。
    """
    return Path.home() / "Library" / "Application Support" / "CSLModernMap"


def application_settings_dir() -> Path:
    """定位跨城市和安装版本共享的应用设置。"""
    if sys.platform == "win32":
        explicit = os.environ.get("CSLMM_USER_DATA_DIR")
        if explicit:
            return Path(explicit)
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "CSLModernMap/UserData"
    if sys.platform == "darwin":
        return app_support_dir() / "preferences"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "CSLModernMap"


def user_data_dir() -> Path:
    """Windows 用户配置与自定义资源共用固定目录。"""
    if sys.platform == "win32" and not _is_macos():
        return application_settings_dir()
    return writable_dir()


def legacy_macos_writable_dir() -> Path | None:
    """旧版 ``writable_dir`` 在 macOS 的落点（.app 的父目录），仅供一次性迁移。"""
    if not (_is_bundled() and _is_macos()):
        return None
    program_dir = _outer_program_dir()
    app_bundle = next(
        (parent for parent in program_dir.parents
         if parent.name.lower().endswith(".app")),
        None,
    )
    return app_bundle.parent if app_bundle is not None else program_dir


#: 旧落点中需要搬移的用户数据文件（cache/ 会重新生成，不搬）。
_MACOS_MIGRATE_FILES = (
    "settings.json", "views.json", "poi_mapping.override.yaml",
)
_MACOS_MIGRATE_DIRS = ("custom_fonts", "user_icon_libraries")

_MACOS_MIGRATION_DONE = False


def _migrate_macos_writable_files(target: Path) -> None:
    """把旧落点（.app 旁）里的用户数据一次性搬进 Application Support。

    只复制目标不存在的条目，绝不覆盖新位置已有的文件；任何失败静默
    跳过——迁移失败不应挡住启动。每个进程最多执行一次。
    """
    global _MACOS_MIGRATION_DONE
    _MACOS_MIGRATION_DONE = True
    legacy = legacy_macos_writable_dir()
    if legacy is None:
        return
    try:
        if legacy.resolve() == target.resolve():
            return
    except OSError:
        return
    for name in _MACOS_MIGRATE_FILES:
        src, dst = legacy / name, target / name
        if src.is_file() and not dst.exists():
            try:
                target.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            except OSError:
                continue
    for name in _MACOS_MIGRATE_DIRS:
        src, dst = legacy / name, target / name
        if src.is_dir() and not dst.exists():
            try:
                target.mkdir(parents=True, exist_ok=True)
                shutil.copytree(src, dst)
            except OSError:
                continue


def writable_dir() -> Path:
    """用户可写的默认输出与缓存目录；Windows 配置另用 user_data_dir()。

    - macOS 打包运行：``~/Library/Application Support/CSLModernMap``。
      旧版本写 .app 的父目录，应用装进 /Applications 时会把 settings.json、
      cache/ 撒进系统目录；.app 内部受签名与更新限制不能写。首次调用时
      会把旧落点的用户数据一次性搬过来（见 ``_migrate_macos_writable_files``）。
      打开存档后，设置/输出目录仍会跟随存档所在目录（GuirBackend 现有行为）。
    - Windows 打包运行：可执行文件所在目录（便携式，沿用现状）。
    - 源码运行：渲染器根目录（与原 Path(__file__).parent 行为一致）
    """
    if not _is_bundled():
        return Path(__file__).resolve().parent.parent
    if _is_macos():
        target = app_support_dir()
        if not _MACOS_MIGRATION_DONE:
            _migrate_macos_writable_files(target)
        return target
    return _outer_program_dir()


def user_fonts_dir() -> Path:
    """用户自定义字体目录，可写且不随 Windows 程序版本变化。"""
    return user_data_dir() / "custom_fonts"
