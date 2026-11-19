"""由已冻结的公共交通输出参数建立共享作品。"""
from __future__ import annotations

import copy
import hashlib
import json
import time

from PIL import Image, ImageDraw

from .artwork import ArtworkSnapshot, MAX_EDGE
from .artwork_capture import ArtworkCapture
from .cmm.session import DocumentSession
from .layers.base import RenderContext
from .render_jobs import build_artwork_job
from .renderer import _build_transform


def build_transit_artwork(job, document, *, document_epoch, revision,
                          session=None, progress=None, sizing=None, diagnostics=None, draft=False):
    """复用交通最终布局；观察窗口和本次导出倍率不参与生成。"""
    from .gui_render_worker import (_build_transit_renderer, _load_ui_style,
                                    _render_transit_base)
    from .layers.watermark_layer import WatermarkLayer
    started = time.perf_counter()

    if job["kind"] != "transit" or job["opts"].get("abstract") or job["opts"].get("pure"):
        raise ValueError("Artwork requires a geographic transit job")
    if job.get("region") is None or job.get("region_policy"):
        raise ValueError("Resolve the draft region before building artwork")
    if type(job['size']) is not int or not 1 <= job['size'] <= MAX_EDGE:
        raise ValueError('Invalid artwork base long edge')
    request = copy.deepcopy(job)
    if 'preview_sizing' in request['ui']:
        raise ValueError('preview_sizing is retired; use elementScale')
    request["aa"] = 1
    artwork_job = build_artwork_job("transit", request["region"],
        long_edge=request["size"], rotation=request["ui"]["rotation"],
        element_scale=request.get('elementScale', request['size'] / 4096))
    request['ui']['elementScale'] = artwork_job.derive_params['elementScale']
    canvas = artwork_job.canvas
    size = canvas["width"], canvas["height"]
    bounds = artwork_job.derive_params["world_bounds"]
    transform = _build_transform(*size, bounds, artwork_job.derive_params["rotation"])
    if session is None:
        session = DocumentSession(document=document)
    elif session.document is not document:
        raise ValueError("Artwork session belongs to another document")
    from . import transit
    from .i18n import tr
    present = {transit.normalize_type(item.type) for item in session._legacy_view().transports}
    types = [item for item in request["types"] if item in present]
    if request["types"] and not types and request["opts"].get("line_ids") != []:
        raise ValueError(tr("gui.transit.no_transport"))

    overlay = _build_transit_renderer(request["ui"], request["style"],
        request['types'], size, stations=bool(request["opts"]["stations"]), opts=request["opts"])
    from .transit_artwork_sizing import prepare_style
    sizing_record = prepare_style(overlay.style, document, size, sizing=sizing, session=session,
                                  element_scale=artwork_job.derive_params['elementScale'])
    prepared = time.perf_counter()

    def base_tiles():
        capture = ArtworkCapture()
        base, _base_renderer = _render_transit_base(request, document, session, size,
                                                   bounds, progress=progress)
        # 底图按作品坐标冻结为独立资源；交通几何、站点、标签保持独立对象。
        # 分片带 8px 相邻内容，防止网页分数缩放及 PNG 下采样在片边露出白缝。
        for y in range(0, size[1], 512):
            for x in range(0, size[0], 512):
                box = (max(0, x - 8), max(0, y - 8),
                       min(x + 520, size[0]), min(y + 520, size[1]))
                capture.image(f"base:{x}:{y}", base.crop(box), box)
        base.close()
        return capture.objects, capture.resources, capture.diagnostics

    def transport_objects():
        capture = ArtworkCapture()
        image = Image.new("RGBA", size)
        context = RenderContext(image, ImageDraw.Draw(image), transform, overlay.style,
            world_x0=bounds[0], world_z0=bounds[1], world_x1=bounds[2], world_z1=bounds[3],
            zoom=transform.zoom, final_zoom=transform.zoom, detail_mode=artwork_job.detail_mode,
            rotation=artwork_job.derive_params["rotation"], canvas_width=size[0], canvas_height=size[1],
            shared={"artwork_capture": capture}, session=session, render_job=artwork_job,
            progress=progress)
        for layer in overlay._layers:
            layer.render(context, document)
        # 没有设施子图层候选时也必须交付已准备的徽章和站名。
        for draw_labels in capture.labels:
            draw_labels()
        for owner, args in capture.station_names:
            owner._draw_icon_name(*args)
        image.close()
        return capture.objects, capture.resources, capture.diagnostics

    def component(name, key, draw):
        if not draft:
            return draw(), False
        cache = session.service_cache
        service = 'transit.artwork.' + name
        current = cache.get(service, key)
        if current is not None:
            return current, True
        current = draw()
        cache.keep_only(service, key)
        cache.put(service, key, current)
        return current, False

    base_key = overlay_key = None
    if draft:
        from .gui_render_worker import _transit_base_layers
        from .transit_label_styles import palette
        ui = {k: v for k, v in request['ui'].items() if k not in ('style_overrides', 'poi_mapping_override')}
        mapping = request.get('_poi_mapping_snapshot', request['ui'].get('poi_mapping_override'))
        common = {'epoch': document_epoch, 'job': artwork_job.to_dict()}
        style = _load_ui_style(request['ui'], request['style']).data
        base_style = {k: v for k, v in style.items() if k not in (
            'name', 'description', 'watermark', 'labels', '_poi_mapping_override')}
        opts = request['opts']
        ordinary = opts.get('ordinary_pois', 'hidden')
        if ordinary == 'hidden':
            base_style.pop('transports', None)
            # 建筑与 POI 占地仍从映射图标取色，保留其图集与明暗设置。
            icons = base_style.get('poi_icons', {})
            base_style['poi_icons'] = {key: icons.get(key) for key in ('appearance', 'sprite')}
            ui = {key: ui[key] for key in ('accurate_intersections', 'terrain', 'district_ids', 'park_ids') if key in ui}
        base_key = {**common, 'ui': ui, 'mapping': mapping,
                    'style': base_style, 'layers': _transit_base_layers(request['layers'], opts),
                    'ordinary': ordinary, 'mask': opts.get('transit_mask'),
                    'themeMask': style.get('transports', {}).get('transit_view', {}).get('mask_intensity')}
        overlay_key = {**common, 'mapping': mapping, 'transports': overlay.style.get('transports'),
                       'poi': overlay.style.get('poi_icons'), 'types': types,
                       'palette': palette(overlay.style.get('canvas.background', 'white'))}
    (base_objects, base_resources, _), base_reused = component('base', base_key, base_tiles)
    base_finished = time.perf_counter()
    (objects, resources, metrics), overlay_reused = component('symbols', overlay_key, transport_objects)
    capture = ArtworkCapture()
    capture.objects = [*base_objects, *objects]
    capture.resources = {**base_resources, **resources}
    capture.diagnostics = {**metrics, 'sizing': sizing_record, 'reusedBase': base_reused,
                           'reusedLayout': overlay_reused}
    layout_finished = time.perf_counter()
    if "watermark" in request["layers"]:
        watermark = Image.new("RGBA", size)
        WatermarkLayer.draw_on_image(watermark,
            overlay.style, session._legacy_view(),
            custom_text=request.get("watermark_text"))
        box = watermark.getbbox()
        if box is not None:
            capture.image("watermark", watermark.crop(box), box)
    identity_ui = copy.deepcopy(request['ui'])
    if '_poi_mapping_snapshot' in request:
        mapping_id = hashlib.sha256(json.dumps(request['_poi_mapping_snapshot'],
            sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        identity_ui['poi_mapping_override'] = mapping_id
        identity_ui['style_overrides'][request['style']]['_poi_mapping_override'] = mapping_id
    style_id = hashlib.sha256(json.dumps({'artworkSizing': sizing_record, "ui": identity_ui, "opts": request["opts"],
        "types": request["types"], "layers": request["layers"], "style": request["style"]},
        ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()
    snapshot = ArtworkSnapshot.create(artwork_job, document_epoch=document_epoch, revision=revision,
        objects=capture.objects, resources=capture.resources.values(), style_id=style_id)
    if diagnostics is not None:
        diagnostics.update(capture.diagnostics)
        diagnostics['timings'] = {'prepare': prepared-started, 'base': base_finished-prepared,
            'layout': layout_finished-base_finished, 'freeze': time.perf_counter()-layout_finished,
            'total': time.perf_counter()-started}
        diagnostics['payload'] = {'objects': len(capture.objects), 'resources': len(capture.resources),
                                  'bytes': len(snapshot._encoded)}
    return snapshot
