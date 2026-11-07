"""Regression: example step case_dir always points at the user's case.

Test function
-------------
`example_workflows._step_view` is the boundary that merges a job status into
an official-example step. The job runs in `.mc-jobs/<job>/work`, but the
public step must name the user-prepared case and keep the work copy separately.

Category
--------
Regression (D4, mixed case_dir semantics)

Preconditions
-------------
No MCP service or OpenMC runtime required.

Input / operation
-----------------
A job status with case_dir inside `.mc-jobs`, and an example case_dir under
`cases/`.

Expected
--------
`case_dir` is the example directory, `job_case_dir` preserves the job work
directory, and the original status mapping is not mutated.

Not proven
----------
Does not prove a real step ran; `test_example_workflows_real` covers the
end-to-end MCP lifecycle. This locks the public naming contract only.
"""
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'mcp'))
from example_workflows import _step_view


class ExampleStepCaseDir(unittest.TestCase):
    def test_public_case_dir_is_the_user_case_not_the_job_work_copy(self):
        status = {'job_id': 'abc', 'state': 'completed',
                  'case_dir': '.mc-jobs/abc/work', 'output_files': {}}
        view = _step_view('cases/pin', status)
        self.assertEqual(view['case_dir'], 'cases/pin')
        self.assertEqual(view['job_case_dir'], '.mc-jobs/abc/work')
        self.assertEqual(status['case_dir'], '.mc-jobs/abc/work')

    def test_submit_response_uses_work_dir_when_case_dir_is_still_the_user_case(self):
        view = _step_view('cases/pin', {'case_dir': 'cases/pin',
                                        'work_dir': '.mc-jobs/abc/work', 'job_id': 'abc'})
        self.assertEqual(view['case_dir'], 'cases/pin')
        self.assertEqual(view['job_case_dir'], '.mc-jobs/abc/work')
        self.assertNotIn('work_dir', view)

    def test_same_path_does_not_add_a_redundant_job_case_dir(self):
        view = _step_view('cases/pin', {'case_dir': 'cases/pin', 'job_id': 'abc'})
        self.assertEqual(view['case_dir'], 'cases/pin')
        self.assertNotIn('job_case_dir', view)


if __name__ == '__main__':
    unittest.main()
