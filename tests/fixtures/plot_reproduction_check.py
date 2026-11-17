"""Actual renderer/reproduction checks, executed in the plotting environment."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'mcp'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from openmc_visuals import draw, draw_mesh, draw_convergence, plot_convergence

provenance = {'data_kind':'test_fixture', 'execution_kind':'fixture', 'professional_acceptance':False}
original_style = dict(plt.rcParams)
base = {'x':[1,2,3], 'mean':[4,None,9], 'std_dev':[.5,None,.75],
        'x_label':'Distance (cm)', 'y_label':'Raw score', 'normalization':'raw'}

with tempfile.TemporaryDirectory(prefix='plot replay ') as temp:
    folder = Path(temp); server = folder/'server'; server.mkdir()
    cases = []
    for name, style, extra in [
        ('curve','line',{}), ('bar','bar',{}),
        ('spectrum','line',{'x_unit':'eV','x_scale':'log','x_lower':[.1,1,10],'x_upper':[1,10,100]}),
        ('scan','line',{'sources':[{'job_id':'first'},None,{'job_id':'third'}]})]:
        data = {**base, **extra}
        result = draw(data, server/(name+'.png'), {'style':style,'provenance':provenance})
        cases.append((name,result))
        saved=json.loads((server/result['series_json']).read_text())
        assert saved['mean']==base['mean'] and saved['std_dev']==base['std_dev']
        assert saved['normalization']=='raw'
    mesh={'values':np.array([[1.,0.],[np.nan,4.]]),'errors':np.array([[.1,0.],[np.nan,.2]]),
          'x_edges':np.array([-1.,0.,1.]),'y_edges':np.array([-1.,0.,1.]),'dimension':[2,2,1],
          'title':'Mesh selection','x_label':'x (cm)','y_label':'y (cm)',
          'colorbar_label':'Raw score','tally_id':3,'mesh_id':2,'score':'flux','level':0,'nuclide':'total',
          'energy_label':'1-2 eV'}
    cases.append(('mesh',draw_mesh(mesh,server/'mesh.png',{'provenance':provenance})))
    convergence={'x':[3,4,5],'mean':[1.,1.1,1.0],'stderr':[None,.1,.15],
                 'generation_k':[1.,1.2,.8],'combined':{'mean':1.02,'std_dev':.1},
                 'title':'k-history','x_label':'Batch number','y_label':'k-effective'}
    cases.append(('convergence',draw_convergence(convergence,server/'convergence.png',{'provenance':provenance})))
    # Multiple generations per batch must exclude the entire inactive interval.
    # These deliberately different values make a batch/generation offset error visible.
    import h5py
    point=server/'generations.h5'
    with h5py.File(point,'w') as f:
        f['k_generation']=[99.,98.,97.,96.,95.,94.,1.,2.,3.,4.,5.,6.]
        f['n_batches']=4;f['n_inactive']=2;f['generations_per_batch']=3
        f['k_combined']=[3.5,.2]
    multi=plot_convergence({'statepoint':str(point),'filename':str(server/'multi-generation.png'),
                            'provenance':provenance,'show_band':False})
    saved=json.loads((server/multi['series_json']).read_text())
    assert saved['generation_k']==[1.,2.,3.,4.,5.,6.]
    assert saved['x']==[7,8,9,10,11,12]
    assert saved['mean']==[1.,1.5,2.,2.5,3.,3.5]
    assert 'Generation' in saved['x_label']
    assert multi['batches']==2 and multi['generations']==6 and multi['generations_per_batch']==3
    cases.append(('multi-generation',multi))
    # A standalone process sees neither the toolkit nor OpenMC. Identical source,
    # data, backend and fonts must reproduce identical PNG bytes.
    for name,result in cases:
        host=folder/('host '+name);host.mkdir()
        data_name=result.get('series_json') or result['values_json']
        script=server/result['plot_script']
        assert script.is_file()
        shutil.copy2(script,host/script.name);shutil.copy2(server/data_name,host/data_name)
        frozen={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in host.iterdir()}
        invocation = "import sys,runpy;sys.modules['openmc']=None;sys.modules['openmc_visuals']=None;sys.modules['plot_templates']=None;sys.argv=sys.argv[1:];runpy.run_path(sys.argv[0],run_name='__main__')"
        env={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME')}
        run=subprocess.run([sys.executable,'-c',invocation,str(host/script.name)],cwd=folder,env=env,
                           capture_output=True,text=True,timeout=30)
        assert run.returncode==0,run.stdout+run.stderr
        assert (host/(name+'-replot.png')).read_bytes()==(server/(name+'.png')).read_bytes(),name
        run=subprocess.run([sys.executable,'-c',invocation,str(host/script.name),'--output',str(host/'custom.svg'),
                            '--title','Adjusted title','--xlabel','Adjusted axis','--font-size','16'],
                           cwd=folder,env=env,capture_output=True,text=True,timeout=30)
        assert run.returncode==0,run.stdout+run.stderr
        svg=ET.parse(host/'custom.svg').getroot()
        visible=''.join(svg.itertext())
        assert 'Adjusted title' in visible and 'Adjusted axis' in visible
        if name=='mesh':
            assert 'layer k=0' in visible and '1-2 eV' in visible and 'unavailable:' in visible
        if name=='convergence':
            assert 'Combined k = 1.02000 ± 0.10000' in visible
        if name=='curve':
            # SVG must contain disconnected segments, not a line bridging a
            # missing middle value. This oracle can detect a wrong renderer
            # even if the server and standalone script make the same mistake.
            paths=[e for e in svg.iter() if e.tag.endswith('}path') and e.get('clip-path')
                   and 'stroke: #1f77b4' in e.get('style','')]
            assert any(p.get('d','').count('M')==2 and 'L' not in p.get('d','') for p in paths)
            for flag,value in [('--font-size','0'),('--width','nan'),('--height','-2'),('--dpi','0'),('--font','missing.ttf')]:
                bad=subprocess.run([sys.executable,'-c',invocation,str(host/script.name),flag,value],
                                   cwd=folder,env=env,capture_output=True,text=True,timeout=10)
                assert bad.returncode!=0 and 'error:' in bad.stderr
        assert all(hashlib.sha256((host/n).read_bytes()).hexdigest()==h for n,h in frozen.items())
    # Deliberately unrelated styles and font choices must not leak across figures.
    assert dict(plt.rcParams)==original_style
print('All plot templates replayed: curve, bar, spectrum, scan, mesh, convergence; source unchanged.')
