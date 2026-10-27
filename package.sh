#!/usr/bin/env bash
# Package source/runtime examples; local credentials, raw contract materials and run data stay local.
set -euo pipefail
mc_package_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python3 - "$mc_package_root" <<'PY'
from pathlib import Path
import hashlib,json,sys,tarfile
root=Path(sys.argv[1]); out=root/'dist'; out.mkdir(exist_ok=True)
excluded={'node_modules','.venv','__pycache__','dist','logs','artifacts','.git','.mc-plans','.mc-backups'}
allowed={'README.md','.gitignore','.gitattributes','CHANGELOG.md','VERSION','mc.sh','quickstart.sh','package.sh','verify.sh','scripts','mcp','harness','docs','config','knowledge','workspace','vendor','tests','examples'}
files=[]
for path in sorted(root.rglob('*')):
    rel=path.relative_to(root)
    if rel.parts[0] not in allowed or any(v in excluded for v in rel.parts) or path.name in {'connection.json','model.local.json'} or path.name.startswith('.env') or path.name.endswith(('.pyc','.local.json')):
        continue
    if rel.parts[0]=='workspace' and rel.parts[1:2]!=('examples',): continue
    if path.is_symlink(): raise RuntimeError(f'Project archive must not contain symlinks: {rel}')
    if path.is_file(): files.append(path)
version=(root/'VERSION').read_text().strip()
manifest={'version':version,'files':{str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
(out/'MANIFEST.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
archive=out/f'mc-tech-execution-{version}.tar.gz'
with tarfile.open(archive,'w:gz') as tar:
    for path in files: tar.add(path,arcname=str(Path('mc-tech-execution')/path.relative_to(root)),recursive=False)
    tar.add(out/'MANIFEST.json',arcname='mc-tech-execution/MANIFEST.json')
(out/'SHA256SUMS').write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n')
print(archive)
PY
