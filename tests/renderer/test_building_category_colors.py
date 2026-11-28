"""两代类别改色的作用范围、父子继承和预览/PNG 契约。"""
from contextlib import ExitStack
from copy import deepcopy
import json

import pytest
from PIL import Image, ImageDraw

from csmodernmap.building_categories import classify_building, building_categories, category_palette
from csmodernmap.cmm import (Building, BuildingRole, DisplayName, Polygon, Vec2,
    MapDocument, Metadata, Origin, Terrain, DocumentSession)
from csmodernmap.layers.base import RenderContext
from csmodernmap.layers.building_layer import BuildingLayer
from csmodernmap.preview_scene import PreviewSceneCompiler, preview_theme, preview_source_signatures
from csmodernmap.style import Style
from csmodernmap.theme_contract import normalize_theme, validate_theme_values
from csmodernmap.theme_metadata import describe_theme
from test_object_inspector import server, selection, read, write, fields, history


def building(ident, service, native='', *, game='cs1', icls='', sub='', parent=None):
    x = 20 + (ident - 1) * 90
    return Building(ident, native, DisplayName('Custom name', 'custom'), BuildingRole.OTHER,
        Polygon((Vec2(x,20), Vec2(x+60,20), Vec2(x+60,80), Vec2(x,80))),
        service=service, sub_service=sub, icls=icls, parent_id=parent,
        category_id=('BUILDING_TITLE' if game == 'cs1' else 'Assets.NAME') + '['+native+']')


def document(game, items):
    return MapDocument(metadata=Metadata(Origin(game,'test','category-colors')),sea_level=0,
        nodes=[],networks=[],buildings=items,districts=[],stops=[],transit_lines=[],routes=[],terrain=Terrain())


@pytest.mark.parametrize('game,service,native,icls,sub,expected', [
    ('cs1','PoliceDepartment','Police Station','Police Department Facility','','police'),
    ('cs1','PoliceDepartment','Bank 01','Bank Office','PoliceDepartmentBank','administration'),
    ('cs1','PoliceDepartment','Prison','Prison Facility','','prison'),
    ('cs1','HealthCare','Custom asset','DeathCare Facility','','deathcare'),
    ('cs1','Road','Custom asset','Road Maintenance Facility','','maintenance'),
    ('cs1','Beautification','Rollercoaster','Parks Item','','amusement'),
    ('cs1','Beautification','Boulder 01','Beautification Item','','decoration'),
    ('cs1','Beautification','Unknown','Beautification Marker','','unclassified'),
    ('cs1','PlayerIndustry','Custom asset','Warehouses','','industrial'),
    ('cs2','PoliceDepartment','JP_PoliceHQ01','','','police'),
    ('cs2','PoliceDepartment','Prison01','','','prison'),
    ('cs2','PoliceDepartment','Unknown mod','','','unclassified'),
    ('cs2','HealthCare','Cemetery01','','','deathcare'),
    ('cs2','HealthCare','EE_Hospital','','','healthcare'),
    ('cs2','HealthCare','Unknown mod','','','unclassified'),
    ('cs2','None','ParkingLot13','','','parking'),
    ('cs2','None','PostOffice01','','','post_telecom'),
    ('cs2','None','CentralBank01','','','administration'),
    ('cs2','None','StarQ_SP CN_BonusLowRent01_L1_3x2','','','unclassified'),
    ('cs2','Beautification','WaterPark01','','','amusement'),
    ('cs2','Fishing','Unknown fish producer','','','industrial'),
])
def test_two_game_boundaries(game, service, native, icls, sub, expected):
    item = building(1,service,native,game=game,icls=icls,sub=sub)
    assert classify_building(item,game) == expected
    item.display_name = DisplayName('Police Hospital Rollercoaster', 'custom')
    assert classify_building(item,game) == expected


def render(doc, data):
    image=Image.new('RGB',(480,100),'white')
    ctx=RenderContext(image,ImageDraw.Draw(image),lambda x,z:(x,z),Style(data),
        world_x0=0,world_z0=0,world_x1=480,world_z1=100,detail_mode='detailed',final_zoom=1)
    BuildingLayer().render(ctx,doc)
    return image


@pytest.mark.parametrize('game', ['cs1','cs2'])
def test_category_changes_police_and_expansion_only_in_png_and_preview(tmp_path, game, modern_style):
    doc=document(game,[
        building(1,'PoliceDepartment','Police Station' if game=='cs1' else 'JP_PoliceHQ01',game=game,icls='Police Department Facility'),
        building(2,'Beautification','Rollercoaster' if game=='cs1' else 'WaterPark01',game=game),
        building(3,'PoliceDepartment' if game=='cs1' else 'None','Bank 01' if game=='cs1' else 'CentralBank01',game=game,sub='PoliceDepartmentBank' if game=='cs1' else ''),
        building(4,'None','Unmapped extension',game=game,parent=1),
        building(5,'None','Unmapped independent',game=game),
    ])
    cfg=deepcopy(modern_style.data)
    cfg['buildings']={'color_mode':'poi','default':'#D8D2C6','border_width':0}
    cfg['poi_icons']={'enabled':False}
    original=render(doc,cfg)
    cfg['buildings'].update(color_mode='category',category_colors={'police':{'enabled':True,'color':'#FF0000'}})
    changed=render(doc,cfg)
    assert [building_categories(doc)[i] for i in range(1,6)] == ['police','amusement','administration','police','unclassified']
    for i in (1,4):
        assert changed.getpixel((50+(i-1)*90,50)) == (255,0,0)
    for i in (2,3,5):
        point=(50+(i-1)*90,50)
        assert changed.getpixel(point)==original.getpixel(point)
    with ExitStack() as scope:
        PreviewSceneCompiler._compile_domain_sources(doc,DocumentSession(doc),tmp_path,1,{},
            {'changed':{'buildings'},'style':cfg},domain_scope=scope)
    source=json.loads((tmp_path/'buildings.geojson').read_text(encoding='utf-8'))
    props={f['properties']['buildingId']:f['properties'] for f in source['features']}
    assert {i:p['colorCategory'] for i,p in props.items()} == building_categories(doc)
    assert preview_theme(cfg)['building']['categoryColors']=={'police':'#FF0000'}
    cfg['buildings']['category_colors']['police']['enabled']=False
    assert render(doc,cfg).tobytes()==original.tobytes()
    cfg['buildings']['category_colors']['police']['enabled']=True
    cfg['buildings']['color_mode']='poi'
    assert render(doc,cfg).tobytes()==original.tobytes()


def test_theme_category_defaults_metadata_and_round_trip(modern_style):
    raw=modern_style.data
    raw['buildings']['default']='#123456'
    before=deepcopy(raw)
    normalized=normalize_theme(raw)
    assert raw==before
    assert normalize_theme(normalized)==normalized
    assert category_palette(normalized['buildings'])=={}
    value=normalized['buildings']['category_colors']['police']
    value.update(enabled=True,color='#112233')
    validate_theme_values(normalized)
    assert category_palette(json.loads(json.dumps(normalized))['buildings'])=={'police':'#112233'}
    metadata=describe_theme(normalized)
    assert metadata['buildings.category_colors.police.color']['kind']=='color'
    assert metadata['buildings.category_colors.police.enabled']['kind']=='toggle'
    assert metadata['buildings.category_colors.police']['label']!='police'
    for bad in ({'enabled':'true','color':'#112233'},{'enabled':True,'color':'red'}):
        value.clear(); value.update(bad)
        with pytest.raises(ValueError,match='category_colors.police'):
            validate_theme_values(normalized)


def test_inspector_targets_category_and_undo_reset_preserve_other_categories(server):
    obj=selection('building',colorCategory='police',poiColor='#123456')
    cfg=server.backend._style_data_mut()['buildings']
    cfg['color_mode']='category'
    cfg['category_colors']={'amusement':{'enabled':True,'color':'#00FF00'}}
    model=write(server,obj,read(server,obj),'building.category.enabled',True)
    model=history(server,obj,model,'undoInspector')
    validate_theme_values(server.backend.current_style_data())
    model=history(server,obj,model,'redoInspector')
    model=write(server,obj,model,'building.category.color','#FF0000')
    assert category_palette(server.backend.current_style_data()['buildings'])=={'police':'#FF0000','amusement':'#00FF00'}
    model=history(server,obj,model,'undoInspector')
    assert fields(model)['building.category.color']['value']!='#FF0000'
    model=history(server,obj,model,'redoInspector')
    assert fields(model)['building.category.color']['value']=='#FF0000'
    server.dispatch('resetInspectorGroup',{'object':obj,'context':'map','documentEpoch':0,
        'settingsRevision':model['settingsRevision'],'group':'building_category'})
    assert category_palette(server.backend.current_style_data()['buildings'])=={'amusement':'#00FF00'}
    assert 'building.category.color' not in fields(read(server,selection('building',colorCategory='../roads')))
