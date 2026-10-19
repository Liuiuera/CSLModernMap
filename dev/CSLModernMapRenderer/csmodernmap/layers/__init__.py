"""图层渲染器包。

每个子功能独立一个模块：
- terrain_layer.py  地形分层设色 + 晕渲
- water_layer.py    水域
- forest_layer.py   森林/绿地
- road_layer.py     道路（双层描边 + 圆角）
- building_layer.py 建筑（描边 + 阴影）
- district_layer.py 区划边界 + 名称
- railway_layer.py  铁路
- label_layer.py    文字标注
"""
from .base import Layer, RenderContext  # noqa: F401
