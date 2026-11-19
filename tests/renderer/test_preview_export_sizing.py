"""Explicit pixel sizes replace measurements borrowed from the preview viewport."""
import pytest
from csmodernmap.cmm import (DisplayName, Direction, GeomKind, NetworkNode, Route,
    Stop, StopKind, TransitKind, TransitLine, Vec3)
from csmodernmap.ui_contracts import ContextMode
from test_png_session_export import setup_engine
from test_surface_model import _document

# Historic request retained only as a rejection fixture.
SIZING = {"viewport": [1280, 720], "radius": 4, "stroke": 1, "badge_size": 17.5,
          "fill": "#fff", "outline": "#39424C"}
UI = dict(elementScale=1, rotation=0, style_overrides={}, merge_overrides={},
    merge_enabled=True, merge_distance=48, building_merge_enabled=True,
    transit_merge_enabled=True, block_gap_m=24., hub_core_m=80.,
    poi_player_names=True, poi_important_names=True)

def transit_document():
    document = _document()
    positions = [Vec3(-5500, 0, -1200), Vec3(0, 0, 2000), Vec3(5500, 0, -1200)]
    document.nodes = [NetworkNode(i, pos) for i, pos in enumerate(positions, 1)]
    document.stops = [Stop(i, i, StopKind.METRO, DisplayName(f"测试车站 {i}", "custom"), pos,
        serving_line_ids=[1]) for i, pos in enumerate(positions, 1)]
    document.transit_lines = [TransitLine(1, "测试线路", TransitKind.METRO, (20, 130, 210, 255))]
    document.routes = [Route(1, 1, Direction.OUTBOUND, [1, 2, 3], False, GeomKind.STOP_DIRECT)]
    return document

@pytest.mark.parametrize('value', [SIZING, None, {}])
def test_viewport_measurements_are_rejected_at_export_boundary(tmp_path, value):
    engine, _ = setup_engine(tmp_path)
    try:
        with pytest.raises(ValueError, match='retired'):
            engine._capture_output_snapshot(ContextMode.TRANSIT, 'PNG', {'previewSizing': value})
        snapshot = engine.snapshot()
        assert 'transport.fit_preview' not in snapshot['fields']
    finally:
        engine.backend.close(); engine._preview_files.cleanup()


def test_export_captures_explicit_scale_without_browser_measurements(tmp_path):
    engine, _ = setup_engine(tmp_path)
    try:
        request = {'output': {'size': 8192, 'elementScale': .75}}
        job, _, _ = engine._capture_output_snapshot(ContextMode.TRANSIT, 'PNG', request)
        request['output']['elementScale'] = 3
        assert job['ui']['elementScale'] == .75
        assert 'preview_sizing' not in job['ui']
        assert 'station_size' not in job['opts']
    finally:
        engine.backend.close(); engine._preview_files.cleanup()
