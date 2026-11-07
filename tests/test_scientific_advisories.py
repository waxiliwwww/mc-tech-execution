"""Scientific input advisories against real SDK objects and real HDF5 metadata.

Positive/negative/boundary: MGXS group alignment, CE temperature inheritance,
missing metadata and bounds. No transport or professional correctness claim.
All generated fixtures are isolated and removed; input serialization unchanged.
Run with the OpenMC SDK interpreter; skip is not evidence of passing.
"""
import importlib.util,json,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'mcp'))

@unittest.skipUnless(importlib.util.find_spec('openmc'),'需要OpenMC SDK/h5py检查真实元数据')
class ScientificAdvisories(unittest.TestCase):
    def model(self,root):
        import openmc,h5py
        material=openmc.Material(material_id=111,name='Hot water is not a temperature declaration')
        material.add_nuclide('H1',1.);material.set_density('g/cc',.74)
        sphere=openmc.Sphere(r=1,boundary_type='vacuum');cell=openmc.Cell(fill=material,region=-sphere)
        model=openmc.Model(openmc.Geometry([cell]),openmc.Materials([material]))
        with h5py.File(root/'h1.h5','w') as f:
            kts=f.create_group('H1/kTs')
            for k in (294,600):kts[str(k)+'K']=k*openmc.data.K_BOLTZMANN
        (root/'cross_sections.xml').write_text('<cross_sections><library materials="H1" type="neutron" path="h1.h5"/></cross_sections>')
        model.materials.cross_sections=str(root/'cross_sections.xml')
        return model,material,cell
    def test_mg_alignment_and_coarsening_do_not_reject_or_rewrite_filters(self):
        import openmc,h5py
        from openmc_scientific_checks import energy_advisory
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);model,_,_=self.model(root);model.settings.energy_mode='multi-group'
            with h5py.File(root/'mg.h5','w') as f:f.attrs['group structure']=[0.,1.,2.,2e7]
            model.materials.cross_sections=str(root/'mg.h5')
            for edges,state in [([0,1,2,2e7],'aligned'),([0,2,2e7],'aligned_subset'),([0,.5,1,2e7],'mismatch')]:
                filt=openmc.EnergyFilter(edges);before=filt.bins.copy();result=energy_advisory(model,[filt])
                self.assertEqual(result['checks'][0]['status'],state)
                self.assertEqual(bool(result['warnings']),state=='mismatch')
                self.assertTrue((filt.bins==before).all())
    def test_default_temperature_warns_and_does_not_infer_from_material_name(self):
        from openmc_scientific_checks import temperature_advisory
        with tempfile.TemporaryDirectory() as d:
            model,mat,_=self.model(Path(d));before=dict(mat.to_xml_element().attrib);r=temperature_advisory(model)
            self.assertTrue(r['metadata_complete']);self.assertFalse(r['applicability_verified'])
            self.assertEqual(r['materials'][0]['requested_k'],293.6);self.assertEqual(r['materials'][0]['source'],'engine_default')
            self.assertTrue(any('未显式声明' in w for w in r['warnings']));self.assertEqual(mat.to_xml_element().attrib,before)
    def test_cell_temperature_precedes_material_default_and_interpolation_is_allowed(self):
        from openmc_scientific_checks import temperature_advisory
        with tempfile.TemporaryDirectory() as d:
            model,mat,cell=self.model(Path(d));mat.temperature=900.;cell.temperature=450.
            model.settings.temperature={'method':'interpolation'};r=temperature_advisory(model)
            self.assertEqual(r['materials'][0]['source'],'cell');self.assertEqual(r['materials'][0]['requested_k'],450.)
            self.assertTrue(r['materials'][0]['datasets'][0]['temperature_supported_by_metadata']);self.assertEqual(r['warnings'],[])
    def test_invalid_temperature_metadata_is_partial_and_json_safe(self):
        import h5py
        from openmc_scientific_checks import temperature_advisory
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);model,_,_=self.model(root)
            with h5py.File(root/'h1.h5','a') as data:data['H1/kTs/600K'][()]=float('nan')
            result=temperature_advisory(model)
            self.assertEqual(result['status'],'partial');self.assertTrue(result['warnings'])
            json.dumps(result,allow_nan=False)

    def test_properties_override_file_makes_effective_temperature_unverified(self):
        from openmc_scientific_checks import temperature_advisory
        with tempfile.TemporaryDirectory() as d:
            model,_,_=self.model(Path(d));model.settings.properties_file='external-properties.h5'
            result=temperature_advisory(model)
            self.assertEqual(result['status'],'partial');self.assertEqual(result['materials'],[])
            self.assertFalse(result['applicability_verified']);self.assertTrue(any('有效温度未核实' in w for w in result['warnings']))

    def test_out_of_range_and_missing_library_are_warnings_not_input_changes(self):
        from openmc_scientific_checks import temperature_advisory
        with tempfile.TemporaryDirectory() as d:
            model,mat,_=self.model(Path(d));mat.temperature=900.;r=temperature_advisory(model)
            self.assertFalse(r['materials'][0]['datasets'][0]['temperature_supported_by_metadata']);self.assertTrue(r['warnings'])
            model.materials.cross_sections=str(Path(d)/'missing.xml');r=temperature_advisory(model)
            self.assertEqual(r['status'],'partial');self.assertTrue(r['warnings']);self.assertEqual(mat.temperature,900.)
if __name__=='__main__':unittest.main()
