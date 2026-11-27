#!/usr/bin/env python3
"""Audit every bundled Mach-O slice and dylib dependency before distribution."""
import argparse
import concurrent.futures
import json
from pathlib import Path
import re
import subprocess
p = argparse.ArgumentParser()
p.add_argument('bundle', type=Path)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
magic = {b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf'}
paths = []
for f in a.bundle.rglob('*'):
    if f.is_file() and not f.is_symlink():
        with f.open('rb') as s:
            if s.read(4) in magic:
                paths.append(f)

def inspect(f):
    arch = subprocess.check_output(['lipo', '-archs', str(f)], text=True).strip()
    loads = subprocess.check_output(['otool', '-arch', 'x86_64', '-l', str(f)], text=True)
    versions = re.findall(r'\bminos (\d+(?:\.\d+)*)', loads)
    versions += re.findall(r'cmd LC_VERSION_MIN_MACOSX\s+cmdsize \d+\s+version (\d+(?:\.\d+)*)', loads)
    deps = subprocess.check_output(['otool', '-arch', 'x86_64', '-L', str(f)], text=True).splitlines()[1:]
    deps = [line.strip().split(' (')[0] for line in deps]
    ids = subprocess.check_output(['otool', '-arch', 'x86_64', '-D', str(f)], text=True).splitlines()[1:]
    deps = [x for x in deps if x not in ids]
    outside = [x for x in deps if x.startswith('/') and not x.startswith(('/usr/lib/', '/System/Library/'))]
    errors = []
    if 'x86_64' not in arch.split(): errors.append('No Intel slice')
    if any(tuple(map(int, v.split('.'))) > (13, 0, 0) for v in versions): errors.append('Minimum OS exceeds macOS 13.0')
    if outside: errors.append('External dependencies: ' + repr(outside))
    return {'path': str(f.relative_to(a.bundle)), 'architectures': arch, 'minimumOS': versions, 'dependencies': deps, 'errors': errors}
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    results = list(pool.map(inspect, paths))
report = {'success': all(not x['errors'] for x in results), 'files': results}
a.output.write_text(json.dumps(report, indent=2))
print(json.dumps({'success': report['success'], 'binaries': len(results), 'failures': [x for x in results if x['errors']]}, indent=2))
raise SystemExit(0 if report['success'] else 1)
