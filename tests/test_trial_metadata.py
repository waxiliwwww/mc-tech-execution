"""Result preview limits and report provenance: actual serialization with finite fixtures."""
import contextlib,importlib.util,json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'mcp'))
class ExamplePreview(unittest.TestCase):
    def test_explicit_limit_reaches_reader_and_invalid_limit_stops_before_lookup(self):
        import example_workflows as flow,openmc_tools,openmc_artifacts
        value=dict(instance_id='i',example_id='e',state='completed',steps={'compute':{'job_id':'j','state':'completed'}})
        with patch.object(flow,'example_status',return_value=value),patch.object(openmc_artifacts,'list_artifacts',return_value={}),\
             patch.object(openmc_tools,'read_results',return_value={'statepoint':'path/to/statepoint.h5'}) as reader,\
             patch.object(flow,'path_in_workspace',return_value=Path('/server/statepoint.h5')),\
             patch.object(flow.jobs,'job_directory',return_value=Path('/server/job')),patch.object(flow.jobs,'read',return_value={}):
            out=flow.read_example_results('i',max_values=7)
            reader.assert_called_once_with('j',max_values=7)
            self.assertEqual(out['calculation']['statepoint_server_path'],'/server/statepoint.h5')
            self.assertEqual(out['max_values_per_tally'],7)
        for bad in (0,-1,10001,True,1.5):
            with patch.object(flow,'example_status') as lookup:
                with self.assertRaises(ValueError):flow.read_example_results('i',max_values=bad)
                lookup.assert_not_called()
@unittest.skipUnless(importlib.util.find_spec('openmc') and importlib.util.find_spec('docx'), '需要SDK和DOCX依赖；不执行输运')
class SerializedMetadata(unittest.TestCase):
    def test_worker_preview_cap_and_shape_are_distinct(self):
        import openmc,numpy as np,openmc_worker
        tally=openmc.Tally();tally.scores=['flux','fission','nu-fission'];tally.nuclides=['total']
        tally.filters=[openmc.EnergyFilter(np.arange(10001,dtype=float))]
        tally._derived=True;tally._mean=np.arange(30000,dtype=float).reshape(10000,1,3)
        tally._std_dev=np.ones((10000,1,3))
        class Point:
            tallies={tally.id:tally};run_mode='fixed source';version=(0,16,0);n_particles=200;n_realizations=8
            def __enter__(self):return self
            def __exit__(self,*a):pass
        with patch.object(openmc,'StatePoint',return_value=Point()):
            for limit in (7,1000,10000):
                out=openmc_worker.execute('results',dict(_expected_version=openmc.__version__,statepoint='fixture',max_values=limit))
                t=out['tallies'][0];self.assertEqual(t['shape'],[10000,1,3])
                self.assertEqual(t['total_values'],30000);self.assertEqual(t['returned_values'],limit)
                self.assertEqual(len(t['mean']),limit);self.assertTrue(t['values_truncated'])
                self.assertEqual(t['mean'],list(range(limit)))
    def test_docx_does_not_classify_real_data_as_synthetic(self):
        from openmc_visuals import render_docx
        from docx import Document
        for kind in ('public_official_example','synthetic_mgxs','user_supplied_unverified'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as d:
                old=Path.cwd()
                try:
                    os.chdir(d)
                    render_docx({'data':dict(job_id='fixture',provenance={'data_kind':kind,'execution_kind':'openmc_runtime'},tallies=[])})
                    text='\n'.join(p.text for p in Document('report.docx').paragraphs)
                    self.assertIn('数据来源：'+kind,text);self.assertNotIn('合成数据和',text)
                    self.assertIn('专业验收：未进行',text)
                finally:os.chdir(old)
    def test_docx_template_header_does_not_repeat_legacy_template_as_generator(self):
        from openmc_visuals import render_docx
        from docx import Document
        with tempfile.TemporaryDirectory() as d:
            old=Path.cwd()
            try:
                os.chdir(d)
                render_docx({'data':dict(job_id='fixture',provenance={'data_kind':'public_benchmark_example'},tallies=[],
                    metadata={'template':'assembly-17x17','generator':'assembly-17x17'})})
                rows=[p.text for p in Document('report.docx').paragraphs if p.text.startswith('模板版本：')]
                self.assertEqual(rows,['模板版本：assembly-17x17'])
            finally:os.chdir(old)
if __name__=='__main__':unittest.main()
