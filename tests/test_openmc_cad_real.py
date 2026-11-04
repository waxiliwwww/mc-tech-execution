"""实跑档：用已配置的CAD环境完成一次真实STEP→h5m→引擎读回；工程验证，不是专业验收。

启用：MC_TEST_CAD_CONFIG 指向含 cad_python_command/cad_library_path 与真实 python_command
（DAGMC版OpenMC环境）的配置文件，例如仓库的 config/openmc.local.json。
夹具 tests/fixtures/cube.step 为 cadquery 生成的 10 mm 立方体（来源与指纹见 docs/adr/0002）。
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'mcp'))
import business
import openmc_cad as cad


@unittest.skipUnless(os.environ.get('MC_TEST_CAD_CONFIG'),
                     '真实CAD转换需MC_TEST_CAD_CONFIG指向含cad_python_command的配置')
class CadRealConversion(unittest.TestCase):
    def setUp(self):
        # Explicit removal lets failed cancellation preserve its record directory.
        self.root = Path(tempfile.mkdtemp(prefix='mc cad real '))
        self.owned_jobs = []
        self.previous_workspace = business.WORKSPACE
        business.WORKSPACE = self.root / 'workspace'
        business.WORKSPACE.mkdir()
        previous = {key: os.environ.get(key) for key in ('MC_WORKSPACE', 'MC_OPENMC_CONFIG')}

        def restore():
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self.addCleanup(restore)
        os.environ['MC_WORKSPACE'] = str(business.WORKSPACE)
        os.environ['MC_OPENMC_CONFIG'] = os.environ['MC_TEST_CAD_CONFIG']
        shutil.copy(ROOT / 'tests/fixtures/cube.step', business.WORKSPACE / 'cube.step')

    def tearDown(self):
        import openmc_jobs as jobs
        try:
            for job_id in self.owned_jobs:
                status = jobs.status(job_id)
                if status['state'] in jobs.TERMINAL:
                    continue
                jobs.cancel(job_id)
                deadline = time.monotonic() + 15
                while status['state'] not in jobs.TERMINAL:
                    status = jobs.status(job_id)
                    if status['state'] in jobs.TERMINAL:
                        break
                    if time.monotonic() >= deadline:
                        self.fail('测试自建作业取消后未确认终态；保留目录：' + str(self.root)
                                  + '；job_id=' + job_id)
                    time.sleep(0.1)
            shutil.rmtree(self.root)
        finally:
            business.WORKSPACE = self.previous_workspace

    def test_inspect_convert_and_engine_read(self):
        inspected = cad.inspect_step('cube.step')
        self.assertEqual(inspected['solids'], 1)
        self.assertIsNotNone(inspected['bounding_box'])
        # 10 mm 立方体按 0.1 缩放为 1 cm，OpenMC 读回厘米制包围盒 ±0.5。
        value = cad.convert_cad('cube.step', 'cube-dagmc', ['steel'], scale_factor=0.1)
        self.assertTrue(value['engine_read']['ok'], value['engine_read'])
        box = value['engine_read']['bounding_box']
        for axis in range(3):
            self.assertAlmostEqual(box['lower_left'][axis], -0.5, places=6)
            self.assertAlmostEqual(box['upper_right'][axis], 0.5, places=6)
        self.assertEqual(value['provenance']['data_kind'], 'cad_conversion_unverified')
        self.assertFalse(value['professional_acceptance'])
        # A5 后续：由h5m组装可运行model.xml，引擎回读验证，DAGMC文件名相对。
        built = cad.build_dagmc_model(
            'cube-dagmc', [{'name': 'steel', 'density': 7.8, 'units': 'g/cm3',
                            'nuclides': [{'name': 'Fe56', 'fraction': 1.0}]}],
            particles=50, batches=6, inactive=2)
        self.assertEqual(built['files'], ['model.xml'])
        self.assertEqual(built['dagmc_filename'], 'dagmc.h5m')
        self.assertTrue(built['engine_parse']['ok'], built['engine_parse'])
        text = (business.WORKSPACE / 'cube-dagmc/model.xml').read_text()
        self.assertIn('dagmc_universe', text)
        self.assertIn('filename="dagmc.h5m"', text)
        marker = json.loads((business.WORKSPACE / 'cube-dagmc/mc-case.json').read_text())
        self.assertIn('model_built', marker)
        self.assertFalse(marker['model_built']['professional_acceptance'])
        self.assertEqual(marker['model_built']['boundary']['type'], 'vacuum')
        self.assertAlmostEqual(marker['model_built']['boundary']['padding_cm'], 0.1)
        # 不覆盖：再组装一次必须拒绝并说明。
        with self.assertRaises(business.MCInputError):
            cad.build_dagmc_model('cube-dagmc', [{'name': 'steel', 'density': 7.8, 'units': 'g/cm3'}])

    def test_same_step_transport_and_selected_artifacts_match_native_statepoint(self):
        """Same converted input, actual transport and native numerical oracle; no model calls."""
        import openmc_tools as tools
        import openmc_jobs as jobs
        import openmc_plots as plots
        import hashlib
        import time

        converted = cad.convert_cad('cube.step', 'transport', ['fuel'], scale_factor=0.1)
        model = cad.build_dagmc_model('transport', [
            {'name': 'fuel', 'density': 18.95, 'units': 'g/cm3',
             'nuclides': [{'name': 'U235', 'fraction': 1.0, 'percent_type': 'ao'}]}],
            particles=1000, batches=12, inactive=2, source_point=[0, 0, 0], seed=42)
        directory = business.WORKSPACE / 'transport'
        self.assertEqual(model['boundary']['type'], 'vacuum')
        h5m_hash = hashlib.sha256((directory / 'dagmc.h5m').read_bytes()).hexdigest()
        self.assertEqual(h5m_hash, converted['sha256'])
        marker = json.loads((directory / 'mc-case.json').read_text())
        self.assertEqual(marker['source_step']['sha256'],
                         hashlib.sha256((business.WORKSPACE / 'cube.step').read_bytes()).hexdigest())

        import xml.etree.ElementTree as ET
        before = ET.parse(directory / 'model.xml').getroot()
        snapshot = {tag: ET.tostring(before.find(tag)) for tag in ('geometry', 'materials', 'settings')}
        defined = tools.define_tally('transport', ['flux'], name='CAD空间通量', filters=[
            {'type': 'mesh', 'dimension': [4, 4, 1],
             'lower_left': [-0.5, -0.5, -0.5], 'upper_right': [0.5, 0.5, 0.5]}])
        self.assertEqual(defined['input_file'], 'model.xml')
        after = ET.parse(directory / 'model.xml').getroot()
        for tag in snapshot:
            self.assertEqual(snapshot[tag], ET.tostring(after.find(tag)), tag)
        self.assertFalse((directory / 'tallies.xml').exists())
        self.assertTrue(tools.validate_inputs('transport', api_check=True)['structure_valid'])
        submitted = tools.submit_job('transport', name='CAD同输入实跑门控')
        self.owned_jobs.append(submitted['job_id'])
        deadline = time.monotonic() + 120
        while True:
            status = jobs.status(submitted['job_id'])
            if status['state'] in jobs.TERMINAL:
                break
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.1)
        self.assertEqual(status['state'], 'completed', status)
        self.assertEqual(status['input_hashes']['dagmc.h5m'], h5m_hash)
        self.assertEqual(status['input_hashes']['model.xml'],
                         hashlib.sha256((directory / 'model.xml').read_bytes()).hexdigest())
        result = tools.read_results(submitted['job_id'])
        chart = plots.plot_mesh(submitted['job_id'], tally_id=defined['tally_id'], score='flux')
        report = tools.export_report(submitted['job_id'], tally_id=defined['tally_id'],
                                     score='flux', nuclide='total')
        self.assertEqual(chart['cells'], 16)
        self.assertEqual(report['points'], 16)
        report_text = Path(report['server_path']).read_text()
        self.assertIn('particles=1000', report_text)
        self.assertIn('随机种子：42', report_text)
        self.assertIn('cad_to_dagmc', report_text)
        job_dir = jobs.job_directory(submitted['job_id'])
        native_check = r'''
import csv,hashlib,json,re,sys
from pathlib import Path
import numpy as np
import openmc
value=json.load(sys.stdin);work=Path(value['workspace']);job=Path(value['job'])
result=value['result'];mesh=json.loads((work/value['chart']['values_json']).read_text())
series=json.loads((work/value['report']['series_json']).read_text())
point_path=work/result['statepoint']
assert hashlib.sha256(point_path.read_bytes()).hexdigest()==result['statepoint_sha256']
with openmc.StatePoint(point_path,autolink=False) as point:
    tally=point.get_tally(id=value['tally_id'])
    mean=tally.mean.ravel();std=tally.std_dev.ravel()
    np.testing.assert_allclose(np.asarray(mesh['values']).ravel(),mean,rtol=1e-12)
    np.testing.assert_allclose(np.asarray(mesh['errors']).ravel(),std,rtol=1e-12)
    np.testing.assert_allclose(series['mean'],mean,rtol=1e-12)
    np.testing.assert_allclose(series['std_dev'],std,rtol=1e-12)
    np.testing.assert_allclose(mesh['x_edges'],np.linspace(-.5,.5,5))
    np.testing.assert_allclose(mesh['y_edges'],np.linspace(-.5,.5,5))
    np.testing.assert_allclose([result['keff']['mean'],result['keff']['std_dev']],
                               [point.keff.nominal_value,point.keff.std_dev],rtol=1e-12)
rows=list(csv.DictReader((work/value['chart']['values_csv']).open()))
for row in rows:
    i,j=int(row['i']),int(row['j']);index=i+4*j
    np.testing.assert_allclose(float(row['value']),mean[index],rtol=1e-8)
    np.testing.assert_allclose(float(row['std_dev']),std[index],rtol=1e-8)
    assert abs(float(row['x_center'])-(-.375+.25*i))<1e-12
    assert abs(float(row['y_center'])-(-.375+.25*j))<1e-12
for image in [work/value['chart']['path'],work/value['report']['chart']]:
    assert image.read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
text=Path(value['report']['server_path']).read_text()
link=re.search(r'!\[.*?\]\((.*?)\)',text).group(1)
assert (job/link).is_file()
print('Native StatePoint, 16 mesh values/errors/coordinates, selected report and images agree.')
'''
        checked = subprocess.run(
            cad.config()['python_command'] + ['-c', native_check], text=True,
            input=json.dumps({'workspace': str(business.WORKSPACE), 'job': str(job_dir),
                              'tally_id': defined['tally_id'], 'result': result,
                              'chart': chart, 'report': report}),
            capture_output=True, timeout=60)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)


if __name__ == '__main__':
    unittest.main()
