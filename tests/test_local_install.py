"""Local source integrity, repeat setup and optional-component preservation.
Uses real files/subprocesses; Office is an explicitly controlled external binary.
No runtime/data downloads or system changes.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
def load(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/(name+'.py'))
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
installer=load('install_local');extras=load('install_local_extras')

class LocalInstall(unittest.TestCase):
    def setUp(self):
        original_path=sys.path[:];original_modules=set(sys.modules)
        def restore_source_imports():
            sys.path[:]=original_path
            for name in set(sys.modules)-original_modules:
                file=getattr(sys.modules[name],'__file__',None)
                if file and Path(file).is_relative_to(self.root):sys.modules.pop(name,None)
        self.addCleanup(restore_source_imports)
        self.temp=tempfile.TemporaryDirectory(prefix='mc install 中文 spaces ');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'source';self.source.mkdir()
        shutil.copytree(ROOT/'mcp',self.source/'mcp',ignore=shutil.ignore_patterns('.venv','logs','__pycache__'))
        (self.source/'config').mkdir()
        shutil.copy2(ROOT/'config/openmc.json',self.source/'config/openmc.json')
        (self.source/'README.md').write_text('source bytes 中文')
        (self.source/'run.sh').write_text('#!/bin/bash\nexit 0\n')
        self.target=self.root/'Linux install'
        files={p.relative_to(self.source).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in self.source.rglob('*') if p.is_file()}
        (self.source/'MANIFEST.json').write_text(json.dumps({'version':'1','files':files}))

    def test_copy_is_verified_repeat_preserves_local_data_and_scripts_execute(self):
        installer.install(self.source,self.target)
        self.assertEqual((self.target/'README.md').read_bytes(),(self.source/'README.md').read_bytes())
        local=self.target/'config.local.json';local.write_text('private config')
        installer.install(self.source,self.target)
        self.assertEqual(local.read_text(),'private config')
        self.assertTrue((self.target/'run.sh').stat().st_mode & 0o100)

    def test_tampered_source_and_changed_destination_are_never_overwritten(self):
        source=self.source/'README.md';source.write_text('changed')
        with self.assertRaisesRegex(ValueError,'Source differs'):installer.install(self.source,self.target)
        self.assertFalse(self.target.exists())
        source.write_text('source bytes 中文');installer.install(self.source,self.target)
        dest=self.target/'README.md';dest.write_text('user changes')
        with self.assertRaisesRegex(ValueError,'未覆盖'):installer.install(self.source,self.target)
        self.assertEqual(dest.read_text(),'user changes')

    def test_new_version_reuses_runtime_without_moving_old_data(self):
        old=self.root/'old';(old/'config').mkdir(parents=True);(old/'.runtime').mkdir()
        (old/'.runtime/data').write_text('existing nuclear data')
        config=old/'config/openmc.local.json';config.write_text('{"python_command":["/existing/python"]}')
        installer.install(self.source,self.target,old)
        self.assertEqual((self.target/'config/openmc.local.json').read_bytes(),config.read_bytes())
        self.assertEqual((self.target/'.runtime/data').read_text(),'existing nuclear data')
        self.assertTrue((old/'.runtime/data').is_file())

    def test_office_addition_preserves_external_openmc_environment(self):
        config=self.root/'config';config.mkdir()
        office=self.root/'Office program';office.write_text('#!/bin/sh\necho controlled-office-version\n');office.chmod(0o755)
        path=config/'openmc.local.json'
        value={'python_command':['/external/python'],'openmc_command':['/external/openmc'],'nuclear_data_library':'existing-library','office_command':[str(office)]}
        path.write_text(json.dumps(value))
        with patch.object(extras,'ROOT',self.root):extras.install(office=True)
        result=json.loads(path.read_text())
        for key in ['python_command','openmc_command','nuclear_data_library']:self.assertEqual(result[key],value[key])

    def test_cad_default_and_explicit_skip_reach_fresh_and_reused_installers(self):
        # Execute the actual dispatcher. External installers record arguments;
        # dependency installation/conversion have their own real probes/tests.
        import os,subprocess,sys,shlex
        for existing in (False,True):
            for option in (None,'--with-cad','--skip-cad'):
                with self.subTest(existing=existing,option=option),tempfile.TemporaryDirectory() as tmp:
                    root=Path(tmp);(root/'scripts').mkdir();(root/'mcp/.venv/bin').mkdir(parents=True);(root/'config').mkdir()
                    shutil.copy2(ROOT/'scripts/local-setup.sh',root/'scripts/local-setup.sh')
                    (root/'mcp/setup.sh').write_text('#!/bin/bash\nexit 0\n')
                    (root/'quickstart.sh').write_text('#!/bin/bash\nprintf "%s\\n" "$@" > "$SETUP_DISPATCH_RECEIPT"\n')
                    (root/'scripts/install_local_extras.py').write_text('import os,sys\nfrom pathlib import Path\nPath(os.environ["SETUP_DISPATCH_RECEIPT"]).write_text("\\n".join(sys.argv[1:]))\n')
                    python=root/'mcp/.venv/bin/python';python.write_text('#!/bin/bash\nif [[ "$1" == scripts/install_local_extras.py ]]; then exec '+shlex.quote(sys.executable)+' "$@"; fi\ncat > /dev/null\n');python.chmod(0o755)
                    if existing:(root/'config/openmc.local.json').write_text('{"existing":"untouched"}')
                    receipt=root/'dispatch.txt'
                    result=subprocess.run(['bash',str(root/'scripts/local-setup.sh')]+([option] if option else []),env={**os.environ,'SETUP_DISPATCH_RECEIPT':str(receipt)},capture_output=True,text=True)
                    self.assertEqual(result.returncode,0,result.stderr)
                    flags=receipt.read_text().splitlines() if receipt.exists() else []
                    if existing:self.assertEqual('--cad' in flags,option!='--skip-cad')
                    else:self.assertEqual('--skip-cad' in flags,option=='--skip-cad')
                    if existing:self.assertEqual((root/'config/openmc.local.json').read_text(),'{"existing":"untouched"}')

    def test_setup_summary_explicitly_allows_no_cad_installation(self):
        original={'openmc_command':['existing-openmc']}
        message=extras.cad_status(original)
        self.assertIn('未安装/未配置',message)
        self.assertIn('基础XML计算仍可用',message)
        self.assertIn('cadquery',message);self.assertIn('cad_to_dagmc',message)
        self.assertEqual(original,{'openmc_command':['existing-openmc']})

    def test_setup_summary_distinguishes_import_failure_from_usable_dependencies(self):
        program=self.root/'cad-probe';program.write_text('#!/bin/sh\necho ImportError: controlled-missing-cad >&2\nexit 1\n');program.chmod(0o755)
        cfg={'cad_python_command':[str(program)]}
        self.assertIn('检查失败',extras.cad_status(cfg))
        program.write_text('#!/bin/sh\nexit 0\n')
        text=extras.cad_status(cfg)
        self.assertIn('依赖已检查',text);self.assertIn('DAGMC输运还须确认',text)
