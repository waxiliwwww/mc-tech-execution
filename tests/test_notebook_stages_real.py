"""Real OpenMC calculations keep independent results when filenames are reused."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
RUNTIME=os.environ.get('MC_TEST_OPENMC_CONFIG')

@unittest.skipUnless(RUNTIME,'设置MC_TEST_OPENMC_CONFIG运行真实求解阶段留存验证')
class NotebookStageResults(unittest.TestCase):
    def test_overwritten_results_and_late_capi_write_remain_independently_readable(self):
        code=r'''
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import nbformat
from notebook_runner import execute_notebook
cells=[
 "import openmc; model=openmc.examples.random_ray_pin_cell(); model.settings.particles=80; model.settings.batches=8; model.settings.inactive=2; model.run(output=False,threads=1); first=openmc.StatePoint('statepoint.8.h5'); print(first.n_particles); first.close()",
 "model.settings.particles=120; model.run(output=False,threads=1); second=openmc.StatePoint('statepoint.8.h5'); print(second.n_particles); second.close()",
 "model=openmc.examples.pwr_pin_cell(); model.settings.particles=120; model.settings.batches=8; model.settings.inactive=2; model.export_to_model_xml(); import openmc.lib; openmc.lib.init(output=False); openmc.lib.run(output=False); openmc.lib.statepoint_write('late-result.h5'); openmc.lib.finalize(); last=openmc.StatePoint('late-result.h5'); print(last.n_particles); last.close()"
]
nbformat.write(nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell(x) for x in cells]),'fixture.ipynb')
result=execute_notebook({'id':'notebook-stage-fixture','name':'stage fixture','source_file':'fixture.ipynb','source_revision':'private-test'})
assert result['executed_code_cells']==3 and not result['errors'],result
stages=json.loads(Path('notebook-stages.json').read_text())
solves=[x for x in stages['calculations'] if x['state']=='completed']
assert [x['kind'] for x in solves]==['transport','transport','library_transport'],solves
import openmc
counts=[]
for stage in solves:
 points=[meta for meta in stage['outputs'].values() if meta['role']=='statepoint']
 assert points,stage
 with openmc.StatePoint(points[0]['path'],autolink=False) as point:counts.append(int(point.n_particles))
assert counts==[80,120,120],counts
assert 'late-result.h5' in solves[-1]['outputs']
assert [x['matched_snapshots'][0]['calculation_id'] for x in stages['reads'][:3]]==[x['id'] for x in solves]
report=Path('notebook-report.html').read_text()
for stage in solves:assert next(iter(stage['outputs'].values()))['path'] in report
print(json.dumps({'counts':counts,'stage_ids':[x['id'] for x in solves]}))
'''
        cfg=json.loads(Path(RUNTIME).read_text())
        sys.path.insert(0,str(ROOT/'mcp'))
        from nuclear_data import runtime_env
        environment=runtime_env(cfg)
        with tempfile.TemporaryDirectory(prefix='mc independent solve results ') as folder:
            p=subprocess.run(cfg['python_command']+['-c',code,str(ROOT/'mcp')],cwd=folder,
                env={**environment,'MC_OPENMC_CONFIG':str(Path(RUNTIME).resolve()),'OMP_NUM_THREADS':'1'},
                capture_output=True,text=True,timeout=120)
            self.assertEqual(p.returncode,0,p.stdout+'\n'+p.stderr)

if __name__=='__main__':unittest.main()
