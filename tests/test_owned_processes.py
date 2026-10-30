"""Schedule a genuine orphan adoption across a deliberately stale /proc read."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

class ProcessSnapshotRace(unittest.TestCase):
    def test_adopted_child_in_stale_snapshot_is_found_signalled_and_reaped(self):
        code=r'''
import json,os,signal,subprocess,sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from owned_processes import OwnedProcessTree,ProcessControlError
owner=OwnedProcessTree()
child_code="import time; time.sleep(120)"
parent_code="import subprocess,sys,time;from pathlib import Path;p=subprocess.Popen([sys.executable,'-c',sys.argv[1]],start_new_session=True);Path('child.pid').write_text(str(p.pid));exec('while not Path(\"release\").exists():\\n time.sleep(.002)');sys.exit(7)"
runner=subprocess.Popen([sys.executable,'-c',parent_code,child_code])
child=None
try:
 deadline=time.monotonic()+5
 while not Path('child.pid').exists():
  assert time.monotonic()<deadline;time.sleep(.005)
 child=int(Path('child.pid').read_text());real_info=owner.info;armed=True;stale_ppid=None
 def scheduled_info(pid):
  global armed,stale_ppid
  if armed and pid==runner.pid:return None
  value=real_info(pid)
  if armed and pid==child and value and value['ppid']==runner.pid:
   stale_ppid=value['ppid'];Path('release').touch();runner.wait(timeout=5);armed=False
   assert real_info(child)['ppid']==os.getpid()
  return value
 owner.info=scheduled_info
 observed=owner.descendants()
 assert stale_ppid==runner.pid and child in observed,observed
 assert observed[child]['ppid']==os.getpid(),observed
 cleanup=owner.stop(runner,kill_runner=False)
 assert cleanup['verified_empty'],cleanup
 assert runner.returncode==7,runner.returncode
 assert not Path(f'/proc/{child}').exists(),'orphan or zombie remains'
 # A final-cleanup enumeration error must return an explicit unknown, not
 # throw a second exception which loses the persistent job outcome.
 def unavailable():raise ProcessControlError('injected enumeration unavailable')
 owner.descendants=unavailable
 failure=owner.stop(runner,kill_runner=False)
 assert failure['verified_empty'] is False and 'unavailable' in failure['error'],failure
 print(json.dumps({'captured_stale_ppid':stale_ppid,'adopted_child':child,'cleanup':cleanup,'runner_exit_code':runner.returncode,'unknown_cleanup':failure}))
finally:
 for pid in [runner.pid,child]:
  if pid and (value:=owner.info(pid)):
   try:owner.kill_owned(value)
   except ProcessLookupError:pass
 try:runner.wait(timeout=5)
 except subprocess.TimeoutExpired:pass
 if child:
  try:os.waitpid(child,0)
  except ChildProcessError:pass
'''
        with tempfile.TemporaryDirectory(prefix='mc adoption race ') as folder:
            value=subprocess.run([sys.executable,'-c',code,str(ROOT/'mcp')],cwd=folder,capture_output=True,text=True,timeout=15)
            self.assertEqual(value.returncode,0,value.stdout+'\n'+value.stderr)
            receipt=json.loads(value.stdout)
            self.assertEqual(receipt['runner_exit_code'],7);self.assertTrue(receipt['cleanup']['verified_empty'])

if __name__=='__main__':unittest.main()
