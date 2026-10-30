"""Guard the public corpus and real example state transitions, not tool counts."""
import ast
from pathlib import Path
import json
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'mcp'))
import official_catalog as catalog
from business import MCInputError


class OfficialCorpus(unittest.TestCase):
    def test_all_frozen_official_source_entries_and_factories_are_represented(self):
        value = catalog.load_catalog()
        source_root = ROOT / value['sources']['source']['root']
        expected_scripts = {p.relative_to(source_root).as_posix()
                            for p in (source_root / 'examples').rglob('*.py')
                            if p.stem not in ('show_flux', 'plot_spectrum')}
        source = [e for e in value['examples'] if e['source_group'] == 'source']
        self.assertEqual({e['source_file'] for e in source}, expected_scripts)
        public = {f.name for f in ast.parse((source_root / 'example_models.py').read_text()).body
                  if isinstance(f, ast.FunctionDef) and not f.name.startswith('_')}
        self.assertEqual({e['factory'] for e in value['examples'] if e['source_group'] == 'api'}, public)
        index = json.loads((ROOT / 'vendor/official-notebooks/index.json').read_text())
        expected_notebooks = {p['name'] for p in index['files'] if p['name'].endswith('.ipynb')}
        self.assertEqual({e['source_file'] for e in value['examples'] if e['source_group'] == 'notebook'}, expected_notebooks)
        self.assertFalse(value['third_party_examples'])
        self.assertEqual({e['source_revision'] for e in value['examples'] if e['source_group']=='notebook'}, {index['revision']})

    def test_user_scope_explicitly_defers_two_removed_resonance_tutorials(self):
        value=catalog.list_official_examples()
        self.assertEqual(value['included_count'],48);self.assertEqual(value['deferred_count'],2)
        self.assertEqual({x['id'] for x in value['examples'] if x['scope']=='deferred'},
                         {'notebook-nuclear-data','notebook-nuclear-data-resonance-covariance'})

    def test_fixed_ids_not_arbitrary_script_paths_or_commands(self):
        for value in ('../../etc/passwd', '/tmp/user.py', 'source-pincell; touch hacked', ''):
            with self.subTest(value=value), self.assertRaises(MCInputError):
                catalog.definition(value)

    def test_listing_does_not_claim_actual_execution(self):
        value = catalog.describe('notebook-depletion')
        self.assertEqual(value['execution_status'], 'not_run')

    def test_source_integrity_failure_is_detected_on_real_files(self):
        # Real temporary copies exercise the file/hash boundary without modifying
        # the corpus used by concurrently running scientific jobs.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'config').mkdir()
            shutil.copy2(ROOT / 'config/official-examples.json', root / 'config/official-examples.json')
            source = ROOT / 'vendor/openmc-official/0.16.0'
            shutil.copytree(source, root / 'vendor/openmc-official/0.16.0')
            with patch.object(catalog, 'ROOT', root):
                item = catalog.definition('source-pincell-multigroup')
                catalog.verify_bundled_sources(item)
                path = root / 'vendor/openmc-official/0.16.0/examples/pincell_multigroup/build_xml.py'
                before = path.read_bytes(); path.write_bytes(before + b'\n# changed\n')
                with self.assertRaisesRegex(MCInputError, '哈希不一致'):
                    catalog.verify_bundled_sources(item)
                path.write_bytes(before)
                catalog.verify_bundled_sources(item)


if __name__ == '__main__':unittest.main()
