"""通过 GNU gettext 目录提供应用界面文本。

修改对应语言的 PO 目录后，运行 tools/renderer/compile_locales.py 编译；JSON 目录仅供审查。新文本在译文获批前回退到中文目录。"""
from __future__ import annotations

import gettext
import json
import os
import locale
from ._paths import resource_dir

DEFAULT_LANG = "zh-CN"
FALLBACK_LANG = "en"
LANGUAGE_OPTIONS = tuple(json.loads((resource_dir("resources") / "i18n/languages.json").read_text(encoding="utf-8")))
AVAILABLE_LANGS = tuple(option["code"] for option in LANGUAGE_OPTIONS)
LANG = DEFAULT_LANG

_translations: dict[str, gettext.GNUTranslations] = {}
_keys_by_lang: dict[str, frozenset[str]] = {}


def _load() -> None:
    if _translations:
        return
    from ._paths import resource_dir

    directory = resource_dir("resources") / "i18n"
    manifest = json.loads((directory / "catalogs.json").read_text(encoding="utf-8"))
    for language in AVAILABLE_LANGS:
        with (directory / language / "LC_MESSAGES/cslmodernmap.mo").open("rb") as stream:
            _translations[language] = gettext.GNUTranslations(stream)
        _keys_by_lang[language] = frozenset(manifest[language])
    for language in AVAILABLE_LANGS:
        if language != DEFAULT_LANG:
            _translations[language].add_fallback(_translations[DEFAULT_LANG])


def normalize_lang(language: str | None) -> str | None:
    if not language:
        return None
    tag = language.replace("_", "-").split(".")[0].lower()
    if tag.startswith("zh"):
        return "zh-TW" if any(part in tag.split("-") for part in ("tw", "hk", "mo", "hant")) else "zh-CN"
    for option in LANGUAGE_OPTIONS:
        if tag == option["code"].lower() or tag.split("-")[0] == option["code"].lower().split("-")[0]:
            return option["code"]
    return None


def set_lang(lang: str) -> None:
    global LANG
    LANG = normalize_lang(lang) or DEFAULT_LANG


def tr_for(lang: str, key: str, **fmt) -> str:
    _load()
    text = _translations.get(normalize_lang(lang), _translations[FALLBACK_LANG]).gettext(key)
    return text.format(**fmt) if fmt else text


def tr(key: str, **fmt) -> str:
    return tr_for(LANG, key, **fmt)


_ = tr


def tr_or(key: str, fallback: str, **fmt) -> str:
    text = tr(key, **fmt)
    return fallback if text == key else text


def catalog_keys() -> frozenset[str]:
    _load()
    return frozenset().union(*_keys_by_lang.values())


def missing_keys(lang: str) -> list[str]:
    _load()
    return sorted(catalog_keys() - _keys_by_lang.get(lang, frozenset()))


def has_translation(key: str, lang: str | None = None) -> bool:
    _load()
    return key in _keys_by_lang.get(lang or LANG, frozenset())


def _system_locale_lang() -> str | None:
    """将操作系统界面语言映射到受支持的语言目录。"""
    try:
        import ctypes

        lang_id = int(ctypes.windll.kernel32.GetUserDefaultUILanguage())
        return normalize_lang(locale.windows_locale.get(lang_id))
    except (AttributeError, OSError):
        pass
    return normalize_lang(os.environ.get("LANGUAGE", "").split(":")[0] or locale.getlocale()[0])


def detect_lang(cli_lang: str | None = None, settings_dir=None) -> str:
    """CLI choice > application preference > game environment > legacy choice > system.

    ``settings_dir`` 供 GUIR 的按存档目录设置文件使用；省略时保持原有
    writable-dir 行为。

    调用方（main.py / guir.app）在启动时执行 set_lang(detect_lang(...))。
    """
    if cli_lang:
        return normalize_lang(cli_lang) or DEFAULT_LANG
    from ._paths import application_settings_dir
    from .settings_store import load_settings

    language = load_settings(application_settings_dir())["values"].get("language")
    if language in AVAILABLE_LANGS:
        return language
    env = os.environ.get("CSLMM_LANG")
    if normalize_lang(env):
        return normalize_lang(env)
    legacy_language = load_settings(settings_dir)["values"].get("language")
    if legacy_language in AVAILABLE_LANGS:
        return legacy_language
    sys_lang = _system_locale_lang()
    if sys_lang:
        return sys_lang
    return DEFAULT_LANG
