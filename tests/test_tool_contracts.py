"""Descriptions must reach callers and preserve actual input/output behavior."""
import asyncio
from copy import deepcopy
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'mcp'))
from adapters import ToolExecutionError, execute_tool, validate_registry
from tools import TOOLS, prepare_tools


class ToolContracts(unittest.TestCase):
    def tool(self, name):return next(t for t in TOOLS if t['name'] == name)

    def test_discoverable_parameters_have_meaning_and_valid_default_annotations(self):
        for tool in TOOLS:
            for name, field in tool['inputSchema'].get('properties', {}).items():
                with self.subTest(tool=tool['name'], parameter=name):
                    self.assertTrue(field.get('description'), '参数必须告诉调用方如何填值')
                    if 'default' in field:self.assertTrue(Draft202012Validator(field).is_valid(field['default']))
        case = self.tool('openmc_submit_job')['inputSchema']['properties']['case_dir']
        self.assertIn('MC_WORKSPACE', case['description'])
        self.assertIn('openmc_list_parameters', self.tool('openmc_submit_scan')['inputSchema']['properties']['axes']['description'])
        selection = self.tool('openmc_plot_results')['inputSchema']['properties']['filter_selection']
        self.assertTrue(selection['items']['properties']['bins']['items']['description'])

    def test_omitted_automatic_selection_is_not_advertised_as_explicit_null(self):
        definition = self.tool('openmc_plot_results')['inputSchema']
        self.assertTrue(Draft202012Validator(definition).is_valid({'job_id':'1'*32}))
        for name in ['tally_id','score','nuclide','filter_selection','title']:
            self.assertNotIn('default', definition['properties'][name])
            self.assertFalse(Draft202012Validator(definition).is_valid({'job_id':'1'*32,name:None}))
        self.assertEqual(self.tool('read_knowledge')['inputSchema']['properties']['limit']['default'],60)
        self.assertEqual(self.tool('search_knowledge')['inputSchema']['properties']['limit']['default'],5)

    def test_annotations_cannot_change_other_tools_or_original_shared_input(self):
        shared={'type':'string','description':'自定义字符串'}
        def f(value='first'):return {'value':value}
        def g(value='second'):return {'value':value}
        definitions=[{'name':name,'description':'示例','inputSchema':{'type':'object','properties':{'value':shared}},
                      'outputSchema':{'type':'object'},'function':function} for name,function in [('f',f),('g',g)]]
        prepare_tools(definitions)
        self.assertEqual(definitions[0]['inputSchema']['properties']['value']['default'],'first')
        self.assertEqual(definitions[1]['inputSchema']['properties']['value']['default'],'second')
        self.assertNotIn('default',shared)

    def test_new_undocumented_parameter_is_rejected_with_actionable_reason(self):
        def f(unknown):return {}
        with self.assertRaisesRegex(ValueError,'unknown.*缺description'):
            prepare_tools([{'name':'custom','description':'test','function':f,
                            'inputSchema':{'type':'object','properties':{'unknown':{'type':'string'}}},'outputSchema':{'type':'object'}}])

    def test_custom_semantics_are_preserved_and_false_default_is_refused(self):
        def f(limit=10):return {'limit':limit}
        schema={'type':'object','properties':{'limit':{'type':'integer','description':'业务温度上限，摄氏度'}}}
        tool={'name':'temperature','description':'温度条件','inputSchema':schema,'outputSchema':{'type':'object'},'function':f}
        prepare_tools([tool]);self.assertEqual(tool['inputSchema']['properties']['limit']['description'],'业务温度上限，摄氏度')
        wrong={**tool,'inputSchema':deepcopy(schema)};wrong['inputSchema']['properties']['limit']['default']=20
        with self.assertRaisesRegex(ValueError,'default.*不一致'):prepare_tools([wrong])
        def automatic(limit=None):return {'limit':limit}
        for advertised in [1,None]:
            wrong={**tool,'function':automatic,'inputSchema':deepcopy(schema)}
            wrong['inputSchema']['properties']['limit']['default']=advertised
            with self.assertRaisesRegex(ValueError,'default.*不一致'):prepare_tools([wrong])
        def native_default(limit=Decimal('1.5')):return {'limit':float(limit)}
        native={'name':'native','description':'原生内部默认值',
                'inputSchema':{'type':'object','properties':{'limit':{'type':'number','description':'业务上限'}}},
                'outputSchema':{'type':'object'},'function':native_default}
        prepare_tools([native]);json.dumps(native['inputSchema'],allow_nan=False)
        self.assertNotIn('default',native['inputSchema']['properties']['limit'])
        self.assertEqual(asyncio.run(execute_tool(native,{})),{'limit':1.5})

    def test_mixed_adapters_and_async_function_can_be_finalized_and_documented(self):
        async def add(a,b=1):return {'result':a+b}
        input_schema={'type':'object','properties':{'a':{'type':'number','description':'第一个加数'},
                                                  'b':{'type':'number','description':'第二个加数'}},'required':['a'],'additionalProperties':False}
        definitions=[{'name':'add','description':'相加','inputSchema':input_schema,'outputSchema':{'type':'object'},'function':add},
                     {'name':'program','description':'程序','inputSchema':{'type':'object'},'outputSchema':{'type':'object'},'program':{'argv':[sys.executable]}},
                     {'name':'http','description':'API','inputSchema':{'type':'object'},'outputSchema':{'type':'object'},'http':{'url':'http://127.0.0.1/calculate'}}]
        prepare_tools(definitions);validate_registry(definitions)
        self.assertEqual(asyncio.run(execute_tool(definitions[0],{'a':20})),{'result':21})
        spec=importlib.util.spec_from_file_location('mc_reference',ROOT/'scripts/generate_tool_reference.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        reference=module.generate(definitions)
        self.assertIn('第一个加数',reference);self.assertIn('命令行程序适配',reference);self.assertIn('HTTP JSON适配',reference)

    def test_bad_program_arguments_are_rejected_before_executable_starts(self):
        with tempfile.TemporaryDirectory() as temp:
            marker=Path(temp)/'started'
            definition={'name':'program','description':'程序','inputSchema':{'type':'object','properties':{'value':{'type':'number','description':'有限数值'}},'required':['value'],'additionalProperties':False},
                        'outputSchema':{'type':'object'},'program':{'argv':[sys.executable,'-c',f'from pathlib import Path; Path({str(marker)!r}).write_text("started"); print("{{}}")']}}
            prepare_tools([definition])
            for arguments in [{'value':'wrong'},{},{'value':1,'unknown':2}]:
                with self.assertRaises(ToolExecutionError):asyncio.run(execute_tool(definition,arguments))
                self.assertFalse(marker.exists())

    def test_output_annotations_allow_existing_branch_shapes_and_null_statistics(self):
        for name,data in [('openmc_submit_job',{'job_id':'1'*32,'state':'queued'}),
                          ('openmc_job_status',{'job_id':'1'*32,'state':'interrupted','error':'lost worker'}),
                          ('openmc_read_results',{'keff':{'mean':1.2,'std_dev':None},'warnings':['statistics unavailable']}),
                          ('openmc_cancel_job',{'job_id':'1'*32,'state':'cancelled','changed':False})]:
            self.assertTrue(Draft202012Validator(self.tool(name)['outputSchema']).is_valid(data))
