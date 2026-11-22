"""User-facing score/nuclide axes, raw units and unverified convergence claims.

Expected units come from bundled official OpenMC 0.16 score tables, not current
output snapshots. Existing total reaction scoring must remain a legal case.
"""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'mcp'))
from result_facts import score_semantics, interpretation, interpretation_summary, selected_value_rows, value_table
from openmc_tools import statistical_adequacy


class ResultSemantics(unittest.TestCase):
    def test_total_can_be_a_reaction_score_and_a_separate_nuclide_scope(self):
        facts = interpretation([{'id':1,'scores':['flux','fission','total']}], 'eigenvalue', {})
        self.assertEqual([(s['score'],s['raw_unit']) for s in facts['scores']],
                         [('flux','particle-cm/source'),('fission','reactions/source'),('total','reactions/source')])
        self.assertIn('nuclide=total表示所有核素汇总',facts['terminology'])
        self.assertIn('score=total',facts['terminology'])

    def test_unknown_and_modified_unit_contexts_stay_usable_without_guessed_units(self):
        self.assertIsNone(score_semantics('custom-score')['raw_unit'])
        for context in ({'response_weighted':True},{'derivative':True},{'multiply_density':False}):
            self.assertIsNone(score_semantics('fission',context)['raw_unit'])
            facts=interpretation([{'id':1,'scores':['fission'],'unit_context':context}],'eigenvalue',{})
            self.assertIsNone(facts['scores'][0]['raw_unit'])
        self.assertEqual(score_semantics('flux',{'multiply_density':False})['raw_unit'],'particle-cm/source')
        series={'score':'flux','nuclide':'total','mean':[6.7],'std_dev':[.1]}
        rows=selected_value_rows(series)
        self.assertEqual(rows[0]['raw_unit'],'particle-cm/source')
        self.assertIn('score（计分类型）',value_table(rows))
        self.assertIn('nuclide（核素/汇总范围）',value_table(rows))
        self.assertIn('particle-cm/source',value_table(rows))

    def test_batch_heuristics_never_become_a_source_convergence_or_bias_diagnosis(self):
        for progress in ({'run_mode':'eigenvalue','batches_total':8,'inactive':2},
                         {'run_mode':'eigenvalue','batches_total':80,'inactive':20},{}):
            adequacy=statistical_adequacy({'mean':1.,'std_dev':.0005},{'realizations':6},progress)
            facts=interpretation([{'id':1,'scores':['flux']}],'eigenvalue',adequacy)
            source=facts['source_convergence']
            self.assertEqual(source['status'],'not_verified')
            self.assertIn('源收敛未验证',source['statement'])
            self.assertIn('阈值不是收敛标准',source['evidence_limit'])
            summary=interpretation_summary(facts)
            self.assertNotIn('系统偏差未消除',summary)
            self.assertNotIn('源未收敛的系统偏差',summary)
            self.assertFalse(adequacy['source_convergence']['verified'])

    def test_fixed_source_does_not_get_eigenvalue_source_convergence_diagnosis(self):
        facts=interpretation([{'id':2,'scores':['fission']}],'fixed source',{})
        self.assertEqual(facts['source_convergence']['status'],'not_applicable')
        self.assertIn('不适用本征值源收敛判定',interpretation_summary(facts))


if __name__=='__main__':unittest.main()
