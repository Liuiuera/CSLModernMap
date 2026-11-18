"""将归档卫星影像按世界坐标绘制到现有栅格上下文。"""
import io
from contextlib import ExitStack
import numpy as np
from PIL import Image
from .i18n import tr


INTERFERING_LAYERS = ("terrain", "water", "building", "built_area", "land_use",
                      "poi_area", "procedural_objects", "road", "railway", "traffic")


def ensure_satellite(document, progress=None):
    """只缓存成品，不改变归档原始影像或元数据。"""
    if document._satellite_png is not None:
        return document._satellite_png
    if document._satellite_composite is not None:
        return document._satellite_composite
    if not document._satellite_layers:
        raise ValueError(tr("gui.satellite.unavailable"))
    from .satellite_export import bridge_coverage
    report = progress or (lambda percent, message: None)
    report(5, tr("gui.satellite.composing"))
    with ExitStack() as stack:
        with Image.open(io.BytesIO(document._satellite_layers["land"])) as source:
            land = stack.enter_context(source.convert("RGB"))
        with Image.open(io.BytesIO(document._satellite_layers["water"])) as source:
            water = stack.enter_context(source.convert("RGBA"))
        report(25, tr("gui.satellite.composing"))
        bridges, _ = bridge_coverage(document.networks, land.width,
                                     document._satellite_info["world_bounds_xz"])
        stack.enter_context(bridges)
        for top in range(0, land.height, 128):
            box = (0, top, land.width, min(top + 128, land.height))
            with water.crop(box) as stripe, bridges.crop(box) as bridge:
                with stripe.getchannel("A") as alpha:
                    coverage = np.array(alpha, dtype=np.float32)
                # 实测非水域 alpha 0–4 为采集残留，不应污染陆地像素。
                coverage[coverage <= 4] = 0
                coverage *= 1 - np.asarray(bridge, dtype=np.float32) / 255
                with Image.fromarray(np.uint8(np.rint(coverage))) as mask:
                    land.paste(stripe, box[:2], mask)
            report(30 + 55 * box[3] / land.height, tr("gui.satellite.composing"))
        target = document._satellite_info["output_size"]
        if target != land.width:
            resized = stack.enter_context(land.resize((target, target), Image.Resampling.LANCZOS))
            land.close()
            land = resized
        report(90, tr("gui.satellite.composing"))
        stream = io.BytesIO()
        land.save(stream, format="PNG")
        document._satellite_composite = stream.getvalue()
    report(100, tr("gui.satellite.ready"))
    return document._satellite_composite


def compile_satellite(document, root, style=None, progress=None):
    """影像 top=+Z/right=+X，与地形使用同一地理坐标映射。"""
    from .preview_scene import world_to_geo
    from .satellite_filter import satellite_image
    xmin, zmin, xmax, zmax = document._satellite_info["world_bounds_xz"]
    (root / "satellite.png").unlink(missing_ok=True)
    with Image.open(io.BytesIO(satellite_image(document, style, progress))) as source:
        with source.transpose(Image.Transpose.ROTATE_90) as image:
            image.save(root / "satellite.png")
    return [world_to_geo(xmax, zmax), world_to_geo(xmax, zmin),
            world_to_geo(xmin, zmin), world_to_geo(xmin, zmax)]


def render_satellite(ctx, document):
    if document._satellite_info is None:
        return False
    from .satellite_filter import satellite_image
    png = satellite_image(document, getattr(ctx, "style", None))
    xmin, zmin, xmax, zmax = document._satellite_info["world_bounds_xz"]
    with Image.open(io.BytesIO(png)) as source:
        width, height = source.size
        corners = [ctx.transform(xmin, zmax), ctx.transform(xmax, zmax), ctx.transform(xmin, zmin)]
        forward = np.array([[ (corners[1][0]-corners[0][0])/width, (corners[2][0]-corners[0][0])/height, corners[0][0]],
                            [ (corners[1][1]-corners[0][1])/width, (corners[2][1]-corners[0][1])/height, corners[0][1]], [0,0,1]])
        inverse = np.linalg.inv(forward)[:2].reshape(-1)
        image = source.convert("RGBA").transform(ctx.image.size, Image.Transform.AFFINE, inverse,
                                                Image.Resampling.BICUBIC, fillcolor=(0,0,0,0))
        ctx.image.paste(image, (0,0), image)
        image.close()
    return True
