from math import cos, sin, radians

import numpy as np
import pytest
from PySide6.QtCore import QPointF
from PySide6.QtGui import QPainterPathStroker

from csmodernmap.cmm import (MapDocument, Metadata, NetworkKind, NetworkNode,
    NetworkSegment, Origin, Polygon, Polyline, RoadLevel, SegmentMode,
    Terrain, Vec2, Vec3)
from csmodernmap.cmm.session import DocumentSession
from csmodernmap.layers.road_layer import RoadLayer
from csmodernmap.preview_road_order import build_road_order, _path, _polygons, _rings
from csmodernmap.preview_scene import _ordered_road_features
from csmodernmap.renderer import Renderer
from csmodernmap.road_geometry import build_road_scene
from csmodernmap.style import Style


def junction_document(game='cs1', angle=0, mode=SegmentMode.GROUND, height=0,
                      branch_level=RoadLevel.BRANCH, main_width=32):
    def rotate(x, z):
        a = radians(angle)
        return (x*cos(a)+z*sin(a), -x*sin(a)+z*cos(a))
    outlines = [((-100,-16),(0,-16),(0,0),(-8,8),(0,16),(-100,16)),
                ((0,-16),(100,-16),(100,16),(0,16),(8,8),(0,0)),
                tuple(rotate(x,z) for x,z in ((0,0),(8,8),(8,100),(-8,100),(-8,8)))]
    x,z = rotate(0,100)
    endpoints = [Vec3(-100,0,0), Vec3(100,0,0), Vec3(x,height,z)]
    networks = []
    for i, endpoint in enumerate(endpoints):
        start = Vec3(0, height if i == 2 else 0, 0)
        networks.append(NetworkSegment(i+10, NetworkKind.ROAD, 1, i+2,
            Polyline((start, endpoint)), mode if i == 2 else SegmentMode.GROUND,
            16 if i == 2 else main_width,
            level=branch_level if i == 2 else RoadLevel.MAIN,
            footprint=Polygon(tuple(Vec2(x,z) for x,z in outlines[i]))))
    return MapDocument(metadata=Metadata(Origin(game,'test','junction')), sea_level=0,
        nodes=[NetworkNode(1,Vec3(0,0,0)), *[NetworkNode(i+2,p) for i,p in enumerate(endpoints)]],
        networks=networks, buildings=[], districts=[], stops=[], transit_lines=[],
        routes=[], terrain=Terrain())


@pytest.mark.parametrize('game', ['cs1','cs2'])
@pytest.mark.parametrize('angle', [0,35,70])
def test_mixed_native_junction_recolors_tip_and_preserves_every_surface(game, angle):
    scene = build_road_scene(junction_document(game,angle))
    order = build_road_order(scene)
    piece = order.pieces[12][0]
    assert piece.paints
    assert {region.road.id for region in piece.paints} == {11,12}
    original = _path([(p.x,p.z) for p in piece.road.footprint.points])
    combined = _path([])
    perimeter_stroke = QPainterPathStroker()
    perimeter_stroke.setWidth(.001)
    perimeter = perimeter_stroke.createStroke(original)
    for region in piece.paints:
        for ring in region.rings:
            combined = combined.united(_path(ring))
        for line in region.boundaries:
            for a,b in zip(line,line[1:]):
                assert perimeter.contains(QPointF((a[0]+b[0])*.5,(a[1]+b[1])*.5))
    assert not _rings(original.subtracted(combined))
    assert not _rings(combined.subtracted(original))
    features,_ = _ordered_road_features(scene,order)
    branch = [f for f in features if f['properties']['roadId'] == 12
              and f['properties']['geometryRole'] == 'road-surface']
    assert {f['properties']['styleKey'] for f in branch} == {'main:ground','branch:ground'}
    assert all(f['properties']['sourceStyleKey'] == 'branch:ground' for f in branch)
    assert all(f['properties']['rank'] == piece.rank for f in branch)


@pytest.mark.parametrize('mode,height', [(SegmentMode.ELEVATED,0),
    (SegmentMode.TUNNEL,0),(SegmentMode.GROUND,10)])
def test_mode_and_grade_separation_never_share_junction_color(mode,height):
    order = build_road_order(build_road_scene(junction_document(mode=mode,height=height)))
    assert not order.pieces[12][0].paints


def test_same_level_different_width_keeps_original_surface():
    order = build_road_order(build_road_scene(junction_document(branch_level=RoadLevel.MAIN)))
    assert not order.pieces[12][0].paints


def test_qt_polygon_conversion_keeps_ring_holes_explicit():
    outer = _path(((0,0),(100,0),(100,100),(0,100)))
    hole = _path(((20,20),(80,20),(80,80),(20,80)))
    polygons = _polygons(outer.subtracted(hole))
    assert len(polygons) == 1 and len(polygons[0]) == 2
    restored = _path(polygons[0][0]).subtracted(_path(polygons[0][1]))
    assert not restored.contains(QPointF(50,50))
    assert restored.contains(QPointF(10,10))


def roundabout_document():
    document = junction_document()
    document.nodes = [NetworkNode(1,Vec3(0,0,0)), NetworkNode(2,Vec3(40,0,-40))]
    outlines = [((-8,0),(8,0),(8,-32),(48,-32),(48,-48),(-8,-48)),
                ((-8,0),(8,0),(8,32),(72,32),(72,-32),(40,-32),
                 (40,-48),(88,-48),(88,48),(-8,48))]
    lines = [((0,0),(0,-40),(40,-40)),
             ((0,0),(0,40),(80,40),(80,-40),(40,-40))]
    document.networks = [NetworkSegment(i+10,NetworkKind.ROAD,1,2,
        Polyline(tuple(Vec3(x,0,z) for x,z in lines[i])),SegmentMode.GROUND,16,
        level=RoadLevel.MAIN if i == 0 else RoadLevel.BRANCH,
        footprint=Polygon(tuple(Vec2(x,z) for x,z in outlines[i]))) for i in range(2)]
    return document


def test_native_roundabout_hole_survives_core_clipping_in_png_and_vectors():
    document = roundabout_document()
    scene = build_road_scene(document)
    order = build_road_order(scene)
    assert order.pieces[11][0].paints
    for pieces in order.pieces.values():
        for piece in pieces:
            for region in piece.paint_regions:
                assert not any(_path(ring).contains(QPointF(40,0)) for ring in region.rings)
    image = render_junction(document)
    assert image.getpixel((256,round(256-40*512/220)))[:3] == (238,238,238)


def render_junction(document, tiled=False):
    session = DocumentSession(document)
    renderer = Renderer(Style({'canvas': {'background':'#eeeeee'},'roads': {
        'main': {'fill':'#368cd1','border':'#111111','border_width':1},
        'branch': {'fill':'#d84a4a','border':'#111111','border_width':1}}}),512,512)
    if tiled:
        renderer.tile_size, renderer.tile_bleed = 128,32
    renderer.add_layer(RoadLayer())
    return renderer.render(document,session=session,region=(-110,-110,110,110))


def test_png_core_color_matches_preview_owner_and_tiling():
    document = junction_document()
    image = render_junction(document)
    # Renderer places screen-right at -Z and screen-up at +X.
    assert image.getpixel((round(256-10*512/220),256))[:3] == (54,140,209)
    assert image.getpixel((round(256-60*512/220),256))[:3] == (216,74,74)
    tiled = render_junction(document,True)
    assert np.abs(np.asarray(image).astype(int)-np.asarray(tiled).astype(int)).max() <= 1
