"""公开站点分组与派生服务。"""

from .grouping import StationGroupingService, group_stations, is_transfer_station
from .sidecar import (
    EDIT_KINDS,
    SCHEMA_SCOPE,
    SCHEMA_VERSION,
    StationFix,
    StationFixApplication,
    StationSidecar,
    StationSidecarError,
    StationSidecarIssue,
    apply_station_fixes,
    apply_sidecar,
    load_station_sidecar,
    load_station_sidecar_if_present,
    load_sidecar,
    merge_params_with_sidecar,
    parse_station_sidecar,
    resolve_station_sidecar_path,
    station_sidecar_to_json,
    validate_station_invariants,
    validate_station_sidecar,
    validate_sidecar,
)


def build_station_model(*args, **kwargs):
    """延迟公开完整交通及站点模型的构建接口。"""
    from ..transit.model import build_transit_model

    return build_transit_model(*args, **kwargs)

__all__ = [
    "group_stations", "is_transfer_station", "StationGroupingService",
    "EDIT_KINDS", "SCHEMA_SCOPE", "SCHEMA_VERSION", "StationFix",
    "StationFixApplication", "StationSidecar", "StationSidecarError",
    "StationSidecarIssue", "apply_station_fixes", "build_station_model",
    "apply_sidecar",
    "load_station_sidecar", "load_station_sidecar_if_present",
    "load_sidecar",
    "merge_params_with_sidecar", "parse_station_sidecar",
    "resolve_station_sidecar_path", "station_sidecar_to_json",
    "validate_station_invariants", "validate_station_sidecar",
    "validate_sidecar",
]
