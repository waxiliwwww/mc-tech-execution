"""User-visible v2 spec rejection and transactional publish, without a solver."""
import copy
import hashlib
import json
import math
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'mcp'))
import business
from openmc_model_spec import (MODEL_SCHEMA,declarative_integrity,model_assumptions,
                               region_surface_ids,region_to_string,validate_spec,validation_report)
from model_validation import available_backend
from openmc_runtime import provenance
import openmc_model_tools as tools
from jsonschema import Draft202012Validator


def specimen():return json.loads((ROOT/'examples/model-specs/rectangular-assembly.json').read_text())


def root_surface_ids(spec):
    root=spec['root_universe']
    return {surface for cell in spec['cells'] if cell['universe']==root
            for surface in region_surface_ids(cell['region'])}


class ModelSpec(unittest.TestCase):
    def test_schema_accepts_custom_layout_and_rejects_unsupported_data(self):
        s=specimen();validate_spec(s);Draft202012Validator(MODEL_SCHEMA).validate(s)
        build_tool=next(tool for tool in tools.MODEL_TOOLS if tool['name']=='openmc_build_model')
        Draft202012Validator(build_tool['inputSchema']).validate({'case_dir':'case','spec':s})
        s['settings']['source']['python']='danger()'
        with self.assertRaisesRegex(ValueError,'不支持的字段'):validate_spec(s)

    def test_v1_spec_and_string_region_are_not_accepted(self):
        s=specimen();s['schema_version']='1'
        with self.assertRaises(ValueError):validate_spec(s)
        s=specimen();s['cells'][0]['region']='1 -2'
        with self.assertRaises(ValueError):validate_spec(s)

    def test_composition_errors_do_not_pass_as_valid_models(self):
        def duplicate(s):s['materials'].append(copy.deepcopy(s['materials'][0]))
        def missing(s):s['cells'][0]['fill']['id']=999
        def cycle(s):s['cells'][1]['fill']={'type':'universe','id':0}
        def nan(s):s['materials'][0]['density']=math.nan
        def injection(s):s['cells'][0]['region']={'op':'halfspace','surface':1,'side':'negative','python':'danger()'}
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
        capabilities=tools.model_capabilities()
        self.assertEqual(capabilities['builder_validation_backend_policy'],
                         'pydantic_if_available_else_fallback')
        self.assertTrue(capabilities['validation_backend_scope'])

    def test_outer_boundary_must_close_the_root_universe(self):
        s=specimen()
        for surface in s['surfaces']:
            if surface['id'] in root_surface_ids(s):
                surface['boundary']='transmission'
        with self.assertRaisesRegex(ValueError,'根宇宙外边界面'):
            validate_spec(s)
        s=specimen()
        for surface in s['surfaces']:
            if surface['id'] in root_surface_ids(s):
                surface.pop('boundary',None)
        with self.assertRaisesRegex(ValueError,'根宇宙外边界面'):
            validate_spec(s)
        s=specimen()
        for surface in s['surfaces']:
            if surface['id'] not in root_surface_ids(s):
                surface['boundary']='transmission'
        validate_spec(s)

    def test_region_tree_serializes_to_openmc_expression(self):
        region={'op':'union','items':[
            {'op':'intersection','items':[
                {'op':'halfspace','surface':1,'side':'negative'},
                {'op':'halfspace','surface':2,'side':'positive'}]},
            {'op':'complement','item':{'op':'halfspace','surface':3,'side':'positive'}}]}
        self.assertEqual(region_to_string(region),'-1 2 | ~3')
        self.assertEqual(region_surface_ids(region),[1,2,3])

    def test_region_shape_rejects_malformed_trees(self):
        regions=[
            {'op':'intersection','items':[]},
            {'op':'halfspace','surface':0,'side':'negative'},
            {'op':'halfspace','surface':1,'side':'inside'},
            {'op':'halfspace','surface':1},
            {'op':'union','items':[{'op':'bad'}]},
            {'op':'complement'},
            {'op':'halfspace','surface':1,'side':'negative','python':'danger()'},
        ]
        for region in regions:
            with self.subTest(region=region):
                s=specimen();s['cells'][0]['region']=region
                with self.assertRaises(ValueError):validate_spec(s)

    def test_expert_override_is_tagged_not_silent(self):
        s=specimen()
        for surface in s['surfaces']:
            if surface['id'] in root_surface_ids(s):
                surface['boundary']='transmission'
        s['validation']={'overrides':[{'code':'outer_boundary_transmission',
                                      'reason':'fixed-source escape test','scope':'run'}]}
        validate_spec(s)
        report=validation_report(s)
        self.assertEqual([item['code'] for item in report['overrides']],['outer_boundary_transmission'])
        self.assertEqual(report['backend'],available_backend())

    def test_untagged_override_is_reported_but_does_not_apply(self):
        s=specimen();s['validation']={'overrides':[{'code':'outer_boundary_transmission',
                                                     'reason':'not applicable here','scope':'run'}]}
        report=validation_report(s)
        self.assertEqual(report['overrides'],[])
        self.assertTrue(any('未触发' in item for item in report['warnings']))

    def test_standalone_validate_cli_needs_no_openmc(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp,'model-spec.json').write_text(
                (ROOT/'examples/model-specs/rectangular-assembly.json').read_text(),encoding='utf-8')
            result=subprocess.run([sys.executable,str(ROOT/'mcp/openmc_model_spec.py'),'--validate',
                                   '--backend','fallback'],cwd=tmp,text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            payload=json.loads(result.stdout)
            self.assertTrue(payload['valid'])
            self.assertEqual(payload['backend'],'fallback')
            self.assertIn('assumptions',payload)

    def test_optional_defaults_are_reported_not_silent(self):
        s=specimen()
        s['materials'][0].pop('temperature',None)
        inner=next(x for x in s['surfaces'] if x['id'] not in root_surface_ids(s))
        inner.pop('boundary',None)
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

    def test_validation_tags_travel_with_job_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            case=Path(tmp)
            marker={'data_kind':'user_supplied_unverified','schema_version':'2',
                    'validation':{'backend':'pydantic','overrides':[
                        {'code':'outer_boundary_transmission','reason':'test','scope':'run'}]}}
            (case/'mc-case.json').write_text(json.dumps(marker),encoding='utf-8')
            tags=provenance(case,{'data_kind':'user_supplied_unverified'})
            self.assertEqual(tags['model_spec_schema_version'],'2')
            self.assertEqual(tags['validation']['overrides'][0]['code'],'outer_boundary_transmission')

    def test_build_reports_server_and_builder_backends_separately(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(business,'WORKSPACE',Path(tmp)):
            with patch.object(tools,'config',return_value={}),patch.object(
                    tools,'api',return_value={'validation':{'backend':'fallback'},'api_parsed':True}):
                result=tools.build_model('case',specimen())
            self.assertEqual(result['validation_backend'],available_backend())
            self.assertEqual(result['builder_validation_backend'],'fallback')
            self.assertEqual(result['validation']['backend'],'fallback')
            self.assertTrue(result['validation_backend_scope'])

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
