"""Real kernel/solver ownership. Internal fixtures do not bypass public source guards.

The public catalogue-integrity boundary is covered separately. Here a private
worker entry runs a deliberately long OpenMC notebook to test actual cleanup.
"""
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'mcp'))
import business
import openmc_jobs as jobs

RUNTIME=os.environ.get('MC_TEST_OPENMC_CONFIG')


@unittest.skipUnless(RUNTIME,'设置MC_TEST_OPENMC_CONFIG运行真实内核/求解器进程树验证')
class NotebookProcessOwnership(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='mc real notebook ownership ')
        self.workspace=Path(self.temp.name)/'workspace';self.workspace.mkdir()
        self.cfg=json.loads(Path(RUNTIME).read_text());self.known={};self.job_ids=[]
        self.patches=[patch.object(business,'WORKSPACE',self.workspace),
                      patch.dict(os.environ,{'MC_WORKSPACE':str(self.workspace)})]
        for p in self.patches:p.start()

    def tearDown(self):
        for identifier in self.job_ids:
            try:jobs.cancel(identifier)
            except ValueError:pass
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            if all(jobs.status(x)['state'] in jobs.TERMINAL for x in self.job_ids):break
            time.sleep(.05)
        # Bounded test-only cleanup, restricted to captured PID identities.
        for pid,identity in self.known.items():
            if jobs.identity(pid)!=identity:continue
            try:
                fd=os.pidfd_open(pid)
                try:
                    if jobs.identity(pid)==identity:signal.pidfd_send_signal(fd,signal.SIGKILL)
                finally:os.close(fd)
            except ProcessLookupError:pass
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()

    def submit(self,name,timeout=120):
        case=self.workspace/'cases'/name;case.mkdir(parents=True)
        pulse="import sys,time;from pathlib import Path;p=Path(sys.argv[1]);exec('while True:\\n p.write_text(str(time.monotonic_ns()))\\n time.sleep(.05)')"
        code=f'''import os,sys,json,subprocess
from pathlib import Path
import openmc
heartbeat=subprocess.Popen([sys.executable,'-c',{pulse!r},str(Path.cwd()/'heartbeat.txt')],start_new_session=True)
Path('kernel-owner.json').write_text(json.dumps({{'kernel_pid':os.getpid(),'heartbeat_pid':heartbeat.pid}}))
model=openmc.examples.random_ray_pin_cell()
model.settings.batches=1000000
model.settings.inactive=10
model.run(output=False)
'''
        notebook={'nbformat':4,'nbformat_minor':5,'metadata':{},'cells':[
            {'cell_type':'code','id':'owned-native-transport','source':code,'metadata':{},'outputs':[],'execution_count':None}]}
        (case/'mg-mode-part-i.ipynb').write_text(json.dumps(notebook))
        runtime={**self.cfg,'job_timeout_seconds':timeout}
        value=jobs.submit('cases/'+name,_runtime=runtime,_example_workflow={
            'example_id':'notebook-mg-mode-part-i','step':'execute','parameters':{}})
        self.job_ids.append(value['job_id']);return value['job_id']

    def descendants(self,worker):
        records={}
        for directory in Path('/proc').iterdir():
            if not directory.name.isdecimal():continue
            try:
                fields=(directory/'stat').read_text().rsplit(')',1)[1].split()
                records[int(directory.name)]=int(fields[1])
            except (FileNotFoundError,PermissionError,ProcessLookupError):pass
        owned={worker}
        while True:
            more={p for p,parent in records.items() if parent in owned}-owned
            if not more:return owned
            owned.update(more)

    def native_started(self,identifier):
        directory=jobs.job_directory(identifier);deadline=time.monotonic()+35
        while time.monotonic()<deadline:
            record=jobs.read(directory);worker=record.get('worker_pid')
            candidates=self.descendants(worker) if worker else set()
            for pid in candidates:
                identity=jobs.identity(pid)
                if identity:self.known[pid]=identity
            marker=directory/'work/kernel-owner.json'
            solver=[]
            for pid in candidates:
                try:
                    if Path(f'/proc/{pid}/exe').resolve()==Path(self.cfg['openmc_command'][0]).resolve():solver.append(pid)
                except (OSError,ValueError):pass
            if marker.exists() and solver:
                value=json.loads(marker.read_text());value.update(worker_pid=worker,solver_pid=solver[0],pids=sorted(candidates))
                self.assertNotEqual(os.getsid(value['kernel_pid']),os.getsid(record['child_pid']) if record.get('child_pid') else os.getsid(worker))
                self.assertTrue((directory/'work/heartbeat.txt').exists())
                return value
            current=jobs.status(identifier)
            if current['state'] in jobs.TERMINAL:self.fail('真实OpenMC尚未启动，作业已终止：'+str(current))
            time.sleep(.05)
        self.fail('未在时限内观测到真实OpenMC进程；未用休眠替身冒充求解器')

    def ended(self,identifier):
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            status=jobs.status(identifier)
            if status['state'] in jobs.TERMINAL:return status
            time.sleep(.05)
        self.fail('作业终态超时')

    def assert_gone(self,pids):
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            if all(not Path(f'/proc/{pid}').exists() for pid in pids):return
            time.sleep(.025)
        self.fail('已宣称结束但仍有进程/僵尸：'+str([p for p in pids if Path(f'/proc/{p}').exists()]))

    def save(self,name,value):
        directory=ROOT/'artifacts/runs/official-examples-20261030';directory.mkdir(parents=True,exist_ok=True)
        (directory/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')

    def test_cancel_real_notebook_solver_tree_preserves_concurrent_job_and_stops_writes(self):
        first=self.submit('first');a=self.native_started(first)
        second=self.submit('second');b=self.native_started(second)
        second_engine_identity=jobs.identity(b['solver_pid'])
        heartbeat=jobs.job_directory(second)/'work/heartbeat.txt';before=heartbeat.stat().st_mtime_ns
        jobs.cancel(first);status=self.ended(first)
        self.assertEqual(status['state'],'cancelled',status)
        self.assertTrue(status['process_cleanup']['verified_empty']);self.assert_gone(a['pids'])
        first_heartbeat=jobs.job_directory(first)/'work/heartbeat.txt';stopped=first_heartbeat.read_bytes()
        time.sleep(.2)
        self.assertEqual(first_heartbeat.read_bytes(),stopped)
        self.assertEqual(jobs.identity(b['solver_pid']),second_engine_identity)
        self.assertEqual(jobs.status(second)['state'],'running')
        self.assertGreater(heartbeat.stat().st_mtime_ns,before)
        self.assertFalse(jobs.cancel(first)['changed'])
        jobs.cancel(second);self.assertEqual(self.ended(second)['state'],'cancelled');self.assert_gone(b['pids'])
        self.save('notebook-native-cancel-isolation.json',{'first':a,'second':b,'status':status})

    def test_timeout_real_notebook_kills_kernel_solver_and_independent_session_writer(self):
        identifier=self.submit('timeout',timeout=15);observed=self.native_started(identifier)
        status=self.ended(identifier)
        self.assertEqual(status['state'],'failed',status);self.assertIn('时限',status['error'])
        self.assertTrue(status['process_cleanup']['verified_empty']);self.assert_gone(observed['pids'])
        heartbeat=jobs.job_directory(identifier)/'work/heartbeat.txt';before=heartbeat.read_bytes();time.sleep(.2)
        self.assertEqual(heartbeat.read_bytes(),before)
        self.save('notebook-native-timeout.json',{'observed':observed,'status':status})


if __name__=='__main__':unittest.main()
