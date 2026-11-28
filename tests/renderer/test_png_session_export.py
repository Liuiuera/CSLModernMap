import io
import logging
import threading

from PIL import Image
import pytest

from csmodernmap import gui_render_worker
from csmodernmap.cmm import DocumentSession
from csmodernmap.guir_backend import GuirBackend
from csmodernmap.winui_engine import EngineServer, LoopScheduler
from test_surface_model import _document


def setup_engine(tmp_path):
    scheduler = LoopScheduler()
    backend = GuirBackend(scheduler, settings_dir=tmp_path)
    backend.document = _document()
    backend.session = DocumentSession(backend.document)
    backend.source_file = str(tmp_path / "city.cmm.gz")
    engine = EngineServer(io.StringIO(), io.StringIO(), backend)
    engine.scheduler = scheduler
    events = []
    engine.event = lambda name, payload: events.append((name, payload))
    return engine, events


def request(engine, target):
    return engine.dispatch("export", {"documentEpoch": engine._document_epoch,
        "requestId": "png-test", "context": "normal", "format": "PNG", "target": str(target)})


@pytest.mark.parametrize("stage", ["rendering", "encoding", "city", "error"])
def test_local_png_cancel_holds_busy_and_discards_output(tmp_path, monkeypatch, stage):
    engine, events = setup_engine(tmp_path)
    backend = engine.backend
    entered, release = threading.Event(), threading.Event()
    original = backend.session
    def render(job, *, session, progress, check_cancel):
        assert session is not original and session.document.nodes is original.document.nodes
        assert job["ui"]["accurate_intersections"] is True
        if stage != "encoding":
            entered.set()
            assert release.wait(10)
        if stage == "error":
            raise OSError("render failed")
        check_cancel()
        return {"image": Image.new("RGB", (32, 24)), "size": 32}
    monkeypatch.setattr(gui_render_worker, "run_render_task", render)
    original_export = __import__('csmodernmap.export', fromlist=['export_png']).export_png
    def encode(image, path):
        if stage == "encoding":
            entered.set()
            assert release.wait(10)
        return original_export(image, path)
    monkeypatch.setattr("csmodernmap.guir_backend.export_png", encode)
    target = tmp_path / "map.png"
    try:
        assert request(engine, target)["started"]
        assert entered.wait(5)
        if stage == "city":
            from pathlib import Path
            city = Path(__file__).parents[1] / "fixtures" / "tiny-city.cmm"
            assert engine.dispatch("openDocument", {"path": city})["started"]
            backend._open_thread.join(5)
            engine.scheduler.pump()
            assert backend.source_file == str(city)
        elif stage != "error":
            answer = engine.dispatch("cancel", {})
            assert answer["cancelRequested"] and not answer["cancelled"]
        assert backend.busy
        assert not request(engine, tmp_path / "second.png")["started"]
        release.set()
        thread = backend._local_render_thread
        thread.join(10)
        assert not thread.is_alive()
        engine.scheduler.pump()
        assert not backend.busy
        assert not target.exists()
        assert not list(tmp_path.glob(".csl-png-*"))
        assert any(name == ("error" if stage == "error" else "exportCancelled") for name, _ in events)
        assert not any(name == "exportDone" for name, _ in events)
    finally:
        release.set()
        backend.close()
        engine._preview_files.cleanup()


def test_session_png_export_can_be_repeated(tmp_path, monkeypatch):
    engine, events = setup_engine(tmp_path)
    backend = engine.backend
    def render(job, *, session, progress, check_cancel):
        assert session.document.nodes is backend.session.document.nodes
        return {"image": Image.new("RGB", (32, 24), "red"), "size": 32}
    monkeypatch.setattr(gui_render_worker, "run_render_task", render)
    try:
        for target in (tmp_path / "map.png", tmp_path / "again.png"):
            assert request(engine, target)["started"]
            thread = backend._local_render_thread
            thread.join(10)
            assert not thread.is_alive()
            engine.scheduler.pump()
            assert target.exists()
        assert [data["path"] for name, data in events if name == "exportDone"] == [
            str(tmp_path / "map.png"), str(tmp_path / "again.png")]
        assert not list(tmp_path.glob(".csl-png-*"))
    finally:
        backend.close()
        engine._preview_files.cleanup()


def test_cleanup_failure_does_not_break_cancel(tmp_path, monkeypatch, caplog):
    engine, _ = setup_engine(tmp_path)
    backend = engine.backend
    directory = tmp_path / "locked"
    directory.mkdir()
    backend._png_tempdir = directory
    original = __import__('shutil').rmtree
    def denied(path, *args, **kwargs):
        if path == directory:
            raise PermissionError("locked")
        return original(path, *args, **kwargs)
    monkeypatch.setattr("csmodernmap.guir_backend.shutil.rmtree", denied)
    try:
        with caplog.at_level(logging.WARNING):
            backend.cancel()
        assert not backend.busy
        assert str(directory) in caplog.text and "locked" in caplog.text
    finally:
        backend.close()
        engine._preview_files.cleanup()


def test_tile_session_excludes_preview_caches_and_document_backlink():
    import pickle
    from csmodernmap.renderer import Renderer
    from style_fixtures import pixel_style
    from csmodernmap.preview_road_order import VERSION
    session = DocumentSession(_document())
    session.document._session = session
    session.service_cache.put("preview_only", {}, lambda: None)
    session.road_order(accurate=True)
    renderer = Renderer(pixel_style({}), 64, 64)
    renderer._active_session = session
    tile = renderer._tile_session()
    restored = pickle.loads(pickle.dumps(tile))
    assert restored.service_cache.get("preview_only", {}) is None
    assert not hasattr(restored.document, "_session")
    assert restored.service_cache.get("road_order", {"version": VERSION, "accurate": True}) is not None


def test_auto_small_png_preserves_explicit_parallelism_and_memory_fallback(monkeypatch):
    from csmodernmap import renderer as pipeline
    from csmodernmap.renderer import Renderer
    from style_fixtures import pixel_style
    monkeypatch.setattr(pipeline.os, "cpu_count", lambda: 16)
    monkeypatch.setattr(pipeline, "total_memory_gb", lambda: 32)
    renderer = Renderer(pixel_style({}), 4096, 4096)
    renderer.parallel_workers = 0
    assert renderer._resolve_parallel_workers() == 1
    renderer.parallel_workers = 4
    assert renderer._resolve_parallel_workers() == 4
    renderer.parallel_workers = 0
    renderer.tile_size = 1024
    assert renderer._resolve_parallel_workers() == 15
    large = Renderer(pixel_style({}), 8192, 8192)
    large.parallel_workers = 0
    assert large._resolve_parallel_workers() == 15
    small = Renderer(pixel_style({}), 64, 64)
    monkeypatch.setattr(pipeline, "SAFE_FULL_IMAGE_PEAK", 0)
    tiled = []
    def render_tiles(**kwargs):
        tiled.append(True)
        return Image.new("RGB", (64, 64))
    monkeypatch.setattr(small, "_render_tiled", render_tiles)
    small.render(_document(), aa_downsample=2)
    assert tiled == [True]
