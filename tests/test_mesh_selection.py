"""Real OpenMC Tally selection and rendering with deliberately distinguishable fixture values.
StatePoint decoding is the external boundary; no solver or scientific acceptance.
"""
from pathlib import Path
import importlib.util,json,sys,tempfile,unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'mcp'))
@unittest.skipUnless(importlib.util.find_spec('openmc') and importlib.util.find_spec('matplotlib'), '需要OpenMC绘图环境，无输运')
class MeshSelection(unittest.TestCase):
    def tally(self,reverse=False,energy=False):
        import openmc,numpy as np
        mesh=openmc.RegularMesh();mesh.dimension=(2,2,1);mesh.lower_left=(-1,-1,-1);mesh.upper_right=(1,1,1)
        tally=openmc.Tally();tally.filters=[openmc.MeshFilter(mesh)]
        if energy:
            tally.filters.append(openmc.EnergyFilter([0.,1.,20.]))
            if reverse:tally.filters=tally.filters[::-1]
        tally.scores=['flux','fission','nu-fission'];tally.nuclides=['total']
        # Fixture means are independent per-cell/per-energy/per-score values.
        cells=8 if energy else 4
        raw=np.arange(1,cells*3+1,dtype=float).reshape(cells,1,3)
        tally._mean=raw;tally._std_dev=raw/100.;tally._sum=raw*10;tally._sum_sq=(raw**2)*10+1;tally._results_read=True;tally._derived=True;tally._n_realizations=10
        return tally,mesh,raw
    def run_plot(self,tmp,tally,mesh,**params):
        import openmc
        import openmc_visuals as v
        xml=Path(tmp)/'tallies.xml';openmc.Tallies([tally]).export_to_xml(xml)
        class Point:
            tallies={tally.id:tally}
            def __enter__(self):return self
            def __exit__(self,*a):pass
        with patch.object(openmc,'StatePoint',return_value=Point()):
            out=v.plot_mesh(dict(statepoint='fixture.h5',tallies_xml=str(xml),filename=str(Path(tmp)/'mesh.svg'),
                           provenance={'data_kind':'unit_selection_fixture'},**params))
        return out,json.loads((Path(tmp)/'mesh.json').read_text())
    def test_each_explicit_score_matches_native_values(self):
        import numpy as np
        tally,mesh,raw=self.tally()
        with tempfile.TemporaryDirectory() as d:
            for index,score in enumerate(tally.scores):
                out,data=self.run_plot(d,tally,mesh,score=score)
                np.testing.assert_allclose(data['values'],raw[:,0,index].reshape(2,2))
                np.testing.assert_allclose(data['errors'],(raw[:,0,index]/100.).reshape(2,2))
                self.assertEqual(out['score'],score);self.assertEqual(data['nuclide'],'total')
            with self.assertRaisesRegex(ValueError,'score明确选择'):self.run_plot(d,tally,mesh)
            with self.assertRaisesRegex(ValueError,'score不存在'):self.run_plot(d,tally,mesh,score='no-score')
    def test_both_filter_orders_preserve_energy_and_cell_coordinates(self):
        import numpy as np
        for reverse in (False,True):
            tally,mesh,raw=self.tally(reverse=reverse,energy=True)
            with tempfile.TemporaryDirectory() as d:
                _,data=self.run_plot(d,tally,mesh,score='fission',energy_bin=1)
                expected=raw[:,0,1].reshape((2,4) if reverse else (4,2))
                expected=expected[1,:] if reverse else expected[:,1]
                np.testing.assert_allclose(data['values'],expected.reshape(2,2))
if __name__=='__main__':unittest.main()
