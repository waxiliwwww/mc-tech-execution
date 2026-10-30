#!/usr/bin/env python3
"""Build the pinned genuine OpenMOC dependency inside the configured Python prefix."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'mcp'))
from nuclear_data import data_digest,fetch_file
from openmc_runtime import atomic_json


def install(archive: Path | None = None):
    item=json.loads((ROOT/'config/openmoc.json').read_text())
    receipt=Path(sys.prefix)/'mc-openmoc-install.json'
    patch_description='SWIG exception $function -> $action; cap parallel build workers'
    def installed_identity():
        # Runs outside the source tree so a generated local wrapper cannot mask
        # an incomplete installation. Record both wrapper and native library.
        code="import openmoc,sys,hashlib,json;from pathlib import Path;m=openmoc.Material();m.setNumEnergyGroups(2);assert m.getNumEnergyGroups()==2;print(json.dumps({str(Path(v.__file__).relative_to(sys.prefix)):hashlib.sha256(Path(v.__file__).read_bytes()).hexdigest() for k,v in sys.modules.items() if k in ['openmoc','openmoc.openmoc','_openmoc','openmoc._openmoc']}))"
        return json.loads(subprocess.check_output([sys.executable,'-c',code],cwd=ROOT,text=True))
    if receipt.is_file():
        old=json.loads(receipt.read_text())
        if (old.get('installer_version')==2 and old.get('source_sha256')==item['sha256']
            and old.get('revision')==item['revision'] and old.get('precision')==item['precision']
            and old.get('swig_version')==item['swig_version'] and old.get('binding_patch')==patch_description):
            if not old.get('installed_files') or installed_identity()!=old['installed_files']:
                raise ValueError('已安装OpenMOC包装层或动态库改变，未复用；保留环境以便排查')
            print('OpenMOC已安装并通过真实绑定检查。',flush=True);return
    if archive is None:
        archive=ROOT/'.runtime/downloads/openmoc.tar.gz';archive.parent.mkdir(parents=True,exist_ok=True)
        if not archive.exists():fetch_file(item['url'],archive,item['bytes'],expected_sha256=item['sha256'],max_time_seconds=900)
    if archive.stat().st_size!=item['bytes'] or data_digest(archive)!=item['sha256']:
        raise ValueError('OpenMOC源码归档身份不符，未编译')
    directory=ROOT/'.runtime/openmoc-build';directory.mkdir(parents=True,exist_ok=True)
    source=directory/('OpenMOC-'+item['revision'])
    if not source.exists():
        with tarfile.open(archive) as original:
            original.extractall(directory,filter='data')
    # Verify cached upstream inputs before compiling, including permitted glue
    # adaptations. Generated binaries are checked separately on installed reuse.
    with tarfile.open(archive) as original:
        for member in original.getmembers():
            if not member.isfile():continue
            path=directory/member.name
            expected=original.extractfile(member).read();allowed=[expected]
            if member.name.endswith('/openmoc/swig/openmoc.i'):
                allowed.append(expected.replace(b'$function',b'$action'))
            if member.name.endswith('/setup.py'):
                allowed.append(expected.replace(b'num_cpus = multiprocessing.cpu_count()',b"num_cpus = min(multiprocessing.cpu_count(), int(os.environ.get('MC_BUILD_JOBS', '4')))"))
            if path.is_symlink() or not path.is_file() or path.read_bytes() not in allowed:
                raise ValueError('OpenMOC编译缓存源码已改变，未继续编译：'+str(path))
    interface=source/'openmoc/swig/openmoc.i'
    text=interface.read_text()
    # Modern SWIG's exception directive uses $action. This is binding glue;
    # every solver source and its algorithm remain the fixed upstream bytes.
    if text.count('$function')==1:interface.write_text(text.replace('$function','$action'))
    elif '$function' in text or '$action' not in text:raise ValueError('SWIG适配位置与固定版本不符')
    setup=source/'setup.py';text=setup.read_text()
    original='num_cpus = multiprocessing.cpu_count()'
    if original in text:
        setup.write_text(text.replace(original,"num_cpus = min(multiprocessing.cpu_count(), int(os.environ.get('MC_BUILD_JOBS', '4')))"))
    env={**os.environ,'PATH':str(Path(sys.executable).parent)+os.pathsep+os.environ.get('PATH',''),'USE_SETUPTOOLS':'1'}
    swig=subprocess.check_output(['swig','-version'],env=env,text=True)
    if 'Version '+item['swig_version'] not in swig:raise ValueError('SWIG版本不符；先安装config/example-requirements.txt')
    print('编译真实OpenMOC（初配一次；默认最多4个编译进程）...',flush=True)
    # A pristine upstream install collects Python files before SWIG generates
    # openmoc.py. Generate the wrapper first, then let install collect it.
    generate="import config,subprocess,shutil;cfg=config.configuration();cfg.cc='gcc';cfg.fp="+repr(item['precision'])+";cfg.setup_extension_modules();subprocess.run(['swig',*cfg.swig_flags,'-DGCC','-o','openmoc/swig/openmoc_wrap.cpp','openmoc/swig/openmoc.i'],check=True);shutil.move('openmoc/swig/openmoc.py','openmoc/openmoc.py')"
    subprocess.run([sys.executable,'-c',generate],cwd=source,env=env,check=True)
    subprocess.run([sys.executable,'setup.py','install','--prefix',sys.prefix,'--cc=gcc','--fp='+item['precision']],cwd=source,env=env,check=True)
    subprocess.run([sys.executable,'-c','import openmoc; m=openmoc.Material(); m.setNumEnergyGroups(2); assert m.getNumEnergyGroups()==2'],cwd=ROOT,check=True)
    atomic_json(receipt,{'installer_version':2,'revision':item['revision'],'source_sha256':item['sha256'],
        'swig_version':item['swig_version'],'precision':item['precision'],
        'binding_patch':patch_description,'installed_files':installed_identity(),
        'python_prefix':sys.prefix})
    print('OpenMOC初次准备完成。',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--archive',type=Path)
    args=parser.parse_args()
    try:install(args.archive)
    except (ValueError,OSError,subprocess.SubprocessError) as error:raise SystemExit('OpenMOC准备失败：'+str(error))
