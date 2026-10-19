"""公开 POI 目录、分类器和派生服务。"""

from .catalog import (
    DedupeSpec,
    PoiCatalogDefinition,
    PoiKindSpec,
    catalog_path,
    clear_catalog_cache,
    load_catalog,
    validate_catalog,
)
from .classifier import AssetClassification, AssetClassifier, classify_building_poi
from .facility import (
    FacilityGroupCatalog,
    build_facility_groups,
    facility_container_building_ids,
)
from .model import POIJobParams, PoiCatalog


def __getattr__(name):
    """延迟加载依赖交通服务的接口，以避免包循环导入。"""
    if name in {
        "FORMAL_SOURCES", "TRANSIT_POI_KINDS", "produce",
    }:
        from . import service
        return getattr(service, name)
    raise AttributeError(name)

__all__ = [
    "DedupeSpec", "PoiCatalogDefinition", "PoiKindSpec", "catalog_path",
    "clear_catalog_cache", "load_catalog", "validate_catalog",
    "AssetClassification", "AssetClassifier", "classify_building_poi",
    "FacilityGroupCatalog", "build_facility_groups",
    "facility_container_building_ids",
    "POIJobParams", "PoiCatalog",
    "FORMAL_SOURCES", "TRANSIT_POI_KINDS",
    "produce",
]
