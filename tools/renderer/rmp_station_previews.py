"""用内嵌编辑器自身的新建与绘制流程生成站点图库，无独立绘图实现。"""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread


def build_station_previews(output: Path, destination: Path | None = None):
    from playwright.sync_api import sync_playwright

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(output)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": 1000, "height": 700}, device_scale_factor=2)
                origin = f"http://127.0.0.1:{server.server_port}"
                page.route("**/*", lambda route: route.continue_() if route.request.url.startswith((origin + "/", "data:", "blob:")) else route.abort())
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.add_init_script("window.events=[]; window.chrome={webview:{postMessage:m=>events.push(m),addEventListener:()=>{}}};")
                page.goto(origin + "/embed.html?language=en")
                page.wait_for_function("() => events.some(e=>e.type==='ready')")

                def command(kind, **payload):
                    request_id = str(page.evaluate("events.length"))
                    page.evaluate("m=>window.cslRmp.dispatch(m)", {"type": kind, "protocolVersion": 1,
                        "projectId": "preview", "requestId": request_id, **payload})
                    failures = page.evaluate("id=>events.filter(e=>e.requestId===id&&e.type==='error')", request_id)
                    if failures:
                        raise RuntimeError(failures)

                command("loadProject", project={"version": 77, "svgViewBoxZoom": 100, "svgViewBoxMin": {"x": 0, "y": 0},
                    "graph": {"options": {"type": "directed", "multi": True, "allowSelfLoops": True}, "attributes": {}, "nodes": [], "edges": []}})
                command("hostState", enabled=True)
                command("getTools")
                catalog = page.evaluate("events.findLast(e=>e.type==='tools').tools.stations")
                directory = destination or Path(__file__).resolve().parents[2] / "dev/third_party/rmp/public/station-previews"
                directory.mkdir(exist_ok=True)
                for item in catalog:
                    # Start from an empty project so symbol defaults and automatic insertion stay identical to the app.
                    command("loadProject", project={"version": 77, "svgViewBoxZoom": 100, "svgViewBoxMin": {"x": 0, "y": 0},
                        "graph": {"options": {"type": "directed", "multi": True, "allowSelfLoops": True}, "attributes": {}, "nodes": [], "edges": []}})
                    command("setMode", mode=item["id"])
                    page.locator("#canvas").click(position={"x": 200, "y": 200})
                    page.wait_for_function("() => window.graph.order===1")
                    page.evaluate("document.fonts.ready")
                    markup = page.evaluate("""() => {
                        const id=window.graph.nodes()[0];
                        const parts=[`${id}.pre`, id, `${id}.post`].map(id=>document.getElementById(id)).filter(Boolean);
                        const boxes=parts.map(node=>node.getBBox());
                        const x=Math.min(...boxes.map(box=>box.x)), y=Math.min(...boxes.map(box=>box.y));
                        const right=Math.max(...boxes.map(box=>box.x+box.width)), bottom=Math.max(...boxes.map(box=>box.y+box.height));
                        const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
                        svg.id='cmm-preview';svg.setAttribute('width','128');svg.setAttribute('height','64');
                        svg.setAttribute('xmlns','http://www.w3.org/2000/svg');
                        svg.setAttribute('viewBox',`${x-5} ${y-5} ${Math.max(right-x,1)+10} ${Math.max(bottom-y,1)+10}`);
                        svg.style.cssText='background:white;color:black';
                        document.querySelectorAll('#canvas defs').forEach(def=>svg.append(def.cloneNode(true)));
                        parts.forEach(node=>{
                            const clone=node.cloneNode(true);clone.removeAttribute('transform');clone.removeAttribute('class');
                            clone.querySelectorAll('.removeMe').forEach(e=>e.remove());svg.append(clone);
                        });
                        return new XMLSerializer().serializeToString(svg);
                    }""")
                    (directory / (item["type"] + ".svg")).write_text(markup + "\n", encoding="utf-8")
                if errors:
                    raise RuntimeError(errors)
                print(f"RMP station previews: {len(catalog)}")
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    build_station_previews(Path(__file__).resolve().parents[2] / "dev/third_party/rmp/dist-embed")
