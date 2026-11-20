"""Result-package user journey: native selection -> report/data -> download/replay.

Reuses the selected-report real fixture (one small synthetic calculation), no LLM.
Checks scientific selection and file content, not merely file creation. Every
bundle leaves original inputs, StatePoint and previous exports intact. Does not
establish physical accuracy, live-host adoption or production deployment.
"""
import base64
import csv
import hashlib
import io
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

import test_selected_report_real as fixture

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = os.environ.get('MC_TEST_OPENMC_CONFIG')


@unittest.skipUnless(RUNTIME,'MC_TEST_OPENMC_CONFIG启用结果包真实数据与报告核对')
class ResultBundleReal(unittest.TestCase):
    call = classmethod(fixture.SelectedReportReal.call.__func__)
    setUpClass = classmethod(fixture.SelectedReportReal.setUpClass.__func__)
    tearDownClass = classmethod(fixture.SelectedReportReal.tearDownClass.__func__)

    def bundle(self,**kwargs):
        return self.call('openmc_generate_result_bundle',{'job_id':self.job,'tally_id':1,'score':'flux',**kwargs})

    def validate(self,bundle):
        self.assertEqual(bundle['state'],'complete')
        folder=self.workspace/Path(bundle['manifest']).parent
        manifest=json.loads((folder/'manifest.json').read_text())
        for name,entry in manifest['files'].items():
            self.assertEqual(hashlib.sha256((folder/name).read_bytes()).hexdigest(),entry['sha256'])
        self.assertEqual(bundle['source']['sha256'],hashlib.sha256(next((self.record/'work').glob('statepoint.*.h5')).read_bytes()).hexdigest())
        self.assertFalse(list(self.record.glob('bundles/.pending-*')))
        # This class is release-critical, so a normal release also guards
        # delivery facts without requiring a separate historical-job fixture.
        summary=bundle['delivery_summary']
        self.assertEqual((folder/'delivery.md').read_text(),summary+'\n')
        self.assertEqual(self.workspace/bundle['delivery_file'],folder/'delivery.md')
        for key in ('chart','data_json','data_csv','plot_script','manifest','delivery_file'):
            self.assertIn(str(self.workspace/bundle[key]),summary)
        for report in bundle['reports']:self.assertIn(str(self.workspace/report['path']),summary)
        for warning in bundle['warnings']:self.assertIn(warning,summary)
        self.assertIn(bundle['source']['sha256'],summary)
        self.assertIn('尚未下载',summary)
        if bundle['selection']['score']=='flux':self.assertIn('particle-cm/source',summary)
        if bundle['selection']['score']=='fission':self.assertIn('reactions/source',summary)
        return folder,manifest

    def test_mesh_package_keeps_selection_image_report_and_sources_identical(self):
        originals={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (self.record/'work').iterdir() if p.is_file()}
        bundle=self.bundle(report_formats=['markdown','csv','docx','pdf','doc'])
        folder,manifest=self.validate(bundle);self.assertEqual(bundle['view'],'mesh');self.assertEqual(bundle['points'],6)
        mesh=json.loads((self.workspace/bundle['data_json']).read_text())
        self.assertEqual(mesh['dimension'],[2,3,1])
        report=(folder/'report.md').read_text()
        for name in [Path(bundle['chart']).name,Path(bundle['data_json']).name,Path(bundle['data_csv']).name,Path(bundle['plot_script']).name]:self.assertIn(name,report)
        self.assertIn(bundle['source']['sha256'],report);self.assertIn(self.job,report)
        self.assertIn('level=',report)
        preview=[l.split('|') for l in report.splitlines() if l.startswith('|tally ')]
        self.assertEqual(len(preview),6)
        for row in preview:
            i,j,k=map(int,re.search(r'mesh\[(\d+),(\d+),(\d+)\]',row[2]).groups())
            self.assertEqual(float(row[3]),float(format(mesh['values'][j][i],'.9g')))
            value=mesh['errors'][j][i]
            self.assertEqual(row[4], '不可用' if value is None else format(value,'.9g'))
        with (folder/'report.csv').open() as source:rows=list(csv.DictReader(source))
        self.assertEqual(len(rows),6)
        for row in rows:
            self.assertAlmostEqual(float(row['value']),mesh['values'][int(row['j'])][int(row['i'])],delta=1e-8)
        # Same exact PNG bytes must be embedded in DOCX, not a reselected plot.
        with zipfile.ZipFile(folder/'report.docx') as z:
            image=(self.workspace/bundle['chart']).read_bytes()
            self.assertIn(image,[z.read(n) for n in z.namelist() if n.startswith('word/media/')])
            text=z.read('word/document.xml').decode();self.assertIn('tally 1 / flux',text);self.assertIn(bundle['source']['sha256'],text)
        from pypdf import PdfReader
        text=''.join(p.extract_text() or '' for p in PdfReader(folder/'report.pdf').pages)
        self.assertIn(self.job,text.replace('\n',''))
        self.assertEqual((folder/'report.doc').read_bytes()[:8],bytes.fromhex('D0CF11E0A1B11AE1'))
        self.assertEqual(originals,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (self.record/'work').iterdir() if p.is_file()})

    def test_arbitrary_series_selection_and_bar_svg_are_not_lost(self):
        bundle=self.bundle(tally_id=2,score='fission',view='series',style='bar',image_format='svg',
                           filter_selection=[{'type':'EnergyFilter','bins':[[1000000,20000000]]}])
        folder,manifest=self.validate(bundle)
        data=json.loads((self.workspace/bundle['data_json']).read_text())
        self.assertEqual(bundle['selection']['tally_id'],2);self.assertEqual(bundle['selection']['score'],'fission')
        self.assertEqual(data['x_lower'],[1000000.]);self.assertEqual(data['x_upper'],[20000000.]);self.assertEqual(len(data['mean']),1)
        self.assertEqual(data['plot']['style'],'bar');self.assertTrue(bundle['chart'].endswith('.svg'))
        report=(folder/'report.md').read_text();self.assertIn('tally 2 / fission',report)

    def test_svg_can_be_reported_as_docx_without_changing_requested_data(self):
        bundle=self.bundle(image_format='svg',report_formats=['markdown','docx'])
        folder,manifest=self.validate(bundle);self.assertTrue(bundle['chart'].endswith('.svg'))
        self.assertEqual(len(list(folder.glob('mesh-*.png'))),1)
        data=[json.loads(p.read_text()) for p in folder.glob('mesh-*.json')]
        self.assertEqual(len(data),2);self.assertEqual(data[0]['values'],data[1]['values'])
        self.assertEqual(data[0]['source_sha256'],data[1]['source_sha256'])

    def test_auto_mesh_uses_bounded_metadata_and_bar_remains_available(self):
        path=self.record/'job.json';original=path.read_bytes();record=json.loads(original)
        try:
            record['runtime']['max_plot_points']=1;path.write_text(json.dumps(record))
            bundle=self.bundle();self.assertEqual(bundle['view'],'mesh');self.assertEqual(bundle['points'],6)
        finally:path.write_bytes(original)
        bar=self.bundle(style='bar');self.assertEqual(bar['view'],'series');self.assertEqual(bar['points'],6)

    def test_download_preserves_dependencies_and_independent_script(self):
        bundle=self.bundle();folder,manifest=self.validate(bundle)
        listed=self.call('openmc_list_artifacts',{'kind':'job','identifier':self.job})
        paths={p['path'] for p in listed['artifacts']};self.assertTrue({p['path'] for p in bundle['artifacts']} <= paths)
        transfer=self.call('openmc_prepare_download',{'kind':'job','identifier':self.job,
                            'paths':[bundle['reports'][0]['path'],bundle['manifest']]})
        blob=bytearray();offset=0
        while offset<transfer['size_bytes']:
            part=self.call('openmc_download_chunk',{'transfer_id':transfer['transfer_id'],'offset':offset})
            decoded=base64.b64decode(part['data_base64']);blob.extend(decoded);offset+=len(decoded)
        self.assertEqual(hashlib.sha256(blob).hexdigest(),transfer['sha256'])
        with tempfile.TemporaryDirectory(prefix='bundle host ') as tmp:
            host=Path(tmp)
            with zipfile.ZipFile(io.BytesIO(blob)) as z:z.extractall(host)
            script=next(host.rglob(Path(bundle['plot_script']).name))
            data=next(host.rglob(Path(bundle['data_json']).name))
            self.assertEqual(hashlib.sha256(data.read_bytes()).hexdigest(),manifest['files'][data.name]['sha256'])
            # The saved renderer imports NumPy/Matplotlib only, not OpenMC/toolkit.
            runner="import builtins,runpy;orig=builtins.__import__;exec(\"def guard(n,*a,**k):\\n if n=='openmc' or n.startswith('mc_agent_tools'):raise ImportError(n)\\n return orig(n,*a,**k)\");builtins.__import__=guard;runpy.run_path("+repr(str(script))+",run_name='__main__')"
            proc=subprocess.run(self.runtime['python_command']+['-c',runner],cwd='/tmp',capture_output=True,text=True,timeout=60)
            self.assertEqual(proc.returncode,0,proc.stderr)
            self.assertTrue(script.with_name(script.stem+'-replot.png').is_file())
        text=(self.workspace/bundle['plot_script']).read_text()
        try:
            (self.workspace/bundle['plot_script']).write_text(text+'\n# modified\n')
            error=self.call('openmc_prepare_download',{'kind':'job','identifier':self.job,'paths':[bundle['plot_script']]},success=False)
            self.assertIn('SHA256',error)
        finally:(self.workspace/bundle['plot_script']).write_text(text)

    def test_invalid_selection_or_late_render_failure_never_replaces_reports(self):
        previous=self.record/'report.md';previous.write_text('legacy report sentinel')
        before={p.name for p in (self.record/'bundles').glob('bundle-*')} if (self.record/'bundles').exists() else set()
        self.call('openmc_generate_result_bundle',{'job_id':self.job,'tally_id':2,'score':'not-a-score'},success=False)
        record=json.loads((self.record/'job.json').read_text());original=json.dumps(record)
        try:
            record['runtime']['office_command']=['/not-installed/soffice'];(self.record/'job.json').write_text(json.dumps(record))
            self.call('openmc_generate_result_bundle',{'job_id':self.job,'tally_id':1,'score':'flux',
                       'report_formats':['markdown','pdf']},success=False)
        finally:(self.record/'job.json').write_text(original)
        self.assertEqual(previous.read_text(),'legacy report sentinel')
        after={p.name for p in (self.record/'bundles').glob('bundle-*')}
        self.assertEqual(before,after);self.assertFalse(list((self.record/'bundles').glob('.pending-*')))

    def test_two_dimensional_mesh_subset_and_legacy_tool_share_native_bins(self):
        selection=[{'type':'MeshFilter','bins':[[2,3,1],[1,1,1]]}]
        whole=self.bundle();grid=json.loads((self.workspace/whole['data_json']).read_text())
        bundle=self.bundle(view='series',filter_selection=selection,style='bar',image_format='svg',
                           report_formats=['markdown','docx'])
        self.validate(bundle);data=json.loads((self.workspace/bundle['data_json']).read_text())
        self.assertEqual(data['mean'],[grid['values'][2][1],grid['values'][0][0]])
        self.assertEqual(data['mesh_indices'],[[1,2,0],[0,0,0]]);self.assertEqual(data['x'],[5,0])
        legacy=self.call('openmc_plot_results',{'job_id':self.job,'tally_id':1,'score':'flux','filter_selection':selection})
        old=json.loads((self.workspace/legacy['series_json']).read_text());self.assertEqual(data['mean'],old['mean'])

    def test_changed_input_snapshot_is_not_misreported_as_original_metadata(self):
        path=self.record/'work/settings.xml';original=path.read_bytes()
        before={p.name for p in (self.record/'bundles').glob('bundle-*')}
        try:
            path.write_bytes(original+b'\n<!-- changed after calculation -->\n')
            error=self.call('openmc_generate_result_bundle',{'job_id':self.job,'tally_id':1,'score':'flux'},success=False)
            self.assertIn('settings.xml',error)
        finally:path.write_bytes(original)
        self.assertEqual(before,{p.name for p in (self.record/'bundles').glob('bundle-*')})

    def test_new_packages_keep_old_bundle_and_legacy_tools_working(self):
        first=self.bundle();folder,manifest=self.validate(first)
        second=self.bundle();self.assertNotEqual(first['bundle_id'],second['bundle_id']);self.validate(first)
        legacy=self.call('openmc_plot_results',{'job_id':self.job,'tally_id':1,'score':'flux','style':'bar'})
        self.assertTrue((self.workspace/legacy['plot_script']).is_file())
        self.assertEqual(first['points'],second['points'])
