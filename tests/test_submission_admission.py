"""Cross-process capacity reservation; real files/locks, solver launch substituted.

Two callers start together and pause after the capacity check. At max_jobs=1
only one may publish a queued job; the other must see that reservation. No
OpenMC or user service is involved. Does not prove solver throughput.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]

class SubmissionAdmission(unittest.TestCase):
    def test_competing_producers_cannot_both_reserve_the_last_slot(self):
        with tempfile.TemporaryDirectory(prefix='mc admission ') as tmp:
            root = Path(tmp); case = root/'workspace/case';case.mkdir(parents=True)
            for section in ('settings','geometry','materials'):
                (case/(section+'.xml')).write_text('<'+section+'/>')
            cfg=json.loads((ROOT/'config/openmc.json').read_text())
            cfg.update(cpu_count=8,max_concurrent_jobs=1,min_free_disk_bytes=0)
            runtime=root/'runtime.json';runtime.write_text(json.dumps(cfg))
            code=r'''
import json,sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1]);import openmc_jobs as jobs
root=Path(sys.argv[2]);number=sys.argv[3]
original=jobs.check_capacity
def paused(cfg,threads):
 value=original(cfg,threads);time.sleep(.4);return value
jobs.check_capacity=paused
# The solver boundary is outside admission; real persistent reservations remain.
jobs.spawn_worker=lambda *args:None
(root/('ready-'+number)).touch()
while not (root/'start').exists():time.sleep(.01)
try:print(json.dumps({'ok':True,**jobs.submit('case')},ensure_ascii=False))
except ValueError as exc:print(json.dumps({'ok':False,'error':str(exc)},ensure_ascii=False))
'''
            env={**os.environ,'MC_WORKSPACE':str(case.parent),'MC_OPENMC_CONFIG':str(runtime)}
            children=[subprocess.Popen([sys.executable,'-c',code,str(ROOT/'mcp'),str(root),str(i)],
                        env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE) for i in range(2)]
            try:
                deadline=time.monotonic()+10
                while len(list(root.glob('ready-*')))<2:
                    if time.monotonic()>deadline:self.fail('admission clients did not become ready')
                    time.sleep(.01)
                (root/'start').touch();results=[]
                for child in children:
                    out,err=child.communicate(timeout=10)
                    self.assertEqual(child.returncode,0,err);results.append(json.loads(out))
                self.assertEqual(sum(r['ok'] for r in results),1,results)
                self.assertIn('并发作业已达上限',next(r['error'] for r in results if not r['ok']))
                records=list((case.parent/'.mc-jobs').glob('*/job.json'))
                self.assertEqual(len(records),1)
                self.assertEqual(json.loads(records[0].read_text())['state'],'queued')
            finally:
                for child in children:
                    if child.poll() is None:child.kill()
                    child.communicate()

if __name__=='__main__':unittest.main()
