from __future__ import annotations

import pytest

from road_fixtures import scene_from_lines, rank_at, joined_surfaces, boundary_covers
from PySide6.QtCore import QPointF
from PySide6.QtGui import QPainterPathStroker

from csmodernmap.cmm import Polygon, Vec2
from csmodernmap.preview_road_order import (
    build_road_order, _clip_lines, _path, _rings, _split_surface,
)


@pytest.mark.parametrize('reverse', [False, True])
def test_consecutive_short_surface_cuts_preserve_ownership_and_perimeter(reverse):
    road = scene_from_lines([[(0, 0, 0), (100, 0, 0)]]).roads[0]
    perimeter = [(p.x, p.z) for p in road.footprint.points]
    if reverse:
        perimeter.reverse()
    original = _path(perimeter)
    remainder = original
    combined = _path([])
    cuts = [0, 49.981415, 50.011735, 50.042055, 100]
    for index, (start, end) in enumerate(zip(cuts, cuts[1:])):
        if index < len(cuts)-2:
            piece, remainder = _split_surface(remainder, road, start, end)
            assert not remainder.contains(QPointF((start+end)*.5, 0))
        else:
            piece = remainder
        assert piece.contains(QPointF((start+end)*.5, 0))
        for line in _clip_lines((tuple(perimeter + [perimeter[0]]),), piece):
            for a, b in zip(line, line[1:]):
                x, z = (a[0]+b[0])*.5, (a[1]+b[1])*.5
                assert abs(z) >= 2.99 or x <= 0 or x >= 100
        combined = combined.united(piece)
    assert not _rings(original.subtracted(combined))
    assert not _rings(combined.subtracted(original))


def test_four_surfaces_form_four_complete_painter_ranks():
    scene = scene_from_lines([
        [(-80, 0, 0), (80, 0, 0)],
        [(0, 10, -80), (0, 10, 80)],
        [(-80, 20, -80), (80, 20, 80)],
        [(-80, 30, 80), (80, 30, -80)],
    ])
    result = build_road_order(scene)
    assert [rank_at(result, i, 0, 0) for i in range(1, 5)] == [0, 1, 2, 3]
    assert result.diagnostics['splitRoads'] == 0


def test_spatial_cycle_splits_without_losing_surface_or_adding_cut_edges():
    scene = scene_from_lines([
        [(-80, -30, 0), (80, 90, 0)],
        [(-40, 10, -80), (-40, 10, 80)],
        [(-100, 20, 60), (100, 20, -60)],
    ])
    result = build_road_order(scene)
    assert result.diagnostics['splitRoads'] > 0
    assert rank_at(result, 1, -40, 0) < rank_at(result, 2, -40, 0)
    assert rank_at(result, 2, -40, 24) < rank_at(result, 3, -40, 24)
    assert rank_at(result, 3, 0, 0) < rank_at(result, 1, 0, 0)
    for road in scene.roads:
        original = _path([(p.x, p.z) for p in road.footprint.points])
        union = _path([])
        for piece in result.pieces[road.id]:
            for ring in piece.rings:
                union = union.united(_path(ring))
            # Every visible edge lies on the original perimeter, never on a cut.
            stroke = QPainterPathStroker()
            stroke.setWidth(.001)
            perimeter = stroke.createStroke(original)
            for line in piece.boundaries:
                for a, b in zip(line, line[1:]):
                    assert perimeter.contains(QPointF((a[0]+b[0])*.5, (a[1]+b[1])*.5))
        # Qt may retain zero-area collinear paths after a boolean operation.
        assert not _rings(original.subtracted(union))
        assert not _rings(union.subtracted(original))


def test_same_pair_can_reverse_order_at_two_crossings():
    scene = scene_from_lines([
        [(-100, -20, 0), (100, 40, 0)],
        [(-60, 10, -50), (-40, 10, 0), (0, 10, 40), (40, 10, 0), (60, 10, -50)],
    ], width=4)
    result = build_road_order(scene)
    assert rank_at(result, 1, -40, 0) < rank_at(result, 2, -40, 0)
    assert rank_at(result, 1, 40, 0) > rank_at(result, 2, 40, 0)


def test_hairpin_cut_does_not_take_a_distant_arm_with_it():
    scene = scene_from_lines([
        [(-80, 0, -20), (80, 0, -20), (80, 30, 20), (-80, 30, 20)],
        [(80, 10, -40), (-80, 10, 40)],
    ], width=4)
    result = build_road_order(scene)
    assert rank_at(result, 1, 40, -20) < rank_at(result, 2, 40, -20)
    assert rank_at(result, 1, -40, 20) > rank_at(result, 2, -40, 20)


def test_parallel_footprints_overlap_without_centreline_crossing():
    scene = scene_from_lines([
        [(-80, 0, 0), (80, 0, 0)],
        [(-80, 10, 4), (80, 10, 4)],
    ], width=10)
    assert not scene.crossings
    result = build_road_order(scene)
    assert result.pieces[1][0].rank < result.pieces[2][0].rank


def test_zero_heights_and_same_grade_do_not_create_false_bridge_layers():
    scene = scene_from_lines([
        [(-80, 0, 0), (80, 0, 0)],
        [(0, 0, -80), (0, 0, 80)],
    ])
    result = build_road_order(scene)
    assert result.diagnostics['ranks'] == 1


def test_fallback_lines_have_the_same_physical_order():
    lines = [[(-80, 0, 0), (80, 0, 0)], [(0, 10, -80), (0, 10, 80)]]
    result = build_road_order(scene_from_lines(lines, surfaces=False))
    assert result.pieces[1][0].rank < result.pieces[2][0].rank


def test_order_does_not_depend_on_source_iteration_order():
    scene = scene_from_lines([
        [(-80, 0, 0), (80, 0, 0)], [(0, 10, -80), (0, 10, 80)]])
    before = build_road_order(scene)
    scene.roads.reverse()
    after = build_road_order(scene)
    assert {i: [p.rank for p in ps] for i, ps in before.pieces.items()} == {
        i: [p.rank for p in ps] for i, ps in after.pieces.items()}


def test_bridge_landing_has_no_transverse_seam_when_mode_changes():
    from dataclasses import replace
    scene = scene_from_lines([
        [(-80, 10, 0), (0, 10, 0)], [(0, 10, 0), (80, 10, 0)]])
    first, second = scene.roads
    second.segment.start_id = first.end_node_id
    for road in scene.roads:
        x0, x1 = road.points[0].x, road.points[-1].x
        road.segment.footprint = Polygon((Vec2(x0, -3), Vec2(x1, -3),
                                          Vec2(x1, 3), Vec2(x0, 3)))
    scene.roads = [replace(first, mode='elevated'), replace(second, mode='ground')]
    scene.roads_by_id = {r.id: r for r in scene.roads}
    result = build_road_order(scene)
    for pieces in result.pieces.values():
        for piece in pieces:
            assert piece.boundaries
            for line in piece.boundaries:
                assert not any(abs(a[0]) < 1e-5 and abs(b[0]) < 1e-5
                               for a, b in zip(line, line[1:]))


@pytest.mark.parametrize('reverse', [False, True])
def test_wide_node_split_caps_are_internal_regardless_of_winding(reverse):
    scene = joined_surfaces()
    if reverse:
        for road in scene.roads:
            road.segment.footprint = Polygon(tuple(reversed(road.footprint.points)))
    order = build_road_order(scene)
    for road_id in (1, 2):
        assert not boundary_covers(order, road_id, (0, 6))
        assert not boundary_covers(order, road_id, (0, -6))
    assert boundary_covers(order, 1, (-40, 12))
    assert boundary_covers(order, 2, (40, 12))
    assert boundary_covers(order, 1, (-80, 0))  # true terminus


def test_partial_cap_coverage_keeps_exposed_shoulders():
    order = build_road_order(joined_surfaces(narrow=True))
    assert not boundary_covers(order, 1, (0, 2))
    assert not boundary_covers(order, 2, (0, 2))
    assert boundary_covers(order, 1, (0, 8))
    assert boundary_covers(order, 1, (0, -8))


def test_coincident_but_disconnected_roads_keep_their_caps():
    order = build_road_order(joined_surfaces(connected=False))
    assert boundary_covers(order, 1, (0, 6))
    assert boundary_covers(order, 2, (0, 6))


def test_common_exterior_is_not_an_internal_seam():
    scene = joined_surfaces()
    scene.roads[1].segment.footprint = scene.roads[0].footprint
    order = build_road_order(scene)
    assert boundary_covers(order, 1, (-40, 12))
    assert boundary_covers(order, 2, (-40, 12))


def test_connected_roads_keep_boundaries_at_remote_grade_separation():
    from dataclasses import replace
    from csmodernmap.cmm import Polyline, Vec3
    scene = joined_surfaces()
    # Share the landing at (0, 0), then overlap again while the second road rises.
    second = scene.roads[1]
    second.segment.geometry = Polyline((Vec3(0, 0, 0), Vec3(-80, 30, 0)))
    second.segment.footprint = Polygon(tuple(Vec2(*p) for p in
        [(0, -16), (-80, -16), (-80, 16), (0, 16)]))
    scene.roads[1] = replace(second, points=second.segment.geometry.points)
    scene.roads_by_id[2] = scene.roads[1]
    scene.height_reliable = True
    order = build_road_order(scene)
    assert boundary_covers(order, 1, (-60, 12))
