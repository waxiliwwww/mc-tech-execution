"""Existing native inputs retain fields outside our modification adapter."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'mcp'))
import business
from openmc_import import import_model, publish_directory


class NativeImport(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mc import ')
        self.root = Path(self.temp.name)
        self.ws = self.root / 'workspace'; self.ws.mkdir()
        self.previous = business.WORKSPACE; business.WORKSPACE = self.ws
        self.addCleanup(setattr, business, 'WORKSPACE', self.previous)
        self.addCleanup(self.temp.cleanup)
        self.config = self.root / 'config.json'
        self.config.write_text((ROOT / 'config/openmc.json').read_text())
        self.env = patch.dict(os.environ, {'MC_WORKSPACE': str(self.ws), 'MC_OPENMC_CONFIG': str(self.config)})
        self.env.start(); self.addCleanup(self.env.stop)
        self.source = self.ws / 'incoming'; self.source.mkdir()
        for name in ('geometry', 'materials', 'settings'):
            (self.source / (name+'.xml')).write_text('<'+name+'/>')

    def test_native_fields_and_python_are_retained_but_not_executed(self):
        text = '<settings><run_mode>fixed source</run_mode><photon_transport>true</photon_transport></settings>'
        (self.source / 'settings.xml').write_text(text)
        (self.source / 'model.py').write_text("raise RuntimeError('must not run')")
        (self.source / 'mc-case.json').write_text(json.dumps({'data_kind': 'public_benchmark_example', 'professional_acceptance': True}))
        value = import_model('incoming', 'cases/陌生模型', api_check=False)
        self.assertEqual((self.ws / 'cases/陌生模型/settings.xml').read_text(), text)
        self.assertEqual((self.source / 'settings.xml').read_text(), text)
        self.assertFalse(value['professional_acceptance'])
        marker=json.loads((self.ws / 'cases/陌生模型/mc-case.json').read_text())
        self.assertEqual(marker['data_kind'], 'user_supplied_unverified')
        self.assertTrue(marker['original_marker_sha256'])
        self.assertEqual(value['server_path'], str(self.ws / 'cases/陌生模型'))

    def test_local_data_copied_and_external_dependency_declared(self):
        (self.source / 'mesh.h5m').write_bytes(b'placeholder; import structure only')
        external = self.root / 'cross_sections.xml'; external.write_text('<cross_sections/>')
        (self.source / 'geometry.xml').write_text('<geometry><dagmc_universe filename="mesh.h5m"/></geometry>')
        (self.source / 'materials.xml').write_text('<materials><cross_sections>'+str(external)+'</cross_sections></materials>')
        value=import_model('incoming', 'copies/model', api_check=False)
        self.assertEqual({r['scope'] for r in value['dependencies']}, {'copied_local', 'server_external'})
        self.assertEqual((self.ws/'copies/model/mesh.h5m').read_bytes(), b'placeholder; import structure only')

    def test_local_cross_section_index_does_not_claim_full_portability(self):
        (self.source/'index.xml').write_text('<cross_sections><directory>/another/server</directory><library path="absent.h5"/></cross_sections>')
        (self.source/'materials.xml').write_text('<materials><cross_sections>index.xml</cross_sections></materials>')
        result=import_model('incoming','copied-index',api_check=False)
        resource=result['dependencies'][0]
        self.assertFalse(resource['portable'])
        self.assertEqual(resource['status'],'index_copied_dependencies_unverified')
        self.assertTrue((self.ws/'copied-index/index.xml').is_file())


    def test_failure_leaves_no_target_or_staging_and_keeps_source(self):
        (self.source / 'settings.xml').write_text('<settings>')
        with self.assertRaisesRegex(ValueError, 'XML语法错误'):
            import_model('incoming', 'copies/failed', api_check=False)
        self.assertFalse((self.ws/'copies/failed').exists())
        self.assertEqual(list((self.ws/'copies').iterdir()), [])
        self.assertEqual((self.source/'settings.xml').read_text(), '<settings>')
        (self.source / 'settings.xml').write_text('<settings/>')
        with patch('openmc_tools.api', side_effect=business.MCInputError('真实API边界错误')):
            with self.assertRaisesRegex(ValueError, '真实API边界错误'):
                import_model('incoming', 'copies/api-failed')
        self.assertFalse((self.ws/'copies/api-failed').exists())

    def test_missing_resource_symlink_escaping_and_overwrite_refused(self):
        (self.source / 'settings.xml').write_text('<settings><source file="missing.h5"/></settings>')
        with self.assertRaisesRegex(ValueError, '本地文件缺失'):
            import_model('incoming', 'bad', api_check=False)
        (self.source / 'settings.xml').write_text('<settings/>')
        (self.source / 'linked').symlink_to(self.source/'settings.xml')
        with self.assertRaisesRegex(ValueError, '符号链接'):
            import_model('incoming', 'bad', api_check=False)
        (self.source/'linked').unlink()
        for target in ('../escape', 'incoming/copy', 'incoming', '.'):
            with self.subTest(target=target), self.assertRaises(ValueError):
                import_model('incoming', target, api_check=False)
        (self.ws/'exists').mkdir(); (self.ws/'exists/keep').write_text('keep')
        with self.assertRaises(ValueError):import_model('incoming', 'exists', api_check=False)
        self.assertEqual((self.ws/'exists/keep').read_text(), 'keep')

    def test_child_encoded_compiled_source_and_output_escape_are_refused(self):
        for xml, message in [
            ('<settings><source><type>compiled</type><library>/tmp/untrusted.so</library></source></settings>', 'compiled source'),
            ('<settings><output><path>../outside</path></output></settings>', 'output'),
            ('<settings><source><type>file</type><file>missing-source.h5</file></source></settings>', '本地文件缺失'),
        ]:
            with self.subTest(xml=xml):
                (self.source/'settings.xml').write_text(xml)
                with self.assertRaisesRegex(ValueError,message):
                    import_model('incoming','refused',api_check=False)
                self.assertFalse((self.ws/'refused').exists())
                self.assertEqual((self.source/'settings.xml').read_text(),xml)


    def test_publication_refuses_racing_empty_target(self):
        staging=self.ws/'staging';staging.mkdir();(staging/'x').write_text('x')
        target=self.ws/'target';target.mkdir()
        with self.assertRaisesRegex(ValueError, '拒绝覆盖'):publish_directory(staging,target)
        self.assertTrue((staging/'x').exists());self.assertEqual(list(target.iterdir()),[])

    def test_default_output_path_is_not_a_file_dependency(self):
        for index, output in enumerate(('<output path="."/>', '<output path="./"/>',
                                        '<output><path>.</path></output>')):
            with self.subTest(output=output):
                text = '<settings>' + output + '</settings>'
                (self.source/'settings.xml').write_text(text)
                value = import_model('incoming', 'default-output-' + str(index), api_check=False)
                self.assertEqual(value['dependencies'], [])
                self.assertEqual((self.ws/value['case_dir']/'settings.xml').read_text(),text)


@unittest.skipUnless(os.environ.get('MC_TEST_OPENMC_CONFIG'), '设置MC_TEST_OPENMC_CONFIG验证真实原生模型导入')
class RealNativeImport(unittest.TestCase):
    def test_integrated_xml_with_new_settings_retained_after_api_read(self):
        from openmc_tools import create_model
        with tempfile.TemporaryDirectory(prefix='mc import real ') as temp:
            previous=business.WORKSPACE;business.WORKSPACE=Path(temp)
            try:
                with patch.dict(os.environ, {'MC_WORKSPACE':temp,'MC_OPENMC_CONFIG':os.environ['MC_TEST_OPENMC_CONFIG']}):
                    create_model('original','synthetic-mgxs',particles=100,batches=6,inactive=2)
                    text=(Path(temp)/'original/settings.xml').read_text()
                    (Path(temp)/'original/settings.xml').write_text(text.replace('</settings>', '<verbosity>2</verbosity></settings>'))
                    value=import_model('original','outside-catalog')
                    self.assertTrue(value['validation']['openmc_api']['api_parsed'])
                    self.assertIn('<verbosity>2</verbosity>',(Path(temp)/'outside-catalog/settings.xml').read_text())
            finally:business.WORKSPACE=previous
