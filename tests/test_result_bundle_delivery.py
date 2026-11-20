"""Existing completed job -> deterministic delivery -> actual MCP download.

Positive/negative regression. Requires MC_TEST_BUNDLE_SOURCE pointing to a
completed job and the owned test-session service. Copies evidence into the
isolated workspace; starts no solver and never edits original evidence. Checks
paths, raw units, retained warnings, source hashes and downloaded bytes; does
not test model compliance, Desktop UI or professional physical correctness.
"""
import base64, hashlib, json, os, shutil, unittest
from pathlib import Path
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from scripts.mcp_test_session import check_discovery

def hashes(folder):
    return {str(p.relative_to(folder)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in folder.rglob('*') if p.is_file()}

@unittest.skipUnless(os.environ.get('MC_TEST_BUNDLE_SOURCE') and os.environ.get('MC_TEST_MCP_URL'),
                     'MC_TEST_BUNDLE_SOURCE and owned MCP session enable existing-result replay')
class ExistingResultDelivery(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.source=Path(os.environ['MC_TEST_BUNDLE_SOURCE']);cls.before=hashes(cls.source)
        cls.workspace=Path(os.environ['MC_WORKSPACE']);cls.job=cls.source.name
        cls.record=cls.workspace/'.mc-jobs'/cls.job
        shutil.copytree(cls.source,cls.record,dirs_exist_ok=True)
        cls.inputs=hashes(cls.record/'work');cls.original_jobs=set((cls.workspace/'.mc-jobs').iterdir())
    @classmethod
    def tearDownClass(cls):
        if hashes(cls.source)!=cls.before:raise AssertionError('Original evidence was changed')
        if hashes(cls.record/'work')!=cls.inputs:raise AssertionError('Native inputs/results were changed')
        if set((cls.workspace/'.mc-jobs').iterdir())!=cls.original_jobs:raise AssertionError('Unexpected new job')

    async def journey(self, score, view):
        async with streamablehttp_client(os.environ['MC_TEST_MCP_URL']) as (read,write,_):
            async with ClientSession(read,write) as session:
                await check_discovery(session)
                async def call(name,args):
                    r=await session.call_tool(name,args);self.assertFalse(r.isError,str(r))
                    return r.structuredContent or json.loads(r.content[0].text)
                bundle=await call('openmc_generate_result_bundle',{'job_id':self.job,'tally_id':1,'score':score,'view':view})
                summary=bundle['delivery_summary']
                self.assertEqual(summary,(self.workspace/bundle['delivery_file']).read_text().rstrip('\n'))
                for key in ['chart','data_json','data_csv','plot_script','manifest','delivery_file']:
                    path=self.workspace/bundle[key]
                    self.assertTrue(path.is_file());self.assertIn(str(path),summary)
                for r in bundle['reports']:self.assertIn(str(self.workspace/r['path']),summary)
                self.assertIn('MCP服务器完整路径',summary);self.assertIn('尚未下载',summary)
                self.assertIn('未换算功率或实际源强',summary);self.assertIn('未作专业验收',summary)
                self.assertIn(bundle['normalization'],summary);self.assertIn(bundle['source']['sha256'],summary)
                for warning in bundle['warnings']:self.assertIn(warning,summary)
                if score=='fission' and view=='mesh':self.assertIn('不能判断物理量',summary)
                self.assertIn('particle-cm/source' if score=='flux' else 'reactions/source',summary)
                self.assertNotIn('particle/cm²/source',summary)
                # Native source identity and values are preserved by the old result tools.
                manifest=json.loads((self.workspace/bundle['manifest']).read_text())
                self.assertEqual(manifest['files']['delivery.md']['sha256'],hashlib.sha256((summary+'\n').encode()).hexdigest())
                status=await call('openmc_job_status',{'job_id':self.job})
                self.assertEqual(status['state'],'completed')
                download=await call('openmc_prepare_download',{'kind':'job','identifier':self.job,'paths':[bundle['delivery_file']]})
                blob=bytearray()
                while len(blob)<download['size_bytes']:
                    part=await call('openmc_download_chunk',{'transfer_id':download['transfer_id'],'offset':len(blob)})
                    data=base64.b64decode(part['data_base64']);self.assertTrue(data);blob.extend(data)
                self.assertEqual(hashlib.sha256(blob).hexdigest(),download['sha256'])
                # A single text file is downloaded directly; only multi-file
                # selections/report dependencies use ZIP in the existing API.
                self.assertEqual(bytes(blob),(summary+'\n').encode())
                evidence=Path(os.environ['MC_TEST_SESSION_DIR'])/(score+'-'+view+'-delivery.json')
                evidence.write_text(json.dumps(bundle,ensure_ascii=False,indent=2))

    async def test_flux_mesh_complete_paths_units_and_download(self):await self.journey('flux','mesh')
    async def test_fission_mesh_preserves_uncertain_zero_warning(self):await self.journey('fission','mesh')
    async def test_selected_series_shares_delivery_contract(self):await self.journey('flux','series')

class DeliveryUnknownUnit(unittest.TestCase):
    def test_unknown_score_does_not_invent_unit_or_zero_diagnosis(self):
        import sys
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'mcp'))
        from result_bundle import delivery_summary
        message=delivery_summary(Path('/srv/a space/模型'),'job',{'score':'custom-score'},'raw normalization','abc',
                                 [('数据','values.csv')],['零值原因未核实'])
        self.assertIn('/srv/a space/模型/values.csv',message)
        self.assertIn('未在本摘要中定义',message);self.assertIn('零值原因未核实',message)
        self.assertNotIn('reactions/source',message)
