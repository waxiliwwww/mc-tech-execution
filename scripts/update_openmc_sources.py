#!/usr/bin/env python3
"""Maintainer-only refresh of the pinned, MIT-licensed official OpenMC sources."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.16.0'
COMMIT = '617d35a5063c57796b43428bc401e627d2011046'
BASE = f'https://raw.githubusercontent.com/openmc-dev/openmc/{COMMIT}/'
DOCUMENTS = {
    'usersguide/settings.rst': '执行设置：particles、batches、inactive及源项',
    'usersguide/materials.rst': '材料、密度和核素',
    'usersguide/geometry.rst': '几何与边界条件',
    'usersguide/tallies.rst': '计分、过滤器与分数',
    'usersguide/troubleshoot.rst': '运行故障排查',
    'usersguide/processing.rst': 'StatePoint与结果处理',
    'usersguide/scripts.rst': '求解器命令与几何调试',
    'io_formats/settings.rst': 'settings.xml字段规范',
}


def fetch(path):
    with urlopen(BASE + path, timeout=60) as response:return response.read()


def save(directory, name, data, path, title):
    target = directory / name; target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return {'file': name, 'title': title, 'version': VERSION, 'sha256': hashlib.sha256(data).hexdigest(),
            'source_url': f'https://github.com/openmc-dev/openmc/blob/{COMMIT}/{path}',
            'raw_url': BASE + path, 'license': 'MIT', 'content_kind': 'official_original'}


def main():
    knowledge = ROOT / 'knowledge' / ('openmc-' + VERSION)
    example = ROOT / 'vendor/openmc-examples' / VERSION
    for directory in (knowledge, example):directory.mkdir(parents=True, exist_ok=True)
    license = fetch('LICENSE')
    entries = [save(knowledge, path, fetch('docs/source/' + path), 'docs/source/' + path, title)
               for path, title in DOCUMENTS.items()]
    provenance = {'upstream': 'openmc-dev/openmc', 'version': VERSION, 'commit': COMMIT,
                  'retrieved_at': datetime.now(timezone.utc).isoformat(), 'license': 'MIT',
                  'note': '原始RST精选；跨文件引用/include/autodoc未展开，不是完整离线网站或官方中文译文。'}
    knowledge.joinpath('LICENSE').write_bytes(license)
    knowledge.joinpath('manifest.json').write_text(json.dumps({**provenance, 'documents': entries}, ensure_ascii=False, indent=2)+'\n')
    source = 'examples/pincell_multigroup/build_xml.py'
    script = save(example, 'pincell_multigroup.py', fetch(source), source, 'Official multigroup pin-cell (C5G7 data)')
    example.joinpath('LICENSE').write_bytes(license)
    example.joinpath('manifest.json').write_text(json.dumps({**provenance, 'files': [script]}, ensure_ascii=False, indent=2)+'\n')
    print(f'已保存{len(entries)}份官方原文和1份官方例题源码，版本{VERSION}，日常运行无需联网。')


if __name__ == '__main__':main()
