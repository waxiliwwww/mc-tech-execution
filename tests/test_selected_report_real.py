"""Selected data, coordinates and actual report content must agree with OpenMC."""
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = os.environ.get('MC_TEST_OPENMC_CONFIG')


@unittest.skipUnless(RUNTIME, '设置MC_TEST_OPENMC_CONFIG核对真实所选结果报告')
class SelectedReportReal(unittest.TestCase):
    @classmethod
    def call(cls, name, arguments, success=True):
        result = subprocess.run([sys.executable, str(ROOT/'mcp/cli.py'), 'call', name,
                                 json.dumps(arguments)], env=cls.env, capture_output=True,
                                text=True, timeout=180)
        if not success:
            if not result.returncode:raise AssertionError('expected failure: '+name)
            return result.stderr
        if result.returncode:raise AssertionError(result.stderr)
        return json.loads(result.stdout)

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='mc selected report ')
        cls.folder = Path(cls.temp.name)
        cls.workspace = cls.folder/'workspace'
        cls.runtime = json.loads(Path(RUNTIME).read_text())
        cls.env = {k:v for k,v in os.environ.items() if not k.startswith(('MC_', 'MCP_', 'HARNESS_'))}
        cls.env.update(MC_WORKSPACE=str(cls.workspace), MC_OPENMC_CONFIG=str(Path(RUNTIME).resolve()))
        cls.call('openmc_create_model', {'case_dir':'input'})
        cell = ET.parse(cls.workspace/'input/geometry.xml').getroot().find('cell').get('id')
        (cls.workspace/'input/tallies.xml').write_text(f'''<tallies>
<filter id="10" type="energy"><bins>0 1000000 20000000</bins></filter>
<filter id="11" type="cell"><bins>{cell}</bins></filter>
<mesh id="12" type="regular"><dimension>2 3</dimension><lower_left>-10 -10</lower_left><upper_right>10 10</upper_right></mesh>
<filter id="12" type="mesh"><bins>12</bins></filter>
<tally id="1"><filters>12</filters><scores>flux</scores></tally>
<tally id="2"><filters>10</filters><scores>flux fission</scores></tally>
<tally id="3"><filters>10 11</filters><scores>flux</scores></tally>
</tallies>''')
        cls.job = cls.call('openmc_submit_job', {'case_dir':'input'})['job_id']
        deadline = time.monotonic()+90
        while time.monotonic()<deadline:
            state = cls.call('openmc_job_status', {'job_id':cls.job})
            if state['state'] in ('completed','failed','interrupted','cancelled'):break
            time.sleep(.2)
        if state['state']!='completed':raise AssertionError(state)
        cls.record = cls.workspace/'.mc-jobs'/cls.job

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_job_summary_docx_and_pdf_include_two_dimensional_mesh_and_energy(self):
        plot = self.call('openmc_plot_results', {'job_id': self.job, 'tally_id': 1, 'score': 'flux'})
        series = json.loads((self.workspace/plot['series_json']).read_text())
        self.assertEqual(len(series['mean']), 6)
        self.assertEqual(series['mesh']['spatial_dimension'], 2)
        self.assertEqual(series['mesh_centers'][0][::2], [-5.0, 0.0])
        self.assertAlmostEqual(series['mesh_centers'][0][1], -20/3)
        self.assertEqual(series['mesh_indices'][5], [1, 2, 0])
        for format in ['docx', 'pdf']:
            result = self.call('openmc_export_report', {'job_id': self.job, 'format': format})
            path = self.workspace/result['path']
            self.assertGreater(path.stat().st_size, 1000)
            if format == 'docx':
                with zipfile.ZipFile(path) as archive:
                    text = archive.read('word/document.xml').decode()
                    self.assertIn('tally 1 / flux', text)
                    self.assertIn('tally 2 / flux', text)
                    self.assertIn('tally 3 / flux', text)
                    self.assertTrue(any(n.startswith('word/media/') for n in archive.namelist()))
            else:
                from pypdf import PdfReader
                text = ''.join(page.extract_text() for page in PdfReader(path).pages)
                self.assertIn('tally 2 / flux', text)

    def test_selected_reports_use_the_requested_second_tally_in_all_formats(self):
        from pypdf import PdfReader
        import olefile
        selection = {'tally_id':2,'score':'fission','nuclide':'total',
                     'filter_selection':[{'type':'EnergyFilter','bins':[[1000000,20000000],[0,1000000]]}]}
        chart = self.call('openmc_plot_results', {'job_id':self.job,**selection})
        series = json.loads((self.workspace/chart['series_json']).read_text())
        point = next((self.record/'work').glob('statepoint.*.h5'))
        # Independent native API: no toolkit selection or coordinate helper.
        script = '''import openmc,json,numpy as np,sys
with openmc.StatePoint(sys.argv[1],autolink=False) as p:
 t=p.get_tally(id=2)
 opts=dict(scores=['fission'],nuclides=['total'],filters=[openmc.EnergyFilter],filter_bins=[((1000000,20000000),(0,1000000))])
 finite=lambda a:[float(x) if np.isfinite(x) else None for x in a.ravel()]
 print(json.dumps({'mean':finite(t.get_values(value='mean',**opts)),'std_dev':finite(t.get_values(value='std_dev',**opts))}))
'''
        native = json.loads(subprocess.check_output(self.runtime['python_command']+['-c',script,str(point)],text=True))
        self.assertEqual(series['mean'],native['mean']);self.assertEqual(series['std_dev'],native['std_dev'])
        self.assertEqual(series['x_lower'],[1000000.,0.])
        self.assertEqual(series['x_upper'],[20000000.,1000000.])
        self.assertEqual(series['x'],[10500000.,500000.])
        self.assertEqual(series['x_scale'],'linear')
        self.assertIn('eV',series['x_label'])
        for format in ['markdown','csv','docx','pdf','doc']:
            output = self.call('openmc_export_report', {'job_id':self.job,'format':format,**selection})
            saved = json.loads((self.workspace/output['series_json']).read_text())
            for key in ['tally_id','score','nuclide','filter_selection','x','x_lower','x_upper','mean','std_dev']:
                self.assertEqual(saved[key],series[key],(format,key))
            with (self.workspace/output['series_csv']).open() as source:
                rows = list(csv.DictReader(source))
            self.assertEqual(len(rows),2)
            self.assertEqual([float(r['x']) for r in rows],series['x'])
            path = self.workspace/output['path']
            if format=='csv':self.assertEqual(path.read_bytes(),(self.workspace/output['series_csv']).read_bytes())
            elif format=='markdown':
                text = path.read_text();self.assertIn('tally 2 / fission / total',text)
                self.assertIn(Path(output['series_csv']).name,text);self.assertIn('eV',text)
                self.assertNotIn('tally 1 / flux',text)
            elif format=='docx':
                with zipfile.ZipFile(path) as package:
                    document = ET.fromstring(package.read('word/document.xml'))
                    self.assertEqual(package.read('word/media/image1.png'),(self.workspace/output['chart']).read_bytes())
                ns = {'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
                text = '\n'.join(''.join(p.itertext()) for p in document.findall('.//w:p',ns))
                self.assertIn('tally 2 / fission / total',text)
                self.assertIn(Path(output['series_csv']).name,text)
                table_rows = [[''.join(c.itertext()) for c in row.findall('w:tc',ns)] for row in document.findall('.//w:tr',ns)]
                selected = [row for row in table_rows if row[0]=='tally 2 / fission']
                self.assertEqual(len(selected),2)
                for index,cells in enumerate(selected):
                    self.assertEqual(cells[2],format_number(native['mean'][index]))
                    self.assertEqual(cells[3],format_number(native['std_dev'][index]))
            elif format=='pdf':
                import pymupdf
                text='\n'.join(p.extract_text() for p in PdfReader(path).pages)
                self.assertIn('tally 2 / fission / total',text)
                self.assertIn('1000000',text);self.assertIn('20000000',text)
                self.assertNotIn('tally 1 / flux',text)
                expected = pymupdf.Pixmap(str(self.workspace/output['chart']))
                if expected.alpha:expected=pymupdf.Pixmap(expected,0)
                matches=[]
                with pymupdf.open(path) as pdf:
                    for page in pdf:
                        for item in page.get_images():
                            actual=pymupdf.Pixmap(pdf,item[0])
                            if actual.alpha:actual=pymupdf.Pixmap(actual,0)
                            matches.append((actual.width,actual.height,actual.samples)==
                                           (expected.width,expected.height,expected.samples))
                self.assertIn(True,matches,'PDF嵌图必须保持所选图的原始像素')
            elif format=='doc':
                with olefile.OleFileIO(path) as ole:self.assertTrue(ole.exists('WordDocument'))
        first = self.call('openmc_export_report', {'job_id':self.job,'format':'markdown',**selection})
        second = self.call('openmc_export_report', {'job_id':self.job,'format':'markdown',**selection})
        self.assertEqual(second['chart'],first['chart'])
        self.assertEqual(second['path'],first['path'])
        self.assertEqual(second['series_json'],first['series_json'])
        old = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.record.glob('report.*') if p.suffix!='.lock'}
        error = self.call('openmc_export_report', {'job_id':self.job,'format':'pdf','tally_id':2,'score':'absent'},False)
        self.assertIn('score或nuclide不存在',error)
        self.assertEqual(old,{name:hashlib.sha256((self.record/name).read_bytes()).hexdigest() for name in old})

    def test_multifilter_coordinates_are_explicit_flat_indices(self):
        output = self.call('openmc_plot_results', {'job_id':self.job,'tally_id':3,'score':'flux'})
        data = json.loads((self.workspace/output['series_json']).read_text())
        self.assertEqual(data['x'],[0,1])
        self.assertEqual(data['x_kind'],'filter_bin_index')
        self.assertIn('EnergyFilter',data['x_label']);self.assertIn('CellFilter',data['x_label'])
        self.assertNotIn('eV',data['x_label'])

    def test_explicit_empty_selection_is_not_the_legacy_summary(self):
        output = self.call('openmc_export_report', {'job_id':self.job,'filter_selection':[],'format':'csv'})
        self.assertEqual(output['selection_mode'],'selected')
        self.assertEqual(output['selection']['tally_id'],1)
        with (self.workspace/output['path']).open() as source:self.assertEqual(len(list(csv.DictReader(source))),6)
        legacy = self.call('openmc_export_report', {'job_id':self.job,'format':'csv'})
        self.assertEqual(legacy['selection_mode'],'job_summary')
        with (self.workspace/legacy['path']).open() as source:
            self.assertEqual({r['tally_id'] for r in csv.DictReader(source) if r['quantity']=='tally_raw'},{'1','2','3'})


def format_number(value):return '不可用' if value is None else format(value,'.9g')
