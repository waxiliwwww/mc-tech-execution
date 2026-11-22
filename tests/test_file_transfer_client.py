"""Host helper uses real byte functions; inject lost replies, not fake storage."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'mcp'));sys.path.insert(0,str(ROOT/'examples'))
import business
import file_transfer as ft
import transfer_files as host

class FileTransferClient(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.work=self.root/'server';self.work.mkdir();self.addCleanup(patch.stopall);patch.object(business,'WORKSPACE',self.work).start()
        cfg=self.root/'cfg.json';cfg.write_text(json.dumps({'chunk_bytes':64,'min_free_disk_bytes':0}));patch.dict(os.environ,{'MC_TRANSFER_CONFIG':str(cfg)}).start()
        self.registry={row['name']:row['function'] for row in ft.TRANSFER_TOOLS}
    async def call(self,name,args):return self.registry[name](**args)
    def uargs(self):
        return argparse.Namespace(command='upload',mcp_url='http://test/mcp',file=str(self.root/'source.txt'),archive=False,receipt=None)
    def dargs(self,done):
        return argparse.Namespace(command='download',mcp_url='http://test/mcp',kind='upload',identifier=done['transfer_id'],paths=[done['file_path']],output=str(self.root/'result.txt'),receipt=None)

    async def test_upload_lost_response_resumes_same_id_then_download_lost_response(self):
        raw=b'0123456789'*40;args=self.uargs();Path(args.file).write_bytes(raw);lost=False
        async def drop(name,data):
            nonlocal lost
            result=await self.call(name,data)
            if name=='openmc_upload_chunk' and not lost:lost=True;raise ConnectionError('reply lost after append')
            return result
        with self.assertRaises(ConnectionError):await host.upload(drop,args)
        done=await host.upload(self.call,args)
        ids=list((self.work/'.mc-transfers/uploads').iterdir());self.assertEqual(len(ids),1)
        self.assertEqual((self.work/done['file_path']).read_bytes(),raw)
        dargs=self.dargs(done);count=0
        async def drop_download(name,data):
            nonlocal count
            result=await self.call(name,data)
            if name=='openmc_download_chunk':
                count+=1
                if count==2:raise ConnectionError('read reply lost')
            return result
        with self.assertRaises(ConnectionError):await host.download(drop_download,dargs)
        self.assertEqual(Path(dargs.output+'.part').stat().st_size,64);self.assertFalse(Path(dargs.output).exists())
        await host.download(self.call,dargs);self.assertEqual(Path(dargs.output).read_bytes(),raw)
        self.assertFalse(Path(dargs.output+'.part').exists())
        self.assertEqual(len(list((self.work/'.mc-transfers/downloads').iterdir())),1)
        again=await host.download(self.call,dargs);self.assertIn('重复',again['message'])

    async def test_bad_prefix_and_existing_destination_are_preserved(self):
        args=self.uargs();Path(args.file).write_bytes(b'x'*100);done=await host.upload(self.call,args);dargs=self.dargs(done)
        Path(dargs.output+'.part').write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError,'前缀'):await host.download(self.call,dargs)
        self.assertEqual(Path(dargs.output+'.part').read_bytes(),b'bad');self.assertFalse(Path(dargs.output).exists())
        Path(dargs.output).write_bytes(b'my-original')
        with self.assertRaisesRegex(ValueError,'未覆盖'):await host.download(self.call,dargs)
        self.assertEqual(Path(dargs.output).read_bytes(),b'my-original')

    async def test_local_receipt_concurrency_is_rejected_and_released(self):
        args=self.uargs()
        with host.local_lock(args):
            with self.assertRaisesRegex(ValueError,'已有传输'): 
                with host.local_lock(args):pass
        with host.local_lock(args):pass

    async def test_same_receipt_cannot_cross_server_or_change_source(self):
        args=self.uargs();Path(args.file).write_bytes(b'first');await host.upload(self.call,args)
        args.mcp_url='http://different/mcp'
        with self.assertRaisesRegex(ValueError,'不同'):await host.upload(self.call,args)
        args.mcp_url='http://test/mcp';Path(args.file).write_bytes(b'second')
        with self.assertRaisesRegex(ValueError,'不同'):await host.upload(self.call,args)

    async def test_local_file_remains_delivered_when_only_close_acknowledgement_is_lost(self):
        args=self.uargs();raw=b'actual local bytes';Path(args.file).write_bytes(raw)
        uploaded=await host.upload(self.call,args);download=self.dargs(uploaded)
        async def lose_close(name,data):
            if name=='openmc_close_transfer':raise ConnectionError('close acknowledgement lost')
            return await self.call(name,data)
        result=await host.download(lose_close,download)
        self.assertTrue(result['local_file_verified']);self.assertEqual(result['transfer_cleanup']['state'],'pending')
        self.assertEqual(Path(result['file']).read_bytes(),raw)
        repeated=await host.download(self.call,download)
        self.assertTrue(repeated['local_file_verified']);self.assertEqual(repeated['transfer_cleanup']['state'],'closed')
        self.assertEqual(Path(result['file']).read_bytes(),raw)


    async def test_legacy_receipt_rehash_is_offline_and_keeps_server_snapshot(self):
        args=self.uargs();raw=b'legacy real file';Path(args.file).write_bytes(raw)
        uploaded=await host.upload(self.call,args);download=self.dargs(uploaded)
        done=await host.download(self.call,download)
        rp=Path(done['receipt']);legacy=json.loads(rp.read_text())
        legacy.pop('host_verification');legacy.pop('transfer_cleanup')
        legacy['result']['local_file_verified']=False
        host.save(rp,legacy);snapshot=legacy['result'].copy()
        verified=host.verify_download_receipt(argparse.Namespace(receipt=str(rp)))
        expected=hashlib.sha256(raw).hexdigest()
        self.assertTrue(verified['local_file_verified'])
        self.assertEqual(verified['sha256'],expected)
        self.assertIn('本机完整路径：'+str(Path(download.output).resolve()),verified['delivery_summary'].splitlines())
        self.assertIn('SHA256：'+expected,verified['delivery_summary'].splitlines())
        saved=json.loads(rp.read_text())
        self.assertEqual(saved['result'],snapshot)
        self.assertEqual(saved['host_verification']['sha256'],expected)
        self.assertEqual(verified['transfer_cleanup']['state'],'not_checked')
        before=rp.read_bytes()
        Path(download.output).write_bytes(b'legacy real fail')  # Same length; stale host flag must not pass.
        with self.assertRaisesRegex(ValueError,'SHA256'):
            host.verify_download_receipt(argparse.Namespace(receipt=str(rp)))
        self.assertEqual(rp.read_bytes(),before)

    async def test_host_proof_is_durable_even_when_close_reply_is_lost(self):
        args=self.uargs();Path(args.file).write_bytes(b'persist proof')
        uploaded=await host.upload(self.call,args);download=self.dargs(uploaded)
        async def lose_close(name,data):
            if name=='openmc_close_transfer':raise ConnectionError('lost acknowledgement')
            return await self.call(name,data)
        done=await host.download(lose_close,download)
        saved=json.loads(Path(done['receipt']).read_text())
        self.assertTrue(saved['host_verification']['local_file_verified'])
        self.assertEqual(saved['host_verification']['sha256'],done['sha256'])
        self.assertEqual(saved['transfer_cleanup']['state'],'pending')
