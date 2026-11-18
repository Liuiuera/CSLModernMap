"""Deterministic map for theme parity and native editor checks (not a city)."""
from pathlib import Path

import numpy as np

from csmodernmap.cmm import Label
from csmodernmap.data import Building, MapData, Node, Point, Segment, SegmentType, Terrain
from cmm_test_factory import document_from_mapdata


def theme_editor_document():
    heights = np.tile(np.linspace(0, 650, 64, dtype=np.float32), (64, 1))
    source = MapData(city_name="主题验证合成地图", terrain=Terrain(
        heightmap=heights, watermap=np.zeros((64, 64), dtype=np.float32),
        width=64, height=64, origin_x=0, origin_z=0, cell=20))
    for i in range(15):
        level, mode = i % 5, i // 5
        key = ("Motorway", "Main Road", "Small Road", "Airport Area Runway", "Pedestrian Path")[level]
        z = 80 + i * 76
        points = [Point(50, mode * 10, z), Point(1230, mode * 10, z)]
        for j, point in enumerate(points):
            source.nodes.append(Node(i * 2 + j + 1, point, og=mode == 0, ug=mode == 2))
        source.segments.append(Segment(i+1, i*2+1, i*2+2, name=key, icls=key,
            custom_name=f"验证道路 {i+1}", width=24 if level == 1 else 16, points=points))
        source.segment_types[key] = SegmentType(road=level != 4, highway=level == 0,
            beautification=level == 4, width=16, car_lane_count=4 if level == 1 else 2)
    for i in range(20):
        x, z = 260 + (i % 4)*200, 130 + (i//4)*215
        source.buildings.append(Building(id=i+1, service="Residential", points=[
            Point(x,0,z),Point(x+55,0,z),Point(x+55,0,z+45),Point(x,0,z+45)]))
    doc = document_from_mapdata(source)
    # Canonical labels exercise the current shared placement/paint pipeline.
    doc.labels = [Label.from_dict({"id": 9001, "type": "DISTRICT_NAME", "source": "CUSTOM",
        "text": "人民广场", "ref_kind": "district", "ref_id": 1,
        "position": {"kind": "POINT", "x": 640, "z": 640}, "priority": 100,
        "style_ref": "labels.district"})]
    return doc
