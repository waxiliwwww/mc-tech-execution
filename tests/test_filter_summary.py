"""User-facing filter summaries describe actual SDK-written filters; no transport."""
import contextlib,importlib.util,json,os,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'mcp'))
@unittest.skipUnless(importlib.util.find_spec('openmc'), '需要OpenMC SDK，无输运')
class FilterSummary(unittest.TestCase):
    def test_defined_filters_match_written_tally_and_have_compact_parameters(self):
        import openmc
        from openmc_worker import execute
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);original=Path.cwd()
            try:
                os.chdir(root)
                # Exercise the same complete native case required by the public
                # mutation tool; an empty directory cannot validate references.
                material=openmc.Material();material.add_nuclide('H1',1.0);material.set_density('g/cm3',1.0)
                sphere=openmc.Sphere(r=10,boundary_type='vacuum')
                cell=openmc.Cell(fill=material,region=-sphere)
                settings=openmc.Settings();settings.particles=10;settings.batches=4;settings.inactive=0
                openmc.Model(openmc.Geometry([cell]),openmc.Materials([material]),settings).export_to_xml()
                mesh={'type':'mesh','dimension':[17,17,1],'lower_left':[-10.71,-10.71,-1],
                      'upper_right':[10.71,10.71,1]}
                result=execute('add-tally',{'_expected_version':openmc.__version__,'scores':['flux'],
                    'filters':[mesh,{'type':'energy','bins':list(range(31))},
                               {'type':'particle','bins':['neutron']} ]})
                restored=openmc.Tallies.from_xml('tallies.xml')[0]
                self.assertEqual(result['tally_id'],restored.id)
                for item,actual in zip(result['filters'],restored.filters):
                    self.assertEqual(item['type'],type(actual).__name__)
                    self.assertEqual(item['bin_count'],actual.num_bins)
                    self.assertNotIn('\n',item['description']);self.assertNotIn('\t',item['description'])
                parameters=result['filters'][0]['parameters']
                self.assertEqual(parameters['dimension'],[17,17,1])
                self.assertEqual(parameters['lower_left'],[-10.71,-10.71,-1.])
                self.assertEqual(parameters['mesh_id'],restored.filters[0].mesh.id)
                self.assertEqual(result['filters'][1]['parameters']['bounds_count'],31)
                self.assertEqual(result['filters'][1]['parameters']['upper_bound'],30.)
                self.assertTrue(result['filters'][1]['parameters']['bounds_truncated'])
                self.assertEqual(result['filters'][2]['parameters']['bins_preview'],['neutron'])
                json.dumps(result,allow_nan=False)
            finally:os.chdir(original)
    def test_reading_long_energy_filters_does_not_dump_all_bounds(self):
        import openmc,numpy as np
        from openmc_worker import filter_summary
        result=filter_summary(openmc.EnergyFilter(np.arange(10001,dtype=float)))
        self.assertEqual(result['bin_count'],10000)
        self.assertEqual(len(result['parameters']['bounds_preview']),20)
        self.assertEqual(result['parameters']['upper_bound'],10000.)
        self.assertLess(len(json.dumps(result)),1000)
    def test_nonfinite_energy_boundary_remains_json_safe_without_zero_substitution(self):
        import openmc
        from openmc_worker import filter_summary
        result=filter_summary(openmc.EnergyFilter([0.,float('inf')]))
        self.assertIsNone(result['parameters']['upper_bound'])
        self.assertEqual(result['parameters']['bounds_preview'],[0.,None])
        json.dumps(result,allow_nan=False)
if __name__=='__main__':unittest.main()
