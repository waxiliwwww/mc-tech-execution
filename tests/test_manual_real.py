"""Execute the literal HTML lesson in isolated data with a real OpenMC runtime."""
from html.parser import HTMLParser
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
RUNTIME=os.environ.get('MC_TEST_OPENMC_CONFIG')


class Commands(HTMLParser):
    def __init__(self, source):
        super().__init__();self.blocks={};self.key=None;self.feed(source)
    def handle_starttag(self, tag, attrs):
        if tag=='pre':self.key=dict(attrs).get('data-run')
        if self.key:self.blocks.setdefault(self.key,'')
    def handle_endtag(self, tag):
        if tag=='pre':self.key=None
    def handle_data(self, value):
        if self.key:self.blocks[self.key]+=value


@unittest.skipUnless(RUNTIME,'设置MC_TEST_OPENMC_CONFIG运行HTML中的真实计算教学')
class ManualReal(unittest.TestCase):
    def test_literal_lesson_reaches_actual_files_and_preserves_original(self):
        blocks=Commands((ROOT/'docs/compute.html').read_text()).blocks
        evidence=ROOT/'artifacts/runs/20261028-html-guide';evidence.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='mc html lesson ') as name:
            folder=Path(name);workspace=folder/'data';variables=folder/'variables';before=folder/'original.sha256'
            env={k:v for k,v in os.environ.items() if not k.startswith(('MC_','MCP_','HARNESS_'))}
            env.update(MC_WORKSPACE=str(workspace),MC_OPENMC_CONFIG=str(Path(RUNTIME).resolve()))
            script='set -euo pipefail\n'+blocks['environment']+'\n'+blocks['prepare']+'\n'
            script+='sha256sum "$mc_workspace/cases/tutorial/settings.xml" > "$MC_LESSON_BEFORE"\n'
            for key in ['discover','change']:script+=blocks[key]+'\n'
            script+='\n{\n'+blocks['validate']+'\n} > "$MC_LESSON_VALIDATION"\n'
            script+='''python3 - <<'CHECK'
import json, os
value=json.load(open(os.environ['MC_LESSON_VALIDATION']))
assert value['structure_valid'] and not value['problems'] and value['openmc_api']['api_parsed']
CHECK
'''
            script+=blocks['submit']+'\n'
            # The page tells the reader to repeat this same status command until terminal.
            script+='''for mc_lesson_attempt in {1..300}; do
'''+blocks['status']+''' > "$MC_LESSON_STATUS"
mc_lesson_state=$(python3 -c 'import json,os;print(json.load(open(os.environ["MC_LESSON_STATUS"]))["state"])')
case "$mc_lesson_state" in
completed) break;;
failed|cancelled|interrupted) cat "$MC_LESSON_STATUS"; exit 1;;
esac
sleep .2
done
[[ "$mc_lesson_state" == completed ]]
'''
            for key in ['read','artifacts','locate']:script+=blocks[key]+'\n'
            script+='printf "%s\\n%s\\n" "$mc_workspace" "$mc_job_id" > "$MC_LESSON_VARIABLES"\n'
            env.update(MC_LESSON_BEFORE=str(before),MC_LESSON_STATUS=str(folder/'status.json'),
                       MC_LESSON_VALIDATION=str(folder/'validation.json'),MC_LESSON_VARIABLES=str(variables))
            (evidence/'literal-lesson.sh').write_text(script)
            result=subprocess.run(['bash','-c',script],cwd=ROOT,env=env,capture_output=True,text=True,timeout=180)
            (evidence/'literal-lesson.stdout').write_text(result.stdout)
            (evidence/'literal-lesson.stderr').write_text(result.stderr)
            self.assertEqual(result.returncode,0,result.stderr[-3000:])
            actual,job_id=variables.read_text().splitlines();self.assertEqual(actual,str(workspace))
            original=workspace/'cases/tutorial/settings.xml';modified=workspace/'cases/tutorial-400/settings.xml'
            self.assertEqual(ET.parse(original).findtext('particles'),'200')
            self.assertEqual(ET.parse(modified).findtext('particles'),'400')
            self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(),before.read_text().split()[0])
            record=workspace/'.mc-jobs'/job_id
            self.assertTrue(list((record/'work').glob('statepoint*.h5')))
            self.assertTrue(list(record.glob('plot-*.png')))
            self.assertTrue((record/'report.md').is_file())
            self.assertTrue((record/'results.json').is_file())
            for file in [*record.glob('plot-*.png'),*record.glob('plot-*.json'),record/'report.md']:
                shutil.copy2(file,evidence/('lesson-'+file.name))
            (evidence/'lesson-receipt.json').write_text(json.dumps({'passed':True,'blocks':list(blocks),'job_id':job_id,
                'original_particles':200,'modified_particles':400,'original_unchanged':True,
                'runtime_config':str(Path(RUNTIME).resolve()),'server_directory_during_run':str(record),
                'temporary_data_cleaned_after_test':True,'professional_acceptance':False},ensure_ascii=False,indent=2))


if __name__=='__main__':unittest.main()
