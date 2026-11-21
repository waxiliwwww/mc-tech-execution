"""Native SDK oracle for multi-score/multi-nuclide and both filter orders; no solve."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'mcp'))
import numpy as np
import openmc
from openmc_worker import tally_value_rows

for reverse in (False, True):
    mesh = openmc.RegularMesh(); mesh.dimension = (3,2,1)
    mesh.lower_left = (-1.8,-1.2,-5); mesh.upper_right = (1.8,1.2,5)
    filters = [openmc.MeshFilter(mesh), openmc.EnergyFilter([0.,1.,20.])]
    tally = openmc.Tally(); tally.filters = filters[::-1] if reverse else filters
    tally.scores = ['flux','fission']; tally.nuclides = ['U235','U238']
    raw = np.arange(1,49,dtype=float).reshape(12,2,2) * 1.37
    tally._mean = raw; tally._std_dev = raw/17
    tally._sum = raw*10; tally._sum_sq = raw**2*10+1
    tally._results_read = True; tally._derived = True; tally._n_realizations = 10
    rows = tally_value_rows(tally, list(range(48)), limit=48)
    for row in rows:
        native_bins = [tuple(entry['bin']) if isinstance(entry['bin'],list) else entry['bin'] for entry in row['filter_bins']]
        arguments = dict(scores=[row['score']], nuclides=[row['nuclide']],
            filters=[type(f) for f in tally.filters], filter_bins=[(b,) for b in native_bins])
        assert tally.get_values(value='mean', **arguments).item() == row['mean'], row
        assert tally.get_values(value='std_dev', **arguments).item() == row['std_dev'], row
    # A coordinate-swap mutant must fail against the independent SDK oracle.
    wrong = dict(rows[4]); wrong['filter_bins'] = rows[8]['filter_bins']
    bins = [tuple(e['bin']) if isinstance(e['bin'],list) else e['bin'] for e in wrong['filter_bins']]
    actual = tally.get_values(value='mean', scores=[wrong['score']], nuclides=[wrong['nuclide']],
        filters=[type(f) for f in tally.filters], filter_bins=[(b,) for b in bins]).item()
    assert actual != wrong['mean']
print('Native SDK matched all 96 paired rows; swapped-coordinate negative control rejected.')

tally = openmc.Tally()
tally.filters = [openmc.EnergyFunctionFilter([0.,20.], [1.,2.])]
tally.scores = ['flux']; tally.nuclides = ['total']
tally._mean = np.array([[[7.]]]); tally._std_dev = np.array([[[.3]]])
tally._results_read = True; tally._derived = True
row = tally_value_rows(tally, [0])[0]
assert row['mean'] == 7. and row['std_dev'] == .3
assert 'bin' not in row['filter_bins'][0]
assert row['filter_bins'][0]['bin_index'] == 0
print('EnergyFunctionFilter without native bins remains readable.')
