"""Fault-injection peer for the Swift JSONL transport; never used in the app."""
import json
import os
import sys

for line in sys.stdin:
    frame = json.loads(line)
    method = frame['method']
    if method == 'close':
        break
    if method == 'timeout':
        print('x' * 100000 + 'stderr-marker', file=sys.stderr, flush=True)
        continue
    if method == 'exit':
        os._exit(23)
    response = {'id': frame['id']}
    if method == 'error':
        response['error'] = {'message':'fixture error'}
    else:
        response['result'] = frame['params']
    print(json.dumps(response, ensure_ascii=False), flush=True)
