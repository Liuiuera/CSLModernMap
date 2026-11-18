"""Small CMM builders for tests that also exercise legacy geometry helpers."""
from __future__ import annotations

import numpy as np

from csmodernmap.cmm import (
    Building,
    DisplayName,
    Grid,
    MapDocument,
    Metadata,
    NetworkNode,
    NetworkSegment,
    Origin,
    Polygon,
    Polyline,
    RoadLevel,
    SegmentMode,
    Terrain,
    Vec2,
    Vec3,
)
from csmodernmap.cmm.map_v41_common import _building_role, _network_kind
from csmodernmap.road_hierarchy import classify_segment, segment_physical_width


def _mode(node) -> SegmentMode:
    if node.ug:
        return SegmentMode.TUNNEL
    if node.og:
        return SegmentMode.GROUND
    return SegmentMode.ELEVATED


def _grid(values, rows: int, cols: int, origin: Vec2, cell: float) -> Grid:
    if values is None:
        return Grid(rows=rows, cols=cols, origin=origin, cell=cell)
    array = np.asarray(values, dtype=np.float32).reshape(rows, cols)
    return Grid(rows=rows, cols=cols, origin=origin, cell=cell, _values=array)


def document_from_mapdata(source) -> MapDocument:
    """Build only the canonical fields needed by synthetic renderer fixtures."""
    legacy_nodes = {node.id: node for node in source.nodes}
    nodes = [
        NetworkNode(node.id, Vec3(node.pos.x, node.pos.y, node.pos.z), _mode(node))
        for node in source.nodes
    ]
    networks = []
    for segment in source.segments:
        segment_type = (source.segment_types.get(segment.name)
                        or source.segment_types.get(segment.icls))
        kind, transit_for = _network_kind(segment, segment_type)
        points = segment.points or [
            node.pos for node in (
                legacy_nodes.get(segment.start_node_id),
                legacy_nodes.get(segment.end_node_id),
            ) if node is not None
        ]
        start = legacy_nodes.get(segment.start_node_id)
        end = legacy_nodes.get(segment.end_node_id)
        mode = (SegmentMode.TUNNEL if (start and start.ug) or (end and end.ug)
                else SegmentMode.GROUND if (not start or start.og) and (not end or end.og)
                else SegmentMode.ELEVATED)
        level = classify_segment(segment, segment_type) if kind.value in {"ROAD", "BEAUTIFICATION"} else None
        if level is not None and level.value == "quay":
            level = RoadLevel.BEAUTIFICATION
        networks.append(NetworkSegment(
            id=segment.id,
            kind=kind,
            start_id=segment.start_node_id,
            end_id=segment.end_node_id,
            geometry=Polyline(tuple(Vec3(point.x, point.y, point.z) for point in points)),
            mode=mode,
            width=segment_physical_width(segment, segment_type),
            level=(level.name if level is not None else None),
            transit_for=transit_for,
            path_segment_ids=list(segment.path_seg_ids),
            name=segment.name or "",
            display_name=segment.custom_name or "",
            source_raw=segment.name or "",
        ))
    buildings = [
        Building(
            id=item.id,
            name=item.name or "",
            display_name=DisplayName(
                item.display_name or item.custom_name or "",
                (item.name_source if item.display_name else
                 "custom" if item.custom_name else "empty"),
            ),
            role=_building_role(item.service, item.sub_service, item.icls),
            footprint=Polygon(tuple(Vec2(point.x, point.z) for point in item.points)),
            service=item.service or "",
            sub_service=item.sub_service or "",
            icls=item.icls or "",
            category_id=item.category_id,
            source_raw=" ".join(filter(None, (item.service, item.sub_service, item.icls))),
        )
        for item in source.buildings
    ]
    raw_terrain = source.terrain
    rows = int(raw_terrain.height or 0)
    cols = int(raw_terrain.width or 0)
    origin = Vec2(raw_terrain.origin_x, raw_terrain.origin_z)
    terrain = Terrain(
        height=_grid(raw_terrain.heightmap, rows, cols, origin, raw_terrain.cell),
        water_depth=_grid(raw_terrain.watermap, rows, cols, origin, raw_terrain.cell),
        origin=origin,
        cell=raw_terrain.cell,
        rows=rows,
        cols=cols,
    )
    return MapDocument(
        metadata=Metadata(Origin("cs1", "test", source.city_name or "fixture")),
        sea_level=source.sea_level,
        nodes=nodes,
        networks=networks,
        buildings=buildings,
        districts=[],
        stops=[],
        transit_lines=[],
        routes=[],
        terrain=terrain,
        traffic=source.traffic,
    )


def cs1_mapdata(**fields):
    """Legacy geometry fixture with an explicit CS1 mapping namespace."""
    from csmodernmap.data import MapData

    source = MapData(**fields)
    source.game = "cs1"
    return source
