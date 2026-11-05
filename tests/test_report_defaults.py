"""Report actual inputs and documented defaults; never fill malformed/missing XML."""
from pathlib import Path
import json,sys,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'mcp'))
from openmc_tools import _report_metadata
from openmc_visuals import metadata_parameters, template_label
class ReportDefaults(unittest.TestCase):
    def metadata(self,xml,version='0.16.0'):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'work').mkdir()
            (p/'job.json').write_text(json.dumps({'runtime':{'expected_version':version}}))
            if xml is not None:(p/'work/settings.xml').write_text(xml)
            return _report_metadata(p)
    def test_documented_default_is_distinct_from_explicit_input(self):
        m=self.metadata('<settings><particles>1000</particles></settings>')
        self.assertEqual(m['parameters']['energy_mode'],'continuous-energy')
        self.assertEqual(m['parameter_sources']['energy_mode'],'openmc_default')
        text=metadata_parameters(m);self.assertIn('OpenMC默认',text)
        self.assertNotIn('seed=未记录',text)
        m=self.metadata('<settings><energy_mode>multi-group</energy_mode></settings>')
        self.assertEqual(m['parameter_sources']['energy_mode'],'input_xml')
        self.assertNotIn('默认',metadata_parameters(m))
    def test_unknown_version_empty_element_missing_or_bad_file_is_not_defaulted(self):
        for xml,version in [(None,'0.16.0'),('<settings>','0.16.0'),('<settings><energy_mode/></settings>','0.16.0'),('<settings/>','0.17.0')]:
            m=self.metadata(xml,version)
            self.assertNotEqual(m['parameters'].get('energy_mode'),'continuous-energy')
    def test_template_is_not_fabricated_as_generator_and_explicit_method_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'work').mkdir()
            (p/'job.json').write_text(json.dumps({'runtime':{'expected_version':'0.16.0'},'provenance':{'template':'assembly-17x17'}}))
            marker=p/'work/mc-case.json';marker.write_text('{"template":"assembly-17x17"}')
            metadata=_report_metadata(p)
            self.assertIsNone(metadata['generator']);self.assertEqual(template_label(metadata),'assembly-17x17')
            marker.write_text('{"template":"assembly-17x17","generation_method":"public_api_template"}')
            metadata=_report_metadata(p)
            self.assertEqual(metadata['generator'],'public_api_template')
            self.assertEqual(template_label(metadata),'assembly-17x17（生成方式：public_api_template）')
        self.assertEqual(template_label({'template':'assembly-17x17','generator':'assembly-17x17'}),'assembly-17x17')
        self.assertEqual(template_label({}),'未记录')
if __name__=='__main__':unittest.main()
