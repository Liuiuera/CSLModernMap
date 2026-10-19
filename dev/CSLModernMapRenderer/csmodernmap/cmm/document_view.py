"""提供由标准地图文档支持的只读延迟数据视图。

按需从 MapDocument 读取字段，不预先复制完整 MapData。仍使用旧接口的交通、POI 和标注服务可通过相同字段及方法访问数据。"""
from __future__ import annotations

from typing import Any

from ..data import (
    Building as LegacyBuilding,
    District as LegacyDistrict,
    Forest,
    MapData,
    Node as LegacyNode,
    Park,
    Point,
    ProceduralObject,
    Segment as LegacySegment,
    SegmentType,
    Terrain as LegacyTerrain,
    Transport,
    TransportStop,
)
from .model import MapDocument, NetworkKind, RoadLevel


def _traffic_snapshot(value):
    from ..traffic import ensure_snapshot
    return ensure_snapshot(value)


def _point(value) -> Point:
    return Point(float(value.x), float(value.y), float(value.z))


def _grid(value, *, fallback_rows: int, fallback_cols: int, origin, cell):
    rows = int(value.rows or fallback_rows)
    cols = int(value.cols or fallback_cols)
    if value.values is None or rows <= 0 or cols <= 0:
        return None, rows, cols
    import numpy as np
    arr = np.asarray(value.values, dtype=float)
    if arr.size < rows * cols:
        return None, rows, cols
    return arr.ravel()[:rows * cols].reshape((rows, cols)), rows, cols


class MapDocumentView:
    """提供由 MapDocument 支持的只读延迟 MapData 兼容视图；字段首次访问时计算并在该视图生命周期内缓存。"""

    def __init__(self, document: MapDocument) -> None:
        self._document = document
        self._cache: dict[str, Any] = {}

    # 延迟构建入口。

    def _build_nodes(self) -> list[LegacyNode]:
        return [LegacyNode(
            id=node.id,
            pos=_point(node.pos),
            elev=node.pos.y,
            ug=getattr(node.mode_hint, "value", node.mode_hint) == "TUNNEL",
            og=getattr(node.mode_hint, "value", node.mode_hint) == "GROUND",
            srv="",
            subsrv=(node.source_raw or ("PublicTransport" + str(getattr(
                node.station_service, "value", node.station_service)).title())
                    if node.station_service else ""),
        ) for node in self._document.nodes]

    def _build_segments_and_types(self) -> tuple[dict[str, SegmentType], list[LegacySegment]]:
        nodes = self.nodes
        segment_types: dict[str, SegmentType] = {}
        segments: list[LegacySegment] = []
        for network in self._document.networks:
            kind = network.kind
            type_name = str(network.name or kind.value)
            st = segment_types.get(type_name)
            if st is None:
                st = SegmentType(
                    name=type_name,
                    highway=network.level is RoadLevel.HIGHWAY,
                    road=kind is NetworkKind.ROAD,
                    beautification=kind is NetworkKind.BEAUTIFICATION,
                    width=float(network.width),
                    car_lane_count=(network.lane_summary.car_lane_count
                                     if network.lane_summary else 0),
                    lane_dir=(network.lane_summary.direction
                              if network.lane_summary else 0),
                    rail=kind is NetworkKind.RAIL,
                    tram=kind is NetworkKind.TRAM,
                    metro=kind is NetworkKind.METRO,
                    monorail=kind is NetworkKind.MONORAIL,
                    cablecar=kind is NetworkKind.CABLECAR,
                )
                segment_types[type_name] = st
            points = [_point(point) for point in network.geometry.points]
            if not points:
                start = next((item for item in nodes if item.id == network.start_id), None)
                end = next((item for item in nodes if item.id == network.end_id), None)
                points = [item.pos for item in (start, end) if item is not None]
            segments.append(LegacySegment(
                id=network.id,
                start_node_id=network.start_id,
                end_node_id=network.end_id,
                icls=type_name,
                width=float(network.width),
                name=type_name,
                custom_name=network.display_name,
                game_name=network.game_name,
                points=points,
                path_seg_ids=list(network.path_segment_ids),
            ))
        return segment_types, segments

    def _build_buildings(self) -> list[LegacyBuilding]:
        return [LegacyBuilding(
            id=building.id,
            name=building.name,
            custom_name=(building.display_name.text
                         if building.display_name.source == "custom" else ""),
            service=building.service or str(getattr(
                building.role, "value", building.role)).lower(),
            sub_service=building.sub_service,
            icls=building.icls or building.source_raw,
            points=[Point(point.x, 0.0, point.z)
                    for point in building.footprint.points],
            sub_building_id=building.sub_building_id or str(
                building.child_ids[0] if building.child_ids else ""),
            parent_building_id=building.parent_building_id or str(
                building.parent_id if building.parent_id is not None else ""),
            display_name=building.display_name.text,
            name_source=building.display_name.source,
            area_id=building.area_id,
            category_id=building.category_id,
            category_name=building.category_name,
            category_locale=building.category_locale,
            growth_kind=building.growth_kind,
            growth_use=building.growth_use,
        ) for building in self._document.buildings]

    def _build_districts_and_parks(self) -> tuple[list[LegacyDistrict], list[Park]]:
        districts: list[LegacyDistrict] = []
        parks: list[Park] = []
        for district in self._document.districts:
            if district.is_background:
                continue
            points = ([Point(point.x, 0.0, point.z) for point in district.boundary.points]
                      if district.boundary else [])
            if not points and district.position is not None:
                points = [Point(district.position.x, 0.0, district.position.z)]
            if not points and district.grid_points:
                points = [Point(
                    sum(point.x for point in district.grid_points) / len(district.grid_points),
                    0.0,
                    sum(point.z for point in district.grid_points) / len(district.grid_points))]
            grid_points = [(point.x, point.z) for point in district.grid_points]
            if district.kind == "PARK_ZONE":
                parks.append(Park(
                    id=district.id, name=district.name.text,
                    ptype=(district.source_raw or str(getattr(
                        district.zone_kind, "value", district.zone_kind))),
                    points=points, grid_points=grid_points,
                    owner_building_id=district.owner_building_id,
                ))
            else:
                districts.append(LegacyDistrict(
                    id=district.id, name=district.name.text,
                    dtype=(district.source_raw or str(getattr(
                        district.zone_kind, "value", district.zone_kind))),
                    points=points, grid_points=grid_points,
                ))
        return districts, parks

    def _build_transports(self) -> list[Transport]:
        stops_by_id = {stop.id: stop for stop in self._document.stops}
        transports: list[Transport] = []
        for line in self._document.transit_lines:
            route_stops: list[TransportStop] = []
            routes = sorted(self._document.routes_of_line(line.id),
                            key=lambda item: (-len(item.stop_ids), item.id))
            for route in routes[:1]:
                for stop_id in route.stop_ids:
                    stop = stops_by_id.get(stop_id)
                    if stop is not None:
                        route_stops.append(TransportStop(
                            node_id=stop.network_node_id,
                            name=stop.name.text,
                        ))
            color = (255, 255, 255, 255)
            if line.user_color is not None:
                r, g, b, a = line.user_color
                color = (int(a), int(r), int(g), int(b))
            transports.append(Transport(
                id=line.id, name=line.name,
                type=(line.source_raw or str(getattr(
                    line.kind, "value", line.kind)).title()),
                color=color, stops=route_stops,
            ))
        return transports

    def _build_terrain(self) -> LegacyTerrain:
        terrain_source = self._document.terrain
        rows = int(terrain_source.rows)
        cols = int(terrain_source.cols)
        heightmap, rows_h, cols_h = _grid(
            terrain_source.height, fallback_rows=rows, fallback_cols=cols,
            origin=terrain_source.origin, cell=terrain_source.cell)
        watermap, rows_w, cols_w = _grid(
            terrain_source.water_depth, fallback_rows=rows, fallback_cols=cols,
            origin=terrain_source.origin, cell=terrain_source.cell)
        rows = rows or rows_h or rows_w
        cols = cols or cols_h or cols_w
        return LegacyTerrain(
            heightmap=heightmap, watermap=watermap,
            land_array=heightmap, water_array=watermap,
            width=cols, height=rows,
            origin_x=terrain_source.origin.x,
            origin_z=terrain_source.origin.z,
            cell=terrain_source.cell,
        )

    def _build_forests(self) -> list[Forest]:
        return [Forest(data=list(item.data)) for item in self._document.forests]

    def _build_procedural_objects(self) -> list[ProceduralObject]:
        return [ProceduralObject(
            id=po.id,
            pos=Point(po.pos.x, po.pos.y, po.pos.z),
            center_x=po.center_x, center_z=po.center_z,
            extent_x=po.extent_x, extent_z=po.extent_z,
            rotation_y=po.rotation_y, scale=po.scale,
            prefab_name=po.prefab_name, source_type=po.source_type,
            color_rgb=po.color_rgb,
        ) for po in self._document.procedural_objects]

    # 只读延迟属性。

    @property
    def nodes(self) -> list[LegacyNode]:
        if "nodes" not in self._cache:
            self._cache["nodes"] = self._build_nodes()
        return self._cache["nodes"]

    @property
    def segments(self) -> list[LegacySegment]:
        return self._get_segments_and_types()[1]

    @property
    def segment_types(self) -> dict[str, SegmentType]:
        return self._get_segments_and_types()[0]

    def _get_segments_and_types(self) -> tuple[dict[str, SegmentType], list[LegacySegment]]:
        if "segments_and_types" not in self._cache:
            self._cache["segments_and_types"] = self._build_segments_and_types()
        return self._cache["segments_and_types"]

    @property
    def buildings(self) -> list[LegacyBuilding]:
        if "buildings" not in self._cache:
            self._cache["buildings"] = self._build_buildings()
        return self._cache["buildings"]

    @property
    def districts(self) -> list[LegacyDistrict]:
        return self._get_districts_and_parks()[0]

    @property
    def parks(self) -> list[Park]:
        return self._get_districts_and_parks()[1]

    def _get_districts_and_parks(self) -> tuple[list[LegacyDistrict], list[Park]]:
        if "districts_and_parks" not in self._cache:
            self._cache["districts_and_parks"] = self._build_districts_and_parks()
        return self._cache["districts_and_parks"]

    @property
    def transports(self) -> list[Transport]:
        if "transports" not in self._cache:
            self._cache["transports"] = self._build_transports()
        return self._cache["transports"]

    @property
    def terrain(self) -> LegacyTerrain:
        if "terrain" not in self._cache:
            self._cache["terrain"] = self._build_terrain()
        return self._cache["terrain"]

    @property
    def forests(self) -> list[Forest]:
        if "forests" not in self._cache:
            self._cache["forests"] = self._build_forests()
        return self._cache["forests"]

    @property
    def procedural_objects(self) -> list[ProceduralObject]:
        if "procedural_objects" not in self._cache:
            self._cache["procedural_objects"] = self._build_procedural_objects()
        return self._cache["procedural_objects"]

    @property
    def traffic(self):
        if "traffic" not in self._cache:
            self._cache["traffic"] = _traffic_snapshot(self._document.traffic)
        return self._cache["traffic"]

    @property
    def game(self) -> str:
        return self._document.metadata.origin.game.lower()

    @property
    def city_name(self) -> str:
        return self._document.metadata.origin.source_name

    @property
    def generated(self) -> str:
        return self._document.metadata.exported_at

    @property
    def sea_level(self) -> float:
        return self._document.sea_level

    @property
    def _source_path(self) -> str:
        return self._document.metadata.source_path or ""

    @_source_path.setter
    def _source_path(self, value: str) -> None:
        pass  # 只读属性。

    @property
    def _station_fix_path(self) -> str | None:
        return None  # CMM 加载文档没有显式站点修复路径。

    @property
    def district_cell(self) -> float:
        return float(self._document.metadata.counters.get("district_cell", 0))

    @property
    def park_cell(self) -> float:
        return self._document.park_cell

    @property
    def _node_by_id(self) -> dict:
        if "_node_by_id" not in self._cache:
            self._cache["_node_by_id"] = {item.id: item for item in self.nodes}
        return self._cache["_node_by_id"]

    @_node_by_id.setter
    def _node_by_id(self, value: dict) -> None:
        pass  # 只读属性，访问时计算正确值。

    @property
    def _segment_by_id(self) -> dict:
        if "_segment_by_id" not in self._cache:
            self._cache["_segment_by_id"] = {item.id: item for item in self.segments}
        return self._cache["_segment_by_id"]

    @_segment_by_id.setter
    def _segment_by_id(self, value: dict) -> None:
        pass  # 只读属性，访问时计算正确值。

    # 方法入口。

    def node(self, node_id: int):
        return self._node_by_id.get(int(node_id))

    def segment(self, seg_id: int):
        return self._segment_by_id.get(int(seg_id))

    def _chain_to_points(self, chain, start_pos):
        """将有序线段链转为世界坐标点，与 MapData 共用同一实现。"""
        from ..data import MapData

        return MapData._chain_to_points(self, chain, start_pos)

    def build_stop_line_graph(self, keyword: str) -> dict:
        """构建站点线路邻接图，规则与 MapData 一致。"""
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

    def build_track_graph(self, kinds=("metro",)) -> dict:
        """构建轨道邻接图，规则与 MapData 一致。"""
        if "build_track_graph" not in self._cache:
            from ..data import MapData  # noqa: PLC0415
            # 构造最小 MapData 以复用其轨道邻接图方法。
            fake = MapData(
                city_name=self.city_name, generated=self.generated,
                sea_level=self.sea_level, terrain=self.terrain,
                forests=self.forests, nodes=self.nodes, segments=self.segments,
                buildings=self.buildings, districts=self.districts,
                parks=self.parks, transports=self.transports,
                procedural_objects=self.procedural_objects,
                segment_types=self.segment_types,
                district_cell=self.district_cell, park_cell=self.park_cell,
                traffic=self.traffic,
            )
            fake._node_by_id = self._node_by_id
            fake._segment_by_id = self._segment_by_id
            self._cache["build_track_graph"] = fake.build_track_graph(kinds)
        return self._cache["build_track_graph"]

    # 便捷访问入口。

    @property
    def stops(self):
        """返回 CMM 原生站点，而非旧版 TransportStop。"""
        return self._document.stops

    @property
    def metadata(self):
        return self._document.metadata

    @property
    def derived(self):
        """通过旧版适配器保留标准设施归属。"""
        return getattr(self._document, "derived", None)
