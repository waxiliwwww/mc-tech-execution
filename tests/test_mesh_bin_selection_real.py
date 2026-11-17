"""Native SDK coordinate adapter: 1D/2D/3D, non-contiguous/reversed selection.

Reuses official Tally.get_values as independent oracle with native-dimensional
bin tuples. No solver/LLM; real StatePoint paths also run in ResultBundleReal.
"""
import json,os,subprocess,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
CONFIG=os.environ.get('MC_TEST_OPENMC_CONFIG')
@unittest.skipUnless(CONFIG,'MC_TEST_OPENMC_CONFIG指定真实SDK运行时')
class MeshBinSelectionReal(unittest.TestCase):
    def test_native_dimensional_bins_and_canonical_tool_bins_agree(self):
        runtime=json.loads(Path(CONFIG).read_text())
        p=subprocess.run(runtime['python_command']+[str(ROOT/'tests/fixtures/mesh_bin_selection_check.py'),str(ROOT/'mcp')],capture_output=True,text=True,timeout=30)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertEqual(p.stdout.count('selection and reversed canonical coordinates agree'),3)
