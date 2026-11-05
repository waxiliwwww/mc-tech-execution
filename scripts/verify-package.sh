#!/usr/bin/env bash
# Verify exactly the selected source archive; no extraction, installation or computation.
set -euo pipefail
python3 - "$@" <<'PY'
from pathlib import Path,PurePosixPath
import hashlib,json,re,sys,tarfile

def digest(stream):
    value=hashlib.sha256()
    for block in iter(lambda:stream.read(1024*1024),b''):value.update(block)
    return value.hexdigest()

def verify(archive):
    if not archive.is_file() or not archive.name.endswith('.tar.gz'):
        raise ValueError('请选择实际交付的.tar.gz压缩包')
    stem=archive.name[:-7]
    checksum=archive.with_name(stem+'.sha256')
    if not checksum.is_file():
        raise ValueError('缺少对应摘要：'+str(checksum)+'；请从交付方取得，不使用通用SHA256SUMS猜测')
    matches=[]
    for line in checksum.read_text(encoding='utf-8').splitlines():
        fields=line.split(maxsplit=1)
        if len(fields)==2 and fields[1].lstrip('*')==archive.name:
            if not re.fullmatch('[0-9a-fA-F]{64}',fields[0]):raise ValueError('摘要格式错误')
            matches.append(fields[0].lower())
    if len(matches)!=1:raise ValueError('摘要须有且仅有一条匹配所选压缩包文件名的记录')
    with archive.open('rb') as source:
        if digest(source)!=matches[0]:raise ValueError('压缩包SHA256不一致，未继续读取归档')
    prefix='mc-tech-execution/'
    with tarfile.open(archive,'r:gz') as bundle:
        members=bundle.getmembers();files={}
        names=set()
        for item in members:
            path=PurePosixPath(item.name)
            if (item.name in names or path.is_absolute() or '..' in path.parts
                    or '\\' in item.name or str(path)!=item.name.rstrip('/')
                    or (item.name!='mc-tech-execution' and not item.name.startswith(prefix))):
                raise ValueError('归档含重复或非规范路径：'+item.name)
            names.add(item.name)
            if item.isdir():continue
            if not item.isfile():raise ValueError('归档含链接或非普通文件：'+item.name)
            files[item.name]=item
        manifest_name=prefix+'MANIFEST.json'
        if manifest_name not in files:raise ValueError('归档内缺少MANIFEST.json')
        with bundle.extractfile(files[manifest_name]) as stream:
            if files[manifest_name].size>10*1024*1024:raise ValueError('归档清单异常过大')
            manifest=json.load(stream)
        if not isinstance(manifest,dict) or not isinstance(manifest.get('version'),str) or not isinstance(manifest.get('files'),dict):
            raise ValueError('归档清单格式错误')
        expected=set()
        for name,value in manifest['files'].items():
            if (not isinstance(name,str) or not name or '\\' in name
                    or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts
                    or str(PurePosixPath(name))!=name or name=='MANIFEST.json'
                    or not isinstance(value,str) or not re.fullmatch('[0-9a-f]{64}',value)):
                raise ValueError('清单文件路径或指纹无效：'+str(name))
            expected.add(prefix+name)
        if set(files)!=(expected|{manifest_name}):raise ValueError('归档文件集合与包内清单不一致')
        for name,item in files.items():
            if name==manifest_name:continue
            with bundle.extractfile(item) as stream:
                if digest(stream)!=manifest['files'][name[len(prefix):]]:
                    raise ValueError('包内文件指纹不一致：'+name)
        if prefix+'VERSION' not in files:raise ValueError('归档缺少VERSION')
        with bundle.extractfile(files[prefix+'VERSION']) as stream:
            if stream.read().decode('utf-8').strip()!=manifest['version']:
                raise ValueError('包内VERSION与MANIFEST版本不一致')
        external=archive.with_name(stem+'.MANIFEST.json')
        if external.exists() and json.loads(external.read_text(encoding='utf-8'))!=manifest:
            raise ValueError('同名分版清单与包内MANIFEST不一致')
    return manifest['version'],len(manifest['files'])

if len(sys.argv)==2 and sys.argv[1] in ('-h','--help'):
    print('用法：./scripts/verify-package.sh <所选.tar.gz路径>\n校验对应.sha256、包内清单/文件，以及存在时的同名分版清单；不读取通用别名、不解压、不安装。')
    raise SystemExit(0)
if len(sys.argv)!=2:
    print('用法：./scripts/verify-package.sh <所选.tar.gz路径>',file=sys.stderr);raise SystemExit(2)
try:
    version,count=verify(Path(sys.argv[1]).expanduser())
except (OSError,ValueError,tarfile.TarError,EOFError) as error:
    print('校验失败：'+str(error),file=sys.stderr);raise SystemExit(1)
print(f'校验成功：版本{version}，压缩包摘要及包内{count}个文件指纹一致；未解压或修改数据。')
PY
