import copy
import io
import json
from pathlib import Path

import pytest

from csmodernmap.winui_engine import EngineServer
from test_rmp_editor_browser import sample_scene


class Backend:
    has_document = True
    busy = False
    local_render_active = False
    _local_png_active = False
    last_rmp_snapshot = None

    def __init__(self, source):
        self.source_file = str(source)
        self.city_info = {"name": "测试城市"}
        self.options = {"pure": True, "types": ["Metro"], "stations": True, "line_ids": [1]}
        self.job = None
        self.last_diagnostics = {}
        self.rmp_cancel_calls = 0

    def transit_options(self, metro):
        assert metro
        return self.options

    def capture_png_export_job(self, context, *, metro_options):
        assert context == "metro"
        return copy.deepcopy({"file": self.source_file, "opts": metro_options, "ui": {"rotation": 0}})

    def generate_rmp_in_engine(self, job, callbacks):
        self.job = job
        return True

    def cancel_rmp_generation(self):
        self.rmp_cancel_calls += 1

    def cancel(self):
        pass

    def cancel_export(self):
        pass


def events(stream):
    return [json.loads(line) for line in stream.getvalue().splitlines()]


@pytest.mark.parametrize("needs_adjustment", [False, True])
def test_rmp_request_freezes_job_and_returns_independent_project(tmp_path, needs_adjustment):
    source = tmp_path / "city.mmap"
    source.write_bytes(b"source fingerprint fixture")
    backend = Backend(source)
    stream = io.StringIO()
    server = EngineServer(io.StringIO(), stream, backend)
    try:
        assert server.dispatch("prepareRmpProject", {"requestId": "edit"}) == {"started": True}
        backend.options["line_ids"].clear()
        assert backend.job["opts"]["line_ids"] == [1]
        assert set(backend.job) == {"file", "opts"}
        backend.last_rmp_snapshot = sample_scene()
        backend.last_diagnostics = {"stats": {"transit_layout": {"needs_visual_adjustment": needs_adjustment}}}
        server._rmp_done()
        frame = events(stream)[-1]
        assert frame["event"] == "rmpProjectReady"
        assert frame["payload"]["requestId"] == "edit"
        draft = frame["payload"]["draft"]
        assert draft["project"]["version"] == 77
        assert draft["metadata"]["cityName"] == "测试城市"
        assert draft["metadata"]["needsVisualAdjustment"] is needs_adjustment
        assert draft["project"]["cmmDraft"]["lines"][0]["chains"] == [["a", "b"]]
        assert "width" not in draft["project"]["cmmDraft"]["lines"][0]
        assert len(draft["metadata"]["layoutFingerprint"]) == 64
        assert not any(frame["event"] == "metroReady" for frame in events(stream))
    finally:
        server._preview_files.cleanup()


@pytest.mark.parametrize("saved", [None, (3, False), (3, True), (1, True)])
def test_real_rmp_is_independent_of_saved_map_watermark(tmp_path, monkeypatch, saved):
    from csmodernmap.cmm import DocumentSession, load_document
    from csmodernmap.guir_backend import GuirBackend
    from csmodernmap.gui_render_worker import run_render_task, WatermarkLayer
    from csmodernmap.rmp_generation import generate_rmp
    from csmodernmap.winui_engine import LoopScheduler

    if saved is not None:
        (tmp_path / "settings.json").write_text(json.dumps({
            "version": saved[0], "values": {"layer_watermark": saved[1]},
        }), encoding="utf-8")
    backend = GuirBackend(LoopScheduler(), settings_dir=tmp_path)
    source = Path(__file__).parent / "fixtures/basan_rings.mmap"
    backend.source_file = str(source)
    backend.document = load_document(source)
    backend.session = DocumentSession(backend.document)
    server = EngineServer(io.StringIO(), io.StringIO(), backend)
    enabled = saved is not None and saved[1]
    calls = []
    draw_watermark = WatermarkLayer.draw_on_image
    render_watermark = WatermarkLayer.render

    def record_watermark(*args, **kwargs):
        calls.append(True)
        return draw_watermark(*args, **kwargs)

    monkeypatch.setattr(WatermarkLayer, "draw_on_image", record_watermark)
    def record_render(*args, **kwargs):
        calls.append(True)
        return render_watermark(*args, **kwargs)

    monkeypatch.setattr(WatermarkLayer, "render", record_render)
    try:
        for _ in range(2):
            _, job = server._rmp_context()
            result = generate_rmp(backend.document, job["opts"])
            assert result["stations"] and result["lines"]
            assert backend.layer_visibility["watermark"] == enabled
        assert not calls
        job = backend.capture_png_export_job("normal", output_profile={"size": 512, "rotation": 0})
        run_render_task(job, mapdata_cache={str(source): backend.document})
        assert bool(calls) == enabled
        assert backend.layer_visibility["watermark"] == enabled
    finally:
        server._preview_files.cleanup()
        backend.close()


def test_cancelled_and_stale_rmp_requests_resolve_with_error(tmp_path):
    backend = Backend(tmp_path / "city.mmap")
    stream = io.StringIO()
    server = EngineServer(io.StringIO(), stream, backend)
    try:
        server.dispatch("prepareRmpProject", {"requestId": "cancelled"})
        assert server.dispatch("cancel", {"rmpRequestId": "previous"}) == {"cancelled": False}
        assert backend.rmp_cancel_calls == 0
        assert server.dispatch("cancel", {})["cancelled"]
        assert backend.rmp_cancel_calls == 1
        frame = events(stream)[-1]
        assert frame["event"] == "rmpProjectError"
        assert frame["payload"]["requestId"] == "cancelled"
        assert frame["payload"]["cancelled"] is True
        server.dispatch("prepareRmpProject", {"requestId": "stale"})
        server._document_epoch += 1
        server._rmp_done()
        assert events(stream)[-1]["event"] == "rmpProjectError"
        assert events(stream)[-1]["payload"]["requestId"] == "stale"
    finally:
        server._preview_files.cleanup()


def test_draft_ignores_retired_appearance_settings(tmp_path):
    backend = Backend(tmp_path / "city.mmap")
    server = EngineServer(io.StringIO(), io.StringIO(), backend)
    try:
        key, job = server._rmp_context()
        backend.options.update(
            line_width=30, station_diameter=40, station_names_enabled=False,
            station_names_mode="none", station_name_color="#FF0000",
            station_line_ids=[999], background="#112233", facilities_scale=3,
            title_enabled=True, legend_enabled=True, ann_credit=True, size=8192,
            schematic_options={"adaptive_scale": True, "adaptive_strength": .85, "scale_debug": True},
        )
        changed_key, changed_job = server._rmp_context()
        assert changed_key == key and changed_job == job
        options = job["opts"]
        assert not {"stations", "station_names_enabled", "station_names_mode", "background",
                    "title_enabled", "legend_enabled", "capture_rmp"} & options.keys()
        backend.options["spacing"] = 345
        assert server._rmp_context()[0] != key
    finally:
        server._preview_files.cleanup()


def test_rmp_draft_exposes_styling_and_adapter_warnings(tmp_path, monkeypatch):
    from csmodernmap.external_edit import rmp_graph
    source = tmp_path / "city.mmap"
    source.write_bytes(b"source")
    backend = Backend(source)
    backend.last_rmp_snapshot = sample_scene()
    backend.last_diagnostics = {"stats": {"transit_layout": {"water": {"status": "degraded"}}}}
    def fail(*args):
        raise rmp_graph.RmpWaterError("cannot split")
    monkeypatch.setattr(rmp_graph, "_water_faces", fail)
    server = EngineServer(io.StringIO(), io.StringIO(), backend)
    try:
        draft = server._rmp_draft("key", {"file": str(source)})
        assert len(draft["metadata"]["warnings"]) == 2
        assert draft["project"]["cmmDraft"]["stations"]
        assert draft["project"]["cmmDraft"]["lines"]
        assert draft["project"]["cmmDraft"]["facilities"]
        assert draft["project"]["cmmDraft"]["water"] == []
    finally:
        server._preview_files.cleanup()
