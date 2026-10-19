"""主题字体选项与 Pillow 字体路径解析。

主题文件保存的是字体族名称（例如 ``SimHei``），而 Pillow 需要字体文件
路径。这里集中处理系统字体解析（Windows 注册表 / macOS 字体目录）、
常用中文字体别名和回退顺序，避免编辑器和各渲染图层维护不同的字体规则。

用户字体：exe 旁的 ``custom_fonts/`` 目录。用户放入自行获取的
.ttf/.otf/.ttc 等字体文件后，程序按族名自动识别、优先于系统字体，
保证跨机器一致（例如获取苹方 PingFang SC 放入其中即可还原 Apple 观感；
macOS 上苹方为系统自带字体，无需自备）。
文本含字体不覆盖的字符时，自动回退到 CJK 字体链。
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from PIL import ImageFont

from ._paths import user_fonts_dir


# value 是写入 YAML 的稳定名称，label 是主题编辑器中的可读名称。
# macOS 苹方为系统自带字体；其他平台需用户自备（程序不内附专有字体）。
_PINGFANG_LABEL = (
    "苹方 (PingFang SC · macOS 系统自带)"
    if sys.platform == "darwin"
    else "苹方 (PingFang SC · 需自备字体)"
)
COMMON_FONT_OPTIONS = (
    ("SimHei", "黑体 (SimHei)"),
    ("Microsoft YaHei", "微软雅黑 (Microsoft YaHei)"),
    ("Microsoft YaHei UI", "微软雅黑 UI (Microsoft YaHei UI)"),
    ("PingFang SC", _PINGFANG_LABEL),
    ("SimSun", "宋体 (SimSun)"),
    ("KaiTi", "楷体 (KaiTi)"),
    ("FangSong", "仿宋 (FangSong)"),
    ("Arial", "Arial"),
    ("Segoe UI", "Segoe UI"),
)


_KNOWN_FILE_NAMES = {
    "simhei": ("simhei.ttf",),
    "microsoft yahei": ("msyh.ttc", "msyh.ttf"),
    "microsoft yahei ui": ("msyhl.ttc", "msyh.ttc", "msyh.ttf"),
    "simsun": ("simsun.ttc", "simsun.ttf"),
    "kaiti": ("simkai.ttf", "simkai.ttc"),
    "fangsong": ("simfang.ttf", "simfang.ttc"),
    "arial": ("arial.ttf",),
    "segoe ui": ("segoeui.ttf",),
    "noto sans cjk sc": ("NotoSansCJK-Regular.ttc", "NotoSansSC-Regular.otf"),
    "arial unicode ms": ("arialuni.ttf",),
    # macOS 自带中文字体（位于 /System/Library/Fonts 等目录）
    "pingfang sc": ("PingFang.ttc",),
    "pingfang tc": ("PingFang.ttc",),
    "pingfang hk": ("PingFang.ttc",),
    "hiragino sans gb": ("Hiragino Sans GB.ttc",),
    "songti sc": ("Songti.ttc",),
    "stheiti": ("STHeiti Light.ttc",),
}

_CJK_FALLBACKS = (
    "PingFang SC",
    "Hiragino Sans GB",
    "Microsoft YaHei",
    "SimHei",
    "SimSun",
    "Noto Sans CJK SC",
    "Arial Unicode MS",
)
_CJK_BOLD_FALLBACKS = (
    "PingFang SC",
    "msyhbd.ttc",
    "simhei.ttf",
    "Microsoft YaHei",
    "SimHei",
    "Noto Sans CJK SC",
)

_DISPLAY_SUFFIX_RE = re.compile(
    r"\s*\((?:TrueType|OpenType|Collection|All resolutions?)\)\s*$",
    re.IGNORECASE,
)


def _norm(value: str) -> str:
    return " ".join(str(value or "").strip().casefold().split())


# ---------------------------------------------------------------- 用户字体
# 用户把自行获取的字体文件放入 exe 旁的 custom_fonts/，程序按族名自动
# 识别并优先生效。mtime 缓存：目录内容变化时重扫，运行中放入也生效。

_USER_FONT_EXTS = (".ttf", ".otf", ".ttc", ".otc")
_BOLD_SUB_RE = re.compile(r"\b(bold|semibold|demibold|heavy|black)\b", re.IGNORECASE)


def _font_family_info(path: Path) -> tuple[str | None, str | None, str]:
    """解析字体族名/显示名/字重，无法解析返回 (None, None, "")。"""
    try:
        from fontTools.ttLib import TTFont
        tf = TTFont(str(path), fontNumber=0, lazy=True)
        try:
            name = tf["name"]
            norm_family = name.getDebugName(16) or name.getDebugName(1) or path.stem
            display = name.getDebugName(1) or path.stem
            sub = name.getDebugName(2) or ""
        finally:
            tf.close()
        return norm_family, display, sub
    except Exception:  # noqa: BLE001 - 单字体解析失败不影响其余字体
        return None, None, ""


def _scan_custom_fonts() -> tuple[dict[str, list[tuple[Path, bool]]],
                                  dict[str, list[Path]],
                                  dict[str, str]]:
    """扫描 custom_fonts/：返回 (族名→[(路径, 是否粗体)],
    文件名stem→[路径], 族名→显示名)。目录不存在时按需创建。"""
    d = user_fonts_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        return {}, {}, {}
    by_family: dict[str, list[tuple[Path, bool]]] = {}
    by_stem: dict[str, list[Path]] = {}
    labels: dict[str, str] = {}
    try:
        entries = sorted(d.iterdir(), key=lambda p: p.name.casefold())
    except OSError:
        return {}, {}, {}
    for path in entries:
        if path.suffix.lower() not in _USER_FONT_EXTS:
            continue
        by_stem.setdefault(_norm(path.stem), []).append(path)
        norm_family, display, sub = _font_family_info(path)
        if not norm_family:
            continue
        norm = _norm(norm_family)
        by_family.setdefault(norm, []).append((path, bool(_BOLD_SUB_RE.search(sub))))
        if display and norm not in labels:
            labels[norm] = display
    return by_family, by_stem, labels


_USER_FONT_CACHE: tuple | None = None


def _custom_font_records():
    """带 mtime 缓存地返回 _scan_custom_fonts() 结果。"""
    global _USER_FONT_CACHE
    d = user_fonts_dir()
    try:
        mtime = d.stat().st_mtime_ns if d.is_dir() else -1
    except OSError:
        mtime = -1
    if _USER_FONT_CACHE is None or _USER_FONT_CACHE[0] != (d, mtime):
        _USER_FONT_CACHE = ((d, mtime), _scan_custom_fonts())
    return _USER_FONT_CACHE[1]


def _custom_font_candidates(name: str, bold: bool) -> list[Path]:
    """按族名/文件名在用户字体目录中解析路径；bold 时优先粗体字重。"""
    by_family, by_stem, _labels = _custom_font_records()
    norm = _norm(name)
    collected: list[Path] = list(by_stem.get(norm, []))
    # 文件名写法带扩展名（如 MyFont.ttf）时也命中对应的 stem 键。
    stem = re.sub(r"\.(?:ttf|otf|ttc|otc)$", "", name, flags=re.IGNORECASE)
    if stem != name:
        collected += by_stem.get(_norm(stem), [])
    fam = by_family.get(norm)
    if fam:
        if bold:
            bold_set = [p for p, is_bold in fam if is_bold]
            collected += bold_set or [p for p, _b in fam]
        else:
            collected += [p for p, _b in fam]
    result: list[Path] = []
    seen: set[str] = set()
    for path in collected:
        key = os.path.normcase(os.path.abspath(str(path)))
        if key in seen:
            continue
        seen.add(key)
        if path.is_file():
            result.append(path)
    return result


_SYSTEM_REGISTRY_CACHE: dict[str, list[Path]] | None = None


def _system_font_registry() -> dict[str, list[Path]]:
    """当前平台系统字体 族名→路径 映射，进程内只扫描一次。

    按文本选字体后每条标注都会查系统字体，逐条重扫会让大图渲染
    慢十几秒。Windows 走注册表，macOS 走字体目录扫描。
    """
    global _SYSTEM_REGISTRY_CACHE
    if _SYSTEM_REGISTRY_CACHE is None:
        if sys.platform == "darwin":
            _SYSTEM_REGISTRY_CACHE = _scan_macos_font_dirs()
        else:
            _SYSTEM_REGISTRY_CACHE = _scan_windows_font_registry()
    return _SYSTEM_REGISTRY_CACHE


def _system_font_dirs() -> list[Path]:
    """当前平台的系统字体目录。

    macOS 苹方等自带字体位于 /System/Library/Fonts；用户安装字体在
    /Library/Fonts 与 ~/Library/Fonts。Windows 兜底目录为 Fonts 目录
    （注册表已覆盖大多数情况，这里仅用于已知文件名与直接路径兜底）。
    """
    if sys.platform == "darwin":
        dirs = [
            Path("/System/Library/Fonts"),
            Path("/System/Library/Fonts/Supplemental"),
            Path("/Library/Fonts"),
        ]
        home_fonts = Path.home() / "Library" / "Fonts"
        if home_fonts not in dirs:
            dirs.append(home_fonts)
        return dirs
    return [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"]


def _scan_windows_font_registry() -> dict[str, list[Path]]:
    if sys.platform != "win32":
        return {}
    try:
        import winreg
    except ImportError:
        return {}

    result: dict[str, list[Path]] = {}
    keys = (
        (winreg.HKEY_CURRENT_USER,
         r"Software\Microsoft\Windows NT\CurrentVersion\Fonts"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
    )
    windows_dir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    for hive, subkey in keys:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                count = winreg.QueryInfoKey(key)[1]
                for index in range(count):
                    try:
                        display_name, raw_path, _kind = winreg.EnumValue(key, index)
                    except OSError:
                        continue
                    raw_path = str(raw_path or "").strip().strip('"')
                    if not raw_path:
                        continue
                    path = Path(raw_path)
                    if not path.is_absolute():
                        path = windows_dir / "Fonts" / path
                    if not path.is_file():
                        continue
                    family = _DISPLAY_SUFFIX_RE.sub("", str(display_name)).strip()
                    for name in (family, path.stem):
                        key_name = _norm(name)
                        if key_name:
                            result.setdefault(key_name, []).append(path)
        except OSError:
            continue
    return result


def _macos_font_family_names(path: Path) -> list[str]:
    """解析字体文件全部字面族名（含 TTC 集合的多个字面）。

    苹方 PingFang.ttc 是一个集合，含 SC/TC/HK 等字面，需逐个读取；
    缺 fontTools 或解析失败时返回空列表，仅退化为已知文件名兜底。
    """
    try:
        from fontTools.ttLib import TTCollection, TTFont
    except ImportError:
        return []
    try:
        if path.suffix.lower() in (".ttc", ".otc"):
            fonts = list(TTCollection(str(path), lazy=True).fonts)
        else:
            fonts = [TTFont(str(path), lazy=True)]
    except Exception:  # noqa: BLE001 - 单字体解析失败不影响其余字体
        return []
    names: list[str] = []
    for tf in fonts:
        try:
            name = tf["name"]
            fam = name.getDebugName(16) or name.getDebugName(1)
            if fam:
                names.append(fam)
        except Exception:  # noqa: BLE001
            continue
        finally:
            try:
                tf.close()
            except Exception:
                pass
    return names


def _scan_macos_font_dirs() -> dict[str, list[Path]]:
    """扫描 macOS 字体目录：返回 族名→文件路径 映射。

    仅在 darwin 上执行；非 macOS 直接返回空，保持跨平台行为一致。
    """
    if sys.platform != "darwin":
        return {}
    result: dict[str, list[Path]] = {}
    for d in _system_font_dirs():
        try:
            entries = sorted(d.iterdir(), key=lambda p: p.name.casefold())
        except OSError:
            continue
        for path in entries:
            if path.suffix.lower() not in _USER_FONT_EXTS:
                continue
            for fam in _macos_font_family_names(path):
                key_name = _norm(fam)
                if key_name:
                    result.setdefault(key_name, []).append(path)
    return result


def available_font_options(current: str | None = None) -> list[tuple[str, str]]:
    """返回编辑器字体下拉选项，保留当前主题中的未知字体。"""
    options = list(COMMON_FONT_OPTIONS)
    seen = {_norm(value) for value, _label in options}
    for family in sorted(_system_font_registry(), key=str.casefold):
        if family in seen or not family:
            continue
        # 系统字体中可能只有文件 stem；它仍然是可被解析的合法选择。
        options.append((family, family))
        seen.add(family)

    # 用户放入 custom_fonts/ 的字体也出现在下拉中，可直接选用。
    _by_family, _by_stem, labels = _custom_font_records()
    for norm_fam, display in sorted(labels.items(), key=lambda kv: kv[1].casefold()):
        if norm_fam in seen or not display:
            continue
        options.append((display, display))
        seen.add(norm_fam)

    current = str(current or "").strip()
    if current and _norm(current) not in seen:
        options.insert(0, (current, f"当前字体 ({current})"))
    return options


def _candidate_paths(font_name: str, bold: bool = False) -> list[Path]:
    name = str(font_name or "").strip()
    if not name:
        return []
    paths: list[Path] = []
    # 用户字体（custom_fonts/）优先于系统字体：放入即可覆盖，跨机器一致。
    # _custom_font_candidates 同时支持族名与文件名两种写法（如 MyFont.ttf）。
    paths.extend(_custom_font_candidates(name, bold))
    paths.append(Path(name))
    system_dirs = _system_font_dirs()
    registry = _system_font_registry()
    paths.extend(registry.get(_norm(name), []))
    for filename in _KNOWN_FILE_NAMES.get(_norm(name), ()):
        for d in system_dirs:
            paths.append(d / filename)
    # 允许主题直接保存字体文件名，例如旧的自定义主题配置。
    for d in system_dirs:
        paths.append(d / name)

    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = os.path.normcase(os.path.abspath(str(path)))
        if key in seen:
            continue
        seen.add(key)
        if path.is_file():
            result.append(path)
    return result


# ---------------------------------------------------------------- 字符覆盖

_COVERAGE_CACHE: dict[str, frozenset | None] = {}


def _font_coverage(path: Path):
    """解析字体文件覆盖的码位集合（每文件只解析一次）。

    无法解析（缺 fontTools / 坏文件）返回 None，调用方按“全覆盖”处理，
    保持无 fontTools 环境的既有行为。
    """
    key = os.path.normcase(os.path.abspath(str(path)))
    if key in _COVERAGE_CACHE:
        return _COVERAGE_CACHE[key]
    cps = None
    try:
        from fontTools.ttLib import TTFont
        tf = TTFont(str(path), lazy=True)
        try:
            cps = frozenset(tf.getBestCmap())
        finally:
            tf.close()
    except Exception:  # noqa: BLE001 - 字体解析失败不能阻断渲染
        cps = None
    _COVERAGE_CACHE[key] = cps
    return cps


def font_covers(path: Path | str | None, text: str | None) -> bool:
    """字体文件是否覆盖文本全部字符（空文本/不可解析视为覆盖）。"""
    if not text or path is None:
        return True
    cps = _font_coverage(Path(path))
    if cps is None:
        return True
    return all(ord(ch) in cps for ch in text)


def resolve_text_font(font_name: str | None = None, fallback_names=(),
                      bold: bool = False, text: str | None = None):
    """按字体族链解析字体文件；给定 text 时优先选能覆盖全部字符的。

    遍历完仍无覆盖字体时退回第一个可解析路径（宁可用原行为也不丢字）。
    """
    names = [str(font_name or "").strip()]
    names.extend(str(n).strip() for n in fallback_names)
    names.extend(_CJK_BOLD_FALLBACKS if bold else _CJK_FALLBACKS)
    first = None
    for nm in names:
        if not nm:
            continue
        for path in _candidate_paths(nm, bold=bold):
            if first is None:
                first = path
            if text is None or font_covers(path, text):
                return path
    return first


def resolve_font_path(font_name: str | None, fallback_names=()) -> Path | None:
    """解析字体族/文件名，返回第一个可用字体文件。"""
    return resolve_text_font(font_name, fallback_names)


def load_pillow_font(size: float, font_name: str | None = None,
                     bold: bool = False,
                     text: str | None = None) -> ImageFont.FreeTypeFont:
    """Load a Pillow font with a CJK-capable system fallback chain.

    Preview and export code must not silently use Pillow's tiny bitmap default
    font for Chinese.  The final default-font fallback is retained only for
    non-Windows environments where no system font is available at all.

    text: 目标文本。给定后逐字体检查字符覆盖（如指定字体不含文本中的
    字符时自动回退到 CJK 字体链），避免缺字空白。
    """
    size_px = max(int(round(float(size))), 6)
    path = resolve_text_font(font_name, (), bold=bold, text=text)
    if path is not None:
        try:
            return ImageFont.truetype(str(path), size_px)
        except OSError:
            pass
    return ImageFont.load_default()


def font_family_label(value: str, options: list[tuple[str, str]] | None = None) -> str:
    """将 YAML 中的字体值转换为编辑器显示值。"""
    value = str(value or "").strip()
    for option_value, label in options or available_font_options(value):
        if _norm(option_value) == _norm(value):
            return label
    return value


def font_value_from_label(label: str, options: list[tuple[str, str]]) -> str:
    """将编辑器显示值还原为写入 YAML 的稳定字体名。"""
    for value, option_label in options:
        if option_label == label:
            return value
    return str(label or "").strip()
