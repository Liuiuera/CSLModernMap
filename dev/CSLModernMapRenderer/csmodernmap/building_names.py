"""选择建筑显示名称，同时保留来源名称和 POI 身份。"""
import logging
import re

from .cmm import DisplayName

LOGGER = logging.getLogger(__name__)


def is_unresolved_building_name(text: str, source: str) -> bool:
    return source != "custom" and re.fullmatch(r"BUILDING_TITLE\[.+\]:[0-9]+", text) is not None


class BuildingNames:
    def __init__(self, buildings, use_game_names=False):
        self.buildings = {int(item.id): item for item in buildings}
        self.use_game_names = bool(use_game_names)
        self.missing_categories = {
            item.id for item in self.buildings.values()
            if not all(getattr(item, key, "").strip() for key in
                       ("category_id", "category_name", "category_locale"))
        }
        self.warned_groups = set()
        if not self.use_game_names:
            missing = sorted(
                item.id for item in self.buildings.values()
                if item.id in self.missing_categories
                and not getattr(item, "custom_name", "")
                and getattr(item.display_name, "source",
                            getattr(item, "name_source", "empty")) != "custom")
            if missing:
                LOGGER.warning(
                    "%d 栋建筑缺少游戏官方分类数据，已跳过分类名称（建筑 ID：%s%s）",
                    len(missing), ", ".join(map(str, missing[:20])),
                    "…" if len(missing) > 20 else "")

    def building(self, building):
        name = building.display_name
        text = str(getattr(name, "text", name) or "").strip()
        source = str(getattr(name, "source", getattr(building, "name_source", "empty")))
        custom = str(getattr(building, "custom_name", "") or "").strip()
        if custom or source == "custom":
            return DisplayName(custom or text, "custom")
        if self.use_game_names:
            return DisplayName(text, source)
        if building.id in self.missing_categories:
            return DisplayName()
        return DisplayName(building.category_name, "category")

    def poi(self, poi, groups):
        if str(poi.semantic_level) == "AREA" or poi.name.source == "custom" or self.use_game_names:
            return poi.name
        if poi.source.kind == "building":
            return self.building(self.buildings[int(poi.source.id)])
        if poi.source.kind == "stop":
            return poi.name
        group = groups.get(str(poi.group_id))
        if (group is not None and str(group.semantic_level) == "FACILITY"
                and group.id.startswith("facility:building:")):
            owner_id = int(group.id.rsplit(":", 1)[1])
            owner = self.buildings.get(owner_id)
            if owner is None:
                if group.id not in self.warned_groups:
                    LOGGER.warning("设施 %s 缺少所属建筑 %s，已跳过分类名称", group.id, owner_id)
                    self.warned_groups.add(group.id)
                return DisplayName()
            return self.building(owner)
        # 区域级设施有独立名称，不依赖单个建筑的类别。
        return poi.name
