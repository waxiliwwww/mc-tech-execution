"""Evidence lifecycle regression; never starts OpenMC, a server or a provider.

Guards interrupted-attempt immutability, capture-failure retention and failed
class setup capture. Fixtures are owned temporary files; no business data.
"""
import importlib.util,io,json,os,shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('pilot_evidence',ROOT/'scripts/verify-pilot-workflows.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class PilotEvidence(unittest.TestCase):
    def test_interrupted_attempt_logs_are_never_overwritten_by_parent_or_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);case=root/'evidence'/'official';case.mkdir(parents=True)
            (case/'unittest.log').write_text('first failure must survive')
            (case/'process.log').write_text('first process output must survive')
            cfg=root/'runtime.json';cfg.write_text(json.dumps({'runtime':'native','python_command':[sys.executable],'openmc_command':[sys.executable]}))
            env={**os.environ,'MC_TEST_OPENMC_CONFIG':str(cfg)}
            for args in [[],['--child']]:
                command=[sys.executable,str(ROOT/'scripts/verify-pilot-workflows.py'),'--workflow','official','--evidence-dir',str(case if args else case.parent),*args]
                result=subprocess.run(command,env=env,capture_output=True,text=True,timeout=10)
                self.assertNotEqual(result.returncode,0)
                self.assertEqual((case/'unittest.log').read_text(),'first failure must survive')
                self.assertEqual((case/'process.log').read_text(),'first process output must survive')
                self.assertFalse((case/'result.json').exists())

    def test_failed_copy_keeps_original_and_cannot_count_as_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager=module.EvidenceCapture(Path(tmp)/'evidence',max_bytes=2)
            try:
                with manager.installed():
                    original=tempfile.TemporaryDirectory();file=Path(original.name)/'settings.xml';file.write_text('<settings/>')
                    original.cleanup()
                self.assertEqual(file.read_text(),'<settings/>')
                self.assertTrue(manager.errors)
                self.assertEqual(manager.captures,[])
                self.assertEqual(manager.status[original.name],'retained_after_capture_error')
            finally:
                if 'original' in locals():shutil.rmtree(original.name,ignore_errors=True)

    def test_class_setup_failure_is_captured_before_its_directory_is_cleaned(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager=module.EvidenceCapture(Path(tmp)/'evidence');state={}
            class FailingSetup(unittest.TestCase):
                @classmethod
                def setUpClass(cls):
                    cls.temp=tempfile.TemporaryDirectory();state['original']=Path(cls.temp.name)
                    (state['original']/'settings.xml').write_text('<settings/>')
                    raise RuntimeError('intentional class setup failure')
                def test_placeholder(self):pass
            with manager.installed():
                result=unittest.TextTestRunner(stream=io.StringIO()).run(unittest.defaultTestLoader.loadTestsFromTestCase(FailingSetup))
            self.assertEqual(len(result.errors),1)
            self.assertFalse(state['original'].exists())
            saved=list((Path(tmp)/'evidence/snapshots').rglob('settings.xml'))
            self.assertEqual(len(saved),1);self.assertEqual(saved[0].read_text(),'<settings/>')
            self.assertEqual(module.missing_artifacts(Path(tmp)/'evidence',['settings.xml','statepoint.*.h5']),['statepoint.*.h5'])


if __name__=='__main__':unittest.main()
