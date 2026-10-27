"""The actual standalone CLI owns direct-application terminal confirmation."""
import json
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import pty
import select
import subprocess
import sys
import tempfile
import time
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]


def terminal_run(command, env, decision, timeout=120, before_decision=None):
    master, slave = pty.openpty()
    transcript = bytearray()
    output = tempfile.TemporaryFile(mode='w+')
    process = subprocess.Popen(command, env=env, stdin=slave, stdout=output, stderr=slave, close_fds=True)
    os.close(slave)
    answered = False; deadline = time.monotonic() + timeout
    try:
        while process.poll() is None or select.select([master], [], [], 0)[0]:
            if time.monotonic() > deadline:raise AssertionError('terminal confirmation flow timed out')
            if select.select([master], [], [], .05)[0]:
                try:chunk = os.read(master, 65536)
                except OSError:break
                if not chunk:break
                transcript.extend(chunk)
            if not answered and b'[y/N]' in transcript:
                if before_decision:before_decision()
                os.write(master, decision.encode() + b'\n'); answered = True
        process.wait(timeout=10); output.seek(0)
        return process.returncode, output.read(), transcript.decode(errors='replace'), answered
    finally:
        if process.poll() is None:process.kill(); process.wait()
        os.close(master); output.close()


class DirectRepairConfirmation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mc cli repair ')
        self.folder = Path(self.temp.name)
        self.env = {**os.environ, 'MC_WORKSPACE': str(self.folder)}
        self.path = self.folder / 'input.txt'; self.path.write_text('particles = -1\n')

    def tearDown(self):self.temp.cleanup()

    def call(self, name, arguments, input=None):
        return subprocess.run([sys.executable, str(ROOT / 'mcp/cli.py'), 'call', name, json.dumps(arguments)],
                              env=self.env, input=input, capture_output=True, text=True, timeout=30)

    def preview(self):
        result = self.call('preview_repair', {'path': 'input.txt', 'old_text': '-1', 'new_text': '100'})
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_noninteractive_and_model_confirmed_flag_cannot_apply(self):
        plan = self.preview()
        result = self.call('apply_repair', {'plan_id': plan['plan_id']}, input='y\n')
        self.assertNotEqual(result.returncode, 0); self.assertIn('非交互', result.stderr)
        result = self.call('apply_repair', {'plan_id': plan['plan_id'], 'confirmed': True})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.path.read_text(), 'particles = -1\n')

    def test_actual_tty_rejection_then_confirmation(self):
        plan = self.preview(); args=[sys.executable,str(ROOT/'mcp/cli.py'),'call','apply_repair',json.dumps({'plan_id':plan['plan_id']})]
        code, _output, shown, answered = terminal_run(args, self.env, 'n')
        self.assertTrue(answered); self.assertNotEqual(code, 0)
        self.assertLess(shown.index('-particles = -1'), shown.index('[y/N]'))
        self.assertEqual(self.path.read_text(), 'particles = -1\n')
        code, output, _shown, answered = terminal_run(args, self.env, 'y')
        self.assertTrue(answered); self.assertEqual(code, 0)
        data = json.loads(output)
        self.assertEqual(self.path.read_text(), 'particles = 100\n')
        self.assertEqual((self.folder/data['backup']).read_text(), 'particles = -1\n')
        self.assertNotEqual(self.call('apply_repair', {'plan_id':plan['plan_id']}).returncode, 0)

    def test_changed_file_while_waiting_does_not_overwrite(self):
        plan = self.preview(); args=[sys.executable,str(ROOT/'mcp/cli.py'),'call','apply_repair',json.dumps({'plan_id':plan['plan_id']})]
        code, _output, shown, answered = terminal_run(args, self.env, 'y', before_decision=lambda: self.path.write_text('external change\n'))
        self.assertTrue(answered); self.assertNotEqual(code, 0)
        self.assertIn('确认期间', shown)
        self.assertEqual(self.path.read_text(), 'external change\n')

    def test_all_internal_snapshot_directories_are_read_only_to_repairs(self):
        for name in ['.mc-jobs/abc/work/settings.xml','.mc-scans/abc/cases/0/settings.xml']:
            path = self.folder/name; path.parent.mkdir(parents=True); path.write_text('value=-1')
            result = self.call('preview_repair', {'path':name,'old_text':'-1','new_text':'1'})
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(path.read_text(),'value=-1')

    def test_two_concurrent_cli_confirmations_do_not_overwrite_each_other(self):
        first=self.preview()
        second=json.loads(self.call('preview_repair',{'path':'./input.txt','old_text':'-1','new_text':'200'}).stdout)
        barrier=threading.Barrier(2)
        def apply(plan):
            command=[sys.executable,str(ROOT/'mcp/cli.py'),'call','apply_repair',json.dumps({'plan_id':plan['plan_id']})]
            return terminal_run(command,self.env,'y',before_decision=lambda:barrier.wait(timeout=20))
        with ThreadPoolExecutor(max_workers=2) as executor:outcomes=list(executor.map(apply,[first,second]))
        self.assertEqual(sum(result[0]==0 for result in outcomes),1)
        self.assertIn(self.path.read_text(),['particles = 100\n','particles = 200\n'])


if __name__ == '__main__':unittest.main()
