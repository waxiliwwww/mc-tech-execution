"""Consume all definite semantic errors across submission boundaries.

Real XML + actual semantic validator + real job admission; only solver spawning
is replaced (outside the admission promise). Check six error families in split
and single XML, warnings/incomplete checks remain allowed, truncated details
cannot hide errors. Real MCP flow lives in test_submission_semantic_real.py.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'mcp'))
import openmc_tools as tools
import openmc_jobs as jobs
import business

class SemanticSubmission(unittest.TestCase):
    def test_truncated_details_cannot_hide_error_and_warning_is_not_a_rejection(self):
        for value in ({'checked':True,'error_count':1,'problems':[{'severity':'warning'}]},
                      {'problems':[{'severity':'error','kind':'new_future_error','detail':'bad input'}]}):
            with self.assertRaisesRegex(ValueError,'输入语义检查失败'):tools.require_semantic_submission(value)
        for value in ({'checked':False}, {'checked':True,'complete':False,'error_count':0,
                        'problems':[{'severity':'warning','kind':'scientific_advisory','detail':'unknown'}]}):
            tools.require_semantic_submission(value)

    def test_snapshot_guard_handles_all_error_kinds_and_both_input_representations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);work=root/'workspace';work.mkdir()
            cfg=json.loads((ROOT/'config/openmc.json').read_text());cfg.update(min_free_disk_bytes=0,cpu_count=8)
            index=root/'cross_sections.xml';index.write_text('<cross_sections><library materials="U235 c_H_in_H2O" path="unused.h5" type="neutron"/></cross_sections>')
            cfg['cross_sections']=str(index);config=root/'config.json';config.write_text(json.dumps(cfg))
            materials='<materials><material id="1"><density units="g/cm3" value="1"/><nuclide name="U235" ao="1"/></material></materials>'
            settings='<settings><run_mode>eigenvalue</run_mode><particles>100</particles><batches>6</batches><inactive>2</inactive></settings>'
            tallies='<tallies><filter id="1" type="material"><bins>1</bins></filter><tally id="1"><filters>1</filters><scores>flux</scores></tally></tallies>'
            variants={
                'missing_nuclide':(materials.replace('U235','Zz999'),tallies),
                'missing_sab':(materials.replace('</material>','<sab name="not_in_library"/></material>'),tallies),
                'density_value':(materials.replace('value="1"','value="-1"'),tallies),
                'invalid_score':(materials,tallies.replace('flux','not-a-score')),
                'filter_reference':(materials,tallies.replace('<filters>1','<filters>999')),
                'tally_material_reference':(materials,tallies.replace('<bins>1','<bins>999'))}
            with patch.dict(os.environ,{'MC_WORKSPACE':str(work),'MC_OPENMC_CONFIG':str(config)}),patch.object(business,'WORKSPACE',work),patch.object(jobs,'spawn_worker') as spawn:
                for single in (False,True):
                    for kind,(mat,tally) in variants.items():
                        with self.subTest(single=single,kind=kind):
                            case=work/(kind+str(single));case.mkdir()
                            if single:(case/'model.xml').write_text('<model>'+mat+'<geometry/>'+settings+tally+'</model>')
                            else:
                                for name,text in [('materials',mat),('geometry','<geometry/>'),('settings',settings),('tallies',tally)]:
                                    (case/(name+'.xml')).write_text(text)
                            before={p.name:p.read_bytes() for p in case.iterdir()}
                            checked=tools.validate_inputs(case.name,semantic_check=True)
                            self.assertIn(kind,[v['kind'] for v in checked['semantic']['problems']])
                            with self.assertRaisesRegex(ValueError,kind):jobs.submit(case.name)
                            self.assertEqual({p.name:p.read_bytes() for p in case.iterdir()},before)
                            self.assertEqual(list((work/'.mc-jobs').glob('*/job.json')),[])
                spawn.assert_not_called()
if __name__=='__main__':unittest.main()
