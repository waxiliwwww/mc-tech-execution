"""Native SDK tally axes, including reverse/noncontiguous bins in both orders."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'mcp'))
import openmc
import numpy as np
from openmc_visuals import selected_series, ResultSelectionError
class Point:pass
energy=openmc.EnergyFilter([0.,1.,10.,100.,1000.,1.e6])
materials=openmc.MaterialFilter([10,20])
for filters in ([materials,energy],[energy,materials]):
    tally=openmc.Tally(tally_id=1);tally.filters=filters;tally.scores=['flux'];tally.nuclides=['total']
    tally._mean=np.arange(10,dtype=float).reshape(10,1,1)+1
    tally._std_dev=tally._mean/10
    tally._results_read=True
    point=Point();point.tallies={1:tally}
    try:selected_series(point,{})
    except ResultSelectionError as e:assert 'filter_selection' in str(e)
    else:raise AssertionError('ambiguous materials must request a choice')
    for requested in (None,[(1000.,1.e6),(1.,10.)]):
        selection=[{'type':'MaterialFilter','bins':[20]}]
        if requested:selection.append({'type':'EnergyFilter','bins':requested})
        params={'filter_selection':selection}
        series=selected_series(point,params)
        bins=energy.bins if requested is None else np.array(requested)
        assert series['x_lower']==bins[:,0].tolist()
        assert series['x_upper']==bins[:,1].tolist()
        assert series['x']==bins.mean(axis=1).tolist()
        assert series['x_unit']=='eV'
        native=tally.get_values(scores=['flux'],nuclides=['total'],filters=[openmc.MaterialFilter]+([openmc.EnergyFilter] if requested else []),filter_bins=[(20,)]+([tuple(requested)] if requested else []))
        assert series['mean']==native.ravel().tolist()
        assert series['std_dev']==(native.ravel()/10).tolist()
print('Native five-bin energy bounds, values, errors and selection order verified')
