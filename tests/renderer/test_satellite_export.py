import pytest

from csmodernmap.cmm.model import NetworkKind, SegmentMode, NetworkSegment, Polyline, Polygon
from csmodernmap.satellite_export import bridge_coverage


def segment(kind, mode=SegmentMode.ELEVATED, footprint=None, width=10):
    return NetworkSegment(1, kind, 1, 2, Polyline.from_dict([[10, 5, 50], [90, 5, 50]]),
                          mode, width, footprint=footprint)


def test_rail_bridge_uses_real_width_and_ignores_stop_line_and_ground():
    rail = segment(NetworkKind.RAIL)
    mask, count = bridge_coverage([rail, segment(NetworkKind.STOP_LINE),
                                 segment(NetworkKind.RAIL, SegmentMode.GROUND)], 100, (0, 0, 100, 100))
    assert count == {"footprint": 0, "rail_centerline": 1}
    assert mask.getpixel((50, 50)) == 255
    assert mask.getpixel((50, 40)) == 0
    assert mask.getpixel((5, 50)) == 0


def test_native_bridge_footprint_preserves_asymmetric_outline():
    road = segment(NetworkKind.ROAD, footprint=Polygon.from_dict([[10, 48], [90, 48], [90, 60], [10, 60]]))
    mask, count = bridge_coverage([road], 100, (0, 0, 100, 100))
    assert count["footprint"] == 1
    assert mask.getpixel((50, 43)) > 250
    assert mask.getpixel((50, 55)) < 5


def test_missing_road_outline_and_invalid_rail_width_are_explicit_errors():
    with pytest.raises(ValueError, match="占地轮廓"):
        bridge_coverage([segment(NetworkKind.ROAD)], 100, (0, 0, 100, 100))
    with pytest.raises(ValueError, match="宽度"):
        bridge_coverage([segment(NetworkKind.RAIL, width=0)], 100, (0, 0, 100, 100))
