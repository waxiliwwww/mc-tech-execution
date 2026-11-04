"""Re-export an existing completed job; compare native data and all report heads, without solving.

Set MC_TEST_REPORT_JOB to a completed .mc-jobs/<id> directory with an explicit seed
and a finite total flux tally. Its runtime must provide OpenMC Python and Office.
All exports are made in a temporary copy; the source stays intact.
"""
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = os.environ.get('MC_TEST_REPORT_JOB')


def normalize(text):
    return ''.join(text.split())


def hashes(directory):
    return {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in directory.rglob('*') if p.is_file()}


@unittest.skipUnless(SOURCE, '设置MC_TEST_REPORT_JOB重导已有作业，不重算')
class SelectedReportMetadataReplay(unittest.TestCase):
    def test_selected_binary_reports_retain_the_input_metadata_data_and_chart(self):
        import pymupdf
        source = Path(SOURCE).resolve()
        original = hashes(source)
        record = json.loads((source/'job.json').read_text())
        self.assertEqual(record['state'], 'completed')
        with tempfile.TemporaryDirectory(prefix='mc report metadata replay ') as folder:
            directory = Path(folder)
            workspace = directory/'workspace'
            target = workspace/'.mc-jobs'/record['job_id']
            shutil.copytree(source, target)
            runtime = record['runtime']
            config = directory/'runtime.json'
            config.write_text(json.dumps(runtime))
            env = {k:v for k,v in os.environ.items() if not k.startswith(('MC_', 'MCP_', 'HARNESS_'))}
            env.update(MC_WORKSPACE=str(workspace), MC_OPENMC_CONFIG=str(config))

            def call(name, args):
                output = subprocess.run([sys.executable, str(ROOT/'mcp/cli.py'), 'call', name,
                                         json.dumps(args)], env=env, capture_output=True,
                                        text=True, timeout=180)
                self.assertEqual(output.returncode, 0, output.stderr)
                return json.loads(output.stdout)

            # Expected metadata comes from the actual submitted input and job record,
            # independently of _report_metadata and the report renderer.
            combined = target/'work/model.xml'
            settings = (ET.parse(combined).getroot().find('settings') if combined.is_file()
                        else ET.parse(target/'work/settings.xml').getroot())
            self.assertIsNotNone(settings)
            marker_file = target/'work/mc-case.json'
            marker = json.loads(marker_file.read_text()) if marker_file.exists() else {}
            self.assertTrue(settings.findtext('seed'), 'Replay fixture needs an explicit seed to detect loss.')
            expected = ['模板版本：', '参数：', 'OpenMC版本：', '运行时间：', '随机种子：'+settings.findtext('seed')]
            for key in ('run_mode', 'particles', 'batches', 'inactive', 'seed'):
                value = settings.findtext(key)
                if value is not None:expected.append(key+'='+value)
            generator = marker.get('generator') or marker.get('template')
            if generator:expected.append(generator)
            expected.append(marker.get('openmc_version') or runtime['expected_version'])
            for key in ('started_at', 'finished_at'):
                if record.get(key):expected.append(datetime.fromtimestamp(record[key],tz=timezone.utc).astimezone().isoformat(timespec='seconds'))

            point = max((target/'work').glob('statepoint.*.h5'),
                        key=lambda path: int(path.name.split('.')[1]))
            expected.append(hashlib.sha256(point.read_bytes()).hexdigest())
            script = '''import openmc,json,sys
with openmc.StatePoint(sys.argv[1],autolink=False) as p:
 t=next(t for t in p.tallies.values() if 'flux' in t.scores)
 print(json.dumps({'tally_id':int(t.id),'mean':t.get_values(scores=['flux'],nuclides=['total'],value='mean').ravel().tolist(),
                   'std_dev':t.get_values(scores=['flux'],nuclides=['total'],value='std_dev').ravel().tolist()}))
'''
            native = json.loads(subprocess.check_output(runtime['python_command']+['-c',script,str(point)],text=True))
            selection = {'job_id':record['job_id'],'tally_id':native['tally_id'],'score':'flux','nuclide':'total'}
            for format in ('markdown', 'docx', 'pdf', 'doc'):
                exported = call('openmc_export_report', {**selection,'format':format})
                path = workspace/exported['path']
                series = json.loads((workspace/exported['series_json']).read_text())
                self.assertEqual(series['mean'], native['mean'], format)
                self.assertEqual(series['std_dev'], native['std_dev'], format)
                with (workspace/exported['series_csv']).open() as input_file:
                    values = list(csv.DictReader(input_file))
                self.assertEqual(len(values), len(native['mean']), format)
                self.assertEqual([float(row['mean']) for row in values],native['mean'],format)
                self.assertEqual([float(row['std_dev']) for row in values],native['std_dev'],format)
                chart = workspace/exported['chart']
                if format == 'markdown':
                    text = path.read_text()
                    self.assertIn('![' , text)
                    self.assertIn(chart.name, text)
                elif format == 'docx':
                    with zipfile.ZipFile(path) as package:
                        xml = ET.fromstring(package.read('word/document.xml'))
                        self.assertEqual(package.read('word/media/image1.png'), chart.read_bytes())
                    text = ''.join(xml.itertext())
                elif format == 'doc':
                    # Check the actual Word97 output, using a fresh, isolated Office profile.
                    converted = directory/'doc-text'
                    converted.mkdir()
                    profile = directory/'doc-reader-profile'
                    process = subprocess.run(runtime['office_command']+[
                        '-env:UserInstallation='+profile.as_uri(),'--headless','--convert-to',
                        'txt:Text (encoded):UTF8','--outdir',str(converted),str(path)],
                        capture_output=True,text=True,timeout=120)
                    self.assertEqual(process.returncode,0,process.stderr)
                    text = (converted/'report.txt').read_text(encoding='utf-8-sig')
                else:
                    expected_chart = pymupdf.Pixmap(str(chart))
                    if expected_chart.alpha:expected_chart=pymupdf.Pixmap(expected_chart,0)
                    text_parts=[];matching_visible_images=[]
                    with pymupdf.open(path) as pdf:
                        for page in pdf:
                            text_parts.append(page.get_text())
                            for image in page.get_images():
                                if not page.get_image_rects(image[0]):continue
                                actual=pymupdf.Pixmap(pdf,image[0])
                                if actual.alpha:actual=pymupdf.Pixmap(actual,0)
                                matching_visible_images.append((actual.width,actual.height,actual.samples)==
                                                               (expected_chart.width,expected_chart.height,expected_chart.samples))
                    self.assertIn(True,matching_visible_images,'PDF needs the selected chart on a visible page.')
                    text='\n'.join(text_parts)
                for value in expected:self.assertIn(normalize(value),normalize(text),(format,value))
                self.assertIn('tally '+str(native['tally_id'])+' / flux / total',text)
            self.assertEqual(original,hashes(source),'Do not replace the original reports or source job.')


if __name__ == '__main__':unittest.main()
