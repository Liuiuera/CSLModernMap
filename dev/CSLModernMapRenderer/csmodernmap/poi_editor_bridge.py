"""连接 WinUI 进程边界上的 POI 映射编辑操作。"""
from __future__ import annotations
from copy import deepcopy
import json
from pathlib import Path
from . import i18n
from ._paths import icons_library_dir
from .services.poi.direct_mapping import (
    LIBRARIES, bundled_path, clear_cache, entry_key, game_for,
    icon_files, load_mapping, save_mapping, user_path,
    document_warnings,
)


def document_entries(document) -> tuple[dict,list[str]]:
    result={};warnings=[]
    if document is None:return result,warnings
    game=game_for(document)
    for building in document.buildings:
        native_id=building.category_id
        if not native_id:continue
        key=entry_key(game,native_id)
        names={building.category_locale:building.category_name} if building.category_name and building.category_locale else {}
        result[key]={'game':game,'id':native_id,'names':names,'source':'current document',
                     'icons':{library:'' for library in LIBRARIES},'display':True}
    for stop in document.stops:
        native_id='stop:'+str(getattr(stop.kind,'value',stop.kind)).lower()
        result.setdefault(entry_key(game,native_id),{'game':game,'id':native_id,'names':{},'source':'current document',
                     'icons':{library:'' for library in LIBRARIES},'display':True})
    for park in document.districts:
        if str(getattr(park.kind,'value',park.kind))!='PARK_ZONE':continue
        native_id='park:'+(park.source_raw or str(getattr(park.zone_kind,'value',park.zone_kind)))
        result.setdefault(entry_key(game,native_id),{'game':game,'id':native_id,'names':{},'source':'current document',
                     'icons':{library:'' for library in LIBRARIES},'display':True})
    warnings.extend(document_warnings(document))
    return result,warnings


class PoiEditorBridge:
    def __init__(self, backend, thumbnail_dir: Path) -> None:
        self.backend=backend
        self.thumbnail_dir=thumbnail_dir
        self.mapping_path=bundled_path()
        self.override_path=Path(getattr(backend,'poi_mapping_override',user_path()))
        if self.override_path.suffix.lower()!='.json':
            self.override_path=self.override_path.with_name('poi_direct_mapping.json')

    def _entries(self):
        current,warnings=document_entries(getattr(self.backend,'document',None))
        entries=deepcopy(load_mapping(self.override_path))
        for key,value in current.items():entries.setdefault(key,value)
        return entries,warnings

    def read(self) -> dict:
        clear_cache()
        entries,warnings=self._entries()
        defaults=load_mapping(defaults=True)
        libraries={}
        for library in LIBRARIES:
            directory=icons_library_dir(library)
            libraries[library]=[{'file':filename,'name':item['name'],'url':(directory/filename).as_uri()}
                                for filename,item in icon_files(library).items()]
        language=i18n.LANG
        rows={}
        for key,entry in entries.items():
            names=entry['names']
            title=names.get(language) or (names.get('zh-CN') if language.startswith('zh') else names.get('en')) or entry['id']
            default=defaults.get(key)
            rows[key]={**entry,'name':title,'default':{'icons':dict(default['icons']) if default else {library:'' for library in LIBRARIES},'display':default['display'] if default else True}}
        return {'entries':rows,'libraries':libraries,'warnings':warnings,
                'sources':json.loads(self.mapping_path.read_text('utf-8'))['sources']}

    def _validated(self,changes):
        if not isinstance(changes,dict):raise ValueError('映射修改必须是对象')
        entries,_=self._entries();available={library:icon_files(library) for library in LIBRARIES}
        for key,fields in changes.items():
            if key not in entries:raise ValueError(f'未知游戏条目：{key}')
            if not isinstance(fields,dict) or set(fields)!={'icons','display'}:
                raise ValueError(f'映射字段不完整：{key}')
            icons=fields['icons']
            if not isinstance(icons,dict) or set(icons)!=set(LIBRARIES):raise ValueError(f'必须提供 CSLModernMap 图库：{key}')
            for library,filename in icons.items():
                if not isinstance(filename,str) or (filename and filename not in available[library]):raise ValueError(f'未知图片：{library}/{filename}')
            if type(fields['display']) is not bool:raise ValueError(f'显示开关必须为布尔值：{key}')
            entries[key]={**entries[key], 'icons':dict(icons),'display':fields['display']}
        return entries

    def validate(self,changes) -> list:
        self._validated(changes)
        return []

    def save(self,changes) -> int:
        entries=self._validated(changes)
        if changes:
            save_mapping(entries,self.override_path)
            self.backend.poi_mapping_saved()
        return len(changes)
