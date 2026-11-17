#!/usr/bin/env python3
"""Combine a verified source archive and matching compiled launcher, no local data."""
import argparse
import hashlib
import json
from pathlib import Path
import stat
import subprocess
import tarfile
import zipfile

ROOT=Path(__file__).resolve().parents[1]

def package(source,launcher,output):
    subprocess.run(['bash',str(ROOT/'scripts/verify-package.sh'),str(source)],check=True)
    build=json.loads((launcher.parent/'launcher-build.json').read_text(encoding='utf-8-sig'))
    exe=launcher.read_bytes()
    if not exe.startswith(b'MZ') or hashlib.sha256(exe).hexdigest()!=build['exe_sha256']:
        raise ValueError('Launcher bytes do not match the Windows build record')
    with tarfile.open(source,'r:gz') as archive:
        manifest=json.load(archive.extractfile('mc-tech-execution/MANIFEST.json'))
        if manifest['version']!=build['version'] or manifest['files']['scripts/windows/Launcher.cs']!=build['source_sha256']:
            raise ValueError('Launcher was built from a different source/version; rebuild before packaging')
        output.parent.mkdir(parents=True,exist_ok=True)
        pending=output.with_suffix(output.suffix+'.tmp')
        try:
            with zipfile.ZipFile(pending,'w',zipfile.ZIP_DEFLATED) as bundle:
                for member in archive.getmembers():
                    if member.isfile():
                        info=zipfile.ZipInfo(member.name);info.external_attr=(stat.S_IFREG|member.mode)<<16;info.compress_type=zipfile.ZIP_DEFLATED
                        bundle.writestr(info,archive.extractfile(member).read())
                bundle.writestr('mc-tech-execution/openmc-forge.exe',exe)
                bundle.writestr('mc-tech-execution/WINDOWS-LAUNCHER.json',json.dumps(build,indent=2)+'\n')
            pending.replace(output)
        finally:pending.unlink(missing_ok=True)
    checksum=hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix+'.sha256').write_text(checksum+'  '+output.name+'\n')
    print(output)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--launcher',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();package(args.source.resolve(),args.launcher.resolve(),args.output.resolve())
