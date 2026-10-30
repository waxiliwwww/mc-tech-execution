#!/usr/bin/env python3
"""Initial setup: verify and unpack the pinned official multi-temperature library."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import sys
import tarfile
import uuid
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'mcp'))
from nuclear_data import data_digest,fetch_file
from openmc_runtime import atomic_json


def install(archive: Path | None = None):
    descriptor=json.loads((ROOT/'config/official-nuclear-data.json').read_text())
    target=ROOT/descriptor['directory'];record=target/'manifest.json'
    if target.exists():
        if not record.is_file():raise ValueError('已有数据目录未完成，不覆盖：'+str(target))
        value=json.loads(record.read_text())
        if value.get('archive_sha256')!=descriptor['archive_sha256']:
            raise ValueError('已有数据版本不符，不覆盖：'+str(target))
        print('官方数据已初始化：'+str(target),flush=True);return value
    if archive is None:
        archive=ROOT/'.runtime/downloads/official-endfb71.tar.xz';archive.parent.mkdir(parents=True,exist_ok=True)
        if not archive.exists():
            fetch_file(descriptor['url'],archive,descriptor['archive_bytes'],
                       expected_sha256=descriptor['archive_sha256'],max_time_seconds=900)
    if archive.stat().st_size!=descriptor['archive_bytes'] or data_digest(archive)!=descriptor['archive_sha256']:
        raise ValueError('官方数据归档大小或SHA256不匹配；未解压')
    target.parent.mkdir(parents=True,exist_ok=True)
    pending=target.with_name(target.name+'.pending-'+uuid.uuid4().hex[:8]);pending.mkdir()
    print('校验通过，解压至：'+str(pending),flush=True)
    hashes={};total=0
    with tarfile.open(archive,'r|xz') as source:
        for member in source:
            path=Path(member.name)
            if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0]!=descriptor['archive_prefix']:
                raise ValueError('归档路径不安全：'+member.name)
            if member.isdir():continue
            if not member.isfile():raise ValueError('归档含链接/特殊文件：'+member.name)
            total+=member.size
            if total>16*1024**3:raise ValueError('解压数据超出固定包合理大小，停止并保留现场')
            name=Path(*path.parts[1:]);destination=pending/name;destination.parent.mkdir(parents=True,exist_ok=True)
            with source.extractfile(member) as input,destination.open('wb') as output:shutil.copyfileobj(input,output,1024*1024)
            hashes[name.as_posix()]=data_digest(destination)
    index=pending/'cross_sections.xml';shutil.copy2(index,pending/'cross_sections.original.xml')
    tree=ET.parse(index);root=tree.getroot()
    for directory in list(root.findall('directory')):root.remove(directory)
    for entry in root.findall('library'):
        old=Path(entry.attrib['path']);name=old.as_posix()
        if old.is_absolute() or '..' in old.parts or name not in hashes:
            matches=[key for key in hashes if Path(key).name==old.name]
            if len(matches)!=1:raise ValueError('无法唯一定位官方数据索引文件：'+str(old))
            name=matches[0]
        entry.set('path',name)
    tree.write(index,encoding='utf-8',xml_declaration=True)
    hashes['cross_sections.original.xml']=hashes['cross_sections.xml'];hashes['cross_sections.xml']=data_digest(index)
    value={'name':descriptor['name'],'archive_sha256':descriptor['archive_sha256'],
           'source_url':descriptor['url'],'files':hashes,'unpacked_bytes':total}
    expected=ROOT/'config/data-index/official-endfb-vii.1-files.json'
    if expected.exists() and json.loads(expected.read_text())['files']!=hashes:
        raise ValueError('解压文件与发行清单不一致，保留现场：'+str(pending))
    atomic_json(pending/'manifest.json',value);pending.rename(target)
    print('官方多温度数据准备完成：'+str(target),flush=True)
    return value


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--archive',type=Path,help='可选已有官方归档，仍须通过固定SHA256校验')
    args=parser.parse_args()
    try:install(args.archive)
    except (ValueError,OSError) as error:raise SystemExit('官方数据初始化失败：'+str(error))
