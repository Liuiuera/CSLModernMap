"""Contract checks for the WinUI process boundary."""

from __future__ import annotations

import io
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse
from urllib.request import url2pathname

import pytest

from csmodernmap.winui_engine import EngineServer, sanitize_theme_data
from csmodernmap.guir_backend import GuirBackend
from csmodernmap.ui_contracts import ContextMode


class _Backend:
    def __init__(self) -> None:
        self.closed = False
        self._local_png_active = False

    def start_preview_server(self) -> str:
        return "http://127.0.0.1:12345/"

    def close(self) -> None:
        self.closed = True

    def discard_png_result(self) -> None:
        pass


def test_open_announces_epoch_before_synchronous_load_failure(tmp_path):
    from csmodernmap.task_error import TaskFailure, capture_error
    backend = _guir_backend(tmp_path)
    server = EngineServer(io.StringIO(), io.StringIO(), backend)
    events = []
    server.event = lambda name, payload: events.append((name, payload))
    def fail_open(path, callbacks):
        diagnostic = capture_error(ValueError("catalog failed"), stage="openDocument")
        diagnostic["operation"] = "open"
        callbacks["error"](TaskFailure(diagnostic))
        return True
    backend.open_file = fail_open
    try:
        for epoch in (1, 2):
            events.clear()
            assert server.dispatch("openDocument", {"path": "city.cmm"})["started"]
            assert events[0] == ("documentOpening", {"documentEpoch": epoch})
            error = next(payload for name, payload in events if name == "error")
            assert error["documentEpoch"] == epoch
            assert error["operation"] == "open"
    finally:
        backend.close()
        server._preview_files.cleanup()


def _guir_backend(settings_dir):
    scheduler = SimpleNamespace(call_soon=lambda *_: 1, call_later=lambda *_args: 1,
                                cancel=lambda *_args: None)
    return GuirBackend(scheduler, settings_dir=settings_dir)


@pytest.mark.parametrize("game", ["cs1", "cs2"])
def test_snapshot_identifies_loaded_game_for_facility_selection(tmp_path, game):
    from cmm_test_factory import document_from_mapdata
    from csmodernmap.data import MapData

    backend = _guir_backend(tmp_path)
    server = EngineServer(io.StringIO(), io.StringIO(), backend)
    try:
        assert server.snapshot()["sourceGame"] == ""
        backend.document = document_from_mapdata(MapData())
        backend.document.metadata.origin.game = game
        snapshot = server.snapshot()
        assert snapshot["hasDocument"] is True
        assert snapshot["sourceGame"] == game
        backend.document = None
        assert server.snapshot()["sourceGame"] == ""
    finally:
        backend.close()
        server._preview_files.cleanup()


@pytest.mark.parametrize("broken_queue", [False, True])
def test_render_poll_reports_worker_failure_instead_of_staying_busy(broken_queue):
    events = []

    class Scheduler:
        def call_soon(self, callback):
            callback()

        def call_later(self, _delay, _callback):
            raise AssertionError("failed worker must not be polled again")

    class BrokenQueue:
        def get_nowait(self):
            raise OSError("pipe closed")

    backend = GuirBackend.__new__(GuirBackend)
    backend.scheduler = Scheduler()
    backend._callbacks = {"error": lambda error: events.append(("error", str(error))),
                          "busy": lambda value: events.append(("busy", value))}
    backend._poll_job = 1
    backend._result_inbox = BrokenQueue() if broken_queue else queue.Queue()
    backend._proc = SimpleNamespace(is_alive=lambda: False, exitcode=9, pid=42)
    backend._serial = 1
    backend._busy = True
    backend._cancelled = False
    backend._worker_exit_at = time.monotonic() - 2
    backend._png_tempdir = None

    backend._poll()

    assert events[0][0] == "error"
    assert ("读取失败" if broken_queue else "退出码 9") in events[0][1]
    assert events[-1] == ("busy", False)
    assert not backend.busy


def test_cancel_does_not_wait_for_dead_worker_queue_feeder():
    events = []

    class Scheduler:
        def cancel(self, _token):
            pass

        def call_soon(self, callback):
            callback()

    class StalledQueue:
        def __init__(self):
            self.closed = False
            self.join_cancelled = False

        def cancel_join_thread(self):
            self.join_cancelled = True

        def close(self):
            assert self.join_cancelled
            self.closed = True

        def join_thread(self):
            raise AssertionError("cancel must not wait for a blocked queue feeder")

    backend = GuirBackend.__new__(GuirBackend)
    backend.scheduler = Scheduler()
    backend._callbacks = {"busy": lambda value: events.append(value)}
    backend._poll_job = 1
    backend._proc = SimpleNamespace(is_alive=lambda: False)
    backend._job_q = StalledQueue()
    backend._result_q = StalledQueue()
    backend._result_inbox = queue.Queue()
    backend._result_reader_stop = threading.Event()
    backend._local_render_stop = threading.Event()
    backend._local_png_active = False
    backend._web_export_active = False
    backend._preview_revision = 0
    backend._open_revision = 0
    backend._preview_base_presentation = None
    backend._png_tempdir = None
    backend._busy = True
    backend._serial = 0

    backend.cancel()

    assert events == [False]
    assert not backend.busy
    assert backend._job_q is None and backend._result_q is None


def test_in_process_metro_cancel_suppresses_stale_result(monkeypatch, tmp_path):
    from csmodernmap import rmp_generation
    from csmodernmap.winui_engine import LoopScheduler

    scheduler = LoopScheduler()
    backend = GuirBackend(scheduler, settings_dir=tmp_path)
    backend.source_file = "city.cmm.gz"
    backend.document = object()
    completed = []
    backend._callbacks = {"rmp_done": lambda: completed.append(backend.last_rmp_snapshot)}

    def render(document, options, *, progress, cancelled):
        assert document is backend.document
        while not backend._local_render_stop.wait(0.01):
            pass
        progress(0.5, "stopped")

    monkeypatch.setattr(rmp_generation, "generate_rmp", render)
    try:
        assert backend.generate_rmp_in_engine({"opts": {}})
        assert backend.busy
        backend.cancel()
        deadline = time.monotonic() + 2
        while backend.local_render_active and time.monotonic() < deadline:
            scheduler.pump()
            time.sleep(0.01)
        scheduler.pump()
        assert not backend.busy
        assert not backend.local_render_active
        assert completed == []
    finally:
        backend.close()


def test_retired_metro_png_cannot_bypass_project_flow():
    backend = GuirBackend.__new__(GuirBackend)
    request = SimpleNamespace(context="metro", render_snapshot={"file": "city"})
    with pytest.raises(ValueError, match="RMP"):
        backend.render_png_export(request, {}, in_engine=True)


@pytest.mark.skipif(not os.environ.get("CSLMM_METRO_TEST_SAVE"), reason="real city not configured")
def test_real_city_metro_request_completes(tmp_path):
    from csmodernmap.cmm import DocumentSession, load_document
    from csmodernmap.winui_engine import LoopScheduler

    original = Path(os.environ["CSLMM_METRO_TEST_SAVE"])
    save = tmp_path / original.name
    save.write_bytes(original.read_bytes())
    output = io.StringIO()
    scheduler = LoopScheduler()
    backend = GuirBackend(scheduler, settings_dir=tmp_path)
    server = EngineServer(io.StringIO(), output, backend)
    server.scheduler = scheduler
    try:
        server.backend.document = load_document(str(save), validate=False)
        server.backend.session = DocumentSession(document=server.backend.document)
        server.backend.source_file = str(save)
        assert server.dispatch("prepareRmpProject", {"requestId": "real-city"})["started"]
        deadline = time.monotonic() + 90
        started_at = time.monotonic()
        last_progress = -1
        while time.monotonic() < deadline:
            server.scheduler.pump()
            frames = [json.loads(line) for line in output.getvalue().splitlines()]
            if os.environ.get("CSLMM_METRO_TRACE"):
                for frame in frames:
                    if frame.get("event") == "progress":
                        percent = int(frame["payload"]["percent"])
                        if percent // 10 > last_progress:
                            last_progress = percent // 10
                            print(f"{time.monotonic() - started_at:.1f}s {percent}% "
                                  f"{frame['payload']['message']}", flush=True)
            terminal = [frame for frame in frames if frame.get("event") in {"rmpProjectReady", "rmpProjectError", "error"}]
            if terminal:
                assert terminal[-1]["event"] == "rmpProjectReady", terminal[-1]
                assert terminal[-1]["payload"]["draft"]["project"]["cmmDraft"]["stations"]
                break
            time.sleep(0.05)
        else:
            pytest.fail("metro render did not reach a terminal event")
    finally:
        server.backend.close()
        server._preview_files.cleanup()


@pytest.mark.skipif(not os.environ.get("CSLMM_METRO_TEST_SAVE"), reason="real city not configured")
def test_real_city_metro_over_winui_process_protocol(tmp_path):
    original = Path(os.environ["CSLMM_METRO_TEST_SAVE"])
    save = tmp_path / original.name
    save.write_bytes(original.read_bytes())
    root = (Path(__file__).resolve().parents[2] / "dev/CSLModernMapRenderer")
    bootstrap = tmp_path / "isolated_engine.py"
    bootstrap.write_text(
        "import sys\nfrom pathlib import Path\n"
        f"sys.path.insert(0, {str(root)!r})\n"
        "import csmodernmap._paths as paths\n"
        f"paths.writable_dir = lambda: Path({str(tmp_path)!r})\n"
        "if __name__ == '__main__':\n"
        "    from csmodernmap.winui_engine import main\n"
        "    raise SystemExit(main())\n", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-u", str(bootstrap)], cwd=root,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", bufsize=1,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    frames = queue.Queue()
    errors = []
    seen = []
    threading.Thread(target=lambda: [frames.put(json.loads(line)) for line in process.stdout], daemon=True).start()
    threading.Thread(target=lambda: [errors.append(line.rstrip()) for line in process.stderr], daemon=True).start()

    def send(request_id, method, params=None):
        process.stdin.write(json.dumps({"id": request_id, "method": method,
                                        "params": params or {}}, ensure_ascii=False) + "\n")
        process.stdin.flush()

    def until(predicate, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                frame = frames.get(timeout=min(1, deadline - time.monotonic()))
            except queue.Empty:
                continue
            if frame.get("event"):
                seen.append((frame["event"], frame.get("payload", {}).get("percent")))
            if os.environ.get("CSLMM_METRO_TRACE") and frame.get("event") in {"progress", "error", "rmpProjectReady"}:
                print(frame, flush=True)
            if predicate(frame):
                return frame
        pytest.fail(f"engine protocol timed out; events: {seen[-20:]}; stderr tail: {errors[-20:]}")

    try:
        send(0, "hello", {"protocolVersion": 1})
        assert until(lambda frame: frame.get("id") == 0, 30)["result"]["protocolVersion"] == 1
        send(10, "startPreview")
        assert until(lambda frame: frame.get("id") == 10, 30)["result"]["viewerUrl"]
        send(11, "setPreviewEnabled", {"enabled": True})
        until(lambda frame: frame.get("id") == 11, 30)
        send(1, "openDocument", {"path": str(save)})
        assert until(lambda frame: frame.get("id") == 1, 30)["result"]["started"]
        open_state = until(lambda frame: frame.get("event") in {"sceneReady", "sceneError"} or
                           (frame.get("event") == "progress" and
                            frame["payload"]["percent"] >= 70), 120)
        assert open_state.get("event") != "sceneError", open_state
        send(2, "prepareRmpProject", {"requestId": "pipe-city"})
        assert until(lambda frame: frame.get("id") == 2, 30)["result"]["started"]
        terminal = until(lambda frame: frame.get("event") in {"rmpProjectReady", "rmpProjectError", "error", "sceneError"}, 90)
        assert terminal["event"] == "rmpProjectReady", (terminal, errors[-20:])
        send(3, "cancel")
        assert until(lambda frame: frame.get("id") == 3, 15)["result"]["cancelled"]
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def test_rmp_busy_rejection_preserves_active_request(tmp_path):
    from test_rmp_engine_protocol import Backend, sample_scene
    source = tmp_path / "city.mmap"
    source.write_bytes(b"city")
    backend = Backend(source)
    output = io.StringIO()
    server = EngineServer(io.StringIO(), output, backend)
    try:
        assert server.dispatch("prepareRmpProject", {"requestId": "first"})["started"]
        backend.busy = True
        with pytest.raises(ValueError):
            server.dispatch("prepareRmpProject", {"requestId": "duplicate"})
        assert server._rmp_pending[2] == "first"
        backend.last_rmp_snapshot = sample_scene()
        server._rmp_done()
        ready = json.loads(output.getvalue())
        assert ready["event"] == "rmpProjectReady" and ready["payload"]["requestId"] == "first"
    finally:
        server._preview_files.cleanup()


def test_retired_metro_protocol_reports_project_entry(tmp_path):
    server = EngineServer(io.StringIO(), io.StringIO(), _Backend())
    try:
        with pytest.raises(ValueError, match="RMP"):
            server.dispatch("renderMetro", {"requestId": "legacy"})
    finally:
        server._preview_files.cleanup()


def test_json_lines_hello_error_and_close() -> None:
    backend = _Backend()
    input_stream = io.StringIO("".join(json.dumps(frame) + "\n" for frame in (
        {"id": 1, "method": "hello", "params": {"protocolVersion": 1}},
        {"id": 2, "method": "hello", "params": {"protocolVersion": 2}},
        {"id": 3, "method": "close", "params": {}},
    )))
    output_stream = io.StringIO()

    assert EngineServer(input_stream, output_stream, backend).run() == 0

    frames = [json.loads(line) for line in output_stream.getvalue().splitlines()]
    assert frames[0] == {"id": 1, "result": {
        "protocolVersion": 1}}
    assert frames[1]["id"] == 2
    assert frames[1]["error"]["code"] == "ValueError"
    assert frames[2] == {"id": 3, "result": {"closed": True}}
    assert backend.closed


class _ThemeBackend:
    theme_stem = "modern"
    theme_migration_issues = ()
    aa = True
    contrast = "standard"
    font_scale = 1.0

    def __init__(self) -> None:
        self.applied = None
        self.user_themes = {}
        self._legacy_themes = {}
        self.merge_overrides = {}
        self._settings = {"values": {}}

    def theme_defaults(self, identity=None):
        return self.current_style_data()

    def theme_data(self, identity):
        return self.current_style_data()

    def theme_choices(self):
        return [{"id": "modern", "name": "Test", "builtin": True, "preview": "modern"}]

    def theme_display(self):
        return "Test"

    def suggest_theme_name(self, value):
        return value

    def _saved_theme_records(self):
        return self.user_themes

    def save_personal_theme(self, data, *, identity, base_id, name):
        self.applied = (data, None)
        return "user_test"

    def current_style_data(self) -> dict:
        return {"canvas": {"background": "#FFFFFF"}, "name": "Test"}

    def reset_style_preview_regions(self) -> None:
        pass

    def on_style_applied(self, data, save_path=None) -> bool:
        self.applied = (data, save_path)
        return True

    def set_aa(self, enabled: bool) -> None:
        self.aa = enabled

    def set_contrast(self, value: str) -> None:
        self.contrast = value

    def set_font_tier(self, value: str) -> None:
        self.font_tier = value

    def _schedule_settings_save(self) -> None:
        pass

    def set_merge_override(self, category: str, *, enabled: bool, distance: float) -> None:
        self.merge_overrides[category] = {"enabled": enabled, "distance": distance}


def test_theme_draft_rejects_bad_color_before_commit() -> None:
    backend = _ThemeBackend()
    server = EngineServer(io.StringIO(), io.StringIO(), backend)
    try:
        draft = server.dispatch("getThemeDraft", {})
        assert draft["data"]["canvas"]["background"] == "#FFFFFF"
        assert "canvas" in draft["sections"]
        with pytest.raises(ValueError, match="invalid color"):
            server.dispatch("commitThemeDraft", {
                "theme": "modern", "documentEpoch": 0,
                "data": {"canvas": {"background": "#XYZ"}},
            })
        assert backend.applied is None
        result = server.dispatch("commitThemeDraft", {
            "theme": "modern", "documentEpoch": 0, "data": draft["data"],
            "settings": {"aa": False, "contrast": "enhanced", "fontTier": "大"},
        })
        assert result["applied"] is True
        assert backend.applied == (draft["data"], None)
        assert backend.aa is True
        assert backend.contrast == "standard"
    finally:
        server._preview_files.cleanup()


def test_theme_draft_exposes_qt_field_metadata() -> None:
    class ThemeBackend(_ThemeBackend):
        def current_style_data(self):
            return {"canvas": {"background": "#FFFFFF"},
                    "roads": {"width": {"fixed": 1.5, "scaled": 0.4}},
                    "terrain": {"elevation_colors": [
                        {"threshold": 0, "color": "#557733"}]},
                    "labels": {"font": "Arial"}}

    server = EngineServer(io.StringIO(), io.StringIO(), ThemeBackend())
    try:
        draft = server.dispatch("getThemeDraft", {})
        assert draft["fields"]["canvas.background"]["label"] != "background"
        assert draft["fields"]["roads.width"]["kind"] == "pair"
        assert draft["fields"]["roads.width"]["step"] > 0
        assert draft["fields"]["terrain.elevation_colors"]["kind"] == "ramp"
        assert draft["fields"]["labels.font"]["kind"] == "font"
        assert draft["fields"]["labels.font"]["choices"]
        assert draft["sectionNames"]["roads"]
    finally:
        server._preview_files.cleanup()


def test_theme_sanitizer_keeps_appearance_and_strips_processing_snapshots():
    raw = {
        "roads": {"road": {"width": 9}, "highway": {"width": 7}},
        "railway": {"line": "#123456"},
        "built_area": {"buffer_meters": 60, "smooth_blur": 2,
                       "contour_upscale": 4},
        "buildings": {
            "color_mode": "uniform", "default": "#123456",
            "commercial": "#ABCDEF",
            "poi_tint": {"contrast": 0.3, "enabled": False,
                          "mix_white": 0.7, "categories": {"shop": "#000000"}},
        },
        "poi_area": {
            "opacity": 0.42, "buffer_meters": 120, "edge_meters": 3,
            "enabled": False, "mix_white": 0.5, "color": "#FFFFFF",
            "categories": {"shop": "#FFFFFF"},
        },
        "poi_icons": {
            "font": "Noto Sans", "active_profile": {"merge_enabled": False},
            "visibility_snapshot": {"icons": {"facility": False}},
            "merge_enabled": False, "hidden_categories": ["shop"],
        },
    }
    cleaned = sanitize_theme_data(raw)
    assert cleaned["buildings"]["color_mode"] == "uniform"
    assert cleaned["buildings"]["default"] == "#123456"
    assert cleaned["buildings"]["poi_tint"] == {"contrast": 0.3}
    assert "poi_area" not in cleaned
    assert cleaned["built_area"] == raw["built_area"]
    assert "road" not in cleaned["roads"]
    assert all("width" not in value for value in cleaned["roads"].values())
    assert cleaned["railway"]["train"]["line"] == "#123456"
    assert cleaned["railway"]["train"]["dash"] == "#FFFFFF"
    assert "line" not in cleaned["railway"]
    assert cleaned["poi_icons"] == {"font": "Noto Sans"}
    # Sanitizing is copy-only so importing a theme cannot mutate the editor's input.
    assert "active_profile" in raw["poi_icons"]


@pytest.mark.parametrize("size", [8, {"fixed": 4, "scaled": 8}, {"scaled": 8}])
def test_get_theme_draft_accepts_station_size_representations(tmp_path, size):
    backend = _guir_backend(tmp_path)
    backend.theme_stem = "warm_natural"
    backend.style_overrides["warm_natural"] = {"transports": {"station": {"size": size}}}
    server = EngineServer(io.StringIO(), io.StringIO(), backend)
    try:
        draft = server.dispatch("getThemeDraft", {})
        assert draft["data"]["transports"]["station"]["size"] == size
        assert draft["fields"]["transports.station.size"]["valueKind"] == "size"
        for invalid in (True, "large", {"pixels": 8}, {"fixed": "large", "scaled": 8}):
            with pytest.raises(ValueError, match=r"transports\.station\.size"):
                server.validate_theme_draft({"transports": {"station": {"size": invalid}}})
    finally:
        backend.close()
        server._preview_files.cleanup()


def test_get_theme_draft_defaults_and_reset_fields_use_builtin_theme_only(tmp_path):
    from csmodernmap import _paths
    from csmodernmap.style import load_style
    from csmodernmap.theme_contract import normalize_theme

    backend = _guir_backend(tmp_path)
    backend.theme_stem = "warm_natural"
    backend.document = object()
    backend.apply_style_override("building.default", "#010203")
    backend.apply_style_override("poi.font", "Test Font")
    backend.layer_visibility["building"] = False
    server = EngineServer(io.StringIO(), io.StringIO(), backend)
    try:
        server._document_epoch = 7
        draft = server.dispatch("getThemeDraft", {})
        builtin = normalize_theme(sanitize_theme_data(load_style(str(
            _paths.resource_dir("styles") / "warm_natural.yaml")).data))
        assert draft["defaults"] == builtin
        assert builtin['buildings']['category_colors']['police']['color'] == builtin['buildings']['default']
        assert not builtin['buildings']['category_colors']['police']['enabled']
        assert "poi_area" not in draft["defaults"]
        before_font = backend.field_value("poi.font")
        before_layers = dict(backend.layer_visibility)

        result = server.dispatch("resetFields", {
            "documentEpoch": 7, "keys": ["building.default"],
        })
        assert result["fields"]["building.default"] == builtin["buildings"]["default"]
        assert backend.field_value("poi.font") == before_font
        assert backend.layer_visibility == before_layers
        with pytest.raises(ValueError, match="outside the visible pages"):
            server.dispatch("resetFields", {
                "documentEpoch": 7, "keys": ["poi.merge"],
            })
    finally:
        backend.close()
        server._preview_files.cleanup()


def test_export_freezes_shared_view_profile_and_ignores_legacy_local_overrides(tmp_path):
    from csmodernmap.guir_backend import GuirBackend

    backend = _guir_backend(tmp_path)
    backend.document = object()
    backend.session = object()
    backend.source_file = str(tmp_path / "city.cslmap")
    backend.theme_stem = "warm_natural"
    backend.layer_visibility = {
        "terrain": True, "water": True, "road": True,
        "building": False, "district": True, "zone": True,
        "label": True, "poi": False,
    }
    latest_visibility = {
        "icons": {"transit": False, "facility": True, "ordinary": False},
        "names": {"transit": True, "facility": True, "ordinary": False},
    }
    backend.set_poi_visibility_snapshot(latest_visibility)
    output_profile = backend.get_output_profile("normal_png")
    output_profile.update(size=6144, rotation=90, aa=False)
    backend.set_output_profile("normal_png", output_profile)
    poi_profile = backend.get_poi_profile("normal_png")
    poi_profile["groups"]["ordinary"]["distance_m"] = 333.5
    backend.set_poi_profile("normal_png", poi_profile)
    live_theme = {
        "buildings": {
            "default": "#123456", "residential": "#ABCDEF",
            "poi_tint": {"contrast": 0.41, "enabled": True},
        },
        "poi_area": {"opacity": 0.47, "buffer_meters": 80,
                     "enabled": True, "mix_white": 0.6},
        "poi_icons": {"font": "Test Font",
                      "active_profile": {"merge_enabled": False},
                      "visibility_snapshot": {"icons": {"ordinary": False}}},
    }
    backend.current_style_data = lambda: live_theme
    backend.world_rect = lambda: (-1000.0, -1000.0, 1000.0, 1000.0)
    backend._build_job = lambda _size, *, preview: {
        "file": backend.source_file, "style": backend.theme_stem,
        "ui": {"style_overrides": {}, "text_enabled": True}, "layers": [],
    }
    captured = {}
    def render_png(request, _callbacks, **_kwargs):
        captured["request"] = request
        return True
    backend.render_png_export = render_png

    server = EngineServer(io.StringIO(), io.StringIO(), backend)
    try:
        local_poi_profile = backend.get_poi_profile("normal_png")
        local_poi_profile["groups"]["ordinary"]["distance_m"] = 275.25
        local_poi_profile["groups"]["facility"]["merge_enabled"] = False
        target = str(tmp_path / "user-selected.png")
        result = server.dispatch("export", {
            "requestId": "png-local-snapshot",
            "documentEpoch": 0,
            "context": "normal", "format": "PNG", "target": target,
            "profileTarget": "normal_png",
            "output": {"size": 8192, "rotation": 90, "aa": False},
            "poiProfile": local_poi_profile,
            # The window's read-only copy can be stale; the engine owns the
            # latest shared visibility and layer snapshot at acceptance time.
            "poiVisibility": {
                "icons": {"transit": True, "facility": False, "ordinary": True},
                "names": {"transit": False, "facility": False, "ordinary": True},
            },
            "region": None,
        })
        assert result == {"started": True}
        request = captured["request"]
        snapshot = request.render_snapshot
        assert request.target == target
        assert request.context.value == "normal"
        assert request.layer_snapshot["building"] is False
        assert request.layer_snapshot["poi"] is True
        assert request.theme_snapshot["data"]["buildings"] == {
            "default": "#123456", "poi_tint": {"contrast": 0.41},
            "color_mode": "poi",
        }
        assert "poi_area" not in request.theme_snapshot["data"]
        assert snapshot["document_epoch"] == 0
        assert snapshot["export_context"] == "normal"
        assert snapshot["export_format"] == "PNG"
        assert snapshot["export_target"] == target
        assert snapshot["output_profile"] == {
            "size": 8192, "rotation": 90, "aa": True}
        assert snapshot["poi_profile"]["groups"]["ordinary"]["distance_m"] == 333.5
        assert snapshot["poi_profile"]["groups"]["facility"]["merge_enabled"] is True
        assert snapshot["poi_visibility_snapshot"] == {
            "icons": {**latest_visibility["icons"], "area": True},
            "names": {**latest_visibility["names"], "area": True},
        }
        assert snapshot["ui"]["poi_profile"]["target"] == "normal_png"
        assert snapshot["ui"]["poi_visibility_snapshot"] == snapshot["poi_visibility_snapshot"]
        assert "poi_area" not in snapshot["ui"]["style_overrides"]["warm_natural"]
    finally:
        backend.close()
        server._preview_files.cleanup()


def test_html_export_only_applies_the_shared_rotation_field(tmp_path):
    backend = _guir_backend(tmp_path)
    backend.document = object()
    backend.session = object()
    backend.source_file = str(tmp_path / "city.cslmap")
    backend.theme_stem = "warm_natural"
    backend.set_output_profile("normal_png", {
        "size": 6144, "rotation": 270, "aa": False,
    })
    backend.world_rect = lambda: (-1000.0, -1000.0, 1000.0, 1000.0)
    backend._build_job = lambda _size, *, preview: {
        "file": backend.source_file, "style": backend.theme_stem,
        "ui": {"style_overrides": {}, "text_enabled": True}, "layers": [],
    }
    captured = {}

    def export_web(_directory, _callbacks, **kwargs):
        captured["request"] = kwargs["request"]
        return True

    backend.export_web = export_web
    server = EngineServer(io.StringIO(), io.StringIO(), backend)
    try:
        result = server.dispatch("export", {
            "requestId": "html-rotation-only",
            "documentEpoch": 0,
            "context": "normal", "format": "HTML",
            "target": str(tmp_path / "map.html"),
            "profileTarget": "normal_png",
            # Only rotation is valid for HTML; stale or over-eager clients may
            # still send hidden size/AA fields, which the engine must ignore.
            "output": {"size": 8192, "rotation": 90, "aa": True},
            "region": None,
        })
        assert result == {"started": True}
        request = captured["request"]
        assert request.render_snapshot["output_profile"] == {
            "size": 6144, "rotation": 270, "aa": True,
        }
        assert request.render_snapshot["poi_profile_target"] == "preview"
        assert request.render_snapshot["ui"]["poi_profile"]["target"] == "preview"
        assert request.target.endswith(".html")
    finally:
        backend.close()
        server._preview_files.cleanup()


def test_stale_export_completion_is_gated_by_document_epoch():
    backend = _Backend()
    server = EngineServer(io.StringIO(), io.StringIO(), backend)
    events = []
    server.event = lambda name, payload: events.append((name, payload))
    server._document_epoch = 2
    server._export.request_id = "old-request"
    server._export.document_epoch = 1
    server._export.context = "normal"
    server._export.format = "PNG"
    server._export.requested_target = "old-city.png"
    server._export.target = "old-city.png"
    backend.save_png_async = lambda *_args, **_kwargs: pytest.fail(
        "a stale render must not be saved after a document switch")
    try:
        server._render_done(object(), 2048, {}, 1.0, False, None, {})
        assert [name for name, _payload in events] == ["error", "busy"]
        assert {key: events[0][1][key] for key in ("requestId", "documentEpoch", "context", "format", "target", "exceptionMessage")} == {
            "requestId": "old-request", "documentEpoch": 1,
            "context": "normal", "format": "PNG", "target": "old-city.png",
            "exceptionMessage": "城市已切换，旧文档的导出结果已丢弃",
        }
        assert server._export.request_id is None
        assert server._export.target is None
    finally:
        server._preview_files.cleanup()


def test_poi_master_switch_updates_only_icon_snapshot_and_derives_layer(tmp_path):
    backend = _guir_backend(tmp_path)
    backend.document = object()
    server = EngineServer(io.StringIO(), io.StringIO(), backend)
    try:
        initial = {
            "icons": {"transit": True, "facility": False, "ordinary": True, "area": True},
            "names": {"transit": False, "facility": True, "ordinary": False, "area": True},
        }
        backend.set_poi_visibility_snapshot(initial)
        assert backend.layer_visibility["poi"] is True

        hidden = server.dispatch("setLayerVisibility", {
            "key": "poi", "enabled": False,
        })
        assert hidden["poiVisibility"] == {
            "icons": {"transit": False, "facility": False, "ordinary": False, "area": False},
            "names": initial["names"],
        }
        assert hidden["layers"]["poi"] is False
        assert backend.layer_visibility["poi"] is False

        shown = server.dispatch("setLayerVisibility", {
            "key": "poi", "enabled": True,
        })
        assert shown["poiVisibility"] == {
            "icons": {"transit": True, "facility": True, "ordinary": True, "area": True},
            "names": initial["names"],
        }
        assert shown["layers"]["poi"] is True
        assert backend.poi_visibility_snapshot() == shown["poiVisibility"]
    finally:
        server._preview_files.cleanup()


def test_reset_profile_fields_changes_only_visible_keys(tmp_path):
    backend = _guir_backend(tmp_path)
    server = EngineServer(io.StringIO(), io.StringIO(), backend)
    try:
        backend.set_output_profile("normal_png", {
            "size": 6144, "rotation": 90, "aa": False,
        })
        reset_output = server.dispatch("resetProfileFields", {
            "documentEpoch": 0, "kind": "output", "target": "normal_png",
            "keys": ["size"],
        })
        assert reset_output == {
            "kind": "output", "target": "normal_png",
            "profile": {"size": 4096, "rotation": 90, "aa": True},
        }

        profile = backend.get_poi_profile("normal_png")
        profile["groups"]["facility"]["merge_enabled"] = False
        profile["groups"]["ordinary"]["distance_m"] = 333.5
        backend.set_poi_profile("normal_png", profile)
        reset_poi = server.dispatch("resetProfileFields", {
            "documentEpoch": 0, "kind": "poi", "target": "normal_png",
            "keys": ["groups.ordinary.distance_m"],
        })
        assert reset_poi["profile"]["groups"]["ordinary"]["distance_m"] == 24.0
        assert "distance_m" not in reset_poi["profile"]["groups"]["facility"]
        assert reset_poi["profile"]["groups"]["facility"]["merge_enabled"] is False
    finally:
        server._preview_files.cleanup()


def test_format_capabilities_match_context_and_visible_controls():
    normal = EngineServer._format_capabilities(ContextMode.NORMAL)
    assert list(normal) == ["PNG", "HTML"]
    assert normal["PNG"]["outputFields"] == ["size", "rotation"]
    assert normal["PNG"]["outputProfileTarget"] == "normal_png"
    assert normal["HTML"]["outputFields"] == ["rotation"]
    assert normal["HTML"]["outputProfileTarget"] == "normal_png"
    assert normal["HTML"]["poiProfileTarget"] is None
    for context in (ContextMode.SELECTION, ContextMode.RANDOM_SPOT):
        capabilities = EngineServer._format_capabilities(context)
        assert list(capabilities) == list(normal)
        assert all({**value, "context": "normal"} == normal[format_name]
                   for format_name, value in capabilities.items())

    transit = EngineServer._format_capabilities(ContextMode.TRANSIT)
    assert list(transit) == ["PNG", "HTML"]
    assert transit["HTML"]["outputProfileTarget"] == "transit_png"
    assert transit["PNG"]["outputProfileTarget"] == "transit_png"
    assert transit["PNG"]["poiProfileTarget"] == "transit_png"

    metro = EngineServer._format_capabilities(ContextMode.METRO)
    assert list(metro) == ["PNG", "RMG", "RMP"]
    assert metro["PNG"]["outputFields"] == ["size"]
    assert metro["PNG"]["outputProfileTarget"] is None
    assert metro["RMG"]["targetKind"] == metro["RMP"]["targetKind"] == "directory"
    assert metro["RMG"]["context"] == "metro"


def test_merge_override_can_return_to_global_inheritance() -> None:
    backend = _ThemeBackend()
    server = EngineServer(io.StringIO(), io.StringIO(), backend)
    try:
        server.dispatch("setMergeOverride", {"category": "hospital",
            "useOverride": True, "enabled": False, "distance": 75})
        assert backend.merge_overrides["hospital"] == {"enabled": False, "distance": 75}
        result = server.dispatch("setMergeOverride", {"category": "hospital",
            "useOverride": False, "enabled": False, "distance": 75})
        assert result["overrides"] == {}
    finally:
        server._preview_files.cleanup()


def test_theme_preview_only_emits_latest_request() -> None:
    class Image:
        def save(self, path, format):
            path.write_bytes(b"preview")

    class Backend:
        def current_style_data(self):
            return {}

        def __init__(self):
            self.session = object()
            self.entered = threading.Event()
            self.release = threading.Event()

        def style_preview_render(self, data, size, scene):
            if data["name"] == "first":
                self.entered.set()
                assert self.release.wait(2)
            return Image()

    backend = Backend()
    server = EngineServer(io.StringIO(), io.StringIO(), backend)
    received = []
    ready = threading.Event()

    def on_event(name, payload):
        received.append((name, payload))
        ready.set()

    server.event = on_event
    try:
        assert server.dispatch("renderThemePreview", {
            "requestId": "first", "data": {"name": "first"}})["started"]
        assert backend.entered.wait(2)
        assert server.dispatch("renderThemePreview", {
            "requestId": "second", "data": {"name": "second"}})["started"]
        backend.release.set()
        assert ready.wait(2)
        assert received == [("themePreviewReady", {
            "requestId": "second", "url": received[0][1]["url"]})]
    finally:
        backend.release.set()
        server._preview_files.cleanup()


def test_rmp_generation_failure_resolves_request_and_releases_backend(monkeypatch, tmp_path):
    from csmodernmap import rmp_generation
    from csmodernmap.winui_engine import LoopScheduler
    scheduler = LoopScheduler()
    backend = GuirBackend(scheduler, settings_dir=tmp_path)
    source = tmp_path / "city.cmm.gz"
    source.write_bytes(b"failure fixture")
    backend.source_file = str(source)
    backend.document = object()
    output = io.StringIO()
    server = EngineServer(io.StringIO(), output, backend)
    def fail(*args, **kwargs):
        raise ValueError("invalid station topology")
    monkeypatch.setattr(rmp_generation, "generate_rmp", fail)
    try:
        assert server.dispatch("prepareRmpProject", {"requestId": "failed-generation"})["started"]
        backend._local_render_thread.join(timeout=2)
        assert not backend.local_render_active
        scheduler.pump()
        frames = [json.loads(line) for line in output.getvalue().splitlines()]
        failures = [frame["payload"] for frame in frames if frame.get("event") == "rmpProjectError"]
        assert len(failures) == 1
        assert failures[0]["requestId"] == "failed-generation"
        assert "invalid station topology" in str(failures[0])
        assert not backend.busy and server._rmp_pending is None
        assert backend.last_rmp_snapshot is None
        assert not any(frame.get("event") == "rmpProjectReady" for frame in frames)
    finally:
        server._preview_files.cleanup()
        backend.close()
