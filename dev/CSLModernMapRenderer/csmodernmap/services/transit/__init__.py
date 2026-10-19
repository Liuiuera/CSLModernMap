"""公开交通派生服务和模型构建入口。"""

from .color import AutoColorService, effective_color, resolve_auto_colors
from .facility import StationFacilityService, match_facilities, match_station_facilities
from .geometry import GeometryResult, RouteGeometryService, WorldPoint, resolve_route_geometry
from .grouping import LineGroupingService, NormalizedTransit, build_line_groups, normalize_transit
from .loop import LoopEvidenceService, effective_sequence, is_loop_route
from .model import TransitSelection, build_station_model, build_transit_model
from .name import NameResolutionService
from .visibility import VisibilityService, build_visibility

__all__ = [
    "NormalizedTransit", "normalize_transit", "build_line_groups",
    "LineGroupingService", "GeometryResult", "WorldPoint", "resolve_route_geometry",
    "RouteGeometryService", "effective_sequence", "is_loop_route",
    "LoopEvidenceService", "resolve_auto_colors", "effective_color",
    "AutoColorService", "build_visibility", "VisibilityService",
    "NameResolutionService", "build_transit_model", "build_station_model",
    "TransitSelection",
    "match_facilities", "match_station_facilities",
    "StationFacilityService",
]
