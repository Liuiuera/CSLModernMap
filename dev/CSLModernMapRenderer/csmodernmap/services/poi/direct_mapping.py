"""统一原生游戏对象到 PNG 图标的直接映射。"""
from __future__ import annotations
from copy import deepcopy
from functools import lru_cache
import json
import os
from pathlib import Path
import tempfile
import warnings

LIBRARIES = ('cn_icons',)
SCHEMA_VERSION = 3
MISSING_ID_WARNING = '部分建筑缺少原生条目 ID，请使用新版导出器重新导出地图；这些建筑不绘制 POI 图标。'

def document_warnings(document) -> list[str]:
    return ([MISSING_ID_WARNING] if document is not None and
            any(not building.category_id for building in document.buildings) else [])

def bundled_path() -> Path:
    return Path(__file__).with_name('poi_direct_mapping.json')

def user_path() -> Path:
    from ..._paths import user_data_dir
    return user_data_dir() / 'poi_direct_mapping.json'

def entry_key(game: str, native_id: str) -> str:
    return f'{game}::{native_id}'

def game_for(source) -> str:
    document = getattr(source, '_document', source)
    metadata = getattr(document, 'metadata', None)
    origin = getattr(metadata, 'origin', None)
    return str(getattr(origin, 'game', getattr(source, 'game', ''))).lower()

def icon_files(library: str, *, directory: Path | None = None) -> dict[str, dict]:
    if directory is None:
        from ..._paths import icons_library_dir
        directory = icons_library_dir(library)
    manifest = json.loads((directory / 'icons_map.json').read_text('utf-8'))
    result = {}
    for item in manifest['icons']:
        filename = item['file']
        if Path(filename).name != filename or not filename.endswith('.png'):
            raise ValueError(f'Invalid PNG filename in {library}: {filename!r}')
        if not (directory / filename).is_file():
            raise ValueError(f'Missing POI image: {library}/{filename}')
        if filename in result:
            raise ValueError(f'Duplicate POI image: {library}/{filename}')
        result[filename] = dict(item)
    return result

def validate_entry(value: dict, available: dict[str, dict]) -> dict:
    if not isinstance(value, dict) or set(value) - {'game','id','names','source','icons','display'}:
        raise ValueError('Invalid direct POI mapping entry')
    game = value.get('game'); native_id = value.get('id')
    if game not in ('cs1','cs2') or not isinstance(native_id,str) or not native_id.strip():
        raise ValueError('Direct POI mappings require a game and native entry ID')
    icons=value.get('icons')
    if not isinstance(icons,dict) or set(icons)!=set(LIBRARIES):
        raise ValueError(f'{native_id}: the CSLModernMap icon library is required')
    for library,filename in icons.items():
        if not isinstance(filename,str) or (filename and filename not in available[library]):
            raise ValueError(f'{native_id}: unknown image {library}/{filename}')
    if type(value.get('display')) is not bool:
        raise ValueError(f'{native_id}: display must be a boolean')
    names=value.get('names',{})
    if not isinstance(names,dict) or not all(isinstance(k,str) and isinstance(v,str) for k,v in names.items()):
        raise ValueError(f'{native_id}: invalid official names')
    return {**value,'names':dict(names),'icons':dict(icons)}

def _read(path: Path, available: dict[str,dict], defaults=None) -> tuple[dict,dict]:
    raw=json.loads(path.read_text('utf-8'))
    if not isinstance(raw,dict) or raw.get('schema_version') not in (2, SCHEMA_VERSION) or not isinstance(raw.get('entries'),list):
        raise ValueError(f'Unsupported direct POI mapping: {path}')
    entries={}
    for item in raw['entries']:
        if raw['schema_version'] == 2:
            item = deepcopy(item)
            icons = item.get('icons')
            if not isinstance(icons, dict) or set(icons) != {'cn_icons', 'poi_icons'}:
                raise ValueError(f'Invalid legacy POI icon libraries: {path}')
            filename = icons['cn_icons']
            if isinstance(filename, str) and filename and filename not in available['cn_icons']:
                key = entry_key(item.get('game'), item.get('id'))
                default = (defaults or {}).get(key)
                filename = default['icons']['cn_icons'] if default else 'mi-default-gray.png'
                warnings.warn(f'{key}: retired POI icon replaced with {filename}', UserWarning)
            item['icons'] = {'cn_icons': filename}
        value=validate_entry(item,available);key=entry_key(value['game'],value['id'])
        if key in entries:raise ValueError(f'Duplicate direct POI mapping key: {key}')
        entries[key]=value
    return raw,entries

@lru_cache(maxsize=8)
def _load(base: str, override: str, base_time: int, user_time: int) -> dict:
    available={library:icon_files(library) for library in LIBRARIES}
    raw,entries=_read(Path(base),available)
    if user_time:
        _,custom=_read(Path(override),available,entries)
        entries.update(custom)
    return entries

def load_mapping(path: str|Path|None=None, *, defaults: bool=False) -> dict:
    base=bundled_path();custom=Path(path) if path is not None else user_path()
    # 不读取旧版 YAML，即使旧主题仍保存着旧路径。
    if custom.suffix.lower()!='.json':custom=custom.with_name('poi_direct_mapping.json')
    mtime=custom.stat().st_mtime_ns if not defaults and custom.is_file() else 0
    return _load(str(base),str(custom),base.stat().st_mtime_ns,mtime)

def clear_cache() -> None:
    _load.cache_clear()

def image_for(key: str, library: str, *, path=None) -> str:
    entry=load_mapping(path).get(key)
    return entry['icons'][library] if entry and entry['display'] else ''

def save_mapping(entries: dict, path: str|Path|None=None) -> None:
    available={library:icon_files(library) for library in LIBRARIES}
    values=[]
    for key,item in entries.items():
        value=validate_entry(item,available)
        if key!=entry_key(value['game'],value['id']):raise ValueError('Direct POI mapping key does not match entry')
        values.append(value)
    target=Path(path) if path is not None else user_path();target.parent.mkdir(parents=True,exist_ok=True)
    descriptor,temporary=tempfile.mkstemp(prefix='.'+target.name+'.',suffix='.tmp',dir=target.parent)
    try:
        with os.fdopen(descriptor,'w',encoding='utf-8') as stream:
            json.dump({'schema_version':SCHEMA_VERSION,'entries':values},stream,ensure_ascii=False,indent=2)
            stream.write('\n');stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    clear_cache()
