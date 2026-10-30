"""Fetch pinned upstream notebooks during setup without relicensing or republishing them."""
from __future__ import annotations
import json
from pathlib import Path
import shutil
from nuclear_data import data_digest, fetch_file
from openmc_runtime import ROOT, Error, atomic_json


def notebook_index():
    return json.loads((ROOT / 'vendor/official-notebooks/index.json').read_text())


def notebook_root():
    return ROOT / '.runtime/official-notebooks' / notebook_index()['revision']


def prepare_notebook_sources(cache: str | None = None) -> dict:
    index=notebook_index();root=notebook_root();root.mkdir(parents=True,exist_ok=True)
    for entry in index['files']:
        destination=root/entry['name']
        destination.parent.mkdir(parents=True,exist_ok=True)
        if destination.is_file() and data_digest(destination,'sha1',True)==entry['git_blob_sha']:continue
        source=Path(cache)/entry['name'] if cache else None
        if source and source.is_file() and data_digest(source,'sha1',True)==entry['git_blob_sha']:
            shutil.copy2(source,destination);continue
        url=f"https://raw.githubusercontent.com/{index['repository']}/{index['revision']}/{entry['name']}?download=1"
        fetch_file(url,destination,entry['source_bytes'],expected_git_sha=entry['git_blob_sha'])
    atomic_json(root/'manifest.json',index)
    return {'source_revision':index['revision'],'directory':str(root),'files':len(index['files']),
            'note':'固定官方原文下载至本包运行目录；未将收录/下载当作例题执行通过。'}


def verify_notebook_sources(name: str) -> Path:
    index=notebook_index();root=notebook_root()
    for entry in index['files']:
        if entry['name'] in (name,'c5g7.h5','chain_simple.xml') or entry['name'].startswith('images/'):
            path=root/entry['name']
            if path.is_symlink() or not path.is_file() or data_digest(path,'sha1',True)!=entry['git_blob_sha']:
                raise Error('官方Notebook原文/配套输入未就绪或哈希不符；先运行./setup-example-sources.sh：'+entry['name'])
    return root
