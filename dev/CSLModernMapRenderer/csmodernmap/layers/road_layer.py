"""以与预览一致的边界和顺序绘制 PNG 道路。

每个等级先绘制外沿再绘制填充，后续等级覆盖其整体；栅格绘制继续使用 PNG 的样式宽度、细节规则和输出变换。"""
from __future__ import annotations

from ..style import hex_to_rgb

from collections import defaultdict
import math

import aggdraw

from ..preview_road_order import build_road_order
from ..road_geometry import RoadRecord, build_junction_mesh, junction_primary, road_sort_key
from ..road_hierarchy import RoadLevel, road_style_candidates, visible_at_abstract_level
from ..theme_contract import ROAD_MIN_FILL, ROAD_WIDTH_SCALE
from .base import Layer, RenderContext


_LEVEL_MIN_PIX = ROAD_MIN_FILL
_SCENE_KEY = "road_scene"
_ORDER_KEY = "road_order"


class RoadLayer(Layer):
    name = "road"

    def prepare(self, session, job) -> None:
        style = session._prepare_style
        session.road_order(accurate=bool(style.get("roads.accurate_intersections", True)))

    def render(self, ctx: RenderContext, document) -> None:
        """按局部高度等级绘制道路表面与边界。"""
        scene = ctx.shared.get(_SCENE_KEY)
        if scene is None and ctx.session is not None:
            scene = ctx.session.road_scene()
            ctx.shared[_SCENE_KEY] = scene
        if scene is None or not scene.roads:
            return
        order = ctx.shared.get(_ORDER_KEY)
        if order is None:
            accurate = bool(ctx.style.get("roads.accurate_intersections", True))
            order = (ctx.session.road_order(accurate=accurate) if ctx.session is not None
                     else build_road_order(scene, accurate=accurate))
            ctx.shared[_ORDER_KEY] = order

        self.ctx, self.scene = ctx, scene
        self.render_scale = ctx.zoom
        self._lvl = ctx.detail_policy.abstract_level
        self._aa_k = ctx.zoom / max(ctx.final_zoom, 1e-9)
        self.wscale = float(ctx.style.get("roads.width_scale", ROAD_WIDTH_SCALE))
        self.roads_cfg = ctx.style.get("roads", {})
        self._has_highways = any(r.level is RoadLevel.HIGHWAY for r in scene.roads)
        bbox = (ctx.world_x0, ctx.world_z0, ctx.world_x1, ctx.world_z1)
        records = sorted((r for r in scene.roads_in(bbox) if self._visible(r)),
                         key=lambda r: road_sort_key(r, False))
        by_rank = defaultdict(list)
        for record in records:
            for piece in order.pieces[record.id]:
                by_rank[self._rank(piece)].append(piece)
        connectors = self._junctions(order, records, bbox)
        draw = aggdraw.Draw(ctx.image)
        for rank, pieces in sorted(by_rank.items()):
            # 人为多边形切分不能生成道路外沿，未切分道路仍使用权威占地边界。
            for piece in pieces:
                if self._has_footprint(piece.road):
                    for region in piece.paint_regions:
                        cfg = self._cfg_record(region.road)
                        color = hex_to_rgb(cfg.get("border", "#C3C8D2"))
                        pen = aggdraw.Pen(color, max(self._border_width(cfg), .5))
                        for line in region.boundaries:
                            coords = tuple(v for x, z in line for v in ctx.transform(x, z))
                            draw.line(coords, pen)
                else:
                    cfg = self._cfg_record(piece.road)
                    color = hex_to_rgb(cfg.get("border", "#C3C8D2"))
                    self._draw_piece_line(draw, piece, cfg, color, casing=True)
            for mesh, border, _ in connectors.get(rank, ()):
                self._draw_fast_polygons(draw, mesh.outer, border)
            for piece in pieces:
                if self._has_footprint(piece.road):
                    for region in piece.paint_regions:
                        cfg = self._cfg_record(region.road)
                        self._draw_fast_polygons(draw, region.rings,
                            hex_to_rgb(cfg.get("fill", "#FFFFFF")))
                else:
                    cfg = self._cfg_record(piece.road)
                    color = hex_to_rgb(cfg.get("fill", "#FFFFFF"))
                    self._draw_piece_line(draw, piece, cfg, color, casing=False)
            for mesh, _, fill in connectors.get(rank, ()):
                self._draw_fast_polygons(draw, mesh.inner, fill)
        draw.flush()

    def _rank(self, piece):
        if self.scene.height_reliable:
            return piece.rank
        # 旧版零高度存档沿用语义回退；真实高度总按共享局部约束排序。
        return {"tunnel": 0, "ground": 1, "elevated": 2}.get(piece.road.mode, 1)

    @staticmethod
    def _has_footprint(record):
        return record.footprint is not None and len(record.footprint.points) >= 3

    def _draw_piece_line(self, draw, piece, cfg, color, *, casing):
        points = [self.ctx.transform(p.x, p.z) for p in piece.points]
        if len(points) < 2:
            return
        width = max(self._fill_width(piece.road) + (self._border_width(cfg) if casing else 0), .5)
        coords = tuple(v for point in points for v in point)
        if casing and cfg.get("dash"):
            self._draw_fast_dashed(draw, coords, color, width, cfg["dash"])
            return
        draw.line(coords, aggdraw.Pen(color, width))
        # 仅对真实端点和转角使用圆角；人为切口加圆帽会伸入其他等级片段并制造假接缝。
        round_points = points[1:-1]
        if piece.start <= 1e-6:
            round_points.append(points[0])
        if piece.end >= piece.road.length-1e-6:
            round_points.append(points[-1])
        self._draw_fast_circles(draw, round_points, width*.5, color)

    def _junctions(self, order, records, bbox):
        """保持仅有中心线的道路路口在 PNG 中的宽度过渡。"""
        fallback_ids = {r.id for r in records if not self._has_footprint(r)}
        result = defaultdict(list)
        scale = max(float(self.ctx.transform.scale), 1e-9)
        for junction in self.scene.junctions_in(bbox):
            groups = defaultdict(list)
            for sid in junction.segment_ids:
                if sid in fallback_ids:
                    road = self.scene.roads_by_id[sid]
                    pieces = order.pieces[sid]
                    endpoint = pieces[0] if road.start_node_id == junction.node_id else pieces[-1]
                    groups[self._rank(endpoint)].append(sid)
            for rank, ids in groups.items():
                if len(ids) < 2:
                    continue
                active = type(junction)(junction.node_id, tuple(ids), junction.x,
                                        junction.z, junction.mean_y, junction.bbox)
                primary = junction_primary(self.scene, active)
                cfg = self._cfg_record(primary)
                mesh = build_junction_mesh(
                    self.scene, active, lambda r: self._fill_width(r)/scale,
                    lambda r: self._border_width(self._cfg_record(r))/scale)
                result[rank].append((mesh, hex_to_rgb(cfg.get("border", "#C3C8D2")),
                                    hex_to_rgb(cfg.get("fill", "#FFFFFF"))))
        return result

    def _draw_fast_polygons(self, draw, polygons, color) -> None:
        brush = aggdraw.Brush(color)
        for polygon in polygons:
            if len(polygon) < 3:
                continue
            coords = tuple(
                value
                for x, z in polygon
                for value in self.ctx.transform(x, z)
            )
            draw.polygon(coords, None, brush)

    @staticmethod
    def _draw_fast_circles(draw, points, radius, color) -> None:
        if radius <= 0:
            return
        brush = aggdraw.Brush(color)
        for x, y in points:
            draw.ellipse(
                (x - radius, y - radius, x + radius, y + radius),
                None, brush,
            )

    def _draw_fast_dashed(self, draw, coords, color, width, dash) -> None:
        dash_len = max(float(dash[0]) * self.render_scale, 0.5)
        gap_len = max(float(dash[1]) * self.render_scale, 0.5)
        pen = aggdraw.Pen(color, width)
        for index in range(0, len(coords) - 3, 2):
            ax, ay, bx, by = coords[index:index + 4]
            dx, dy = bx - ax, by - ay
            length = math.hypot(dx, dy)
            if length <= 1e-7:
                continue
            cursor = 0.0
            on = True
            while cursor < length:
                step = dash_len if on else gap_len
                end = min(cursor + step, length)
                if on:
                    t0, t1 = cursor / length, end / length
                    draw.line((
                        ax + dx * t0, ay + dy * t0,
                        ax + dx * t1, ay + dy * t1,
                    ), pen)
                cursor = end
                on = not on

    def _visible(self, record: RoadRecord) -> bool:
        return visible_at_abstract_level(
            record.level, self._lvl, has_highways=self._has_highways,
        )

    def _cfg_record(self, record: RoadRecord) -> dict:
        for key in road_style_candidates(record.level, record.mode):
            cfg = self.roads_cfg.get(key, {})
            if cfg:
                return cfg
        return {}

    def _fill_width(self, record: RoadRecord) -> float:
        cfg = self._cfg_record(record)
        minimum = float(cfg.get(
            "min_fill_px", _LEVEL_MIN_PIX[record.level]
        )) * self._aa_k
        physical = 0.5 + record.width * self.wscale * self.render_scale
        return max(physical, minimum)

    def _border_width(self, cfg: dict) -> float:
        bw = cfg.get("border_width", 1.0)
        if isinstance(bw, dict):
            return bw.get("fixed", 0.0) + bw.get("scaled", 0.0) * self.render_scale
        return float(bw)
