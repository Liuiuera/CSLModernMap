#!/usr/bin/env python3
"""Build a relocatable Intel test bundle without changing the Qt environment."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import plistlib
import shutil
import subprocess

source = Path(__file__).resolve().parents[1]
repo = source.parents[1]
workspace = Path(os.environ.get('CSLMM_WORKSPACE_DIR', repo)).resolve()
parser = argparse.ArgumentParser()
parser.add_argument('--runtime', type=Path, required=True)
parser.add_argument('--site-packages', type=Path, required=True)
parser.add_argument('--skip-native-build', action='store_true')
parser.add_argument('--app-dir', type=Path, help='Output bundle; defaults to the integration candidate')
args = parser.parse_args()
viewer_assets = repo / 'dev/CSLModernMapRenderer/viewer/dist/assets'
if any(b'__CSL_NATIVE_CAMERA__' in path.read_bytes() for path in viewer_assets.glob('*.js')):
    parser.error('Rebuild the viewer without VITE_CSLMM_NATIVE_CHECKS before packaging.')
app = (args.app_dir or workspace / 'outputs/native-integration/CSLModernMap Renderer.app').resolve()
app.parent.mkdir(parents=True, exist_ok=True)
if args.skip_native_build:
    info = app / 'Contents/Info.plist'
    if not info.is_file() or plistlib.loads(info.read_bytes()).get('CMMIntegrationChecks') is not False:
        parser.error('Run without --skip-native-build to compile the application without integration checks.')
else:
    subprocess.run(['bash', str(source / 'scripts/build-dev.sh')], check=True, env={**os.environ, 'CSLMM_NATIVE_APP_DIR': str(app)})
resources = app / 'Contents/Resources'
engine = resources / 'engine'
engine.mkdir(exist_ok=True)

def copy_tree(src, dest):
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, symlinks=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store'))

copy_tree(args.runtime.resolve(), engine / 'python')
packages = engine / 'python/lib/python3.13/site-packages'
packages.mkdir(parents=True, exist_ok=True)
# Include distribution license/metadata alongside every dependency.
excluded = {'nuitka', '_pytest', 'pytest', 'pygments', 'iniconfig', 'pluggy', 'ordered_set', 'zstandard', 'playwright', 'greenlet', 'pyee'}
for item in args.site_packages.resolve().iterdir():
    key = item.name.lower().split('-')[0].split('.')[0]
    if key in excluded or item.name == '__pycache__':
        continue
    dest = packages / item.name
    if item.is_dir():
        copy_tree(item, dest)
    else:
        shutil.copy2(item, dest)
# The engine uses only QtCore/QtGui geometry, never database drivers.
shutil.rmtree(packages / 'PySide6/Qt/plugins/sqldrivers', ignore_errors=True)
renderer = engine / 'project/dev/CSLModernMapRenderer' 
renderer.mkdir(parents=True, exist_ok=True)
for name in ('csmodernmap', 'styles', 'resources'):
    copy_tree(repo / 'dev/CSLModernMapRenderer' / name, renderer / name)
copy_tree(repo / 'dev/CSLModernMapRenderer/viewer/dist', renderer / 'viewer/dist')
copy_tree(repo / 'dev/third_party/rmp/dist-embed', renderer / 'viewer/dist/rmp')
copy_tree(repo / 'assets/cn_icons', engine / 'project/assets/cn_icons')
shutil.copy2(repo / 'dev/CSLModernMapRenderer/main.py', renderer / 'main.py')
shutil.copy2(repo / 'version.json', engine / 'project/version.json')
launcher = engine / 'run-engine'
launcher.write_text('''#!/bin/bash
set -euo pipefail
ENGINE_DIR="$(cd "$(dirname "$0")" && pwd)"
export PYTHONNOUSERSITE=1
export PYTHONDONTWRITEBYTECODE=1
unset PYTHONPATH PYTHONHOME
exec "$ENGINE_DIR/python/bin/python3.13" "$ENGINE_DIR/project/dev/CSLModernMapRenderer/main.py" engine
''')
launcher.chmod(0o755)
license_dir = resources / 'Licenses'
license_dir.mkdir(exist_ok=True)
shutil.copy2(repo / 'LICENSE', license_dir / 'CSLModernMap-LICENSE')
shutil.copy2(repo / 'dev/third_party/rmp/COPYING', license_dir / 'RMP-GPL-3.0')
# Preserve notices of all bundled JS dependencies (including fonts and polyfills).
for project in (repo / 'dev/CSLModernMapRenderer/viewer', repo / 'dev/third_party/rmp'):
    for root, dirs, files in os.walk(project / 'node_modules'):
        dirs[:] = [d for d in dirs if d not in ('.bin', '.cache', '.vite')]
        for name in files:
            if name.lower().startswith(('license', 'copying', 'notice', 'ofl')):
                src = Path(root) / name
                dest = license_dir / project.name / src.relative_to(project / 'node_modules')
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
version = json.loads((repo / 'version.json').read_text())
plist_path = app / 'Contents/Info.plist'
plist = plistlib.loads(plist_path.read_bytes())
plist['CFBundleShortVersionString'] = version['assembly_version'].rsplit('.', 1)[0]
plist['CFBundleVersion'] = version['assembly_version']
plist['CSLModernMapVersion'] = version.get('macos_version', version['version'])
plist_path.write_bytes(plistlib.dumps(plist))
# Sign nested binaries first. Keep symlinks untouched.
macho = []
magic = {b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf'}
for path in app.rglob('*'):
    if path.is_file() and not path.is_symlink():
        with path.open('rb') as stream:
            if stream.read(4) in magic:
                macho.append(path)
for path in sorted(macho, key=lambda p: len(p.parts), reverse=True):
    subprocess.run(['codesign', '--force', '--sign', '-', str(path)], check=True, capture_output=True)
for framework in sorted(app.rglob('*.framework'), key=lambda p: len(p.parts), reverse=True):
    subprocess.run(['codesign', '--force', '--sign', '-', str(framework)], check=True, capture_output=True)
subprocess.run(['codesign', '--force', '--sign', '-', str(app)], check=True)
subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
manifest = {'version': version, 'architecture': 'x86_64', 'minimumSystem': '13.0',
            'signature': 'ad-hoc', 'files': []}
for path in sorted(app.rglob('*')):
    if path.is_file() and not path.is_symlink():
        manifest['files'].append({'path': str(path.relative_to(app)), 'size': path.stat().st_size,
                                  'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
(app.parent / 'app-build-manifest.json').write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2))
print(app)
