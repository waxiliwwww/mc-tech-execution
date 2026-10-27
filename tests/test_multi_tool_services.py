"""Use generated services and mixed MC adapters through the actual MCP protocol."""
import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
import urllib.request

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

ROOT=Path(__file__).resolve().parents[1]


def port():
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


@contextmanager
def running_service(folder,command,extra=None):
    selected=port();url=f'http://127.0.0.1:{selected}/mcp'
    env={k:v for k,v in os.environ.items() if not k.startswith(('MC_','MCP_','HARNESS_'))}
    env.update(MCP_PORT=str(selected),MCP_CONNECTION_FILE=str(folder/'test-connection.json'),**(extra or {}))
    with (folder/'test-server.log').open('w+') as log:
        process=subprocess.Popen(command,cwd=folder,env=env,stdout=log,stderr=log,start_new_session=True)
        try:
            deadline=time.monotonic()+150
            while True:
                try:urllib.request.urlopen(url.replace('/mcp','/health'),timeout=.3).close();break
                except OSError:
                    if process.poll() is not None or time.monotonic()>deadline:
                        log.seek(0);raise AssertionError('Service startup failed: '+log.read()[-4000:])
                    time.sleep(.1)
            yield url
        finally:
            try:os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid,signal.SIGKILL);process.wait()


async def inspect_and_call(url,names,key,marker=None):
    async with streamablehttp_client(url) as (read,write,_):
        async with ClientSession(read,write) as session:
            await session.initialize();tools=(await session.list_tools()).tools
            assert {tool.name for tool in tools}==set(names)
            for tool in tools:
                assert all(field.get('description') for field in tool.inputSchema['properties'].values())
            bad=await session.call_tool(names[1],{'a':'wrong','b':23});assert bad.isError
            if marker is not None:assert not marker.exists(),'Invalid input started the wrapped program'
            values=[]
            for name in names:
                result=await session.call_tool(name,{'a':20,'b':23});assert not result.isError,str(result)
                values.append(result.structuredContent[key])
            return values


class GeneratedServices(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='mc multi tools ');self.folder=Path(self.temp.name)
        self.evidence=ROOT/'artifacts/runs/20261027-downstream-flow';self.evidence.mkdir(parents=True,exist_ok=True)

    def tearDown(self):self.temp.cleanup()

    def generate(self,language):
        target=self.folder/'created service'
        run=subprocess.run([str(ROOT/'create-mcp.sh'),str(target),'--language',language],capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stderr)
        moved=self.folder/'moved service';target.rename(moved);return moved

    def test_python_custom_function_and_non_json_cli_survive_packaging(self):
        folder=self.generate('python')
        (folder/'business.py').write_text('def add(a,b):\n    return {"result":a*b}\n')
        tools=folder/'tools.py';text=tools.read_text().replace('"name": "add",','"name": "multiply",').replace('"name": "add_program",','"name": "sum_program",')
        text=text.replace('Add two finite numbers using a Python business function.','Multiply two finite numbers; result is their product.')
        tools.write_text(text)
        (folder/'programs/bare_cli.py').write_text('import sys\nprint(float(sys.argv[1])+float(sys.argv[2]))\n')
        (folder/'programs/add_json.py').write_text('''import json,sys,subprocess
from pathlib import Path
values=json.load(sys.stdin)
Path('program-invoked').write_text('actual external program launched')
result=subprocess.run([sys.executable,str(Path(__file__).with_name('bare_cli.py')),str(values['a']),str(values['b'])],check=True,capture_output=True,text=True,timeout=5)
print(json.dumps({'result':float(result.stdout)}))
''')
        names=['multiply','sum_program']
        with running_service(folder,['./start.sh']) as url:
            self.assertEqual(asyncio.run(inspect_and_call(url,names,'result',folder/'program-invoked')),[460,43])
            connection=json.loads((folder/'test-connection.json').read_text());self.assertEqual(connection[0]['url'],url)
        packed=subprocess.run(['./package.sh'],cwd=folder,capture_output=True,text=True,timeout=60)
        self.assertEqual(packed.returncode,0,packed.stderr)
        archive=folder/'dist/mcp-toolkit-python.tar.gz';unpacked=self.folder/'unpacked service';unpacked.mkdir()
        with tarfile.open(archive) as bundle:
            self.assertIn('programs/bare_cli.py',bundle.getnames());bundle.extractall(unpacked,filter='data')
        with running_service(unpacked,['./start.sh']) as url:
            self.assertEqual(asyncio.run(inspect_and_call(url,names,'result',unpacked/'program-invoked')),[460,43])
        (self.evidence/'generated-python.json').write_text(json.dumps({'custom_function':460,'bare_cli_via_adapter':43,'descriptions_discovered':True,'invalid_input_did_not_launch_program':True,'moved_and_packaged_independent_service':True},indent=2))

    @unittest.skipUnless(shutil.which('node') and shutil.which('corepack'),'生成的TypeScript服务验证需Node22/Corepack')
    def test_typescript_function_and_program_are_described_and_callable(self):
        folder=self.generate('typescript')
        with running_service(folder,['./start.sh']) as url:
            self.assertEqual(asyncio.run(inspect_and_call(url,['add','add_program'],'sum')),[43,43])
        packed=subprocess.run(['./package.sh'],cwd=folder,capture_output=True,text=True,timeout=150)
        self.assertEqual(packed.returncode,0,packed.stderr[-4000:])
        archive=next((folder/'dist').glob('*.tar.gz'));unpacked=self.folder/'unpacked ts';unpacked.mkdir()
        with tarfile.open(archive) as bundle:
            bundle.extractall(unpacked,filter='data')
        deployment=next(p.parent for p in unpacked.rglob('server.mjs'))
        with running_service(deployment,['./start.sh']) as url:
            self.assertEqual(asyncio.run(inspect_and_call(url,['add','add_program'],'sum')),[43,43])
        (self.evidence/'generated-typescript.json').write_text(json.dumps({'function':43,'program':43,'descriptions_discovered':True,'source_and_packaged_service':True},indent=2))

    def test_mc_registration_serves_async_function_program_and_real_http(self):
        calls=[]
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                calls.append(data);body=json.dumps({'result':data['a']+data['b']}).encode()
                self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(body)
            def log_message(self,*args):pass
        http=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        driver=self.folder/'mixed.py'
        driver.write_text('''import sys
sys.path.insert(0,sys.argv[1])
import tools
async def add(a,b):return {'result':a+b}
inputs={'type':'object','properties':{'a':{'type':'number','description':'First operand'},'b':{'type':'number','description':'Second operand'}},'required':['a','b'],'additionalProperties':False}
base={'description':'Calculate a plus b','inputSchema':inputs,'outputSchema':{'type':'object'}}
tools.TOOLS=tools.prepare_tools([{**base,'name':'function','function':add},
 {**base,'name':'program','program':{'argv':[sys.executable,'-c',"import json,sys;v=json.load(sys.stdin);print(json.dumps({'result':v['a']+v['b']}))"]}},
 {**base,'name':'http','http':{'url':sys.argv[2]}}])
import server
raise SystemExit(server.main())
''')
        config=self.folder/'service.json';config.write_text(json.dumps({'host':'127.0.0.1','port':8770,'path':'/mcp','name':'mixed','version':'1.0.0','clientName':'mixed'}))
        async def run(url):
            async with streamablehttp_client(url) as (read,write,_):
                async with ClientSession(read,write) as session:
                    await session.initialize();self.assertEqual(len((await session.list_tools()).tools),3)
                    for name in ['function','program','http']:
                        result=await session.call_tool(name,{'a':20,'b':23});self.assertFalse(result.isError,str(result));self.assertEqual(result.structuredContent,{'result':43})
                    before=len(calls);bad=await session.call_tool('http',{'a':'invalid','b':23});self.assertTrue(bad.isError);self.assertEqual(len(calls),before)
        try:
            with running_service(self.folder,[sys.executable,str(driver),str(ROOT/'mcp'),f'http://127.0.0.1:{http.server_port}/calculate'],{'MCP_CONFIG_FILE':str(config)}) as url:
                asyncio.run(run(url))
        finally:http.shutdown();http.server_close();thread.join(timeout=5)
        self.assertEqual(calls,[{'a':20,'b':23}])
