"""已保存主题的真实城市缩略图；缓存不参与主题持久化。"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import random

from PIL import Image


def cache_prefix(identity: str) -> str:
    return hashlib.sha256(identity.encode('utf-8')).hexdigest() + '-'


def cache_path(directory: Path, identity: str, data: dict) -> Path:
    digest = hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True,
                                      allow_nan=False).encode('utf-8')).hexdigest()
    return directory / 'theme-previews' / (cache_prefix(identity) + digest + '.png')


def preview_url(directory: Path, identity: str, data: dict) -> str | None:
    path = cache_path(directory, identity, data)
    return path.resolve().as_uri() if path.is_file() else None


def render(data, session, visibility, rotation, mapping):
    from .layers.registry import gui_layer_registry
    from .regions import closeup_region
    from .renderer import Renderer
    from .style import Style
    from .poi_profiles import default_poi_profile

    # 固定随机种子使同一城市的连续保存采用相同特写。裁为 2:1，避免拉伸地图。
    x0, z0, x1, z1 = closeup_region(session._legacy_view(), 'overall', rng=random.Random(0))
    center = (z0 + z1) / 2
    region = (x0, center - (z1 - z0) / 4, x1, center + (z1 - z0) / 4)
    style = Style(copy.deepcopy(data))
    style.data['_poi_mapping_override'] = mapping
    icons = style.data.setdefault('poi_icons', {})
    for cfg in (icons, icons.setdefault('building', {}), icons.setdefault('transit', {})):
        cfg['active_profile'] = {**default_poi_profile('normal'), **cfg.get('active_profile', {}),
                                 'view': 'normal', 'density_enabled': True, 'density_tier': 'extremely_sparse'}
    renderer = Renderer(style, width=1024, height=512, rotation=rotation)
    for key, cls, default, _title in gui_layer_registry():
        if visibility.get(key, default):
            renderer.add_layer(cls())
    image = renderer.render(session.document, region=region, session=session)
    return image.resize((304, 152), Image.Resampling.LANCZOS)
