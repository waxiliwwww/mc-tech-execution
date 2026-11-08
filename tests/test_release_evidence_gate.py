"""Actual unittest record + terminal-result checks, not name mentions.

Construct logs with both inline and interleaved output; check class and method
markers, missing/skip/fail/truncation and dishonest summaries. No solver/model.
CLI parsing is also exercised by real unittest output in test_cli_reads_real_run.
"""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = spec_from_file_location('release_evidence', ROOT / 'scripts/check-release-evidence.py')
MODULE = module_from_spec(SPEC); SPEC.loader.exec_module(MODULE)
NAME = 'test_accuracy_baseline.AccuracyReal'
REQUIRED = [NAME]

def log(rows, summary='OK', count=None):
    return '\n'.join(rows) + f'\nRan {len(rows) if count is None else count} tests in 0.1s\n\n{summary}\n'

def record(method='test_real', status='ok', owner=NAME):
    return f'{method} ({owner}.{method}) ... {status}'

class ReleaseEvidenceGate(unittest.TestCase):
    def test_all_required_tests_ran_and_passed(self):
        text = log([record()])
        for marker in (NAME, NAME+'.test_real', 'test_real'):
            self.assertEqual(MODULE.evaluate(text, [marker]), [])

    def test_same_class_every_record_must_pass_not_only_one(self):
        for status in ("skipped 'no runtime'", 'FAIL', 'ERROR', 'expected failure', 'unexpected success'):
            with self.subTest(status=status):
                self.assertTrue(MODULE.evaluate(log([record(), record('test_other',status)]), REQUIRED))

    def test_skipped_critical_test_is_a_failure(self):
        errors = MODULE.evaluate(log([record(status="skipped 'no runtime'")], 'OK (skipped=1)'), REQUIRED)
        self.assertTrue(any('跳过' in x for x in errors))

    def test_missing_critical_test_is_a_failure(self):
        self.assertTrue(any('未执行' in x for x in MODULE.evaluate(log([record(owner='other.Other')]),REQUIRED)))

    def test_failed_log_is_a_failure(self):
        self.assertTrue(MODULE.evaluate(log([record(status='FAIL')], 'FAILED (failures=1)'), REQUIRED))

    def test_mentions_prefix_collisions_and_success_summary_cannot_fake_execution(self):
        for text in (log([NAME, record(owner='other.Other')], count=1),
                     log([record(owner=NAME+'Extra')]),
                     log([record(method='test_real_extra')])):
            self.assertTrue(MODULE.evaluate(text,[NAME+'.test_real']))

    def test_interleaved_warning_with_standalone_status_is_an_execution(self):
        text=log([record(status='Executing <Task warning>'), 'sdk Warning: example', 'ok'],count=1)
        self.assertEqual(MODULE.evaluate(text,REQUIRED),[])
        self.assertTrue(MODULE.evaluate(text.replace('\nok\n','\n'),REQUIRED))

    def test_docstring_description_on_next_line_is_an_execution(self):
        text=log([f'test_real ({NAME}.test_real)', 'User-visible result assertion. ... ok'],count=1)
        self.assertEqual(MODULE.evaluate(text,REQUIRED),[])
        self.assertTrue(MODULE.evaluate(text.replace(' ... ok',''),REQUIRED))

    def test_summary_and_record_count_must_agree(self):
        for text in (log([record()],count=2),record()+'\nOK\n',
                     log([record()])+log([record()]),log([record(status='ERROR')])):
            self.assertTrue(MODULE.evaluate(text,REQUIRED))

    def test_noncritical_skip_does_not_pretend_required_test_skipped(self):
        text=log([record(),record('test_other',"skipped 'optional'",'other.Optional')],'OK (skipped=1)')
        self.assertEqual(MODULE.evaluate(text,REQUIRED),[])

    def test_cli_reads_real_run_and_rejects_names_mentioned_in_prose(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'test_sample.py'
            path.write_text('import unittest\nclass Sample(unittest.TestCase):\n def test_ok(self): pass\n')
            run=subprocess.run([sys.executable,'-m','unittest','discover','-s',tmp,'-v'],capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stderr)
            self.assertEqual(MODULE.evaluate(run.stderr,['test_sample.Sample.test_ok']),[])
            forged='\n'.join(MODULE.markers())+'\n'+run.stderr
            proof=Path(tmp)/'proof.log';proof.write_text(forged)
            gate=subprocess.run([sys.executable,str(ROOT/'scripts/check-release-evidence.py'),str(proof)],capture_output=True,text=True)
            self.assertEqual(gate.returncode,1,gate.stdout+gate.stderr)
            self.assertIn('关键测试未执行',gate.stderr)

if __name__ == '__main__': unittest.main()
