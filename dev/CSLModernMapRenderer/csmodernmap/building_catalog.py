"""建筑列表只按现有父子关系归并，不依赖预览可见性。"""
from pathlib import Path

from . import i18n
from .building_categories import building_categories
from .building_names import BuildingNames
from .preview_scene import _polygon_geometry, _building_category
from .services.poi.facility import _components


class BuildingCatalog:
    def __init__(self, document, use_game_names=False):
        self.document = document
        self.use_game_names = use_game_names
        self.groups, _ = _components(document.buildings)
        self.roots = {item.id: root for root, items in self.groups.items() for item in items}
        self.by_id = {item.id: item for item in document.buildings}
        self.categories = building_categories(document)
        self.names = BuildingNames(document.buildings, use_game_names)

    def rows(self, style=None):
        from .poi_color import POIColorResolver
        from .services.poi.direct_mapping import entry_key
        style = style or {}
        resolver = POIColorResolver({**style.get("poi_icons", {}),
                                    "_poi_mapping_override": style.get("_poi_mapping_override")})
        game = self.document.metadata.origin.game.lower()
        icons = {}
        for item in self.by_id.values():
            key = entry_key(game, item.category_id)
            if key in icons:
                continue
            entry = resolver.mapping.get(key)
            filename = entry["icons"].get(resolver.icon_map.library, "") if entry and entry["display"] else ""
            info = resolver.icon_map.get_icon(filename) if filename else None
            icons[key] = ((Path(resolver.icons_dir) / ("dark" if resolver.icon_map.appearance == "dark" else "")
                           / info["file"]).resolve().as_uri() if info and not info.get("hidden", False) else "")
        return [{"id": root, "name": self.names.building(self.by_id[root]).text,
                 "nameSource": self.names.building(self.by_id[root]).source,
                 "category": self.categories[root],
                 "categoryName": i18n.tr("building.category." + self.categories[root]),
                 "iconPath": icons[entry_key(game, self.by_id[root].category_id)],
                 "memberIds": [item.id for item in members]}
                for root, members in self.groups.items()]

    def geometry(self, root):
        polygons = []
        for item in self.groups[root]:
            geometry = _polygon_geometry(item.footprint.points)
            if geometry is not None:
                polygons.append(geometry["coordinates"])
        return {"type": "MultiPolygon", "coordinates": polygons}

    def selection(self, building_id, style):
        if type(building_id) is not int or building_id not in self.roots:
            raise ValueError("Unknown building ID")
        root = self.roots[building_id]
        item = self.by_id[root]
        category = self.categories[root]
        props = {"featureId": f"building:{root}", "buildingId": root,
                 "name": self.names.building(item).text, "category": _building_category(item),
                 "colorCategory": category, "categoryId": item.category_id,
                 "categoryName": item.category_name, "categoryLocale": item.category_locale,
                 "height_m": item.height_m, "base_elevation_m": item.base_elevation_m,
                 "height_source": item.height_source, "stationId": item.station_id,
                 "groupMemberCount": len(self.groups[root])}
        from .poi_color import POIColorResolver
        color = POIColorResolver({**style.get("poi_icons", {}),
                                 "_poi_mapping_override": style.get("_poi_mapping_override")}).building_color(
                                     item, self.document.metadata.origin.game.lower())
        props["poiColor"] = "#%02X%02X%02X" % tuple(color[1]) if color is not None else None
        feature = {"type": "Feature", "id": props["featureId"],
                   "properties": props, "geometry": self.geometry(root)}
        return {"objectType": "building", "styleLayer": "building", "source": "building-catalog",
                "layerId": "buildings", "featureId": props["featureId"],
                "geometryType": "MultiPolygon", "properties": props, "feature": feature,
                "members": [{"id": child.id, "name": self.names.building(child).text,
                             "categoryName": i18n.tr("building.category." + self.categories[child.id])}
                            for child in self.groups[root] if child.id != root]}

    def highlight(self, category):
        if not isinstance(category, str) or category not in set(self.categories.values()):
            raise ValueError("Unknown building category")
        return {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"buildingId": root}, "geometry": self.geometry(root)}
            for root in self.groups if self.categories[root] == category]}
