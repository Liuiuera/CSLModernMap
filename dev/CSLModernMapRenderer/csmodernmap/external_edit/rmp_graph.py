"""保持 RMP v77 原生格式的站线、水域和设施转换。"""
from __future__ import annotations

from shapely import union_all
from shapely.geometry import LineString, Polygon, shape
from shapely.ops import split
from shapely.errors import GEOSException

from .. import i18n

RMP_COMMIT = "401ddafeb869255d10a5b51e3fae1acb34322d57"
RMP_GRAPH_VERSION = 12


class RmpWaterError(ValueError):
    """合法水面无法通过原生闭合面适配。"""


def _adapt_water(water, warnings):
    if water.is_empty:
        return []
    if not water.is_valid or water.geom_type not in ("Polygon", "MultiPolygon"):
        raise ValueError("无法转换无效水域几何")
    parts = list(water.geoms) if water.geom_type == "MultiPolygon" else [water]
    faces = []
    for index, part in reversed(list(enumerate(parts))):
        try:
            candidate = _water_faces(part)
        except (RmpWaterError, GEOSException) as error:
            message = i18n.tr("gui.transit.rmp_water_omitted", component=index + 1, reason=str(error))
            if warnings is None:
                # 没有诊断接收方的内部调用不能静默丢弃水面。
                raise RmpWaterError(message) from error
            warnings.append(message)
        else:
            faces.extend(candidate)
    return faces


def build_editor_draft(scene, *, warnings=None):
    """WinUI 仅传递生成元数据，原生样式由 RMP 注册表初始化。"""
    project = {"version": 77, "svgViewBoxZoom": 100, "svgViewBoxMin": {"x": -20, "y": -20},
               "graph": {"options": {"type": "directed", "multi": True, "allowSelfLoops": True},
                         "attributes": {}, "nodes": [], "edges": []}}
    water = shape(scene["water"]) if scene.get("water") else Polygon()
    project["cmmDraft"] = {
        "version": 1,
        "stations": [{**{key: row[key] for key in ("id", "x", "y", "name", "transfer")},
                      "name_source": row.get("name_source", "unknown")}
                     for row in scene["stations"]],
        "lines": [{"source_line_id": row["source_line_id"], "name": row["source_name"], "chains": row["chains"],
                   "color": row["source_color"]}
                  for row in scene["lines"]],
        "water": [list(face.exterior.coords)[:-1] for face in _adapt_water(water, warnings)],
        "facilities": [{key: row[key] for key in ("kind", "x", "y", "name")}
                       for row in scene.get("facilities", [])],
    }
    return project


def _water_faces(water):
    """原生 Fill 只支持短单环；沿直线拆面，保持最终平滑岸线和岛洞。"""
    pending = list(water.geoms) if water.geom_type == "MultiPolygon" else [water]
    faces = []
    while pending:
        part = pending.pop()
        if part.is_empty:
            continue
        if part.geom_type != "Polygon":
            raise RmpWaterError("水域拆分产生非面状几何")
        part = part.simplify(0, preserve_topology=True)
        if not part.interiors and len(part.exterior.coords)-1 <= 80:
            faces.append(part)
            continue
        x0, y0, x1, y1 = part.bounds
        if part.interiors:
            y = Polygon(part.interiors[0]).representative_point().y
            cut = LineString([(x0-1, y), (x1+1, y)])
        elif x1-x0 >= y1-y0:
            x = (x0+x1)/2
            cut = LineString([(x, y0-1), (x, y1+1)])
        else:
            y = (y0+y1)/2
            cut = LineString([(x0-1, y), (x1+1, y)])
        pieces = list(split(part, cut).geoms)
        if len(pieces) < 2:
            raise RmpWaterError("无法将水域拆分为 RMP 原生闭合面")
        pending.extend(pieces)
    if union_all(faces).symmetric_difference(water).area > max(1e-5, water.area*1e-8):
        raise RmpWaterError("RMP 水域拆分未保持最终水面和岛洞")
    return faces
