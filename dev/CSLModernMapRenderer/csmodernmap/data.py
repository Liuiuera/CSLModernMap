"""数据模型：定义解析后统一使用的数据结构。

每个子功能（图层渲染）都依赖这里的数据模型，
模型与解析/渲染解耦，方便各模块独立开发。
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class Point:
    """三维坐标点（游戏坐标系：y 为高度）。"""

    x: float
    y: float
    z: float

    def as_2d(self) -> Tuple[float, float]:
        """转换为 2D 平面坐标 (x, z)，供渲染使用。"""
        return (self.x, self.z)


def _dist2_pt(a: Point, b: Point) -> float:
    """两点 2D 距离平方（x/z 平面）。"""
    return (a.x - b.x) ** 2 + (a.z - b.z) ** 2


def _same_xy(a: Point, b: Point) -> bool:
    """两点 2D 坐标是否重合（容差 1e-3 米）。"""
    return abs(a.x - b.x) < 1e-3 and abs(a.z - b.z) < 1e-3


@dataclass
class Node:
    """道路节点。"""

    id: int
    pos: Point
    elev: float = 0.0
    ug: bool = False          # 是否地下
    og: bool = False          # 是否位于地面（XML og = OnGround）
    srv: str = ""             # 服务类型
    subsrv: str = ""          # 子类型
    dist: int = 0


@dataclass
class Segment:
    """道路/线段。"""

    id: int
    start_node_id: int
    end_node_id: int
    icls: str = ""            # 道路类别标识（映射到渲染等级）
    width: float = 0.0
    name: str = ""            # 道路类型名（SegmentTypes/ST 名）
    custom_name: str = ""     # 玩家自定义道路名（用于道路名标注）
    points: List[Point] = field(default_factory=list)   # 形状点（含首尾）
    # 线路路径段序列（本段所属交通线路的完整路径，<Path>/<Segs>）。
    # 由游戏内 Mod 导出段 m_path 的 PathUnit 得来（对齐 CSLMapView）：
    # 沿路径方向依次列出该线路经过的全部轨道段 ID。地铁线路渲染沿此
    # 序列直接取形状点连线，可精确贴合真实轨道（取代 BFS 反推）。
    path_seg_ids: List[int] = field(default_factory=list)
    game_name: str = ""

    @property
    def is_underground(self) -> bool:
        return False


@dataclass
class Building:
    """建筑。"""

    id: int
    name: str = ""
    custom_name: str = ""         # 玩家自定义名（XML myname，改过名才有；地铁站等含此）
    service: str = ""             # 功能区：residential/commercial/industrial/office...
    sub_service: str = ""
    icls: str = ""
    points: List[Point] = field(default_factory=list)   # 轮廓
    # 子建筑关系（CSLMapView SubBuildingId/ParentBuildingId）
    sub_building_id: str = ""
    parent_building_id: str = ""
    display_name: str = ""       # 游戏实际显示文本（自定义名或自动游戏名）
    name_source: str = "empty"   # 自定义名称、游戏名称或空名称。
    area_id: int | None = None
    category_id: str = ""
    category_name: str = ""
    category_locale: str = ""
    growth_kind: str = "unknown"
    growth_use: str = ""


@dataclass
class District:
    """区划（行政区，游戏"区划"工具）。"""

    id: int
    name: str = ""
    dtype: str = ""                                          # 专精类型（游戏 District 策略位标志，如 IndustrialFarming/Ore/Oil/Forestry、CommercialHigh、OfficeHightech…；None=普通）
    points: List[Point] = field(default_factory=list)        # 边界（旧版仅名称定位点）
    grid_points: List[Tuple[float, float]] = field(default_factory=list)  # 区划网格采样点（世界坐标 (x,z)，Mod v6.5+ 的 <DistrictGrid> 导出）


@dataclass
class Park:
    """公园/功能区（游戏"公园区/产业区"工具，如森林公园/校园/机场/产业区）。"""

    id: int
    name: str = ""
    ptype: str = ""                                          # 公园类型（ParkType：Forest/CityPark/Zoo/AmusementPark/NatureReserve/Campus/Industry/Airport/University…）
    points: List[Point] = field(default_factory=list)        # 边界
    grid_points: List[Tuple[float, float]] = field(default_factory=list)  # 功能区网格采样点（世界坐标 (x,z)，Mod v6.5+ 的 <ParkGrid> 导出）
    owner_building_id: Optional[int] = None                  # 可选的设施根建筑（CS2 SubArea 归属）


@dataclass
class Forest:
    """森林（网格编码数据）。"""

    data: List[int] = field(default_factory=list)       # 原始网格编码


@dataclass
class TransportStop:
    """交通站点。"""

    node_id: int
    name: str = ""


@dataclass
class Transport:
    """交通线路（公交/地铁/火车）。"""

    id: int
    name: str = ""
    type: str = ""            # 公交、地铁、火车等交通类型。
    color: Tuple[int, int, int, int] = (255, 255, 255, 255)  # 颜色通道顺序为 RGBA。
    stops: List[TransportStop] = field(default_factory=list)
    # 自动配色结果（transit.py 分配；XML 无用户色/纯白时使用）。
    # 与 color 的关系：color 为玩家自定义色（有则优先），auto_color 为兜底。
    auto_color: Optional[Tuple[int, int, int]] = None


@dataclass
class ProceduralObject:
    """Procedural Object（魔物 MOD 自定义对象）。

    所有坐标为游戏世界坐标 (x, y, z)，y 为高度，x/z 平面供 2D 渲染使用。
    AABB（轴对齐包围盒）由 (center_x, center_z) + 半宽 (extent_x, extent_z) 表示，
    即四角 = (cx±ex, cz±ez)。若 rotation_y 非零（绝对值超过 epsilon），
    则 AABB 应绕中心旋转 rotation_y 弧度，成为 OBB（有向包围盒），渲染端按 OBB 处理。

    color_rgb 非 None 表示玩家主动染色；为 None 时走 prefab 分类色板（由渲染层决定）。
    """

    id: int
    pos: Point                          # 世界坐标（位置原点）
    center_x: float = 0.0               # AABB 中心 x（米，mesh.bounds.center.x + pos.x）
    center_z: float = 0.0               # AABB 中心 z（米）
    extent_x: float = 0.5               # AABB 半宽（x 方向，米；≥0.1）
    extent_z: float = 0.5               # AABB 半宽（z 方向，米；≥0.1）
    rotation_y: float = 0.0             # Y 轴旋转（弧度，0 时 AABB 轴对齐）
    scale: float = 1.0                  # 等比缩放倍数（m_scale，≥0.1）
    prefab_name: str = ""               # 来源资产名（basePrefabName）
    source_type: str = ""               # "PROP" 或 "BUILDING"
    color_rgb: Optional[Tuple[int, int, int]] = None  # 用户染色 RGB (0-255)；None=走分类色板


@dataclass
class SegmentType:
    """道路/轨道类型定义（对应 XML SegmentTypes/ST）。

    由车道数据推导（对应 CSLMapView SegmentManager.Init）：
    - highway: ST highway="true"
    - road: 车道含 Car 车辆
    - beautification: 纯步行/自行车道（Pedestrian/Bicycle）
    - rail/tram/metro/monorail/cablecar: 车道 vType 分别含
      Train/Tram/Metro/Monorail/CableCar（对应 WayType 轨道位）
    - width: 车道总宽度（游戏内米），= max(最右lane右缘 - 最左lane左缘, 3)
    """

    name: str = ""
    highway: bool = False
    road: bool = False
    beautification: bool = False
    width: float = 0.0
    # Vehicle + Car 车道数；道路层级以此为第一判据，旧数据缺失时为 0。
    car_lane_count: int = 0
    # 车道交通方向（相对段方向 sn→en）：1=单向 Forward，-1=单向 Backward，0=双向/未知。
    # 由 ST Lanes 的 dir 属性推导（匝道等单向段用）：高速出入口方向判定依赖此值，
    # 否则把"段方向=交通方向"当作假设（mod 匝道/游戏纠正方向时会判定反）。
    lane_dir: int = 0
    rail: bool = False          # 火车铁路（WayType.Rail）
    tram: bool = False          # 有轨电车（WayType.Tram）
    metro: bool = False         # 地铁轨道（WayType.Metro）
    monorail: bool = False      # 单轨（WayType.Monorail）
    cablecar: bool = False      # 缆车（WayType.CableCar）

    @property
    def is_rail(self) -> bool:
        """是否任意轨道类型。"""
        return self.rail or self.tram or self.metro or self.monorail or self.cablecar


@dataclass
class Terrain:
    """地形高度图。

    反编译 CSLMapView 确认的格式（ProcessTerrains）：
    - Terrains/Ter 文本为逗号分隔的有序对 "陆地高度:水域深度"，
      共 1081*1081 对，按顺序填入数组（行优先，y = i // 1081）。
    - 原始值 = int / 64（单位米）。
    """

    # 网格编码原始文本（"陆地:水域" 有序对，逗号分隔）
    raw: str = ""
    # 高度图 2D 数组（numpy ndarray，shape=(rows,cols)），行=世界 Z，列=世界 X。
    # 保持解析后的原始网格轴顺序；各渲染层按自身的像素坐标变换处理方向。
    heightmap: Optional[object] = None
    # 水域深度 2D 数组（行=世界 Z，列=世界 X，>0.2 视为水域）
    watermap: Optional[object] = None
    # 原始陆地高度数组（未翻转，行 0 = 世界 Z 最小），供坐标换算使用
    land_array: Optional[object] = None
    # 原始水域深度数组（未翻转）
    water_array: Optional[object] = None
    width: int = 0
    height: int = 0
    # 网格原点（网格 (0,0) 的世界坐标，米）与单元尺寸（米）。
    # 全图网格 origin=(-8640,-8640)、cell≈15.985；裁剪小存档由 Terrains
    # 节点属性指定（originX/originZ/cell/cols/rows），图层据此按世界坐标采样。
    origin_x: float = -8640.0
    origin_z: float = -8640.0
    cell: float = 17280.0 / 1081.0

    def elevation_at(self, x: int, y: int) -> float:
        """取某个网格的海拔高度（渲染网格坐标）。"""
        if self.heightmap is None:
            return 0.0
        return float(self.heightmap[y, x])


@dataclass
class MapData:
    """整个地图的解析结果。"""

    # 元数据
    city_name: str = ""
    generated: str = ""
    sea_level: float = 0.0

    # 数据
    terrain: Terrain = field(default_factory=Terrain)
    forests: List[Forest] = field(default_factory=list)
    nodes: List[Node] = field(default_factory=list)
    segments: List[Segment] = field(default_factory=list)
    buildings: List[Building] = field(default_factory=list)
    districts: List[District] = field(default_factory=list)
    parks: List[Park] = field(default_factory=list)
    transports: List[Transport] = field(default_factory=list)
    procedural_objects: List[ProceduralObject] = field(default_factory=list)  # 魔物 MOD PO 对象
    # 道路类型定义：{ST 名: SegmentType}（由 SegmentTypes/ST 解析）
    segment_types: dict = field(default_factory=dict)

    # 区划网格单元大小（米）。Mod v6.5+ 的 <DistrictGrid> 带 'cell=' 头，
    # 解析时存入；0 表示未知（旧存档），渲染端回退按格点间距推断。
    district_cell: float = 0.0
    # 功能区（公园区/产业区）网格单元大小（米），来源 <ParkGrid> 的 'cell=' 头。
    park_cell: float = 0.0

    # 内嵌交通快照（可选；csmodernmap 含有 <TrafficSnapshotJson> 节点时自动填充）
    # 类型为 Optional[TrafficSnapshot]，用前向引用字符串避免循环 import
    traffic: "object | None" = None

    # 辅助索引
    _node_by_id: dict = field(default_factory=dict)
    _segment_by_id: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._node_by_id = {node.id: node for node in self.nodes}
        self._segment_by_id = {segment.id: segment for segment in self.segments}

    def node(self, node_id: int) -> Optional[Node]:
        return self._node_by_id.get(node_id)

    def segment(self, seg_id: int) -> Optional[Segment]:
        return self._segment_by_id.get(seg_id)

    # ------------------------------------------------------------------
    # 轨道线路沿真实轨道寻路（v6.4：解决"线路偏离轨道"）
    # ------------------------------------------------------------------

    def build_track_graph(self, kinds=("metro",)) -> dict:
        """构建轨道邻接图：node_id -> [(邻接 node_id, seg_id), ...]。

        与 RailwayLayer 同源：只用 SegmentType 轨道标志（metro/rail/tram/
        monorail/cablecar）对应的轨道段——彩色线路与灰色轨道完全同一批数据。

        结果由调用方按渲染上下文复用；MapData 本身不保存动态渲染缓存。
        """
        return self._build_track_only_graph(tuple(kinds))

    def _build_track_only_graph(self, kinds) -> dict:
        """按轨道段（SegmentType 标志）建图。"""
        graph: dict = {}
        for seg in self.segments:
            st = self.segment_types.get(seg.name) or self.segment_types.get(seg.icls)
            if st is None:
                continue
            if not any(getattr(st, k, False) for k in kinds):
                continue
            a, b = seg.start_node_id, seg.end_node_id
            if a == b:
                continue
            graph.setdefault(a, []).append((b, seg.id))
            graph.setdefault(b, []).append((a, seg.id))
        return graph

    def nearest_track_node(self, graph: dict, node_id: int):
        """把站点节点投影到最近轨道节点（轨道图内距离最小的节点）。"""
        n = self.node(node_id)
        if n is None or not graph:
            return None
        best = None
        best_d = float("inf")
        for tn in graph:
            t = self.node(tn)
            if t is None:
                continue
            d = (t.pos.x - n.pos.x) ** 2 + (t.pos.z - n.pos.z) ** 2
            if d < best_d:
                best_d = d
                best = tn
        return best

    def build_metro_line_graph(self) -> dict:
        """地铁线路停靠线网络：node_id -> [(邻接 node_id, seg_id), ...]。

        等价于 build_stop_line_graph("metro line")；保留旧名供既有调用。
        """
        return self.build_stop_line_graph("metro line")

    def build_stop_line_graph(self, keyword: str) -> dict:
        """按停靠线段类型关键字构建停靠线网络：node_id -> [(邻接, seg_id), ...]。

        "Metro Line" 段是游戏地铁线路的停靠/显示线（站点所在网络，
        PublicTransport lane，vType=None 无车辆）。站点节点直接挂在此网络上，
        相邻站点之间必有同类型停靠线段直接连接（实测 100%）。
        不同类型（Metro/Train/Monorail…）停靠线网络相互独立，站序首尾
        直连边只能在自身类型网络内取证；keyword 按段名子串匹配（如
        "train line"），匹配不到时返回空图（表示无闭环证据）。
        注意：此网络与行驶轨道（Metro Track 等）平行偏移约 70m 且为直线段，
        仅用于线路走向骨架与站点顺序，不直接用于贴合渲染。
        """
        graph: dict = {}
        kw = keyword.lower()
        for seg in self.segments:
            if kw not in (seg.name or "").lower():
                continue
            a, b = seg.start_node_id, seg.end_node_id
            if a == b:
                continue
            graph.setdefault(a, []).append((b, seg.id))
            graph.setdefault(b, []).append((a, seg.id))
        return graph

    def track_components(self, graph: dict) -> List[set]:
        """轨道图连通分量划分，返回 [set(节点), ...]（按节点数降序）。"""
        parent: dict = {}

        def find(x):
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for nid, nbrs in graph.items():
            for nxt, _sid in nbrs:
                union(nid, nxt)
        comps: dict = {}
        for nid in graph:
            comps.setdefault(find(nid), set()).add(nid)
        return sorted(comps.values(), key=len, reverse=True)

    def _component_dist_sq(self, comp: set, pos: Point) -> float:
        """站点世界坐标到分量内最近轨道节点的距离平方。"""
        best = float("inf")
        for nid in comp:
            n = self.node(nid)
            if n is None:
                continue
            d = (n.pos.x - pos.x) ** 2 + (n.pos.z - pos.z) ** 2
            if d < best:
                best = d
        return best

    def metro_track_path(self, stop_node_ids) -> List[Point]:
        """地铁线路 → 沿真实轨道（Metro Track）的完整贴合路径。

        背景：站点节点挂在地铁"停靠线"（Metro Line 直线网络）上，与行驶
        轨道（Metro Track）平行偏移约 70m；换乘站/平行轨道处 naive 最近
        节点投影会选到别的线路的轨道（不同连通分量），导致 BFS 断裂。

        算法：
        1. 轨道图按连通分量划分（轨道网络闭合，同线路轨道必在同一分量）；
        2. 对每条线路选「所有站点到分量距离平方和最小」的分量作为线路
           轨道分量（消除换乘站的投影歧义）；
        3. 站点投影到该分量最近节点，逐对 BFS 取轨道段形状点 → 完整贴合。
        """
        if len(stop_node_ids) < 2:
            return []
        graph = self.build_track_graph(("metro",))
        comps = self.track_components(graph)
        if not comps:
            return []
        # 选最优分量：所有有效站点距离平方和最小
        nodes = [(sid, self.node(sid)) for sid in stop_node_ids]
        nodes = [(sid, n) for sid, n in nodes if n is not None]
        if len(nodes) < 2:
            return []
        best_comp = None
        best_score = float("inf")
        for comp in comps:
            score = sum(
                self._component_dist_sq(comp, n.pos) for _sid, n in nodes
            )
            if score < best_score:
                best_score = score
                best_comp = comp
        # 站点投影到最优分量 + 逐对 BFS 取段形状点
        entries = []
        for _sid, n in nodes:
            best_nid = None
            best_d = float("inf")
            for nid in best_comp:
                t = self.node(nid)
                if t is None:
                    continue
                d = (t.pos.x - n.pos.x) ** 2 + (t.pos.z - n.pos.z) ** 2
                if d < best_d:
                    best_d = d
                    best_nid = nid
            entries.append(best_nid)
        pts: List[Point] = []
        # 注意：首尾站点节点在"停靠线"网络上（离轨道约 70m），
        # 不能直接加入路径（会造成首尾 70m 跳跃断开）；只输出纯轨道点列。
        for a, b in zip(entries, entries[1:]):
            if a is None or b is None or a == b:
                continue
            r = self.track_route(graph, a, b)
            if r is None:
                # 同分量内必然连通；此处兜底直连（数据异常时保持可读）
                r = ([a, b], [])
            _nodes, segs = r
            for i, seg_id in enumerate(segs):
                seg = self.segment(seg_id)
                if seg is None or not seg.points:
                    for nd in (_nodes[i], _nodes[i + 1]):
                        nd2 = self.node(nd)
                        if nd2 is not None:
                            pts.append(Point(nd2.pos.x, nd2.pos.y, nd2.pos.z))
                    continue
                sp = seg.points
                sp2 = sp if seg.start_node_id == _nodes[i] else list(reversed(sp))
                pts.extend(sp2 if i == 0 else sp2[1:])
            if not segs:
                for nd in _nodes:
                    nd2 = self.node(nd)
                    if nd2 is not None:
                        pts.append(Point(nd2.pos.x, nd2.pos.y, nd2.pos.z))
        out: List[Point] = []
        for p in pts:
            if out and abs(p.x - out[-1].x) < 1e-3 and abs(p.z - out[-1].z) < 1e-3:
                continue
            out.append(p)
        return out

    # CSLMapView.TransportManager：停靠线的 Path/Segs 是按行驶方向排列的轨道链。
    # 链包含站台轨道及环线闭合段；缺少链时使用 metro_track_path。

    def _ensure_metro_line_index(self) -> dict:
        """惰性建立停靠线段索引：start_node -> [(seg_id, end_node, 去重后链), ...]。

        只对 "Metro Line" 段（name 含 "metro line"）且带 Path 链的建索引；
        链去掉相邻重复段 ID（链首重复同 CSLMapView Simplify2，此处去全部相邻重复）。
        """
        idx = {}
        for seg in self.segments:
            if "metro line" not in seg.name.lower():
                continue
            ids = seg.path_seg_ids
            if not ids:
                continue
            dedup: List[int] = []
            for x in ids:
                if not dedup or dedup[-1] != x:
                    dedup.append(x)
            idx.setdefault(seg.start_node_id, []).append(
                (seg.id, seg.end_node_id, dedup)
            )
        return idx

    def metro_line_geometry(self, stop_node_ids) -> List[Point]:
        """地铁线路 → 沿真实轨道完整贴合（站点停靠线段 Path 链重建）。

        逐对站点 (i, (i+1)%n) 取"起点=stop[i]、终点=stop[i+1]"的 Metro Line
        停靠线段，读其 Path 链拼接轨道段形状点；任一非闭合站段链缺失（数据
        异常）则整线回退 metro_track_path（旧 BFS 分量选择方案，行为不变）。
        环线（内环/外环）靠闭环遍历自动闭合；开环线最后一段无闭合链则自然断开。

        拼接要点（消除站台段重复描画）：
        - 所有站段链先拼成一条完整段链（相邻链共享的站台段只保留一次），
          再单遍定向取点——避免"每段链独立定向"造成的站台处 144m 回跳/重复；
        - 环线首尾共享同一站台段时去掉尾部重复，使首末点重合（环线闭合）。

        与 metro_track_path（分量选择 + BFS）相比：
        - 精确：链是实际行驶路线，非最短路径近似；
        - 完整：Metro Line 段 100% 携带链（实证），无需回退保底；
        - 闭合：闭环遍历处理"最后站→首站"闭合段（BFS 方案首尾缺口 284~2469m）。
        """
        stops = [s for s in stop_node_ids if self.node(s) is not None]
        if len(stops) < 2:
            return []
        # 伪环线：stops 列表首尾重复时去掉（湖州/泉都无此情况，防御性处理）
        if stops[-1] == stops[0]:
            stops = stops[:-1]
            if len(stops) < 2:
                return []
        idx = self._ensure_metro_line_index()
        chains: List[List[int]] = []
        closed = False
        n = len(stops)
        for i in range(n):
            a, b = stops[i], stops[(i + 1) % n]
            if i == n - 1:
                # 闭合段：仅当真实存在"最后站→首站"（或反向）停靠线段时才连，
                # 否则这是开环线，最后一段直接断开（避免画出跨图长线）。
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
                # 非闭合站段链缺失 = 数据异常 → 整线回退旧 BFS 方案
                return self.metro_track_path(stop_node_ids)
            chains.append(chain)
        # 拼接完整段链：去掉相邻链在站台处共享的重复段（只画一次）。
        # 规则：仅当共享数为 1（该站台段，线路直通穿过、只画一次）时移除；
        # 共享数 > 1 = 真实折返/回行（train 原路返回，如尽端站/长回行），
        # 必须整链保留（移除会扯断线路，产生数百米跳变）。
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
        # 环线：首尾共享同一站台段时去掉尾部重复 → 首末点重合
        if closed and len(full) > 1 and full[0] == full[-1]:
            full = full[:-1]
        pts = self._chain_to_points(full, self.node(stops[0]).pos)
        out: List[Point] = []
        for p in pts:
            if out and _same_xy(out[-1], p):
                continue
            out.append(p)
        return out

    def _chain_to_points(self, chain: List[int], start_pos: Point) -> List[Point]:
        """完整段链（轨道段 ID 有序列表）→ 世界坐标点列（单遍定向）。

        定向依据链的**节点序列**（相邻段共享的节点），而非逐段距离猜测：
        - 段 k 的"后节点" = 与段 k+1 共享的节点（链按行驶方向有序，相邻段
          必共享节点）→ 段朝该节点定向；末段按与上一段共享的节点定向；
        - 仅当首段与次段无共享节点（数据异常）时，才按"离起点站近的一端"
          兜底定向；链断裂处按距上一段末点近的一端兜底。
        这样避免站台两端等距时方向猜错（如 30906 平台 144m 两端距站点
        71.7/72.3m，proximity 会选错，导致链在站台处回跳）。
        """
        segs: List[Optional[Segment]] = [self.segment(sid) for sid in chain]
        pts: List[Point] = []
        prev_after_node: Optional[int] = None
        for k, seg in enumerate(segs):
            if seg is None:
                continue
            sp = list(seg.points)
            if not sp:
                na, nb = self.node(seg.start_node_id), self.node(seg.end_node_id)
                if na is None or nb is None:
                    continue
                sp = [
                    Point(na.pos.x, na.pos.y, na.pos.z),
                    Point(nb.pos.x, nb.pos.y, nb.pos.z),
                ]
            rev: Optional[bool] = None
            if k < len(segs) - 1:
                nxt = segs[k + 1]
                if nxt is not None:
                    if seg.end_node_id == nxt.start_node_id or seg.end_node_id == nxt.end_node_id:
                        rev = False  # 段 k 的后节点 = 共享节点（en 侧）
                    elif seg.start_node_id == nxt.start_node_id or seg.start_node_id == nxt.end_node_id:
                        rev = True   # 共享节点在 sn 侧 → 反转使 en 侧为后节点
            if rev is None and prev_after_node is not None:
                # 末段 / 链断裂处：按与上一段的共享节点定向
                if seg.start_node_id == prev_after_node:
                    rev = False
                elif seg.end_node_id == prev_after_node:
                    rev = True
            if rev is None:
                # 数据异常兜底：按离"上一段末点/起点站"近的一端定向
                anchor = pts[-1] if pts else start_pos
                rev = _dist2_pt(sp[0], anchor) > _dist2_pt(sp[-1], anchor)
            if rev:
                sp.reverse()
                prev_after_node = seg.start_node_id
            else:
                prev_after_node = seg.end_node_id
            if pts and sp:
                if _same_xy(pts[-1], sp[0]):
                    pts.extend(sp[1:])
                else:
                    pts.extend(sp)
            else:
                pts.extend(sp)
        return pts

    def track_route(self, graph: dict, start: int, end: int):
        """BFS 在轨道图 graph 上找 start→end 的最短路径。

        返回 (node_id 列表, seg_id 列表)；无路径返回 None。
        """
        if start == end:
            return [start], []
        prev = {start: None}   # node -> (前驱 node, 使用的 seg_id)
        queue = deque([start])
        while queue:
            cur = queue.popleft()
            if cur == end:
                break
            for nxt, seg_id in graph.get(cur, ()):
                if nxt not in prev:
                    prev[nxt] = (cur, seg_id)
                    queue.append(nxt)
        if end not in prev:
            return None
        nodes: List[int] = []
        segs: List[int] = []
        cur = end
        while cur is not None:
            nodes.append(cur)
            info = prev[cur]
            if info is not None:
                segs.append(info[1])
                cur = info[0]
            else:
                cur = None
        nodes.reverse()
        segs.reverse()
        return nodes, segs

    def track_line_geometry(self, graph: dict, stop_node_ids) -> List[Point]:
        """线路站点序列 → 沿轨道形状点的世界坐标点列。

        先把每个站点投影到最近轨道节点，再沿轨道段（与灰色轨道同源）
        逐对 BFS 取形状点；无通路对回退站点直连；首尾含站点自身位置；
        结果去重相邻重复点。
        """
        # 存档可能包含尚未配置站点的空线路（例如湖州存档中的废弃线路）。
        # 这类线路没有可绘制的几何，不能继续访问 stop_node_ids[0]。
        if not stop_node_ids:
            return []
        entries = [
            self.nearest_track_node(graph, sid) for sid in stop_node_ids
        ]
        pts: List[Point] = []
        n0 = self.node(stop_node_ids[0])
        if n0 is not None:
            pts.append(Point(n0.pos.x, n0.pos.y, n0.pos.z))
        for a, b in zip(entries, entries[1:]):
            if a is None or b is None or a == b:
                continue
            r = self.track_route(graph, a, b)
            if r is None:
                r = ([a, b], [])
            nodes, segs = r
            for i, seg_id in enumerate(segs):
                seg = self.segment(seg_id)
                if seg is None or not seg.points:
                    # 兜底：直连节点
                    for nd in (nodes[i], nodes[i + 1]):
                        n = self.node(nd)
                        if n is not None:
                            pts.append(Point(n.pos.x, n.pos.y, n.pos.z))
                    continue
                sp = seg.points
                sp2 = sp if seg.start_node_id == nodes[i] else list(reversed(sp))
                pts.extend(sp2 if i == 0 else sp2[1:])
            if not segs:
                for nd in nodes:
                    n = self.node(nd)
                    if n is not None:
                        pts.append(Point(n.pos.x, n.pos.y, n.pos.z))
        n1 = self.node(stop_node_ids[-1])
        if n1 is not None:
            pts.append(Point(n1.pos.x, n1.pos.y, n1.pos.z))
        out: List[Point] = []
        for p in pts:
            if out and abs(p.x - out[-1].x) < 1e-3 and abs(p.z - out[-1].z) < 1e-3:
                continue
            out.append(p)
        return out


    def bounds_2d(self) -> Tuple[float, float, float, float]:
        """计算所有实体的 2D 包围盒 (min_x, min_z, max_x, max_z)。"""
        xs: List[float] = []
        zs: List[float] = []
        for n in self.nodes:
            xs.append(n.pos.x)
            zs.append(n.pos.z)
        for s in self.segments:
            for p in s.points:
                xs.append(p.x)
                zs.append(p.z)
        for b in self.buildings:
            for p in b.points:
                xs.append(p.x)
                zs.append(p.z)
        for d in self.districts:
            for p in d.points:
                xs.append(p.x)
                zs.append(p.z)
        if not xs:
            return (0.0, 0.0, 0.0, 0.0)
        return (min(xs), min(zs), max(xs), max(zs))
