"""定义 POI 归属分组和图标可见性类别。"""

TRANSIT_POI_KINDS = frozenset({
    "airport", "train", "metro", "ship", "bus", "trolleybus", "tram",
    "monorail", "cable_car", "helicopter", "cargo_train", "cargo_harbor",
    "private_airport", "taxi", "tours",
})


POI_GROUPS = ("transit", "facility", "ordinary", "area")


def poi_group_for(poi) -> str:
    """返回候选对象的稳定语义分组；只有实际站点记录属于交通分组，设施归属由身份图确定。"""
    source = getattr(getattr(poi, "source", None), "kind", "")
    if str(getattr(poi, "semantic_level", "")).upper() == "AREA":
        return "area"
    if str(source).casefold() == "stop":
        return "transit"

    semantic_level = str(getattr(poi, "semantic_level", "") or "").upper()
    group_id = str(getattr(poi, "group_id", "") or "")
    parent_id = str(getattr(poi, "parent_poi_id", "") or "")
    if (semantic_level == "FACILITY"
            or group_id.startswith("facility:")
            or parent_id.startswith("facility:")):
        return "facility"

    kind = str(getattr(poi, "kind", "") or "").casefold()
    if kind == "airport" and str(source).casefold() in {"building", "park"}:
        return "facility"
    return "ordinary"


def sublayer_for(poi) -> str:
    """返回旧版双子图层主题控件使用的兼容归属。"""
    return "transit" if poi_visibility_group_for(poi) == "transit" else "building"


def poi_visibility_group_for(poi) -> str:
    """按显示图标类别分类，独立于实体合并和归属分组。"""
    if poi_group_for(poi) == "area":
        return "area"
    kind = str(getattr(poi, "kind", "") or "").casefold()
    if kind in TRANSIT_POI_KINDS or poi_group_for(poi) == "transit":
        return "transit"
    return poi_group_for(poi)
