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
            assert backend._local_render_thread.is_alive()
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


def test_png_runs_while_old_preview_thread_is_blocked(tmp_path, monkeypatch):
    engine, events = setup_engine(tmp_path)
    backend = engine.backend
    release = threading.Event()
    backend._preview_compile_thread = threading.Thread(target=release.wait)
    backend._preview_compile_thread.start()
    original = backend.session
    def render(job, *, session, progress, check_cancel):
        assert session is not original
        assert session.document.nodes is original.document.nodes
        return {"image": Image.new("RGB", (32, 24), "red")}
    monkeypatch.setattr(gui_render_worker, "run_render_task", render)
    try:
        target = tmp_path / "map.png"
        assert request(engine, target)["started"]
        backend._local_render_thread.join(5)
        engine.scheduler.pump()
        assert any(name == "exportDone" for name, _ in events)
        assert backend._preview_compile_thread.is_alive()
        with Image.open(target) as image:
            assert image.getpixel((0, 0)) == (255, 0, 0)
        assert not list(tmp_path.glob(".csl-png-*"))
    finally:
        release.set()
        backend.close()
        engine._preview_files.cleanup()


@pytest.mark.parametrize("late_error", [False, True])
def test_latest_city_opens_while_previous_read_is_still_running(tmp_path, monkeypatch, late_error):
    engine, events = setup_engine(tmp_path)
    backend = engine.backend
    entered, release = threading.Event(), threading.Event()
    def load(path, **kwargs):
        if path.endswith("B.cmm"):
            entered.set()
            assert release.wait(10)
            if late_error:
                raise ValueError("late B error")
        return _document()
    monkeypatch.setattr("csmodernmap.guir_backend.load_document", load)
    try:
        assert engine.dispatch("openDocument", {"path": tmp_path/"B.cmm"})["started"]
        old = backend._open_thread
        assert entered.wait(5)
        assert not backend.has_document
        assert engine.dispatch("openDocument", {"path": tmp_path/"C.cmm"})["started"]
        backend._open_thread.join(5)
        engine.scheduler.pump()
        assert backend.source_file == str(tmp_path/"C.cmm") and old.is_alive()
        release.set()
        old.join(5)
        engine.scheduler.pump()
        assert [data["documentEpoch"] for name, data in events if name == "meta"] == [2]
        assert not any(name == "documentOpenFailed" for name, _ in events)
        assert backend.source_file == str(tmp_path/"C.cmm")
    finally:
        release.set()
        backend.close()
        engine._preview_files.cleanup()


def test_compatibility_image_can_finish_before_old_export(tmp_path, monkeypatch):
    engine, events = setup_engine(tmp_path)
    backend = engine.backend
    entered, release, preview_done = threading.Event(), threading.Event(), threading.Event()
    def render(job, *, session, progress, check_cancel):
        if not entered.is_set():
            entered.set()
            assert release.wait(10)
        else:
            preview_done.set()
        return {"image": Image.new("RGB", (7, 5), "green")}
    monkeypatch.setattr(gui_render_worker, "run_render_task", render)
    try:
        assert request(engine, tmp_path/"export.png")["started"]
        assert entered.wait(5)
        backend.preview_enabled = True
        assert engine.dispatch("renderCompatibilityPreview", {
            "documentEpoch": 0, "context": "normal", "requestId": "fallback"})["started"]
        assert preview_done.wait(5)
        import time
        deadline = time.monotonic() + 5
        while not any(name == "compatibilityPreviewReady" for name, _ in events):
            assert time.monotonic() < deadline
            engine.scheduler.wakeup.wait(0.05)
            engine.scheduler.pump()
        assert backend.busy
        assert engine._compatibility_files["fallback"].is_file()
        assert engine.dispatch("releaseCompatibilityPreview", {"requestId": "fallback"})["released"]
    finally:
        release.set()
        if backend._local_render_thread:
            backend._local_render_thread.join(5)
        engine.scheduler.pump()
        backend.close()
        engine._preview_files.cleanup()


@pytest.mark.parametrize("kind", ["HTML", "RMG"])
def test_switch_cancels_unpublished_export_and_keeps_existing_file(tmp_path, monkeypatch, kind):
    from pathlib import Path
    from types import SimpleNamespace
    engine, events = setup_engine(tmp_path)
    backend = engine.backend
    entered, release = threading.Event(), threading.Event()
    target = tmp_path/"out" if kind == "RMG" else tmp_path/"city.html"
    if kind == "HTML":
        target.write_text("previous export")
        def write(bundle, city, path, **kwargs):
            Path(path).write_text("unpublished HTML")
            entered.set()
            assert release.wait(10)
        monkeypatch.setattr("csmodernmap.web_export_v2.export_preview_bundle", write)
    else:
        def write(snapshot, request, **kwargs):
            folder = request.output_root / "export"
            folder.mkdir()
            (folder/"manifest.json").write_text("{}")
            entered.set()
            assert release.wait(10)
            return SimpleNamespace(directory=folder, files=(), warnings=(), skipped_line_count=0)
        monkeypatch.setattr("csmodernmap.external_edit.export_rmg", write)
    try:
        assert engine.dispatch("export", {"documentEpoch": 0, "requestId": "old-export", "format": kind,
            "context": "metro" if kind == "RMG" else "normal", "target": str(target)})["started"]
        assert entered.wait(6)
        city = Path(__file__).parents[1]/"fixtures"/"tiny-city.cmm"
        assert engine.dispatch("openDocument", {"path": city})["started"]
        backend._open_thread.join(5)
        engine.scheduler.pump()
        assert backend.source_file == str(city)
        release.set()
        import time
        deadline = time.monotonic() + 5
        while not any(name == "exportCancelled" for name, _ in events):
            assert time.monotonic() < deadline
            engine.scheduler.wakeup.wait(0.05)
            engine.scheduler.pump()
        assert not any(name in {"externalExportDone", "exportDone"} for name, _ in events)
        if kind == "HTML":
            assert target.read_text() == "previous export"
            assert not list(tmp_path.glob(".csl-html-*"))
        else:
            assert not list(target.iterdir())
    finally:
        release.set()
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
