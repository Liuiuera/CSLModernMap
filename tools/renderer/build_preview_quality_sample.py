"""Crop an existing production scene into the bundled, offline quality demonstration."""
import argparse
import base64
import io
import json
from pathlib import Path

from PIL import Image
from csmodernmap.export_clip import clip_scene_payload
from csmodernmap._paths import resource_dir
from csmodernmap.preview_scene import preview_theme
from csmodernmap.style import load_style


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('scene', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--bounds', type=float, nargs=4, required=True)
    args = parser.parse_args()
    manifest = json.loads((args.scene/'manifest.json').read_text(encoding='utf-8'))
    keys = {'roads', 'buildings', 'labels', 'railway', 'water', 'zones', 'pois'}
    manifest['sources'] = {key: url for key, url in manifest['sources'].items() if key in keys}
    manifest['sourceRevisions'] = {key: 1 for key in manifest['sources']}
    manifest['sceneId'] = 'quality-demo-quandu-cloverleaf'
    manifest['revision'] = 1
    manifest['layers'] = {key: True for key in ['road', 'building', 'label', 'railway', 'water', 'zone', 'poi']}
    manifest['renderStack'] = ['water', 'zone', 'building', 'road', 'railway', 'label', 'poi']
    manifest['theme'] = preview_theme(load_style(str(resource_dir('styles')/'modern.yaml')), {})
    for key in ['surfaceFields', 'terrainCorners', 'satelliteCorners', 'satelliteSize', 'fonts', 'navigation', 'navigationRoute', 'view']:
        manifest.pop(key, None)
    manifest['rotation'] = 0
    manifest['satelliteAvailable'] = False
    sources = {url.split('?')[0]: json.loads((args.scene/url.split('?')[0]).read_text(encoding='utf-8'))
        for url in manifest['sources'].values()}
    # The small demo only needs the POIs that appear at its overview and street scales.
    pois = manifest['sources']['pois'].split('?')[0]
    sources[pois]['features'] = [f for f in sources[pois]['features']
        if any(z <= 16 for z in f['properties']['visibleZooms'])]
    payload = {'manifest': manifest, 'embedded': {'sources': sources, 'images': {}, 'poiAtlasIndex': {}}}
    clipped = clip_scene_payload(payload, args.bounds)
    atlas = manifest['poiAtlas']
    index = json.loads((args.scene/atlas['index']).read_text(encoding='utf-8'))
    used = sorted({f['properties']['iconKey'] for f in clipped['embedded']['sources'][pois]['features']} & index.keys())
    entries = {key: index[key] for key in used}
    sprite = Image.new('RGBA', (max(1, sum(e['width'] for e in entries.values())),
        max((e['height'] for e in entries.values()), default=1)))
    with Image.open(args.scene/atlas['image']) as original:
        left = 0
        for entry in entries.values():
            x, y, w, h = (entry[k] for k in ('x', 'y', 'width', 'height'))
            sprite.paste(original.crop((x, y, x+w, y+h)), (left, 0))
            entry.update(x=left, y=0)
            left += w
    buffer = io.BytesIO()
    sprite.save(buffer, format='PNG')
    clipped['embedded']['images'][atlas['image']] = 'data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode()
    clipped['embedded']['poiAtlasIndex'][atlas['index']] = entries
    # Only the standard EmbeddedPayload contract is needed at runtime.
    result = {key: clipped[key] for key in ['manifest', 'embedded']}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    print(json.dumps({'bytes': args.output.stat().st_size,
        'counts': {key: len(source['features']) for key, source in result['embedded']['sources'].items()}}, indent=2))


if __name__ == '__main__':
    main()
