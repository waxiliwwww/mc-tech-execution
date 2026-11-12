#!/usr/bin/env python3
"""Host-side file bytes over standard MCP tools. No model and no file HTTP API.
Run this on the computer that owns the source/destination file. A remote MCP
server cannot read a host path. Receipts persist request IDs across reconnects.
"""
from contextlib import contextmanager, asynccontextmanager
import argparse
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

from openmc_demo import ToolCallError, error_text


ROOT=Path(__file__).resolve().parents[1]

@asynccontextmanager
async def client(url):
    """Portable client: do not import the Linux server to check definitions."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    token=os.environ.get('MCP_AUTH_TOKEN')
    headers={'Authorization':'Bearer '+token} if token else None
    async with streamablehttp_client(url,headers=headers) as (read,write,_):
        async with ClientSession(read,write) as session:
            hello=await session.initialize();expected=(ROOT/'VERSION').read_text().strip()
            if hello.serverInfo.version!=expected:raise ValueError('MCP服务版本'+hello.serverInfo.version+'与宿主工具包'+expected+'不同；先确认正确实例，不传文件')
            contract=json.loads((ROOT/'config/file-transfer-tools.json').read_text())
            found={};cursor=None
            while True:
                listing=await session.list_tools(cursor=cursor)
                found.update({t.name:t for t in listing.tools});cursor=listing.nextCursor
                if not cursor:break
            for name,definition in contract.items():
                if name not in found or any(getattr(found[name],key)!=value for key,value in definition.items()):
                    raise ValueError('MCP文件工具定义不一致：'+name+'；刷新发现/核对服务后再传输')
            async def call(name,arguments):
                result=await session.call_tool(name,arguments)
                if result.isError:raise ToolCallError('; '.join(c.text for c in result.content if c.type=='text'))
                return result.structuredContent or json.loads(result.content[0].text)
            yield call


def digest(path, length=None):
    result=hashlib.sha256()
    with Path(path).open('rb') as stream:
        remaining=length
        while remaining is None or remaining>0:
            block=stream.read(65536 if remaining is None else min(65536,remaining))
            if not block:break
            result.update(block)
            if remaining is not None:remaining-=len(block)
    return result.hexdigest()


def save(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp-'+uuid.uuid4().hex)
    try:
        with temp.open('x',encoding='utf-8') as f:
            json.dump(value,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
        os.replace(temp,path)
    finally:temp.unlink(missing_ok=True)


def receipt(path, identity):
    path=Path(path)
    if path.exists():
        stored=json.loads(path.read_text())
        if stored['request']!=identity:raise ValueError('回执属于不同文件、目标或服务器；保留原回执，本次换一个 --receipt')
        return stored
    stored={'request':identity,'request_id':uuid.uuid4().hex}
    save(path,stored)  # Save BEFORE the first remote call, including begin/prepare.
    return stored


async def upload(call,args):
    source=Path(args.file).resolve()
    if not source.is_file():raise ValueError('宿主源文件不存在：'+str(source))
    identity={'direction':'upload','url':args.mcp_url,'file':str(source),
              'filename':source.name,'size_bytes':source.stat().st_size,'sha256':digest(source),'archive':args.archive}
    rp=Path(args.receipt or str(source)+'.mc-upload.json');r=receipt(rp,identity)
    status=await call('openmc_upload_begin',{k:identity[k] for k in ('filename','size_bytes','sha256','archive')}|{'request_id':r['request_id']})
    r['transfer_id']=status['transfer_id'];save(rp,r)
    if status['state']=='closed':raise ValueError('上传已关闭；保留回执，使用新 --receipt 开始')
    if status['state']!='complete':
        offset=status['received_bytes']
        if digest(source,offset)!=status['prefix_sha256']:raise ValueError('服务端已有前缀与宿主源文件不一致，停止续传')
        with source.open('rb') as f:
            f.seek(offset)
            while block:=f.read(status['chunk_bytes']):
                await call('openmc_upload_chunk',{'transfer_id':r['transfer_id'],'offset':offset,'data_base64':base64.b64encode(block).decode()})
                offset+=len(block)
        if digest(source)!=identity['sha256']:raise ValueError('上传时源文件发生变化，未发布；保留回执以便关闭缓存')
    result=await call('openmc_upload_finish',{'transfer_id':r['transfer_id']})
    r['result']=result;save(rp,r)
    return {'message':'上传完成，长度与SHA256校验通过','receipt':str(rp.resolve()),**result}


async def download(call,args):
    target=Path(args.output).resolve();target.parent.mkdir(parents=True,exist_ok=True)
    rp=Path(args.receipt or str(target)+'.mc-download.json')
    identity={'direction':'download','url':args.mcp_url,'kind':args.kind,'identifier':args.identifier,'paths':args.paths,'output':str(target)}
    r=receipt(rp,identity)
    # If publication succeeded but acknowledgement/close failed, finish idempotently.
    if target.exists():
        result=r.get('result',{})
        if not target.is_file() or not result or target.stat().st_size!=result['size_bytes'] or digest(target)!=result['sha256']:
            raise ValueError('目标已有文件且不能证明属于本次下载，未覆盖；选择新的 --output')
        await call('openmc_close_transfer',{'direction':'download','transfer_id':r['transfer_id']})
        return {'message':'本次文件已完整保存，重复调用未覆盖','file':str(target),'sha256':result['sha256']}
    status=await call('openmc_prepare_download',{'kind':args.kind,'identifier':args.identifier,'paths':args.paths,'request_id':r['request_id']})
    r.update(transfer_id=status['transfer_id'],result=status);save(rp,r)
    part=target.with_name(target.name+'.part')
    offset=part.stat().st_size if part.exists() else 0
    checked=await call('openmc_download_status',{'transfer_id':r['transfer_id'],'prefix_bytes':offset})
    if part.exists() and digest(part)!=checked['prefix_sha256']:raise ValueError('本地.part与下载快照前缀不同，未追加；保留现场，选择新的输出文件和回执')
    with part.open('ab') as f:
        while offset<status['size_bytes']:
            block=await call('openmc_download_chunk',{'transfer_id':r['transfer_id'],'offset':offset,'length':status['chunk_bytes']})
            data=base64.b64decode(block['data_base64'],validate=True)
            if block['offset']!=offset or block['bytes']!=len(data) or not data or hashlib.sha256(data).hexdigest()!=block['chunk_sha256']:
                raise ValueError('分块长度、偏移或SHA256不一致，未追加此块')
            if offset+len(data)>status['size_bytes']:raise ValueError('下载超过声明长度，停止')
            f.write(data);f.flush();os.fsync(f.fileno());offset+=len(data)
    if part.stat().st_size!=status['size_bytes'] or digest(part)!=status['sha256']:raise ValueError('完整下载SHA256不符，保留.part，未发布目标文件')
    # A hard link publishes atomically without replacing an existing user file.
    try:os.link(part,target)
    except OSError as exc:raise ValueError('无法无覆盖发布文件；目标需支持硬链接（如本地NTFS/ext4），完整.part已保留。换本地目录后重试；不覆盖已有文件。') from exc
    part.unlink()
    await call('openmc_close_transfer',{'direction':'download','transfer_id':r['transfer_id']})
    return {'message':'下载完成，分块及完整SHA256校验通过','file':str(target),'bytes':status['size_bytes'],
            'sha256':status['sha256'],'receipt':str(rp.resolve()),'archive_paths':status.get('archive_paths',[])}


async def run(args):
    # Retry only transport failures: re-open the session, resume using saved IDs.
    # Explicit business errors and local integrity errors stop without replay.
    for attempt in range(3):
        try:
            async with client(args.mcp_url) as call:
                if args.command=='upload':return await upload(call,args)
                if args.command=='download':return await download(call,args)
                if args.command=='preview':return await call('openmc_preview_file',{'kind':args.kind,'identifier':args.identifier,'path':args.path})
                return await call('openmc_close_transfer',{'direction':args.direction,'transfer_id':args.transfer_id})
        except (ToolCallError,ValueError,OSError):raise
        except Exception as exc:
            def terminal(error):
                return isinstance(error,(ToolCallError,ValueError,OSError)) or any(terminal(e) for e in getattr(error,'exceptions',()))
            if terminal(exc) or attempt==2:raise
            await asyncio.sleep(0.5*(attempt+1))


@contextmanager
def local_lock(args):
    if args.command not in ('upload','download'):
        yield;return
    base=Path(args.receipt or (str(Path(args.file).resolve())+'.mc-upload.json' if args.command=='upload' else str(Path(args.output).resolve())+'.mc-download.json'))
    base.parent.mkdir(parents=True,exist_ok=True)
    # Keep the tiny lock file: unlinking it while another process waits would
    # allow two locks on different inodes. OS releases the lock after a crash.
    with Path(str(base)+'.lock').open('a+b') as lock:
        if os.name=='nt':
            import msvcrt
            lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
            try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            except OSError as exc:raise ValueError('同一回执已有传输进程，请等待该进程结束') from exc
        else:
            import fcntl
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError as exc:raise ValueError('同一回执已有传输进程，请等待该进程结束') from exc
        try:yield
        finally:
            if os.name=='nt':lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(lock,fcntl.LOCK_UN)


def main():
    p=argparse.ArgumentParser(description='宿主侧标准MCP文件交接；不调用模型。断线后原命令重跑，保留回执和.part。')
    p.add_argument('--mcp-url',required=True,help='实际MCP地址，例如 http://127.0.0.1:8770/mcp；鉴权用MCP_AUTH_TOKEN环境变量')
    sub=p.add_subparsers(dest='command',required=True)
    u=sub.add_parser('upload');u.add_argument('file');u.add_argument('--archive',action='store_true',help='按ZIP模型包解压');u.add_argument('--receipt')
    d=sub.add_parser('download');d.add_argument('kind',choices=['job','scan','upload']);d.add_argument('identifier');d.add_argument('paths',nargs='+');d.add_argument('--output',required=True);d.add_argument('--receipt')
    v=sub.add_parser('preview');v.add_argument('kind',choices=['job','scan','upload']);v.add_argument('identifier');v.add_argument('path')
    c=sub.add_parser('close');c.add_argument('direction',choices=['upload','download']);c.add_argument('transfer_id')
    args=p.parse_args()
    try:
        with local_lock(args):print(json.dumps(asyncio.run(run(args)),ensure_ascii=False,indent=2))
    except Exception as exc:
        print('文件交接未完成：'+error_text(exc)+'\n保留回执和.part；连接恢复后重跑同一命令。完整性或业务错误先按原因处理。',file=sys.stderr);return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
