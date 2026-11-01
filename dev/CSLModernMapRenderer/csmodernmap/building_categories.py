"""两代建筑共用的着色类别；原生条目身份和用户图标选择保持独立。"""
from __future__ import annotations

import re
from .services.poi.mapping_registry import load_mapping_registry


CATEGORIES = (
    'residential', 'commercial', 'office', 'industrial',
    'police', 'prison', 'fire', 'healthcare', 'deathcare', 'education', 'administration',
    'transport', 'parking', 'post_telecom', 'electricity', 'water', 'garbage', 'maintenance',
    'park', 'amusement', 'culture', 'decoration', 'unclassified',
)

# 明确原生条目补充导出器合并掉的用途，不按名称子串或用户图标推断。
NATIVE_ENTRIES = {
    'cs1': {
        'amusement': ('Amusement Park Side Gate', 'Amusement Park Main Gate',
            'Small Amusement Park Main Gate', 'Ferris Wheel', 'Rollercoaster', 'MagickaPark'),
        'decoration': tuple(f'Boulder {n:02}' for n in range(1, 5))
            + tuple(f'Rock Area {n:02}' for n in range(1, 5))
            + tuple(f'Rock Formation {n:02}{suffix}' for n in range(1, 5) for suffix in ('', ' B', ' C'))
            + tuple(f'Cliff {n:02}' for n in range(1, 13))
            + tuple(f'Cave {n:02}' for n in range(1, 4)),
        'parking': ('Decoration Concrete Parking Lot 01', 'Decoration Concrete Parking Lot 02',
            'Decoration Concrete Parking Lot 03', 'ROJ Paid Parking Large', 'ROJ Paid Parking Small'),
        'deathcare': ('Cemetery', 'Crematory', 'Ancient Cemetery 01', 'Crematorium Memorial Park',
            'MP28_Crematorium01'),
    },
    'cs2': {
        'police': ('PoliceStation01', 'PoliceStation02', 'PoliceStation03', 'PoliceHeadquarters01',
            'CCP5_PoliceStation', 'RS_PoliceStation01', 'UbPr_PoliceHQ01', 'JP_PoliceHQ01',
            'JP_Police Station', 'CN_ModernPoliceStation', 'USNE_PoliceHQ01_7x6'),
        'prison': ('Prison01',),
        'healthcare': ('Hospital01', 'RS_Hospital01', 'EE_Hospital', 'CN_Hospital01',
            'MedicalClinic01', 'MedicalClinic02', 'RS_MedicalClinic01', 'UbPr_MedicalCenter01',
            'DiseaseControlCenter01', 'EE_DiseaseControl01', 'WelfareOffice01', 'RS_ServiceCenter'),
        'deathcare': ('Cemetery01', 'Cemetery02', 'Crematorium01', 'RuinsOldCemetery01'),
        'parking': tuple(f'ParkingLot{n:02}' for n in range(1, 18))
            + tuple(f'ParkingHall{n:02}' for n in range(1, 5))
            + ('AutomatedParkingBuilding01', 'BicycleParkingArea01', 'BicycleParkingHall01',
               'BicycleParkingHall02', 'BicycleParkingHall03', 'BicycleStorage01',
               'BicycleStorage02', 'BicycleStorage03'),
        'post_telecom': ('PostOffice01', 'PostOffice02', 'PostSortingFacility01',
            'Pack10-PostOffice01', 'EE_PostOffice02', 'TelecomTower01',
            'RS_TelecomBuilding01'),
        'maintenance': ('RoadMaintenanceDepot01', 'ParkMaintenanceDepot01'),
        'administration': ('CityHall01', 'RS_CityHall01', 'CentralBank01', 'JP_CityHall01',
            'CN_CityHall', 'CN_BonusCityHall01'),
        'amusement': ('ParkFerrisWheel01', 'ParkFerrisWheel02', 'FerrisWheel01', 'WaterPark01',
            'ParkBouncyCastle01'),
        'culture': ('RS_ArtMuseum', 'MaritimeMuseumSignature01', 'ObservationTower01',
            'CCP6_ServiceBuilding03', 'RuinsOldMill01', 'RuinsCastle01', 'RuinsStoneHeadFormation01',
            'RuinsStoneFormation01', 'RuinsStoneFormation02', 'RuinsStoneCairns01',
            'RuinsStoneCairns02', 'RuinsStoneCairns03'),
    },
}
_NATIVE = {
    (game, ('BUILDING_TITLE' if game == 'cs1' else 'Assets.NAME') + '[' + name + ']'): category
    for game, groups in NATIVE_ENTRIES.items()
    for category, names in groups.items() for name in names
}
_KINDS = {
    'police': 'police', 'police_generic': 'police', 'police_bank': 'administration', 'prison': 'prison',
    'hospital': 'healthcare', 'clinic': 'healthcare', 'childcare': 'healthcare',
    'eldercare': 'healthcare', 'sauna': 'healthcare', 'healthcare_generic': 'healthcare',
    'cemetery': 'deathcare', 'post': 'post_telecom', 'road_maintenance': 'maintenance',
    'snow_dump': 'maintenance', 'hotel': 'culture',
}
_SERVICES = {
    'Residential': 'residential', 'Commercial': 'commercial', 'Office': 'office',
    'Industrial': 'industrial', 'PlayerIndustry': 'industrial', 'Fishing': 'industrial',
    'Education': 'education', 'PlayerEducation': 'education', 'FireDepartment': 'fire',
    'Disaster': 'fire', 'PublicTransport': 'transport', 'Electricity': 'electricity',
    'Water': 'water', 'Garbage': 'garbage', 'Road': 'maintenance', 'ServicePoint': 'maintenance',
    'Beautification': 'park', 'VarsitySports': 'park', 'Race': 'park',
    'Monument': 'culture', 'Museums': 'culture', 'Hotel': 'culture',
}


def classify_building(building, game: str) -> str:
    native = _NATIVE.get((game.lower(), building.category_id))
    if native:
        return native
    service, sub = building.service, building.sub_service
    if sub == 'PoliceDepartmentBank':
        return 'administration'
    if sub == 'PublicTransportPost':
        return 'post_telecom'
    if game.lower() == 'cs1':
        if building.icls in ('DeathCare Facility', 'Cemetery Facility', 'Crematorium Facility'):
            return 'deathcare'
        entry = load_mapping_registry().lookup_building(building.icls, service, sub)
        if entry and entry.display == 'hidden':
            # 旧 POI 隐藏表同时含装饰、标记和轨道，不能全部当作自然装饰。
            return 'unclassified'
        if entry and entry.poi_kind in _KINDS:
            return _KINDS[entry.poi_kind]
    # CS2 旧导出把警察/监狱、医疗/殡葬合并；无已核实原生条目时不猜细类。
    if service in _SERVICES:
        return _SERVICES[service]
    role = str(building.role)
    if service in ('', 'None') and role in ('RESIDENTIAL', 'COMMERCIAL', 'OFFICE', 'INDUSTRIAL'):
        return role.lower()
    return 'unclassified'


def building_categories(document) -> dict[int, str]:
    """只沿已导出的父子关系继承，不按邻近关系或园区覆盖独立设施。"""
    by_id = {b.id: b for b in document.buildings}
    game = document.metadata.origin.game.lower()
    result = {}
    for building in document.buildings:
        chain, seen, current = [], set(), building
        while current.id not in result:
            if current.id in seen:
                raise ValueError(f'Building parent cycle: {current.id}')
            chain.append(current.id)
            seen.add(current.id)
            parent = by_id.get(current.parent_id)
            if parent is None:
                result[current.id] = classify_building(current, game)
                break
            current = parent
        category = result[current.id]
        for ident in chain:
            result[ident] = category
    return result


def category_palette(cfg: dict) -> dict[str, str]:
    return {key: value.get('color', cfg.get('default', '#D8D2C6')) for key, value in cfg.get('category_colors', {}).items()
            if key in CATEGORIES and value.get('enabled', False)}


def validate_category_colors(cfg: dict) -> None:
    colors = cfg.get('category_colors', {})
    if not isinstance(colors, dict):
        raise ValueError('buildings.category_colors: expected an object')
    for key, value in colors.items():
        if key not in CATEGORIES or not isinstance(value, dict) or set(value) - {'enabled', 'color'}:
            raise ValueError(f'buildings.category_colors.{key}: invalid category')
        color = value.get('color', cfg.get('default', '#D8D2C6'))
        if type(value.get('enabled', False)) is not bool or not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
            raise ValueError(f'buildings.category_colors.{key}: expected enabled and #RRGGBB color')
