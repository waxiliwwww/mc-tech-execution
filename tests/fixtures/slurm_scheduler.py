"""Controlled Slurm boundary, never a real scheduler or HPC acceptance oracle.

Executes the product's generated script on a local disposable directory. The
fixture only stores Slurm-shaped queue/accounting responses and faults.
"""
import json
import shlex
import os
from pathlib import Path
import subprocess
import sys
import time

root=Path(os.environ['SLURM_FIXTURE_ROOT']);root.mkdir(exist_ok=True)
records=root/'scheduler.json';rows=json.loads(records.read_text()) if records.exists() else {}
action=sys.argv[1];args=sys.argv[2:]
mode=os.environ.get('SLURM_FIXTURE_MODE','complete')
with (root/'calls.jsonl').open('a') as log:log.write(json.dumps([action,args])+'\n')

def save():records.write_text(json.dumps(rows))
if action=='sbatch':
    identifier=str(1000+len(rows));script=Path(args[-1]).read_text()
    directives={x.split('=',1)[0].split()[-1]:shlex.split(x.split('=',1)[1])[0] for x in script.splitlines() if x.startswith('#SBATCH ') and '=' in x}
    name=directives['--job-name']
    rows[identifier]={'name':name,'comment':directives.get('--comment',''),'state':'PENDING','exit':'0:0'};save()
    if mode not in ('hold','receipt_lost'):
        with open('slurm-'+identifier+'.out','wb') as log:run=subprocess.run(['bash',args[-1]],stdout=log,stderr=subprocess.STDOUT)
        rows[identifier].update(state='COMPLETED' if run.returncode==0 else 'FAILED',exit=str(run.returncode)+':0');save()
    if mode=='receipt_lost':print('transport disconnected',file=sys.stderr);sys.exit(1)
    print(identifier)
elif action in ('squeue','sacct'):
    if action=='squeue' and mode=='slow_queue':time.sleep(2)
    names=next((v.split('=',1)[1] for v in args if v.startswith('--name=')),None)
    ids=next((v.split('=',1)[1] for v in args if v.startswith('--jobs=')),None)
    for identifier,row in rows.items():
        if (ids and ids!=identifier) or (names and names!=row['name']):continue
        if action=='squeue':
            if mode in ('queue_error','queue_only_error'):print('queue unavailable',file=sys.stderr);sys.exit(1)
            if row['state'] in ('PENDING','RUNNING'):print(identifier+'|'+row['state']+('|' + row.get('comment','') if any('%k' in x for x in args) else ''))
        elif mode not in ('accounting_delay','queue_error'):
            print(identifier+'|'+('PENDING' if mode=='stale_accounting' and row['state']=='RUNNING' else row['state'])+'|'+row['exit']+('|' + row.get('comment','') if any('Comment' in x for x in args) else ''))
            print(identifier+'.batch|COMPLETED|0:0') # must not mistake a step for the allocation
elif action=='scontrol':
    if mode=='slow_queue':time.sleep(2)
    if mode in ('queue_error','queue_only_error','accounting_delay'):sys.exit(1)
    identifier=args[-1];row=rows.get(identifier)
    if row and row['state'] in ('PENDING','RUNNING'):print('JobId='+identifier+' JobState='+row['state']+' Reason=Resources')
elif action=='scancel':
    if mode=='slow_cancel':time.sleep(2)
    rows[args[-1]].update(state='CANCELLED',exit='0:15');save()
else:sys.exit(2)
