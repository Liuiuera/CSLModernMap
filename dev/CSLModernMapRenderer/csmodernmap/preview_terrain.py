"""将正式地形图层适配为离线预览图像。"""
from copy import copy, deepcopy
from dataclasses import replace
from math import floor, ceil

from PIL import Image, ImageDraw

from .layers.base import RenderContext
from .layers.terrain_layer import TerrainLayer
from .ribbon_schema import normalize_terrain_mode
from .style import Style


def raster_session_for_bounds(session, bounds):
    """裁剪采样网格，保留一圈邻点供插值和水域轮廓使用。"""
    from .cmm.model import Vec2
    from .cmm.session import DocumentSession
    from .export_region import document_bounds

    terrain = session.document.terrain
    if bounds is None or tuple(bounds) == document_bounds(session.document):
        return session
    if terrain.cell <= 0 or min(terrain.rows, terrain.cols) < 2:
        return session

    def crop():
        x0, z0, x1, z1 = bounds
        col0 = max(0, min(terrain.cols - 2, floor((x0 - terrain.origin.x) / terrain.cell - .5) - 1))
        row0 = max(0, min(terrain.rows - 2, floor((z0 - terrain.origin.z) / terrain.cell - .5) - 1))
        col1 = min(terrain.cols, max(col0 + 2, ceil((x1 - terrain.origin.x) / terrain.cell - .5) + 2))
        row1 = min(terrain.rows, max(row0 + 2, ceil((z1 - terrain.origin.z) / terrain.cell - .5) + 2))
        origin = Vec2(terrain.origin.x + col0 * terrain.cell,
                      terrain.origin.z + row0 * terrain.cell)

        def grid(value):
            return replace(value, rows=row1-row0, cols=col1-col0, origin=origin,
                           _values=None if value.values is None else value.values[row0:row1, col0:col1])
        document = copy(session.document)
        document.terrain = replace(terrain, rows=row1-row0, cols=col1-col0,
                                   origin=origin, height=grid(terrain.height),
                                   water_depth=grid(terrain.water_depth))
        return DocumentSession(document)

    session.service_cache.keep_only("preview.raster_session", tuple(bounds))
    return session.service_cache.get_or_compute("preview.raster_session", tuple(bounds), crop)


class PreviewTerrainCompiler:
    @staticmethod
    def compile(session, style, state, root, bounds=None):
        from .preview_scene import world_to_geo

        terrain = session.document.terrain
        if terrain.height.values is None:
            return None
        data = deepcopy(getattr(style, "data", style))
        cfg = data.setdefault("terrain", {})
        # 与生产渲染同一套取值：认不出来的（含已下线的 facets / gradient）一律归一。
        mode = normalize_terrain_mode(state.get("mode"))
        cfg["ground"] = {"enabled": False}
        # 与 PNG 共用等高区间；只插值高度，不混合色带颜色。
        cfg["elevation_mode"] = "stepped"
        shade = cfg.setdefault("hillshade", {})
        # 「立体棱角」已删除，面片参数不再有含义；旧主题覆写里可能还留着，清掉。
        for retired in ("mode", "facet_size_meters", "facet_jitter"):
            shade.pop(retired, None)
        shade.update(enabled=mode == "shade",
                     intensity=max(0., min(1., float(state.get("shade", 35)) / 100.)),
                     azimuth=float(state.get("sun", 315)))
        # 严格沿用正式渲染的坐标变换和采样中心边界。
        x0 = float(terrain.origin.x) + terrain.cell * .5
        z0 = float(terrain.origin.z) + terrain.cell * .5
        x1 = x0 + terrain.cell * (terrain.cols - 1)
        z1 = z0 + terrain.cell * (terrain.rows - 1)
        if bounds is not None:
            x0, z0 = max(x0, bounds[0]), max(z0, bounds[1])
            x1, z1 = min(x1, bounds[2]), min(z1, bounds[3])
        if x1 <= x0 or z1 <= z0:
            raise ValueError("Invalid terrain extent")
        density = 2047 / max(x1 - x0, z1 - z0)
        width = max(2, round((z1-z0)*density) + 1)
        height = max(2, round((x1-x0)*density) + 1)
        image = Image.new("RGB", (width, height), data.get("canvas", {}).get("background", "#F6F7F9"))
        transform = lambda x, z: ((z1-z)/(z1-z0)*(width-1), (x1-x)/(x1-x0)*(height-1))
        ctx = RenderContext(image, ImageDraw.Draw(image), transform, Style(data),
                            world_x0=x0, world_z0=z0, world_x1=x1, world_z1=z1,
                            session=session)
        TerrainLayer().render(ctx, session.document)
        image.save(root / "terrain.png")
        return [world_to_geo(x1, z1), world_to_geo(x1, z0),
                world_to_geo(x0, z0), world_to_geo(x0, z1)]
