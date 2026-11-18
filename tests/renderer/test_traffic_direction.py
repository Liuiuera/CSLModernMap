"""路况方向统计、PNG 偏移与预览共享中心线。

全是纯函数，不用开 GUI，也不需要真实存档。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = (Path(__file__).resolve().parents[2] / "dev/CSLModernMapRenderer")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from csmodernmap.preview_scene import (  # noqa: E402
    METERS_PER_DEGREE, _line_geometry, _traffic_features, world_to_geo,
)
from csmodernmap.layers.traffic_layer import TrafficLayer

from csmodernmap.traffic import (  # noqa: E402
    DIRECTION_BACKWARD, DIRECTION_BOTH, DIRECTION_FORWARD,
    SegStats, TrafficLane, TrafficSegment, Vec3,
)


def lane(position=0.0, congestion=0.5, direction=DIRECTION_FORWARD,
         lane_type="Vehicle", data_quality=0, width=3.5) -> TrafficLane:
    return TrafficLane(lane_type=lane_type, position=position, congestion=congestion,
                       direction=direction, data_quality=data_quality, width=width)


def segment(lanes, *, width=14.0, stats_congestion=-1.0, first=0.0, last=100.0,
            z=0.0) -> TrafficSegment:
    """沿 +x 的直线路段：世界坐标从 (first, 0, z) 到 (last, 0, z)。"""
    return TrafficSegment(
        segment_id=7, width=width,
        node_a=Vec3(first, 0.0, z), node_b=Vec3(last, 0.0, z),
        lanes=list(lanes),
        seg_stats=SegStats(weighted_avg_congestion=stats_congestion),
    )


def geo_to_world(coordinate) -> tuple[float, float]:
    """``world_to_geo`` 的逆：经纬度 → 世界坐标 (x, z)。"""
    lng, lat = coordinate
    return lat * METERS_PER_DEGREE, -lng * METERS_PER_DEGREE


def band(points, offset) -> dict | None:
    if len(points) < 2:
        return None
    seg = TrafficSegment(node_a=points[0], node_b=points[-1], curve_points=points[1:-1],
                         lanes=[lane(offset)])
    _, bands = TrafficLayer()._direction_bands(seg, 1, lambda x, z: (x, z))
    coords = bands[0][0]
    return {"type": "LineString", "coordinates": [
        world_to_geo(x, z) for x, z in zip(coords[::2], coords[1::2])]}


# ---------------------------------------------------------------- 分桶

def test_buckets_split_forward_and_backward():
    seg = segment([lane(-1.75, .8, DIRECTION_FORWARD),
                   lane(-5.25, .6, DIRECTION_FORWARD),
                   lane(1.75, .2, DIRECTION_BACKWARD)])
    buckets = seg.directional_lanes()
    assert set(buckets) == {DIRECTION_FORWARD, DIRECTION_BACKWARD}
    assert len(buckets[DIRECTION_FORWARD]) == 2
    assert len(buckets[DIRECTION_BACKWARD]) == 1


def test_both_direction_folds_into_forward_and_unknown_is_dropped():
    seg = segment([lane(1.0, .5, DIRECTION_BOTH),
                   lane(2.0, .5, ""),
                   lane(3.0, .5, "None")])
    buckets = seg.directional_lanes()
    assert list(buckets) == [DIRECTION_FORWARD], buckets
    assert len(buckets[DIRECTION_FORWARD]) == 1


@pytest.mark.parametrize("bad", [
    lane(1.0, -1.0, DIRECTION_FORWARD),                    # 无统计
    lane(1.0, .5, DIRECTION_FORWARD, lane_type="Pedestrian"),  # 非机动车道
    lane(1.0, .5, DIRECTION_FORWARD, data_quality=1),      # 降级数据
])
def test_unreliable_lanes_are_filtered_out(bad):
    # congestion = -1 必须挡掉，否则渲染器插值成 0 → 整条画成"畅通绿"。
    assert segment([bad]).directional_lanes() == {}


# ---------------------------------------------------------------- 偏移

def test_offset_is_the_mean_lane_position_per_direction():
    seg = segment([lane(2.0, .5, DIRECTION_FORWARD),
                   lane(4.0, .5, DIRECTION_FORWARD)])
    assert seg.direction_offset(DIRECTION_FORWARD) == pytest.approx(3.0)
    seg2 = segment([lane(-2.0, .5, DIRECTION_BACKWARD),
                    lane(-6.0, .5, DIRECTION_BACKWARD)])
    assert seg2.direction_offset(DIRECTION_BACKWARD) == pytest.approx(-4.0)


def test_offset_falls_back_to_quarter_width_with_the_right_sign():
    # 该方向没有有效车道时兜底 ±width/4：正 = 前进方向右侧。
    seg = segment([lane(1.0, .5, DIRECTION_FORWARD)], width=20.0)
    assert seg.direction_offset(DIRECTION_BACKWARD) == pytest.approx(-5.0)
    empty = segment([], width=20.0)
    assert empty.direction_offset(DIRECTION_FORWARD) == pytest.approx(5.0)
    assert empty.direction_offset(DIRECTION_BACKWARD) == pytest.approx(-5.0)


def test_offset_pushes_points_to_the_right_of_travel_direction():
    """沿 +x 前进时，正偏移应落在 +z 侧（法线取切线 (dx,dz) 的 (-dz,dx)）。"""
    seg = segment([lane(0.0, .5, DIRECTION_FORWARD)])
    geometry = band(seg.world_points(), 3.5)
    xs = [geo_to_world(c)[0] for c in geometry["coordinates"]]
    zs = [geo_to_world(c)[1] for c in geometry["coordinates"]]
    assert xs == pytest.approx([0.0, 100.0])
    assert zs == pytest.approx([3.5, 3.5])


def test_offset_stays_in_world_metres_away_from_the_origin():
    """偏移量在世界坐标里算，不能随位置/纬度被缩放。"""
    seg = segment([lane(0.0, .5, DIRECTION_FORWARD)], first=4000.0, last=4200.0, z=5000.0)
    geometry = band(seg.world_points(), 3.5)
    zs = [geo_to_world(c)[1] for c in geometry["coordinates"]]
    assert zs == pytest.approx([5003.5, 5003.5])


def test_offset_uses_central_difference_so_corners_stay_parallel():
    points = [Vec3(0, 0, 0), Vec3(100, 0, 0), Vec3(100, 0, 100)]
    geometry = band(points, 10.0)
    assert geometry is not None and len(geometry["coordinates"]) == 3
    # 中间拐点用前后点差分求切线 → 法线指向 45°，两个分量都约 7.07。
    mid = geo_to_world(geometry["coordinates"][1])
    assert mid[0] == pytest.approx(100 - 10 / 2 ** .5)
    assert mid[1] == pytest.approx(10 / 2 ** .5)


def test_offset_line_needs_two_points():
    assert band([Vec3(0, 0, 0)], 3.0) is None
    assert band([], 3.0) is None


def test_degenerate_preview_centerline_keeps_source_endpoints():
    feature, = _traffic_features(segment([], first=5, last=5, z=5))
    assert feature["geometry"]["coordinates"] == [world_to_geo(5, 5)] * 2


def test_png_band_agrees_with_offsetting_in_world_space():
    points = [Vec3(0, 0, 0), Vec3(50, 0, 10), Vec3(100, 0, 0)]
    normals = TrafficLayer._normals(points)
    geometry = band(points, 4.0)
    expected = [world_to_geo(p.x + nx * 4.0, p.z + nz * 4.0)
                for p, (nx, nz) in zip(points, normals)]
    flat = lambda pairs: [value for pair in pairs for value in pair]
    assert flat(geometry["coordinates"]) == pytest.approx(flat(expected))


def test_two_way_segment_shares_centerline_with_opposite_offsets():
    seg = segment([lane(3.5, .9, DIRECTION_FORWARD),
                   lane(-3.5, .1, DIRECTION_BACKWARD)])
    features = _traffic_features(seg)
    assert len(features) == 1
    feature = features[0]
    assert feature["id"] == "traffic:7"
    assert feature["geometry"] == _line_geometry(seg.world_points())
    props = feature["properties"]
    assert props["forwardCongestion"] == .9
    assert props["backwardCongestion"] == .1
    assert props["forwardOffset"] == 3.5
    assert props["backwardOffset"] == -3.5
    assert props["forwardWidth"] == props["backwardWidth"] == 3.5


def test_one_way_segment_keeps_id_and_width_weighted_statistics():
    seg = segment([lane(-1, .2, DIRECTION_FORWARD, width=2),
                   lane(1, .8, DIRECTION_FORWARD, width=6)])
    feature, = _traffic_features(seg)
    assert feature["id"] == "traffic:7"
    props = feature["properties"]
    assert props["forwardCongestion"] == pytest.approx(.65)
    assert props["forwardWidth"] == 8
    assert props["forwardOffset"] == .5
    assert props["backwardCongestion"] == -1


def test_segment_statistics_remain_centred_without_reliable_lanes():
    seg = segment([lane(1, .7, DIRECTION_FORWARD, data_quality=1)], stats_congestion=.33)
    feature, = _traffic_features(seg)
    assert feature["properties"]["forwardCongestion"] == .33
    assert feature["properties"]["forwardOffset"] == 0
    assert feature["properties"]["forwardWidth"] == 14
    assert feature["geometry"] == _line_geometry(seg.world_points())


def test_no_data_keeps_centerline_without_inventing_free_flow():
    for seg in (segment([], stats_congestion=-1),
                segment([lane(1, -1, DIRECTION_FORWARD)])):
        feature, = _traffic_features(seg)
        assert feature["properties"]["geometryRole"] == "traffic"
        assert feature["properties"]["forwardCongestion"] == -1
        assert feature["properties"]["backwardCongestion"] == -1


def test_preview_uses_matched_road_width_and_style():
    from test_preview_road_order import scene_from_lines
    road = scene_from_lines([[(0, 0, 0), (100, 0, 0)]], width=12).roads[0]
    feature, = _traffic_features(segment([lane(3, .5)], width=20), road)
    props = feature["properties"]
    assert props["physicalWidth"] == 12
    assert props["styleKey"] == f"{road.level.value}:{road.mode}"
    assert props["forwardOffset"] == 3
    assert props["surface"] is True


def test_missing_direction_data_still_keeps_two_way_sides():
    seg = segment([lane(3.5, .8), lane(-3.5, -1, DIRECTION_BACKWARD)])
    props = _traffic_features(seg)[0]["properties"]
    assert props["splitDirections"] is True
    assert props["forwardSide"] == 1
    assert props["backwardCongestion"] == -1
    seg.lanes[0].position = -3.5
    seg.lanes[1].position = 3.5
    assert _traffic_features(seg)[0]["properties"]["forwardSide"] == -1


def test_simplification_bounds_error_preserves_endpoints_and_source():
    from copy import deepcopy
    from math import sin
    from shapely.geometry import LineString
    seg = segment([], first=4000, last=4100, z=5000)
    seg.curve_points = [Vec3(4000+i, 0, 5000+sin(i/10)) for i in range(1, 100)]
    original = deepcopy(seg)
    feature, = _traffic_features(seg)
    points = [geo_to_world(c) for c in feature["geometry"]["coordinates"]]
    assert points[0] == pytest.approx((4000, 5000))
    assert points[-1] == pytest.approx((4100, 5000))
    assert len(points) < 20
    assert LineString([(p.x, p.z) for p in seg.world_points()]).hausdorff_distance(
        LineString(points)) <= .250001
    assert seg == original
