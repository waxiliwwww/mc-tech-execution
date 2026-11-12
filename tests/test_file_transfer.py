"""Real filesystem byte protocol: complete handoff, resume, limits and failures.
No fake uploader/downloader, no external service and no models.
"""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'mcp'))
import business
import file_transfer as ft
from openmc_runtime import Error


def sha(b):return hashlib.sha256(b).hexdigest()

def encoded(b):return base64.b64encode(b).decode()


class FileTransfer(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='mc transfer ');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.work=self.root/'workspace';self.work.mkdir()
        self.addCleanup(patch.stopall);patch.object(business,'WORKSPACE',self.work).start()
        self.cfg=self.root/'transfer.json';self.write_config()
        patch.dict(os.environ,{'MC_TRANSFER_CONFIG':str(self.cfg)}).start()

    def write_config(self,**changes):
        self.cfg.write_text(json.dumps({'min_free_disk_bytes':0,**changes}))

    def begin(self,data,name='input.txt',archive=False,**kw):return ft.upload_begin(name,len(data),sha(data),archive,**kw)
    def upload(self,data,name='input.txt',archive=False):
        s=self.begin(data,name,archive);tid=s['transfer_id']
        for n in range(0,len(data),s['chunk_bytes']):ft.upload_chunk(tid,n,encoded(data[n:n+s['chunk_bytes']]))
        return ft.upload_finish(tid)
    def zip(self,entries):
        b=io.BytesIO()
        with zipfile.ZipFile(b,'w',zipfile.ZIP_DEFLATED) as z:
            for name,value in entries:z.writestr(name,value)
        return b.getvalue()
    def read_download(self,s):
        result=b''
        while len(result)<s['size_bytes']:
            row=ft.download_chunk(s['transfer_id'],len(result));part=base64.b64decode(row['data_base64'])
            self.assertEqual(sha(part),row['chunk_sha256']);result+=part
        self.assertEqual(sha(result),s['sha256']);return result

    def test_raw_roundtrip_preview_and_close_preserve_published(self):
        raw='中文输入\n'.encode()*200;done=self.upload(raw)
        preview=ft.preview_file('upload',done['transfer_id'],done['file_path'],max_bytes=11)
        self.assertTrue(preview['truncated']);self.assertLessEqual(preview['next_offset'],11)
        s=ft.prepare_download('upload',done['transfer_id'],[done['file_path']])
        self.assertEqual(self.read_download(s),raw)
        ft.close_transfer('download',s['transfer_id']);self.assertFalse((ft._dir('downloads',s['transfer_id'])/'data.bin').exists())
        self.assertTrue(ft.close_transfer('upload',done['transfer_id'])['state']=='complete')
        self.assertEqual((self.work/done['file_path']).read_bytes(),raw)
        with self.assertRaises(Error):ft.download_chunk(s['transfer_id'],0)

    def test_zip_root_relative_assets_and_multiple_download_paths(self):
        raw=self.zip([('model/geometry.xml','<geometry/>'),('model/materials.xml','<materials/>'),('model/data/x.h5',b'asset')])
        done=self.upload(raw,'model.zip',True);self.assertTrue(done['source_dir'].endswith('/model'))
        self.assertEqual((self.work/done['source_dir']/'data/x.h5').read_bytes(),b'asset')
        s=ft.prepare_download('upload',done['transfer_id'],[f['path'] for f in done['files']])
        with zipfile.ZipFile(io.BytesIO(self.read_download(s))) as z:
            self.assertEqual(z.read('model/data/x.h5'),b'asset')

    def test_resume_identical_chunk_and_lost_begin_finish_acknowledgements(self):
        data=b'abcd'*100;key=uuid.uuid4().hex;s=self.begin(data,request_id=key);tid=s['transfer_id']
        ft.upload_chunk(tid,0,encoded(data[:113]))
        same=self.begin(data,request_id=key);self.assertEqual(same['received_bytes'],113);self.assertEqual(same['prefix_sha256'],sha(data[:113]))
        self.assertTrue(ft.upload_chunk(tid,0,encoded(data[:113]))['duplicate'])
        ft.upload_chunk(tid,113,encoded(data[113:]));done=ft.upload_finish(tid)
        self.assertEqual(ft.upload_finish(tid)['file_path'],done['file_path'])
        self.assertEqual(self.begin(data,request_id=key)['state'],'complete')
        with self.assertRaises(Error):self.begin(data+b'x',request_id=key)
        # Simulate a crash after publication, before recording complete.
        d=ft._dir('uploads',tid);r=ft._read(d);r['state']='receiving';ft.atomic_json(d/'record.json',r)
        self.assertEqual(ft.upload_finish(tid)['state'],'complete')

    def test_incomplete_conflicting_or_gapped_chunks_never_publish(self):
        data=b'abcdefgh';s=self.begin(data);tid=s['transfer_id']
        with self.assertRaises(Error):ft.upload_finish(tid)
        ft.upload_chunk(tid,0,encoded(data[:4]))
        for offset,block in ((0,b'xxxx'),(3,b'def'),(6,b'gh'),(4,b'efghi')):
            with self.subTest(offset=offset,block=block),self.assertRaises(Error):ft.upload_chunk(tid,offset,encoded(block))
        self.assertEqual(ft.upload_status(tid)['received_bytes'],4);self.assertFalse((self.work/'inbox'/tid).exists())
        ft.close_transfer('upload',tid);self.assertFalse((ft._dir('uploads',tid)/'data.part').exists())
        self.assertFalse(ft.close_transfer('upload',tid)['changed'])

    def test_wrong_full_hash_does_not_publish(self):
        s=ft.upload_begin('bad.txt',4,sha(b'good'));ft.upload_chunk(s['transfer_id'],0,encoded(b'evil'))
        with self.assertRaisesRegex(Error,'SHA256'):ft.upload_finish(s['transfer_id'])
        self.assertFalse((self.work/'inbox'/s['transfer_id']).exists())

    def test_default_5m_boundary_and_chunks_cannot_bypass(self):
        maximum=5*1024**2;data=b'a'*maximum
        done=self.upload(data);self.assertEqual((self.work/done['file_path']).stat().st_size,maximum)
        with self.assertRaisesRegex(Error,'上限'):self.begin(data+b'a')
        s=ft.upload_begin('tiny.txt',1,sha(b'a'))
        with self.assertRaises(Error):ft.upload_chunk(s['transfer_id'],0,encoded(b'ab'))
        self.write_config(max_file_bytes=maximum+1,max_package_bytes=maximum+1)
        self.assertEqual(self.begin(data+b'a')['size_bytes'],maximum+1)
        # Changing transport limit does not remove or restrict existing scientific inputs.
        self.write_config(max_file_bytes=10)
        self.assertEqual(ft.preview_file('upload',done['transfer_id'],done['file_path'])['preview_type'],'text')
        with self.assertRaises(Error):ft.prepare_download('upload',done['transfer_id'],[done['file_path']])
        self.assertEqual((self.work/done['file_path']).stat().st_size,maximum)

    def test_zip_rejects_path_families_and_links_without_partial_publication(self):
        for name in ('../escape','/absolute','C:/drive','..\\escape','dir/../../escape'):
            with self.subTest(name=name):
                s=self.begin(self.zip([(name,'bad')]),'bad.zip',True);ft.upload_chunk(s['transfer_id'],0,encoded(self.zip([(name,'bad')])))
                with self.assertRaises(Error):ft.upload_finish(s['transfer_id'])
                self.assertFalse((self.work/'inbox'/s['transfer_id']).exists())
        link=zipfile.ZipInfo('symlink');link.create_system=3;link.external_attr=(stat.S_IFLNK|0o777)<<16
        for entries in ([(link,'../elsewhere')],[('same','a'),('same','b')],[('a','file'),('a/b','child')]):
            raw=self.zip(entries);s=self.begin(raw,'bad.zip',True);ft.upload_chunk(s['transfer_id'],0,encoded(raw))
            with self.assertRaises(Error):ft.upload_finish(s['transfer_id'])
            self.assertFalse((self.work/'inbox'/s['transfer_id']).exists())

    def test_zip_actual_expansion_and_filecount_limits(self):
        self.write_config(max_file_bytes=1024,max_package_bytes=100)
        for entries in ([('a',b'a'*101)],[('a',b'a'*60),('b',b'b'*60)]):
            raw=self.zip(entries);s=self.begin(raw,'bomb.zip',True);ft.upload_chunk(s['transfer_id'],0,encoded(raw))
            with self.assertRaisesRegex(Error,'总量'):ft.upload_finish(s['transfer_id'])
        self.write_config(max_archive_files=1)
        raw=self.zip([('a','a'),('b','b')]);s=self.begin(raw,'two.zip',True);ft.upload_chunk(s['transfer_id'],0,encoded(raw))
        with self.assertRaisesRegex(Error,'数量'):ft.upload_finish(s['transfer_id'])

    def test_quota_config_and_cancel_release_capacity(self):
        self.write_config(max_staging_bytes=4,max_pending_sessions=1)
        s=self.begin(b'four')
        with self.assertRaisesRegex(Error,'限额'):self.begin(b'a')
        ft.close_transfer('upload',s['transfer_id']);self.begin(b'a')
        self.cfg.write_text('{"unknown":5}')
        with self.assertRaisesRegex(Error,'未知'):ft.file_limits()

    def test_snapshot_stable_resume_prefix_and_lost_prepare_ack(self):
        raw=b'abcdefgh'*100;done=self.upload(raw);key=uuid.uuid4().hex
        s=ft.prepare_download('upload',done['transfer_id'],[done['file_path']],key)
        first=ft.download_chunk(s['transfer_id'],0,17);prefix=base64.b64decode(first['data_base64'])
        self.assertEqual(ft.download_status(s['transfer_id'],17)['prefix_sha256'],sha(prefix))
        self.assertEqual(ft.prepare_download('upload',done['transfer_id'],[done['file_path']],key)['sha256'],sha(raw))
        (self.work/done['file_path']).write_bytes(b'changed')
        self.assertEqual(self.read_download(s),raw)  # download immutable snapshot
        with self.assertRaisesRegex(Error,'SHA256'):ft.prepare_download('upload',done['transfer_id'],[done['file_path']])
        with self.assertRaises(Error):ft.download_status(s['transfer_id'],len(raw)+1)
        with self.assertRaises(Error):ft.download_chunk(s['transfer_id'],0,length=2**21)

    def test_only_published_manifest_paths_can_be_read(self):
        done=self.upload(b'ok')
        for path in ('../VERSION','/etc/passwd','.mc-transfers/quota.lock','inbox/other/secret'):
            with self.subTest(path=path),self.assertRaises(Error):ft.preview_file('upload',done['transfer_id'],path)
        (self.work/done['file_path']).unlink();(self.work/done['file_path']).symlink_to(self.root/'secret')
        with self.assertRaises(Error):ft.preview_file('upload',done['transfer_id'],done['file_path'])

    def test_corrupted_download_cache_rejected_and_original_survives(self):
        done=self.upload(b'original');s=ft.prepare_download('upload',done['transfer_id'],[done['file_path']])
        (ft._dir('downloads',s['transfer_id'])/'data.bin').write_bytes(b'corrupt!')
        with self.assertRaisesRegex(Error,'快照'):ft.download_status(s['transfer_id'])
        self.assertEqual((self.work/done['file_path']).read_bytes(),b'original')

    def test_mcp_logging_omits_payload(self):
        from server import snapshot_arguments
        raw=encoded(b'secret-file-content')
        rendered=json.dumps(snapshot_arguments({'data_base64':raw,'offset':0,'nested':{'token':'secret'}}))
        self.assertNotIn(raw,rendered);self.assertNotIn('"secret"',rendered);self.assertIn('omitted',rendered)

    def test_directory_flood_is_bounded_before_creation(self):
        self.write_config(max_archive_files=3)
        raw=self.zip([('a/',''),('b/',''),('c/',''),('data','x')])
        s=self.begin(raw,'dirs.zip',True);ft.upload_chunk(s['transfer_id'],0,encoded(raw))
        with self.assertRaisesRegex(Error,'条目数量'):ft.upload_finish(s['transfer_id'])
        self.assertFalse((self.work/'inbox'/s['transfer_id']).exists())

    def test_report_dependencies_are_from_the_same_report_snapshot(self):
        j=uuid.uuid4().hex;d=self.work/'.mc-jobs'/j;d.mkdir(parents=True)
        (d/'job.json').write_text(json.dumps({'output_files':{},'provenance':{}}))
        (d/'plot-old.png').write_bytes(b'old');(d/'plot-new.png').write_bytes(b'new')
        report=d/'report.md';report.write_text('![figure](plot-old.png)')
        original=Path.read_bytes
        def replace_after_read(path):
            value=original(path)
            if path==report:path.write_text('![figure](plot-new.png)')
            return value
        # Simulate a writer changing the report after dependency discovery.
        with patch.object(Path,'read_bytes',replace_after_read):
            with self.assertRaisesRegex(Error,'SHA256'):ft.prepare_download('job',j,[str(report.relative_to(self.work))])
        self.assertFalse(list((self.work/'.mc-transfers/downloads').glob('*/record.json')))

    def test_configured_free_space_stops_without_publication(self):
        self.write_config(min_free_disk_bytes=2**63)
        with self.assertRaisesRegex(Error,'空间不足'):self.begin(b'abc')
        self.assertFalse(list((self.work/'.mc-transfers/uploads').glob('*/record.json')))

    def test_download_zip_overhead_is_included_in_cache_quota(self):
        done=self.upload(self.zip([('a','aa'),('b','bb')]),'tiny.zip',True)
        self.write_config(max_staging_bytes=4)
        with self.assertRaisesRegex(Error,'限额'):ft.prepare_download('upload',done['transfer_id'],[x['path'] for x in done['files']])
        self.assertFalse(list((self.work/'.mc-transfers/downloads').glob('*/record.json')))

    def test_host_schema_snapshot_matches_real_registered_definitions(self):
        from tools import TOOLS
        names={t['name'] for t in ft.TRANSFER_TOOLS}
        expected={t['name']:{key:t[key] for key in ('description','inputSchema','outputSchema')} for t in TOOLS if t['name'] in names}
        self.assertEqual(json.loads((ROOT/'config/file-transfer-tools.json').read_text()),expected)

    def test_report_replacement_between_select_and_copy_cannot_mix_dependencies(self):
        j=uuid.uuid4().hex;d=self.work/'.mc-jobs'/j;d.mkdir(parents=True)
        (d/'job.json').write_text(json.dumps({'output_files':{},'provenance':{}}))
        (d/'plot-old.png').write_bytes(b'old');(d/'plot-new.png').write_bytes(b'new')
        report=d/'report.md';report.write_text('![figure](plot-old.png)')
        original=ft.file_hash;calls=0
        def replace_after_select(path):
            nonlocal calls
            value=original(path)
            if path==report:
                calls+=1
                if calls==2:report.write_text('![figure](plot-new.png)')
            return value
        with patch.object(ft,'file_hash',replace_after_select):
            with self.assertRaises(Error):ft.prepare_download('job',j,[str(report.relative_to(self.work))])
        self.assertFalse(list((self.work/'.mc-transfers/downloads').glob('*/record.json')))

    def test_begin_publication_failure_can_retry_the_same_request(self):
        key=uuid.uuid4().hex
        with patch.object(ft,'publish_directory',side_effect=OSError('controlled publication failure')):
            with self.assertRaises(OSError):self.begin(b'input',request_id=key)
        self.assertFalse((self.work/'.mc-transfers/uploads'/key).exists())
        self.assertEqual(self.begin(b'input',request_id=key)['received_bytes'],0)
