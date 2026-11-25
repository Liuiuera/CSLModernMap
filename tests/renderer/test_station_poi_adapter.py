"""Merged traffic stations retain their identity and artwork in the POI path."""
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from csmodernmap.layers.base import RenderContext
from csmodernmap.layers.transport_layer import TransportLayer
from csmodernmap.layers.poi_icon_layer import POIIconLayer, POIItem
from csmodernmap.style import Style
from test_transit_model_t02 import _fixture


def context(stations=True, names=False):
    image = Image.new("RGBA", (640, 400), "white")
    from csmodernmap._paths import resource_dir
    from csmodernmap.style import load_style
    style=load_style(str(resource_dir('styles')/'modern.yaml'))
    style.data['transports'].update(enabled=['Metro','Bus'], label={'enabled':False})
    style.data['transports']['station'].update(enabled=stations, diameter=12, names={'enabled':names,'size':11,'gap':5,'stroke_width':1})
    return RenderContext(image, ImageDraw.Draw(image), lambda x,z:(x+50,z+120), style)



def test_png_bus_and_metro_stops_use_the_same_circle():
    data = _fixture()
    for node in data.nodes[4:6]:
        node.pos.z = -60
    for line in data.transports[:3]:
        line.color = (255, 32, 64, 128)
    ctx = context()
    ctx.style.data['transports'].update(auto_color=False, lines_visible=False)
    TransportLayer(transit_view=True).render(ctx, data)

    def marker(line_ids):
        item = next(item for item in ctx.shared['station_poi_items']
                    if set(item.serving_line_ids) == line_ids)
        x, y = round(item.px), round(item.py)
        return ctx.image.crop((x - 6, y - 6, x + 6, y + 6))

    bus, metro = marker({3}), marker({1, 2})
    assert bus.tobytes() == metro.tobytes()
    assert min(metro.getpixel((0, 0))) >= 250
    assert metro.getpixel((6, 6)) == pytest.approx((32, 64, 128, 255), abs=5)


@pytest.mark.parametrize("transit_view", [False, True])
def test_station_identity_and_ordinary_map_pixels(transit_view):
    ctx = context()
    layer = TransportLayer(transit_view=transit_view)
    layer.render(ctx, _fixture())
    items = ctx.shared["station_poi_items"]
    assert [(p.poi_id, p.label, p.world_x, p.world_z, p.serving_line_ids) for p in items] == [
        (f"station:{s.id}", s.name.text, s.position.x, s.position.z, tuple(s.serving_line_ids))
        for s in layer._transit_model.stations]


def test_disabling_circles_also_disables_their_names(monkeypatch):
    labels = []
    monkeypatch.setattr(POIIconLayer, "_draw_icon_name", lambda self, *args: labels.append(args))
    TransportLayer(transit_view=True).render(context(False, True), _fixture())
    assert not labels
    TransportLayer(transit_view=True).render(context(True, True), _fixture())
    assert labels
    assert all(args[0].symbol is not None for args in labels)


def test_station_symbols_do_not_enter_density_or_icon_collision(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("station symbol entered POI density filtering")
    monkeypatch.setattr(POIIconLayer, "_visual_cluster", forbidden)
    ctx = context()
    TransportLayer(transit_view=True).render(ctx, _fixture())
    assert len(ctx.shared["station_poi_items"]) > 1


def test_facility_offset_uses_station_bounds_and_final_icon_position():
    class Icons:
        def render_icon(self, *args, **kwargs):
            return Image.new("RGBA", (20, 20), "red")
        def get_icon(self, key):
            return {"borderless": True}
    layer = POIIconLayer(icon_map=Icons())
    ctx = context()
    layer.render_station_items(ctx, [])
    layer._poi_cfg = {"show_name": True, "declutter": False}
    item = POIItem(100, 100, "icon", "Name", "metro", 100, "transport",
                   station_box=(90, 90, 110, 110))
    layer._draw_icon(item, 20, False, 0)
    assert ctx.image.getpixel((113, 67)) == (255, 0, 0, 255)
    assert ctx.image.getpixel((100, 100)) == (255, 255, 255, 255)
    assert layer._pending_names[0][2:6] == (111, 65, 24, 24)
