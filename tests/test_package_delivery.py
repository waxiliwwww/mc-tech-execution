"""Actual packaging/verification on tiny source trees; never packages user data."""
import hashlib,io,json,shutil,subprocess,tarfile,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class PackageDelivery(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='mc package delivery ');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);(self.root/'scripts').mkdir()
        shutil.copy2(ROOT/'package.sh',self.root/'package.sh')
        shutil.copy2(ROOT/'scripts/verify-package.sh',self.root/'scripts/verify-package.sh')
        (self.root/'VERSION').write_text('9.8.7\n');(self.root/'README.md').write_text('independent source package\n')
        (self.root/'config').mkdir();(self.root/'config/openmc.local.json').write_text('{"private":"exclude me"}')
        for suffix in ('.pem','.key','.p12'):
            (self.root/('config/local-secret'+suffix)).write_text('private key fixture, never ship')
        (self.root/'workspace/reproduction').mkdir(parents=True);(self.root/'workspace/reproduction/private.txt').write_text('not shipped')
        result=self.package();self.assertEqual(result.returncode,0,result.stderr)
        self.archive=self.root/'dist/mc-tech-execution-9.8.7.tar.gz'
        self.manifest=self.archive.with_name('mc-tech-execution-9.8.7.MANIFEST.json')
        self.sha=self.archive.with_name('mc-tech-execution-9.8.7.sha256')
        self.assertEqual(result.stdout.strip(),str(self.archive),'stdout remains the archive path')
    def package(self):
        return subprocess.run(['bash',str(self.root/'package.sh')],capture_output=True,text=True)
    def verify(self):
        return subprocess.run(['bash',str(ROOT/'scripts/verify-package.sh'),str(self.archive)],capture_output=True,text=True)
    def update_checksum(self):
        self.sha.write_text(hashlib.sha256(self.archive.read_bytes()).hexdigest()+'  '+self.archive.name+'\n')
    def rewrite_member(self,name,content):
        with tarfile.open(self.archive,'r:gz') as source:
            entries=[(m,source.extractfile(m).read()) for m in source.getmembers()]
        with tarfile.open(self.archive,'w:gz') as output:
            for item,data in entries:
                if item.name==name:data=content
                item.size=len(data);output.addfile(item,io.BytesIO(data))
        self.update_checksum()
    def test_generated_versioned_metadata_is_exactly_the_selected_archive_and_verification_is_read_only(self):
        expected=json.loads(self.manifest.read_text())
        self.assertEqual(expected['version'],'9.8.7')
        self.assertEqual(set(expected['files']),{'README.md','VERSION','package.sh','scripts/verify-package.sh'})
        self.assertEqual((self.root/'dist/MANIFEST.json').read_bytes(),self.manifest.read_bytes())
        self.assertEqual((self.root/'dist/SHA256SUMS').read_bytes(),self.sha.read_bytes())
        with tarfile.open(self.archive) as archive:
            self.assertEqual(json.load(archive.extractfile('mc-tech-execution/MANIFEST.json')),expected)
            self.assertEqual(archive.extractfile('mc-tech-execution/README.md').read(),b'independent source package\n')
        # A stale generic alias must not change which release is being verified.
        (self.root/'dist/MANIFEST.json').write_text('{"version":"old"}')
        (self.root/'dist/SHA256SUMS').write_text('stale generic alias\n')
        before={str(p.relative_to(self.root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
        result=self.verify();self.assertEqual(result.returncode,0,result.stderr);self.assertIn('校验成功',result.stdout)
        self.assertEqual(before,{str(p.relative_to(self.root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()})
    def test_missing_specific_checksum_does_not_fall_back_to_generic_alias(self):
        self.sha.unlink();result=self.verify()
        self.assertNotEqual(result.returncode,0);self.assertIn('缺少对应摘要',result.stderr)
    def test_corrupted_archive_fails_outer_checksum(self):
        with self.archive.open('ab') as stream:stream.write(b'changed bytes')
        result=self.verify();self.assertNotEqual(result.returncode,0);self.assertIn('压缩包SHA256不一致',result.stderr)
    def test_modified_payload_fails_even_after_outer_checksum_is_recomputed(self):
        self.rewrite_member('mc-tech-execution/README.md',b'changed source payload')
        result=self.verify();self.assertNotEqual(result.returncode,0);self.assertIn('包内文件指纹不一致',result.stderr)
    def test_mismatched_versioned_sidecar_is_rejected(self):
        manifest=json.loads(self.manifest.read_text());manifest['version']='0.0.0';self.manifest.write_text(json.dumps(manifest))
        result=self.verify();self.assertNotEqual(result.returncode,0);self.assertIn('同名分版清单与包内MANIFEST不一致',result.stderr)
    def test_failed_repackage_keeps_previous_archive_and_metadata(self):
        before={p.name:p.read_bytes() for p in (self.root/'dist').iterdir()}
        (self.root/'scripts/link').symlink_to(self.root/'README.md')
        result=self.package();self.assertNotEqual(result.returncode,0)
        self.assertEqual(before,{p.name:p.read_bytes() for p in (self.root/'dist').iterdir()})
    def run_with_injected_filesystem_event(self,code):
        import os
        hook=self.root/'hooks';hook.mkdir()
        (hook/'sitecustomize.py').write_text(code)
        return subprocess.run(['bash',str(self.root/'package.sh')],capture_output=True,text=True,
            env={**os.environ,'PYTHONPATH':str(hook),'MC_FIXTURE_ROOT':str(self.root)})
    def test_source_change_between_manifest_and_tar_never_publishes_an_inconsistent_package(self):
        before={p.name:p.read_bytes() for p in (self.root/'dist').iterdir() if p.is_file()}
        result=self.run_with_injected_filesystem_event("""import os,tarfile
from pathlib import Path
root=Path(os.environ['MC_FIXTURE_ROOT']);original=tarfile.TarFile.add
changed=False
def add(self,name,*args,**kw):
 global changed
 if Path(name)==root/'README.md' and not changed:
  (root/'README.md').write_text('CONCURRENT_EDITOR');changed=True
 return original(self,name,*args,**kw)
tarfile.TarFile.add=add
""")
        self.assertEqual((self.root/'README.md').read_text(),'CONCURRENT_EDITOR')
        if result.returncode==0:
            checked=self.verify();self.assertEqual(checked.returncode,0,checked.stderr)
        else:
            self.assertEqual(before,{p.name:p.read_bytes() for p in (self.root/'dist').iterdir() if p.is_file()})
        self.assertFalse(list((self.root/'dist').glob('.package-*/')))
    def test_sidecar_publish_failure_restores_previous_release_combination(self):
        before={p.name:p.read_bytes() for p in (self.root/'dist').iterdir() if p.is_file()}
        (self.root/'README.md').write_text('new release attempt; keep the previous package on failure')
        result=self.run_with_injected_filesystem_event("""import os
from pathlib import Path
original=os.replace
root=Path(os.environ['MC_FIXTURE_ROOT'])
def replace(src,dst,*args,**kw):
 src,dst=Path(src),Path(dst)
 if dst==root/'dist/SHA256SUMS' and src.parent.name.startswith('.package-'):
  raise OSError('injected sidecar publication failure')
 return original(src,dst,*args,**kw)
os.replace=replace
""")
        self.assertNotEqual(result.returncode,0);self.assertIn('原有交付组合已恢复',result.stderr)
        self.assertEqual(before,{p.name:p.read_bytes() for p in (self.root/'dist').iterdir() if p.is_file()})
        checked=self.verify();self.assertEqual(checked.returncode,0,checked.stderr)
if __name__=='__main__':unittest.main()
