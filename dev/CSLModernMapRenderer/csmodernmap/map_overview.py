"""生成独立于导出图层和标注的地理定位缩略图。"""
import numpy as np
from PIL import Image, ImageColor, ImageDraw


def build_map_overview(document, bounds, style=None, size=512):
    """着色前先采样地形，避免分配全分辨率栅格；方向与预览及 PNG 一致，定位图省略标注和 POI。"""
    style = style or {}
    x0, z0, x1, z1 = bounds
    density = size / max(x1 - x0, z1 - z0)
    width, height = max(1, round((z1-z0)*density)), max(1, round((x1-x0)*density))
    background = style.get("canvas", {}).get("background", "#e4ecd9")
    image = Image.new("RGB", (width, height), background)
    terrain = getattr(document, "terrain", None)

    def sample(name):
        grid = getattr(terrain, name, None)
        values = getattr(grid, "values", None)
        if values is None:
            return None
        values = np.asarray(values)
        if values.ndim != 2 or not values.size:
            return None
        cell = grid.cell or terrain.cell
        origin = grid.origin
        xs = x1 - (np.arange(height) + .5) / height * (x1-x0)
        zs = z1 - (np.arange(width) + .5) / width * (z1-z0)
        cols = np.clip(((xs-origin.x)/cell).astype(int), 0, values.shape[1]-1)
        rows = np.clip(((zs-origin.z)/cell).astype(int), 0, values.shape[0]-1)
        return np.nan_to_num(values[np.ix_(rows, cols)].T)

    elevations = sample("height")
    pixels = np.asarray(image).copy()
    if elevations is not None:
        stops = style.get("terrain", {}).get("elevation_colors", [])
        if stops:
            stops = sorted(stops, key=lambda stop: stop["threshold"])
            colors = np.array([ImageColor.getrgb(stop["color"])[:3] for stop in stops])
            levels = [stop["threshold"] for stop in stops]
            for c in range(3):
                pixels[:, :, c] = np.interp(elevations, levels, colors[:, c])
        # 即使当前导出隐藏地形，轻微起伏仍有助于定位。
        if min(elevations.shape) > 1:
            dx, dz = np.gradient(elevations, 1/density)
            shade = np.clip(1 + (dx-dz)*.45, .78, 1.12)
            pixels = np.clip(pixels * shade[:, :, None], 0, 255).astype(np.uint8)
    water = sample("water_depth")
    if water is not None:
        pixels[water > .1] = ImageColor.getrgb(style.get("water", {}).get("color", "#92cddd"))[:3]
    image = Image.fromarray(pixels)
    draw = ImageDraw.Draw(image)

    def point(p):
        return ((z1-p.z)*density, (x1-p.x)*density)

    for building in getattr(document, "buildings", ()):
        points = building.footprint.points
        if len(points) >= 3:
            draw.polygon([point(p) for p in points], fill="#b5b7b1")
    for network in getattr(document, "networks", ()):
        kind = getattr(network.kind, "value", network.kind)
        mode = getattr(network.mode, "value", network.mode)
        if kind not in {"ROAD", "RAIL", "HIGHWAY"} or mode == "TUNNEL":
            continue
        points = network.geometry.points
        if len(points) >= 2:
            draw.line([point(p) for p in points], fill="#ffffff" if kind == "ROAD" else "#a0a29e",
                      width=max(1, round(network.width*density)))
    return image
