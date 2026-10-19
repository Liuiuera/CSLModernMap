# -*- coding: utf-8 -*-
"""
POI 图标工具模块
提供图标查询、坐标获取、精灵图裁切等功能

使用示例:
    from poi_icon_utils import POIIconMap

    icon_map = POIIconMap()  # 默认读取本模块同目录下的 icons_map.json

    # 按ID获取图标信息
    info = icon_map.get_icon("medical_hospital")
    print(info["x"], info["y"], info["category"])

    # 按类别获取所有图标
    hospitals = icon_map.get_by_category("医疗健康")

    # 从精灵图中裁切单个图标
    icon_img = icon_map.crop_icon("medical_hospital", sprite_path="icons_1.png")
"""

import json
import os
import sys
from PIL import Image


def bundle_base_dir():
    """返回当前运行方式下包含 icons_map.json 的基目录。

    - 源码运行：本文件所在目录（assets/cn_icons）
    - PyInstaller：sys._MEIPASS（解压目录）
    - Nuitka 打包：可执行文件解压/所在目录（含 cn_icons）

    Nuitka 下本模块被编译进二进制，__file__ 指向构建机源码路径，
    不能用它定位资源，必须按实际存在 icons_map.json 的位置探测。
    """
    candidates = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(os.path.abspath(meipass))
    if getattr(sys, "frozen", False) or hasattr(sys, "__compiled__"):
        candidates.append(os.path.dirname(os.path.abspath(sys.executable)))
    candidates.append(os.path.dirname(os.path.abspath(__file__)))
    for cand in candidates:
        if os.path.isfile(os.path.join(cand, "icons_map.json")):
            return cand
    return candidates[-1]


class POIIconMap:
    def __init__(self, json_path=None, mapping_path=None, appearance="light"):
        if appearance not in ("light", "dark"):
            raise ValueError("Unknown icon appearance: " + str(appearance))
        self.appearance = appearance
        if json_path is None:
            json_path = os.path.join(bundle_base_dir(), "icons_map.json")

        with open(json_path, "r", encoding="utf-8") as f:
            self.data = json.load(f)
        self.base_dir = os.path.dirname(os.path.abspath(json_path))
        self.mapping_path = mapping_path
        self.library = os.path.basename(self.base_dir)
        self.direct_mapping = None

        self.meta = self.data["meta"]
        self.icons = self.data["icons"]
        self.categories = self.data.get("categories", [])
        self.storage = self.meta.get("storage", "sprite")
        if self.storage not in ("sprite", "files"):
            raise ValueError("Unknown icon storage: " + self.storage)

        # 建立索引
        self._by_id = {icon["id"]: icon for icon in self.icons}
        self._by_file = {icon.get("file"): icon for icon in self.icons}
        self._by_category = {}
        for icon in self.icons:
            cat = icon["category"]
            if cat not in self._by_category:
                self._by_category[cat] = []
            self._by_category[cat].append(icon)

    def get_icon(self, icon_id):
        if "::" in icon_id:
            if self.direct_mapping is None:
                from csmodernmap.services.poi.direct_mapping import load_mapping
                self.direct_mapping = load_mapping(self.mapping_path)
            entry = self.direct_mapping.get(icon_id)
            filename = (entry["icons"].get(self.library, "")
                        if entry and entry["display"] else "")
            return self._by_file.get(filename) if filename else None
        return self._by_file.get(icon_id) or self._by_id.get(icon_id)

    def is_hidden(self, icon_id):
        """Return whether this icon key explicitly suppresses POI rendering."""
        info = self.get_icon(icon_id)
        return info is None or bool(info.get("hidden", False))

    def get_by_category(self, category):
        return self._by_category.get(category, [])

    def list_categories(self):
        return list(self.categories)

    def search_by_name(self, keyword):
        keyword = keyword.lower()
        return [icon for icon in self.icons
                if keyword in icon["name"].lower() or keyword in icon["id"].lower()]

    def crop_icon(self, icon_id, sprite_image=None, sprite_path=None, scale=1.0):
        info = self.get_icon(icon_id)
        if not info or info.get("hidden", False):
            return None

        if self.storage == "files":
            filename = os.path.abspath(os.path.join(self.base_dir, "dark" if self.appearance == "dark" else "", info["file"]))
            if os.path.commonpath([self.base_dir, filename]) != self.base_dir:
                raise ValueError("Icon file escapes library directory")
            with Image.open(filename) as source:
                icon = source.convert("RGBA")
            if scale != 1.0:
                icon = icon.resize((max(1, int(icon.width * scale)),
                                    max(1, int(icon.height * scale))), Image.LANCZOS)
            return icon

        if sprite_image is None:
            if sprite_path is None:
                sprite_path = os.path.join(self.base_dir, self.meta["sprite"])
            sprite_image = Image.open(sprite_path)

        x = info["x"]
        y = info["y"]
        w = self.meta["tile_width"]
        h = self.meta["tile_height"]

        icon = sprite_image.crop((x, y, x + w, y + h))

        if scale != 1.0:
            new_w = int(w * scale)
            new_h = int(h * scale)
            icon = icon.resize((new_w, new_h), Image.LANCZOS)

        return icon

    def get_sprite_info(self):
        return dict(self.meta)

    def render_icon(self, icon_id, sprite_image=None):
        """Normalize transparent margins at native resolution; retain raw crops."""
        icon = self.crop_icon(icon_id, sprite_image=sprite_image)
        info = self.get_icon(icon_id)
        if icon is None or not info.get("normalize_content", False):
            return icon
        bounds = icon.getchannel("A").point(lambda a: 255 if a >= 128 else 0).getbbox()
        if bounds is None:
            return icon
        width, height = bounds[2]-bounds[0], bounds[3]-bounds[1]
        side = max(width, height)
        padding = max(1, round(side / 18))
        canvas = Image.new("RGBA", (side + padding * 2,) * 2)
        canvas.paste(icon, ((canvas.width-width)//2-bounds[0],
                            (canvas.height-height)//2-bounds[1]))
        return canvas

    def load_sprite(self, sprite_path=None):
        """Return the legacy sheet, or None for a standalone-file library."""
        if self.storage == "files":
            return None
        path = sprite_path or self.meta["sprite"]
        if not os.path.isabs(path):
            path = os.path.join(self.base_dir, path)
        with Image.open(path) as source:
            return source.convert("RGBA")

    def __len__(self):
        return len(self.icons)

    def __iter__(self):
        return iter(self.icons)


def main():
    icons_dir = os.path.dirname(os.path.abspath(__file__))
    json_path = os.path.join(icons_dir, "icons_map.json")

    icon_map = POIIconMap(json_path)

    print(f"=== POI 图标库 ===")
    print(f"图标总数: {len(icon_map)}")
    print(f"精灵图尺寸: {icon_map.meta['tile_width']}x{icon_map.meta['tile_height']} / 个")
    print(f"网格: {icon_map.meta['cols']}列 x {icon_map.meta['rows']}行")
    print()

    print("=== 类别列表 ===")
    for cat in icon_map.list_categories():
        count = len(icon_map.get_by_category(cat))
        print(f"  {cat}: {count} 个")
    print()

    print("=== 示例: 医疗健康类图标 ===")
    for icon in icon_map.get_by_category("医疗健康"):
        print(f"  {icon['id']:25s} {icon['name']:6s} 位置({icon['x']:3d},{icon['y']:3d})")
    print()

    print("=== 示例: 交通出行类图标 ===")
    for icon in icon_map.get_by_category("交通出行"):
        print(f"  {icon['id']:25s} {icon['name']:6s} 位置({icon['x']:3d},{icon['y']:3d})")


if __name__ == "__main__":
    main()
