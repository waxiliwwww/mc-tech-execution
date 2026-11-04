"""Installed wheel executes model generation outside the source tree.

Uses an isolated pip --target directory and existing explicitly configured engine;
never installs into the shared venv. No solver or paid provider is called.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
RUNTIME=os.environ.get('MC_TEST_OPENMC_CONFIG')


@unittest.skipUnless(RUNTIME,'MC_TEST_OPENMC_CONFIG启用独立wheel安装和模型API实测')
class InstalledModelRuntime(unittest.TestCase):
    def test_installed_wheel_builds_and_reads_custom_model_from_another_directory(self):
        with tempfile.TemporaryDirectory(prefix='mc installed model ') as temp:
            d=Path(temp);wheels=d/'wheels';site=d/'site';workspace=d/'workspace'
            cfg=json.loads(Path(RUNTIME).read_text())
            built=subprocess.run(cfg['python_command']+['-m','pip','wheel','--no-deps','--no-build-isolation',
                                  '--wheel-dir',str(wheels),str(ROOT)],cwd=d,text=True,capture_output=True,timeout=120)
            self.assertEqual(built.returncode,0,built.stdout+built.stderr)
            wheel=next(wheels.glob('*.whl'))
            installed=subprocess.run([sys.executable,'-m','pip','install','--no-deps','--target',str(site),str(wheel)],
                                     cwd=d,text=True,capture_output=True,timeout=120)
            self.assertEqual(installed.returncode,0,installed.stdout+installed.stderr)
            spec=json.loads((ROOT/'examples/model-specs/rectangular-assembly.json').read_text())
            spec['settings']['particles']=321
            spec['materials'][1]['density']=0.65
            args={'case_dir':'new-combination','spec':spec}
            input_file=d/'input.json';input_file.write_text(json.dumps(args))
            env={**os.environ,'PYTHONPATH':str(site),'MC_WORKSPACE':str(workspace),
                 'MC_OPENMC_CONFIG':str(Path(RUNTIME).resolve()),
                 'OPENMC_CROSS_SECTIONS':str(ROOT/'.runtime/nuclear-data'/cfg.get('nuclear_data_library','official-endfb-vii.1')/'cross_sections.xml')}
            script="import json,pathlib,sys;import mc_agent_tools.launcher as x;assert str(x.__file__).startswith(sys.argv[2]);args=json.loads(pathlib.Path(sys.argv[1]).read_text());x.main(['call','openmc_build_model',json.dumps(args)])"
            call=subprocess.run([sys.executable,'-c',script,str(input_file),str(site)],cwd=d,env=env,text=True,capture_output=True,timeout=120)
            self.assertEqual(call.returncode,0,call.stdout+call.stderr)
            result=json.loads(call.stdout);self.assertEqual(result['case_dir'],'new-combination')
            case=workspace/'new-combination'
            self.assertTrue((case/'build_model.py').is_file())
            read="import openmc,json;m=openmc.Model.from_xml();print(json.dumps({'particles':m.settings.particles,'density':m.materials[1].density}))"
            native=subprocess.run(cfg['python_command']+['-c',read],cwd=case,env=env,text=True,capture_output=True,timeout=60)
            self.assertEqual(native.returncode,0,native.stderr)
            value=json.loads(native.stdout.strip().splitlines()[-1]);self.assertEqual(value['particles'],321);self.assertEqual(value['density'],0.65)
