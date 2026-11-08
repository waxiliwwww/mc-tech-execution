"""Trial regressions: evidence projection, diagnostic attribution, transactional writes.

Independent expected outcomes; no solver and no physical acceptance claims.
"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'mcp'))
from response_detail import project
from log_findings import match_lines
from openmc_tally_ops import publish_set
from openmc_model_tools import model_capabilities

class WorkflowBoundaries(unittest.TestCase):
    def test_summary_preserves_values_sources_and_warnings_without_mutating_evidence(self):
        value={'keff':{'mean':1.2,'source':'statepoint:k_combined'},'tallies':[{'mean':[1,2]}],
               'warnings':['temperature未核实'], 'scientific_checks':{'temperature':{'materials':[{'id':1}],'status':'partial'}}}
        original=copy.deepcopy(value);summary=project(value,'summary','results')
        self.assertEqual(value,original)
        for key in ('keff','tallies','warnings'):self.assertEqual(summary[key],value[key])
        self.assertNotIn('materials',summary['scientific_checks']['temperature'])
        self.assertEqual(project(value,'full','results')['scientific_checks'],value['scientific_checks'])
        with self.assertRaises(ValueError):project(value,'unknown','results')

    def test_capability_default_is_small_but_full_schema_is_the_execution_contract(self):
        from tools import TOOLS
        summary=model_capabilities();full=model_capabilities('full')
        self.assertNotIn('spec_schema',summary)
        self.assertEqual(full['spec_schema'],next(t for t in TOOLS if t['name']=='openmc_build_model')['inputSchema']['properties']['spec'])
        self.assertLess(len(json.dumps(summary)),len(json.dumps(full))/2)
        for name in ('openmc_update_tally','openmc_delete_tally'):
            self.assertIn(name,[t['name'] for t in TOOLS])

    def test_repair_summary_keeps_diff_and_plan_identity(self):
        value={'plan':{'plan_id':'abc','diff':'-old\n+new', 'context':{'origin_job_id':'job','case_dir':'repair','input_files':{'x':'hash'},'diagnosis':{'findings':[1]}}}}
        result=project(value,'summary','repair')
        self.assertEqual(result['plan']['diff'],value['plan']['diff'])
        self.assertEqual(result['plan']['plan_id'],'abc')
        self.assertEqual(result['plan']['context']['origin_job_id'],'job')
        self.assertNotIn('input_files',result['plan']['context'])

    def test_specific_material_tally_error_not_a_cross_sections_problem(self):
        rules=json.loads((ROOT/'config/error-rules.json').read_text())
        findings=match_lines(enumerate(['ERROR: Could not find material 99 specified on tally filter'],1),rules)
        self.assertEqual([f['category'] for f in findings],['tally_material_reference'])
        self.assertIn('openmc_update_tally',findings[0]['suggestion'])

    def test_wrapped_lattice_error_and_duplicate_lines_are_one_finding(self):
        rules=json.loads((ROOT/'config/error-rules.json').read_text())
        lines=['Particle 74 could not be located after crossing a boundary of lattice 10',
               'Particle 75 could not be located after crossing a boundary of lattice 10']
        found=match_lines(enumerate(lines,1),rules)
        self.assertEqual(len(found),1);self.assertEqual(found[0]['category'],'lost_particle')
        self.assertEqual(found[0]['line_end'],2);self.assertEqual(found[0]['occurrences'],2)
        wrapped=match_lines(enumerate(['Particle could not be located in any','cell'],1),rules)
        self.assertEqual(wrapped[0]['line_end'],2)

    def test_failed_second_publication_restores_all_original_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);stage=root/'stage';target=root/'case';stage.mkdir();target.mkdir()
            for name in ('a.xml','b.json','mc-case.json'):
                (target/name).write_text('original-'+name);(stage/name).write_text('new-'+name)
            before={p.name:p.read_bytes() for p in target.iterdir()}
            import os
            replace=os.replace;counter=[0]
            def fail_second(src,dst):
                counter[0]+=1
                if counter[0]==2:raise OSError('injected publication fault')
                return replace(src,dst)
            with patch('openmc_tally_ops.os.replace',side_effect=fail_second):
                with self.assertRaisesRegex(OSError,'injected'):publish_set(stage,target,list(before))
            self.assertEqual({p.name:p.read_bytes() for p in target.iterdir()},before)

    def test_recovery_failure_keeps_original_backup_even_if_rename_fails(self):
        from openmc_tally_ops import staging_directory, RecoveryFailure
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'case';target.mkdir();(target/'a.xml').write_text('original')
            import shutil
            copy2=shutil.copy2
            def fail_restore(src,dst,*args,**kwargs):
                if '.backup' in Path(src).parts:raise OSError('restore unavailable')
                return copy2(src,dst,*args,**kwargs)
            with self.assertRaises(RecoveryFailure):
                with staging_directory(target.parent) as stage:
                    (stage/'a.xml').write_text('new')
                    with patch('openmc_tally_ops.os.replace',side_effect=OSError('publish unavailable')), \
                         patch('openmc_tally_ops.shutil.copy2',side_effect=fail_restore), \
                         patch('openmc_tally_ops.os.rename',side_effect=OSError('rename unavailable')):
                        publish_set(stage,target,['a.xml'])
            self.assertTrue(stage.is_dir())
            self.assertEqual((stage/'.backup/a.xml').read_text(),'original')

    def test_broken_status_cache_falls_back_to_verified_original(self):
        import openmc_jobs as jobs
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'work').mkdir();point=root/'work/statepoint.6.h5';point.write_bytes(b'original')
            digest=hashlib.sha256(b'original').hexdigest()
            record={'mode':'run','output_files':{point.name:digest},'runtime':{}}
            expected={'keff':{'mean':1.2,'std_dev':.01,'source':'statepoint:k_combined'},'warnings':[]}
            for invalid in ('{broken','[]','{"key":[],"result":[]}','{}'):
                (root/'.status-keff.json').write_text(invalid)
                with patch('openmc_runtime.api',return_value=copy.deepcopy(expected)), \
                     patch('openmc_jobs.relative',return_value='work/'+point.name):
                    actual=jobs._final_keff(root,record)
                self.assertEqual(actual['keff'],expected['keff'])
                self.assertEqual(actual['statepoint_sha256'],digest)

    def test_depletion_reader_checks_sealed_output_before_using_sdk(self):
        import hashlib
        import business
        import openmc_jobs as jobs
        import openmc_tools as tools
        import os
        with tempfile.TemporaryDirectory() as tmp:
            previous=business.WORKSPACE;business.WORKSPACE=Path(tmp)
            try:
                identifier='d'*32;directory=Path(tmp)/'.mc-jobs'/identifier
                (directory/'work').mkdir(parents=True)
                point=directory/'work/depletion_results.h5';point.write_bytes(b'original result')
                digest=hashlib.sha256(point.read_bytes()).hexdigest()
                record={'mode':'deplete','runtime':{},'provenance':{},'output_files':{point.name:digest}}
                (directory/'job.json').write_text(json.dumps(record))
                with patch.object(jobs,'status',return_value={'state':'completed'}), \
                     patch.object(tools,'api',return_value={'steps':[]}) as sdk:
                    tools.read_depletion(identifier);self.assertEqual(sdk.call_count,1)
                    sdk.reset_mock();point.write_bytes(b'different result')
                    with self.assertRaisesRegex(ValueError,'计算后改变'):tools.read_depletion(identifier)
                    sdk.assert_not_called()
                    point.unlink();point.symlink_to(directory/'job.json')
                    with self.assertRaisesRegex(ValueError,'符号链接'):tools.read_depletion(identifier)
                    sdk.assert_not_called()
            finally:business.WORKSPACE=previous


if __name__=='__main__':unittest.main()
