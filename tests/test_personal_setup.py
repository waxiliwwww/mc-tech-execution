"""One-click setup reuses real installed runtime and keeps edits in one file.
No downloads, physics solver, shared configuration writes or provider calls.
"""
import hashlib,json,os,shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

@unittest.skipUnless(os.environ.get('MC_TEST_OPENMC_CONFIG'),'MC_TEST_OPENMC_CONFIG enables actual runtime setup probe')
class PersonalSetup(unittest.TestCase):
    def test_repeat_setup_keeps_personal_workspace_and_original_records(self):
        with tempfile.TemporaryDirectory(prefix='personal setup ') as folder:
            root=Path(folder)/'install';root.mkdir()
            for name in ('mcp','skills','config'):
                shutil.copytree(ROOT/name,root/name,ignore=shutil.ignore_patterns('.venv','logs','__pycache__','*.local.json'))
            shutil.copytree(ROOT/'mcp/.venv',root/'mcp/.venv',symlinks=True)
            shutil.copy2(ROOT/'VERSION',root/'VERSION')
            (root/'scripts').mkdir()
            for name in ('local-setup.sh','install_local_extras.py'):shutil.copy2(ROOT/'scripts'/name,root/'scripts'/name)
            # This is the actual prepared runtime, not an installation stub.
            legacy=root/'config/openmc.local.json';shutil.copyfile(os.environ['MC_TEST_OPENMC_CONFIG'],legacy)
            old=root/'old workspace';old.mkdir();(old/'original-task-record.json').write_text('{"original":"retained"}')
            launcher=root/'config/launcher.local.json';launcher.write_text(json.dumps({'workspace':str(old)}))
            unchanged={p:p.read_bytes() for p in (legacy,launcher)}
            profile=Path(folder)/'personal/config.json'
            env={k:v for k,v in os.environ.items() if not k.startswith(('MC_','MCP_'))}
            env.update(MC_PERSONAL_CONFIG=str(profile),PIP_NO_INDEX='1')
            command=['bash',str(root/'scripts/local-setup.sh'),'--skip-cad','--no-connection-output']
            first=subprocess.run(command,env=env,capture_output=True,text=True,timeout=60)
            self.assertEqual(first.returncode,0,first.stderr)
            saved=json.loads(profile.read_text());self.assertEqual(saved['workspace'],str(old))
            self.assertEqual(saved['runtime']['python_command'],json.loads(legacy.read_text())['python_command'])
            new=root/'new workspace';shutil.move(str(old),str(new));saved['workspace']=str(new)
            profile.write_text(json.dumps(saved))
            second=subprocess.run(command,env=env,capture_output=True,text=True,timeout=60)
            self.assertEqual(second.returncode,0,second.stderr)
            self.assertEqual(json.loads(profile.read_text())['workspace'],str(new))
            self.assertEqual((new/'original-task-record.json').read_text(),'{"original":"retained"}')
            self.assertEqual({p:p.read_bytes() for p in unchanged},unchanged)
            self.assertFalse(old.exists())
            # A failed directory probe must not repoint future connections.
            blocking=root/'not a directory';blocking.write_text('existing user file')
            failed=subprocess.run(command+['--workspace',str(blocking)],env=env,capture_output=True,text=True,timeout=60)
            self.assertNotEqual(failed.returncode,0)
            self.assertEqual(json.loads(profile.read_text())['workspace'],str(new))
            self.assertEqual((new/'original-task-record.json').read_text(),'{"original":"retained"}')
            self.assertEqual(blocking.read_text(),'existing user file')
            evidence=ROOT/'artifacts/personal-config-20261126';evidence.mkdir(parents=True,exist_ok=True)
            (evidence/'one-click-setup.json').write_text(json.dumps({'real_local_setup_dispatch_and_environment_probe':True,'repeated_setup_kept_personal_edit':True,'failed_workspace_probe_did_not_change_saved_workspace':True,'legacy_files_unchanged':True,'existing_runtime_reused':True,'new_installations':0,'provider_requests':0,'physics_solves':0},indent=2))

if __name__=='__main__':unittest.main()
