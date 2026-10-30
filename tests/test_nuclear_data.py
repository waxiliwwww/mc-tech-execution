"""Integrity and administrator ownership at the data-cache boundary; no physics mock claims."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'mcp'))
import nuclear_data as data
from openmc_runtime import Error


class DataCacheIntegrity(unittest.TestCase):
    def test_complete_default_library_contains_actual_water_thermal_object(self):
        identity=json.loads((ROOT/'config/data-index/endfb-vii.1-files.json').read_text())
        self.assertEqual(identity['revision'],data.LIBRARIES['endfb-vii.1']['revision'])
        self.assertIn('neutron/c_H_in_H2O.h5',identity['files'])
        self.assertGreater(identity['files']['neutron/c_H_in_H2O.h5']['bytes'],100000)

    def test_model_external_index_has_precedence_and_never_gets_a_fixed_official_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);model=root/'case';model.mkdir()
            (model/'settings.xml').write_text('<settings/>')
            (model/'materials.xml').write_text('<materials><cross_sections>model-xs.xml</cross_sections><material><nuclide name="H1"/></material></materials>')
            (model/'H1.h5').write_bytes(b'external fixture bytes, not a physics dataset')
            index=model/'model-xs.xml';index.write_text('<cross_sections><library materials="H1" type="neutron" path="H1.h5"/></cross_sections>')
            admin=root/'admin.xml';admin.write_text('<cross_sections><library materials="H2" type="neutron" path="missing.h5"/></cross_sections>')
            cfg={'nuclear_data_library':'official-endfb-vii.1','cross_sections':str(admin),
                 'python_command':[sys.executable],'openmc_command':['openmc']}
            with patch.object(data,'ROOT',root),patch.dict(data.os.environ,{},clear=True):
                result=data.ensure_for_model(model,cfg)
            self.assertEqual(result['cross_sections'],str(index))
            self.assertEqual(result['library'],'external_unverified');self.assertIsNone(result['source_revision'])
            self.assertEqual(result['index_sha256'],data.data_digest(index))
            self.assertEqual(result['file_identities'][0]['sha256'],data.data_digest(model/'H1.h5'))

    def test_external_directory_uses_the_solver_file_not_a_same_named_index_neighbor(self):
        for relative in [False,True]:
            with self.subTest(relative=relative),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);model=root/'case';model.mkdir();indices=model/'indices';indices.mkdir()
                actual=model/'actual';actual.mkdir()
                (actual/'H1.h5').write_bytes(b'actual library object')
                (indices/'H1.h5').write_bytes(b'different misleading same-named neighbor')
                (model/'settings.xml').write_text('<settings/>')
                (model/'materials.xml').write_text('<materials><cross_sections>indices/xs.xml</cross_sections><material><nuclide name="H1"/></material></materials>')
                directory='actual' if relative else str(actual)
                index=indices/'xs.xml';index.write_text('<cross_sections><directory>'+directory+'</directory><library materials="H1" type="neutron" path="H1.h5"/></cross_sections>')
                cfg={'nuclear_data_library':'official-endfb-vii.1','python_command':[sys.executable],'openmc_command':['openmc']}
                with patch.object(data,'ROOT',root),patch.dict(data.os.environ,{},clear=True):result=data.ensure_for_model(model,cfg)
                self.assertEqual(result['file_identities'][0]['server_path'],str(actual/'H1.h5'))
                self.assertEqual(result['file_identities'][0]['sha256'],data.data_digest(actual/'H1.h5'))
                self.assertNotEqual(result['file_identities'][0]['sha256'],data.data_digest(indices/'H1.h5'))

    def test_changing_cached_file_and_local_receipt_cannot_replace_fixed_upstream_identity(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);cfg={'nuclear_data_library':'endfb-vii.1','python_command':[sys.executable],'openmc_command':['openmc']}
            cache=root/'.runtime/nuclear-data/endfb-vii.1';cache.mkdir(parents=True)
            (root/'config/data-index').mkdir(parents=True)
            xml='<cross_sections><library materials="H1" type="neutron" path="H1.h5"/></cross_sections>'
            (cache/'cross_sections.xml').write_text(xml)
            original=b'fixed upstream object, deliberately not a physics test'
            fixed=hashlib.sha1(b'blob '+str(len(original)).encode()+b'\0'+original).hexdigest()
            (root/'config/data-index/endfb-vii.1-files.json').write_text(json.dumps({'files':{'H1.h5':{'git_blob_sha':fixed,'bytes':len(original)}}}))
            target=cache/'H1.h5';target.write_bytes(original)
            receipt=cache/'H1.source.json'
            receipt.write_text(json.dumps({'revision':data.LIBRARIES['endfb-vii.1']['revision'],'sha256':hashlib.sha256(original).hexdigest()}))
            model=root/'model';model.mkdir();(model/'settings.xml').write_text('<settings/>')
            (model/'materials.xml').write_text('<materials><material><nuclide name="H1"/></material></materials>')
            with patch.object(data,'ROOT',root),patch.dict(data.os.environ,{},clear=True),patch.object(data,'fetch_file',side_effect=AssertionError('cache verification must not download')):
                self.assertTrue(data.ensure_for_model(model,cfg,extra_nuclides=['decay-only-not-in-neutron-index'])['available'])
                target.write_bytes(b'replaced object');value=json.loads(receipt.read_text())
                value['sha256']=data.data_digest(target);receipt.write_text(json.dumps(value))
                with self.assertRaisesRegex(Error,'哈希不一致'):data.ensure_for_model(model,cfg)


if __name__=='__main__':unittest.main()
