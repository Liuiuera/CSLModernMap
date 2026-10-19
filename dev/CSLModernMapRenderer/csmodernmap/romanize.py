"""地图双语文字：仅把中文片段转换为拼音，其他字符原样保留。"""

from __future__ import annotations

import re
from .i18n import tr

_HAN_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")

# 地铁图站名中的通用词采用读者更容易识别的英文，而专有名称仍使用
# 连写的拼音。最长词优先，避免“火车站”被先拆成“火车”与“站”。
_COMMON_TERMS = tuple(sorted({
    "火车站": "RailwayStation",
    "高铁站": "HighSpeedRailStation",
    "地铁站": "MetroStation",
    "汽车站": "BusStation",
    "机场": "Airport",
    "车站": "Station",
    "东": "East",
    "西": "West",
    "南": "South",
    "北": "North",
    "中心": "Center",
    "大学": "University",
    "公园": "Park",
    "广场": "Square",
    "医院": "Hospital",
    "码头": "Harbor",
    "体育馆": "Stadium",
    "体育场": "Stadium",
    "会展中心": "ConventionCenter",
    "客运中心": "PassengerTerminal",
    "市民中心": "CivicCenter",
    "行政中心": "CivicCenter",
    "商业中心": "BusinessCenter",
    "科技园": "SciencePark",
    "工业园": "IndustrialPark",
    "开发区": "DevelopmentZone",
    "高新区": "HighTechZone",
    "博物馆": "Museum",
    "图书馆": "Library",
    "火车": "Railway",
    "铁路": "Railway",
    "高铁": "HighSpeedRail",
    "地铁": "Metro",
    "轻轨": "LightRail",
    "客运": "Passenger",
    "新区": "NewTown",
    "老城": "OldTown",
    "新城": "NewTown",
    "大桥": "Bridge",
    "大道": "Avenue",
    "路": "Road",
    "街": "Street",
    "站": "Station",
}.items(), key=lambda item: len(item[0]), reverse=True))


def clean_station_name(value: str) -> str:
    """剥离玩家站名末尾冗余的“站”字，保留名称中间的同字。"""
    text = str(value or "").strip()
    return text[:-1].rstrip() if len(text) > 1 and text.endswith("站") else text


def _romanize_han(text: str) -> str:
    """将一个纯中文片段变为 PascalCase 拼音与常用英文词。"""
    from pypinyin import Style, lazy_pinyin

    chunks: list[str] = []
    position = 0
    while position < len(text):
        for chinese, english in _COMMON_TERMS:
            if text.startswith(chinese, position):
                chunks.append(english)
                position += len(chinese)
                break
        else:
            syllable = lazy_pinyin(text[position], style=Style.NORMAL)[0]
            chunks.append(syllable[:1].upper() + syllable[1:])
            position += 1
    return "".join(chunks)


def chinese_pinyin(value: str) -> str | None:
    """将字符串内的中文转为无声调拼音；没有中文时返回 ``None``。"""
    text = str(value or "")
    if not _HAN_RUN.search(text):
        return None
    try:
        import pypinyin  # noqa: F401 - 用于检查可选运行时依赖
    except ImportError as exc:
        raise RuntimeError(tr("gui.error.pypinyin_missing")) from exc

    return _HAN_RUN.sub(
        lambda match: _romanize_han(match.group(0)),
        text)


def bilingual_text(value: str, language: str) -> str:
    """双语模式返回“中文\\n拼音”；没有中文的文本不会重复显示。"""
    text = str(value or "").strip()
    if language != "bilingual" or not text:
        return text
    pinyin = chinese_pinyin(text)
    return f"{text}\n{pinyin}" if pinyin and pinyin != text else text
