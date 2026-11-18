"""Facility identity and child controls survive both preview POI sources."""
from __future__ import annotations

import json
from math import log2

import pytest

from csmodernmap.cmm import (
    Building, BuildingRole, DisplayName, District, DocumentSession,
    MapDocument, Metadata, NetworkNode, Origin, Polygon, Route, Stop, StopKind,
    Terrain, TransitKind, TransitLine, Vec2, Vec3,
)
from csmodernmap.preview_scene import PreviewSceneCompiler
from csmodernmap.style import Style


def facility_document(area_type="Airport", area_id=-2):
    buildings = []
    for building_id, center, ai in (
        (10, 40, "AirportBuildingAI"),
        (11, 56, "MetroStationAI"),
        (12, 400, "AirportBuildingAI"),
    ):
        buildings.append(Building(
            building_id, ai, DisplayName(f"Facility {building_id}", "custom"),
            BuildingRole.TRANSIT_STATION,
            Polygon((Vec2(center - 2, center - 2), Vec2(center + 2, center - 2),
                     Vec2(center + 2, center + 2), Vec2(center - 2, center + 2))),
            icls=ai,
        ))
    return MapDocument(
        metadata=Metadata(Origin("cs1", "test", "facility-preview")), sea_level=0,
        nodes=[NetworkNode(1, Vec3(48, 0, 48))], networks=[], buildings=buildings,
        districts=[District(
            area_id, DisplayName("Functional area", "custom"), "PARK_ZONE",
            area_type, [Vec2(48, 48)], grid_cell=96, position=Vec2(48, 48), source_raw=area_type,
        )],
        stops=[Stop(20, 1, StopKind.METRO, DisplayName("Area station", "custom"),
                    Vec3(48, 0, 48), serving_line_ids=[7])],
        transit_lines=[TransitLine(7, "Area line", TransitKind.METRO)],
        routes=[Route(70, 7, "outbound", [20], False, "stop_direct")], terrain=Terrain(),
    )
