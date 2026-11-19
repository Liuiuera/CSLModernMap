"""Native RMP rendering, offline export and project round trips in Chromium."""
from __future__ import annotations

import base64
import copy
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import threading
import time
import xml.etree.ElementTree as ET

import pytest
from PIL import Image
from shapely.geometry import Polygon, mapping

from csmodernmap.external_edit.rmp_graph import build_editor_draft

pytestmark = [pytest.mark.browser, pytest.mark.skipif(os.environ.get("CSL_RMP_BROWSER_TEST") != "1",
                                reason="opt-in built RMP editor integration test")]
ROOT = Path(__file__).resolve().parents[2]


def sample_scene():
    return {
        "width": 700, "height": 500, "background": [255, 255, 255],
        "stations": [
            {"id": "a", "x": 100, "y": 100, "name": "机场站", "transfer": False,
             "visible": True, "label": {"x": 115, "y": 80}},
            {"id": "b", "x": 400, "y": 100, "name": "港口站", "transfer": True,
             "visible": True, "label": {"x": 415, "y": 80}},
        ],
        "lines": [{"chains": [["a", "b"]], "color": "#FF0000", "source_color": [255, 0, 0, 255], "source_name": "机场线", "width": 6, "source_line_id": 1}],
        "water": mapping(Polygon([(100, 200), (400, 200), (400, 450), (100, 450)],
                                 holes=[[(200, 280), (300, 280), (300, 370), (200, 370)]])),
        "water_color": [0, 100, 255], "water_alpha": 255,
        "facilities": [{"kind": kind, "station_id": "a", "x": 480, "y": 200 + i * 70,
                        "size": 16, "name": name, "name_visible": True}
                       for i, (kind, name) in enumerate([
                           ("airport", "机场"), ("rail", "铁路客站"), ("port", "客运港")])],
    }


def legacy_project():
    # A saved v77 project verifies reopening earlier user work after generator retirement.
    return json.loads((ROOT / "tests/renderer/fixtures/rmp-legacy-project.json").read_text(encoding="utf-8"))


@pytest.fixture
def editor():
    from playwright.sync_api import sync_playwright

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

    assets = ROOT / "dev/third_party/rmp/dist-embed"
    assert (assets / "embed.html").is_file(), "Build the RMP embed assets first"
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(assets)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": 1400, "height": 900})
                origin = f"http://127.0.0.1:{server.server_port}"
                external, errors = [], []
                def route(request):
                    if request.request.url.startswith((origin + "/", "data:", "blob:")):
                        request.continue_()
                    else:
                        external.append(request.request.url)
                        request.abort()
                page.route("**/*", route)
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.add_init_script("""window.events=[]; window.chrome=window.chrome||{};
                    window.chrome.webview={postMessage:m=>window.events.push(m),addEventListener:()=>{}};""")
                page.goto(origin + "/embed.html?language=zh-Hans")
                deadline = time.monotonic() + 20
                while not page.evaluate("window.events.some(e=>e.type==='ready')"):
                    assert time.monotonic() < deadline, errors
                    page.wait_for_timeout(50)
                yield page
                assert not external, external
                assert not errors, errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def command(page, kind, *, project_id="sample", **payload):
    request_id = str(page.evaluate("window.events.length"))
    page.evaluate("m=>window.cslRmp.dispatch(m)", {
        "protocolVersion": 1, "type": kind, "projectId": project_id,
        "requestId": request_id, **payload,
    })
    return page.evaluate("id=>window.events.findLast(e=>e.requestId===id)", request_id)


def test_metadata_draft_uses_rmp_styles_and_unrestricted_local_editing(editor, tmp_path):
    scene = sample_scene()
    scene["lines"][0].update(width=80, casing_width=120, casing_color="#000000")
    scene["lines"] = [{**scene["lines"][0], "source_line_id": index} for index in range(6)]
    scene["stations"][0].update(label=None, visible=False)
    assert command(editor, "loadProject", project=build_editor_draft(scene), fit=True)["type"] == "loaded"
    command(editor, "hostState", enabled=True)
    saved = command(editor, "saveProject")["project"]
    assert "cmmDraft" not in saved
    route = next(e for e in saved["graph"]["edges"] if e["key"].startswith("line_route_"))
    assert len(route["attributes"]["generic"]["layers"]) == 1
    assert route["attributes"]["generic"]["layers"][0]["width"] == 5
    assert route["attributes"]["generic"]["layers"][0]["color"][2] == "#ff0000"
    assert sorted(e["attributes"]["parallelIndex"] for e in saved["graph"]["edges"]
                  if e["key"].startswith("line_route_")) == list(range(6))
    assert saved["graph"]["nodes"][0]["attributes"]["shmetro-basic"]["names"][0] == "机场站"
    assert next(n for n in saved["graph"]["nodes"] if n["key"] == "stn_cmm_b")["attributes"]["shmetro-int"]["width"] == 13
    fills = [n for n in saved["graph"]["nodes"] if n["attributes"]["type"] == "fill"]
    assert all(n["attributes"]["fill"]["color"][2] == "#B9E3F9" for n in fills)
    tools = command(editor, "getTools")["tools"]
    assert {"simple", "ray-guided"} <= {item["id"] for item in tools["paths"]}
    for mode in ("line-simple/generic", "line-ray-guided/generic"):
        assert command(editor, "setMode", mode=mode)["state"]["mode"] == mode
    command(editor, "setMode", mode="select")
    editor.locator('[id^="line_route_"] path').first.evaluate("el => el.dispatchEvent(new PointerEvent('pointerdown', {bubbles:true}))")
    panel = editor.get_by_test_id("side-panel-root")
    for _ in range(3):
        panel.get_by_role("button", name="添加线层", exact=True).click()
    assert panel.get_by_test_id("generic-layer-3").is_visible()
    assert panel.get_by_text("订阅可解锁额外线层。", exact=True).count() == 0
    copies = panel.get_by_role("button", name="复制线层", exact=True)
    copies.first.click()
    edited = command(editor, "saveProject")["project"]
    assert len(next(e for e in edited["graph"]["edges"] if e["key"] == route["key"])["attributes"]["generic"]["layers"]) == 5
    assert command(editor, "undo")["type"] == "historyChanged"
    undone = command(editor, "saveProject")["project"]
    assert len(next(e for e in undone["graph"]["edges"] if e["key"] == route["key"])["attributes"]["generic"]["layers"]) == 4
    assert command(editor, "redo")["type"] == "historyChanged"
    assert command(editor, "saveProject")["project"] == edited
    assert command(editor, "loadProject", project=edited)["type"] == "loaded"
    assert command(editor, "saveProject")["project"] == edited
    svg = command(editor, "export", format="svg")
    assert svg["type"] == "exported", svg.get("message")
    assert "机场站" in svg["data"]
    png = command(editor, "export", format="png")
    assert png["type"] == "exported"
    image = Image.open(io.BytesIO(base64.b64decode(png["data"]))).convert("RGBA")
    image.save(tmp_path / "rmp-native-defaults.png")
    viewbox = list(map(float, ET.fromstring(svg["data"]).attrib["viewBox"].split()))
    assert image.getpixel((round(150 - viewbox[0]), round(320 - viewbox[1]))) == (185, 227, 249, 255)
    assert image.getpixel((round(250 - viewbox[0]), round(320 - viewbox[1]))) == (255, 255, 255, 255)


def test_edit_save_reopen_and_offline_svg_png(editor, tmp_path):
    project = legacy_project()
    assert command(editor, "loadProject", project=project, fit=True)["type"] == "loaded"
    command(editor, "hostState", enabled=True)
    route = editor.locator('[id^="line_route_"] path').first
    original_path = route.get_attribute("d")
    # Actual keyboard editing exercises selection, Redux persistence and dirty notifications.
    station = editor.locator("#stn_core_stn_cmm_a")
    station.click()
    editor.locator("#canvas").press("l")
    assert route.get_attribute("d") != original_path
    saved = command(editor, "saveProject")
    assert saved["type"] == "project"
    a = next(n for n in saved["project"]["graph"]["nodes"] if n["key"] == "stn_cmm_a")
    assert a["attributes"]["x"] > 100
    assert editor.evaluate("window.events.some(e=>e.type==='changed')")
    assert command(editor, "undo")["type"] == "historyChanged"
    undone = command(editor, "saveProject")
    assert undone["project"]["graph"]["nodes"][0]["attributes"]["x"] == 100
    assert command(editor, "redo")["type"] == "historyChanged"
    name = editor.get_by_test_id("side-panel-root").get_by_role("group", name="中文名称", exact=True).get_by_role("textbox")
    name.fill("编辑后的机场站")
    name.press("Tab")
    editor.wait_for_function("() => window.graph.getNodeAttribute('stn_cmm_a', 'shmetro-basic').names[0] === '编辑后的机场站'")
    saved = command(editor, "saveProject")
    assert saved["project"]["graph"]["nodes"][0]["attributes"]["shmetro-basic"]["names"][0] == "编辑后的机场站"
    assert command(editor, "loadProject", project=saved["project"])["type"] == "loaded"
    assert command(editor, "saveProject")["project"] == saved["project"]
    svg = command(editor, "export", format="svg")
    assert svg["type"] == "exported", svg.get("message")
    root = ET.fromstring(svg["data"])
    assert root.find(".//*[@id='rmp_info']") is None
    assert "data:font/woff2;base64," in svg["data"]
    assert "编辑后的机场站" in svg["data"] and "客运港" in svg["data"]
    assert not root.findall(".//{http://www.w3.org/2000/svg}image")
    assert len(root.findall(".//{http://www.w3.org/2000/svg}symbol")) == 3
    (tmp_path / "edited.svg").write_text(svg["data"], encoding="utf-8")
    png = command(editor, "export", format="png")
    assert png["type"] == "exported", png.get("message")
    image = Image.open(io.BytesIO(base64.b64decode(png["data"]))).convert("RGBA")
    image.save(tmp_path / "edited.png")
    viewbox = list(map(float, root.attrib["viewBox"].split()))
    def pixel(x, y):
        return image.getpixel((round(x - viewbox[0]), round(y - viewbox[1])))
    assert pixel(150, 320) == (0, 100, 255, 255)
    assert pixel(250, 320) == (255, 255, 255, 255), "Island must reveal the white canvas"
    assert image.getchannel("A").getextrema() == (255, 255)
    assert root.find(".//*[@id='rmp-background']").get("fill") == "#FFFFFF"


def test_bad_import_preserves_open_project_and_stale_commands_are_ignored(editor):
    project = legacy_project()
    assert command(editor, "loadProject", project=project)["type"] == "loaded"
    for mutate in (lambda p: p.update(version=78),
                   lambda p: p["graph"]["nodes"][0]["attributes"].update(type="image")):
        invalid = copy.deepcopy(project)
        mutate(invalid)
        assert command(editor, "loadProject", project=invalid, project_id="bad")["type"] == "error"
        assert command(editor, "saveProject")["project"]["graph"] == project["graph"]
    assert command(editor, "saveProject", project_id="obsolete") is None


@pytest.mark.parametrize("width", [1400, 800, 560])
def test_host_tools_state_and_responsive_canvas(editor, tmp_path, width):
    editor.set_viewport_size({"width": width, "height": 900})
    project = legacy_project()
    assert command(editor, "loadProject", project=project, fit=True)["type"] == "loaded"
    command(editor, "hostState", enabled=True)
    assert editor.locator(".cmm-ribbon,.cmm-titlebar").count() == 0
    tools = command(editor, "getTools")["tools"]
    assert any(item["id"] == "station-shmetro-basic" for item in tools["stations"])
    assert all(item["label"] for group in tools.values() for item in group)
    assert {item["id"] for item in tools["elements"]} == {
        "misc-node-virtual", "misc-node-fill", "misc-node-facilities", "misc-node-text"}
    for invalid in ("station-missing", "misc-node-image", "misc-node-master", "line-missing/single-color"):
        assert command(editor, "setMode", mode=invalid)["type"] == "error"
    editor.wait_for_function("""() => {
        const canvas=document.querySelector('#canvas'), box=canvas.getBoundingClientRect();
        return Math.abs(box.top)<1 && Math.abs(box.right-innerWidth)<1
            && Math.abs(box.bottom-innerHeight)<1 && Math.abs(canvas.viewBox.baseVal.width-box.width)<1;
    }""")
    assert command(editor, "setMode", mode="station-shmetro-basic")["state"]["mode"] == "station-shmetro-basic"
    box = editor.locator("#canvas").bounding_box()
    before = command(editor, "saveProject")["project"]
    editor.locator("#canvas").click(position={"x": box["width"] * .35, "y": box["height"] * .65})
    after = command(editor, "saveProject")["project"]
    original_ids = {node["key"] for node in before["graph"]["nodes"]}
    added = [node for node in after["graph"]["nodes"] if node["key"] not in original_ids]
    assert len(added) == 1 and added[0]["attributes"]["type"] == "shmetro-basic"
    for axis, fraction in (("x", .35), ("y", .65)):
        length = box["width" if axis == "x" else "height"]
        expected = length * fraction * before["svgViewBoxZoom"] / 100 + before["svgViewBoxMin"][axis]
        assert abs(added[0]["attributes"][axis] - expected) <= 2.5
    assert editor.evaluate("window.events.findLast(e=>e.type==='editorState').state.canUndo")
    command(editor, "undo")
    assert command(editor, "saveProject")["project"]["graph"] == before["graph"]
    assert editor.evaluate("window.events.findLast(e=>e.type==='editorState').state.canRedo")
    command(editor, "redo")
    assert command(editor, "saveProject")["project"]["graph"] == after["graph"]
    revision = editor.evaluate("window.events.filter(e=>e.type==='changed').length")
    options = {"gridLines": True, "snapLines": False, "showWaterControls": True, "detailsVisible": False}
    state = command(editor, "setViewOptions", options=options)["state"]
    assert all(state[key] == value for key, value in options.items())
    assert command(editor, "setViewOptions", options={"unknown": True})["type"] == "error"
    assert editor.evaluate("window.events.filter(e=>e.type==='changed').length") == revision
    command(editor, "setMode", mode="select")
    states = editor.evaluate("window.events.filter(e=>e.type==='editorState').length")
    editor.locator("#canvas").hover(position={"x": 10, "y": 10})
    assert editor.evaluate("window.events.filter(e=>e.type==='editorState').length") == states
    command(editor, "hostState", enabled=False)
    assert editor.locator("#root").evaluate("element=>element.inert")
    command(editor, "hostState", enabled=True)
    assert not editor.locator("#root").evaluate("element=>element.inert")
    editor.screenshot(path=tmp_path / f"rmp-workspace-{width}.png")


def test_host_shortcuts_and_native_details_focus(editor, tmp_path):
    assert command(editor, "loadProject", project=legacy_project(), fit=True)["type"] == "loaded"
    command(editor, "hostState", enabled=True)
    for keys, action in (("Control+s", "save"), ("Control+Shift+s", "saveAs"),
                         ("Control+o", "open"), ("Control+Shift+e", "export")):
        editor.locator("#canvas").focus()
        count = editor.evaluate("window.events.filter(e=>e.type==='commandRequested').length")
        editor.keyboard.press(keys)
        assert editor.evaluate("window.events.filter(e=>e.type==='commandRequested').length") == count + 1
        assert editor.evaluate("window.events.findLast(e=>e.type==='commandRequested').command") == action
    editor.locator("#stn_core_stn_cmm_a").click()
    panel = editor.get_by_test_id("side-panel-root")
    panel.get_by_role("button", name="其他属性", exact=True).click()
    coordinate = panel.get_by_role("group", name="横坐标", exact=True).get_by_role("textbox")
    coordinate.fill("125")
    coordinate.press("Enter")
    assert editor.evaluate("window.graph.getNodeAttribute('stn_cmm_a','x')") == 125
    # Text editing must not delete a selected object or consume the canvas history.
    original_count = editor.evaluate("window.graph.order")
    coordinate.press("Delete")
    assert editor.evaluate("window.graph.order") == original_count
    coordinate.fill("125")
    editor.locator("#canvas").focus()
    editor.keyboard.press("Control+z")
    assert editor.evaluate("window.graph.getNodeAttribute('stn_cmm_a','x')") == 100
    editor.keyboard.press("Control+y")
    assert editor.evaluate("window.graph.getNodeAttribute('stn_cmm_a','x')") == 125
    editor.keyboard.press("Escape")
    state = editor.evaluate("window.events.findLast(e=>e.type==='editorState').state")
    assert state["selected"] == [] and state["mode"] == "free"
    editor.screenshot(path=tmp_path / "rmp-details-keyboard.png")


def test_missing_local_asset_rolls_back_failed_load(editor):
    original = legacy_project()
    original["graph"]["nodes"] = [node for node in original["graph"]["nodes"]
                                  if not node["key"].startswith("misc_node_facility_")]
    assert command(editor, "loadProject", project=original)["type"] == "loaded"
    editor.route("**/images/facilities/airport.svg", lambda route: route.fulfill(status=404, body="missing"))
    result = command(editor, "loadProject", project=legacy_project(), project_id="failed")
    assert result["type"] == "error"
    assert command(editor, "saveProject")["project"]["graph"] == original["graph"]


def test_real_city_geometry_survives_native_render_and_serialization(editor, tmp_path):
    from csmodernmap.cmm import load_document
    from csmodernmap.rmp_generation import generate_rmp
    scene = generate_rmp(load_document(ROOT / "tests/renderer/fixtures/basan_rings.mmap"), {"types": ["Metro"]})
    project = build_editor_draft(scene)
    assert command(editor, "loadProject", project=project, fit=True)["type"] == "loaded"
    saved = command(editor, "saveProject")
    project = saved["project"]
    assert command(editor, "loadProject", project=project)["type"] == "loaded"
    assert command(editor, "saveProject")["project"] == project
    # RMP generates its own bends; topology and native renderability remain the contract.
    lengths = editor.evaluate("""() => [...document.querySelectorAll('[id^=line_route_] path')]
        .map(p=>p.getTotalLength())""")
    assert lengths and all(0 < length < float("inf") for length in lengths)
    routes = [edge for edge in project["graph"]["edges"] if edge["key"].startswith("line_route_")]
    assert all(edge["source"].startswith("stn_cmm_") and edge["target"].startswith("stn_cmm_")
               for edge in routes)
    editor.screenshot(path=tmp_path / "basan-editor.png")
    svg = command(editor, "export", format="svg")
    assert svg["type"] == "exported", svg.get("message")
    (tmp_path / "basan.svg").write_text(svg["data"], encoding="utf-8")


def test_export_scale_transparency_and_svg_version(editor):
    project = legacy_project()
    assert command(editor, "loadProject", project=project)["type"] == "loaded"
    before = command(editor, "saveProject")["project"]
    editor.evaluate("document.querySelector('#canvas text').classList.add('rmp-name-outline')")
    svg = command(editor, "export", format="svg", svgVersion=2)
    legacy = command(editor, "export", format="svg", svgVersion=1.1)
    assert svg["type"] == legacy["type"] == "exported"
    current_root, legacy_root = ET.fromstring(svg["data"]), ET.fromstring(legacy["data"])
    ns = {"svg": "http://www.w3.org/2000/svg"}
    assert len(legacy_root.findall(".//svg:text", ns)) > len(current_root.findall(".//svg:text", ns))
    assert "data:font/woff2;base64," in legacy["data"]
    width, height = float(current_root.get("width")), float(current_root.get("height"))
    for scale, transparent in [(100, False), (200, True), (50, False)]:
        result = command(editor, "export", format="png", scale=scale, transparent=transparent)
        assert result["type"] == "exported", result.get("message")
        image = Image.open(io.BytesIO(base64.b64decode(result["data"]))).convert("RGBA")
        assert image.size == (int(width * scale / 100), int(height * scale / 100))
        assert image.getchannel("A").getextrema() == ((0, 255) if transparent else (255, 255))
    assert command(editor, "saveProject")["project"] == before


def test_partial_water_draft_loads_native_stations_lines_and_remaining_fill(editor, monkeypatch):
    from shapely.geometry import MultiPolygon, GeometryCollection, box, shape
    from csmodernmap.external_edit import rmp_graph
    scene = sample_scene()
    scene["water"] = mapping(MultiPolygon([shape(scene["water"]), box(500, 300, 600, 400)]))
    monkeypatch.setattr(rmp_graph, "split", lambda part, cut: GeometryCollection([part]))
    warnings = []
    project = build_editor_draft(scene, warnings=warnings)
    assert len(warnings) == 1
    assert command(editor, "loadProject", project=project, fit=True)["type"] == "loaded"
    graph = command(editor, "saveProject")["project"]["graph"]
    assert len([node for node in graph["nodes"] if node["key"].startswith("stn_cmm_")]) == 2
    assert len([edge for edge in graph["edges"] if edge["key"].startswith("line_route_")]) == 1
    assert len([node for node in graph["nodes"] if node["attributes"]["type"] == "fill"]) == 1
    assert len([node for node in graph["nodes"] if node["attributes"]["type"] == "facilities"]) == 3
