"""Personal settings migration and persistence, using isolated user files only."""
import hashlib,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'mcp'))
import personal_config as config

class PersonalConfig(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'old install';(self.root/'config').mkdir(parents=True);(self.root/'mcp').mkdir()
        self.file=Path(self.temp.name)/'user/config.json'
        env={k:v for k,v in os.environ.items() if not k.startswith(('MC_','MCP_'))}
        env['MC_PERSONAL_CONFIG']=str(self.file)
        guard=patch.dict(os.environ,env,clear=True);guard.start();self.addCleanup(guard.stop)
        (self.root/'config/openmc.json').write_text(json.dumps({'python_command':['python3'],'openmc_command':['openmc'],'job_timeout_seconds':3600}))
    def put(self,name,value):
        path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value));return path
    def test_reads_are_side_effect_free_and_defaults_still_work(self):
        self.assertEqual(config.workspace(self.root),self.root/'workspace')
        self.assertEqual(config.logs(self.root),self.root/'mcp/logs')
        self.assertEqual(config.runtime(self.root)['python_command'],['python3'])
        self.assertFalse(self.file.parent.exists())
    def test_first_setup_imports_only_active_legacy_files_and_preserves_them(self):
        old=self.put('config/openmc.local.json',{'python_command':['.runtime/env/bin/python'],'openmc_command':['.runtime/env/bin/openmc'],'cross_sections':'data/cross_sections.xml','extra_site_setting':'retained'})
        launcher=self.put('config/launcher.local.json',{'workspace':'original jobs'})
        service=self.put('mcp/service.config.json',{'port':9191,'host':'127.0.0.1','path':'/tools','version':'obsolete','versionFile':'obsolete','bearerToken':'not-imported'})
        slurm=self.put('site/slurm.json',{'transport':'local','remote_root':'/scratch/u','resource_defaults':{'ntasks':3}})
        transfer=self.put('site/transfer.json',{'chunk_bytes':4096})
        os.environ.update(MC_SLURM_CONFIG=str(slurm),MC_TRANSFER_CONFIG=str(transfer))
        before={p:p.read_bytes() for p in (old,launcher,service,slurm,transfer)}
        value=config.ensure(self.root)
        self.assertEqual(value['workspace'],str(self.root/'original jobs'))
        self.assertEqual(value['runtime']['python_command'],[str(self.root/'.runtime/env/bin/python')])
        self.assertEqual(value['runtime']['cross_sections'],str(self.root/'data/cross_sections.xml'))
        self.assertEqual(value['runtime']['extra_site_setting'],'retained')
        self.assertEqual(value['slurm']['resource_defaults']['ntasks'],3)
        self.assertEqual(value['transfer']['chunk_bytes'],4096)
        self.assertEqual(value['http'],{'host':'127.0.0.1','port':9191,'path':'/tools'})
        self.assertEqual(self.file.stat().st_mode & 0o777,0o600)
        config.update({'workspace':'/new/jobs','runtime':{'job_timeout_seconds':55}},self.root)
        config.ensure(self.root)
        self.assertEqual(config.workspace(self.root),Path('/new/jobs'))
        self.assertEqual(config.runtime(self.root)['job_timeout_seconds'],55)
        self.assertEqual({p:p.read_bytes() for p in before},before)
    def test_migrated_personal_runtime_survives_obsolete_broken_file(self):
        old=self.put('config/openmc.local.json',{'python_command':['/old/python'],'openmc_command':['/old/openmc']})
        config.ensure(self.root)
        old.write_text('not JSON')
        self.assertEqual(config.runtime(self.root)['python_command'],['/old/python'])
        self.assertEqual(old.read_text(),'not JSON')

    def test_no_personal_file_keeps_installed_module_legacy_log_directory(self):
        site=self.root/'site';module=site/'mc_agent_tools/personal_config.py'
        with patch.object(config,'__file__',str(module)),patch.object(config,'ROOT',site):
            self.assertEqual(config.logs(site),site/'mc_agent_tools/logs')
        self.assertFalse(self.file.exists())

    def test_hand_edited_home_workspace_uses_user_home(self):
        with patch.dict(os.environ,{'HOME':str(self.root/'home')}):
            config.update({'workspace':'~/calculations'},self.root,seed=False)
            self.assertEqual(config.workspace(self.root),self.root/'home/calculations')

    def test_explicit_overrides_and_writers_use_same_effective_file(self):
        config.ensure(self.root);before=self.file.read_bytes()
        external=self.put('external.json',{'python_command':['/external/python'],'openmc_command':['/external/openmc']})
        os.environ.update(MC_OPENMC_CONFIG=str(external),MC_WORKSPACE='/explicit/jobs')
        self.assertEqual(config.workspace(self.root),Path('/explicit/jobs'))
        cfg=config.runtime(self.root);cfg['cad_python_command']=['/cad/python'];config.save_runtime(cfg,self.root)
        self.assertEqual(json.loads(external.read_text())['cad_python_command'],['/cad/python'])
        self.assertEqual(self.file.read_bytes(),before)
        del os.environ['MC_OPENMC_CONFIG']
        config.save_runtime({'python_command':['.runtime/bin/python'],'openmc_command':['openmc']},self.root)
        self.assertEqual(config.runtime(self.root)['python_command'],[str(self.root/'.runtime/bin/python')])
        self.assertFalse((self.root/'config/openmc.local.json').exists())
    def test_partial_profile_reuses_legacy_runtime_and_concurrent_updates_keep_other_sections(self):
        self.put('config/openmc.local.json',{'python_command':['/old/python'],'openmc_command':['/old/openmc']})
        config.update({'runtime':{'job_timeout_seconds':77}},self.root,seed=False)
        self.assertEqual(config.runtime(self.root)['python_command'],['/old/python'])
        code='import sys;sys.path.insert(0,sys.argv[1]);import personal_config as p;p.update({sys.argv[2]:{"value":sys.argv[3]}},sys.argv[4])'
        procs=[subprocess.Popen([sys.executable,'-c',code,str(ROOT/'mcp'),name,name,str(self.root)]) for name in ('http','transfer','slurm')]
        for proc in procs:self.assertEqual(proc.wait(timeout=10),0)
        saved=config.read()
        for name in ('http','transfer','slurm'):self.assertEqual(saved[name]['value'],name)
        self.assertEqual(saved['runtime']['job_timeout_seconds'],77)
    def test_summary_shows_common_effective_values_without_raw_commands_or_site_secrets(self):
        config.update({'runtime':{'python_command':['/python','--secret','DO-NOT-SHOW'],'openmc_command':['/openmc']},'slurm':{'host':'cluster','transport':'ssh','remote_root':'/jobs','setup':['DO-NOT-SHOW'],'password':'DO-NOT-SHOW'},'http':{'host':'127.0.0.1','port':8770,'path':'/mcp','bearerToken':'DO-NOT-SHOW'}},self.root)
        value=config.summary(self.root,Path('/active/jobs'))
        self.assertNotIn('DO-NOT-SHOW',json.dumps(value));self.assertEqual(value['workspace'],'/active/jobs')
        self.assertEqual(value['log_directory'],'/active/jobs/.mc-logs')
        self.assertEqual(value['runtime']['python_command'],['/python'])
    def test_http_profile_does_not_override_explicit_config_or_current_package_identity(self):
        import server
        config.update({'http':{'port':9192,'path':'/custom','version':'obsolete','versionFile':'/missing'}},self.root)
        env=dict(os.environ)
        active=server.load_config(env=env)
        self.assertEqual(active.port,9192);self.assertEqual(active.path,'/custom')
        self.assertEqual(active.version,(ROOT/'VERSION').read_text().strip())
        custom=self.put('service.json',{'host':'127.0.0.1','port':9193,'path':'/original','name':'explicit','clientName':'client','version':'external-explicit'})
        external=server.load_config(env={**env,'MCP_CONFIG_FILE':str(custom)})
        self.assertEqual(external.port,9193);self.assertEqual(external.path,'/original')
        self.assertEqual(server.load_config(env={**env,'MCP_PORT':'9194'}).port,9194)

    def test_xdg_and_explicit_personal_location_follow_user_account(self):
        del os.environ['MC_PERSONAL_CONFIG'];os.environ['XDG_CONFIG_HOME']=str(Path(self.temp.name)/'xdg')
        self.assertEqual(config.location(),Path(self.temp.name)/'xdg/openmc-forge/config.json')

if __name__=='__main__':unittest.main()
