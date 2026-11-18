"""按地图坐标显示会话内合成的卫星底图。"""
from .base import Layer
from ..satellite_image import ensure_satellite, render_satellite


class SatelliteLayer(Layer):
    name = "satellite"
    full_canvas = True

    def supports(self, document):
        return document._satellite_info is not None

    def prepare(self, session, job):
        ensure_satellite(session.document)

    def render(self, ctx, document):
        ensure_satellite(document)
        render_satellite(ctx, document)
