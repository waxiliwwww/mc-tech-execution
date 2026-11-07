"""Audit regressions: useful schema diagnostics without echoing caller secrets.

No solver or model calls. Check density aliases through both actual validators,
and errors at the real MCP adapter boundary. Does not prove transport physics.
"""
import copy,json,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'mcp'))
from adapters import ToolExecutionError,validate_value
from model_validation import MODEL_SCHEMA,validate_spec
from jsonschema import Draft202012Validator

class AuditContractFeedback(unittest.TestCase):
    def test_g_cc_alias_is_accepted_by_discovery_and_both_backends(self):
        spec=json.loads((ROOT/'examples/model-specs/rectangular-assembly.json').read_text())
        spec['materials'][0]['density_unit']='g/cc'
        Draft202012Validator(MODEL_SCHEMA).validate(spec)
        for backend in ('pydantic','fallback'):validate_spec(copy.deepcopy(spec),backend)
    def test_enum_and_union_errors_explain_expected_schema_without_input(self):
        with self.assertRaises(ToolExecutionError) as caught:
            validate_value('submitted-secret',{'type':'string','enum':['flux','fission']},'Input')
        text=str(caught.exception);self.assertIn('flux',text);self.assertNotIn('submitted-secret',text)
        with self.assertRaises(ToolExecutionError) as caught:
            validate_value({'type':'unknown-secret'},{'oneOf':[{'properties':{'type':{'const':'energy'}}},{'properties':{'type':{'const':'mesh'}}}]},'Input')
        self.assertIn('energy',str(caught.exception));self.assertNotIn('unknown-secret',str(caught.exception))
    def test_old_schema_marker_has_refresh_guidance_without_compatibility_fallback(self):
        spec=json.loads((ROOT/'examples/model-specs/rectangular-assembly.json').read_text());spec['schema_version']='2'
        with self.assertRaisesRegex(ValueError,'刷新tools/list'):validate_spec(spec)
        with self.assertRaisesRegex(ToolExecutionError,'刷新tools/list'):validate_value(spec,MODEL_SCHEMA,'Input')
if __name__=='__main__':unittest.main()
