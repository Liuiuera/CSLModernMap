"""公共交通站点的外径、描边与去留白换乘母版。"""
from functools import lru_cache

from PIL import Image

from ._paths import resource_dir


def station_circle_metrics(diameter: float) -> tuple[float, float]:
    return diameter / 2, min(1.5, diameter / 8)


@lru_cache(maxsize=1)
def interchange_master() -> Image.Image:
    with Image.open(resource_dir('resources')/'icons/rmp/chongqingrt-int.png') as source:
        image = source.convert('RGBA')
    bounds = image.getchannel('A').getbbox()
    image = image.crop(bounds)
    side = max(image.size)
    master = Image.new('RGBA', (side, side))
    master.paste(image, ((side-image.width)//2, (side-image.height)//2))
    return master
