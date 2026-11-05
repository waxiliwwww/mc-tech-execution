"""Rendered zero markers must agree with unchanged scientific data (no transport)."""
from pathlib import Path
import importlib.util,json,csv,sys,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'mcp'))
@unittest.skipUnless(importlib.util.find_spec('numpy') and importlib.util.find_spec('matplotlib'), '需要图表环境；可用已配置OpenMC Python执行，无输运')
class MeshZero(unittest.TestCase):
    def test_zero_and_missing_bins_remain_distinct_in_svg_json_and_csv(self):
        import numpy as np
        from openmc_visuals import draw_mesh
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'mesh.svg'
            series=dict(values=np.array([[0.,2.],[np.nan,-0.]]),errors=np.array([[0.,.1],[np.nan,0.]]),
                        x_edges=np.array([0.,1.,2.]),y_edges=np.array([0.,1.,2.]),
                        dimension=[2,2,1],mesh_id=7,tally_id=42,level=None,score='flux',energy_label=None,
                        x_label='x (cm)',y_label='y (cm)',title='flux',colorbar_label='raw flux')
            result=draw_mesh(series,path,{'provenance':{'data_kind':'unit_plot_fixture'}})
            self.assertEqual(result['zero_bins'],2);self.assertTrue(result['warnings'])
            data=json.loads(path.with_suffix('.json').read_text())
            self.assertEqual(data['zero_bins'],2);self.assertEqual(data['values'],[[0.,2.],[None,-0.]])
            with path.with_suffix('.csv').open() as stream:
                rows=list(csv.DictReader(stream))
            self.assertEqual([r['value'] for r in rows],['0','2','','-0'])
            self.assertIn('zero estimate: 2 bins',path.read_text())
            self.assertIn('not a physical-zero conclusion',path.read_text())
if __name__=='__main__':unittest.main()
