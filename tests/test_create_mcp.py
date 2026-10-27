"""Template creation is offline, editable, portable and non-overwriting."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CreateMCP(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='mc create ');self.folder=Path(self.temp.name)

    def tearDown(self):self.temp.cleanup()

    def invoke(self,target,*options):
        return subprocess.run([str(ROOT/'create-mcp.sh'),str(target),*options],cwd='/tmp',capture_output=True,text=True)

    def test_both_languages_create_editable_services_without_installing_runtime(self):
        for language,entry in [('python','tools.py'),('typescript','src/tools.ts')]:
            with self.subTest(language=language):
                target=self.folder/(language+' service');run=self.invoke(target,'--language',language)
                self.assertEqual(run.returncode,0,run.stderr);self.assertTrue((target/entry).is_file())
                self.assertTrue((target/'start.sh').stat().st_mode & 0o111)
                self.assertFalse((target/'.venv').exists());self.assertFalse((target/'node_modules').exists())
                self.assertNotIn('/home/zhangbolun/',(target/'README.md').read_text())
                self.assertIn('tools/list',(target/'README.md').read_text())
                self.assertEqual(json.loads((target/'TEMPLATE.json').read_text())['language'],language)

    def test_existing_empty_directory_file_and_symlink_are_preserved(self):
        empty=self.folder/'empty';empty.mkdir();existing=self.folder/'file';existing.write_text('keep')
        link=self.folder/'link';link.symlink_to(existing)
        for target in [empty,existing,link]:
            run=self.invoke(target);self.assertNotEqual(run.returncode,0);self.assertIn('拒绝覆盖',run.stderr)
        self.assertEqual(existing.read_text(),'keep');self.assertEqual(list(empty.iterdir()),[]);self.assertTrue(link.is_symlink())

    def test_missing_parent_is_not_silently_created(self):
        target=self.folder/'missing/service';run=self.invoke(target)
        self.assertNotEqual(run.returncode,0);self.assertFalse(target.parent.exists())

    def module(self):
        spec=importlib.util.spec_from_file_location('mc_create',ROOT/'scripts/create_mcp.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        module.ROOT=self.folder;return module

    def test_bad_archive_hash_is_refused_before_creating_target(self):
        module=self.module();vendor=self.folder/'vendor';vendor.mkdir()
        (vendor/module.ARCHIVE).write_bytes(b'wrong archive')
        (vendor/'mcp-toolkit-MANIFEST.json').write_text(json.dumps({'artifacts':{module.ARCHIVE:'0'*64}}))
        target=self.folder/'service'
        with self.assertRaisesRegex(ValueError,'哈希不符'):module.create(target,'python')
        self.assertFalse(target.exists())

    def test_hash_valid_but_unsafe_archive_paths_or_links_are_refused(self):
        module=self.module();vendor=self.folder/'vendor';vendor.mkdir();archive=vendor/module.ARCHIVE
        for name,link in [('../outside',False),('/absolute',False),('mcp-toolkit/python/link',True)]:
            with self.subTest(name=name):
                with tarfile.open(archive,'w:gz') as bundle:
                    info=tarfile.TarInfo(name)
                    if link:info.type=tarfile.SYMTYPE;info.linkname='/tmp/outside';bundle.addfile(info)
                    else:info.size=4;bundle.addfile(info,io.BytesIO(b'evil'))
                (vendor/'mcp-toolkit-MANIFEST.json').write_text(json.dumps({'artifacts':{module.ARCHIVE:hashlib.sha256(archive.read_bytes()).hexdigest()}}))
                target=self.folder/'service'
                with self.assertRaisesRegex(ValueError,'不安全'):module.create(target,'python')
                self.assertFalse(target.exists())
