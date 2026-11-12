"""MCP-only byte transfer. No HTTP endpoints, browser UI or host filesystem access.

The host reads/writes its own files; this module only sees declared bytes.
Staging is bounded and explicit-close; published inputs/original results are
never deleted by closing a transfer. All persistent IDs survive service restart.
"""
from __future__ import annotations
import base64
import binascii
from contextlib import contextmanager
import fcntl
import hashlib
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import uuid
import zipfile

from openmc_runtime import Error, atomic_json, path_in_workspace, relative, workspace
from openmc_jobs import file_hash
from openmc_import import publish_directory

DEFAULTS={'max_file_bytes':5*1024**2,'max_package_bytes':5*1024**2,
          'max_staging_bytes':50*1024**2,'max_pending_sessions':100,'chunk_bytes':256*1024,
          'max_archive_files':1000,'preview_text_bytes':8192,'preview_image_bytes':256*1024,
          'min_free_disk_bytes':1024**3}
MAX_CHUNK=1024**2


def policy():
    values=dict(DEFAULTS);name=os.environ.get('MC_TRANSFER_CONFIG')
    if name:
        try:custom=json.loads(Path(name).read_text())
        except (OSError,ValueError) as exc:raise Error('MC_TRANSFER_CONFIG无法读取；现有OpenMC/服务配置不受影响') from exc
        if not isinstance(custom,dict) or set(custom)-set(values):raise Error('文件传输配置包含未知字段')
        values.update(custom)
    for key,value in values.items():
        if type(value) is not int or value<(0 if key=='min_free_disk_bytes' else 1):raise Error(key+'须为合法整数字节数/数量')
    if values['chunk_bytes']>MAX_CHUNK:raise Error('chunk_bytes不能超过MCP分块帧上限1 MiB；完整文件限制由max_file_bytes配置')
    if values['preview_text_bytes']>64*1024 or values['preview_image_bytes']>1024**2:raise Error('预览最多64 KiB文本或1 MiB图片；完整内容用下载函数')
    return values


def file_limits():
    return {**policy(),'transport':'MCP tool calls only','server_workspace':str(workspace()),
            'host_requirement':'宿主必须能读取/写入本地字节。远端工具不能读取C:\\等宿主路径；推荐在宿主执行examples/transfer_files.py，直接调用MCP而不把字节交给模型。',
            'next':'上传用upload_begin/chunk/status/finish，下载用prepare_download/download_status/chunk，预览用preview_file；关闭缓存不删除已发布输入或原始结果。'}


def _id(value):
    if not isinstance(value,str) or not re.fullmatch('[a-f0-9]{32}',value):raise Error('传输编号须为工具返回的32位小写编号')
    return value


def _sha(value):
    if not isinstance(value,str) or not re.fullmatch('[a-f0-9]{64}',value):raise Error('sha256须为完整文件的64位小写SHA256')
    return value


def _int(value,field,minimum=0):
    if type(value) is not int or value<minimum:raise Error(field+'须为大于等于'+str(minimum)+'的整数')
    return value


def _root():
    root=path_in_workspace('.mc-transfers',exists=False,internal=True);root.mkdir(exist_ok=True)
    return root


@contextmanager
def _quota_lock():
    root=_root()
    with (root/'quota.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX);yield root


def _dir(direction,identifier):
    _id(identifier)
    d=path_in_workspace('.mc-transfers/'+direction+'/'+identifier,internal=True)
    if not (d/'record.json').is_file():raise Error('传输记录不存在')
    return d


def _read(d):
    try:return json.loads((d/'record.json').read_text())
    except (OSError,ValueError) as exc:raise Error('传输记录不可读取，保留现场') from exc


@contextmanager
def _lock(direction,identifier):
    d=_dir(direction,identifier)
    with (d/'record.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX);yield d,_read(d)


def _check_size(size,limit,label='文件'):
    if size>limit:raise Error(f'{label}大小{size}字节超过本次传输上限{limit}字节；调整MC_TRANSFER_CONFIG后重试，或留在服务端分析。不限制服务端已有大文件计算。')


def _space(bytes_needed,p):
    if shutil.disk_usage(workspace()).free < bytes_needed+p['min_free_disk_bytes']:raise Error('传输暂存空间不足；未发布输入/结果，清理已确认不再需要的传输缓存或扩容后重试')


def _reserve(root,size,p):
    used=count=0
    for direction in ('uploads','downloads'):
        for f in (root/direction).glob('*/record.json'):
            r=_read(f.parent)
            if r['state'] in ('receiving','ready','preparing'):
                used+=r['size_bytes'];count+=1
    if used+size>p['max_staging_bytes'] or count>=p['max_pending_sessions']:
        raise Error('未完成传输/下载缓存已达配置限额；先用openmc_close_transfer关闭已知编号，原输入和计算结果不会被删除')
    _space(size,p)


def _info(r):
    return {k:r[k] for k in ('transfer_id','direction','state','filename','size_bytes','sha256','source_dir','file_path','files_count','files','files_truncated','archive_paths','source_files') if k in r}


def _upload_info(d,r):
    result=_info(r)
    if r['state']=='receiving':
        blob=d/'data.part';result.update(received_bytes=blob.stat().st_size,prefix_sha256=file_hash(blob))
    result['chunk_bytes']=policy()['chunk_bytes']
    return result


def upload_begin(filename: str,size_bytes: int,sha256: str,archive: bool=False,request_id: str|None=None):
    if not isinstance(filename,str) or not filename or len(filename)>255 or filename in ('.','..') or any(c in filename for c in '/\\\0\r\n'):
        raise Error('filename只填文件名，不是宿主路径，且不能含路径分隔符或控制字符')
    if any(ord(c)<32 for c in filename):raise Error('filename不能含控制字符')
    _int(size_bytes,'size_bytes');_sha(sha256)
    if type(archive) is not bool:raise Error('archive须为布尔值；true按ZIP模型包处理')
    p=policy();_check_size(size_bytes,p['max_file_bytes'])
    identifier=_id(request_id) if request_id else uuid.uuid4().hex
    with _quota_lock() as root:
        parent=root/'uploads';parent.mkdir(exist_ok=True);d=parent/identifier
        if d.exists():
            old=_read(d)
            if (old['filename'],old['size_bytes'],old['sha256'],old['archive'])!=(filename,size_bytes,sha256,archive):raise Error('request_id已用于不同文件，不覆盖原传输')
            return _upload_info(d,old)
        _reserve(root,size_bytes,p)
        r={'transfer_id':identifier,'direction':'upload','state':'receiving','filename':filename,
           'size_bytes':size_bytes,'sha256':sha256,'archive':archive}
        stage=Path(tempfile.mkdtemp(prefix='.begin-'+identifier+'-',dir=root))
        try:
            (stage/'data.part').touch();atomic_json(stage/'record.json',r)
            publish_directory(stage,d)
        finally:
            if stage.exists():shutil.rmtree(stage)
    return {**_upload_info(d,r),'next':'宿主读取文件字节，按chunk_bytes分块调用openmc_upload_chunk；网络结果未知先upload_status，不重新创建编号。'}


def upload_status(transfer_id: str):
    with _lock('uploads',transfer_id) as (d,r):return _upload_info(d,r)


def upload_chunk(transfer_id: str,offset: int,data_base64: str):
    _int(offset,'offset');p=policy()
    if not isinstance(data_base64,str) or len(data_base64)>4*((p['chunk_bytes']+2)//3):raise Error('分块超过chunk_bytes，或data_base64不是字符串')
    try:data=base64.b64decode(data_base64,validate=True)
    except (ValueError,binascii.Error) as exc:raise Error('data_base64不是有效Base64') from exc
    if not data:raise Error('空分块无需提交，空文件直接finish')
    _check_size(len(data),p['chunk_bytes'],'分块')
    with _lock('uploads',transfer_id) as (d,r):
        if r['state']!='receiving':raise Error('上传不是receiving状态；查upload_status，不追加或覆盖已发布文件')
        _check_size(r['size_bytes'],p['max_file_bytes'])
        blob=d/'data.part';current=blob.stat().st_size
        if offset+len(data)>r['size_bytes']:raise Error('实际接收字节超过声明文件长度，未写入')
        if offset>current:raise Error(f'offset不连续，服务端已接收{current}字节；先status再续传')
        if offset<current:
            with blob.open('rb') as f:f.seek(offset);previous=f.read(len(data))
            if offset+len(data)>current or previous!=data:raise Error('重复分块与已有字节不一致，未覆盖')
            return {'transfer_id':transfer_id,'received_bytes':current,'duplicate':True}
        _space(len(data),p)
        with blob.open('ab') as f:f.write(data);f.flush();os.fsync(f.fileno())
        return {'transfer_id':transfer_id,'received_bytes':offset+len(data),'duplicate':False}


def _extract(blob,target,p):
    total=0;seen=set();count=0
    try:
        with zipfile.ZipFile(blob) as archive:
            if len(archive.infolist())>p['max_archive_files']:raise Error('ZIP条目数量超过max_archive_files（目录也计入）')
            for item in archive.infolist():
                name=item.filename;pure=PurePosixPath(name)
                mode=(item.external_attr>>16)&0xffff
                if not name or '\\' in name or pure.is_absolute() or '..' in pure.parts or re.match(r'^[A-Za-z]:',name) or name in seen:
                    raise Error('ZIP包含重复/越界路径，未发布：'+name)
                seen.add(name)
                if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0,stat.S_IFREG,stat.S_IFDIR)) or item.flag_bits&1:
                    raise Error('ZIP包含链接、特殊文件或加密内容，未发布')
                dest=target.joinpath(*pure.parts)
                if item.is_dir():dest.mkdir(parents=True,exist_ok=True);continue
                count+=1
                if count>p['max_archive_files']:raise Error('ZIP文件数量超过max_archive_files')
                _check_size(item.file_size,p['max_file_bytes'],'ZIP内文件')
                _check_size(total+item.file_size,p['max_package_bytes'],'ZIP解压后总量')
                _space(item.file_size,p);dest.parent.mkdir(parents=True,exist_ok=True)
                written=0
                with archive.open(item) as src,dest.open('xb') as dst:
                    for chunk in iter(lambda:src.read(65536),b''):
                        written+=len(chunk);total+=len(chunk)
                        _check_size(written,p['max_file_bytes'],'ZIP内实际文件');_check_size(total,p['max_package_bytes'],'ZIP实际解压总量');dst.write(chunk)
                if written!=item.file_size:raise Error('ZIP文件实际长度与声明不符')
    except (zipfile.BadZipFile,RuntimeError,OSError,NotImplementedError) as exc:raise Error('ZIP无效或解压失败，原始上传保留，未发布') from exc
    if not count:raise Error('ZIP没有普通文件，未发布')


def _published_info(d,r,target,manifest):
    # Recover a lost finish acknowledgement without republishing/overwriting.
    actual={relative(x):{'sha256':file_hash(x),'bytes':x.stat().st_size} for x in target.rglob('*') if x.is_file() and not x.is_symlink()}
    if actual!=manifest or any(x.is_symlink() for x in target.rglob('*')):raise Error('已发布输入与上传时指纹不同，未覆盖；保留现场')
    r.update(state='complete',files_count=len(manifest),files=[{'path':name,**info} for name,info in list(manifest.items())[:20]],files_truncated=len(manifest)>20)
    r['source_dir']=relative(target)
    children=list(target.iterdir())
    if r['archive'] and len(children)==1 and children[0].is_dir():r['source_dir']=relative(children[0])
    if not r['archive']:r['file_path']=relative(target/r['filename'])
    atomic_json(d/'record.json',r);(d/'data.part').unlink(missing_ok=True)
    return {**_info(r),'next':'模型包用source_dir调用openmc_import_model，再预览/校核；STEP用file_path。仅传输完成，不代表物理输入正确。'}


def upload_finish(transfer_id: str):
    with _lock('uploads',transfer_id) as (d,r), _quota_lock():
        if r['state'] not in ('receiving','complete'):raise Error('上传已关闭，不能finish')
        p=policy();_check_size(r['size_bytes'],p['max_file_bytes'])
        target=path_in_workspace('inbox/'+transfer_id,exists=False)
        saved=d/'manifest.json'
        if target.exists():
            if not saved.is_file():raise Error('目标目录已有内容且无本次发布记录，未覆盖')
            return _published_info(d,r,target,json.loads(saved.read_text()))
        if r['state']=='complete':raise Error('已发布文件已被移走，未重新发布；查看原source_dir')
        blob=d/'data.part'
        if blob.stat().st_size!=r['size_bytes']:raise Error('上传尚未完整；先status再继续，不发布半个文件')
        if file_hash(blob)!=r['sha256']:raise Error('完整文件SHA256校验失败，未发布；关闭本传输后核对宿主原文件再上传')
        _space(r['size_bytes'],p)
        target.parent.mkdir(parents=True,exist_ok=True)
        stage=Path(tempfile.mkdtemp(prefix='.upload-',dir=target.parent))
        try:
            if r['archive']:_extract(blob,stage,p)
            else:shutil.copyfile(blob,stage/r['filename'])
            manifest={str(target/x.relative_to(stage)): {'sha256':file_hash(x),'bytes':x.stat().st_size} for x in sorted(stage.rglob('*')) if x.is_file()}
            manifest={str(Path(k).relative_to(workspace())):v for k,v in manifest.items()}
            atomic_json(saved,manifest)
            publish_directory(stage,target)
            return _published_info(d,r,target,manifest)
        finally:
            if stage.exists():shutil.rmtree(stage)


def _sources(kind,identifier):
    if kind=='upload':
        d=_dir('uploads',identifier);r=_read(d)
        if r['state']!='complete':raise Error('上传尚未发布')
        manifest=json.loads((d/'manifest.json').read_text())
        return path_in_workspace('inbox/'+identifier),{name:(path_in_workspace(name),value['sha256']) for name,value in manifest.items()}
    if kind not in ('job','scan'):raise Error('kind须为job、scan或upload')
    from openmc_artifacts import artifact_files
    target,record,paths=artifact_files(kind,identifier)
    result={}
    for path in paths:
        rel=relative(path)
        if not path.is_file() or path.is_symlink():continue
        path=path_in_workspace(rel,internal=True)
        expected=record.get('output_files',{}).get(path.relative_to(target/'work').as_posix()) if kind=='job' and path.is_relative_to(target/'work') else None
        result[rel]=(path,expected)
    return target,result


def _select(sources,name,transfer=True):
    if name not in sources:raise Error('文件不在该上传/作业产物清单中；从清单复制path，不传宿主路径或任意服务器文件')
    path,expected=sources[name]
    if transfer:_check_size(path.stat().st_size,policy()['max_file_bytes'])
    if expected and file_hash(path)!=expected:raise Error('原始文件与记录SHA256不同，停止取回并保留现场')
    return path


def _download_info(d,r,prefix_bytes=0,verify=True):
    _int(prefix_bytes,'prefix_bytes');p=policy();_check_size(r['size_bytes'],p['max_file_bytes'])
    if r['state']!='ready':raise Error('下载缓存已关闭；原始结果仍在服务器，可重新准备')
    blob=d/'data.bin'
    if not blob.is_file() or blob.stat().st_size!=r['size_bytes'] or (verify and file_hash(blob)!=r['sha256']):raise Error('下载快照缺失或改变，不能继续拼接；重新准备下载，原结果不改')
    if prefix_bytes>r['size_bytes']:raise Error('prefix_bytes超过文件总长度')
    h=hashlib.sha256()
    with blob.open('rb') as f:
        remaining=prefix_bytes
        while remaining:
            chunk=f.read(min(remaining,65536));h.update(chunk);remaining-=len(chunk)
    return {**_info(r),'chunk_bytes':p['chunk_bytes'],'prefix_bytes':prefix_bytes,'prefix_sha256':h.hexdigest()}


def prepare_download(kind: str,identifier: str,paths: list[str],request_id: str|None=None):
    if not isinstance(paths,list) or not paths or any(not isinstance(x,str) for x in paths) or len(set(paths))!=len(paths):raise Error('paths须为不重复的实际文件path列表')
    p=policy()
    if len(paths)>p['max_archive_files']:raise Error('请求文件数量超过max_archive_files')
    identifier=_id(identifier);transfer_id=_id(request_id) if request_id else uuid.uuid4().hex
    request={'kind':kind,'identifier':identifier,'paths':paths}
    with _quota_lock() as root:
        parent=root/'downloads';parent.mkdir(exist_ok=True);d=parent/transfer_id
        if d.exists():
            r=_read(d)
            if r['request']!=request:raise Error('request_id已用于不同下载，不覆盖')
            return _download_info(d,r)
        target,sources=_sources(kind,identifier);selected=list(paths)
        # Include local report dependencies; never follow arbitrary server paths/URLs.
        if kind in ('job','scan'):
            for name in list(selected):
                path=_select(sources,name);_check_size(path.stat().st_size,p['max_file_bytes'])
                if path.name=='report.md':
                    report_bytes=path.read_bytes()
                    sources[name]=(path,hashlib.sha256(report_bytes).hexdigest())
                    for link in re.findall(r'\]\(([^)]+)\)',report_bytes.decode('utf-8')):
                        if '://' in link or link.startswith('#'):continue
                        related=(path.parent/link)
                        if '..' in PurePosixPath(link).parts or not related.is_relative_to(target):raise Error('报告含越界引用，未打包')
                        rel=relative(related)
                        if rel not in sources:raise Error('报告引用缺失或不在产物清单：'+link)
                        if rel not in selected:selected.append(rel)
        size=0
        for name in selected:
            path=_select(sources,name);_check_size(path.stat().st_size,p['max_file_bytes']);size+=path.stat().st_size
        if len(selected)>p['max_archive_files']:raise Error('报告依赖文件数量超过max_archive_files')
        _check_size(size,p['max_package_bytes'],'所选文件总量');_reserve(root,size,p);_space(2*size+1024**2,p)
        stage=Path(tempfile.mkdtemp(prefix='.download-',dir=parent))
        try:
            copied=stage/'files';copied.mkdir();manifest={}
            for name in selected:
                path=_select(sources,name);rel=path.relative_to(target).as_posix();dest=copied/rel;dest.parent.mkdir(parents=True,exist_ok=True)
                before=sources[name][1] or file_hash(path);shutil.copyfile(path,dest)
                if file_hash(dest)!=before or file_hash(path)!=before:raise Error('复制期间源文件改变，未发布下载')
                manifest[rel]={'source_path':name,'sha256':before,'bytes':dest.stat().st_size}
            bundle=len(manifest)>1
            blob=stage/'data.bin'
            if bundle:
                with zipfile.ZipFile(blob,'w',compression=zipfile.ZIP_STORED) as z:
                    for rel in manifest:z.write(copied/rel,arcname=rel)
                filename='mc-'+kind+'-'+identifier+'.zip'
            else:
                rel=next(iter(manifest));shutil.move(copied/rel,blob);filename=Path(rel).name
            _check_size(blob.stat().st_size,p['max_file_bytes'],'下载包');_reserve(root,blob.stat().st_size,p);shutil.rmtree(copied)
            r={'transfer_id':transfer_id,'direction':'download','state':'ready','filename':filename,'size_bytes':blob.stat().st_size,
               'sha256':file_hash(blob),'request':request,'source_files':manifest,'archive_paths':list(manifest) if bundle else []}
            atomic_json(stage/'record.json',r);publish_directory(stage,d)
            return _download_info(d,r)
        finally:
            if stage.exists():shutil.rmtree(stage)


def download_status(transfer_id: str,prefix_bytes: int=0):
    with _lock('downloads',transfer_id) as (d,r):return _download_info(d,r,prefix_bytes)


def download_chunk(transfer_id: str,offset: int,length: int|None=None):
    _int(offset,'offset');p=policy();length=p['chunk_bytes'] if length is None else _int(length,'length',1)
    _check_size(length,p['chunk_bytes'],'分块')
    with _lock('downloads',transfer_id) as (d,r):
        _download_info(d,r,verify=False)
        if offset>r['size_bytes']:raise Error('offset超过文件总长度')
        with (d/'data.bin').open('rb') as f:f.seek(offset);data=f.read(length)
        return {'transfer_id':transfer_id,'offset':offset,'bytes':len(data),'data_base64':base64.b64encode(data).decode(),
                'chunk_sha256':hashlib.sha256(data).hexdigest(),'eof':offset+len(data)==r['size_bytes']}


def close_transfer(direction: str,transfer_id: str):
    names={'upload':'uploads','download':'downloads'}
    if direction not in names:raise Error('direction须为upload或download')
    with _lock(names[direction],transfer_id) as (d,r):
        if r['state']=='complete':return {**_info(r),'changed':False,'note':'已发布输入保留，不删除；关闭只清理未完成上传或下载缓存。'}
        if r['state']=='closed':return {**_info(r),'changed':False}
        for name in ('data.part','data.bin'):(d/name).unlink(missing_ok=True)
        r['state']='closed';atomic_json(d/'record.json',r)
        return {**_info(r),'changed':True,'note':'仅清理本传输缓存，原输入/作业结果保留。'}


def preview_file(kind: str,identifier: str,path: str,offset: int=0,max_bytes: int=8192):
    _int(offset,'offset');_int(max_bytes,'max_bytes',1);p=policy();_,sources=_sources(kind,identifier);source=_select(sources,path,transfer=False)
    size=source.stat().st_size;mime=mimetypes.guess_type(source.name)[0] or 'application/octet-stream'
    result={'path':path,'bytes':size,'mime_type':mime,'download_allowed':size<=p['max_file_bytes'],
            'note':'预览不代表完整内容；大文件仍可在服务端分析，完整取回受传输限额约束。'}
    if source.suffix.lower() in {'.png','.jpg','.jpeg'} and size<=p['preview_image_bytes']:
        raw=source.read_bytes()
        if raw.startswith(b'\x89PNG\r\n\x1a\n') or raw.startswith(b'\xff\xd8\xff'):
            return {**result,'preview_type':'image','image_base64':base64.b64encode(raw).decode()}
    if source.suffix.lower() in {'.xml','.json','.csv','.md','.txt','.log','.py','.rst','.yaml','.yml'}:
        if offset>size:raise Error('offset超过文件总长度')
        count=min(max_bytes,p['preview_text_bytes'])
        with source.open('rb') as f:f.seek(offset);data=f.read(count)
        return {**result,'preview_type':'text','text':data.decode('utf-8',errors='replace'),'offset':offset,
                'next_offset':offset+len(data),'truncated':offset+len(data)<size}
    return {**result,'preview_type':'metadata','note':'该格式或图片大小不适合内联预览；在服务端用结果工具分析，或按需准备下载。'}


def _schema(fields,required=()):return {'type':'object','properties':fields,'required':list(required),'additionalProperties':False}
S={'type':'string','minLength':1};I={'type':'integer','minimum':0}
ID={**S,'pattern':'^[a-f0-9]{32}$','description':'由传输工具返回的transfer_id，服务重启后仍用同一编号恢复；不是本地文件路径。'}
KIND={'type':'string','enum':['job','scan','upload'],'description':'job/scan为计算/扫描产物，identifier取相应job_id/scan_id；upload取完成上传的transfer_id。'}
IDENT={**ID,'description':'kind所对应的job_id、scan_id或已完成上传的transfer_id。'}
REQUEST={**ID,'description':'可选由宿主生成并保存的32位小写随机幂等键；请求结果未知时重用同一个值，不重复预留或生成文件包。'}
DEFS=[
 ('openmc_file_limits','查询MCP文件传输与预览限额。默认完整文件5 MiB，管理员可配置；不限制服务端大文件计算。不提供网页或额外HTTP上传下载接口。',file_limits,{},[],True),
 ('openmc_upload_begin','预留上传。宿主读取本地文件，提交文件名/总字节数/完整SHA256；服务端无法读取宿主C:路径。大块字节请用宿主MCP传输程序发送，不让模型逐块复述。',upload_begin,
  {'filename':{**S,'description':'仅文件名，例如model.zip或component.step，不是宿主路径。'},'size_bytes':{**I,'description':'完整原始文件字节数，不能靠小分块绕过完整文件上限。'},'sha256':{**S,'pattern':'^[a-f0-9]{64}$','description':'宿主对完整文件计算的SHA256，用于最终校验。'},'archive':{'type':'boolean','description':'true表示ZIP模型包，finish受控解压并核对实际解压总量；false保留单文件。'},'request_id':REQUEST},['filename','size_bytes','sha256'],False),
 ('openmc_upload_status','读取上传实际已收字节数和已有前缀SHA256；断线先查询，用于核对源文件并续传。不会发布或执行文件。',upload_status,{'transfer_id':ID},['transfer_id'],True),
 ('openmc_upload_chunk','接收宿主读取的Base64分块；offset按实际字节，不是Base64字符数。重复相同分块幂等，冲突或超总长拒绝；仅写临时文件。',upload_chunk,{'transfer_id':ID,'offset':{**I,'description':'分块原始字节起始位置，取upload_status.received_bytes或上一块回执。'},'data_base64':{'type':'string','maxLength':4*((MAX_CHUNK+2)//3),'description':'由宿主传输程序编码的文件字节，不是文件路径；单块上限先查openmc_file_limits。不要把文件内容放进模型上下文。'}},['transfer_id','offset','data_base64'],False),
 ('openmc_upload_finish','完整长度/SHA256通过后才发布inbox输入；ZIP禁止越界/链接并检查解压总量。返回source_dir或file_path，接现有导入/STEP工具。不执行上传程序。',upload_finish,{'transfer_id':ID},['transfer_id'],False),
 ('openmc_prepare_download','按清单paths创建一致的下载快照；报告Markdown自动包含已登记本地图片/数据引用。受完整文件/整包限额约束；原始大文件默认留服务端。',prepare_download,
  {'kind':KIND,'identifier':IDENT,'paths':{'type':'array','minItems':1,'uniqueItems':True,'items':{**S,'description':'从list_artifacts.artifacts[].path或完成上传files[].path复制的工作区相对路径，不是任意服务器路径。'},'description':'要取回的明确文件path列表；多个文件为ZIP，保持报告相对引用。'},'request_id':REQUEST},['kind','identifier','paths'],False),
 ('openmc_download_status','检查下载快照、总长和SHA256，可按本地.part长度返回对应前缀SHA256，以安全恢复下载。',download_status,{'transfer_id':ID,'prefix_bytes':{**I,'description':'可选本地已下载.part的实际字节数，用返回prefix_sha256核对后才追加。'}},['transfer_id'],True),
 ('openmc_download_chunk','返回固定快照的Base64字节和分块SHA256；由宿主传输程序直接写本地文件，不把大块响应交给模型。',download_chunk,
  {'transfer_id':ID,'offset':{**I,'description':'本地已核对前缀后的字节偏移。'},'length':{'type':'integer','minimum':1,'description':'本块原始字节数，默认当前chunk_bytes，不能超过配置分块上限。'}},['transfer_id','offset'],True),
 ('openmc_close_transfer','显式关闭未完成上传或下载缓存，释放暂存空间；保留已发布输入、原始结果及传输记录。重复关闭不会删除其他文件。',close_transfer,
  {'direction':{'type':'string','enum':['upload','download'],'description':'关闭上传临时文件还是下载快照。'},'transfer_id':ID},['direction','transfer_id'],False),
 ('openmc_preview_file','对已上传输入或登记产物做有限文本/图片预览；大二进制只返回元信息，不因预览而下载整个文件。宿主是否展示图片取决于客户端。',preview_file,
  {'kind':KIND,'identifier':IDENT,'path':{**S,'description':'从实际文件/产物清单复制path。'},'offset':{**I,'description':'文本预览字节偏移，省略从0开始。'},'max_bytes':{'type':'integer','minimum':1,'description':'文本请求字节数，实际受preview_text_bytes约束，默认8192。'}},['kind','identifier','path'],True)
]
TRANSFER_TOOLS=[{'name':name,'description':description,'function':function,'inputSchema':_schema(fields,required),
                 'outputSchema':{'type':'object'},'annotations':{'readOnlyHint':read_only,'destructiveHint':name=='openmc_close_transfer',
                 'idempotentHint':name not in ('openmc_upload_begin','openmc_prepare_download'),'openWorldHint':False}}
                for name,description,function,fields,required,read_only in DEFS]
