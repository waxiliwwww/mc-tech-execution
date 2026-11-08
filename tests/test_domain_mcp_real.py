"""Real MCP -> non-catalog model -> OpenMC -> native data/chart/report readback.

No LLM/provider calls. Requires MC_TEST_OPENMC_CONFIG; a temporary loopback service
and workspace are owned by this test, not the user's running service.
"""
import asyncio
import csv
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.request

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = os.environ.get('MC_TEST_OPENMC_CONFIG')


@unittest.skipUnless(RUNTIME, 'MC_TEST_OPENMC_CONFIG启用真实MCP/新模型/原生结果对照')
class DomainMCP(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mc domain mcp ')
        self.directory = Path(self.temp.name)
        self.workspace = self.directory / 'workspace'
        self.workspace.mkdir()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        self.url = f'http://127.0.0.1:{port}/mcp'
        cfg = self.directory / 'service.json'
        cfg.write_text(json.dumps({'host':'127.0.0.1','port':port,'path':'/mcp',
                                  'name':'domain-test','version':'1','clientName':'domain-test'}))
        env = {k:v for k,v in os.environ.items() if not k.startswith(('MC_', 'MCP_', 'HARNESS_'))}
        env.update(MC_WORKSPACE=str(self.workspace), MC_OPENMC_CONFIG=str(Path(RUNTIME).resolve()),
                   MCP_CONFIG_FILE=str(cfg), MCP_CONNECTION_FILE=str(self.directory / 'connection.json'))
        self.env = env
        self.log = (self.directory / 'server.log').open('w+')
        self.server = subprocess.Popen([sys.executable, str(ROOT/'mcp/server.py')], cwd=ROOT,
                                       env=env, stdout=self.log, stderr=self.log)
        self.addAsyncCleanup(self.stop)
        for _ in range(150):
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=.2):break
            except OSError:
                if self.server.poll() is not None:
                    self.log.seek(0);self.fail(self.log.read())
                await asyncio.sleep(.05)
        else:self.fail('isolated MCP server startup timed out')

    async def stop(self):
        if getattr(self, 'identifier', None):
            await asyncio.to_thread(subprocess.run,
                [sys.executable, str(ROOT/'mcp/cli.py'), 'call', 'openmc_cancel_job',
                 json.dumps({'job_id':self.identifier})],
                cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=20)
        self.server.terminate()
        try:await asyncio.to_thread(self.server.wait, 8)
        except subprocess.TimeoutExpired:
            self.server.kill();self.server.wait()
        self.log.close();self.temp.cleanup()

    async def test_new_combination_import_and_named_compute_share_native_results(self):
        async with streamablehttp_client(self.url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                async def call(name, data):
                    result = await session.call_tool(name, data)
                    self.assertFalse(result.isError, str(result))
                    return result.structuredContent or json.loads(result.content[0].text)
                capabilities = await call('openmc_model_capabilities', {'detail':'full'})
                found = {t.name:t for t in (await session.list_tools()).tools}
                self.assertEqual(found['openmc_build_model'].inputSchema['properties']['spec'],
                                 capabilities['spec_schema'])
                spec = json.loads((ROOT/'examples/model-specs/rectangular-assembly.json').read_text())
                # A caller's new configuration, not an ID from the fixed examples registry.
                spec['name'] = 'MCP caller custom layout'
                spec['lattices'][0]['universes'] = [[2,1,2],[1,1,2]]
                spec['materials'][1]['density'] = 0.82
                spec['materials'][1]['density_unit'] = 'g/cc'
                spec['settings']['seed'] = 71
                built = await call('openmc_build_model', {'case_dir':'caller/new-model','spec':spec})
                self.assertTrue(built['api_parsed'])
                # A normal researcher sequence: add -> change -> remove -> derive.
                source_before = {p.name:p.read_bytes() for p in (self.workspace/'caller/new-model').glob('*.xml')}
                rejected = await session.call_tool('openmc_define_tally', {'case_dir':'caller/new-model',
                    'scores':['flux'],'filters':[{'type':'material','bins':[99]}],'tally_id':9})
                self.assertTrue(rejected.isError)
                self.assertEqual({p.name:p.read_bytes() for p in (self.workspace/'caller/new-model').glob('*.xml')},source_before)
                await call('openmc_define_tally', {'case_dir':'caller/new-model','scores':['flux'],
                    'filters':[{'type':'material','bins':[1]}],'tally_id':9})
                changed=await call('openmc_update_tally', {'case_dir':'caller/new-model','tally_id':9,'scores':['fission']})
                self.assertTrue(changed['declarative_source_synchronized'])
                await call('openmc_define_tally', {'case_dir':'caller/new-model','scores':['flux'],'tally_id':10})
                await call('openmc_delete_tally', {'case_dir':'caller/new-model','tally_id':10})
                derived=await call('openmc_prepare_case',{'case_dir':'caller/new-model','target_dir':'derived-model',
                    'parameters':{'settings.particles':321}})
                self.assertTrue(derived['parameter_diff']['verified'])
                derived_spec=json.loads((self.workspace/'derived-model/model-spec.json').read_text())
                self.assertEqual(next(t for t in derived_spec['tallies'] if t['id']==9)['scores'],['fission'])
                self.assertNotIn(10,[t['id'] for t in derived_spec['tallies']])
                imported = await call('openmc_import_model', {'source_dir':'caller/new-model',
                                                            'target_dir':'imported-model'})
                self.assertTrue(imported['validation']['openmc_api']['api_parsed'])
                for name in ('geometry.xml','materials.xml','settings.xml','tallies.xml'):
                    self.assertEqual((self.workspace/'caller/new-model'/name).read_bytes(),
                                     (self.workspace/'imported-model'/name).read_bytes())
                bad = json.loads(json.dumps(spec));bad['settings']['execute_python'] = 'untrusted'
                rejected = await session.call_tool('openmc_build_model', {'case_dir':'rejected','spec':bad})
                self.assertTrue(rejected.isError)
                self.assertFalse((self.workspace/'rejected').exists())
                preview = await call('openmc_preview_input', {'case_dir':'imported-model'})
                submitted = await call('openmc_submit_job', {'case_dir':'imported-model','name':'陌生组件计算'})
                self.identifier = submitted['job_id']
                for _ in range(150):
                    status = await call('openmc_job_status', {'job_id':self.identifier})
                    if status['state'] in ('completed','failed','cancelled','interrupted'):break
                    await asyncio.sleep(.2)
                self.assertEqual(status['state'], 'completed', str(status))
                for file in preview['files']:
                    if file.get('sha256'):self.assertEqual(status['input_hashes'][file['name']],file['sha256'])
                named = await call('openmc_list_jobs', {'name':'陌生组件计算'})
                self.assertEqual([j['job_id'] for j in named['jobs']], [self.identifier])
                result = await call('openmc_read_results', {'job_id':self.identifier})
                self.assertEqual(status['final_result']['keff'],result['keff'])
                self.assertEqual(status['final_result']['statepoint_sha256'],result['statepoint_sha256'])
                selection = {'job_id':self.identifier,'tally_id':1,'score':'flux'}
                chart = await call('openmc_plot_results', selection)
                report = await call('openmc_export_report', {**selection,'format':'markdown'})
                mesh = await call('openmc_plot_mesh', selection)
                series = json.loads(Path(chart['server_path']).with_suffix('.json').read_text())
                with Path(chart['server_path']).with_suffix('.csv').open() as stream:
                    table = list(csv.DictReader(stream))
                self.assertEqual(len(table),6)
                self.assertEqual([float(r['mean']) for r in table], series['mean'])
                self.assertEqual([float(r['std_dev']) for r in table], series['std_dev'])
                self.assertIn('flux',Path(report['server_path']).read_text())
                report_series = json.loads((self.workspace/report['series_json']).read_text())
                self.assertEqual(report_series['mean'],series['mean'])
                self.assertEqual(report_series['std_dev'],series['std_dev'])
                # Independent native read: an incorrect adapter array cannot pass merely by
                # writing equally incorrect JSON/CSV/chart/report files.
                record = self.workspace/'.mc-jobs'/self.identifier
                native = self.directory/'native.json'
                python = json.loads(Path(RUNTIME).read_text())['python_command'][0]
                command = [python,'-c',
                    'import openmc,json,sys;from pathlib import Path;'
                    'p=openmc.StatePoint(str(next(Path(sys.argv[1]).glob("statepoint.*.h5"))));'
                    't=p.get_tally(id=1);'
                    'print(json.dumps({"mean":t.mean.ravel().tolist(),"std_dev":t.std_dev.ravel().tolist(),'
                    '"keff":[p.keff.nominal_value,p.keff.std_dev]}));p.close()',str(record/'work')]
                done = await asyncio.to_thread(subprocess.run,command,capture_output=True,text=True,timeout=30)
                self.assertEqual(done.returncode,0,done.stderr)
                native.write_text(done.stdout)
                truth = json.loads(done.stdout)
                self.assertEqual(series['mean'],truth['mean'])
                self.assertEqual(series['std_dev'],truth['std_dev'])
                self.assertEqual(result['keff']['mean'],truth['keff'][0])
                self.assertEqual(result['keff']['std_dev'],truth['keff'][1])
                self.assertTrue(Path(mesh['server_path']).is_file())


if __name__ == '__main__':unittest.main()
