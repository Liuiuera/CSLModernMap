#!/usr/bin/env python3
"""Exercise the existing JSONL protocol against real inputs and real output files."""
import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
from urllib.parse import urlparse
import uuid

parser = argparse.ArgumentParser()
parser.add_argument('--python', type=Path, required=True)
parser.add_argument('--main', type=Path, required=True)
parser.add_argument('--city', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--extended', action='store_true')
parser.add_argument('--alternate-city', type=Path)
a = parser.parse_args()
out = a.output.resolve(); out.mkdir(parents=True, exist_ok=True)
env = os.environ | {'QT_QPA_PLATFORM': 'offscreen', 'PYTHONIOENCODING': 'utf-8',
                    'CSLMM_NATIVE_DATA_DIR': str(out / 'data')}
checks = []
report = {'success': False, 'checks': checks}

def record(name, details=True):
    checks.append({'name': name, 'result': details})
    (out / 'engine-workflows.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(name, flush=True)

class Client:
    def __init__(self):
        self.stderr = (out / 'engine-stderr.log').open('a')
        self.child = subprocess.Popen([str(a.python.absolute()), str(a.main.resolve()), 'engine'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr, env=env,
            text=True, encoding='utf-8')
        self.inbox = queue.Queue(); self.frames = []; self.serial = 0; self.epoch = 0
        def read():
            for line in self.child.stdout:
                self.inbox.put(json.loads(line))
            self.inbox.put({'eof': self.child.poll()})
        threading.Thread(target=read, daemon=True).start()
    def send(self, method, params):
        self.serial += 1
        self.child.stdin.write(json.dumps({'id': self.serial, 'method': method, 'params': params}, ensure_ascii=False) + '\n')
        self.child.stdin.flush()
        return self.serial
    def wait(self, predicate, timeout=300):
        deadline = time.monotonic() + timeout
        while True:
            for i, frame in enumerate(self.frames):
                if predicate(frame): return self.frames.pop(i)
            frame = self.inbox.get(timeout=max(.001, deadline-time.monotonic()))
            if 'eof' in frame: raise RuntimeError('Engine exited: ' + str(frame))
            if frame.get('event') == 'documentOpening': self.epoch = frame['payload']['documentEpoch']
            if frame.get('event') == 'sceneReady':
                p = frame['payload']
                self.send('previewPresented', {'bundleId': urlparse(p['manifestUrl']).path.split('/')[-2],
                    'revision': p['revision'], 'completion': p['completion'], 'performance': {}})
            self.frames.append(frame)
    def request(self, method, params=None):
        i = self.send(method, params or {})
        frame = self.wait(lambda f: f.get('id') == i)
        if 'error' in frame: raise RuntimeError(json.dumps(frame['error'], ensure_ascii=False))
        return frame['result']
    def event(self, names, task=None):
        frame = self.wait(lambda f: f.get('event') in names and (task is None or f.get('payload', {}).get('requestId') == task))
        return frame['event'], frame['payload']
    def close(self):
        self.request('close'); self.child.stdin.close(); self.child.wait(timeout=10); self.stderr.close()

c = Client()
try:
    assert c.request('hello', {'protocolVersion':1}) == {'protocolVersion':1}
    assert c.request('startPreview')['viewerUrl'].startswith('http://127.0.0.1:')
    assert c.request('setPreviewEnabled', {'enabled':True}) == {'enabled':True}
    record('explicit-preview-startup')
    try: c.request('hello', {'protocolVersion':999})
    except RuntimeError as error: record('protocol-version-error', str(error))
    else: raise AssertionError('Bad protocol version accepted')
    assert c.request('openDocument', {'path':str(a.city.resolve())})['started']
    while True:
        name, payload = c.event({'sceneReady','error','sceneError'})
        if name != 'sceneReady': raise RuntimeError(str(payload))
        if payload['completion'] == 'complete': break
    snapshot = c.request('getSnapshot'); epoch = snapshot['documentEpoch']
    record('open-real-city', {'stats':snapshot['stats'], 'source':snapshot['sourceFile'], 'epoch':epoch})
    regions = c.request('getRegionCatalog'); record('real-regions', {k:len(v) for k,v in regions.items() if isinstance(v,list)})
    lines = c.request('getTransitLines', {'metro':False})
    ids = [x['id'] for x in lines['lines']]
    for selected in [None, [], ids[:2]]:
        c.request('setTransitLines', {'metro':False, 'documentEpoch':epoch, 'lineIds':selected})
        current = c.request('getTransitLines', {'metro':False})
        expected = None if selected is None else sorted(selected)
        assert current['lineIds'] == expected, (selected, current['lineIds'])
    c.request('setTransitLines', {'metro':False, 'documentEpoch':epoch, 'lineIds':None})
    record('transit-null-empty-partial-roundtrip', len(ids))
    c.request('setField', {'key':'road.level.highway.footprint_scale', 'value':1.15, 'documentEpoch':epoch})
    assert abs(c.request('getSnapshot')['fields']['road.level.highway.footprint_scale']-1.15) < 1e-6
    c.request('setOutputOption', {'key':'rotation','value':90, 'documentEpoch':epoch})
    record('fields-and-rotation')
    try: c.request('getObjectInspector', {'object':{}, 'context':'map','documentEpoch':epoch-1})
    except RuntimeError: record('stale-document-rejected')
    else: raise AssertionError('Stale epoch accepted')
    if a.extended:
        draft = c.request('getThemeDraft')
        draft['data']['canvas']['background'] = '#E6EEF4'
        saved = c.request('commitThemeDraft', {'data':draft['data'], 'theme':draft['theme'],
            'documentEpoch':epoch, 'settingsRevision':draft['settingsRevision'], 'persist':True})
        assert saved['saved'] and saved['theme'].startswith('user_')
        draft = c.request('getThemeDraft')
        assert draft['data']['canvas']['background'] == '#E6EEF4'
        exported = c.request('exportThemeDraft', {'data':draft['data'], 'documentEpoch':epoch,
            'path':str(out/'测试 主题.yaml')})
        assert exported['exported'] and (out/'测试 主题.yaml').stat().st_size > 0
        record('theme-edit-save-as-and-persist')
        poi = c.request('getPoiEditor'); poi_key = next(iter(poi['entries']))
        poi_value = not poi['entries'][poi_key]['display']
        change = {poi_key:{'icons':poi['entries'][poi_key]['icons'], 'display':poi_value}}
        assert c.request('validatePoiMapping', {'token':poi['token'], 'changes':change})['issues'] == []
        assert c.request('savePoiMapping', {'token':poi['token'], 'changes':change})['saved'] == 1
        assert c.request('getPoiEditor')['entries'][poi_key]['display'] == poi_value
        record('poi-edit-validate-save')
        text = c.request('getTextIconSettings', {'target':'normal'})
        text['profile']['density_tier'] = 'sparse'
        c.request('setTextIconSettings', {**text, 'fields':{}, 'options':{}, 'target':'normal', 'documentEpoch':epoch})
        assert c.request('getTextIconSettings', {'target':'normal'})['profile']['density_tier'] == 'sparse'
        record('text-icon-settings-save')
        selected_regions = [r['id'] for r in regions['parks'][:2]]
        c.request('setRegionSelection', {'group':'parks','ids':selected_regions,'documentEpoch':epoch})
        assert c.request('getRegionCatalog')['park_ids'] == sorted(selected_regions)
        record('region-selection-roundtrip')
        task = uuid.uuid4().hex; target = out/'立交 合集.png'
        assert c.request('renderInterchanges', {'target':str(target), 'requestId':task,
            'documentEpoch':epoch, 'request':{'top_count':1,'tile_size':512}})['started']
        name, payload = c.event({'exportDone','error'}, task)
        assert name == 'exportDone', payload
        assert target.stat().st_size > 0
        record('interchanges-write-completion', target.stat().st_size)
    for context, fmt in ([] if a.extended else [('normal','PNG'),('transit','PNG'),('transit','HTML'),('metro','RMG')]):
        task = uuid.uuid4().hex
        target = out / (context + '-' + fmt.lower())
        if fmt in ('PNG','HTML'): target = target.with_suffix('.'+fmt.lower())
        else: target.mkdir(exist_ok=True)
        params = {'context':context,'format':fmt,'target':str(target),'documentEpoch':epoch,
                  'requestId':task,'output':{'size':1024},'auto_open':False}
        assert c.request('export', params)['started']
        name, payload = c.event({'exportDone','externalExportDone','error'}, task)
        if name == 'error': raise RuntimeError(str(payload))
        assert target.exists()
        files = [target] if target.is_file() else [p for p in target.rglob('*') if p.is_file()]
        assert files and all(p.stat().st_size > 0 for p in files)
        record(context+'-'+fmt, {'files':len(files),'bytes':sum(p.stat().st_size for p in files)})
    random = c.request('pickRandomRegion'); assert len(random['bounds']) == 4
    record('random-region', random['bounds'])
    if a.extended:
        task = uuid.uuid4().hex; target = out/'随机 特写.png'
        assert c.request('export', {'context':'random_spot','format':'PNG','target':str(target),
            'documentEpoch':epoch,'requestId':task,'output':{'size':512},'auto_open':False,
            'region':{'source':'random_spot','bounds':random['bounds']}})['started']
        name, payload = c.event({'exportDone','error'}, task)
        assert name == 'exportDone' and target.stat().st_size > 0, payload
        record('random-region-png')
        assert c.request('clearCache')['cleared']
        record('cache-cleared')
    diagnostics = c.request('getDiagnostics'); record('diagnostics', list(diagnostics))
    c.close(); c = Client(); c.request('hello', {'protocolVersion':1})
    c.request('startPreview'); c.request('setPreviewEnabled', {'enabled':True})
    snapshot = c.request('getSnapshot')
    assert abs(snapshot['fields']['road.level.highway.footprint_scale']-1.15) < 1e-6
    record('restart-restores-fields')
    if a.extended:
        assert c.request('getThemeDraft')['data']['canvas']['background'] == '#E6EEF4'
        assert c.request('getPoiEditor')['entries'][poi_key]['display'] == poi_value
        assert c.request('getTextIconSettings', {'target':'normal'})['profile']['density_tier'] == 'sparse'
        record('restart-restores-theme-poi-text')
    if a.alternate_city:
        c.frames.clear()
        assert c.request('openDocument', {'path':str(a.city.resolve())})['started']
        cancelled = c.request('cancel')
        record('cancel-reports-backend-result', cancelled)
        for _ in range(3):
            assert c.request('openDocument', {'path':str(a.alternate_city.resolve())})['started']
        while True:
            name, payload = c.event({'sceneReady','error','sceneError'})
            if name != 'sceneReady': raise RuntimeError(str(payload))
            if payload['completion'] == 'complete':
                restored = c.request('getSnapshot')
                if Path(restored['sourceFile']) == a.alternate_city.resolve(): break
        assert restored['documentEpoch'] == 4, restored['documentEpoch']
        record('rapid-city-switch-and-cancel', restored['documentEpoch'])
    c.close(); report['success'] = True
except BaseException as error:
    report['error'] = repr(error)
    if c.child.poll() is None:
        c.child.stdin.close()
        try: c.child.wait(timeout=10)
        except subprocess.TimeoutExpired: c.child.terminate(); c.child.wait(timeout=10)
finally:
    (out / 'engine-workflows.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
if not report['success']: raise SystemExit(report['error'])
