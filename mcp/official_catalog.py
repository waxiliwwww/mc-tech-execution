"""Pinned official corpus; collection, adapter availability and execution are distinct."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from openmc_runtime import ROOT, Error


def load_catalog() -> dict:
    value = json.loads((ROOT / 'config/official-examples.json').read_text(encoding='utf-8'))
    if value.get('schema_version') != 1:
        raise Error('官方例题清单版本不支持')
    ids = [e['id'] for e in value['examples']]
    if len(ids) != len(set(ids)):
        raise Error('官方例题ID重复，不能继续执行')
    return value


def definition(example_id: str) -> dict:
    for item in load_catalog()['examples']:
        if item['id'] == example_id:
            return item
    raise Error('未找到官方例题；先列出官方例题，使用返回的example_id')


def steps(item: dict) -> list[dict]:
    kind = item['workflow']
    if kind == 'notebook':
        return [{'id': 'execute', 'name': '按原顺序执行官方Notebook并保存实际输出', 'implemented': True}]
    if kind in ('depletion', 'depletion_restart', 'pulsed_source'):
        actions=[{'id': 'execute', 'name': '执行官方计算及后处理', 'implemented': True}]
        if kind=='depletion_restart':
            actions.append({'id':'restart','name':'由前一步燃耗结果继续官方重启计算','implemented':True})
        return actions
    result = []
    if kind == 'compiled_source':
        result.append({'id': 'compile', 'name': '编译官方自定义源', 'implemented': True})
    result.extend([{'id': 'prepare', 'name': '生成模型输入', 'implemented': kind in ('transport', 'random_ray', 'compiled_source')},
                   {'id': 'data', 'name': '准备模型所需核数据', 'implemented': True},
                   {'id': 'compute', 'name': '执行OpenMC计算', 'implemented': kind in ('transport', 'random_ray', 'compiled_source')}])
    if item['id'] in ('source-pincell','source-custom-source','source-parameterized-custom-source'):
        result.append({'id':'postprocess','name':'执行官方结果绘图脚本','implemented':True})
    return result


def describe(example_id: str) -> dict:
    item = definition(example_id)
    pipeline=steps(item)
    status=('deferred_by_user' if item.get('scope')=='deferred' else
            'adapter_available_pending_verification' if all(s['implemented'] for s in pipeline) else 'pending_adapter')
    return {**item, 'implementation_status':status, 'example_id': item['id'], 'steps': pipeline,
            'execution_status': 'not_run', 'note': '已收录不等于已经运行；按步骤执行后查询实际终态。'}


def list_official_examples(group: str = 'all') -> dict:
    value = load_catalog()
    if group not in ('all', 'source', 'api', 'notebook'):
        raise Error('group须为all/source/api/notebook')
    selected = [describe(x['id']) for x in value['examples'] if group == 'all' or x['source_group'] == group]
    return {'openmc_version': value['openmc_version'], 'examples': selected,
            'count': len(selected), 'included_count':sum(x.get('scope')!='deferred' for x in selected),
            'deferred_count':sum(x.get('scope')=='deferred' for x in selected), 'sources': value['sources'],
            'note': '固定官方清单；第三方例题不在范围，未执行条目不能计为已通过。'}


def verify_bundled_sources(item: dict) -> Path:
    if item['source_group'] not in ('source', 'api'):
        raise Error('此入口不是随包源码；须按该Notebook的初始化说明下载固定版本原文')
    root = ROOT / load_catalog()['sources'][item['source_group']]['root']
    manifest = json.loads((root / 'manifest.json').read_text())
    if manifest['revision'] != item['source_revision']:
        raise Error('官方源码版本与清单不一致')
    for name, expected in manifest['files'].items():
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
            raise Error('官方源码缺失或路径不安全：' + name)
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise Error('官方源码与固定哈希不一致：' + name)
    return root
