"""Native OpenMC checks for new combinations, axes and relocatable Python rebuild.

The real solver is gated; no model/provider calls and no professional acceptance.
"""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'mcp'))
from openmc_model_spec import validate_spec

RUNTIME=os.environ.get('MC_TEST_OPENMC_CONFIG')


@unittest.skipUnless(RUNTIME,'MC_TEST_OPENMC_CONFIG启用真实组合模型生成/运行')
class ModelSpecReal(unittest.TestCase):
    def test_changed_layout_builds_relocates_and_computes(self):
        cfg=json.loads(Path(RUNTIME).read_text());python=cfg['python_command'][0]
        with tempfile.TemporaryDirectory(prefix='mc model spec ') as tmp:
            d=Path(tmp);s=json.loads((ROOT/'examples/model-specs/rectangular-assembly.json').read_text())
            # Not the checked-in demo: transposed dimensions/new material ratio.
            s['lattices'][0]['universes']=[[1,2],[2,1],[1,1]]
            s['lattices'][0]['lower_left']=[-1.2,-1.8]
            s['surfaces'][0]['parameters']['x0']=-1.2;s['surfaces'][1]['parameters']['x0']=1.2
            s['surfaces'][2]['parameters']['y0']=-1.8;s['surfaces'][3]['parameters']['y0']=1.8
            s['settings']['source']={'type':'point','point':[0,0,0]}
            s['tallies'][0]['filters'][0].update(dimension=[2,3,1],lower_left=[-1.2,-1.8,-5],upper_right=[1.2,1.8,5])
            s['materials'][0]['nuclides'][0]['fraction']=0.06
            validate_spec(s)
            (d/'model-spec.json').write_text(json.dumps(s));shutil.copy(ROOT/'mcp/openmc_model_spec.py',d/'build_model.py')
            generated=subprocess.run([python,'build_model.py'],cwd=d,capture_output=True,text=True,timeout=60)
            self.assertEqual(generated.returncode,0,generated.stderr)
            # Confirm axes against the original declaration, not flattened output order.
            inspect="""import openmc,json
m=openmc.Model.from_xml();l=m.geometry.get_all_lattices()[10]
print(json.dumps({'rows':[[int(u.id) for u in row] for row in l.universes],'pitch':list(l.pitch),'nuclides':m.materials[0].get_nuclide_atom_densities()}))
"""
            out=subprocess.run([python,'-c',inspect],cwd=d,capture_output=True,text=True,timeout=60)
            self.assertEqual(out.returncode,0,out.stderr)
            read=json.loads(out.stdout.strip().splitlines()[-1]);self.assertEqual(read['rows'],s['lattices'][0]['universes'])
            self.assertAlmostEqual(read['nuclides']['U235']/read['nuclides']['U238'],0.06/0.95,places=12)
            self.assertAlmostEqual(read['nuclides']['O16']/read['nuclides']['U238'],2.0/0.95,places=12)
            moved=d/'relocated';moved.mkdir()
            for name in ('build_model.py','model-spec.json'):shutil.copy(d/name,moved/name)
            rerun=subprocess.run([python,'build_model.py'],cwd=moved,capture_output=True,text=True,timeout=60)
            self.assertEqual(rerun.returncode,0,rerun.stderr)
            for name in ('geometry.xml','materials.xml','settings.xml','tallies.xml'):
                self.assertEqual((d/name).read_bytes(),(moved/name).read_bytes())
            env={**os.environ,'OMP_NUM_THREADS':'1'}
            if not env.get('OPENMC_CROSS_SECTIONS'):
                env['OPENMC_CROSS_SECTIONS']=str(ROOT/'.runtime/nuclear-data'/cfg.get('nuclear_data_library','official-endfb-vii.1')/'cross_sections.xml')
            done=subprocess.run(cfg['openmc_command'],cwd=moved,env=env,capture_output=True,text=True,timeout=180)
            self.assertEqual(done.returncode,0,done.stdout[-2000:]+done.stderr[-1000:])
            check="""import openmc,json,numpy as np
with openmc.StatePoint('statepoint.12.h5') as sp:
 t=sp.get_tally(id=1);print(json.dumps({'k':sp.keff.nominal_value,'shape':list(t.mean.shape),'finite':bool(np.isfinite(t.mean).all()),'nonzero':bool((t.mean>0).all())}))
"""
            out=subprocess.run([python,'-c',check],cwd=moved,env=env,capture_output=True,text=True,timeout=60)
            self.assertEqual(out.returncode,0,out.stderr)
            data=json.loads(out.stdout.strip().splitlines()[-1]);self.assertEqual(data['shape'],[6,1,1]);self.assertTrue(data['finite']);self.assertTrue(data['nonzero']);self.assertGreater(data['k'],0)

    def test_parameter_update_keeps_python_source_and_spec_in_sync_and_refuses_drift(self):
        cfg=json.loads(Path(RUNTIME).read_text());python=cfg['python_command'][0]
        script = r"""
import hashlib,json,os,pathlib,shutil,tempfile
from openmc_model_spec import build
from openmc_parameters import load_model,apply,write_parameters
root=pathlib.Path.cwd()
spec=json.loads(pathlib.Path(os.environ['SPEC_INPUT']).read_text())
build(spec)
original={p.name:p.read_bytes() for p in root.glob('*.xml')}
# A saved script is not trusted executable input to any tool action.
(root/'build_model.py').write_text("raise RuntimeError('copied code executed')")
parameters={'materials.2.density':0.65,'materials.1.temperature':293.6,
 'geometry.surfaces.7.r':0.39,'settings.particles':321,
 'settings.generations_per_batch':2,'settings.source.0.strength':1.7}
model=load_model();apply(model,parameters);write_parameters(model,parameters)
updated=json.loads((root/'model-spec.json').read_text())
assert updated['materials'][1]['density']==0.65
assert updated['surfaces'][6]['parameters']['r']==0.39
assert updated['settings']['particles']==321
assert updated['settings']['generations_per_batch']==2
assert updated['settings']['source']['strength']==1.7
model=load_model()
assert model.materials[1].density==0.65
assert model.materials[0].temperature==293.6
assert model.geometry.get_all_surfaces()[7].r==0.39
assert model.settings.generations_per_batch==2
assert model.settings.source[0].strength==1.7
marker=json.loads((root/'mc-case.json').read_text())
assert marker['model_spec_sha256']==hashlib.sha256((root/'model-spec.json').read_bytes()).hexdigest()
for name,digest in marker['model_xml_sha256'].items():assert hashlib.sha256((root/name).read_bytes()).hexdigest()==digest
# Rebuild only from exported source and spec in a relocated directory.
rebuild=root/'relocated';rebuild.mkdir()
for name in ('model-spec.json','build_model.py'):shutil.copy(root/name,rebuild/name)
import subprocess,sys
p=subprocess.run([sys.executable,'build_model.py'],cwd=rebuild,capture_output=True,text=True)
assert p.returncode==0,p.stderr
for name in marker['model_xml_sha256']:assert (root/name).read_bytes()==(rebuild/name).read_bytes(),name
# Native edit must not be silently overwritten by stale declarative source.
settings=root/'settings.xml';settings.write_text(settings.read_text().replace('321','322'))
before={p.name:p.read_bytes() for p in root.iterdir() if p.is_file()}
model=load_model();apply(model,{'settings.particles':333})
try:write_parameters(model,{'settings.particles':333})
except ValueError as exc:assert '漂移' in str(exc),exc
else:raise AssertionError('drifting XML accepted')
assert before=={p.name:p.read_bytes() for p in root.iterdir() if p.is_file()}
print('source/spec/XML synchronized; native drift rejected without modifications')
"""
        with tempfile.TemporaryDirectory(prefix='mc spec update ') as tmp:
            env={**os.environ,'PYTHONPATH':str(ROOT/'mcp'),
                 'SPEC_INPUT':str(ROOT/'examples/model-specs/rectangular-assembly.json')}
            done=subprocess.run([python,'-c',script],cwd=tmp,env=env,capture_output=True,text=True,timeout=60)
            self.assertEqual(done.returncode,0,done.stdout[-1000:]+done.stderr[-2000:])

    def test_scan_preflight_rejects_unmappable_combinations_and_drift_without_records(self):
        cfg=json.loads(Path(RUNTIME).read_text());python=cfg['python_command'][0]
        script = r"""
import json,os,pathlib
from openmc_model_tools import build_model
from openmc_scan import submit_scan,prepare_case
root=pathlib.Path(os.environ['MC_WORKSPACE'])
spec=json.loads(pathlib.Path(os.environ['SPEC_INPUT']).read_text())
build_model('case',spec)
for axes in ({'settings.inactive':[1]}, {'settings.particles':[100000001]},
             {'geometry.lattices.1.pitch':[1.5]}, {'geometry.surfaces.1.r':[-1.0]}):
 try:submit_scan('case',axes)
 except ValueError:pass
 else:raise AssertionError('invalid combination accepted: '+str(axes))
assert not (root/'.mc-scans').exists()
path=root/'case/settings.xml';path.write_text(path.read_text().replace('<particles>200</particles>','<particles>201</particles>'))
before={p.name:p.read_bytes() for p in (root/'case').iterdir() if p.is_file()}
for action in (lambda:submit_scan('case',{'settings.particles':[202]}),
               lambda:prepare_case('case','modified',{'settings.particles':202})):
 try:action()
 except ValueError as exc:assert '漂移' in str(exc),exc
 else:raise AssertionError('drift accepted')
assert not (root/'.mc-scans').exists()
assert not (root/'modified').exists()
assert before=={p.name:p.read_bytes() for p in (root/'case').iterdir() if p.is_file()}
print('scan/prepare preflight rejects drift and unsupported combinations without records')
"""
        with tempfile.TemporaryDirectory(prefix='mc spec preflight ') as tmp:
            env={**os.environ,'PYTHONPATH':str(ROOT/'mcp'),'MC_WORKSPACE':tmp,
                 'MC_OPENMC_CONFIG':RUNTIME,
                 'SPEC_INPUT':str(ROOT/'examples/model-specs/fixed-source-water.json')}
            done=subprocess.run([str(ROOT/'mcp/.venv/bin/python'),'-c',script],cwd=tmp,env=env,capture_output=True,text=True,timeout=60)
            self.assertEqual(done.returncode,0,done.stdout[-1000:]+done.stderr[-2000:])
