"""冻结二维作品布局；网页和 PNG 只消费已确定的几何与局部图像资源。"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import io
import json
import math
from concurrent.futures import Future
from functools import cached_property

import aggdraw
from PIL import Image, ImageColor

from .cmm.model import RenderJob
from .export_region import normalize_bounds, output_rotation
from .renderer import _build_transform

SCHEMA_VERSION = 1
MAX_EDGE = 32768


def _json(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _numbers(values, count, name):
    if not isinstance(values, (list, tuple)) or len(values) != count or any(
            type(v) not in (int, float) or not math.isfinite(v) for v in values):
        raise ValueError(f"{name} requires {count} finite numbers")
    return tuple(float(v) for v in values)


def color(value: str) -> str:
    r, g, b, a = ImageColor.getcolor(value, "RGBA")
    return f"#{r:02x}{g:02x}{b:02x}{a:02x}"


def image_resource(image: Image.Image) -> dict:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    data = buffer.getvalue()
    return {"id": hashlib.sha256(data).hexdigest(), "width": image.width, "height": image.height,
            "url": "data:image/png;base64," + base64.b64encode(data).decode("ascii")}


def circle(object_id: str, center, diameter: float, *, fill: str = "white",
           stroke: str = "#39424c", stroke_width: float = 1.5) -> dict:
    x, y = center
    radius = diameter / 2
    return {"id": object_id, "kind": "circle", "center": [x, y], "diameter": diameter,
            "fill": color(fill), "stroke": color(stroke), "strokeWidth": stroke_width,
            "bbox": [x-radius, y-radius, x+radius, y+radius]}


def polyline(object_id: str, points, width: float, stroke: str) -> dict:
    points = [list(point) for point in points]
    xs, ys = zip(*points)
    radius = width / 2
    return {"id": object_id, "kind": "polyline", "points": points, "width": width,
            "stroke": color(stroke),
            "bbox": [min(xs)-radius, min(ys)-radius, max(xs)+radius, max(ys)+radius]}


def bitmap(object_id: str, resource: dict, bbox, *, text_run: dict | None = None) -> dict:
    item = {"id": object_id, "kind": "image", "resource": resource["id"], "bbox": list(bbox)}
    if text_run is not None:
        item["textRun"] = text_run
    return item


def _validate(payload):
    if set(payload) - {'previewBase'} != {"schemaVersion", "documentEpoch", "revision", "job", "styleId", "canvas", "background", "objects", "resources"}:
        raise ValueError("Unexpected artwork fields; observation is not artwork data")
    if payload.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("Unsupported artwork schema")
    for key in ("documentEpoch", "revision"):
        if type(payload.get(key)) is not int or payload[key] < 0:
            raise ValueError(f"Invalid artwork {key}")
    job = RenderJob.from_dict(payload["job"])
    if 'previewBase' in payload:
        base = payload['previewBase']
        if (job.kind != 'normal' or not isinstance(base, dict) or
                base.get('layoutRevision') != payload['revision'] or
                not isinstance(base.get('theme'), dict) or
                not isinstance(base.get('requiredSources'), list) or
                not isinstance(base.get('layers'), list) or
                not all(isinstance(layer, str) for layer in base['layers']) or
                not all(isinstance(source, str) for source in base['requiredSources'])):
            raise ValueError('Invalid artwork preview base recipe')
    if job.kind not in {"normal", "transit"}:
        raise ValueError("Artwork requires a two-dimensional geographic map")
    w, h = payload["canvas"]["width"], payload["canvas"]["height"]
    if any(type(v) is not int or not 1 <= v <= MAX_EDGE for v in (w, h)):
        raise ValueError("Invalid artwork dimensions")
    if (w, h) != (job.canvas["width"], job.canvas["height"]):
        raise ValueError("Artwork canvas and render job differ")
    bounds = normalize_bounds(job.derive_params["world_bounds"])
    rotation = output_rotation(job.derive_params["rotation"])
    transform = _build_transform(w, h, bounds, rotation)
    a, b, c, d, e, f = _numbers(payload["canvas"]["worldToArtwork"], 6, "worldToArtwork")
    for x, z in ((0, 0), (1, 0), (0, 1)):
        expected = transform(x, z)
        if max(abs(a*x+c*z+e-expected[0]), abs(b*x+d*z+f-expected[1])) > 1e-7:
            raise ValueError("Artwork transform differs from the PNG transform")
    if not isinstance(payload["styleId"], str) or not payload["styleId"]:
        raise ValueError("Artwork requires a resolved style identity")
    color(payload["background"])
    resources = payload["resources"]
    for key, resource in resources.items():
        url = resource["url"]
        if not isinstance(url, str) or not url.startswith("data:image/png;base64,"):
            raise ValueError("Artwork resources must be local PNG data")
        data = base64.b64decode(url.split(",", 1)[1], validate=True)
        if len(data) > 16 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != key or resource["id"] != key:
            raise ValueError("Artwork resource size or identity mismatch")
        with Image.open(io.BytesIO(data)) as image:
            if image.format != "PNG" or image.size != (resource["width"], resource["height"]) or image.width * image.height > 16_777_216:
                raise ValueError("Invalid artwork resource dimensions")
            image.verify()
    ids = set()
    for item in payload["objects"]:
        if not isinstance(item["id"], str) or not item["id"] or item["id"] in ids:
            raise ValueError("Artwork object IDs must be nonempty and unique")
        ids.add(item["id"])
        x0, y0, x1, y1 = _numbers(item["bbox"], 4, "bbox")
        if x1 <= x0 or y1 <= y0:
            raise ValueError("Artwork object has an empty box")
        if item["kind"] == "circle":
            _numbers(item["center"], 2, "center")
            diameter, stroke = _numbers([item["diameter"], item["strokeWidth"]], 2, "circle sizes")
            if diameter <= 0 or not 0 <= stroke <= diameter / 2:
                raise ValueError("Invalid artwork circle sizes")
            color(item["fill"])
            color(item["stroke"])
        elif item["kind"] == "polyline":
            if item.get("lineCap", "round") not in {"round", "butt"}:
                raise ValueError("Invalid artwork line cap")
            if len(item["points"]) < 2:
                raise ValueError("Artwork line requires two points")
            for point in item["points"]:
                _numbers(point, 2, "line point")
            if _numbers([item["width"]], 1, "line width")[0] <= 0:
                raise ValueError("Artwork line width must be positive")
            color(item["stroke"])
        elif item["kind"] == "image":
            if item["resource"] not in resources:
                raise ValueError("Missing artwork image resource")
        else:
            raise ValueError(f"Unsupported artwork primitive: {item['kind']}")


@dataclass(frozen=True)
class ArtworkSnapshot:
    """规范 JSON 字节隔离调用方的可变字典；观察数据不属于作品。"""

    _encoded: bytes
    _render_plan: object | None = None

    @classmethod
    def create(cls, job: RenderJob, *, document_epoch: int, revision: int,
               objects, resources=(), style_id: str, background: str = "white", preview_base=None):
        width, height = job.canvas["width"], job.canvas["height"]
        transform = _build_transform(width, height, job.derive_params["world_bounds"],
                                     job.derive_params["rotation"])
        e, f = transform(0, 0)
        ax, ay = transform(1, 0)
        zx, zy = transform(0, 1)
        payload = {"schemaVersion": SCHEMA_VERSION, "documentEpoch": document_epoch,
            "revision": revision, "job": job.to_dict(), "styleId": style_id,
            "canvas": {"width": width, "height": height,
                       "worldToArtwork": [ax-e, ay-f, zx-e, zy-f, e, f]},
            "background": color(background), "objects": list(objects),
            "resources": {resource["id"]: resource for resource in resources}}
        if preview_base is not None:
            payload['previewBase'] = preview_base
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, payload):
        encoded = _json(payload)
        _validate(json.loads(encoded))
        return cls(encoded)

    def to_dict(self) -> dict:
        return json.loads(self._encoded)

    @property
    def digest(self) -> str:
        return hashlib.sha256(self._encoded).hexdigest()

    @cached_property
    def identity(self) -> tuple[int, int]:
        value = self.to_dict()
        return value["documentEpoch"], value["revision"]


class ArtworkState:
    """串行设置入口请求修订，异步工作仅可提交该文档最新结果。"""

    def __init__(self, document_epoch: int):
        self.document_epoch = document_epoch
        self.revision = 0
        self.snapshot: ArtworkSnapshot | None = None
        self.error: str | None = None
        self.presented: tuple[int, int] | None = None
        self._exports = {}

    def set_document(self, document_epoch: int) -> None:
        if type(document_epoch) is not int or document_epoch <= self.document_epoch:
            raise ValueError("Document epochs must increase")
        self.cancel_exports()
        self.document_epoch = document_epoch
        self.revision = 0
        self.snapshot = None
        self.error = None
        self.presented = None

    def request(self) -> tuple[int, int]:
        self.revision += 1
        self.error = None
        return self.document_epoch, self.revision

    def accept(self, snapshot: ArtworkSnapshot) -> bool:
        for pending in self._exports.pop(snapshot.identity, ()):
            if not pending.done():
                pending.set_result(snapshot)
        if snapshot.identity != (self.document_epoch, self.revision):
            return False
        self.snapshot = snapshot
        return True

    def acknowledge(self, identity) -> bool:
        if self.snapshot is None or tuple(identity) != self.snapshot.identity or tuple(identity) != (self.document_epoch, self.revision):
            return False
        self.presented = tuple(identity)
        return True

    @property
    def status(self) -> str:
        if self.error:
            return "failed"
        if self.snapshot is None or self.snapshot.identity != (self.document_epoch, self.revision):
            return "pending"
        return "presented" if self.presented == self.snapshot.identity else "ready"

    def fail(self, identity, error: str) -> bool:
        for pending in self._exports.pop(tuple(identity), ()):
            if not pending.done():
                pending.set_exception(RuntimeError(str(error)))
        if tuple(identity) != (self.document_epoch, self.revision):
            return False
        self.error = error
        return True

    def pin_export(self) -> Future:
        """在接受导出时固定修订；完成通知由所属调度线程提交。"""
        if self.revision == 0:
            raise RuntimeError('No artwork job has been accepted')
        pending = Future()
        identity = self.document_epoch, self.revision
        if self.error:
            pending.set_exception(RuntimeError(str(self.error)))
        elif self.snapshot is not None and self.snapshot.identity == identity:
            pending.set_result(self.snapshot)
        else:
            self._exports.setdefault(identity, []).append(pending)
        return pending

    def is_pinned(self, identity) -> bool:
        pending = [future for future in self._exports.get(tuple(identity), ()) if not future.done()]
        if pending:
            self._exports[tuple(identity)] = pending
        else:
            self._exports.pop(tuple(identity), None)
        return bool(pending)

    def cancel_exports(self) -> None:
        pending, self._exports = self._exports, {}
        for group in pending.values():
            for future in group:
                future.cancel()

    def freeze(self) -> ArtworkSnapshot:
        if self.error:
            raise RuntimeError(self.error)
        if self.snapshot is None or self.snapshot.identity != (self.document_epoch, self.revision):
            raise RuntimeError("Latest artwork layout is not ready")
        return self.snapshot


def render_artwork(snapshot: ArtworkSnapshot, *, scale: float = 1, aa: int = 1) -> Image.Image:
    """对完成布局等比栅格化；不读取窗口、重新选择或测量文字。"""
    if type(scale) not in (int, float) or not math.isfinite(scale) or scale <= 0 or type(aa) is not int or aa not in (1, 2, 4):
        raise ValueError("Invalid artwork raster scale or AA")
    payload = snapshot.to_dict()
    w, h = payload["canvas"]["width"], payload["canvas"]["height"]
    output = (round(w*scale), round(h*scale))
    if min(output) < 1 or max(output) > MAX_EDGE:
        raise ValueError("Invalid artwork output dimensions")
    s = scale * aa
    plan = snapshot._render_plan
    if 'previewBase' in payload and plan is None:
        raise ValueError('Draft export requires its bound document and render recipe')
    plan_data = json.loads(plan.encoded) if plan is not None else None
    image = (plan.render_base((output[0]*aa, output[1]*aa), plan_data) if plan is not None else
             Image.new("RGBA", (output[0]*aa, output[1]*aa), payload["background"]))
    draw = None
    for item in payload["objects"]:
        if item["kind"] != "image" and draw is None:
            draw = aggdraw.Draw(image)
        if item["kind"] == "circle":
            x, y = (v*s for v in item["center"])
            r, stroke = item["diameter"]*s/2, item["strokeWidth"]*s
            radius = r-stroke/2
            draw.ellipse((x-radius, y-radius, x+radius, y+radius), aggdraw.Brush(ImageColor.getcolor(item["fill"], "RGBA")),
                         aggdraw.Pen(ImageColor.getcolor(item["stroke"], "RGBA"), stroke) if stroke else None)
        elif item["kind"] == "polyline":
            points = [(x*s, y*s) for x, y in item["points"]]
            path = aggdraw.Path()
            path.moveto(*points[0])
            for point in points[1:]:
                path.lineto(*point)
            ink = ImageColor.getcolor(item["stroke"], "RGBA")
            draw.path(path, None, aggdraw.Pen(ink, item["width"]*s))
            r = item["width"]*s/2
            cap_points = points if item.get("lineCap", "round") == "round" else points[1:-1]
            for x, y in cap_points:
                draw.ellipse((x-r, y-r, x+r, y+r), aggdraw.Brush(ink))
        else:
            # AGG 持有自己的画布缓冲；只在切换到局部位图时提交，避免每个
            # 几何对象都复制整张高分辨率画布，同时保持对象的合成顺序。
            if draw is not None:
                draw.flush()
                draw = None
            resource = payload["resources"][item["resource"]]
            x0, y0, x1, y1 = item["bbox"]
            left, top, right, bottom = (round(v*s) for v in (x0, y0, x1, y1))
            if right <= left or bottom <= top:
                continue
            clipped = max(0, left), max(0, top), min(image.width, right), min(image.height, bottom)
            if clipped[2] <= clipped[0] or clipped[3] <= clipped[1]:
                continue
            with (plan.open_resource(item['id'], max(4, s), plan_data) if plan is not None else
                  Image.open(io.BytesIO(base64.b64decode(resource["url"].split(",", 1)[1])))) as asset:
                box = ((clipped[0]-left)*asset.width/(right-left), (clipped[1]-top)*asset.height/(bottom-top),
                       (clipped[2]-left)*asset.width/(right-left), (clipped[3]-top)*asset.height/(bottom-top))
                tile = asset.convert("RGBA").resize((clipped[2]-clipped[0], clipped[3]-clipped[1]),
                                                   Image.Resampling.LANCZOS, box=box)
            image.alpha_composite(tile, clipped[:2])
            tile.close()
    if draw is not None:
        draw.flush()
    if aa > 1:
        image = image.resize(output, Image.Resampling.LANCZOS)
    return image
