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

    def test_normalization_distinguishes_per_source_from_engineering_conversion(self):
        facts=interpretation([{'id':1,'scores':['flux']}],'eigenvalue',{})
        self.assertIn('每源粒子归一化',facts['normalization'])
        self.assertIn('未作工程源强/功率换算',facts['normalization'])
        self.assertNotIn('未归一化',facts['normalization'])

    def test_zero_estimates_do_not_establish_events_tracks_or_low_statistics_cause(self):
        facts=interpretation([{'id':1,'scores':['fission'],'estimator':'tracklength',
                              'zero_values':2,'zero_mean_std_values':2}],'eigenvalue',{})
        evidence=facts['zero_value_evidence']
        self.assertEqual(evidence['cause_status'],'not_determined')
        statement=evidence['statement']
        self.assertIn('成因也未确定',statement)
        self.assertIn('不是事件计数',statement)
        self.assertIn('不能仅据零值断言',statement)
        self.assertIn('没有对应证据时不指定成因',interpretation_summary(facts))
        self.assertIn('不增补可能成因清单或零估计概率判断',statement)

    def test_reaction_scores_remain_estimates_not_observed_event_counts(self):
        for score in ('fission','total'):
            item=score_semantics(score)
            self.assertEqual(item['raw_unit'],'reactions/source')
            self.assertIn('估计值',item['estimator_note'])
            self.assertIn('不等于本次实际发生事件的计数',item['estimator_note'])
            text=interpretation_summary(interpretation([{'id':1,'scores':[score]}],'eigenvalue',{}))
            self.assertIn(item['estimator_note'],text)

    def test_mesh_contract_separates_one_based_bins_and_zero_based_mesh_offsets(self):
        facts=interpretation([{'id':7,'scores':['flux'],
            'mesh':{'type':'RegularMesh','dimension':[2,3,4]}}],'eigenvalue',{})
        mesh=facts['mesh_indexing'][0]
        self.assertEqual((mesh['bin_base'],mesh['mesh_offset_base']),(1,0))
        self.assertEqual(mesh['first'],{'bin':[1,1,1],'mesh_offset':0})
        self.assertEqual(mesh['last'],{'bin':[2,3,4],'mesh_offset':23})
        self.assertEqual(mesh['formula'],'(i-1) + nx*(j-1) + nx*ny*(k-1)')
        self.assertIn('不是含其他filter',mesh['scope'])
        self.assertIn(mesh['formula'],interpretation_summary(facts))
        self.assertEqual(interpretation([{'id':8,'scores':['flux']}],'eigenvalue',{})['mesh_indexing'],[])

    def test_missing_professional_acceptance_is_not_a_failed_evaluation(self):
        facts=interpretation([], 'eigenvalue', {})
        state=facts['professional_acceptance_status']
        self.assertEqual(state['status'],'not_performed_by_toolkit')
        self.assertIsNone(state['result'])
        self.assertIn('本工具未执行专业验收',state['statement'])
        self.assertIn('不表示验收失败',interpretation_summary(facts))

    def test_batch_summary_labels_total_inactive_and_active_without_negative_parentheses(self):
        from result_facts import batch_settings_summary
        for total,inactive in ((8,2),(80,20),(8,0)):
            progress={'run_mode':'eigenvalue','batches_total':total,'inactive':inactive}
            from openmc_tools import _source_convergence
            source=_source_convergence(progress)
            batch=batch_settings_summary(progress)
            self.assertEqual((batch['batches_total'],batch['inactive'],batch['active_batches']),(total,inactive,total-inactive))
            self.assertEqual(source['batch_settings'],batch)
            self.assertIn(f'非活跃批次(inactive)={inactive}',batch['statement'])
            self.assertIn(f'活跃批次(active)={total-inactive}',batch['statement'])
            self.assertNotIn('非inactive',batch['statement'])
            facts=interpretation([], 'eigenvalue', {'source_convergence':source})
            self.assertIn(batch['statement'],interpretation_summary(facts))
            self.assertIn('不是源收敛标准',batch['statement'])

    def test_unknown_and_non_eigenvalue_batches_are_not_invented(self):
        from result_facts import batch_settings_summary
        for progress in (None,{},[],{'run_mode':'eigenvalue','batches_total':8,'inactive':8},
                         {'run_mode':'eigenvalue','batches_total':True,'inactive':0}):
            batch=batch_settings_summary(progress)
            self.assertEqual(batch['status'],'unavailable')
            self.assertNotIn('active_batches',batch)
        batch=batch_settings_summary({'run_mode':'fixed source','batches_total':8,'inactive':0})
        self.assertEqual(batch['status'],'not_applicable')
        self.assertNotIn('active_batches',batch)

    def test_fixed_source_does_not_get_eigenvalue_source_convergence_diagnosis(self):
        facts=interpretation([{'id':2,'scores':['fission']}],'fixed source',{})
        self.assertEqual(facts['source_convergence']['status'],'not_applicable')
        self.assertIn('不适用本征值源收敛判定',interpretation_summary(facts))


if __name__=='__main__':unittest.main()
