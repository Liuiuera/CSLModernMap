# -*- coding: utf-8 -*-
"""设置持久化：settings.json 读写 + 版本迁移（W1，6.5.0 首次运行向导基础）。

- 文件位置：Windows 默认使用固定用户目录，调用方可显式指定目录。
- 文件结构：{"version": 3.1, "values": {设置项名: 值}}，values 为扁平 dict，
  图层勾选以 "layer_图层名" 键存储（与其它 tk 变量同名规则一致）。
- 保存策略：由 GUI 2.0 防抖调用；本模块只负责原子读写与校验，不关心触发时机。
- 加载策略：文件缺失 / 损坏 → 空 values（走首次运行默认 + 配置档推荐）。
- 版本迁移：v1 → v2 丢弃 language（v1 每次保存都写它，不代表用户选择）。
  v2 → v3 默认开启准确交叉关系，之后仍保留用户选择。
"""

import json
import math
import os
from pathlib import Path

from csmodernmap._paths import user_data_dir

SETTINGS_FILE = "settings.json"
SETTINGS_VERSION = 3.1


class SettingsVersionError(ValueError):
    """不支持的配置版本不能被当前程序重写。"""


def settings_version(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value not in (1, 2, 3, SETTINGS_VERSION):
        raise SettingsVersionError(f"Unsupported settings version: {value!r}")
    return value


def settings_path(directory=None):
    """settings.json 完整路径。"""
    base = user_data_dir() if directory is None else directory
    return Path(base).resolve() / SETTINGS_FILE


def _migrate(version: int | float, values: dict) -> dict:
    """按保存版本迁移 values；语言不在此写默认——language 仅在用户显式选择/保存时落盘，
    缺失即「未选择」，由 i18n.detect_lang 走系统区域检测，避免英文系统被默认中文覆盖。"""
    # v1（6.6.0 / 8.27 发布版）**每次保存**都把当前界面语言写进 language，
    # 因此文件里躺着 zh-CN 并不代表"用户选过中文"。留着会让 detect_lang 的
    # 第二道优先级永远命中，系统区域检测形同不存在：英文系统用户升级后界面
    # 仍是中文，而首次向导已删，界面上再没有能改语言的地方。只能整条丢弃。
    if version < 2:
        values.pop("language", None)
    if version < 3:
        values["accurate_intersections"] = True
    # 渲染器不再提供“新功能一览”，清理旧版本留下的提示状态。
    values.pop("features_last_seen_version", None)
    values.pop("features_notice_disabled", None)
    return values


def load_settings(directory=None) -> dict:
    """读取设置。

    返回配置版本和 values 字典；文件缺失/损坏/字段异常时返回空 values，
    由调用方按首次运行默认值处理。
    """
    data = {"version": SETTINGS_VERSION, "values": {}}
    try:
        p = settings_path(directory)
        if not p.exists():
            return data
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return data
        data["version"] = settings_version(raw.get("version", 3))
        vals = raw.get("values")
        data["values"] = vals if isinstance(vals, dict) else {}
        data["values"] = _migrate(data["version"], data["values"])
    except SettingsVersionError:
        raise
    except (OSError, ValueError):
        pass
    return data


def settings_bytes(values: dict, version: int | float) -> bytes:
    return json.dumps({"version": settings_version(version), "values": values},
                      ensure_ascii=False, indent=2).encode("utf-8")


def save_settings(values: dict, directory=None, version=None) -> bool:
    """原子写 settings.json（先写临时文件再 os.replace），失败返回 False。"""
    try:
        p = settings_path(directory)
        if version is None:
            version = settings_version(json.loads(p.read_text(encoding="utf-8")).get("version", 3)) if p.exists() else SETTINGS_VERSION
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_bytes(settings_bytes(values, version))
        os.replace(tmp, p)
        return True
    except OSError:
        return False


def clear_settings(directory=None) -> bool:
    """删除 settings.json（恢复默认设置用）。"""
    try:
        p = settings_path(directory)
        if p.exists():
            p.unlink()
        return True
    except OSError:
        return False
