"""Workflow guidance contracts and an owned real MCP session; no solver/model.
Checks source identity, bounded file access and the user's single-connection path.
"""
import hashlib,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'mcp'))
import workflow_guidance as guide
from unittest.mock import patch
from adapters import ToolExecutionError

class WorkflowGuidance(unittest.TestCase):
    def test_all_chapters_are_the_authored_skill_bytes(self):
        for chapter,relative in guide.FILES.items():
            value=guide.get_workflow(chapter);raw=(ROOT/'skills/openmc-workflows'/relative).read_bytes()
            self.assertEqual(value['content'].encode(),raw)
            self.assertEqual(value['sha256'],hashlib.sha256(raw).hexdigest())
            self.assertEqual(guide.read_resource(value['uri']),value['content'])
        self.assertEqual(guide.get_workflow(),guide.get_workflow('start'))

    def test_bootstrap_is_sourced_from_skill_and_missing_section_is_not_silent(self):
        with tempfile.TemporaryDirectory() as d,patch.object(guide,'skill_root',return_value=Path(d)):
            skill=Path(d)/'SKILL.md'
            skill.write_text('# Skill\n## 连接时速查\nunique instruction one\n## Other\nnot bootstrap')
            self.assertIn('unique instruction one',guide.server_instructions())
            self.assertNotIn('not bootstrap',guide.server_instructions())
            skill.write_text(skill.read_text().replace('unique instruction one','updated instruction two'))
            self.assertIn('updated instruction two',guide.server_instructions())
            self.assertNotIn('unique instruction one',guide.server_instructions())
            skill.write_text('# Skill without required core')
            with self.assertRaises(ToolExecutionError):guide.server_instructions()

    def test_unknown_files_and_missing_installation_fail_without_fallback(self):
        for chapter in ['../SKILL.md','/etc/passwd','unknown','start/../files']:
            with self.assertRaises(ToolExecutionError):guide.get_workflow(chapter)
        with self.assertRaises(ValueError):guide.read_resource(guide.PREFIX+'../secret')
        with tempfile.TemporaryDirectory() as d,patch.object(guide,'skill_root',return_value=Path(d)):
            with self.assertRaises(ToolExecutionError):guide.get_workflow()

    def test_wheel_distribution_resolves_its_installed_skill(self):
        with tempfile.TemporaryDirectory() as d:
            site=Path(d)/'site-packages'
            relative=Path('share/mc-agent-tools/skills/openmc-workflows')
            for layout in ('target','prefix'):
                root=(site if layout=='target' else Path(d)/'prefix')/relative
                root.mkdir(parents=True);(root/'SKILL.md').write_text(layout)
                with patch.object(guide,'__file__',str(site/'mc_agent_tools/workflow_guidance.py')),patch.object(guide.sysconfig,'get_path',return_value=str(Path(d)/'prefix')):
                    self.assertEqual(guide.get_workflow()['content'],layout)
                (root/'SKILL.md').unlink()

    @unittest.skipUnless(os.environ.get('MC_TEST_WORKFLOW_WHEEL'),'MC_TEST_WORKFLOW_WHEEL指定已构建wheel，验证真实隔离安装')
    def test_actual_wheel_outside_source_tree(self):
        with tempfile.TemporaryDirectory(prefix='workflow installed ') as d:
            site=Path(d)/'site'
            installed=subprocess.run([sys.executable,'-m','pip','install','--no-deps','--target',str(site),os.environ['MC_TEST_WORKFLOW_WHEEL']],capture_output=True,text=True,timeout=60)
            self.assertEqual(installed.returncode,0,installed.stderr)
            code="from mc_agent_tools.launcher import _prepare;_prepare();from workflow_guidance import get_workflow;import json;print(json.dumps({k:get_workflow(k)['sha256'] for k in ['start','files','advanced']}))"
            read=subprocess.run([sys.executable,'-c',code],cwd=d,env={**os.environ,'PYTHONPATH':str(site)},capture_output=True,text=True,timeout=20)
            self.assertEqual(read.returncode,0,read.stderr)
            self.assertEqual(json.loads(read.stdout),{k:guide.get_workflow(k)['sha256'] for k in guide.FILES})

    def test_one_connection_handshake_guidance_tool_and_resources_then_cleanup(self):
        with tempfile.TemporaryDirectory(prefix='mc workflow protocol ') as d:
            out=Path(d)/'session'
            result=subprocess.run([sys.executable,str(ROOT/'scripts/mcp_test_session.py'),'--output',str(out),'--timeout','90','--',sys.executable,str(ROOT/'tests/fixtures/workflow_mcp_client.py')],capture_output=True,text=True,timeout=120)
            if result.returncode:self.fail(result.stdout+result.stderr+'\n'+ '\n'.join(p.read_text(errors='replace')[-9000:] for p in out.glob('*.log')))
            data=json.loads((out/'workflow.json').read_text());self.assertTrue(data['tool_resource_bytes_identical'])
            receipt=json.loads((out/'receipt.json').read_text());self.assertTrue(receipt['cleanup']['verified_empty']);self.assertTrue(receipt['port_released'])
            if os.environ.get('MC_WORKFLOW_EVIDENCE'):
                import shutil
                shutil.copytree(out,os.environ['MC_WORKFLOW_EVIDENCE'],dirs_exist_ok=True)
