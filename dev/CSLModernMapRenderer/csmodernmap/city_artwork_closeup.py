"""活动城市草稿的只读样式特写；没有对应要素时不提供替代取景。"""
from __future__ import annotations

import math
import random

import numpy as np

from .cmm import normalize_transit_kind
from .layers.railway_layer import _KIND_BY_NETWORK
from .layers.transport_layer import _clip_segment_to_box
from .regions import _closeup_window, _feature_grid, _weighted_index
from .road_hierarchy import RoadLevel


_SECTION_LAYERS = {
    'terrain': {'terrain'}, 'water': {'water'}, 'roads': {'road'},
    'buildings': {'building'}, 'railway': {'railway'},
    'transports': {'transit', 'metro'}, 'zone': {'zone'},
    'district': {'district'}, 'poi_icons': {'poi'},
    'forest': {'terrain'}, 'procedural_objects': {'procedural_objects'},
    'built_area': {'built_area'}, 'land_use': {'land_use'}, 'traffic': {'traffic'},
}


def _point_region(points, bounds, rng):
    x0, z0, x1, z1 = bounds
    points = [(x, z) for x, z in points if x0 <= x <= x1 and z0 <= z <= z1]
    if not points:
        return None
    return _closeup_window(points[_weighted_index([1] * len(points), rng)], bounds, rng)


def _line_region(lines, bounds, rng):
    # 仅以画布内的二维长度为权重，跨越画布但端点均在外的线段也参与。
    selected, total = None, 0.0
    for points in lines:
        for a, b in zip(points, points[1:]):
            clipped = _clip_segment_to_box(a, b, bounds)
            if clipped is None:
                continue
            start, end = clipped
            length = math.hypot(b.x - a.x, b.z - a.z) * (end - start)
            if length > 0:
                total += length
                # 加权蓄水池只持有一个候选，不复制大城市的全部折线边。
                if rng.random() * total < length:
                    selected = a, b, start, end
    if selected is None:
        return None
    a, b, start, end = selected
    t = rng.uniform(start, end)
    return _closeup_window((a.x + (b.x - a.x) * t, a.z + (b.z - a.z) * t), bounds, rng)


def city_artwork_closeup(session, job, section, *, road_level=None, label_type=None, rng=None):
    """返回世界坐标范围；不修改草稿、样式、图层或启动渲染。"""
    rng = rng or random.Random()
    bounds = job['region']
    layers = set(job['layers'])
    ui = job['ui']
    document = session.document
    if section == 'labels':
        # 旧 labels.building/park/stop 已由 POI 样式接管，不能声称可预览这些字段。
        if label_type not in {'road', 'district'} or 'label' not in layers:
            return None
        if not ui.get(label_type + '_names', True):
            return None
        if label_type == 'road':
            return _line_region((road.points for road in session.road_scene().roads
                if ((ui.get('road_player_names', True) and road.segment.display_name)
                    or (ui.get('road_asset_names', False) and road.segment.game_name))), bounds, rng)
        from .labeling.collectors import collect_district_names
        points = ((label.position.x, label.position.z)
                  for label in collect_district_names(session._legacy_view()))
        return _point_region(points, bounds, rng)
    if not layers.intersection(_SECTION_LAYERS.get(section, ())):
        return None
    if section == 'roads':
        if road_level is not None and road_level not in {level.value for level in RoadLevel}:
            return None
        return _line_region((road.points for road in session.road_scene().roads
                             if road_level is None or road.level == RoadLevel(road_level)), bounds, rng)
    if section == 'railway':
        return _line_region((network.geometry.points for network in document.networks
                             if network.kind in _KIND_BY_NETWORK), bounds, rng)
    if section == 'traffic':
        snapshot = session.traffic_snapshot()
        if snapshot is None:
            return None
        return _line_region((segment.world_points() for segment in snapshot.segments), bounds, rng)
    if section in {'terrain', 'water'}:
        grid = 24
        features = _feature_grid(session._legacy_view(), section, grid, *bounds)
        cells = np.argwhere(features > 0)
        if not len(cells):
            return None
        gz, gx = cells[_weighted_index(features[cells[:, 0], cells[:, 1]], rng)]
        x0, z0, x1, z1 = bounds
        cell_x, cell_z = (x1 - x0) / grid, (z1 - z0) / grid
        cx, cz = x0 + (gx + .5) * cell_x, z0 + (gz + .5) * cell_z
        region = _closeup_window((cx, cz), bounds, rng)
        # 地形统计格可能比狭长画布的短边大，窗口必须包含被选中的整个格。
        return (max(x0, min(region[0], cx - cell_x / 2)),
                max(z0, min(region[1], cz - cell_z / 2)),
                min(x1, max(region[2], cx + cell_x / 2)),
                min(z1, max(region[3], cz + cell_z / 2)))
    if section in {'buildings', 'built_area', 'land_use'}:
        buildings = document.buildings
        if section == 'land_use':
            from .layers.land_use_layer import LandUseLayer
            buildings = (b for group in LandUseLayer()._categorize(document, 'base').values()
                         for b in group)
        points = ((sum(p.x for p in b.footprint.points) / len(b.footprint.points),
                   sum(p.z for p in b.footprint.points) / len(b.footprint.points))
                  for b in buildings if b.footprint.points)
    elif section == 'transports':
        enabled = {normalize_transit_kind(kind) for kind in ui['transit_types']}
        line_ids = ui.get('transit_line_ids')
        lines = {line.id for line in document.transit_lines if line.kind in enabled
                 and (line_ids is None or line.id in line_ids)}
        stops = {stop_id for route in document.routes
                 if route.line_id in lines for stop_id in route.stop_ids}
        points = ((stop.position.x, stop.position.z) for stop in document.stops
                  if stop.id in stops)
    elif section in {'district', 'zone'}:
        districts = [d for d in document.districts if not d.is_background
                     and (d.kind == 'PARK_ZONE') == (section == 'zone')]
        points = ((p.x, p.z) for d in districts for p in d.grid_points)
    elif section == 'forest':
        points = ((forest.data[i], forest.data[i + 1]) for forest in document.forests
                  for i in range(0, len(forest.data) - 1, 2))
    elif section == 'procedural_objects':
        points = ((obj.pos.x, obj.pos.z) for obj in document.procedural_objects)
    else:  # poi_icons
        from .services.poi.model import POIJobParams
        from .services.poi.visibility import visible_pois
        from .gui_render_worker import _load_ui_style
        style = _load_ui_style(ui, job['style']).data
        cfg = style.get('poi_icons', {})
        transports = {**style.get('transports', {}), 'enabled': ui.get('transit_types', ()),
                      'enabled_line_ids': ui.get('transit_line_ids')}
        catalog = session.poi_catalog(POIJobParams.from_style(
            {'poi_icons': cfg, 'transports': transports}),
            override_path=ui.get('poi_mapping_override') or None)
        points = ((poi.anchor.x, poi.anchor.z) for poi in visible_pois(
            catalog, cfg, visibility_snapshot=ui.get('poi_visibility_snapshot')) if poi.icon_key)
    return _point_region(points, bounds, rng)
