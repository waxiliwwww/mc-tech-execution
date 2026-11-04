"""Fault sample library: reproducible bad cases plus real engine log excerpts.

样本只用于自检诊断覆盖面；诊断通过不代表专业结论，未命中不等于输入正确。
"""
import json
import re
import shutil
import tempfile
from pathlib import Path

import business
import openmc_tools as tools
from openmc_runtime import Error, workspace

FAULTS = business.ROOT / 'fixtures/faults'
REGISTRY = business.ROOT / 'config/fault-samples.json'
RULES = business.ROOT / 'config/error-rules.json'


def samples() -> list[dict]:
    value = json.loads(REGISTRY.read_text(encoding='utf-8'))
    if not isinstance(value, list) or not value:
        raise Error('故障样本注册表损坏：config/fault-samples.json')
    return value


def list_samples() -> dict:
    """列出故障样本库：编号、种类、预期检测类别与依据。"""
    entries = []
    for sample in samples():
        entries.append({'id': sample['id'], 'kind': sample['kind'],
                        'expected': sample['expected'], 'evidence': sample['evidence'],
                        'directory': sample.get('directory'), 'log': sample.get('log')})
    return {'count': len(entries), 'samples': entries, 'library': str(FAULTS),
            'workspace': str(workspace()), 'location': 'server',
            'note': '样本是人为构造的坏算例或真实引擎报错摘录，用于自检诊断是否定位得到；'
                    '不代表甲方故障清单，也不证明专业诊断能力。'}


def _nuclear_data_ready() -> bool:
    """本机是否已就绪核数据库索引（核素可用性检查依赖它）。"""
    try:
        from openmc_runtime import config
        import nuclear_data
        return (nuclear_data.managed_directory(config()) / 'cross_sections.xml').is_file()
    except Exception:  # noqa: BLE001
        return False


def _check_input(sample: dict) -> dict:
    name = sample['directory']
    directory = FAULTS / name
    if not directory.is_dir():
        raise Error('样本目录不存在：' + name)
    base = workspace()
    temporary = Path(tempfile.mkdtemp(prefix='.fault-', dir=base))
    try:
        for item in directory.iterdir():
            if item.is_file():
                shutil.copyfile(item, temporary / item.name)
        value = tools.validate_inputs(str(temporary.relative_to(base)), semantic_check=True)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    needles = [sample['expected']['contains']]
    collected = []
    for problem in value.get('problems') or []:
        collected.append({'source': 'problems', 'detail': problem})
    semantic = (value.get('semantic') or {}).get('problems') or []
    for problem in semantic:
        collected.append({'source': 'semantic', 'kind': problem.get('kind'),
                          'detail': problem.get('location', '') + '：' + problem.get('detail', '')})
    hits = [item for item in collected
            if any(needle in (item.get('kind') or '') or needle in item['detail'] for needle in needles)]
    return {'id': sample['id'], 'kind': 'input', 'passed': bool(hits),
            'expected': sample['expected'], 'found': hits[:5], 'examined': len(collected),
            'evidence_path': str(directory)}


def _check_log(sample: dict) -> dict:
    path = FAULTS / sample['log']
    if not path.is_file():
        raise Error('样本日志不存在：' + sample['log'])
    rules = json.loads(RULES.read_text(encoding='utf-8'))
    expected = sample['expected']
    found = []
    lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
    for index, line in enumerate(lines):
        window = line
        if index + 1 < len(lines):
            window = line.rstrip() + ' ' + lines[index + 1].strip()
        for rule in rules:
            if re.search(rule['pattern'], line, re.IGNORECASE) or re.search(rule['pattern'], window, re.IGNORECASE):
                found.append({'source': sample['log'], 'line': index + 1, 'excerpt': window.strip()[:300],
                              'category': rule['category'], 'suggestion': rule['suggestion'],
                              'rule_status': rule['status']})
    hits = [item for item in found if item['category'] == expected['category']]
    return {'id': sample['id'], 'kind': 'log', 'passed': bool(hits),
            'expected': expected, 'found': hits[:5], 'examined': len(found),
            'evidence_path': str(path)}


def check_sample(sample_id: str | None = None) -> dict:
    """自检故障样本：给编号只查一条，省略则查全部并按通过数汇总。"""
    entries = samples()
    if sample_id is not None:
        selected = [item for item in entries if item['id'] == sample_id]
        if not selected:
            raise Error('未知故障样本编号；先用openmc_list_fault_samples查看')
    else:
        selected = entries
    ready = _nuclear_data_ready()
    results = []
    for sample in selected:
        if sample.get('requires') == 'nuclear_data' and not ready:
            results.append({'id': sample['id'], 'kind': sample['kind'], 'passed': None,
                            'expected': sample['expected'],
                            'skipped': '需要生效截面索引（核数据未就绪），本条未判定'})
            continue
        results.append(_check_input(sample) if sample['kind'] == 'input' else _check_log(sample))
    passed = sum(1 for item in results if item['passed'] is True)
    skipped = [item['id'] for item in results if item['passed'] is None]
    return {'requested': sample_id, 'total': len(results), 'passed': passed,
            'skipped': skipped,
            'failed': [item['id'] for item in results if item['passed'] is False],
            'results': results, 'workspace': str(workspace()), 'location': 'server',
            'note': '输入样本由 validate_inputs 定位到文件与问题；日志样本由规则库定位到行号。'
                    '样本通过只说明诊断能定位该构造缺陷，不代表专业结论或已排除其他问题。'}
