"""Exercise archive extraction/integrity on real files, not scientific validity."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('official_data_installer',ROOT/'scripts/install_official_data.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)


class OfficialDataSetup(unittest.TestCase):
    def fixture(self,root,unsafe=False):
        archive=root/'data.tar.xz'
        entries={'dataset/cross_sections.xml':b'<cross_sections><library materials="H1" path="neutron/H1.h5" type="neutron"/></cross_sections>',
                 'dataset/neutron/H1.h5':b'fixture bytes, not a scientific dataset'}
        with tarfile.open(archive,'w:xz') as output:
            for name,content in entries.items():
                item=tarfile.TarInfo(name);item.size=len(content);output.addfile(item,io.BytesIO(content))
            if unsafe:
                item=tarfile.TarInfo('dataset/link');item.type=tarfile.SYMTYPE;item.linkname='/tmp/outside';output.addfile(item)
        (root/'config').mkdir()
        descriptor={'name':'fixture','url':'not fetched','archive_bytes':archive.stat().st_size,
                    'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
                    'archive_prefix':'dataset','directory':'.runtime/data'}
        (root/'config/official-nuclear-data.json').write_text(json.dumps(descriptor))
        return archive

    def test_complete_install_preserves_bytes_records_identity_and_keeps_existing_dataset(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);archive=self.fixture(root)
            with patch.object(installer,'ROOT',root):
                result=installer.install(archive);again=installer.install(archive)
            self.assertEqual(result,again)
            target=root/'.runtime/data'
            self.assertEqual((target/'neutron/H1.h5').read_bytes(),b'fixture bytes, not a scientific dataset')
            self.assertEqual(result['files']['neutron/H1.h5'],hashlib.sha256((target/'neutron/H1.h5').read_bytes()).hexdigest())
            self.assertTrue((target/'cross_sections.original.xml').is_file())

    def test_bad_checksum_and_links_are_rejected_before_exposing_complete_data(self):
        for unsafe in (False,True):
            with self.subTest(unsafe=unsafe),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);archive=self.fixture(root,unsafe)
                if not unsafe:archive.write_bytes(archive.read_bytes()+b'changed')
                with patch.object(installer,'ROOT',root),self.assertRaises(ValueError):installer.install(archive)
                self.assertFalse((root/'.runtime/data').exists())


if __name__=='__main__':unittest.main()
