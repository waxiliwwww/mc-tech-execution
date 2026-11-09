"""Official index completeness and truthful finite-interface documentation.

Offline source/manifest/registry checks, no service, solver or model. Corrupt or
missing upstream inputs and undocumented mapping targets must fail generation.
These checks do not prove all OpenMC features or parameter combinations run.
"""
from pathlib import Path
import importlib.util
import json
import re
import shutil
import tempfile
import unittest
import sys

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('api_coverage', ROOT/'scripts/generate_api_coverage.py')
coverage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coverage)


class APICoverage(unittest.TestCase):
    def test_all_official_index_modules_and_symbols_are_present_without_duplicates(self):
        manifest, mapping, rows = coverage.inventory()
        toc = (ROOT/'docs/assets/openmc-api/index.rst').read_text().split('.. toctree::',1)[1]
        pages = re.findall(r'^   ([a-z_]+)$',toc,re.M)
        self.assertEqual([m['page'] for m in manifest['modules']],pages)
        self.assertEqual(len(pages),9)
        indexed = [r for r in rows if r['origin']=='index']
        self.assertEqual(len(indexed),464,'Pinned official index changed: review the new corpus, not a coverage percentage')
        self.assertEqual(len(rows),468)
        self.assertEqual(len({r['name'] for r in rows}),len(rows))
        for row in rows:self.assertIn(row['group_id'],mapping['groups'])
        self.assertIn('openmc.data.njoy.make_ace',{r['name'] for r in rows})
        self.assertIn('openmc.lib.next_batch',{r['name'] for r in rows})
        self.assertIn('openmc.cmfd.CMFDRun',{r['name'] for r in rows})
        self.assertIn('openmc.lib.cells',{r['name'] for r in rows})
        self.assertIn('openmc.deplete.pool.NUM_PROCESSES',{r['name'] for r in rows})

    def test_tool_and_source_links_resolve_to_actual_implementation(self):
        sys.path.insert(0,str(ROOT/'mcp'))
        from tools import TOOLS
        names={t['name'] for t in TOOLS}
        _, mapping, _ = coverage.inventory()
        for group in mapping['groups'].values():
            self.assertLessEqual(set(group['tools']),names)
            for name in group['implementation']+group['evidence']:
                self.assertTrue((ROOT/name).is_file(),name)

    def test_boundary_cases_do_not_turn_templates_or_internal_usage_into_full_support(self):
        _, mapping, rows = coverage.inventory()
        groups={r['name']:r['group_id'] for r in rows}
        self.assertEqual(groups['openmc.HexLattice'],'hex')
        self.assertEqual(groups['openmc.mgxs.EnergyGroups'],'mgxs-template')
        self.assertEqual(groups['openmc.deplete.PredictorIntegrator'],'depletion-run')
        self.assertEqual(groups['openmc.deplete.CECMIntegrator'],'depletion-other')
        self.assertEqual(groups['openmc.lib.next_batch'],'capi')
        self.assertEqual(groups['openmc.cmfd.CMFDRun'],'advanced')
        self.assertEqual(groups['openmc.Summary'],'advanced')
        # Creation and editing are different capabilities: Plane/Cone edit
        # adapters must not be documented as additional generic constructors.
        created={r['name'] for r in rows if r['group_id']=='csg'}
        self.assertEqual(created,{'openmc.XPlane','openmc.YPlane','openmc.ZPlane',
            'openmc.XCylinder','openmc.YCylinder','openmc.ZCylinder','openmc.Sphere'})
        filters={r['name'] for r in rows if r['group_id']=='filter-basic'}
        self.assertEqual(filters,{'openmc.MaterialFilter','openmc.MeshFilter','openmc.EnergyFilter','openmc.ParticleFilter'})
        self.assertIn('固定CoupledOperator+Predictor',mapping['groups']['depletion-run']['boundary'])

    def copied_basis(self):
        temp=tempfile.TemporaryDirectory(prefix='mc API coverage ');self.addCleanup(temp.cleanup)
        root=Path(temp.name);(root/'docs/assets').mkdir(parents=True);(root/'config').mkdir()
        shutil.copytree(ROOT/'docs/assets/openmc-api',root/'docs/assets/openmc-api')
        shutil.copy2(ROOT/'config/openmc-api-coverage.json',root/'config/openmc-api-coverage.json')
        shutil.copy2(ROOT/'docs/index.html',root/'docs/index.html')
        return root

    def test_changed_or_missing_official_source_is_not_silently_dropped(self):
        root=self.copied_basis();source=root/'docs/assets/openmc-api/base.rst'
        original=source.read_text();self.assertIn('   openmc.Material\n',original)
        source.write_text(original.replace('   openmc.Material\n',''))
        with self.assertRaisesRegex(ValueError,'指纹不符'):coverage.inventory(root)
        source.unlink()
        with self.assertRaises(FileNotFoundError):coverage.inventory(root)

    def test_unknown_mapping_name_and_stale_render_are_rejected(self):
        root=self.copied_basis();p=root/'config/openmc-api-coverage.json'
        data=json.loads(p.read_text());data['assignments']['openmc.invented_api']='csg';p.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError,'不存在'):coverage.inventory(root)
        data['assignments'].pop('openmc.invented_api');p.write_text(json.dumps(data))
        page=root/'docs/index.html';page.write_text(page.read_text().replace('data-openmc-api="openmc.Material"','data-openmc-api="missing"'))
        with self.assertRaisesRegex(ValueError,'未同步'):coverage.update(root,check=True)

    def test_current_overview_contains_the_generated_inventory(self):
        coverage.update(check=True)
        page=(ROOT/'docs/index.html').read_text()
        self.assertEqual(page.count('data-openmc-api='),468)
        self.assertIn('id="api-module-map"',page)
        self.assertIn('不代表每个类的全部方法',page)


if __name__ == '__main__':unittest.main()
