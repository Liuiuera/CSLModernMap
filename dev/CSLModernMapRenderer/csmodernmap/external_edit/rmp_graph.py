"""保持 RMP v77 原生格式的站线、水域和设施转换。"""
from __future__ import annotations

import math

from shapely import union_all
from shapely.geometry import LineString, Polygon, shape
from shapely.ops import split

RMP_COMMIT = "401ddafeb869255d10a5b51e3fae1acb34322d57"
RMP_GRAPH_VERSION = 6
FACILITY_TYPES = {"airport": "airport", "rail": "railway", "port": "cruise_centre"}


def build_editor_draft(scene):
    """WinUI 仅传递生成元数据，原生样式由 RMP 注册表初始化。"""
    project = build_graph({"stations": [], "lines": []})
    water = shape(scene["water"]) if scene.get("water") else Polygon()
    if not water.is_empty and not water.is_valid:
        raise ValueError("无法转换无效水域几何")
    project["cmmDraft"] = {
        "version": 1,
        "stations": [{key: row[key] for key in ("id", "x", "y", "name", "transfer")}
                     for row in scene["stations"]],
        "lines": [{"source_line_id": row["source_line_id"], "name": row["source_name"], "chains": row["chains"],
                   "color": row["source_color"]}
                  for row in scene["lines"]],
        "water": [list(face.exterior.coords)[:-1] for face in _water_faces(water)]
                 if not water.is_empty else [],
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
            raise ValueError("水域拆分产生非面状几何")
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
            raise ValueError("无法将水域拆分为 RMP 原生闭合面")
        pending.extend(pieces)
    if union_all(faces).symmetric_difference(water).area > max(1e-5, water.area*1e-8):
        raise ValueError("RMP 水域拆分未保持最终水面和岛洞")
    return faces


def build_graph(scene):
    """从站点、线路与设施构建 RMP 原生图结构。"""
    nodes, edges = [], []
    parallel_counts = {}

    def color(value):
        if isinstance(value, (tuple, list)):
            return "#" + "".join(f"{int(v):02X}" for v in value[-3:])
        return str(value)

    def theme(value):
        return ["other", "other", color(value), "#FFFFFF"]

    def node(key, point, kind="virtual", attrs=None, *, z=0, visible=True):
        x, y = map(float, point)
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError(f"RMP 节点坐标不是有限数值：{key}")
        nodes.append({"key": key, "attributes": {
            "x": x, "y": y, "type": kind, kind: attrs or {},
            "visible": visible, "zIndex": z,
        }})
        return key

    def edge(key, left, right, style, attrs, *, z=0, visible=True, reconcile="",
             path_type="simple", path_attrs=None, parallel_index=-1):
        edges.append({"key": key, "source": left, "target": right, "attributes": {
            "visible": visible, "zIndex": z, "type": path_type, path_type: path_attrs or {"offset": 0},
            "style": style, style: attrs, "reconcileId": reconcile, "parallelIndex": parallel_index,
        }})

    for row in scene["stations"]:
        kind = "shmetro-int" if row["transfer"] else "shmetro-basic"
        names = str(row["name"]).split("\n", 1)
        attrs = {"names": [names[0], names[1] if len(names) > 1 else ""],
                 "nameOffsetX": "right", "nameOffsetY": "top"}
        if row["label"] is not None:
            label = row["label"]
            attrs["preciseNameOffsets"] = {"x": label["x"]-row["x"],
                "y": label["y"]-row["y"]+6.67, "anchor": "start"}
        else:
            attrs["names"] = ["", ""]
        if row["transfer"]:
            attrs.update(rotate=0, height=10, width=15)
        node(f"stn_cmm_{row['id']}", (row["x"], row["y"]), kind, attrs,
             z=5, visible=row["visible"])

    station_keys = {item["key"] for item in nodes}
    for number, line in enumerate(scene["lines"]):
        attrs = {"layers": [{"id": f"cmm_{number}", "color": theme(line["color"]),
            "width": line["width"], "opacity": 1, "linecap": "round", "dash": 0, "gap": 0}]}
        if line.get("casing_width") is not None:
            attrs["layers"].insert(0, {"id": f"casing_{number}", "color": theme(line["casing_color"]),
                "width": line["casing_width"], "opacity": 1, "linecap": "round", "dash": 0, "gap": 0})
        seen = set()
        for chain_number, chain in enumerate(line["chains"]):
            keys = [f"stn_cmm_{station_id}" for station_id in chain]
            if not set(keys) <= station_keys:
                raise ValueError(f"RMP 线路 {line['source_line_id']} 引用了缺失站点")
            for index, (left, right) in enumerate(zip(keys, keys[1:])):
                pair = tuple(sorted((left, right)))
                if left == right or pair in seen:
                    continue
                seen.add(pair)
                parallel_index = parallel_counts.get(pair, 0)
                parallel_counts[pair] = parallel_index + 1
                # 真实站点直接相连；转角、圆角及共站并行偏移由 RMP 原生路径处理。
                edge(f"line_route_{number}_{chain_number}_{index}", left, right, "generic", attrs,
                     path_type="diagonal", path_attrs={"startFrom": "from" if left < right else "to",
                         "offsetFrom": 0, "offsetTo": 0, "roundCornerFactor": 10},
                     parallel_index=parallel_index)
                edges[-1]["attributes"]["cmmSourceLineId"] = line["source_line_id"]

    water = shape(scene["water"]) if scene.get("water") else Polygon()
    if not water.is_empty:
        if not water.is_valid:
            raise ValueError("无法转换无效水域几何")
        water_rgb = scene["water_color"]
        alpha = scene["water_alpha"] / 255
        # 面片统一使用白底合成水色，保持不透明，避免接缝叠色。
        water_color = [round(value*alpha + 255*(1-alpha)) for value in water_rgb]
        faces = _water_faces(water)
        for number, face in enumerate(faces):
            points = list(face.exterior.coords)[:-1]
            keys = [node(f"misc_node_water_{number}_{index}", point,
                         "fill" if index == 0 else "virtual",
                         {"color": theme(water_color), "opacity": 1, "selectedPatterns": []}
                         if index == 0 else None, z=-10)
                    for index, point in enumerate(points)]
            for index, left in enumerate(keys):
                edge(f"line_water_{number}_{index}", left, keys[(index+1) % len(keys)],
                     "single-color", {"color": theme(water_color)}, z=-10, visible=False)

    for number, row in enumerate(scene.get("facilities", [])):
        node(f"misc_node_facility_{number}", (row["x"], row["y"]), "facilities",
             {"type": FACILITY_TYPES[row["kind"]]}, z=6)
        if row.get("name"):
            node(f"misc_node_facility_name_{number}", (row["x"]+15, row["y"]), "text", {
                "content": row["name"], "fontSize": row.get("font_size", 10),
                "lineHeight": 12, "textAnchor": "start", "dominantBaseline": "middle",
                "language": "zh", "rotate": 0, "italic": "normal", "bold": "normal",
                "outline": 0, "color": theme("#333333")}, z=6, visible=row.get("name_visible", False))
    return {"version": 77, "svgViewBoxZoom": 100,
            "svgViewBoxMin": {"x": -20, "y": -20}, "graph": {
                "options": {"type": "directed", "multi": True, "allowSelfLoops": True},
                "attributes": {}, "nodes": nodes, "edges": edges}}
