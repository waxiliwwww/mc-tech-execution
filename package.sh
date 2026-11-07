#!/usr/bin/env bash
# Package source/runtime examples; local credentials, raw contract materials and run data stay local.
set -euo pipefail
mc_package_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python3 - "$mc_package_root" <<'PY'
from pathlib import Path
import fcntl,hashlib,io,json,os,re,shutil,subprocess,sys,tarfile,tempfile
root=Path(sys.argv[1]);out=root/'dist';out.mkdir(exist_ok=True)
lock=(out/'.package.lock').open('a')
try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError:raise SystemExit('已有打包进程运行；本次未发布，请等待后重试')
excluded={'node_modules','.venv','__pycache__','dist','logs','artifacts','.git','.mc-plans','.mc-backups'}
allowed={'README.md','.gitignore','.gitattributes','CHANGELOG.md','VERSION','pyproject.toml','.github','mc.sh','quickstart.sh','cleanup.sh','create-mcp.sh','package.sh','verify.sh','setup-example-sources.sh','scripts','deploy','mcp','harness','docs','config','knowledge','fixtures','workspace','vendor','tests','examples'}
files=[]
for path in sorted(root.rglob('*')):
    rel=path.relative_to(root)
    if rel.parts[0] not in allowed or any(v in excluded for v in rel.parts) or path.name in {'connection.json','model.local.json'} or path.name.startswith('.env') or path.name.endswith(('.pyc','.local.json','.pem','.key','.p12')):
        continue
    if rel.parts[0]=='workspace' and rel.parts[1:2]!=('examples',):continue
    if path.is_symlink():raise RuntimeError(f'Project archive must not contain symlinks: {rel}')
    if path.is_file():files.append(path)
version=(root/'VERSION').read_text().strip()
if not re.fullmatch(r'[0-9A-Za-z][0-9A-Za-z.+_-]*',version):
    raise RuntimeError('VERSION必须是可用于文件名的版本标识，不含路径或空白')
manifest={'version':version,'files':{str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
manifest_bytes=(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
archive_name=f'mc-tech-execution-{version}.tar.gz'
manifest_name=f'mc-tech-execution-{version}.MANIFEST.json'
checksum_name=f'mc-tech-execution-{version}.sha256'
stage=Path(tempfile.mkdtemp(prefix='.package-',dir=out));keep_stage=False
try:
    archive=stage/archive_name
    with tarfile.open(archive,'w:gz') as tar:
        for path in files:tar.add(path,arcname=str(Path('mc-tech-execution')/path.relative_to(root)),recursive=False)
        info=tarfile.TarInfo('mc-tech-execution/MANIFEST.json');info.size=len(manifest_bytes)
        tar.addfile(info,io.BytesIO(manifest_bytes))
    checksum=hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive_name+'\n'
    (stage/manifest_name).write_bytes(manifest_bytes);(stage/checksum_name).write_text(checksum)
    (stage/'MANIFEST.json').write_bytes(manifest_bytes);(stage/'SHA256SUMS').write_text(checksum)
    verified=subprocess.run(['bash',str(root/'scripts/verify-package.sh'),str(archive)],capture_output=True,text=True)
    if verified.returncode:
        raise RuntimeError('临时归档校验失败，未发布；源文件可能在打包期间改变：'+verified.stderr.strip())
    # Publish the archive last; all bytes and backups are ready before replacement.
    # A failed rename restores earlier replacements. Power loss still requires
    # explicit archive verification; independent files are not one atomic object.
    names=[manifest_name,checksum_name,'MANIFEST.json','SHA256SUMS',archive_name]
    backups=stage/'rollback';backups.mkdir();existed=set()
    for name in names:
        target=out/name
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise RuntimeError('输出位置不是普通文件，未发布：'+str(target))
        if target.exists():shutil.copy2(target,backups/name);existed.add(name)
    published=[]
    try:
        for name in names:
            os.replace(stage/name,out/name);published.append(name)
    except BaseException as error:
        failures=[]
        for name in reversed(published):
            try:
                if name in existed:os.replace(backups/name,out/name)
                else:(out/name).unlink(missing_ok=True)
            except OSError as rollback_error:failures.append(name+': '+str(rollback_error))
        if failures:
            keep_stage=True
            raise RuntimeError('发布失败且回退未完成；恢复材料保留在'+str(stage)+'；'+ '; '.join(failures)) from error
        raise RuntimeError('发布失败；原有交付组合已恢复') from error
finally:
    if not keep_stage:shutil.rmtree(stage)
print(out/archive_name)  # Preserve the original machine-readable stdout path.
print('打包成功；对应清单：'+str(out/manifest_name),file=sys.stderr)
print('归档校验：'+str(out/checksum_name),file=sys.stderr)
print('校验命令：./scripts/verify-package.sh '+str(Path('dist')/archive_name),file=sys.stderr)
PY
