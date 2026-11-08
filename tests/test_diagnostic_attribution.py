"""Normal vs fault log classification through both diagnostic consumers.

Real rule matcher and files; no solver. Covers normal startup, wrapped errors,
per-span specificity, repeats and independent faults. Does not prove domain
root cause or CAD conversion.
"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'mcp'))
import business
from log_findings import match_lines

class DiagnosticAttribution(unittest.TestCase):
    def setUp(self):self.rules=json.loads((ROOT/'config/error-rules.json').read_text())
    def categories(self,lines):return [x['category'] for x in match_lines(enumerate(lines,1),self.rules)]
    def test_startup_and_loading_are_not_fault_evidence(self):
        for line in ['Loading file dagmc.h5m','Initializing DAGMC geometry...',
                     'DAGMC initialization completed with no errors','MOAB: 0 errors',
                     'MOAB initialization without errors','MOAB version 5.5.1','DAGMC initialization completed successfully',
                     'Reading cross sections XML file...','Loading material U235']:
            with self.subTest(line=line):self.assertEqual(self.categories([line]),[])
        self.assertEqual(self.categories(['Loading file dagmc.h5m','Initializing DAGMC geometry...']),[])
    def test_explicit_dagmc_failures_in_either_word_order_and_wrapped_are_retained(self):
        for lines in [['Failed to load dagmc.h5m'],['MOAB error: invalid mesh'],
                      ['ERROR: could not load','dagmc.h5m']]:
            with self.subTest(lines=lines):self.assertEqual(self.categories(lines),['dagmc_geometry'])
    def test_unrelated_adjacent_failure_does_not_blame_normal_loading(self):
        self.assertEqual(self.categories(['Loading dagmc.h5m','ERROR: Unknown score']),['invalid_score'])
        self.assertEqual(self.categories(['DAGMC reports 0 errors; failed to load mesh']),['dagmc_geometry'])

    def test_specificity_is_per_error_not_per_entire_log(self):
        lines=['ERROR: Could not find nuclide U236x in the nuclear data library',
               'ordinary separating line', 'ERROR: cross sections XML file does not exist']
        found=match_lines(enumerate(lines,1),self.rules)
        self.assertEqual([x['category'] for x in found],['missing_nuclide','missing_input_data'])
        self.assertEqual([x['line'] for x in found],[1,3])
        wrapped=match_lines(enumerate(['ERROR: Could not find nuclide','U236x in the nuclear data library'],1),self.rules)
        self.assertEqual([x['category'] for x in wrapped],['missing_nuclide'])
    def test_repeated_faults_are_grouped_and_standalone_consumer_uses_same_rules(self):
        line='ERROR: Could not find nuclide U236x in the nuclear data library'
        with tempfile.TemporaryDirectory() as tmp,patch.object(business,'WORKSPACE',Path(tmp)):
            (Path(tmp)/'log.txt').write_text('Loading file dagmc.h5m\n'+line+'\n'+line+'\n')
            found=business.diagnose_log('log.txt')['findings']
            self.assertEqual([x['category'] for x in found],['missing_nuclide'])
            self.assertEqual(found[0]['line'],2);self.assertEqual(found[0]['line_end'],3)
            self.assertEqual(found[0]['occurrences'],2)
    def test_specific_cross_sections_and_material_errors_suppress_only_generic_overlap(self):
        for line,wanted in [('ERROR: Could not find material 99 specified on tally filter','tally_material_reference'),
                            ('ERROR: Unable to load cross sections; missing material','missing_cross_sections')]:
            with self.subTest(line=line):self.assertEqual(self.categories([line]),[wanted])
if __name__=='__main__':unittest.main()
