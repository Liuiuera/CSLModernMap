"""保存共享交通排版的对象、资源及诊断；绘制端不再决定布局。"""
from __future__ import annotations

from collections import defaultdict
import math
import hashlib
from pathlib import Path

from .artwork import bitmap, circle, image_resource, polyline
from PIL import Image


class ArtworkCapture:
    def __init__(self, *, draft=False):
        self.draft = draft
        self.export_resources = {}
        self.export_bindings = {}
        self.path_recipes = {}
        self.objects = []
        self.resources = {}
        self.prefix = "transit"
        self._counts = defaultdict(int)
        self._fonts = {}
        self.labels = []
        self.station_names = []
        self.diagnostics = {'names': [], 'badges': [], 'symbols': [], 'facilitySelection': []}

    def _id(self, key):
        index = self._counts[key]
        self._counts[key] += 1
        return f"{key}:{index}"

    def reuse_objects(self, payload, plan, span):
        """复用已验证的对象及其预览、正式导出资源，保留原绘制顺序。"""
        for item in payload['objects'][span[0]:span[1]]:
            self.objects.append(item)
            if item['kind'] != 'image':
                continue
            key = item['resource']
            self.resources[key] = payload['resources'][key]
            object_id = item['id']
            if object_id in plan['paths']:
                recipe = self.path_recipes[object_id] = plan['paths'][object_id]
                source = recipe['source']
            else:
                source = self.export_bindings[object_id] = plan['bindings'][object_id]
            self.export_resources[source] = plan['resources'][source]

    def line(self, points, width, stroke):
        # 保留全部路径点；限制远低于绘制精度的尾数，避免大作品被 JSON 尾数撑满。
        points = [(round(x, 3), round(y, 3)) for x, y in points]
        item = polyline(self._id(self.prefix + ":line"), points, width, stroke)
        # AGG 的线端由交通层另画圆帽；不能在网页再补一次。
        item["lineCap"] = "butt"
        self.objects.append(item)

    def cap(self, point, width, fill):
        point = [round(value, 3) for value in point]
        self.objects.append(circle(self._id(self.prefix + ":cap"), point, width,
                                   fill=fill, stroke_width=0))

    def image(self, key, image, bounds, *, text_run=None, path_recipe=None):
        original = None
        resized = False
        if self.draft and path_recipe is None:
            original = image_resource(image)
            size = (max(1, math.ceil(bounds[2]-bounds[0])), max(1, math.ceil(bounds[3]-bounds[1])))
            if image.width > size[0] or image.height > size[1]:
                image = image.resize(size, Image.Resampling.LANCZOS)
                resized = True
        resource = image_resource(image)
        if resized:
            image.close()
        self.resources[resource["id"]] = resource
        object_id = self._id(key)
        if self.draft:
            if path_recipe is not None:
                source = path_recipe['source']
                self.export_resources[source['id']] = source
                self.path_recipes[object_id] = {**path_recipe, 'source': source['id']}
            else:
                self.export_resources[original['id']] = original
                self.export_bindings[object_id] = original['id']
        self.objects.append(bitmap(object_id, resource, bounds, text_run=text_run))

    def tiled_image(self, key, image):
        # 相邻分片各带 8px 内容，避免分数观察倍率和下采样时露出片缝。
        for y in range(0, image.height, 512):
            for x in range(0, image.width, 512):
                box = (max(0, x-8), max(0, y-8),
                       min(x+520, image.width), min(y+520, image.height))
                tile = image.crop(box)
                try:
                    if tile.mode != 'RGBA' or tile.getbbox() is not None:
                        self.image(f'{key}:{x}:{y}', tile, box)
                finally:
                    tile.close()

    def font_details(self, text, font):
        path = Path(font.path)
        identity = self._fonts.get((str(path), font.index))
        if identity is None:
            from fontTools.ttLib import TTFont
            with TTFont(str(path), fontNumber=font.index, lazy=True) as face:
                identity = {"fontId": hashlib.sha256(path.read_bytes()).hexdigest(),
                            "fontIndex": font.index, "fontFile": path.name,
                            "fontWeight": int(face['OS/2'].usWeightClass),
                            "coverage": set(face.getBestCmap())}
            self._fonts[(str(path), font.index)] = identity
        return {key: value for key, value in identity.items() if key != "coverage"} | {
            "missing": sorted({f"U+{ord(char):04X}" for char in text
                               if ord(char) not in identity["coverage"]})}

    def text_run(self, text, font, position, *, stroke_width=0):
        ascent, _ = font.getmetrics()
        return self.font_details(text, font) | {
            "text": text, "fontSize": font.size, "advance": font.getlength(text),
            "baseline": [position[0], position[1] + ascent], "strokeWidth": stroke_width}
