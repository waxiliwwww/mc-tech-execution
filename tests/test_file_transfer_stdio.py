"""Real stdio sessions and host CLI: ZIP bytes, interruption, resume and SHA."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'mcp'))
import host_transfer as host

class FileTransferStdio(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='stdio download 中文 ')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.workspace=self.root/'server';self.workspace.mkdir()
        self.config=self.root/'mcp.json'
        env={k:'' for k in os.environ if k.startswith(('MC_','MCP_'))}
        env.update(MC_WORKSPACE=str(self.workspace),MC_PERSONAL_CONFIG=str(self.root/'personal.json'))
        self.config.write_text(json.dumps({'mcpServers':{'openmc':{'command':sys.executable,
            'args':[str(ROOT/'mcp/stdio_service.py')],'cwd':str(ROOT),'env':env}}}))
        self.stdio,self.identity=host.stdio_connection(self.config)
        policy=self.root/'transfer.json';policy.write_text(json.dumps({'chunk_bytes':64,'min_free_disk_bytes':0}))
        self.stdio['env']['MC_TRANSFER_CONFIG']=str(policy)
        value=json.loads(self.config.read_text());value['mcpServers']['openmc']['env']['MC_TRANSFER_CONFIG']=str(policy)
        self.config.write_text(json.dumps(value))
        self.stdio,self.identity=host.stdio_connection(self.config)
    def download_args(self,transfer):
        return argparse.Namespace(mcp_url=None,stdio_identity=self.identity,transfer_id=transfer,
            kind=None,identifier=None,paths=[],output=str(self.root/'host result.zip'),receipt=None)
    async def test_real_stdio_zip_interrupted_then_cli_resumes_same_transfer(self):
        source=self.root/'source.zip';raw=('真实文件 content '*100).encode()
        with zipfile.ZipFile(source,'w') as z:z.writestr('source.txt',raw)
        args=argparse.Namespace(mcp_url=None,stdio_identity=self.identity,file=str(source),archive=False,receipt=None)
        async with host.client(None,self.stdio) as call:
            uploaded=await host.upload(call,args)
            prepared=await call('openmc_prepare_download',{'kind':'upload','identifier':uploaded['transfer_id'],
                'paths':[uploaded['file_path']]})
            self.assertIn('--stdio-config',prepared['host_action']['stdio_argv'])
            download=self.download_args(prepared['transfer_id'])
            chunks=0
            async def lose_reply(name,data):
                nonlocal chunks
                result=await call(name,data)
                if name=='openmc_download_chunk':
                    chunks+=1
                    if chunks==2:raise ConnectionError('controlled lost reply after real read')
                return result
            with self.assertRaises(ConnectionError):await host.download(lose_reply,download)
        part=Path(download.output+'.part');self.assertEqual(part.stat().st_size,64)
        self.assertFalse(Path(download.output).exists())
        import asyncio
        wheel=os.environ.get('MC_TEST_WORKFLOW_WHEEL')
        env=os.environ.copy()
        if wheel:
            site=self.root/'installed'
            installed=await asyncio.to_thread(subprocess.run,[sys.executable,'-m','pip','install','--no-deps','--target',str(site),wheel],capture_output=True,text=True,timeout=60)
            self.assertEqual(installed.returncode,0,installed.stderr)
            command=[sys.executable,'-c','from mc_agent_tools.launcher import transfer;raise SystemExit(transfer())']
            env['PYTHONPATH']=str(site)
        else:command=[sys.executable,str(ROOT/'examples/transfer_files.py')]
        command+=['--stdio-config',str(self.config),'receive',prepared['transfer_id'],download.output]
        result=await asyncio.to_thread(subprocess.run,command,cwd=self.root,env=env,capture_output=True,text=True,timeout=40)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        done=json.loads(result.stdout)
        self.assertTrue(done['local_file_verified'])
        target=Path(done['file']);self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(),done['sha256'])
        with zipfile.ZipFile(target) as z:
            self.assertEqual(len(z.namelist()),1);self.assertEqual(z.read(z.namelist()[0]),raw)
        receipt=json.loads(Path(done['receipt']).read_text())
        self.assertEqual(receipt['transfer_id'],prepared['transfer_id'])
        self.assertFalse(part.exists())
        self.assertEqual(len(list((self.workspace/'.mc-transfers/downloads').iterdir())),1)
        # Changing connection settings must not let an old receipt target a different server.
        download.stdio_identity='stdio:different'
        with self.assertRaisesRegex(ValueError,'不同'):
            await host.download(None,download)
    async def test_multiple_servers_require_choice_and_receipts_do_not_expose_environment(self):
        value=json.loads(self.config.read_text());value['mcpServers']['other']=value['mcpServers']['openmc']
        value['mcpServers']['openmc']['env']['PRIVATE_TEST_TOKEN']='test-secret'
        self.config.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,'多个'):host.stdio_connection(self.config)
        params,identity=host.stdio_connection(self.config,'openmc')
        self.assertEqual(params['env']['PRIVATE_TEST_TOKEN'],'test-secret')
        self.assertNotIn('test-secret',identity)
        with self.assertRaisesRegex(ValueError,'不存在'):host.stdio_connection(self.config,'missing')
