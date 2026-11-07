"""Fresh standard MCP process: cached schema must not mix with changed disk input.

Uses a disposable source copy, real HTTP/MCP and actual file edits/permissions.
No OpenMC or model call required. Positive monitoring/cancellation, negative
new-model execution. All permissions restored, process and fixtures removed.
"""
import asyncio,json,os,shutil,socket,subprocess,sys,tempfile,unittest,urllib.request
from pathlib import Path
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
ROOT=Path(__file__).resolve().parents[1]

class SchemaDriftMCP(unittest.IsolatedAsyncioTestCase):
    async def test_schema_only_change_and_unreadable_identity_keep_query_cancel_available(self):
        with tempfile.TemporaryDirectory(prefix='mc schema drift ') as directory:
            root=Path(directory);shutil.copytree(ROOT/'mcp',root/'mcp',ignore=shutil.ignore_patterns('.venv','logs','connection.json','__pycache__'))
            (root/'VERSION').write_text('1.2.3');(root/'config').mkdir();shutil.copy2(ROOT/'config/openmc.json',root/'config/openmc.json')
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            env={k:v for k,v in os.environ.items() if not k.startswith(('MC_','MCP_','HARNESS_'))};env.update(MCP_PORT=str(port),MC_WORKSPACE=str(root/'workspace'))
            log=(root/'server.log').open('w');process=subprocess.Popen([sys.executable,str(root/'mcp/server.py')],cwd=root/'mcp',env=env,stdout=log,stderr=log)
            schema=root/'mcp/model_validation/schema.json'
            try:
                for _ in range(120):
                    try:
                        with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=.2):break
                    except OSError:
                        if process.poll() is not None:self.fail('isolated service exited')
                        await asyncio.sleep(.05)
                else:self.fail('isolated service startup timeout')
                async with streamablehttp_client(f'http://127.0.0.1:{port}/mcp') as (read,write,_):
                    async with ClientSession(read,write) as session:
                        await session.initialize();await session.list_tools()
                        schema.write_text(schema.read_text()+'\n')
                        for mode in ("changed", "unreadable_file", "unreadable_directory"):
                            inaccessible = mode != "changed"
                            if inaccessible:
                                if os.name=='nt' or (hasattr(os,'geteuid') and os.geteuid()==0):continue
                                (schema.parent if mode == "unreadable_directory" else schema).chmod(0)
                            try:
                                result=await session.call_tool('openmc_build_model',{'case_dir':'denied','spec':{}})
                                self.assertTrue(result.isError);self.assertIn('已阻止新操作',result.content[0].text)
                                self.assertFalse((root/'workspace/denied').exists())
                                query=await session.call_tool('openmc_list_jobs',{});self.assertFalse(query.isError)
                                cancelled=await session.call_tool('openmc_cancel_job',{'job_id':'not-a-job'})
                                self.assertNotIn('已阻止新操作',str(cancelled))
                                environment=await session.call_tool('openmc_environment',{});self.assertFalse(environment.isError)
                                data=environment.structuredContent or json.loads(environment.content[0].text)
                                self.assertTrue(data['service']['restart_required']);self.assertFalse(data['service']['ready_for_new_operations'])
                                if inaccessible:self.assertEqual(data['service']['identity_check'],'unavailable')
                                else:self.assertIn('mcp/model_validation/schema.json',data['service']['changed_files'])
                            finally:
                                schema.parent.chmod(0o755);schema.chmod(0o644)
            finally:
                process.terminate()
                try:process.wait(8)
                except subprocess.TimeoutExpired:process.kill();process.wait()
                log.close()
if __name__=='__main__':unittest.main()
