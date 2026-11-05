"""Version at configuration and HTTP boundaries comes from one source, fixed at startup."""
import asyncio,json,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'mcp'))
import server
DEFAULTS={'host':'127.0.0.1','port':8770,'path':'/mcp','name':'test','clientName':'mc'}
class ServiceVersion(unittest.TestCase):
    def test_source_default_uses_current_repository_version(self):
        self.assertEqual(server.load_config(env={}).version,(ROOT/'VERSION').read_text().strip())
    def test_unrelated_installed_metadata_cannot_mask_missing_source_version(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)/'mcp';folder.mkdir()
            (folder/'service.config.json').write_text(json.dumps(dict(DEFAULTS,versionFile='../VERSION')))
            unrelated=SimpleNamespace(version='old',locate_file=lambda _:Path('/another/install/mc_agent_tools/server.py'))
            with patch.object(server,'ROOT',folder),patch('importlib.metadata.distribution',return_value=unrelated):
                with self.assertRaisesRegex(ValueError,'does not belong'):server.load_config(env={})
    def test_version_file_is_relative_to_configuration_and_precedes_stale_literal(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);cfg=root/'config/service.json';cfg.parent.mkdir();v=root/'VERSION';v.write_text('9.8.7\n')
            cfg.write_text(json.dumps(dict(DEFAULTS,version='old',versionFile='../VERSION')))
            config=server.load_config(env={'MCP_CONFIG_FILE':str(cfg)})
            self.assertEqual(config.version,'9.8.7')
            # Source changing after startup must not make an old process pretend to be updated.
            v.write_text('9.8.8\n');self.assertEqual(config.version,'9.8.7')
            app=server.create_app(config,None,tools=[])
            async def request(route):
                output=[]
                async def receive():return {'type':'http.request','body':b''}
                async def send(value):output.append(value)
                await app({'type':'http','method':'GET','path':route,'headers':[(b'host',b'127.0.0.1:8770')],
                           'scheme':'http','server':('127.0.0.1',8770),'client':('127.0.0.1',1),'query_string':b''},receive,send)
                return json.loads(b''.join(x.get('body',b'') for x in output))
            self.assertEqual(asyncio.run(request('/health'))['version'],'9.8.7')
            self.assertEqual(asyncio.run(request('/info'))['service']['version'],'9.8.7')
            v.unlink()
            with self.assertRaisesRegex(ValueError,'versionFile'):server.load_config(env={'MCP_CONFIG_FILE':str(cfg)})
if __name__=='__main__':unittest.main()
