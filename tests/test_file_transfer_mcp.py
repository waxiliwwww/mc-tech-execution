"""Owned new MCP instance + actual host client + byte/real scientific oracle."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]

class FileTransferMCP(unittest.TestCase):
    def exercise(self,real):
        with tempfile.TemporaryDirectory(prefix='file transfer mcp ') as tmp:
            folder=Path(tmp);env={k:v for k,v in os.environ.items() if not k.startswith(('MC_','MCP_','TRANSFER_'))}
            if real:env.update(MC_TEST_OPENMC_CONFIG=os.environ['MC_TEST_OPENMC_CONFIG'],TRANSFER_REAL_OPENMC='1')
            evidence=folder/'evidence';env['TRANSFER_EVIDENCE']=str(evidence)
            session=folder/'session'
            done=subprocess.run([sys.executable,str(ROOT/'scripts/mcp_test_session.py'),'--output',str(session),'--timeout','180','--',sys.executable,str(ROOT/'tests/fixtures/file_transfer_mcp_client.py')],env=env,capture_output=True,text=True,timeout=220)
            if done.returncode:
                self.fail(done.stdout+done.stderr+'\n'+'\n'.join(p.read_text(errors='replace')[-15000:] for p in session.glob('*.log')))
            result=json.loads((evidence/'result.json').read_text());self.assertTrue(result['uploaded_bytes_match']);self.assertTrue(result['file_roundtrip'])
            cleanup=json.loads((session/'receipt.json').read_text());self.assertTrue(cleanup['cleanup']['verified_empty']);self.assertTrue(cleanup['port_released'])
            if real:
                self.assertTrue(result['native_values_match']);self.assertTrue(result['report_dependencies_match']);self.assertTrue(result['preview_image_mcp'])
            # Keep small evidence only when explicitly requested; all scratch data removed.
            dest=os.environ.get('TRANSFER_KEEP_EVIDENCE')
            if dest:
                import shutil
                target=Path(dest)/('real' if real else 'protocol');target.mkdir(parents=True,exist_ok=True)
                shutil.copytree(evidence,target,dirs_exist_ok=True);shutil.copyfile(session/'receipt.json',target/'session.json')
    def test_standard_mcp_upload_import_download(self):self.exercise(False)
    @unittest.skipUnless(os.environ.get('MC_TEST_OPENMC_CONFIG'),'MC_TEST_OPENMC_CONFIG启用真实OpenMC')
    def test_real_mcp_upload_compute_preview_report_download(self):self.exercise(True)
