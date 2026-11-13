"""Skill distribution contract: current callable names, relocatable references,
and actual shipped bytes. No model/service/solver or host configuration writes.
These checks cannot prove a model follows the skill in a target host.
"""
import importlib.util
from pathlib import Path
import re, shutil, sys, tarfile, tempfile, unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'mcp'))
from tools import TOOLS

class SkillDelivery(unittest.TestCase):
    def test_named_tools_exist_in_current_registry(self):
        names={t['name'] for t in TOOLS}
        for p in (ROOT/'skills/openmc-workflows').rglob('*.md'):
            mentioned=set(re.findall(r'\bopenmc_[a-z_]+',p.read_text()))
            self.assertFalse(mentioned-names,(str(p),mentioned-names))

    def test_installed_copy_has_resolvable_supporting_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill=Path(tmp)/'openmc-workflows'
            shutil.copytree(ROOT/'skills/openmc-workflows',skill)
            self.assertTrue((skill/'SKILL.md').is_file())
            for p in skill.rglob('*.md'):
                for link in re.findall(r'\]\(([^)]+)\)',p.read_text()):
                    target=(p.parent/link).resolve()
                    self.assertTrue(target.is_relative_to(skill),link)
                    self.assertTrue(target.is_file(),link)

    def test_real_package_ships_skill_files_without_local_configuration(self):
        # Reuse the production package fixture instead of generating run data.
        spec=importlib.util.spec_from_file_location('package_fixture',ROOT/'tests/test_package_delivery.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        fixture=module.PackageDelivery();fixture.setUp()
        try:
            shutil.copytree(ROOT/'skills',fixture.root/'skills')
            private=fixture.root/'skills/openmc-workflows/config.local.json';private.write_text('not for shipping')
            result=fixture.package();self.assertEqual(result.returncode,0,result.stderr)
            with tarfile.open(fixture.archive) as bundle:
                for source in (ROOT/'skills/openmc-workflows').rglob('*'):
                    if source.is_file():
                        item='mc-tech-execution/'+str(source.relative_to(ROOT))
                        self.assertEqual(bundle.extractfile(item).read(),source.read_bytes())
                self.assertNotIn('mc-tech-execution/skills/openmc-workflows/config.local.json',bundle.getnames())
        finally: fixture.doCleanups()
