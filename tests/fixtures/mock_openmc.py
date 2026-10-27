"""Process-control fixture only. It never computes physics or emits a statepoint."""
import json
from pathlib import Path
import subprocess
import sys
import time

if '--version' in sys.argv:
    print('OpenMC 0.16.0 - MOCK PROCESS FIXTURE')
    raise SystemExit(0)
if '--threads' not in sys.argv or '-t' in sys.argv:
    print('invalid thread option: use --threads; -t enables tracks', flush=True)
    raise SystemExit(9)
mode = json.loads(Path('mock-mode.json').read_text())['mode']
print('MOCK PROCESS FIXTURE; no physical result', flush=True)
if mode == 'fail':
    print('fixture intentional failure', flush=True)
    raise SystemExit(7)
if mode in ('long', 'timeout', 'parent-exits'):
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
    Path('child.pid').write_text(str(child.pid))
    if mode != 'parent-exits':time.sleep(120)
if mode == 'log-limit':
    print('x' * 5000, flush=True)
print('MOCK COMPLETED', flush=True)
