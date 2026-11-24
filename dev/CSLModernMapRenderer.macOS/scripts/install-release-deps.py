#!/usr/bin/env python3
"""Install the locked Intel runtime wheels for macOS 13, not the host OS."""
import argparse
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--target', required=True, type=Path)
args = parser.parse_args()
if args.target.exists() and any(args.target.iterdir()):
    parser.error('Use an empty target directory for the release dependencies.')
if platform.system() != 'Darwin' or platform.machine() != 'x86_64' or sys.version_info[:2] != (3, 13):
    parser.error('Use Intel macOS and Python 3.13 to build the release dependencies.')

development = {'nuitka', 'pytest', 'iniconfig', 'pluggy', 'pygments', 'ordered-set', 'zstandard'}
lock = Path(__file__).resolve().parents[1] / 'requirements-macos13.lock'
requirements = [line for line in lock.read_text().splitlines()
                if line and line.split('==', 1)[0] not in development]
aggdraw = next(req for req in requirements if req.startswith('aggdraw=='))
requirements.remove(aggdraw)
# The upstream aggdraw wheel requires macOS 14+. Build its geometry-only
# extension from source, without optional host FreeType, for macOS 13.
with tempfile.TemporaryDirectory(prefix='cmm-wheel-') as wheels:
    subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-deps', '--no-cache-dir',
                    '--no-binary=aggdraw', '--wheel-dir', wheels, aggdraw],
                   env={**os.environ, 'MACOSX_DEPLOYMENT_TARGET': '13.0',
                        'AGGDRAW_FREETYPE_ROOT': ''}, check=True)
    wheel = next(Path(wheels).glob('aggdraw-*.whl'))
    subprocess.run([sys.executable, '-m', 'pip', 'install',
                    '--target', str(args.target.resolve()), '--platform', 'macosx_13_0_x86_64',
                    '--implementation', 'cp', '--python-version', '3.13', '--abi', 'cp313',
                    '--only-binary=:all:', '--no-deps', str(wheel), *requirements], check=True)
