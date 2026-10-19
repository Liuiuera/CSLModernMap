"""Generate approved gradient POI artwork from a pinned Map-Icons font."""
from __future__ import annotations

import argparse
import colorsys
import hashlib
import html
import json
from pathlib import Path
import re
import shutil

from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont
from fontTools.ttLib import TTFont
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen

ROOT = Path(__file__).resolve().parents[2]
CATALOG = Path(__file__).with_name('map_icons_catalog.json')
DARK_RING = '#737780'
DARK_MIX = '#20242B'
DARK_WEIGHT = 0.30


def darken(rgb):
    return tuple(round(c*(1-DARK_WEIGHT)+shade*DARK_WEIGHT)
                 for c, shade in zip(rgb, ImageColor.getrgb(DARK_MIX)))


def preview_colors(appearance):
    if appearance == 'dark':
        return ('#171A20', '#E5E7EB', '#A6ADBA', '#22262E', '#404650', '#1B1F26')
    return ('#F2F5F8', '#253449', '#66758A', 'white', '#DCE3EB', '#E4E9EE')

# Project silhouettes fill missing categories and replace unsuitable font
# symbols. Coordinates share the 96 px canvas; zero-fill shapes cut out details.
# Traffic lights retain a transparent placeholder without changing mappings.
CUSTOM_GLYPHS = {
    'traffic-light': [],
    'park': [
        ('rect', (43, 46, 53, 76), 255),
        ('ellipse', (31, 22, 61, 53), 255),
        ('ellipse', (21, 35, 52, 64), 255),
        ('ellipse', (43, 33, 75, 64), 255),
        ('rect', (34, 74, 62, 78), 255)],
    'school': [
        ('rect', (47, 17, 50, 29), 255),
        ('polygon', [(50, 17), (62, 21), (50, 25)], 255),
        ('polygon', [(24, 44), (48, 25), (72, 44)], 255),
        ('rect', (28, 43, 68, 73), 255),
        ('rect', (21, 51, 29, 73), 255),
        ('rect', (67, 51, 75, 73), 255),
        ('ellipse', (43, 33, 53, 43), 0),
        ('rect', (47, 34, 49, 39), 255),
        ('rect', (48, 38, 52, 40), 255),
        *[('rect', (x, y, x+6, y+6), 0)
          for x in (33, 57) for y in (49, 61)],
        ('rect', (44, 57, 52, 73), 0)],
    'university': [
        ('polygon', [(20, 39), (48, 25), (76, 39), (48, 53)], 255),
        ('polygon', [(30, 48), (48, 57), (66, 48),
                     (66, 64), (48, 72), (30, 64)], 255),
        ('rect', (73, 39, 76, 65), 255),
        ('polygon', [(74, 60), (79, 69), (69, 69)], 255)],
    'residential-low': [
        ('polygon', [(22, 44), (48, 23), (74, 44)], 255),
        ('rect', (31, 42, 65, 72), 255),
        ('rect', (45, 55, 53, 72), 0),
        ('rect', (36, 49, 41, 55), 0),
        ('rect', (57, 49, 61, 55), 0)],
    'residential-high': [
        ('rect', (23, 38, 45, 72), 255),
        ('rect', (45, 24, 73, 72), 255),
        *[('rect', (x, y, x+4, y+5), 0)
          for x in (28, 36) for y in (44, 54, 64)],
        *[('rect', (x, y, x+5, y+5), 0)
          for x in (50, 61) for y in (30, 41, 52, 63)]],
    'office': [
        ('rect', (37, 25, 59, 38), 255),
        ('rect', (42, 30, 54, 36), 0),
        ('rect', (22, 35, 74, 70), 255),
        ('rect', (22, 48, 74, 51), 0),
        ('rect', (44, 46, 52, 54), 255)],
    'industrial': [
        ('polygon', [(22, 72), (22, 45), (38, 54), (38, 44),
                     (55, 53), (55, 39), (65, 39), (65, 25),
                     (73, 25), (73, 72)], 255),
        *[('rect', (x, 61, x+6, 67), 0) for x in (28, 42, 57)]],
}


class Artwork:
    def __init__(self, source: Path):
        font_path = source / 'map-icons.ttf'
        css = (source / 'map-icons.css').read_text('utf-8')
        self.codes = dict(re.findall(r'\.map-icon-([\w-]+):before\s*\{\s*content:\s*"\\([0-9a-f]+)"', css))
        self.font = TTFont(font_path)
        self.glyphs = self.font.getGlyphSet()
        self.cmap = self.font.getBestCmap()
        self.raster_font = ImageFont.truetype(str(font_path), 512)
        self.hashes = {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
                       for name in ('map-icons.ttf', 'map-icons.css')}

    def render(self, key, color, name, appearance='light'):
        if key == 'traffic-light':
            image = Image.new('RGBA', (768, 768))
            svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96" viewBox="0 0 96 96"><title>{html.escape(name)}</title></svg>\n'
            return image, svg, None, None
        image = Image.new('RGBA', (768, 768))
        ImageDraw.Draw(image).ellipse((16, 22, 752, 758), fill=(15, 23, 42, 40))
        image = image.filter(ImageFilter.GaussianBlur(10))
        ImageDraw.Draw(image).ellipse((16, 16, 752, 752), fill=DARK_RING if appearance == 'dark' else 'white')
        hue, lightness, saturation = colorsys.rgb_to_hls(*(c/255 for c in ImageColor.getrgb(color)))
        top = tuple(round(c*255) for c in colorsys.hls_to_rgb(hue, min(1, lightness+0.09), saturation))
        bottom = tuple(round(c*255) for c in colorsys.hls_to_rgb(hue, max(0, lightness-0.10), saturation))
        if key == 'default-gray':
            top, bottom = (165, 173, 195), (111, 122, 152)
        if appearance == 'dark':
            top, bottom = darken(top), darken(bottom)
        gradient = Image.new('RGBA', image.size)
        rows = ImageDraw.Draw(gradient)
        for y in range(768):
            amount = max(0.0, min(1.0, (y-48)/672))
            row = tuple(round(a+(b-a)*amount) for a, b in zip(top, bottom))
            rows.line((0, y, 767, y), fill=(*row, 255))
        circle = Image.new('L', image.size)
        ImageDraw.Draw(circle).ellipse((48, 48, 720, 720), fill=255)
        image.paste(gradient, (0, 0), circle)
        if key == 'default-gray':
            ImageDraw.Draw(image).ellipse((288, 288, 480, 480), fill='white')
            path = '<circle cx="48" cy="48" r="12" fill="#fff"/>'
        elif key in CUSTOM_GLYPHS:
            mask = Image.new('L', image.size)
            pen = ImageDraw.Draw(mask)
            shapes = []
            for kind, coordinates, fill in CUSTOM_GLYPHS[key]:
                if kind == 'polygon':
                    pen.polygon([(x*8, y*8) for x, y in coordinates], fill=fill)
                    points = ' '.join(f'{x},{y}' for x, y in coordinates)
                    shapes.append(f'<polygon points="{points}" fill="rgb({fill},{fill},{fill})"/>')
                elif kind == 'ellipse':
                    x0, y0, x1, y1 = coordinates
                    pen.ellipse((x0*8, y0*8, x1*8-1, y1*8-1), fill=fill)
                    shapes.append(f'<ellipse cx="{(x0+x1)/2:g}" cy="{(y0+y1)/2:g}" rx="{(x1-x0)/2:g}" ry="{(y1-y0)/2:g}" fill="rgb({fill},{fill},{fill})"/>')
                else:
                    x0, y0, x1, y1 = coordinates
                    pen.rectangle((x0*8, y0*8, x1*8-1, y1*8-1), fill=fill)
                    shapes.append(f'<rect x="{x0}" y="{y0}" width="{x1-x0}" height="{y1-y0}" fill="rgb({fill},{fill},{fill})"/>')
            image.paste('white', (0, 0), mask)
            path = '<defs><mask id="glyph">'+''.join(shapes)+'</mask></defs><rect width="96" height="96" fill="#fff" mask="url(#glyph)"/>'
        else:
            code = int(self.codes[key], 16)
            glyph = self.glyphs[self.cmap[code]]
            bounds = BoundsPen(self.glyphs)
            glyph.draw(bounds)
            x0, y0, x1, y1 = bounds.bounds
            pen = SVGPathPen(self.glyphs)
            glyph.draw(pen)
            scale = min(52/(x1-x0), 52/(y1-y0))
            path = f'<path fill="#fff" d="{pen.getCommands()}" transform="translate(48 48) scale({scale:.8f} {-scale:.8f}) translate({-(x0+x1)/2:g} {-(y0+y1)/2:g})"/>'
            char = chr(code)
            box = self.raster_font.getbbox(char)
            mask = Image.new('L', (box[2]-box[0], box[3]-box[1]))
            ImageDraw.Draw(mask).text((-box[0], -box[1]), char, font=self.raster_font, fill=255)
            mask = mask.crop(mask.getbbox())
            mask.thumbnail((416, 416), Image.Resampling.LANCZOS)
            image.paste('white', ((768-mask.width)//2, (768-mask.height)//2), mask)
        top_hex, bottom_hex = ('#'+''.join(f'{c:02x}' for c in rgb) for rgb in (top, bottom))
        ring = DARK_RING if appearance == 'dark' else '#fff'
        svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96" viewBox="0 0 96 96"><title>{html.escape(name)}</title><defs><linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{top_hex}"/><stop offset="1" stop-color="{bottom_hex}"/></linearGradient><filter id="shadow" x="-20%" y="-20%" width="140%" height="140%"><feDropShadow dx="0" dy="0.75" stdDeviation="1.25" flood-color="#0f172a" flood-opacity="0.16"/></filter></defs><circle cx="48" cy="48" r="46" fill="{ring}" filter="url(#shadow)"/><circle cx="48" cy="48" r="42" fill="url(#bg)"/>{path}</svg>\n'''
        return image, svg, top_hex, bottom_hex


def check_catalog(catalog, codes):
    chosen = [row[0] for batch in catalog['batches'] for row in batch['icons']]
    if len(chosen) != len(set(chosen)):
        raise ValueError('Duplicate artwork key')
    declared = set(chosen) - {'default-gray'} - (set(CUSTOM_GLYPHS) - set(codes))
    excluded = set(catalog['excluded'])
    if declared & excluded or declared | excluded != set(codes):
        raise ValueError('Every upstream glyph must be included or explicitly excluded')


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def contact_sheet(batch, records, output, font_path, appearance='light'):
    background, text, muted, card, border, small_background = preview_colors(appearance)
    columns, width, height = 6, 260, 215
    rows = (len(records)+columns-1)//columns
    image = Image.new('RGB', (columns*width+40, rows*height+130), background)
    draw = ImageDraw.Draw(image)
    def font(size):
        return ImageFont.truetype(str(font_path), size)
    draw.text((24, 18), batch['name']+f' · {len(records)} 枚', font=font(27), fill=text)
    subtitle = ('Map-Icons · 深色渐变 / 灰色外圈 / 白色符号 · 大图 96 px，下排 24 / 32 px'
                if appearance == 'dark' else
                'Map-Icons · 灰圈参考渐变 / 白色符号 / 柔和阴影 · 大图 96 px，下排 24 / 32 px')
    draw.text((24, 60), subtitle, font=font(15), fill=muted)
    for i, record in enumerate(records):
        x, y = 20+(i%columns)*width, 100+(i//columns)*height
        draw.rounded_rectangle((x, y, x+width-12, y+height-12), radius=10, fill=card, outline=border)
        icon = Image.open(output / 'png-96' / record['file'])
        image.paste(icon, (x+76, y+8), icon)
        draw.text((x+12, y+110), record['name'], font=font(17), fill=text)
        draw.text((x+12, y+139), record['upstream_id'], font=font(12), fill=muted)
        draw.rounded_rectangle((x+12, y+160, x+width-24, y+height-24), radius=5, fill=small_background)
        for j, size in enumerate((24, 32)):
            small = Image.open(output / f'png-{size}' / record['file'])
            image.paste(small, (x+76+j*64-size//2, y+161+(32-size)//2), small)
    image.save(output / f'{batch["id"]}.png')


def gallery(catalog, records, output, appearance='light'):
    sections = []
    for batch in catalog['batches']:
        cards = []
        for row in records:
            if row['batch'] != batch['id']:
                continue
            key, filename = row['upstream_id'], row['file']
            cards.append(f'<article><img src="png-96/{filename}" width="96" height="96" alt="{row["name"]}"><h3>{row["name"]}</h3><code>{key}</code><div class="small"><img src="png-24/{filename}" width="24" height="24" alt="24像素"><img src="png-32/{filename}" width="32" height="32" alt="32像素"></div><a href="svg/{key}.svg">SVG</a> · <a href="png-96/{filename}">PNG</a></article>')
        sections.append(f'<section id="{batch["id"]}"><h2>{batch["name"]} · {len(cards)} 枚</h2><div class="grid">'+''.join(cards)+'</div></section>')
    nav = ' · '.join(f'<a href="#{b["id"]}">{b["name"]}</a>' for b in catalog['batches'])
    page = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>新 POI 图库</title><style>body{{font:15px system-ui,"Microsoft YaHei",sans-serif;color:#253449;background:#f2f5f8;margin:28px}}h1{{font-size:28px}}p{{line-height:1.8}}nav{{line-height:2.2}}a{{color:#245dc0}}section{{margin-top:35px}}.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:14px}}article{{text-align:center;border:1px solid #dce3eb;background:white;border-radius:10px;padding:16px}}h3{{font-size:16px}}code{{font-size:11px;color:#66758a;overflow-wrap:anywhere}}.small{{height:48px;display:flex;align-items:center;justify-content:center;gap:28px;background:#e4e9ee;border-radius:6px;margin:12px 0}}footer{{margin:30px 0;color:#66758a}}</style><h1>新 POI 图库 · {len(records)} 枚</h1><p>按当前统一样式分 {len(catalog["batches"])} 批绘制；图标加入 cn_icons 时使用 mi- 前缀。绘制完成不代表语义映射已确认，默认灰圈保持独立。</p><nav>{nav}</nav>{''.join(sections)}<footer>Scott de Jonge / Map Icons · 固定提交 {catalog['upstream_commit']} · 字体字形：上游声明 SIL OFL 1.1 · <a href="THIRD-PARTY.md">来源说明</a></footer></html>'''
    if appearance == 'dark':
        background, text, muted, card, border, small_background = preview_colors(appearance)
        for old, new in (('#253449', text), ('#f2f5f8', background),
                         ('#66758a', muted), ('#dce3eb', border),
                         ('background:white', 'background:'+card),
                         ('#e4e9ee', small_background), ('#245dc0', '#9ABEFF')):
            page = page.replace(old, new)
        page = page.replace('新 POI 图库', '深色 POI 图库')
        page = page.replace('图标加入 cn_icons 时使用 mi- 前缀。',
                            '与现有图标逐一对应，保留 mi- 文件名；本套仅供资源预览，尚未接入主题。')
    (output / 'index.html').write_text(page, encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--label-font', type=Path, required=True)
    parser.add_argument('--appearance', choices=('light', 'dark'), default='light')
    parser.add_argument('--install', action='store_true', help='Install the shared POI library without changing POI mappings')
    args = parser.parse_args()
    if args.appearance == 'dark' and args.install:
        parser.error('--install is only supported for light artwork')
    catalog = json.loads(CATALOG.read_text('utf-8'))
    source_commit = json.loads((args.source / 'commit.json').read_text('utf-8'))['sha']
    if source_commit != catalog['upstream_commit']:
        raise ValueError('Source commit does not match the reviewed upstream version')
    artwork = Artwork(args.source)
    check_catalog(catalog, artwork.codes)
    output = args.output.resolve()
    for directory in ('svg', 'png-96', 'png-48', 'png-32', 'png-24'):
        (output / directory).mkdir(parents=True, exist_ok=True)
    records = []
    for batch in catalog['batches']:
        batch_records = []
        for item in batch['icons']:
            key, name = item[:2]
            color = item[2] if len(item) == 3 else batch['color']
            image, svg, top, bottom = artwork.render(key, color, name, args.appearance)
            filename = f'mi-{key}.png'
            (output / 'svg' / f'{key}.svg').write_text(svg, encoding='utf-8')
            for size in (24, 32, 48, 96):
                image.resize((size, size), Image.Resampling.LANCZOS).save(output / f'png-{size}' / filename)
            record = {'id': 'mi_'+key.replace('-', '_'), 'name': name+('（自绘）' if key in CUSTOM_GLYPHS else '（Map Icons）'),
                      'category': batch['name'], 'file': filename, 'upstream_id': key,
                      'batch': batch['id'], 'background': color, 'gradient_top': top, 'gradient_bottom': bottom,
                      'origin': 'project-custom' if key in CUSTOM_GLYPHS else ('project-default' if key == 'default-gray' else 'Map Icons')}
            if key == 'traffic-light':
                record['borderless'] = True
            records.append(record)
            batch_records.append({**record, 'name': name})
        contact_sheet(batch, batch_records, output, args.label_font, args.appearance)
        print(f'{batch["id"]}: {len(batch_records)} done', flush=True)
    manifest = {'upstream_commit': catalog['upstream_commit'], 'palette': catalog.get('palette'),
        'source_hashes': artwork.hashes, 'custom_glyphs': CUSTOM_GLYPHS, 'icons': records, 'excluded': catalog['excluded']}
    if args.appearance == 'dark':
        manifest['appearance'] = {'mode': 'dark', 'ring': DARK_RING,
                                  'mix_color': DARK_MIX, 'mix_weight': DARK_WEIGHT,
                                  'mix_space': 'RGB', 'glyph': '#FFFFFF'}
    write_json(output / 'catalog.json', manifest)
    gallery(catalog, records, output, args.appearance)
    for filename, target in [('README.md', 'README-upstream.md'), ('LICENSE', 'LICENSE-upstream-MIT.txt')]:
        shutil.copyfile(args.source / filename, output / target)
    shutil.copyfile(Path(__file__).with_name('map_icons_NOTICE.md'), output / 'THIRD-PARTY.md')
    if args.install:
        directory = ROOT / 'assets/cn_icons'
        manifest_path = directory / 'icons_map.json'
        manifest = json.loads(manifest_path.read_text('utf-8'))
        old = {item['id']: item for item in manifest['icons'] if item['file'].startswith('mi-')}
        for record in records:
            if record['id'] in old and old[record['id']]['file'] != record['file']:
                raise ValueError(f'Existing icon ID conflict: {record["id"]}')
        for record in records:
            shutil.copyfile(output / 'png-96' / record['file'], directory / record['file'])
            old[record['id']] = {key: record[key] for key in ('id', 'name', 'category', 'file', 'borderless') if key in record}
        manifest['icons'] = list(old.values())
        manifest['categories'] = list(dict.fromkeys([*manifest.get('categories', []), *(b['name'] for b in catalog['batches'])]))
        write_json(manifest_path, manifest)
        attribution = ROOT / 'assets/map_icons_source'
        attribution.mkdir(exist_ok=True)
        for filename in ('catalog.json', 'THIRD-PARTY.md', 'README-upstream.md', 'LICENSE-upstream-MIT.txt'):
            shutil.copyfile(output / filename, attribution / filename)
        print(f'Installed {len(records)} shared POI PNGs; mappings preserved.', flush=True)


if __name__ == '__main__':
    main()
