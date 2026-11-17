#!/usr/bin/env python3
"""Add requested CAD/Office components without replacing the OpenMC runtime."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'mcp'))
from openmc_runtime import atomic_json


def install(cad=False,office=False):
    path=ROOT/'config/openmc.local.json';cfg=json.loads(path.read_text())
    if cad:
        command=cfg['python_command']
        interpreter=subprocess.check_output(command+['-c','import sys;print(sys.executable)'],text=True,timeout=30).strip()
        prefix=ROOT/'.runtime/cadenv'
        current=cfg.get('cad_python_command',[])
        if len(current)==1 and Path(current[0]).parent.name=='bin':prefix=Path(current[0]).parent.parent
        subprocess.run(command+[str(ROOT/'scripts/install_cad.py'),'--python',interpreter,'--prefix',str(prefix)],check=True)
    if office:
        cfg=json.loads(path.read_text())  # Preserve a successful CAD update.
        command=cfg.get('office_command',[])
        if not command or not shutil.which(command[0]):
            system=shutil.which('soffice')
            if system:command=[system]
            else:
                subprocess.run([sys.executable,str(ROOT/'scripts/install_office.py')],check=True)
                command=[str(ROOT/'.runtime/office/soffice')]
        subprocess.run(command+['--headless','--version'],check=True,timeout=30)
        cfg['office_command']=command
        for base in [ROOT/'.runtime/office/usr/share/fonts',Path('/usr/share/fonts')]:
            match=next(base.rglob('NotoSansCJK-Regular.ttc'),None)
            if match:cfg['font_path']=str(match.resolve());break
        atomic_json(path,cfg)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cad',action='store_true');parser.add_argument('--office',action='store_true')
    args=parser.parse_args();install(args.cad,args.office)
