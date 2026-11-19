"""Small authoritative road scenes shared by geometry and browser checks."""
from PySide6.QtCore import QPointF
from PySide6.QtGui import QPainterPathStroker
from cmm_test_factory import document_from_mapdata
from csmodernmap.cmm import Polygon, Vec2
from csmodernmap.data import MapData, Node, Point, Segment, SegmentType
from csmodernmap.preview_road_order import _path, _rings
from csmodernmap.road_geometry import build_road_scene

def scene_from_lines(lines, *, surfaces=True, width=6):
    nodes, segments = [], []
    for i, coordinates in enumerate(lines):
        points = [Point(*p) for p in coordinates]
        start, end = 2*i+1, 2*i+2
        nodes.extend([Node(start, points[0]), Node(end, points[-1])])
        segments.append(Segment(i+1, start, end, name='road', points=points))
    data = MapData(nodes=nodes, segments=segments, segment_types={
        'road': SegmentType(name='road', road=True, width=width)})
    data._node_by_id = {n.id: n for n in nodes}
    data._segment_by_id = {s.id: s for s in segments}
    document = document_from_mapdata(data)
    if surfaces:
        stroker = QPainterPathStroker()
        stroker.setWidth(width)
        for network in document.networks:
            path = stroker.createStroke(_path([(p.x, p.z) for p in network.geometry.points], False))
            network.footprint = Polygon(tuple(Vec2(*p) for p in _rings(path)[0]))
    return build_road_scene(document)



def rank_at(order, road_id, x, z):
    return max(piece.rank for piece in order.pieces[road_id]
               if any(_path(ring).contains(QPointF(x, z)) for ring in piece.rings))



def joined_surfaces(*, narrow=False, connected=True):
    scene = scene_from_lines([
        [(-80, 0, 0), (0, 0, 0)], [(0, 0, 0), (80, 0, 0)]], width=24)
    first, second = scene.roads
    if connected:
        second.segment.start_id = first.end_node_id
    first.segment.footprint = Polygon(tuple(Vec2(*p) for p in
        [(-80, -12), (0, -12), (0, 0), (0, 12), (-80, 12)]))
    half = 4 if narrow else 12
    second.segment.footprint = Polygon(tuple(Vec2(*p) for p in
        [(0, -half), (80, -half), (80, half), (0, half), (0, 0)]))
    return scene



def boundary_covers(order, road_id, point, tolerance=.06):
    from math import hypot
    for piece in order.pieces[road_id]:
        for line in piece.boundaries:
            for a, b in zip(line, line[1:]):
                dx, dz = b[0]-a[0], b[1]-a[1]
                t = max(0, min(1, ((point[0]-a[0])*dx+(point[1]-a[1])*dz)/(dx*dx+dz*dz)))
                if hypot(point[0]-a[0]-t*dx, point[1]-a[1]-t*dz) < tolerance:
                    return True
    return False
