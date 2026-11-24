"""Package the signed native app using the established Finder installer layout."""
import argparse
import hashlib
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import time

from ds_store import DSStore
from mac_alias import Alias
from PIL import Image, ImageDraw, ImageFont


def run(*args):
    return subprocess.run(args, check=True, stdout=subprocess.PIPE).stdout


def attach(image, mount_root, access):
    mounted = plistlib.loads(run('hdiutil', 'attach', access, '-nobrowse',
                                '-noautoopen', '-plist', '-mountroot', str(mount_root), str(image)))
    return Path(next(e['mount-point'] for e in mounted['system-entities'] if 'mount-point' in e))


def make_dmg(app, output, version):
    app, output = app.resolve(), output.resolve()
    if app.name != 'CSLModernMap Renderer.app':
        raise ValueError('Expected CSLModernMap Renderer.app')
    run('codesign', '--verify', '--deep', '--strict', str(app))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.dmg-layout-', dir=output.parent) as work:
        work = Path(work)
        stage = work / 'stage'
        stage.mkdir()
        run('ditto', str(app), str(stage / app.name))
        (stage / 'Applications').symlink_to('/Applications')
        image = Image.open(Path(__file__).with_name('dmg-background.png')).convert('RGB')
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype('/System/Library/Fonts/Helvetica.ttc', 12)
        draw.text((330, 60), f'v{version} · macOS Intel (x86_64)',
                  font=font, fill='#7f8da5', anchor='mm')
        image.save(stage / '.background.png')
        writable = work / 'layout.dmg'
        for attempt in range(3):
            try:
                run('hdiutil', 'create', '-volname', 'CSLModernMap Renderer',
                    '-fs', 'HFS+', '-srcfolder', str(stage), '-format', 'UDRW',
                    '-ov', str(writable))
                break
            except subprocess.CalledProcessError:
                if attempt == 2:
                    raise
                time.sleep(3 * (attempt + 1))
        mount_root = work / 'mounts'
        mount_root.mkdir()
        mount = attach(writable, mount_root, '-readwrite')
        try:
            with DSStore.open(str(mount / '.DS_Store'), 'w+') as store:
                store['.']['bwsp'] = {
                    'ContainerShowSidebar': False, 'PreviewPaneVisibility': False,
                    'ShowPathbar': False, 'ShowSidebar': False, 'ShowStatusBar': False,
                    'ShowTabView': False, 'ShowToolbar': False, 'SidebarWidth': 180,
                    'WindowBounds': '{{120, 120}, {660, 480}}',
                }
                store['.']['icvl'] = ('type', b'icnv')
                store['.']['icvp'] = {
                    'arrangeBy': 'none', 'backgroundColorBlue': 1.0,
                    'backgroundColorGreen': 1.0, 'backgroundColorRed': 1.0,
                    'backgroundImageAlias': Alias.for_file(str(mount / '.background.png')).to_bytes(),
                    'backgroundType': 2, 'gridOffsetX': 0.0, 'gridOffsetY': 0.0,
                    'gridSpacing': 100.0, 'iconSize': 96.0, 'labelOnBottom': True,
                    'scrollPositionX': 0.0, 'scrollPositionY': 0.0,
                    'showIconPreview': False, 'showItemInfo': False,
                    'textSize': 13.0, 'viewOptionsVersion': 1,
                }
                store['.']['vSrn'] = ('long', 1)
                store[app.name]['Iloc'] = (170, 158)
                store['Applications']['Iloc'] = (490, 158)
            run('sync')
            run('bless', '--folder', str(mount), '--openfolder', str(mount))
        finally:
            run('hdiutil', 'detach', str(mount))

        candidate = work / 'candidate.dmg'
        run('hdiutil', 'convert', str(writable), '-format', 'ULMO', '-o', str(candidate))
        run('codesign', '--force', '--sign', '-', str(candidate))
        size = candidate.stat().st_size
        if size > 100_000_000:
            raise ValueError(f'DMG is {size} bytes; release limit is 100,000,000 bytes')
        run('codesign', '--verify', str(candidate))
        run('hdiutil', 'verify', str(candidate))
        mount = attach(candidate, mount_root, '-readonly')
        try:
            if (not (mount / app.name).is_dir() or
                    os.readlink(mount / 'Applications') != '/Applications' or
                    not (mount / '.background.png').is_file()):
                raise ValueError('DMG contents or installation background are missing.')
            with DSStore.open(str(mount / '.DS_Store'), 'r') as store:
                options = store['.']['icvp']
                if (options['backgroundType'] != 2 or options['iconSize'] != 96.0 or
                        not options['backgroundImageAlias'] or
                        store[app.name]['Iloc'] != (170, 158) or
                        store['Applications']['Iloc'] != (490, 158)):
                    raise ValueError('Invalid Finder installer layout.')
            run('codesign', '--verify', '--deep', '--strict', str(mount / app.name))
        finally:
            run('hdiutil', 'detach', str(mount))
        sha = hashlib.sha256(candidate.read_bytes()).hexdigest()
        os.replace(candidate, output)
        output.with_suffix('.dmg.sha256').write_text(f'{sha}  {output.name}\n')
        print(f'{output}\n{size} bytes\nSHA256 {sha}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--version', required=True)
    args = parser.parse_args()
    make_dmg(args.app, args.output, args.version)
