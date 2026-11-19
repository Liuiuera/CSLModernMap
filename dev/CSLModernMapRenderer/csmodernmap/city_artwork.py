"""由城市作业生成共享作品；观察状态不参与样式或布局。"""
from __future__ import annotations

import copy
import base64
import io
import tempfile
from pathlib import Path
import hashlib
import json
import time
from dataclasses import dataclass, replace

from PIL import Image, ImageDraw

from .artwork import ArtworkSnapshot, MAX_EDGE, color
from .artwork_capture import ArtworkCapture
from .city_artwork_sizing import prepare_style
from .cmm.session import DocumentSession
from .gui_render_worker import _build_normal_renderer
from .layers.base import RenderContext
from .layers.label_layer import LabelLayer
from .layers.poi_icon_layer import POIIconLayer
from .layers.transport_layer import TransportLayer
from .layers.watermark_layer import WatermarkLayer
from .render_jobs import build_artwork_job
from .renderer import _build_transform
from .fonts import font_resolution_scope


def _overlay_groups(style, layers, ui):
    """基础文字、水印各自绘制；POI 与交通共享占位，必须一起复用。"""
    from .transit_label_styles import palette
    groups = {}
    if any(isinstance(layer, LabelLayer) for layer in layers):
        groups['labels'] = (style.get('labels', {}), style.get('roads.use_game_names', False),
                            ui['road_names'], ui['district_names'],
                            ui.get('road_player_names', True), ui.get('road_asset_names', False))
    if any(isinstance(layer, (TransportLayer, POIIconLayer)) for layer in layers):
        badges = any(isinstance(layer, TransportLayer) and layer.transit_view for layer in layers)
        groups['symbols'] = (style.get('poi_icons', {}), style.get('transports', {}),
                             palette(style.get('canvas.background', 'white')) if badges else None,
                             ui.get('poi_mapping_override'))
    if any(isinstance(layer, WatermarkLayer) for layer in layers):
        name = style.get('name') if style.get('watermark.content.style_name', True) else None
        groups['watermark'] = (style.get('watermark', {}), name, ui.get('watermark_text'))
    return groups


def _overlay_group(layer):
    if isinstance(layer, LabelLayer):
        return 'labels'
    if isinstance(layer, WatermarkLayer):
        return 'watermark'
    return 'symbols'


@dataclass(frozen=True)
class CityRenderPlan:
    """仅后端持有正式绘制配方；不把底图或高采样资源传入网页。"""
    encoded: bytes
    session: DocumentSession

    def render_base(self, size, data):
        request = data['request']
        with tempfile.TemporaryDirectory(prefix='csl-artwork-base-') as mapping_dir:
            request = copy.deepcopy(request)
            if '_poi_mapping_snapshot' in request:
                path = Path(mapping_dir)/'poi_direct_mapping.json'
                path.write_text(json.dumps(request['_poi_mapping_snapshot']), encoding='utf-8')
                request['ui']['poi_mapping_override'] = str(path)
            return self._render_base(size, data['sizing'], request)

    def _render_base(self, size, sizing, request):
        renderer = _build_normal_renderer(request['ui'], request['style'], request['layers'],
                                          size, False, 'normal')
        from .pixel_sizes import prepare_style as resolve_pixels
        resolve_pixels(renderer.style, sizing['elementScale'],
                       output_scale=max(size) / sizing['longEdge'])
        renderer._layers = [layer for layer in renderer._layers if not isinstance(layer,
            (LabelLayer, POIIconLayer, TransportLayer, WatermarkLayer))]
        renderer.render_job = replace(renderer.render_job, canvas={'width': size[0], 'height': size[1]},
            derive_params={**renderer.render_job.derive_params,
                           'world_bounds': request['region'], 'rotation': request['ui']['rotation']})
        if renderer._resolve_parallel_workers() > 1 and renderer.tile_size == 0:
            renderer.tile_size = renderer._resolve_tile()
        renderer.parallel_workers = 1
        image = renderer.render(self.session.document, region=request['region'], session=self.session)
        if image.mode != 'RGBA':
            converted = image.convert('RGBA')
            image.close()
            return converted
        return image

    def open_resource(self, object_id, density, data):
        from .artwork_sampling import sample_path
        recipe = data['paths'].get(object_id)
        resource = data['resources'][recipe['source'] if recipe else data['bindings'][object_id]]
        asset = Image.open(io.BytesIO(base64.b64decode(resource['url'].split(',', 1)[1])))
        if recipe is None:
            return asset
        try:
            return sample_path(asset, recipe['strips'], recipe['bounds'],
                               recipe['textHeight'], recipe['pad'], density)
        finally:
            asset.close()


@font_resolution_scope()
def build_city_artwork(job, document, *, document_epoch, revision,
                       session=None, progress=None, sizing=None, diagnostics=None, draft=False):
    """进度为阶段内百分比；支持阶段的回调沿用 supports_stage_progress 约定。"""
    started = time.perf_counter()
    def report(percent, message, stage):
        if progress is not None:
            if getattr(progress, 'supports_stage_progress', False):
                progress(percent, message, stage=stage)
            else:
                progress(percent, message)
    report(0, 'prepare artwork', 'layout')
    # 既有普通 PNG 快照不含 kind；交通快照显式包含 kind='transit'。
    if job.get('kind', 'normal') != 'normal':
        raise ValueError('City artwork requires a normal map job')
    if job.get('region') is None or job.get('region_policy'):
        raise ValueError('Resolve the draft region before building artwork')
    if type(job['size']) is not int or not 1 <= job['size'] <= MAX_EDGE:
        raise ValueError('Invalid artwork base long edge')
    request = copy.deepcopy(job)
    if 'preview_sizing' in request['ui']:
        raise ValueError('preview_sizing is retired; use elementScale')
    request['aa'] = 1
    artwork_job = build_artwork_job('normal', request['region'],
        long_edge=request['size'], rotation=request['ui']['rotation'],
        element_scale=request.get('elementScale', request['size'] / 4096))
    request['ui']['elementScale'] = artwork_job.derive_params['elementScale']
    size = artwork_job.canvas['width'], artwork_job.canvas['height']
    bounds = artwork_job.derive_params['world_bounds']
    if session is None:
        session = DocumentSession(document)
    elif session.document is not document:
        raise ValueError('Artwork session belongs to another document')
    renderer = _build_normal_renderer(request['ui'], request['style'],
        request['layers'], size, False, 'normal')
    # 保留原分片计划，避免多进程各自持有全城数据导致内存峰值随并发放大。
    if renderer._resolve_parallel_workers() > 1 and renderer.tile_size == 0:
        renderer.tile_size = renderer._resolve_tile()
    renderer.parallel_workers = 1
    sizing_record = prepare_style(renderer.style, document, size, sizing=sizing, session=session,
                                  element_scale=artwork_job.derive_params['elementScale'])
    artwork_job = replace(artwork_job, layers=renderer.render_job.layers,
                          rule_overrides=renderer.render_job.rule_overrides)
    renderer.render_job = artwork_job
    layers = renderer._layers
    overlays = [layer for layer in layers if isinstance(layer,
        (LabelLayer, POIIconLayer, TransportLayer, WatermarkLayer))]
    identity_ui = copy.deepcopy(request['ui'])
    if '_poi_mapping_snapshot' in request:
        mapping_id = hashlib.sha256(json.dumps(request['_poi_mapping_snapshot'],
            sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        identity_ui['poi_mapping_override'] = mapping_id
        identity_ui['style_overrides'][request['style']]['_poi_mapping_override'] = mapping_id
    style_id = hashlib.sha256(json.dumps({'artworkSizing': sizing_record,
        'ui': identity_ui, 'layers': request['layers'], 'style': request['style'],
        'watermark': request.get('watermark_text')},
        ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()
    cache_key = None
    reused_groups = set()
    spans = {}
    layer_slots = {}
    if draft:
        background = renderer.style.get('canvas.background', 'white')
        key_request = artwork_job.to_dict()
        del key_request['layers']
        members = {}
        for layer in overlays:
            group = _overlay_group(layer)
            entries = members.setdefault(group, [])
            layer_slots[layer] = (group, len(entries))
            entries.append((layer.name, getattr(layer, 'transit_view', None), getattr(layer, 'sublayer', None)))
        cached = session.service_cache.get('city.artwork.draft', 'latest')
        groups = _overlay_groups(renderer.style, overlays, identity_ui)
        groups = {group: (settings, members[group]) for group, settings in groups.items()}
        cache_key = {'epoch': document_epoch, 'request': key_request, 'groups': groups,
                     'order': list(layer_slots.values())}
        if cached is not None:
            old_key, previous, previous_diagnostics, old_spans = cached
            if old_key['epoch'] == document_epoch and old_key['request'] == key_request:
                reused_groups = {key for key in groups if groups[key] == old_key['groups'].get(key)}
            if reused_groups or old_key == cache_key:
                payload = previous.to_dict()
                plan_data = json.loads(previous._render_plan.encoded)
        if cached is not None and old_key == cache_key:
            prepared = time.perf_counter()
            report(0, 'reuse artwork', 'layout')
            payload.update(documentEpoch=document_epoch, revision=revision,
                           job=artwork_job.to_dict(), styleId=style_id, background=color(background))
            base = payload['previewBase']
            base['layoutRevision'] = revision
            base.pop('sceneId', None)
            base.pop('revision', None)
            base.update(request.get('_preview_binding', {}))
            from .preview_scene import preview_theme
            base['theme'] = preview_theme(renderer.style)
            base['layers'] = list(request['layers'])
            base['requiredSources'] = required_sources(request['layers'], renderer.style.get('terrain.mode', 'simple'))
            plan_data['request'] = request
            plan = CityRenderPlan(json.dumps(plan_data, ensure_ascii=False, allow_nan=False).encode(), session)
            # 对象和资源已通过快照边界校验，本路径只替换受控修订和底图元数据。
            snapshot = ArtworkSnapshot(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                separators=(',', ':'), allow_nan=False).encode(), plan)
            if diagnostics is not None:
                diagnostics.update(copy.deepcopy(previous_diagnostics))
                diagnostics.update(reusedLayout=True, reusedGroups=sorted(groups), overlaySeconds={}, deferredSeconds=0)
                diagnostics['sizing'] = sizing_record
                diagnostics['payload']['bytes'] = len(snapshot._encoded)
                diagnostics['timings'] = {'prepare': prepared-started, 'base': 0,
                    'layout': 0, 'freeze': time.perf_counter()-prepared, 'total': time.perf_counter()-started}
            report(100, 'reuse artwork', 'layout')
            return snapshot
    def stage_for(layer):
        if isinstance(layer, LabelLayer):
            return 'labels'
        if isinstance(layer, POIIconLayer):
            return 'icons'
        if isinstance(layer, TransportLayer):
            return 'transport'
        return 'layout'
    totals = {stage: sum(stage_for(layer) == stage for layer in overlays)
              for stage in ('layout', 'labels', 'icons', 'transport')}
    # 准备、延迟绘制和快照冻结各算一项；图层内部通知不算完成了整个图层。
    totals['layout'] += 3
    completed = dict.fromkeys(totals, 0)
    def complete(stage, message):
        completed[stage] += 1
        report(100 * completed[stage] / totals[stage], message, stage)
    complete('layout', 'prepare artwork')
    renderer._layers = [layer for layer in layers if layer not in overlays]
    prepared = time.perf_counter()
    capture = ArtworkCapture(draft=draft)
    capture.diagnostics['sizing'] = sizing_record
    if not draft:
        base = renderer.render(document, region=bounds, session=session,
            progress=lambda fraction, message: report(fraction * 100, message, 'base'))
        try:
            capture.tiled_image('base', base)
        finally:
            base.close()
    base_finished = time.perf_counter()
    transform = _build_transform(*size, bounds, artwork_job.derive_params['rotation'])
    image = Image.new('RGBA', size)
    context = RenderContext(image, ImageDraw.Draw(image), transform, renderer.style,
        world_x0=bounds[0], world_z0=bounds[1], world_x1=bounds[2], world_z1=bounds[3],
        zoom=transform.zoom, final_zoom=transform.zoom, detail_mode=artwork_job.detail_mode,
        rotation=artwork_job.derive_params['rotation'], canvas_width=size[0], canvas_height=size[1],
        shared={'artwork_capture': capture}, session=session, render_job=artwork_job,
        progress=None)
    station_pois = bool(renderer.style.get('transports.station.enabled', False))
    def late(layer):
        return layer.late or (station_pois and isinstance(layer, POIIconLayer))
    # 保留普通城市的 early/late 顺序；启用站点时 POI 跟在线路与站点之后。
    overlays = [layer for layer in overlays if not late(layer)] + [layer for layer in overlays if late(layer)]
    try:
        for layer in overlays:
            layer_started = time.perf_counter()
            stage = stage_for(layer)
            report(100 * completed[stage] / totals[stage], layer.name, stage)
            context.progress = lambda _fraction, message: report(
                100 * completed[stage] / totals[stage], message, stage)
            if not layer.supports(document):
                complete(stage, layer.name)
                continue
            start = len(capture.objects)
            if _overlay_group(layer) in reused_groups:
                capture.reuse_objects(payload, plan_data, old_spans[layer_slots[layer]])
            elif isinstance(layer, WatermarkLayer):
                watermark = Image.new('RGBA', size)
                watermark_ctx = replace(context, image=watermark, draw=ImageDraw.Draw(watermark))
                layer.render(watermark_ctx, document)
                box = watermark.getbbox()
                if box is not None:
                    capture.image('watermark', watermark.crop(box), box)
                watermark.close()
            else:
                layer.render(context, document)
            if draft:
                spans[layer_slots[layer]] = (start, len(capture.objects))
            capture.diagnostics.setdefault('overlaySeconds', {})[layer.name] = time.perf_counter()-layer_started
            complete(stage, layer.name)
        deferred_started = time.perf_counter()
        deferred_total = len(capture.labels) + len(capture.station_names)
        deferred_done = 0
        def deferred_progress():
            fraction = deferred_done / deferred_total if deferred_total else 0
            report(100 * (completed['layout'] + fraction) / totals['layout'],
                   'deferred labels', 'layout')
        deferred_progress()
        start = len(capture.objects)
        if 'symbols' in reused_groups:
            capture.reuse_objects(payload, plan_data, old_spans['deferred'])
        for draw_labels in capture.labels:
            draw_labels()
            deferred_done += 1
            deferred_progress()
        for owner, args in capture.station_names:
            owner._draw_icon_name(*args)
            deferred_done += 1
            deferred_progress()
        spans['deferred'] = (start, len(capture.objects))
        capture.diagnostics['deferredSeconds'] = time.perf_counter()-deferred_started
        complete('layout', 'deferred labels')
    finally:
        image.close()
    layout_finished = time.perf_counter()
    preview_base = None
    if draft:
        from .preview_scene import preview_theme
        preview_base = {'theme': preview_theme(renderer.style),
            'layers': list(request['layers']), 'requiredSources': required_sources(request['layers'],
                renderer.style.get('terrain.mode', 'simple')), 'layoutRevision': revision,
            **request.get('_preview_binding', {})}
    snapshot = ArtworkSnapshot.create(artwork_job, document_epoch=document_epoch, revision=revision,
        objects=capture.objects, resources=capture.resources.values(), style_id=style_id,
        background=renderer.style.get('canvas.background', 'white') if draft else 'white',
        preview_base=preview_base)
    if draft:
        reference = {'size_contract': 'pixel-v2', 'elementScale': sizing_record['scale'],
                     'longEdge': max(size)}
        plan = CityRenderPlan(json.dumps({'request': request, 'sizing': reference,
            'resources': capture.export_resources, 'bindings': capture.export_bindings,
            'paths': capture.path_recipes},
            ensure_ascii=False, allow_nan=False).encode(), session)
        snapshot = replace(snapshot, _render_plan=plan)
    measured = copy.deepcopy(capture.diagnostics)
    for group, fields in (('symbols', ('names', 'badges', 'symbols', 'facilitySelection')),
                          ('labels', ('baseLabels', 'labelBoxes'))):
        if group in reused_groups:
            measured.update({field: previous_diagnostics[field] for field in fields if field in previous_diagnostics})
    measured['reusedGroups'] = sorted(reused_groups)
    measured['reusedLayout'] = False
    measured['timings'] = {'prepare': prepared-started, 'base': base_finished-prepared,
            'layout': layout_finished-base_finished, 'freeze': time.perf_counter()-layout_finished,
            'total': time.perf_counter()-started}
    measured['payload'] = {'objects': len(capture.objects), 'resources': len(capture.resources),
                                  'bytes': len(snapshot._encoded)}
    measured['baseReuse'] = {'enabled': draft, 'renders': 0 if draft else 1,
                                    'transferredResources': 0 if draft else sum(
                                        item['id'].startswith('base:') for item in capture.objects)}
    if cache_key is not None:
        session.service_cache.put('city.artwork.draft', 'latest', (cache_key, snapshot, measured, spans))
    if diagnostics is not None:
        diagnostics.update(copy.deepcopy(measured))
    complete('layout', 'freeze artwork')
    return snapshot


def required_sources(layers, terrain_mode='simple'):
    sources = {'road': 'roads', 'building': 'buildings', 'water': 'water',
               'terrain': 'forest', 'railway': 'railway', 'district': 'districts',
               'zone': 'zones', 'land_use': 'buildings', 'poi_area': 'buildings', 'procedural_objects': 'proceduralObjects',
               'satellite': 'satellite', 'built_area': 'builtArea', 'traffic': 'traffic'}
    required = list(dict.fromkeys(source for layer, source in sources.items() if layer in layers))
    if 'terrain' in layers and terrain_mode != 'simple':
        required.append('terrain')
    return required
