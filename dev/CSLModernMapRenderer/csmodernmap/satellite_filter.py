"""卫星底图成像效果；每次从无损合成图生成，不改变世界坐标。"""
from contextlib import ExitStack
import io
import math

import numpy as np
from PIL import Image
from PIL import ImageFilter


PRESETS = {
    "original": {},
    "haze": {"haze": .16, "haze_variation": .15},
    "grain": {"grain": 4.5, "correlated": 3.8},
    "resolution": {"blur": .95, "sample": .58},
    "distortion": {"channel_shift": .75, "motion": 1.3, "jpeg": 70},
    "combined": {"haze": .09, "haze_variation": .08, "blur": .65,
                 "sample": .72, "grain": 2.7, "correlated": 1.7,
                 "channel_shift": .4, "motion": .65, "jpeg": 88},
}
DEFAULTS = {"preset": "original", "intensity": 100}


def filter_key(style):
    data = getattr(style, "data", style) or {}
    config = data.get("satellite", {})
    if not isinstance(config, dict):
        raise ValueError("satellite: expected an object")
    preset = config.get("preset", DEFAULTS["preset"])
    intensity = config.get("intensity", DEFAULTS["intensity"])
    if not isinstance(preset, str) or preset not in PRESETS:
        raise ValueError("satellite.preset: unknown imaging preset")
    if (type(intensity) not in (int, float) or not math.isfinite(intensity)
            or not 0 <= intensity <= 100):
        raise ValueError("satellite.intensity: expected a number from 0 to 100")
    return ("original", 0) if preset == "original" or intensity == 0 else (preset, float(intensity))


def _shift(channel, dx, dy):
    # 补边后采样，避免色彩通道偏移在地图边缘产生黑线。
    pad = math.ceil(max(abs(dx), abs(dy))) + 2
    w, h = channel.size
    with Image.new("L", (w + 2*pad, h + 2*pad)) as padded:
        padded.paste(channel, (pad, pad))
        for box, target in (((0, 0, 1, h), (0, pad, pad, pad+h)),
                            ((w-1, 0, w, h), (pad+w, pad, 2*pad+w, pad+h))):
            with channel.crop(box) as edge:
                with edge.resize((pad, h)) as expanded:
                    padded.paste(expanded, target[:2])
        with padded.crop((0, pad, padded.width, pad+1)) as edge:
            with edge.resize((padded.width, pad)) as expanded:
                padded.paste(expanded, (0, 0))
        with padded.crop((0, pad+h-1, padded.width, pad+h)) as edge:
            with edge.resize((padded.width, pad)) as expanded:
                padded.paste(expanded, (0, pad+h))
        return padded.transform((w, h), Image.Transform.AFFINE,
                                (1, 0, pad-dx, 0, 1, pad-dy), Image.Resampling.BICUBIC)


def apply_filter(source, key, bounds, progress=None):
    """返回新的图像；参数像素以 17280 米 / 4096 为基准换算。"""
    preset, intensity = key
    if preset == "original":
        return source.copy()
    amount = intensity / 100
    spec = PRESETS[preset]
    scale = source.width / (bounds[2] - bounds[0]) * (17280 / 4096)
    report = progress or (lambda _pct, _message: None)
    from .i18n import tr
    message = tr("gui.satellite.filtering")
    with ExitStack() as stack:
        image = stack.enter_context(source.convert("RGB"))
        if spec.get("blur"):
            result = stack.enter_context(image.filter(ImageFilter.GaussianBlur(spec["blur"]*amount*scale)))
            image.close(); image = result
        if spec.get("sample"):
            ratio = 1 - (1-spec["sample"])*amount
            with image.resize(tuple(max(1, round(v*ratio)) for v in image.size), Image.Resampling.BOX) as small:
                result = stack.enter_context(small.resize(source.size, Image.Resampling.BILINEAR))
            image.close(); image = result
        report(25, message)
        if spec.get("motion") or spec.get("channel_shift"):
            channels = image.split()
            parts = []
            for index, channel in enumerate(channels):
                stack.enter_context(channel)
                current = channel
                if spec.get("motion"):
                    distance = spec["motion"]*amount*scale
                    with _shift(channel, distance, distance*.35) as a, _shift(channel, -distance, -distance*.35) as b:
                        with Image.blend(a, b, .5) as spread:
                            current = stack.enter_context(Image.blend(channel, spread, .4))
                if index != 1 and spec.get("channel_shift"):
                    distance = spec["channel_shift"]*amount*scale*(1 if index == 0 else -1)
                    shifted = stack.enter_context(_shift(current, distance, distance*.3))
                    if current is not channel:
                        current.close()
                    current = shifted
                parts.append(current)
            result = stack.enter_context(Image.merge("RGB", parts))
            for channel in channels + tuple(parts):
                channel.close()
            image.close(); image = result
        if spec.get("haze") or spec.get("grain"):
            rng = np.random.default_rng(674)
            field_rng = np.random.default_rng(6742)
            if spec.get("haze"):
                with Image.fromarray(np.uint8(np.clip(128+field_rng.normal(0,30,(9,9)),0,255))) as field:
                    haze = stack.enter_context(field.resize(source.size, Image.Resampling.BICUBIC))
            if spec.get("grain"):
                grid = field_rng.standard_normal((max(1, source.height//2), max(1, source.width//2)), dtype=np.float32)*24
                with Image.fromarray(np.uint8(np.clip(128+grid,0,255))) as field:
                    correlated = stack.enter_context(field.resize(source.size, Image.Resampling.BILINEAR))
                del grid
            for top in range(0, image.height, 128):
                box = (0, top, image.width, min(top+128, image.height))
                with image.crop(box) as stripe:
                    rgb = np.asarray(stripe, dtype=np.float32)
                    if spec.get("haze"):
                        with haze.crop(box) as fog:
                            field = np.clip((np.asarray(fog, dtype=np.float32)-80)/96,0,1)
                        opacity = amount*(spec["haze"]+spec["haze_variation"]*field)
                        rgb = rgb*(1-opacity[...,None])+np.array([202,211,216],dtype=np.float32)*opacity[...,None]
                    if spec.get("grain"):
                        lum = rgb @ np.array([.2126,.7152,.0722],dtype=np.float32)
                        noise = rng.normal(0,spec["grain"],rgb.shape[:2]).astype(np.float32)
                        with correlated.crop(box) as grain:
                            noise += (np.asarray(grain,dtype=np.float32)-128)/16*spec["correlated"]
                        rgb += noise[...,None]*amount*(.75+.55*(1-lum/255))[...,None]
                    with Image.fromarray(np.uint8(np.clip(np.rint(rgb),0,255))) as result:
                        image.paste(result,box[:2])
                report(30 + 50*box[3]/image.height, message)
        if spec.get("jpeg"):
            stream = io.BytesIO()
            image.save(stream, format="JPEG", quality=round(100-(100-spec["jpeg"])*amount), subsampling=2)
            with Image.open(io.BytesIO(stream.getvalue())) as compressed:
                result = stack.enter_context(compressed.convert("RGB"))
            image.close(); image = result
        if source.mode == "RGBA":
            with source.getchannel("A") as alpha:
                image.putalpha(alpha)
        report(95, message)
        return image.copy()


def satellite_image(document, style=None, progress=None):
    """会话只保留当前滤镜成品，原始合成图始终无损保留。"""
    from .satellite_image import ensure_satellite
    key = filter_key(style)
    cached = document._satellite_filtered
    if cached is not None and cached[0] == key:
        return cached[1]
    png = ensure_satellite(document)
    if key[0] == "original":
        document._satellite_filtered = None
        return png
    # 替换前释放旧成品；处理失败时归档原图与已显示的预览资源仍可用。
    document._satellite_filtered = None
    with Image.open(io.BytesIO(png)) as source:
        with apply_filter(source, key, document._satellite_info["world_bounds_xz"], progress) as image:
            stream = io.BytesIO()
            image.save(stream, format="PNG")
    result = stream.getvalue()
    document._satellite_filtered = (key, result)
    return result
