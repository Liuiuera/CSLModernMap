"""Preview completeness and ownership are verified against actual bundles."""
import json
from collections import deque
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from csmodernmap.cmm import DocumentSession
from csmodernmap.preview_scene import PreviewSceneBundle, PreviewSceneCompiler, PreviewSceneServer
from csmodernmap._paths import resource_dir
from csmodernmap.style import load_style
from preview_fixtures import facility_document


@pytest.mark.parametrize("accurate", [False, True])
def test_base_full_signatures_and_disabled_areas_do_not_compute(accurate):
    session = DocumentSession(facility_document())
    style = load_style(str(resource_dir("styles") / "modern.yaml")).merged({"roads": {"accurate_intersections": accurate}})
    base = PreviewSceneCompiler.compile_full(session, style, 1, options={"phase": "base"}, visibility={"poi_area": False})
    try:
        assert base.manifest["completion"] == "base"
        assert "pois" not in base.manifest["sources"]
        full = PreviewSceneCompiler.compile_full(session, style, 1, previous=base,
            options={"phase": "full"}, visibility={"poi_area": False})
        try:
            assert full.manifest["completion"] == "complete"
            assert "pois" in full.manifest["sources"]
            assert "poiArea" not in full.manifest["surfaceFields"]
            assert "landUse" not in full.manifest["sources"]
            assert "landUse" not in base.manifest["sources"]
            for domain in ("buildings",):
                assert full.manifest["sourceSignatures"][domain] == base.manifest["sourceSignatures"][domain]
                assert full.manifest["sourceRevisions"][domain] == base.manifest["sourceRevisions"][domain]
                filename = base.manifest["sources"][domain].split("?")[0]
                assert (base.root / filename).read_bytes() == (full.root / filename).read_bytes()
            assert (full.manifest["sourceSignatures"]["roads"] != base.manifest["sourceSignatures"]["roads"]) is accurate
            cold = PreviewSceneCompiler.compile_full(session, style, 1, visibility={"poi_area": False})
            try:
                for domain in ("roads", "buildings"):
                    filename = cold.manifest["sources"][domain].split("?")[0]
                    assert (cold.root / filename).read_bytes() == (full.root / filename).read_bytes()
            finally:
                cold.close()
        finally:
            full.close()
    finally:
        base.close()


def test_ack_releases_old_snapshots_after_export_reader_finishes(tmp_path):
    server = PreviewSceneServer(tmp_path)
    def bundle(name, phase):
        root = tmp_path/name
        root.mkdir()
        (root/"manifest.json").write_text("{}")
        return PreviewSceneBundle(root, "same", 1, {"completion": phase})
    base, full = bundle("base", "base"), bundle("full", "complete")
    try:
        server.replace_bundle(base)
        base.retain()
        server.replace_bundle(full)
        assert not server.acknowledge_bundle("base")
        assert server.acknowledge_bundle("full")
        assert base.root.exists()
        base.release()
        assert not base.root.exists()
        assert full.root.exists()
        assert list(server._bundles) == ["full"]
    finally:
        server.close()
    assert not full.root.exists()


def test_cancel_during_compile_removes_temporary_bundle(monkeypatch, tmp_path):
    session = DocumentSession(facility_document())
    root = tmp_path/"cancelled"
    root.mkdir()
    monkeypatch.setattr("csmodernmap.preview_scene.tempfile.mkdtemp", lambda **_: str(root))
    class Cancelled(Exception):
        pass
    calls = 0
    def check():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise Cancelled()
    with pytest.raises(Cancelled):
        PreviewSceneCompiler.compile_full(session, load_style(str(resource_dir("styles") / "modern.yaml")), 1, options={"check_cancel": check})
    assert not root.exists()


@pytest.mark.parametrize("change", ["city", "settings", "cancel"])
def test_queued_preview_cannot_publish_after_city_settings_or_cancel(monkeypatch, tmp_path, change):
    from csmodernmap.guir_backend import GuirBackend
    backend = GuirBackend(SimpleNamespace(call_soon=lambda *_: None,
        call_later=lambda *_: None, cancel=lambda *_: None), settings_dir=tmp_path)
    backend.document = facility_document()
    backend.session = DocumentSession(backend.document)
    backend.start_preview_server()
    queued, built, ready, errors = [], [], [], []
    backend._post = queued.append
    compile_original = PreviewSceneCompiler.compile_full
    def compile_record(*args, **kwargs):
        result = compile_original(*args, **kwargs)
        built.append(result)
        return result
    monkeypatch.setattr(PreviewSceneCompiler, "compile_full", compile_record)
    try:
        callbacks = {"scene_ready": lambda bundle, _url: ready.append(bundle), "scene_error": errors.append}
        assert backend.compile_preview_scene(callbacks)
        backend._preview_compile_thread.join(timeout=10)
        assert not backend._preview_compile_thread.is_alive()
        old = list(built)
        assert len(old) == 2
        if change == "city":
            backend.document = facility_document(area_id=-3)
            backend.session = DocumentSession(backend.document)
        if change == "cancel":
            backend.invalidate_preview_compile()
        else:
            backend.accurate_intersections = False
            assert backend.compile_preview_scene(callbacks)
            backend._preview_compile_thread.join(timeout=10)
            assert not backend._preview_compile_thread.is_alive()
        for deliver in queued:
            deliver()
        assert not errors
        assert all(not bundle.root.exists() for bundle in old)
        if change == "cancel":
            assert not ready
        else:
            assert len(ready) == 2
            assert ready[-1].manifest["completion"] == "complete"
            assert backend._preview_bundle_session is backend.session
            assert backend._preview_server.acknowledge_bundle(ready[-1].root.name)
            assert not ready[0].root.exists()
    finally:
        backend.close()
    assert all(not bundle.root.exists() for bundle in built)


def progress_document():
    from csmodernmap.cmm.loader import load_document
    return load_document(Path(__file__).parent / "fixtures/tiny-city.cmm")


@pytest.fixture
def progress_backend(tmp_path):
    from csmodernmap.guir_backend import GuirBackend
    queued = deque()
    scheduler = SimpleNamespace(call_soon=queued.append,
        call_later=lambda *_: None, cancel=lambda *_: None)
    backend = GuirBackend(scheduler, settings_dir=tmp_path)
    backend.start_preview_server()
    events = []
    callbacks = {
        "progress": lambda pct, message: events.append(("progress", pct, message)),
        "scene_ready": lambda bundle, url: events.append(("scene", bundle.manifest["completion"])),
        "error": lambda error: events.append(("error", str(error))),
        "scene_error": lambda error: events.append(("error", str(error))),
    }
    def drain():
        while queued:
            queued.popleft()()
    yield backend, events, callbacks, drain
    backend.close()


def join_worker(thread):
    thread.join(timeout=15)
    assert not thread.is_alive()


def test_open_and_refresh_have_separate_monotonic_progress(monkeypatch, tmp_path, progress_backend):
    backend, events, callbacks, drain = progress_backend
    city = tmp_path / "city.cmm"
    city.touch()
    def load(_path, progress, **kwargs):
        progress(0, "Reading")
        progress(1, "Loaded")
        return progress_document()
    monkeypatch.setattr("csmodernmap.guir_backend.load_document", load)
    assert backend.open_file(city, callbacks)
    join_worker(backend._open_thread)
    drain()
    join_worker(backend._preview_compile_thread)
    drain()
    assert not [e for e in events if e[0] == "error"]
    values = [e[1] for e in events if e[0] == "progress"]
    assert values == sorted(values)
    assert values[0] == 0 and values[-1] == 95
    assert {20, 30, 55, 95} <= set(values)
    assert [e[1] for e in events if e[0] == "scene"] == ["base", "complete"]

    events.clear()
    assert backend.compile_preview_scene(callbacks, force=True)
    join_worker(backend._preview_compile_thread)
    drain()
    values = [e[1] for e in events if e[0] == "progress"]
    assert values == sorted(values)
    assert values[0] == 0 and values[-1] == 95
    assert [e[1] for e in events if e[0] == "scene"] == ["complete"]
    assert not [e for e in events if e[0] == "error"]


@pytest.mark.parametrize("fail_detail", [False, True])
def test_full_result_or_detail_error_delivered_without_base_ack(progress_backend, monkeypatch, fail_detail):
    backend, events, callbacks, drain = progress_backend
    backend.document = progress_document()
    backend.session = DocumentSession(backend.document)
    callbacks["scene_detail_error"] = lambda base, error: events.append(("detail", str(error)))
    compile_scene = PreviewSceneCompiler.compile_full
    def compile_stage(*args, **kwargs):
        if fail_detail and kwargs["options"]["phase"] == "full":
            raise ValueError("detail failed")
        return compile_scene(*args, **kwargs)
    monkeypatch.setattr(PreviewSceneCompiler, "compile_full", compile_stage)
    assert backend.compile_preview_scene(callbacks)
    join_worker(backend._preview_compile_thread)
    drain()
    result = [event for event in events if event[0] in {"scene", "detail"}]
    assert result == [("scene", "base"), ("detail", "detail failed") if fail_detail else ("scene", "complete")]


def test_cold_refresh_reserves_base_and_final_presentation(progress_backend):
    backend, events, callbacks, drain = progress_backend
    backend.document = progress_document()
    backend.session = DocumentSession(backend.document)
    assert backend.compile_preview_scene(callbacks)
    join_worker(backend._preview_compile_thread)
    drain()
    values = [e[1] for e in events if e[0] == "progress"]
    assert values == sorted(values)
    assert values[0] == 0 and values[-1] == 95 and 35 in values
    assert not [e for e in events if e[0] == "error"]


@pytest.mark.parametrize("failed", [False, True])
def test_cancel_discards_queued_open_callbacks(monkeypatch, tmp_path, progress_backend, failed):
    backend, events, callbacks, drain = progress_backend
    city = tmp_path / "city.cmm"
    city.touch()
    def load(_path, progress, **kwargs):
        progress(0, "Reading")
        if failed:
            raise ValueError("broken city")
        progress(1, "Loaded")
        return progress_document()
    monkeypatch.setattr("csmodernmap.guir_backend.load_document", load)
    assert backend.open_file(city, callbacks)
    join_worker(backend._open_thread)
    backend.cancel()
    drain()
    assert events == []
    assert backend.document is None and backend.session is None

    monkeypatch.setattr("csmodernmap.guir_backend.load_document", lambda *a, **kw: progress_document())
    assert backend.open_file(city, callbacks)
    join_worker(backend._open_thread)
    drain()
    join_worker(backend._preview_compile_thread)
    drain()
    assert [e[1] for e in events if e[0] == "scene"] == ["base", "complete"]
    assert not [e for e in events if e[0] == "error"]


def test_cancel_during_whole_file_read_stops_before_preparation(monkeypatch, tmp_path, progress_backend):
    backend, events, callbacks, drain = progress_backend
    city = tmp_path / "city.cmm"
    city.touch()
    started, release = threading.Event(), threading.Event()
    def load(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return progress_document()
    def prepare(*args, **kwargs):
        pytest.fail("cancelled read must not prepare data")
    monkeypatch.setattr("csmodernmap.guir_backend.load_document", load)
    monkeypatch.setattr("csmodernmap.guir_backend._build_transit_line_catalog", prepare)
    assert backend.open_file(city, callbacks)
    try:
        assert started.wait(5)
        backend.cancel()
    finally:
        release.set()
        join_worker(backend._open_thread)
    drain()
    assert events == [] and backend.document is None


@pytest.mark.parametrize("count", [0, 10])
def test_catalog_counts_skipped_lines_and_throttles(monkeypatch, count):
    from csmodernmap.guir_backend import _build_transit_line_catalog
    clock = [0.0]
    def now():
        clock[0] += 0.11
        return clock[0]
    monkeypatch.setattr("csmodernmap.guir_backend.time.monotonic", now)
    lines = [SimpleNamespace(type="unsupported") for _ in range(count)]
    session = SimpleNamespace(_legacy_view=lambda: SimpleNamespace(transports=lines))
    events = []
    result = _build_transit_line_catalog(session,
        progress=lambda pct, text: events.append((clock[0], pct, text)))
    assert result == []
    assert events[0][1] == 0 and events[-1][1] == 100
    assert f"{count}/{count}" in events[-1][2]
    if count:
        assert events[1][1] == 50
        assert len(events) < count
        # The final count is unconditional; only intermediate reports are throttled.
        assert all(b[0] - a[0] >= 0.3 for a, b in zip(events[1:-2], events[2:-1]))


def test_cancel_discards_queued_compile_progress(progress_backend):
    backend, events, callbacks, drain = progress_backend
    backend.document = progress_document()
    backend.session = DocumentSession(backend.document)
    assert backend.compile_preview_scene(callbacks)
    join_worker(backend._preview_compile_thread)
    backend.cancel()
    drain()
    assert events == []


def test_catalog_feedback_preserves_lines_and_honors_cancellation():
    from csmodernmap.guir_backend import _build_transit_line_catalog
    document = facility_document()
    session = DocumentSession(document)
    expected = _build_transit_line_catalog(session)
    assert expected
    events = []
    assert _build_transit_line_catalog(session, progress=lambda *args: events.append(args)) == expected
    assert events[0][0] == 0 and events[-1][0] == 100
    assert "1/1" in events[-1][1]
    class Cancelled(Exception):
        pass
    def cancel():
        if len(events) > 1:
            raise Cancelled()
    events.clear()
    with pytest.raises(Cancelled):
        _build_transit_line_catalog(session, progress=lambda *args: events.append(args), check_cancel=cancel)
    assert events[-1][0] == 50
