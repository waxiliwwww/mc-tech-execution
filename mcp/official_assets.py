"""Serve fixed official-tutorial inputs locally after initial environment setup."""
from __future__ import annotations
from email.message import Message
import io
import json
from pathlib import Path
import shutil
from openmc_runtime import ROOT,Error
from nuclear_data import data_digest,fetch_file


def prepare_assets(cache: str | None = None) -> dict:
    items=json.loads((ROOT/'config/official-assets.json').read_text())['sources']
    directory=ROOT/'.runtime/official-assets';directory.mkdir(parents=True,exist_ok=True)
    for item in items:
        target=directory/item['id']
        if target.exists():
            if target.is_symlink() or data_digest(target)!=item['sha256']:
                raise Error('已有官方输入与固定SHA256不一致，未覆盖：'+item['id'])
            continue
        source=Path(cache)/item['id'] if cache else None
        if source and source.is_file() and data_digest(source)==item['sha256']:
            shutil.copy2(source,target);continue
        print('准备官方例题输入：'+item['id'],flush=True)
        fetch_file(item['download_url'],target,item['bytes'],expected_sha256=item['sha256'],max_time_seconds=900)
    return {'directory':str(directory),'files':len(items),
            'note':'仅初次配置联网准备固定输入；正常Notebook读取本地已校验文件。'}


class CachedResponse(io.BufferedReader):
    def __init__(self,path: Path,url: str):
        super().__init__(path.open('rb'));self.status=200;self.code=200;self.url=url
        self.headers=Message();self.headers['Content-Length']=str(path.stat().st_size)
        self.headers['Content-Type']='application/octet-stream'
    def info(self):return self.headers
    def geturl(self):return self.url
    def getcode(self):return self.code
    def getheader(self,name,default=None):return self.headers.get(name,default)


def local_response(url: str):
    catalogue=json.loads((ROOT/'config/official-assets.json').read_text())
    item=next((x for x in catalogue['sources'] if url in
               (x['request_url'],x.get('download_url'))),None)
    if item is None:
        raise Error('官方例题输入尚未收录到固定本地缓存，未发起网络请求：'+url)
    path=ROOT/'.runtime/official-assets'/item['id']
    if path.is_symlink() or not path.is_file() or path.stat().st_size!=item['bytes'] or data_digest(path)!=item['sha256']:
        raise Error('官方例题输入未初始化或已改变；先运行初次环境准备：'+item['id'])
    return CachedResponse(path,url)
