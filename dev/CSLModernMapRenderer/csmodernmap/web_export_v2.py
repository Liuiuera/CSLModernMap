"""地图 Web 导出 v2 — MapLibre 单文件 HTML（内嵌 Preview Scene）。

把桌面实时预览使用的版本化 Preview Scene（``preview_scene.compile_full``
产物）整份内联进一个自包含 HTML，基于 Vite 构建的 Viewer 前端
（``viewer/dist``）离线运行：零联网、双击即用、矢量无限缩放。

产物为单文件，全部数据（场景 GeoJSON、地形与表面贴图、POI 雪碧图与
索引）内嵌，大图通常十几到几十 MB，属于已确认接受的高体积 trade-off。

用法（由 GUI/CLI 复用）::

    from csmodernmap.web_export_v2 import export_web
    export_web(session_or_document, style, city, out_path,
               options={...}, callbacks={...})
"""
from __future__ import annotations

from . import i18n

import base64
import json
from html import escape
import re
import time
from pathlib import Path
from typing import Any, Callable, Optional

from ._paths import resource_dir
from .export_region import output_rotation

# Vite 哈希产物文件名（index-*.js / index-*.css / maplibre-gl-csp-worker.js）
_JS_RE = re.compile(r'src="\./assets/(index-[^"]+\.js)"')
_CSS_RE = re.compile(r'href="\./assets/(index-[^"]+\.css)"')


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _escape_script(text: str) -> str:
    """内联脚本正文转义，防止出现 </script 提前截断 HTML。"""
    return text.replace("</script", "<\\/script")


def _data_uri(path: Path) -> str:
    """PNG 文件 → data URI（base64，mime image/png）。"""
    return "data:image/png;base64," + base64.b64encode(
        path.read_bytes()).decode("ascii")


def _output_rotation(value: Any) -> int:
    """从界面或命令行输出选项读取顺时针旋转的四分之一圈数。"""
    if value is None:
        return 0
    try:
        return output_rotation(int(str(value).strip().rstrip("°")))
    except (TypeError, ValueError) as exc:
        raise ValueError("rotation must be 0/90/180/270") from exc


def _session_for(source: Any):
    """把 DocumentSession 或 MapDocument 统一为 DocumentSession。"""
    if hasattr(source, "road_scene"):
        return source
    from .cmm.session import DocumentSession
    return DocumentSession(document=source)


def build_scene_payload(session, style: Any, options: dict[str, Any],
                        visibility: dict[str, bool] | None = None, *,
                        progress: Optional[Callable[[float, str], None]] = None) -> dict:
    """编译完整 Preview Scene 并把 bundle 全部数据读入内存。

    返回与 Viewer ``EmbeddedPayload`` 对齐的结构：
    ``{**manifest, "embedded": {"sources", "images", "fonts", "poiAtlasIndex"}}``。

    ``options["rotation"]`` 是输出旋转角度（顺时针度数，与界面同义），只写进
    manifest 供查看器摆正视角，不进场景几何。bundle 临时目录在返回前被关闭释放。
    """
    from .preview_scene import PreviewSceneCompiler

    revision = int(options.get("revision") or 0)
    if not revision:
        revision = int(time.time())
    compile_options = dict(options)
    compile_options.pop("revision", None)
    compile_options["force"] = True

    def report(pct: float, message: str) -> None:
        if progress is not None:
            progress(15.0 + max(0.0, min(100.0, pct)) * 0.75, message)

    compile_options["progress"] = report
    if progress is not None:
        progress(10, i18n.tr('ui.web_export_v2.build_scene_payload.6084dd902b'))

    bundle = PreviewSceneCompiler.compile_full(
        session, style, revision,
        visibility=dict(visibility or {}), options=compile_options)
    try:
        payload = _payload_from_bundle(bundle, progress)
        raw_region = options.get("region")
        if raw_region is not None:
            try:
                region = tuple(float(value) for value in raw_region)
            except (TypeError, ValueError):
                region = None
            if region is not None and len(region) == 4:
                payload["manifest"]["initialRegion"] = list(region)
                # 同步旧版集成直接读取的清单镜像字段。
                payload["initialRegion"] = list(region)
        rotation = _output_rotation(options.get("rotation"))
        payload["manifest"]["rotation"] = rotation
        # 扁平镜像同上：老集成直接从顶层读字段。
        payload["rotation"] = rotation
        if raw_region is not None:
            from .export_clip import clip_scene_payload
            payload = clip_scene_payload(payload, raw_region)
        return payload
    finally:
        bundle.close()


def _payload_from_bundle(bundle, progress=None) -> dict:
    """把 bundle 目录内容读入内存并构造成内嵌 payload。"""
    sources: dict[str, Any] = {}
    images: dict[str, str] = {}
    fonts: dict[str, str] = {}
    poi_atlas_index: dict[str, Any] = {}
    for path in bundle.root.iterdir():
        if path.is_dir():
            continue
        name = path.name
        if name == "manifest.json":
            continue
        if name.endswith(".png"):
            images[name] = _data_uri(path)
        elif name.startswith("label-font-") and name.endswith((".ttf", ".otf")):
            fonts[name] = "data:font/" + path.suffix[1:] + ";base64," + base64.b64encode(
                path.read_bytes()).decode("ascii")
        elif name == "poi-sprite.json":
            poi_atlas_index[name] = json.loads(path.read_text(encoding="utf-8"))
        elif name.endswith((".json", ".geojson")):
            sources[name] = json.loads(path.read_text(encoding="utf-8"))
    if progress is not None:
        progress(91, i18n.tr('ui.web_export_v2._payload_from_bundle.cea7853717'))
    manifest = dict(bundle.manifest)
    embedded = {
        "sources": sources, "images": images, "fonts": fonts,
        "poiAtlasIndex": poi_atlas_index,
    }
    # 预览器读取稳定的清单封装；平铺镜像字段保留旧版 Python 接口兼容。
    return {"manifest": manifest, **manifest, "embedded": embedded}


def payload_from_preview_bundle(bundle, *, theme: dict[str, Any] | None = None,
                                visibility: dict[str, bool] | None = None,
                                region=None, rotation: Any = None) -> dict:
    """将已发布的中央预览打包为单文件 HTML；几何资源复用原包，实时主题和旋转写入清单副本。"""
    payload = _payload_from_bundle(bundle)
    manifest = dict(payload["manifest"])
    if theme is not None:
        manifest["theme"] = theme
    if visibility is not None:
        manifest["layers"] = {
            **dict(manifest.get("layers") or {}), **dict(visibility),
        }
    if region is not None:
        values = tuple(float(value) for value in region)
        if len(values) != 4:
            raise ValueError("region must contain four coordinates")
        manifest["initialRegion"] = list(values)
    manifest["rotation"] = _output_rotation(rotation)
    payload["manifest"] = manifest
    # 保持旧版集成读取的平铺镜像与标准清单一致。
    payload.update(manifest)
    if region is not None:
        from .export_clip import clip_scene_payload
        payload = clip_scene_payload(payload, region)
    return payload


def _viewer_assets(viewer_dist: Path) -> tuple[Path, Path, Path]:
    """从 dist/index.html 提取三类资产路径，缺文件时给出构建提示。"""
    index = viewer_dist / "index.html"
    if not index.is_file():
        raise RuntimeError(
            i18n.tr('ui.web_export_v2._viewer_assets.1669cd8574', p0=index))
    text = index.read_text(encoding="utf-8")
    js_match, css_match = _JS_RE.search(text), _CSS_RE.search(text)
    if not js_match or not css_match:
        raise RuntimeError(i18n.tr('ui.web_export_v2._viewer_assets.28040ea0ef', p0=index))
    assets = viewer_dist / "assets"
    paths = (assets / js_match.group(1), assets / css_match.group(1),
             assets / "maplibre-gl-csp-worker.js")
    missing = [str(p) for p in paths if not p.is_file()]
    if missing:
        raise RuntimeError(
            i18n.tr('ui.web_export_v2._viewer_assets.3c4c241c6f', p0=missing))
    return paths


def render_export_html(payload: dict, viewer_dist: Path, city: str = "") -> str:
    """把 payload 与 Viewer 三件套组装成单文件 HTML（无 CSP meta）。"""
    js_path, css_path, worker_path = _viewer_assets(viewer_dist)
    css = css_path.read_text(encoding="utf-8")
    js = js_path.read_text(encoding="utf-8")
    worker = worker_path.read_text(encoding="utf-8")
    title = i18n.tr('ui.web_export_v2.render_export_html.cee2179528', p0=city) if city else i18n.tr('ui.web_export_v2.render_export_html.0fe5c970b8')
    worker_json = _escape_script(json.dumps(worker, ensure_ascii=False))
    payload_json = _escape_script(_json_text(payload))
    # 旧版预览器可能早于适配后的 Vellum 标注和此提示。
    notice_path = viewer_dist / "VELLUM_LICENSE.txt"
    vellum_notice = notice_path.read_text(encoding="utf-8") if notice_path.is_file() else ""
    i18next_notice_path = viewer_dist / "I18NEXT_LICENSE.txt"
    i18next_notice = i18next_notice_path.read_text(encoding="utf-8") if i18next_notice_path.is_file() else ""
    dependency_notices = (viewer_dist / "THIRD_PARTY_LICENSES.txt").read_text(encoding="utf-8")
    dependency_notices = dependency_notices.replace("--", "&#45;&#45;")
    return f"""<!DOCTYPE html>
<html lang="{escape(i18n.LANG, quote=True)}">
<head>
<!-- Web preview dependencies
{dependency_notices}
-->
<!-- Vellum
{vellum_notice}
-->
<!-- i18next
{i18next_notice}
-->
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{escape(title)}</title>
<style>
{css}
</style>
</head>
<body>
<div id="map"></div>
<div id="selection" aria-hidden="true"></div>
<div id="status" hidden></div>
<div id="hint" hidden></div>
<script>window.__CSL_SCENE_WORKER_SRC__={worker_json};</script>
<script>window.__CSL_SCENE__={payload_json};</script>
<script type="module">{_escape_script(js)}</script>
</body>
</html>
"""


def export_preview_bundle(bundle, city: str, out_path, *,
                          theme: dict[str, Any] | None = None,
                          visibility: dict[str, bool] | None = None,
                          region=None, rotation: Any = None) -> str:
    """将当前中央预览保存为独立 HTML，不重新解析文档或编译场景。"""
    payload = payload_from_preview_bundle(
        bundle, theme=theme, visibility=visibility, region=region,
        rotation=rotation)
    viewer_dist = resource_dir("viewer") / "dist"
    html = render_export_html(payload, viewer_dist, city=city)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return str(out)


def export_web(mapdata_or_session, style: Any, city: str, out_path,
               options: Optional[dict[str, Any]] = None,
               callbacks: Optional[dict[str, Callable]] = None) -> str:
    """单文件 MapLibre HTML 导出的编排器（GUI 与 CLI 共用，同步执行）。

    mapdata_or_session：DocumentSession 或 MapDocument（自动包成会话）。
    style：Style 或主题 data dict（与实时预览一致，注意 POI 密度/合并设置）。
    out_path：输出 .html 路径。options：透传给场景编译的选项
    （revision/progress/visibility/rotation 等）。
    ``rotation`` 为输出旋转角度（顺时针度数）；callbacks 可选键：
    ``progress(pct, msg)`` / ``web_done(path)`` / ``error(exc)``。
    返回实际写入的路径。
    """
    callbacks = callbacks or {}
    progress = callbacks.get("progress")
    if progress is not None:
        progress(0, i18n.tr('ui.web_export_v2.export_web.29024b3e04'))
    try:
        session = _session_for(mapdata_or_session)
        style_data = getattr(style, "data", style)
        options = dict(options or {})
        revision = int(options.get("revision") or time.time())
        payload = build_scene_payload(
            session, style_data, {**options, "revision": revision},
            visibility=options.get("visibility"), progress=progress)
        if progress is not None:
            progress(95, i18n.tr('ui.web_export_v2.export_web.eae840aba7'))
        viewer_dist = resource_dir("viewer") / "dist"
        html = render_export_html(payload, viewer_dist, city=city)
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")
        if progress is not None:
            progress(100, i18n.tr('ui.web_export_v2.export_web.b99e3597ca'))
        done = callbacks.get("web_done")
        if done is not None:
            done(str(out))
        return str(out)
    except Exception as exc:  # noqa: BLE001
        error = callbacks.get("error")
        if error is not None:
            error(exc)
        raise
