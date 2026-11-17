"""Exercise actual SDK Tally selection, no mock get_values and no transport run."""
from pathlib import Path
import sys
sys.path.insert(0,sys.argv[1])
import numpy as np
import openmc
from openmc_visuals import selected_series, ResultSelectionError
from types import SimpleNamespace

for dimension in [(2,3),(2,3,2),(3,)]:
    openmc.reset_auto_ids()
    mesh=openmc.RegularMesh();mesh.dimension=dimension
    mesh.lower_left=[0.]*len(dimension);mesh.upper_right=[float(d) for d in dimension]
    tally=openmc.Tally();tally.filters=[openmc.MeshFilter(mesh)]
    tally.scores=['flux','fission'];tally.nuclides=['total']
    count=int(np.prod(dimension));raw=np.arange(1,count*2+1,dtype=float).reshape(count,1,2)
    tally._mean=raw;tally._std_dev=raw/10;tally._sum=raw*10;tally._sum_sq=raw**2*10+1
    tally._results_read=True;tally._derived=True;tally._n_realizations=10
    last=list(dimension)+[1]*(3-len(dimension));bins=[last,[1,1,1]]
    actual=selected_series(SimpleNamespace(tallies={tally.id:tally}),{
        'tally_id':tally.id,'score':'fission','nuclide':'total',
        'filter_selection':[{'type':'MeshFilter','bins':bins}]})
    expected=tally.get_values(scores=['fission'],nuclides=['total'],filters=[openmc.MeshFilter],
                              filter_bins=[tuple(tuple(c[:len(dimension)]) for c in bins)]).flatten()
    assert actual['mean']==expected.tolist(),(dimension,actual['mean'],expected)
    assert actual['mesh_indices']==[[int(v)-1 for v in c] for c in bins]
    assert actual['filter_selection'][0]['bins']==bins
    assert actual['x']==[count-1,0],actual['x']
    try:
        selected_series(SimpleNamespace(tallies={tally.id:tally}),{'tally_id':tally.id,
            'filter_selection':[{'type':'MeshFilter','bins':[[1,1,last[2]+1]]}]})
    except ResultSelectionError:pass
    else:raise AssertionError('Out-of-range canonical bins must remain rejected')
    for invalid in ([1.5,1,1],[True,1,1]):
        try:
            selected_series(SimpleNamespace(tallies={tally.id:tally}),{'tally_id':tally.id,
                'filter_selection':[{'type':'MeshFilter','bins':[invalid]}]})
        except ResultSelectionError:pass
        else:raise AssertionError('Fractional/boolean coordinates must not silently become integer bins')
    print('SDK native '+str(len(dimension))+'D selection and reversed canonical coordinates agree')
