"""User-visible spec rejection and transactional publish, without a solver."""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'mcp'))
import business
from openmc_model_spec import MODEL_SCHEMA,validate_spec,model_assumptions,declarative_integrity
import openmc_model_tools as tools
from jsonschema import Draft202012Validator


def specimen():return json.loads((ROOT/'examples/model-specs/rectangular-assembly.json').read_text())


class ModelSpec(unittest.TestCase):
    def test_schema_accepts_custom_layout_and_rejects_unsupported_data(self):
        s=specimen();validate_spec(s);Draft202012Validator(MODEL_SCHEMA).validate(s)
        s['settings']['source']['python']='danger()'
        with self.assertRaisesRegex(ValueError,'不支持的字段'):validate_spec(s)

    def test_composition_errors_do_not_pass_as_valid_models(self):
        def duplicate(s):s['materials'].append(copy.deepcopy(s['materials'][0]))
        def missing(s):s['cells'][0]['fill']['id']=999
        def cycle(s):s['cells'][1]['fill']={'type':'universe','id':0}
        def nan(s):s['materials'][0]['density']=math.nan
        def injection(s):s['cells'][0]['region']="__import__('os')"
        def rows(s):s['lattices'][0]['universes'][1].append(1)
        def bad_plane(s):s['surfaces'][0]['parameters']={'y0':2}
        def bins_explode(s):s['tallies'][0]['filters'][0]['dimension']=[101,101,101]
        for change in (duplicate,missing,cycle,nan,injection,rows,bad_plane,bins_explode):
            with self.subTest(change=change.__name__):
                s=specimen();change(s)
                with self.assertRaises(ValueError):validate_spec(s)

    def test_capabilities_schema_is_detached_and_matches_execute_contract(self):
        c=tools.model_capabilities();c['spec_schema']['required'].append('evil')
        self.assertNotIn('evil',tools.model_capabilities()['spec_schema']['required'])
        self.assertEqual(tools.model_capabilities()['spec_schema'],tools.MODEL_TOOLS[1]['inputSchema']['properties']['spec'])

    def test_optional_defaults_are_reported_not_silent(self):
        s=specimen()
        s['materials'][0].pop('temperature',None)
        s['surfaces'][0].pop('boundary',None)
        s['settings'].pop('seed',None)
        s['settings']['source'].pop('energy_ev',None)
        s['settings']['source'].pop('strength',None)
        notes=chr(10).join(model_assumptions(s))
        for phrase in ('temperature','boundary','energy_ev','strength','seed'):
            self.assertIn(phrase,notes)

    def test_declarative_integrity_reports_drift_without_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            case=Path(tmp)
            (case/'model-spec.json').write_text('{}',encoding='utf-8')
            (case/'model.xml').write_text('<model/>',encoding='utf-8')
            marker={'generation_method':'declarative_public_python_api',
                    'model_spec_sha256':hashlib.sha256(b'{}').hexdigest(),
                    'model_xml_sha256':{'model.xml':hashlib.sha256(b'<model/>').hexdigest()}}
            (case/'mc-case.json').write_text(json.dumps(marker),encoding='utf-8')
            self.assertIsNone(declarative_integrity(case/'nope'))
            self.assertTrue(declarative_integrity(case)['consistent'])
            (case/'model.xml').write_text('<model><changed/></model>',encoding='utf-8')
            drifted=declarative_integrity(case)
            self.assertFalse(drifted['consistent'])
            self.assertEqual(drifted['changed_files'],['model.xml'])
            self.assertTrue(drifted['note'])

    def test_failed_build_and_racing_target_preserve_existing_input(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(business,'WORKSPACE',Path(tmp)):
            with patch.object(tools,'config',return_value={}),patch.object(tools,'api',side_effect=business.MCInputError('engine rejected')):
                with self.assertRaisesRegex(ValueError,'engine rejected'):tools.build_model('case',specimen())
            self.assertFalse((Path(tmp)/'case').exists())
            self.assertFalse(list(Path(tmp).glob('.building-*')))
            def race(*args,**kwargs):
                (Path(tmp)/'case').mkdir();(Path(tmp)/'case/keep').write_text('original');return {}
            with patch.object(tools,'config',return_value={}),patch.object(tools,'api',side_effect=race):
                with self.assertRaisesRegex(ValueError,'不覆盖'):tools.build_model('case',specimen())
            self.assertEqual((Path(tmp)/'case/keep').read_text(),'original')
            with self.assertRaisesRegex(ValueError,'已存在'):tools.build_model('case',specimen())
            with self.assertRaisesRegex(ValueError,'上级'):tools.build_model('../escape',specimen())
