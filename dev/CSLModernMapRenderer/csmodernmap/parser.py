"""CSLMap XML 解析器。

输入：CSLMapView 导出的 .mmap（XML 格式），支持 .gz 压缩。
输出：MapData 数据模型。

XML 结构（版本 4.1）：
    CSLExportXML
    ├── City / Generated / SeaLevel          # 元数据
    ├── Nodes / Node(id, elev, ug, og, srv, subsrv, dist)
    │       └── Pos(x, y, z)
    ├── Segments / Seg(id, sn, en, icls, width)
    │       ├── Name
    │       └── Points / P(x, y, z)
    ├── Terrains / Ter                        # text 为 key:value 编码
    ├── Buildings / Buil(id, name, srv, subsrv, icls)
    │       └── Points / P(x, y, z)
    ├── Districts / Dist(id, name) / P(x, y, z)
    ├── Parks / Park(id, name) / P(x, y, z) + type
    ├── Transports / Trans(id, name, type)
    │       ├── color(a, r, g, b)
    │       └── Stops / Stop(node)
    ├── Forests / Forest                       # text 为数字编码
    └── SegmentTypes / ST(name, highway)       # 道路类别定义
"""
from __future__ import annotations

import gzip
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

from lxml import etree as LET

# 地形文本节点可超过默认大小限制。
_LXML_PARSER = LET.XMLParser(huge_tree=True)

from .data import (
    Building,
    District,
    Forest,
    MapData,
    Node,
    Park,
    Point,
    ProceduralObject,
    Segment,
    SegmentType,
    Terrain,
    Transport,
    TransportStop,
)


def _load_element_tree(path: str) -> ET.Element:
    """读取 .mmap 或 .mmap.gz 文件，返回根元素。

    使用必需依赖 lxml，保留大文本节点与 gzip 支持。
    """
    p = Path(path)
    if p.suffix.lower() == ".gz":
        with gzip.open(p, "rb") as f:
            return LET.parse(f, parser=_LXML_PARSER).getroot()
    return LET.parse(str(p), parser=_LXML_PARSER).getroot()


def _parse_pos(elem: ET.Element) -> Point:
    return Point(
        x=float(elem.get("x", 0)),
        y=float(elem.get("y", 0)),
        z=float(elem.get("z", 0)),
    )


def _parse_points(parent: ET.Element) -> list:
    """解析父元素下的所有坐标点（P 元素）。"""
    points = []
    for pe in parent.findall("P"):
        points.append(_parse_pos(pe))
    return points


def _parse_bool(val: str) -> bool:
    return val.strip().lower() == "true"


def parse_segment_types(root: ET.Element, mapdata: MapData) -> None:
    """解析道路类型定义（SegmentTypes/ST）。

    依据反编译 CSLMapView SegmentManager.Init：
    - highway = ST 的 highway 属性
    - road = 车道类型 Vehicle 且 vType 含 Car/Plane
    - beautification = Pedestrian 车道 或 vType 含 Bicycle
      且没有 road/highway 时才算（否则清 Beautification 位）
    - 含 Blimp/Ferry 的段不渲染（WayType.None）
    - width = max(最右 lane 右缘 - 最左 lane 左缘, 3)（游戏内米）
    """
    sts_elem = root.find("SegmentTypes")
    if sts_elem is None:
        return
    for st in sts_elem.findall("ST"):
        name = st.get("name", "")
        highway = st.get("highway", "false").strip().lower() == "true"
        road = False
        beautification = False
        rail = False
        tram = False
        metro = False
        monorail = False
        cablecar = False
        has_ferry = False
        car_lane_count = 0
        has_dir_f = False   # 存在 Forward 车辆车道（沿段方向 sn→en 单向）
        has_dir_b = False   # 存在 Backward 车辆车道（en→sn 单向）
        min_edge = float("inf")
        max_edge = float("-inf")
        for lane in st.findall("Lanes/Lane"):
            ltype = (lane.get("type") or "").strip()
            vtypes = [(v or "").strip() for v in (lane.get("vType") or "").split(",")]
            ldir = (lane.get("dir") or "").strip()
            try:
                pos = float(lane.get("pos", 0))
                width = float(lane.get("width", 0))
            except ValueError:
                continue
            if ltype == "Vehicle" and ("Car" in vtypes or "Train" in vtypes):
                if ldir == "Forward":
                    has_dir_f = True
                elif ldir == "Backward":
                    has_dir_b = True
            min_edge = min(min_edge, pos - width / 2)
            max_edge = max(max_edge, pos + width / 2)
            if ltype == "Vehicle":
                for vt in vtypes:
                    if vt in ("Car", "Plane"):
                        road = True
                        if vt == "Car":
                            car_lane_count += 1
                    elif vt == "Train":
                        rail = True
                    elif vt == "Tram":
                        tram = True
                    elif vt == "Metro":
                        metro = True
                    elif vt == "Monorail":
                        monorail = True
                    elif vt == "CableCar":
                        cablecar = True
                    elif vt == "Bicycle":
                        beautification = True
                    elif vt in ("Blimp", "Ferry"):
                        has_ferry = True
            elif ltype == "Pedestrian":
                beautification = True
        if has_ferry:
            continue  # 船/飞艇线不渲染
        if road or highway or rail or tram or metro or monorail or cablecar:
            beautification = False  # 有道路/高速/轨道时清除美化位
        # 车道方向位：仅存在单向 Forward → +1；仅存在单向 Backward → -1；其余 0
        lane_dir = 1 if (has_dir_f and not has_dir_b) else (-1 if (has_dir_b and not has_dir_f) else 0)
        seg_type = SegmentType(
            name=name,
            highway=highway,
            road=road,
            beautification=beautification,
            width=max(int(max_edge - min_edge), 3) if max_edge > min_edge else 0.0,
            car_lane_count=car_lane_count,
            lane_dir=lane_dir,
            rail=rail,
            tram=tram,
            metro=metro,
            monorail=monorail,
            cablecar=cablecar,
        )
        mapdata.segment_types[name] = seg_type


def _parse_zone_grid(text: str):
    """解析区划/功能区网格文本，返回 (by_id, cell)。

    格式：'cell=96;x,z,id;x,z,id;...'，每项为一个采样格点
    （世界坐标 x,z + 实体 id）。'cell=' 头的网格单元大小（米）
    返回给调用方；旧格式无头时 cell 为 0（渲染端回退推断）。
    """
    cell = 0.0
    if not text:
        return {}, cell
    parts = [p for p in text.split(";") if p]
    if not parts:
        return {}, cell
    if parts[0].startswith("cell="):
        try:
            cell = float(parts[0][5:])
        except ValueError:
            pass
        parts = parts[1:]
    by_id: dict[int, list] = {}
    for token in parts:
        seg = token.split(",")
        if len(seg) != 3:
            continue
        try:
            x, z, eid = float(seg[0]), float(seg[1]), int(seg[2])
        except ValueError:
            continue
        by_id.setdefault(eid, []).append((x, z))
    return by_id, cell


# 地形网格边长（反编译 CSLMapView 确认：TerrainBuilder.Size = 1081）
TERRAIN_SIZE = 1081


def parse_document(path: str, progress=None, *, validate: bool = True):
    """在应用边界直接将源 XML 解析为标准 CMM 文档，不经过旧版 MapData 往返转换。"""
    from .cmm.loader import load_document

    return load_document(path, progress=progress, validate=validate)
