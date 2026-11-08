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
    def test_full_filter_identity_sees_hidden_bins_but_ignores_assigned_mesh_ids(self):
        import openmc
        from openmc_worker import filter_identity,filter_summary
        first=list(range(31));second=list(first);second[24]=24.5
        a=openmc.EnergyFilter(first);b=openmc.EnergyFilter(second)
        self.assertEqual(filter_summary(a),filter_summary(b))
        self.assertNotEqual(filter_identity(a),filter_identity(b))
        m1=openmc.RegularMesh(mesh_id=87001);m1.dimension=[2,2];m1.lower_left=[-1,-1];m1.upper_right=[1,1]
        m2=openmc.RegularMesh(mesh_id=87002);m2.dimension=[2,2];m2.lower_left=[-1,-1];m2.upper_right=[1,1]
        self.assertEqual(filter_identity(openmc.MeshFilter(m1)),filter_identity(openmc.MeshFilter(m2)))
        m2.upper_right=[2,1]
        self.assertNotEqual(filter_identity(openmc.MeshFilter(m1)),filter_identity(openmc.MeshFilter(m2)))
        self.assertNotEqual(filter_identity(openmc.MaterialFilter([1,2])),filter_identity(openmc.MaterialFilter([1,3])))
        translated=openmc.MeshFilter(m1);translated.translation=[1,0,0]
        self.assertNotEqual(filter_identity(openmc.MeshFilter(m1)),filter_identity(translated))
        material_a=openmc.MeshMaterialFilter(m1,[(0,1),(1,2)])
        material_b=openmc.MeshMaterialFilter(m1,[(0,1),(1,3)])
        self.assertNotEqual(filter_identity(material_a),filter_identity(material_b))
        m2.upper_right=[1,1]
        self.assertEqual(filter_identity(material_a),filter_identity(openmc.MeshMaterialFilter(m2,[(0,1),(1,2)])))

    def test_instance_filters_require_actual_ordered_geometry_paths(self):
        import openmc
        from openmc_worker import filter_identity
        first=openmc.DistribcellFilter(1);first._num_bins=2
        self.assertIsNone(filter_identity(first))
        first._paths=['u0->c1->l1(0,0)','u0->c1->l1(1,0)']
        second=openmc.DistribcellFilter(1);second._num_bins=2
        second._paths=list(reversed(first.paths))
        self.assertNotEqual(filter_identity(first),filter_identity(second))
        second._paths=list(first.paths)
        self.assertEqual(filter_identity(first),filter_identity(second))
        self.assertIsNone(filter_identity(openmc.CellInstanceFilter([(1,0)])))

if __name__=='__main__':unittest.main()
