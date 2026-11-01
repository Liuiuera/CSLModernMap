"""地图文件边界的格式与版本检查，不受模型 validate 开关影响。"""
from __future__ import annotations

import re

from ..i18n import tr


class MapFileError(ValueError):
    def __init__(self, code: str, **values):
        self.code = code
        super().__init__(tr("gui.file." + code, **values))


def check_version(value, *, format: str, supported: str, pattern: str) -> None:
    if value is None or value == "":
        raise MapFileError("missing_version", format=format)
    if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
        raise MapFileError("invalid_version", format=format)
    if value == supported:
        return
    actual = tuple(int(part) for part in re.findall(r"\d+", value))
    current = tuple(int(part) for part in re.findall(r"\d+", supported))
    raise MapFileError("old_version" if actual < current else "unsupported_version",
                       format=format, version=value, supported=supported)


def check_cmm(raw) -> None:
    if not isinstance(raw, dict) or ("schema_version" not in raw and "metadata" not in raw):
        raise MapFileError("invalid_cmm")
    check_version(raw.get("schema_version"), format="CMM", supported="cmm-v1",
                  pattern=r"cmm-v[0-9]+")
    # 空城市允许空数组；配置文件或错误的顶层结构不能被默认值变成空地图。
    metadata = raw.get("metadata")
    if not isinstance(metadata, dict) or not isinstance(metadata.get("origin"), dict):
        raise MapFileError("invalid_cmm")
    for key in ("nodes", "networks", "buildings", "districts", "stops", "transit_lines",
                "routes", "forests", "procedural_objects", "issues"):
        if key in raw and (not isinstance(raw[key], list)
                           or any(not isinstance(item, dict) for item in raw[key])):
            raise MapFileError("invalid_cmm")
    for key in ("terrain", "derived", "extensions"):
        if key in raw and not isinstance(raw[key], dict):
            raise MapFileError("invalid_cmm")


def check_cslmap(root) -> str:
    if root.tag != "CSLExportXML" or not any(child.tag in {
            "City", "Nodes", "Segments", "Buildings", "Terrains", "Transports"
    } for child in root):
        raise MapFileError("invalid_xml")
    version = (root.get("version") or "").strip()
    # 历史 XML 允许省略版本；保留读取能力，但不再宣称它是 4.1。
    if not version:
        return "MISSING_XML_VERSION"
    check_version(version, format="CSLMap",
                  supported="4.1", pattern=r"[0-9]+\.[0-9]+")
    return "LEGACY_XML_FORMAT"


def file_warnings(document) -> list[str]:
    keys = {"LEGACY_XML_FORMAT": "legacy_xml", "MISSING_XML_VERSION": "unversioned_xml"}
    return [tr("gui.file." + keys[code]) for code in dict.fromkeys(
        issue.code for issue in document.issues if issue.code in keys)]
