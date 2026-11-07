"""Parity checks for the Pydantic and stdlib validators."""
import copy
import json
import math
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'mcp'))
from model_validation import (MODEL_SCHEMA,available_backend,validate_spec_result)

try:
    from model_validation.pydantic_backend import ModelSpec
except ImportError:
    ModelSpec=None


def examples():
    return [json.loads(path.read_text(encoding='utf-8'))
            for path in sorted((ROOT/'examples/model-specs').glob('*.json'))]


def invalid_cases():
    base=examples()[0]
    changes=[]
    def add(name, mutate):
        value=copy.deepcopy(base);mutate(value);changes.append((name,value))
    add('string_region',lambda s:s['cells'][0].update(region='1 -2'))
    add('bad_region_op',lambda s:s['cells'][0].update(region={'op':'bad'}))
    add('empty_region',lambda s:s['cells'][0].update(region={'op':'intersection','items':[]}))
    add('bad_surface_param',lambda s:s['surfaces'][0].update(parameters={'y0':2}))
    add('unknown_field',lambda s:s.update(extra=True))
    add('nan_density',lambda s:s['materials'][0].update(density=math.nan))
    add('null_validation',lambda s:s.update(validation=None))
    add('null_source_strength',lambda s:s['settings']['source'].update(strength=None))
    def deep(s):
        region={'op':'halfspace','surface':1,'side':'negative'}
        for _ in range(81):region={'op':'union','items':[region]}
        s['cells'][0]['region']=region
    add('deep_region',deep)
    add('duplicate_id',lambda s:s['materials'].append(copy.deepcopy(s['materials'][0])))
    add('dangling_fill',lambda s:s['cells'][0]['fill'].update(id=999))
    add('bad_tally_filter',lambda s:s['tallies'][0]['filters'][0].update(dimension=[101,101,101]))
    add('zero_mesh_dimension',lambda s:s['tallies'][0]['filters'][0].update(dimension=[0,1,1]))
    return changes


class ValidationParity(unittest.TestCase):
    def test_static_schema_has_regions(self):
        self.assertIn('HalfSpace', MODEL_SCHEMA['$defs'])
        self.assertIn('RegionIntersection', MODEL_SCHEMA['$defs'])
        self.assertIn('XPlane', MODEL_SCHEMA['$defs'])

    def test_valid_examples_pass_both_available_backends(self):
        for spec in examples():
            validate_spec_result(spec,backend='fallback')
            if ModelSpec is not None:
                validate_spec_result(spec,backend='pydantic')

    def test_invalid_cases_fail_both_available_backends(self):
        for name,spec in invalid_cases():
            with self.subTest(name=name,backend='fallback'):
                with self.assertRaises(ValueError):validate_spec_result(spec,backend='fallback')
            if ModelSpec is not None:
                with self.subTest(name=name,backend='pydantic'):
                    with self.assertRaises(ValueError):validate_spec_result(spec,backend='pydantic')

    @unittest.skipUnless(ModelSpec is not None,'Pydantic backend not installed')
    def test_pydantic_schema_is_the_checked_in_schema(self):
        self.assertEqual(ModelSpec.model_json_schema(),MODEL_SCHEMA)

    def test_auto_backend_reports_its_identity(self):
        report=validate_spec_result(examples()[0],backend='auto')
        self.assertEqual(report['backend'],available_backend())
