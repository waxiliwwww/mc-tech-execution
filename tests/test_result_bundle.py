"""Report adaptation must retain mesh coordinates, order, slice and missing values.

Small independent asymmetric values detect an otherwise plausible transpose;
no runtime, solver or model is needed for this coordinate contract.
"""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'mcp'))
from result_bundle import mesh_report_series


class ResultBundleMapping(unittest.TestCase):
    def test_asymmetric_mesh_values_remain_bound_to_their_coordinates(self):
        data={'dimension':[3,2,2],'level':1,'mesh':{'lower_left':[0,-2,-3],'upper_right':[6,2,3]},
              'values':[[1.,2.,3.],[4.,5.,6.]],'errors':[[.1,.2,.3],[.4,None,.6]],
              'x_edges':[0,2,4,6],'y_edges':[-2,0,2],'tally_id':7,'score':'flux','nuclide':'total',
              'normalization':'raw, no power normalization','energy_bounds':[.001,20.]}
        result=mesh_report_series(data)
        self.assertEqual(result['mean'],[1.,2.,3.,4.,5.,6.])
        self.assertEqual(result['std_dev'],[.1,.2,.3,.4,None,.6])
        self.assertEqual(result['mesh_indices'],[[0,0,1],[1,0,1],[2,0,1],[0,1,1],[1,1,1],[2,1,1]])
        self.assertEqual(result['mesh_centers'],[[1,-1,1.5],[3,-1,1.5],[5,-1,1.5],[1,1,1.5],[3,1,1.5],[5,1,1.5]])
        self.assertIn('[0.001, 20.0]',result['selection_note'])
        self.assertEqual(result['normalization'],data['normalization'])
