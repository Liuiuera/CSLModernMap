"""公共交通独立单元（6.6.0）：类型注册表 / 线路几何 / 自动配色 / 站点合并。

把公共交通从 transport_layer.py 的"地铁专用"逻辑中独立成一个单元，
供常规渲染、公共交通视图、SVG/网页导出三处共用。

参考逆向项目 CSLMapView（反编译源码）：
- TransportInfo.cs       类型 → 渲染开关映射（Bus/Trolleybus→公交、Tram→电车、
  Metro→地铁、Train→铁路、Monorail→单轨、CableCar→缆车、Ship→渡轮、Airplane→飞艇）
- TransportManager.cs    线路沿「XX Line」停靠线段 Path 链重建实际行驶路线
- AutoTransportColoring.cs  自动配色（分组 + 共享线路冲突规避 + 色板）
- StationGrouping.cs     站点合并（48m 半径 + Node.SubService 合并规则）
- TransportMapBuilder.cs 线路/站点绘制规范（绘制本身在各图层层）

本单元保持纯逻辑（不依赖 PIL），输入 MapData + 配置，输出：
- line_geometry_world()：线路几何（世界坐标点列）
- merge_stations()：合并站点
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .data import MapData, Point, Transport, TransportStop
from .building_names import is_unresolved_building_name

# ---------------------------------------------------------------------------
# 类型注册表
# ---------------------------------------------------------------------------
# 轨道标志：data.build_track_graph(kinds) 用的轨道段标志；None = 无轨道图（示意型）。
# line_network：该类型停靠线段网络段名的关键词（Path 链重建匹配 seg.name，小写）。
# chain_preferred：轨道型线路优先用 Path 链（实际行驶路线，精确且天然闭合环线）；
#                 链缺失时回退轨道 BFS。
# schematic_fallback：允许回退"站点直连示意图"（Bus/Ferry 等无轨道图类型用）。
_TRACK_FLAGS = {
    "Metro": "metro",
    "Train": "rail",
    "Tram": "tram",
    "Monorail": "monorail",
    "CableCar": "cablecar",
}

# 类型别名归一化：Mod 导出的 TransportType 经此表映射到注册表键
_TYPE_ALIASES = {key.casefold(): value for key, value in {
    "Bus": "Bus",
    "Trolleybus": "Trolleybus",
    "Tram": "Tram",
    "Metro": "Metro",
    "Train": "Train",
    "Monorail": "Monorail",
    "CableCar": "CableCar",
    "Ship": "Ship",          # 游戏内渡船（Mass Transit）
    "Ferry": "Ferry",        # Mod 补充渡船类型
    "Airplane": "Airplane",  # 飞艇（参考项目 Airplane → Blimp 线）
    "Plane": "Airplane",
    "Blimp": "Airplane",
    "Helicopter": "Helicopter",
}.items()}

# 站点合并用的 Node.SubService 关键词（按类型）
_STATION_SUBSRV = {
    "Bus": ("PublicTransportBus", "PublicTransportTrolleybus"),
    "Trolleybus": ("PublicTransportBus", "PublicTransportTrolleybus"),
    "Tram": ("PublicTransportTram",),
    "Metro": ("PublicTransportMetro",),
    "Train": ("PublicTransportTrain",),
    "Monorail": ("PublicTransportMonorail",),
    "CableCar": ("PublicTransportCableCar",),
    "Ship": ("PublicTransportShip",),
    "Ferry": ("PublicTransportShip",),
    "Airplane": ("PublicTransportPlane", "PublicTransportBlimp"),
    "Helicopter": ("PublicTransportHelicopter",),
}

_STATION_ICLS_KEYWORDS = (
    "metro", "train", "station", "monorail", "tram", "cablecar",
    # 机场 DLC 的一体化地铁站常把玩家名称保存在紧邻站台的航站楼
    # （Airplane Facility）上，而不是 Stop.name / Metro Facility 上。
    # station_building_name 仍要求建筑有实际显示名且位于 90m 内，
    # 因而不会把无名机坪或远处机场设施误当作普通站名。
    "airplane facility",
)


def _named_station_buildings(mapdata):
    """按原建筑顺序返回有实际名称的车站候选及中心。"""
    is_cs1 = getattr(mapdata, "game", "").lower() == "cs1"
    for building in mapdata.buildings:
        name = (building.custom_name or "").strip()
        source = "custom" if name else ""
        if not name:
            display = getattr(building, "display_name", "")
            name = str(getattr(display, "text", display) or "").strip()
            source = str(getattr(display, "source", "") or
                         getattr(building, "name_source", "") or "game")
        if not name:
            continue
        if is_cs1 and is_unresolved_building_name(name, source):
            continue
        icls = (building.icls or "").lower()
        if not any(keyword in icls for keyword in _STATION_ICLS_KEYWORDS):
            continue
        if not building.points:
            continue
        cx = sum(point.x for point in building.points) / len(building.points)
        cz = sum(point.z for point in building.points) / len(building.points)
        yield cx, cz, name, source if source in {"custom", "game"} else "game"


def station_building_display_name(mapdata: MapData, node_id: int,
                                  max_distance: float = 90.0) -> tuple[str, str]:
    """返回邻近车站建筑的实际显示名及其来源。"""
    node = mapdata.node(node_id)
    if node is None:
        return "", "empty"
    best_name = ""
    best_source = "empty"
    best_distance = float(max_distance)
    for cx, cz, name, source in _named_station_buildings(mapdata):
        distance = math.hypot(cx - node.pos.x, cz - node.pos.z)
        if distance < best_distance:
            best_distance = distance
            best_name = name
            best_source = source
    return best_name, best_source


def station_building_name(mapdata: MapData, node_id: int,
                          max_distance: float = 90.0) -> str:
    """兼容旧调用：仅返回邻近车站建筑的实际显示文本。"""
    return station_building_display_name(mapdata, node_id, max_distance)[0]


@dataclass(frozen=True)
class TransitLineType:
    """交通线路类型定义（类型注册表条目）。"""

    key: str                     # 注册表键（XML type 归一化后）
    label_key: str               # i18n 翻译键（gui.transit.type.*）
    track_flag: Optional[str]    # 轨道标志（build_track_graph kinds）；None=示意型
    line_network: str            # 停靠线段网络段名关键词（Path 链重建）
    chain_preferred: bool        # 轨道型优先 Path 链（链缺失回退 BFS）
    schematic_fallback: bool     # 允许回退"站点直连示意图"（无轨道图类型必须 True）
    default_enabled: bool        # 公共交通视图默认是否启用
    airborne: bool = False       # 空中直飞型（不沿线段 Path 链，站点直连）
    station_subsrv: Tuple[str, ...] = ()
    style: dict = field(default_factory=dict)  # 类型级样式默认（YAML 可覆盖）


def _mk(key: str, track_flag=None, chain_preferred: bool = True,
        schematic_fallback: bool = False, airborne: bool = False,
        default_enabled: bool = False,
        style: Optional[dict] = None) -> TransitLineType:
    """构造注册表条目（默认值收敛，避免每条重复写）。"""
    return TransitLineType(
        key=key,
        label_key=f"gui.transit.type.{key}",
        track_flag=track_flag,
        line_network=(key + " Line").lower().replace("cablecar", "cable car"),
        chain_preferred=chain_preferred and track_flag is not None,
        schematic_fallback=schematic_fallback,
        airborne=airborne,
        default_enabled=default_enabled,
        station_subsrv=_STATION_SUBSRV.get(key, ()),
        style=style or {},
    )


TYPE_REGISTRY: Dict[str, TransitLineType] = {
    # 轨道型：Path 链优先，链缺失回退轨道 BFS；公共交通视图默认全部启用
    "Metro": _mk("Metro", "metro", default_enabled=True,
                 style={"width": 1.4, "dash": (), "color_hint": "line"}),
    "Train": _mk("Train", "rail", default_enabled=True,
                 style={"width": 1.4, "dash": (), "color_hint": "line"}),
    "Monorail": _mk("Monorail", "monorail", default_enabled=True,
                    style={"width": 1.2, "dash": (), "color_hint": "line"}),
    "Tram": _mk("Tram", "tram", default_enabled=False,
                style={"width": 1.0, "dash": (), "color_hint": "line"}),
    "CableCar": _mk("CableCar", "cablecar", default_enabled=False,
                    style={"width": 1.0, "dash": (), "color_hint": "line"}),
    # 示意型：无轨道图，站点直连（Catmull-Rom 平滑在图层层做）；
    # 若对应停靠线段 Path 链可解析则优先沿链（链缺失回退示意图）
    "Ship": _mk("Ship", None, schematic_fallback=True, default_enabled=True,
                style={"width": 1.1, "dash": (8, 6), "color_hint": "water"}),
    "Ferry": _mk("Ferry", None, schematic_fallback=True, default_enabled=True,
                 style={"width": 1.1, "dash": (8, 6), "color_hint": "water"}),
    "Bus": _mk("Bus", None, schematic_fallback=True, default_enabled=True,
               style={"width": 0.9, "dash": (), "color_hint": "line"}),
    "Trolleybus": _mk("Trolleybus", None, schematic_fallback=True,
                      default_enabled=False,
                      style={"width": 0.9, "dash": (4, 4), "color_hint": "line"}),
    "Airplane": _mk("Airplane", None, schematic_fallback=True,
                    airborne=True, default_enabled=False,
                    style={"width": 1.0, "dash": (10, 8), "color_hint": "water"}),
    "Helicopter": _mk("Helicopter", None, schematic_fallback=True,
                      airborne=True, default_enabled=False,
                      style={"width": 0.8, "dash": (6, 6), "color_hint": "line"}),
}


def normalize_type(raw: str) -> Optional[str]:
    """Mod 导出的 TransportType → 注册表键；未知类型返回 None。"""
    return _TYPE_ALIASES.get((raw or "").casefold())


def type_def(key: str) -> Optional[TransitLineType]:
    """注册表键（或原始类型名）→ TransitLineType；未知返回 None。"""
    return TYPE_REGISTRY.get(key) or TYPE_REGISTRY.get(normalize_type(key))


def resolve_enabled(types: List[str], *, view_all: bool = False) -> List[str]:
    """把样式/配置里的类型列表解析为最终启用集。

    view_all=True（公共交通视图）：只按 default_enabled 过滤（默认全开）。
    否则按传入列表原样归一化（未知类型丢弃、别名归一化）。
    """
    out: List[str] = []
    for raw in types:
        key = normalize_type(raw) or raw
        if key in TYPE_REGISTRY and key not in out:
            if view_all and not TYPE_REGISTRY[key].default_enabled:
                continue
            out.append(key)
    return out


# ---------------------------------------------------------------------------
# 线路几何：Path 链 → 轨道 BFS → 站点直连 三级回退
# ---------------------------------------------------------------------------
# Path 链重建逻辑与 data.metro_line_geometry 同源（v6.4.2 地铁验证），
# 此处泛化到任意类型：按 line_network 关键词匹配停靠线段，读其 Path 链。
# 链索引按调用构建；渲染期复用由 RenderContext.shared 负责。


def _line_chain_index(mapdata: MapData, keyword: str) -> dict:
    """停靠线段索引：start_node -> [(seg_id, end_node, 去重后链), ...]。"""
    idx = {}
    for seg in mapdata.segments:
        if keyword not in seg.name.lower():
            continue
        ids = seg.path_seg_ids
        if not ids:
            continue
        dedup: List[int] = []
        for x in ids:
            if not dedup or dedup[-1] != x:
                dedup.append(x)
        idx.setdefault(seg.start_node_id, []).append(
            (seg.id, seg.end_node_id, dedup))
    return idx


def _same_xy(a: Point, b: Point) -> bool:
    return abs(a.x - b.x) < 1e-3 and abs(a.z - b.z) < 1e-3


def line_chain_segment_ids(mapdata: MapData, stop_node_ids,
                           keyword: str) -> List[int]:
    """线路 → 沿「keyword Line」停靠线段 Path 链的有序实体线段 ID。

    与 data.metro_line_geometry 同源（站点→停靠线段→链 = 实际行驶路线）：
    逐对站点取起点/终点匹配的停靠线段，读其 Path 链拼接轨道段形状点；
    环线（内环/外环）靠闭环遍历自动闭合；链缺失（数据异常）返回 []，
    由调用方决定回退 BFS 或示意图。
    """
    stops = [s for s in stop_node_ids if mapdata.node(s) is not None]
    if len(stops) < 2:
        return []
    if stops[-1] == stops[0]:
        stops = stops[:-1]
        if len(stops) < 2:
            return []
    idx = _line_chain_index(mapdata, keyword)
    chains: List[List[int]] = []
    closed = False
    n = len(stops)
    for i in range(n):
        a, b = stops[i], stops[(i + 1) % n]
        if i == n - 1:
            has_fwd = any(c[1] == b for c in idx.get(a, ()))
            has_bwd = any(c[1] == a for c in idx.get(b, ()))
            if not has_fwd and not has_bwd:
                break
            closed = True
        chain = None
        cand = [c for c in idx.get(a, ()) if c[1] == b]
        if cand:
            chain = cand[0][2]
        else:
            cand = [c for c in idx.get(b, ()) if c[1] == a]
            if cand:
                chain = list(reversed(cand[0][2]))
        if chain is None:
            return []  # 链缺失 → 调用方回退
        chains.append(chain)
    full: List[int] = []
    for chain in chains:
        if not chain:
            continue
        k = 0
        while k < len(chain) and k < len(full) and chain[k] == full[-1 - k]:
            k += 1
        full.extend(chain[1:] if k == 1 else chain)
    if not full:
        return []
    if closed and len(full) > 1 and full[0] == full[-1]:
        full = full[:-1]
    return full


def line_chain_geometry(mapdata: MapData, stop_node_ids,
                        keyword: str) -> List[Point]:
    """线路 → 沿停靠线段 Path 链重建的世界坐标点列。"""
    stops = [s for s in stop_node_ids if mapdata.node(s) is not None]
    if len(stops) < 2:
        return []
    full = line_chain_segment_ids(mapdata, stops, keyword)
    if not full:
        return []
    return mapdata._chain_to_points(full, mapdata.node(stops[0]).pos)


def _schematic_points(mapdata: MapData, tr: Transport) -> List[Point]:
    """站点直连示意图：站点节点坐标点列（去重相邻重复）。"""
    pts: List[Point] = []
    for stop in tr.stops:
        n = mapdata.node(stop.node_id)
        if n is None:
            continue
        p = Point(n.pos.x, n.pos.y, n.pos.z)
        if pts and _same_xy(pts[-1], p):
            continue
        pts.append(p)
    return pts


def line_geometry_world(mapdata: MapData, tr: Transport,
                        td: Optional[TransitLineType] = None) -> List[Point]:
    """线路几何（世界坐标点列）：Path 链 → 轨道 BFS → 站点直连 三级回退。

    与 transport_layer 的关系：本函数只出世界坐标，像素变换/样式绘制在各图层层。
    """
    td = td or type_def(tr.type)
    if td is None:
        return []
    stops = [s.node_id for s in tr.stops]
    # 1) Path 链（实际行驶路线，优先）：轨道型 + 地面示意型（公交/电车/渡轮同样
    #    沿「XX Line」停靠线段 Path 链走实际道路/航线，参考 TransportManager 只
    #    排除空中直飞）；链缺失时按类型回退 BFS 或站点直连。
    if td.chain_preferred or (td.schematic_fallback and not td.airborne):
        pts = line_chain_geometry(mapdata, stops, td.line_network)
        if len(pts) >= 2:
            return pts
    # 2) 轨道 BFS 跟随（轨道型）
    if td.track_flag:
        graph = mapdata.build_track_graph((td.track_flag,))
        pts = mapdata.track_line_geometry(graph, stops)
        if len(pts) >= 2:
            return pts
    # 3) 站点直连示意图（示意型 / 链与轨道均失败）
    if td.schematic_fallback:
        return _schematic_points(mapdata, tr)
    return []


# ---------------------------------------------------------------------------
# 自动配色（参考 AutoTransportColoring.cs）
# ---------------------------------------------------------------------------
# 逆向项目用段级 ForwardTransports/BackwardTransports 判共享线路；本实现用
# 「站点集合重叠」近似（两条线路有共同站点即视为换乘共享、避免同色），
# 不依赖段级导出数据，效果接近。

# 默认色板（参考 DefaultStyleBuilder.RouteMapColors 的 7 色）
DEFAULT_PALETTE: List[Tuple[int, int, int]] = [
    (255, 0, 0),      # 红
    (0, 130, 0),      # 绿
    (0, 0, 255),      # 蓝
    (0, 170, 255),    # 青
    (230, 80, 0),     # 橙
    (100, 0, 190),    # 紫
    (160, 0, 100),    # 品红
]

# 自动配色分组（参考 AutoTransportColoring：公交/电车一组、地铁一组、其他一组）
def _is_user_color(color) -> bool:
    """XML color 是否携带用户自定义色：纯白 (255,255,255,255) 视为无用户色。"""
    if not color or len(color) < 3:
        return False
    return not (color[-3] == 255 and color[-2] == 255 and color[-1] == 255)


def line_display_color(tr, td=None):
    """线路展示色（RGB 三元组）：玩家用户色 → 自动配色；都没有返回 None。

    用于公共交通建筑/站点按线路色上色（transit 单元不读 YAML，类型默认色
    由各图层从样式取）。
    """
    td = td or type_def(tr.type)
    if _is_user_color(tr.color):
        return (int(tr.color[-3]), int(tr.color[-2]), int(tr.color[-1]))
    if tr.auto_color:
        return tuple(int(c) for c in tr.auto_color)
    return None


def building_line_colors(mapdata: MapData) -> Dict[int, Tuple[int, int, int]]:
    """公共交通建筑（PublicTransport* 服务）→ 线路色（建筑轮廓内站点的线路）。

    地铁站/火车站/码头等站房建筑按其覆盖的站点节点所属线路取色，
    供公共交通图把公共交通建筑从普通建筑中抽离出来按线路色单独上色。
    结果可按 mapdata 缓存（分块渲染每瓦片只算一次）。
    """
    # node_id → 线路色集合
    node_colors: Dict[int, set] = {}
    for tr in mapdata.transports:
        c = line_display_color(tr)
        if c is None:
            continue
        for s in tr.stops:
            node_colors.setdefault(s.node_id, set()).add(c)

    out: Dict[int, Tuple[int, int, int]] = {}
    for b in mapdata.buildings:
        if not b.sub_service.lower().startswith("publictransport"):
            continue
        if len(b.points) < 3:
            continue
        xs = [p.x for p in b.points]
        zs = [p.z for p in b.points]
        colors: set = set()
        for nid, cs in node_colors.items():
            n = mapdata.node(nid)
            if n is None:
                continue
            if (min(xs) <= n.pos.x <= max(xs)
                    and min(zs) <= n.pos.z <= max(zs)):
                colors.update(cs)
        if colors:
            # 换乘站取注册表顺序靠前的线路色（sorted 稳定）
            out[b.id] = sorted(colors)[0]
    return out


# ---------------------------------------------------------------------------
# 站点合并（参考 StationGrouping.cs）
# ---------------------------------------------------------------------------

@dataclass
class MergedStation:
    """合并后的站点（同位置/近邻的不同线路站点合并为一个）。

    参考 StationGrouping.MergeStations：merge_range 半径内且子服务允许
    合并（Bus/Tram 可互并、Train/Metro 可互并、其余同子服务才并）的
    TransportStop 归为一组，供图层按"一个站点"绘制（白底圆点/图标）。
    lines：途经本站的线路列表（去重）——换乘判定按线路数而非站点数
    （同一线路相邻近站合并不算换乘）。
    """

    id: int
    stops: List[TransportStop] = field(default_factory=list)
    position: Optional[Point] = None   # 平均位置（世界坐标）
    subsrv: str = ""                   # 主导子服务（用于图标/标注分类）
    lines: List[Transport] = field(default_factory=list)


def _is_merge_target(s1: str, s2: str, merge_bus_tram: bool,
                     merge_train_metro: bool) -> bool:
    """IsMergeTarget：子服务合并规则（参考 StationGrouping）。"""
    if merge_bus_tram:
        bus = ("PublicTransportBus", "PublicTransportTrolleybus",
               "PublicTransportTram")
        if s1 in bus and s2 in bus:
            return True
    if merge_train_metro:
        rail = ("PublicTransportTrain", "PublicTransportMetro")
        if s1 in rail and s2 in rail:
            return True
    return s1 == s2 and bool(s1)


def merge_stations(mapdata: MapData, transports: List[Transport],
                   merge_range: float = 48.0,
                   merge_bus_tram: bool = True,
                   merge_train_metro: bool = False,
                   enabled: bool = True,
                   merge_overrides: Optional[Dict[str, dict]] = None) -> List[MergedStation]:
    """合并全部线路的站点（参考 StationGrouping.MergeStations）。

    返回 MergedStation 列表；每个站点归且只归一个合并组。
    merge_range：合并半径（米），逆向项目默认 48.0。
    enabled：关闭时保留每个原始站台为独立视觉站点。

    实现：空间哈希网格（格边 = merge_range）O(N) 近邻收集，替代旧的
    全池线性扫描（O(N²)，大存档数千站点时公共交通视图卡顿）。
    合并语义与旧版一致：种子广度扩张（传递闭包），A~B、B~C 同组。
    """
    import math as _math

    def category_for(subsrv: str) -> str:
        text = (subsrv or "").lower()
        if "metro" in text:
            return "metro"
        if "train" in text:
            return "train"
        if "ship" in text:
            return "ship"
        if "tram" in text or "bus" in text or "trolleybus" in text:
            return "bus"
        return text

    overrides = merge_overrides if isinstance(merge_overrides, dict) else {}

    def rule_for(subsrv: str) -> Tuple[bool, float]:
        raw = overrides.get(category_for(subsrv))
        if not isinstance(raw, dict):
            return bool(enabled), float(merge_range)
        try:
            distance = float(raw.get("distance", merge_range))
        except (TypeError, ValueError):
            distance = float(merge_range)
        return bool(raw.get("enabled", enabled)), max(distance, 1e-6)

    ranges = [rule_for(n.subsrv or "")[1] for tr in transports for s in tr.stops
              if (n := mapdata.node(s.node_id)) is not None]
    cell = max([float(merge_range), *ranges, 1e-6])
    # 条目: (stop, transport, subsrv, x, z)
    entries: list = []
    grid: Dict[Tuple[int, int], List[int]] = {}
    for tr in transports:
        for s in tr.stops:
            n = mapdata.node(s.node_id)
            if n is None:
                continue
            idx = len(entries)
            entries.append((s, tr, n.subsrv or "", n.pos.x, n.pos.z))
            key = (int(_math.floor(n.pos.x / cell)),
                   int(_math.floor(n.pos.z / cell)))
            grid.setdefault(key, []).append(idx)
    if not entries:
        return []

    used = [False] * len(entries)
    merged: List[MergedStation] = []
    for seed_i in range(len(entries)):
        if used[seed_i]:
            continue
        used[seed_i] = True
        group = [seed_i]
        stack = [seed_i]
        while enabled and stack:
            me_i = stack.pop()
            me = entries[me_i]
            mx, mz = me[3], me[4]
            cgx = int(_math.floor(mx / cell))
            cgz = int(_math.floor(mz / cell))
            for gx in (cgx - 1, cgx, cgx + 1):
                for gz in (cgz - 1, cgz, cgz + 1):
                    for j in grid.get((gx, gz), ()):
                        if used[j]:
                            continue
                        o = entries[j]
                        allow_me, range_me = rule_for(me[2])
                        allow_o, range_o = rule_for(o[2])
                        if not (allow_me and allow_o):
                            continue
                        if not _is_merge_target(
                                me[2], o[2], merge_bus_tram, merge_train_metro):
                            continue
                        # 两端均有覆盖时取较小半径，避免一类站点扩大范围后
                        # 把另一类仍继承父设置的站点意外拉入同一组。
                        r2 = min(range_me, range_o) ** 2
                        if (o[3] - mx) ** 2 + (o[4] - mz) ** 2 <= r2:
                            used[j] = True
                            group.append(j)
                            stack.append(j)
        nodes = [mapdata.node(entries[i][0].node_id) for i in group]
        nodes = [n for n in nodes if n is not None]
        pos = None
        if nodes:
            pos = Point(
                sum(n.pos.x for n in nodes) / len(nodes),
                sum(n.pos.y for n in nodes) / len(nodes),
                sum(n.pos.z for n in nodes) / len(nodes),
            )
        subsrv = ""
        for i in group:
            sv = entries[i][2]
            if sv:
                subsrv = sv
                break
        # 途经线路（去重，保持顺序）
        lines: List[Transport] = []
        seen = set()
        for i in group:
            t = entries[i][1]
            if id(t) not in seen:
                seen.add(id(t))
                lines.append(t)
        merged.append(MergedStation(
            id=len(merged) + 1,
            stops=[entries[i][0] for i in group],
            position=pos, subsrv=subsrv, lines=lines))
    return merged
