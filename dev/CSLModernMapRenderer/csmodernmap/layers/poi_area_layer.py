"""POI 区域复用建筑轮廓和填色，不扩张区域或绘制边框。"""
from .building_layer import BuildingLayer


class POIAreaLayer(BuildingLayer):
    name = "poi_area"

    def __init__(self):
        super().__init__(fill_only=True)
