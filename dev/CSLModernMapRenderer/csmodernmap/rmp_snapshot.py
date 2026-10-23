"""提取最终站位、真实站序及样式，供 RMP 原生路径布局。"""
from __future__ import annotations

from shapely.geometry import mapping
from PIL import ImageColor
from . import transit


def capture_layer(layer, water, water_cfg, station_cfg):
    layout = layer._schematic
    stations = []
    names = layer._metro_station_names
    for station in layer._merged or ():
        if station.id not in layout.node_grid:
            continue
        x, y = layer._schematic_to_px(*layout.node_grid[station.id])
        name = names.get(station.id, getattr(station.name, "text", ""))
        stations.append({
            "id": str(station.id), "x": x, "y": y, "name": name,
            "visible": bool(station_cfg.get("enabled", False)),
            "transfer": layer._is_transfer_station(station),
            "label": layer._rmp_labels.get(str(station.id)),
        })
    lines = []
    for line in layer._tr_by_id.values():
        if id(line) in layout.route_alias:
            continue
        chains = layout.source_chains.get(int(line.id))
        if not chains:
            continue
        td = transit.type_def(line.type)
        width = layer._resolve_width(td)
        lines.append({
            "source_line_id": int(line.id),
            "source_name": layer._line_by_id[int(line.id)].name,
            "source_color": layer._line_by_id[int(line.id)].user_color,
            "chains": [[str(station_id) for station_id in chain] for chain in chains],
            "color": layer._resolve_color(line, td),
            "width": width,
            "casing_color": layer._halo_color,
            "casing_width": width * layer._halo_ratio,
        })
    return {
        "width": layer.ctx.image.width, "height": layer.ctx.image.height,
        "background": list(ImageColor.getrgb(layer.ctx.style.get("canvas.background", "#FAFAF8"))),
        "stations": stations, "lines": lines,
        "facilities": layer._rmp_facilities,
        "water": mapping(water) if water is not None and water_cfg.get("enabled", True) else None,
        "water_color": list(water_cfg.get("color_rgb", (116, 190, 222))),
        "water_alpha": max(72, min(int(water_cfg.get("alpha", 84)), 255)),
    }
