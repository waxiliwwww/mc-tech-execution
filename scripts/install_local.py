#!/usr/bin/env python3
"""Copy verified source bytes into a Linux filesystem, retaining local runtime/data."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile


def reuse_runtime(target,previous):
    if previous is None or previous==target:return
    cfg=previous/'config/openmc.local.json'
    if not cfg.is_file() or (target/'config/openmc.local.json').exists():return
    # Runtime/data remain owned by the old installation; never move or mutate
    # them while its workers may still be running.
    runtime=previous/'.runtime'
    if runtime.is_dir() and not (target/'.runtime').exists():
        (target/'.runtime').symlink_to(runtime.resolve(),target_is_directory=True)
    destination=target/'config/openmc.local.json'
    shutil.copyfile(cfg,destination);destination.chmod(0o600)
    print('复用原运行时与核数据；原安装仍被引用，请保留：'+str(previous))


def install(source,target,previous=None):
    manifest=json.loads((source/'MANIFEST.json').read_text())
    files=manifest['files']
    for name,digest in files.items():
        relative=Path(name)
        if relative.is_absolute() or '..' in relative.parts:raise ValueError('Invalid source manifest path')
        path=source/relative
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise ValueError('Source differs from MANIFEST.json: '+name)
    if target.exists():
        for name,digest in files.items():
            path=target/name
            if not path.is_file() or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
                raise ValueError('安装目录含不同版本内容，未覆盖：'+str(target)+'；请使用新版本包或选择新的安装目录。')
        reuse_runtime(target,previous)
        print('复用同版Linux安装：'+str(target));return
    target.parent.mkdir(parents=True,exist_ok=True)
    pending=Path(tempfile.mkdtemp(prefix='.'+target.name+'-',dir=target.parent))
    try:
        for name in files:
            dest=pending/name;dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source/name,dest)
            if hashlib.sha256(dest.read_bytes()).hexdigest()!=files[name]:raise ValueError("Source changed during installation: "+name)
            dest.chmod(0o755 if dest.suffix=='.sh' else 0o644)
        shutil.copyfile(source/'MANIFEST.json',pending/'MANIFEST.json')
        # Same Linux filesystem; never merge over an existing installation.
        # The existing directory publish helper provides no-replace semantics.
        import sys
        sys.path.insert(0,str(source/'mcp'))
        from openmc_import import publish_directory
        publish_directory(pending,target)
        reuse_runtime(target,previous)
        print('已安装到Linux：'+str(target))
    finally:
        if pending.exists():shutil.rmtree(pending)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True);parser.add_argument('--target',type=Path,required=True)
    parser.add_argument('--reuse-from',type=Path)
    args=parser.parse_args()
    try:install(args.source.resolve(),args.target.resolve(),args.reuse_from.resolve() if args.reuse_from else None)
    except (OSError,ValueError) as error:raise SystemExit('安装未完成，原目录保留：'+str(error))
