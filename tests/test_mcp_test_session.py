"""Owned test entry: real MCP identity/contract negatives and process lifecycle.

No model, nuclear data or solver required. Old-version and stale tool definitions
are real subprocess services, not mocked handshakes. Assert no command can run
past a failed preflight, and normal/failure/timeout/signals drain owned processes.
User services/data are not modified. SIGKILL/power failure cannot run a finally.
"""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import signal
import shutil
import shlex
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('mcp_test_session', ROOT/'scripts/mcp_test_session.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class TestSession(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mc test lifecycle ')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def run_owned(self, command=(), options=(), signal_to_send=None):
        evidence = self.directory/'evidence'
        process = subprocess.Popen([sys.executable, str(ROOT/'scripts/mcp_test_session.py'),
            '--output', str(evidence), *options, '--', *command], cwd=ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        if signal_to_send is not None:
            deadline = time.monotonic()+15
            while not (evidence/'ready').exists():
                if process.poll() is not None or time.monotonic()>deadline:
                    self.fail('test command never reached ready: '+process.communicate(timeout=5)[0])
                time.sleep(.025)
            process.send_signal(signal_to_send)
        output = process.communicate(timeout=25)[0]
        receipt = json.loads((evidence/'receipt.json').read_text())
        self.assertTrue(receipt['cleanup']['verified_empty'], output)
        self.assertTrue(receipt['port_released'], output)
        self.assertFalse(Path('/proc/'+str(receipt['service_pid'])).exists(), output)
        self.assertFalse((evidence/'workspace').is_symlink())
        return process.returncode, receipt, output

    def test_success_records_real_full_contract_and_releases_service(self):
        code, receipt, output = self.run_owned([sys.executable, '-c',
            'import os;from pathlib import Path;Path(os.environ["MC_WORKSPACE"],"evidence.txt").write_text(os.environ["MC_TEST_MCP_URL"])'])
        self.assertEqual(code, 0, output)
        self.assertEqual(receipt['preflight']['version'], (ROOT/'VERSION').read_text().strip())
        self.assertEqual(set(receipt['preflight']['definitions']), set(runner.definitions()))
        self.assertEqual((Path(receipt['workspace'])/'evidence.txt').read_text(), receipt['url'])

    def test_failing_command_preserves_failure_and_evidence_but_stops_service(self):
        code, receipt, output = self.run_owned([sys.executable, '-c', 'raise SystemExit(7)'])
        self.assertEqual(code, 7, output)
        self.assertEqual(receipt['exit_code'], 7)

    def detached_command(self):
        return [sys.executable, '-c', '''import os,subprocess,sys,time
from pathlib import Path
p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'],start_new_session=True)
Path(os.environ['MC_TEST_SESSION_DIR'],'child.pid').write_text(str(p.pid))
Path(os.environ['MC_TEST_SESSION_DIR'],'ready').touch()
time.sleep(120)
''']

    def assert_detached_gone(self):
        pid = (self.directory/'evidence/child.pid').read_text()
        self.assertFalse(Path('/proc/'+pid).exists(), 'detached descendant leaked')

    def test_timeout_drains_detached_worker(self):
        code, receipt, output = self.run_owned(self.detached_command(), ('--timeout','0.5'))
        self.assertNotEqual(code, 0, output)
        self.assertIn('TimeoutExpired', receipt['error'])
        self.assert_detached_gone()

    def test_signals_drain_service_and_independent_worker_sessions(self):
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            with self.subTest(signal=sig):
                # Each run needs a fresh output to preserve previous evidence.
                self.directory = Path(self.temp.name)/str(sig)
                self.directory.mkdir()
                code, receipt, output = self.run_owned(self.detached_command(), signal_to_send=sig)
                self.assertEqual(code, 128+sig, output)
                self.assert_detached_gone()

    def probe_service(self, version=None, mutation=''):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port = sock.getsockname()[1]
        cfg = self.directory/'service.json'
        cfg.write_text(json.dumps({'host':'127.0.0.1','port':port,'path':'/mcp','name':'old-test',
            'clientName':'test','version':version or (ROOT/'VERSION').read_text().strip()}))
        env = {k:v for k,v in os.environ.items() if not k.startswith(('MC_', 'MCP_'))}
        env.update(MCP_CONFIG_FILE=str(cfg), MCP_CONNECTION_FILE=str(self.directory/'connection.json'),
                   MC_WORKSPACE=str(self.directory/'workspace'))
        code = 'import sys;sys.path.insert(0,'+repr(str(ROOT/'mcp'))+');import server;'+(mutation or 'pass')+';sys.exit(server.main())'
        log = (self.directory/'server.log').open('w')
        server = subprocess.Popen([sys.executable,'-c',code], env=env, stdout=log, stderr=log)
        self.addCleanup(log.close)
        def stop():
            if server.poll() is None:
                server.terminate()
                try:server.wait(timeout=6)
                except subprocess.TimeoutExpired:server.kill();server.wait()
        self.addCleanup(stop)
        for _ in range(200):
            if (self.directory/'connection.json').exists(): break
            if server.poll() is not None:self.fail((self.directory/'server.log').read_text())
            time.sleep(.025)
        return f'http://127.0.0.1:{port}/mcp'

    def assert_blocked(self, url, message):
        # A downstream action lives strictly after preflight, just as in main().
        async def attempt():
            await runner.verify_session(url, (ROOT/'VERSION').read_text().strip(),
                                        runner.definitions(), runner.runtime_identity.code_fingerprint())
            (self.directory/'should-not-run').touch()
        with self.assertRaises(BaseException) as caught:
            asyncio.run(attempt())
        def texts(error):
            return str(error)+' '.join(texts(e) for e in getattr(error,'exceptions',[]))
        self.assertIn(message, texts(caught.exception))
        self.assertFalse((self.directory/'should-not-run').exists())

    def test_old_version_is_rejected_before_downstream_action(self):
        self.assert_blocked(self.probe_service(version='0.14.0'), '服务版本不一致')

    def test_existing_old_service_is_neither_reused_nor_stopped(self):
        url = self.probe_service(version='0.14.0')
        code, receipt, output = self.run_owned()
        self.assertEqual(code, 0, output)
        self.assertNotEqual(receipt['url'], url)
        # Still reachable, still old: owned cleanup did not stop that service.
        self.assert_blocked(url, '服务版本不一致')

    def test_same_version_missing_tools_is_rejected(self):
        self.assert_blocked(self.probe_service(mutation="server.TOOLS[:]=[t for t in server.TOOLS if t['name'] not in ('openmc_update_tally','openmc_delete_tally')]"), 'missing=')

    def test_same_version_stale_schema_is_rejected(self):
        self.assert_blocked(self.probe_service(mutation="next(t for t in server.TOOLS if t['name']=='openmc_build_model')['inputSchema']['required'].append('obsolete_field')"), 'changed=')

    def test_same_version_stale_descriptions_are_rejected(self):
        self.assert_blocked(self.probe_service(mutation="server.TOOLS[0]['description']='stale cached description'"), 'changed=')

    def test_real_entry_old_startup_configuration_explains_and_cleans_failure(self):
        evidence = self.directory/'old-startup'
        # Inject an actual old configured service at the launch boundary, not
        # into the checker or its HTTP/MCP response. No product file is edited.
        launch = """import os,json,sys
from pathlib import Path
cfg=Path(os.environ['MCP_CONFIG_FILE']);d=json.loads(cfg.read_text());d.pop('versionFile');d['version']='0.14.0';cfg.write_text(json.dumps(d))
sys.path.insert(0, str(Path.cwd()/'mcp'));import server;sys.exit(server.main())
"""
        harness = '''import sys
import scripts.mcp_test_session as r
original=r.subprocess.Popen
def start(cmd,**kwargs):
    if len(cmd)>1 and cmd[1].endswith('/mcp/server.py'):
        cmd=[sys.executable,'-c',LAUNCH]
    return original(cmd,**kwargs)
r.subprocess.Popen=start
sys.exit(r.main(ARGS))
'''.replace('LAUNCH',repr(launch)).replace('ARGS',repr(['--output',str(evidence),'--',sys.executable,'-c',
    'from pathlib import Path;Path('+repr(str(self.directory/'unexpected'))+').touch()']))
        result = subprocess.run([sys.executable,'-c',harness],cwd=ROOT,capture_output=True,text=True,timeout=25)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('服务版本不一致',result.stderr)
        self.assertIn('0.14.0',result.stderr)
        self.assertFalse((self.directory/'unexpected').exists())
        receipt=json.loads((evidence/'receipt.json').read_text())
        self.assertTrue(receipt['cleanup']['verified_empty'])
        self.assertTrue(receipt['port_released'])

    def test_signal_to_verify_shell_pid_is_forwarded_and_waits_for_cleanup(self):
        # Exercise the actual verify.sh and supervisor. Only dependency setup is
        # replaced (fake pip module); no checker/server/lifecycle is mocked.
        package=self.directory/'package';package.mkdir()
        shutil.copy2(ROOT/'verify.sh',package/'verify.sh')
        shutil.copy2(ROOT/'VERSION',package/'VERSION')
        shutil.copytree(ROOT/'mcp',package/'mcp',ignore=shutil.ignore_patterns('.venv','logs','__pycache__'))
        # CI installs SDK dependencies into the active global Python, whereas
        # local runs use .venv. Delegate to this test's actual interpreter in
        # either case; do not assume the source checkout contains a .venv.
        python = package/'mcp/.venv/bin/python'
        python.parent.mkdir(parents=True)
        python.write_text('#!/bin/sh\nexec '+shlex.quote(sys.executable)+' "$@"\n')
        python.chmod(0o755)
        (package/'scripts').mkdir()
        for name in ('test-mcp.sh','mcp_test_session.py'):
            shutil.copy2(ROOT/'scripts'/name,package/'scripts'/name)
        (package/'tests').mkdir()
        (package/'tests/test_wait.py').write_text('''import os,subprocess,sys,time,unittest
from pathlib import Path
class Wait(unittest.TestCase):
 def test_wait(self):
  p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'],start_new_session=True)
  Path(os.environ['MC_TEST_SESSION_DIR'],'child.pid').write_text(str(p.pid))
  Path(os.environ['MC_TEST_SESSION_DIR'],'ready').touch()
  time.sleep(120)
''')
        fake=self.directory/'dependency-stub';(fake/'pip').mkdir(parents=True)
        (fake/'pip/__init__.py').touch();(fake/'pip/__main__.py').write_text('pass\n')
        env={**os.environ,'PYTHONPATH':str(fake)}
        process=subprocess.Popen(['bash',str(package/'verify.sh'),'--tools-only'],cwd=package,env=env,
                                 stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,start_new_session=True)
        self.addCleanup(lambda:process.poll() is None and process.kill())
        deadline=time.monotonic()+20
        while True:
            ready=list((package/'artifacts/test-sessions').glob('*/ready'))
            if ready:break
            if process.poll() is not None or time.monotonic()>deadline:
                self.fail('verify test never ready: '+process.communicate(timeout=3)[0])
            time.sleep(.025)
        process.send_signal(signal.SIGTERM)  # Shell PID only, not a process group.
        output=process.communicate(timeout=25)[0]
        self.assertEqual(process.returncode,143,output)
        receipt=json.loads((ready[0].parent/'receipt.json').read_text())
        self.assertEqual(receipt['exit_code'],143)
        self.assertTrue(receipt['cleanup']['verified_empty'])
        self.assertTrue(receipt['port_released'])
        for pid in (receipt['service_pid'],int((ready[0].parent/'child.pid').read_text())):
            self.assertFalse(Path('/proc/'+str(pid)).exists())


if __name__ == '__main__':
    unittest.main()
