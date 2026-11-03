"""Independent OpenMC operations exposed by both the CLI and standard MCP registry."""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

import openmc_jobs as jobs
from openmc_runtime import (ROOT, Error, api, atomic_json, command, config, path_in_workspace,
                            provenance, relative, workspace, file_location)


def environment() -> dict:
    cfg = config()
    result = {'runtime': cfg['runtime'], 'expected_version': cfg['expected_version'],
              'workspace': str(workspace()), 'location': 'server',
              'api_available': False, 'executable_available': False,
              'cross_sections_configured': bool(__import__('os').environ.get('OPENMC_CROSS_SECTIONS')),
              'professional_acceptance': False}
    try:
        probe = api('probe', workspace(), {}, cfg)
        result.update(api_available=True, api_version=probe['api_version'])
    except Error as exc:
        result['api_error'] = str(exc)
    try:
        completed = subprocess.run(command(cfg, workspace(), arguments=['--version']),
                                   capture_output=True, text=True, timeout=30)
        text = (completed.stdout + completed.stderr).strip()
        result['executable_available'] = completed.returncode == 0
        result['executable_version_output'] = text[:1000]
        result['version_matches'] = completed.returncode == 0 and cfg['expected_version'] in text
    except (OSError, subprocess.TimeoutExpired) as exc:
        result['executable_error'] = type(exc).__name__ + '；请按部署文档配置OpenMC运行时'
        result['version_matches'] = False
    result['note'] = '接口包、可执行程序和核数据分别检查；探测成功不等于物理验收。'
    return result


def create_model(case_dir: str, template: str = 'synthetic-mgxs', particles: int = 200,
                 batches: int = 10, inactive: int = 2) -> dict:
    if template not in ('synthetic-mgxs', 'pwr-pin-cell', 'pincell-multigroup'):
        raise Error('当前支持synthetic-mgxs、pwr-pin-cell及pincell-multigroup模板')
    if any(type(v) is not int for v in (particles, batches, inactive)) or particles <= 0 or not 0 <= inactive < batches or batches - inactive < 2:
        raise Error('particles须为正整数，batches-inactive至少为2，inactive非负')
    directory = path_in_workspace(case_dir, exists=False)
    if directory.exists():
        raise Error('目标算例目录已存在；请选择新目录，避免覆盖输入')
    directory.mkdir(parents=True)
    try:
        value = api('create', directory, {'template': template, 'particles': particles,
                                        'batches': batches, 'inactive': inactive})
    except Error:
        # Keep any partial products for diagnosis rather than deleting user-visible evidence.
        atomic_json(directory / 'creation-failed.json', {'state': 'failed', 'note': '生成失败，不能当作完整输入使用。'})
        raise
    return {'case_dir': case_dir, **value, 'workspace': str(workspace()),
            'case_server_path': str(directory), 'location': 'server',
            'generation_method': 'deterministic_public_api_template',
            'fine_tuned_model_used': False}


def _semantic_index(cfg: dict):
    """按运行时同一解析顺序定位生效的截面索引；返回(路径, 来源)或(None, 原因)。"""
    import os
    configured = cfg.get('cross_sections')
    if configured:
        path = Path(configured)
        if path.is_file():
            return path, 'deployment_config_cross_sections'
        return None, '部署配置cross_sections不存在：' + str(path)
    env_value = os.environ.get('OPENMC_CROSS_SECTIONS')
    if env_value:
        path = Path(env_value)
        if path.is_file():
            return path, 'env_OPENMC_CROSS_SECTIONS'
        return None, '环境变量OPENMC_CROSS_SECTIONS指向的索引不存在'
    managed = __import__('nuclear_data').managed_directory(cfg) / 'cross_sections.xml'
    if managed.is_file():
        return managed, 'managed_cache'
    key = cfg.get('nuclear_data_library', 'endfb-viii.0')
    pinned = ROOT / ('config/data-index/' + key + '.xml')
    if pinned.is_file():
        return pinned, 'pinned_data_index'
    return None, '未找到截面索引；先初始化核数据或在部署配置cross_sections'


def _semantic_summary(directory: Path, inspected: list, cfg: dict) -> dict:
    """连续能核素/S(a,b)名对照生效截面索引；多群与宏观材料如实跳过并说明原因。"""
    if 'materials.xml' not in inspected and 'model.xml' not in inspected:
        return {'checked': False, 'skipped_reason': '材料输入缺失或未通过结构检查，未做语义校验'}
    single = directory / 'model.xml'
    try:
        if single.exists():
            root = ET.parse(single).getroot()
            mode = root.findtext('.//energy_mode', 'continuous-energy')
        else:
            root = ET.parse(directory / 'materials.xml').getroot()
            settings = directory / 'settings.xml'
            mode = (ET.parse(settings).getroot().findtext('.//energy_mode', 'continuous-energy')
                    if settings.exists() else 'continuous-energy')
    except (ET.ParseError, OSError) as exc:
        return {'checked': False, 'skipped_reason': '材料/设置输入无法解析：' + str(exc)[:200]}
    if mode != 'continuous-energy':
        return {'checked': False, 'energy_mode': mode,
                'skipped_reason': '多群模式需匹配多群截面库；本版语义校验未覆盖'}
    nuclides, sabs, macroscopic = [], [], []
    for material in root.iter('material'):
        label = material.get('name') or ('id=' + (material.get('id') or '?'))
        for node in material.iter('nuclide'):
            nuclides.append((label, node.get('name') or ''))
        for node in material.iter('sab'):
            sabs.append((label, node.get('name') or ''))
        for node in material.iter('macroscopic'):
            macroscopic.append(label)
    if macroscopic:
        return {'checked': False,
                'skipped_reason': '存在macroscopic（多群）材料：' + '; '.join(macroscopic[:5])}
    index, source = _semantic_index(cfg)
    if index is None:
        return {'checked': False, 'skipped_reason': source}
    try:
        catalog = ET.parse(index).getroot()
    except (ET.ParseError, OSError) as exc:
        return {'checked': False, 'index_source': source,
                'skipped_reason': '截面索引无法解析：' + str(exc)[:200]}
    neutron, thermal = set(), set()
    for entry in catalog.iter('library'):
        names = (entry.get('materials') or '').split()
        if entry.get('type') == 'thermal':
            thermal.update(names)
        elif entry.get('type') == 'neutron':
            neutron.update(names)
    missing = [{'material': label, 'nuclide': name} for label, name in nuclides if name not in neutron]
    missing_sab = [{'material': label, 'sab': name} for label, name in sabs if name not in thermal]
    return {'checked': True, 'ok': not missing and not missing_sab,
            'index_source': source, 'index_path': str(index),
            'library_key': cfg.get('nuclear_data_library', 'endfb-viii.0'),
            'nuclides_checked': len({name for _, name in nuclides}),
            'sab_checked': len({name for _, name in sabs}),
            'missing_nuclides': sorted({item['nuclide'] for item in missing}),
            'missing_sab': sorted({item['sab'] for item in missing_sab}),
            'missing_detail': (missing + missing_sab)[:50],
            'note': '仅校验核素/S(a,b)名是否存在于生效截面索引；不校验温度、光子数据、共振自屏或物理适用性，索引与实际加载数据仍可能有差异。'}


PREVIEW_MAX_FILE_BYTES = 2 * 1024 * 1024
PREVIEW_CONTENT_CHARS = 20000


def _preview_summary(roots: dict) -> dict:
    """轻量结构摘要：只数已解析节点并提取常用设置，不重复完整校验。"""
    model = roots.get('model.xml')
    settings = model if model is not None else roots.get('settings.xml')
    summary = {}
    if model is not None:
        summary['layout'] = 'model.xml单文件'
    if settings is not None:
        for tag in ('run_mode', 'energy_mode', 'particles', 'batches', 'inactive', 'seed',
                    'photon_transport'):
            value = settings.findtext('.//' + tag)
            if value is not None:
                summary[tag] = value.strip()
    materials = model if model is not None else roots.get('materials.xml')
    if materials is not None:
        entries = []
        for material in materials.iter('material'):
            nuclides = [node.get('name') for node in material.iter('nuclide')]
            entries.append({'id': material.get('id'), 'name': material.get('name'),
                            'nuclides': nuclides[:20], 'nuclides_truncated': len(nuclides) > 20,
                            'sab': [node.get('name') for node in material.iter('sab')][:10]})
        summary['materials'] = entries[:20]
        summary['materials_truncated'] = len(entries) > 20
    geometry = model if model is not None else roots.get('geometry.xml')
    if geometry is not None:
        summary['cells'] = len(list(geometry.iter('cell')))
        summary['surfaces'] = len(list(geometry.iter('surface')))
    tallies = model if model is not None else roots.get('tallies.xml')
    if tallies is not None:
        entries = []
        for tally in tallies.iter('tally'):
            scores = (tally.findtext('scores') or '').split()
            entries.append({'id': tally.get('id'), 'name': tally.get('name'), 'scores': scores[:20]})
        if entries:
            summary['tallies'] = entries[:20]
    return summary


def preview_input(case_dir: str) -> dict:
    """提交前看清输入：逐文件截断预览、指纹与结构摘要；只读，不作物理判定。"""
    directory = path_in_workspace(case_dir)
    if not directory.is_dir():
        raise Error('case_dir须为目录')
    names = ['model.xml'] if (directory / 'model.xml').exists() else \
            ['materials.xml', 'geometry.xml', 'settings.xml']
    for optional in ('tallies.xml', 'plots.xml'):
        if (directory / optional).exists():
            names.append(optional)
    files, problems, roots = [], [], {}
    for name in names:
        path = directory / name
        if not path.is_file():
            if name != 'plots.xml':
                problems.append(name + '缺失')
            files.append({'name': name, 'present': False})
            continue
        if path.is_symlink():
            # 与复制作业同一口径：防经符号链接读取工作区外文件。
            problems.append(name + '为符号链接；预览拒绝读取')
            files.append({'name': name, 'present': True, 'symlink': True})
            continue
        data = path.read_bytes()
        entry = {'name': name, 'present': True, 'bytes': len(data),
                 'sha256': hashlib.sha256(data).hexdigest()}
        if len(data) > PREVIEW_MAX_FILE_BYTES:
            entry.update(content=None, truncated=True, note='超过2MiB预览上限；未返回内容')
        else:
            text = data.decode('utf-8', errors='replace')
            entry.update(content=text[:PREVIEW_CONTENT_CHARS],
                         truncated=len(text) > PREVIEW_CONTENT_CHARS)
            if entry['truncated']:
                entry['note'] = f'内容截断至前{PREVIEW_CONTENT_CHARS}字符；完整文件以服务端为准'
            try:
                roots[name] = ET.fromstring(data)
            except ET.ParseError as exc:
                entry['parse_error'] = str(exc)[:200]
        files.append(entry)
    return {'case_dir': case_dir, 'files': files, 'summary': _preview_summary(roots),
            'problems': problems, 'provenance': provenance(directory),
            'professional_acceptance': False, 'workspace': str(workspace()), 'location': 'server',
            'note': '服务端文件的截断预览与结构摘要，供提交前人工核对；不代表几何、核数据或物理正确。'}


def validate_inputs(case_dir: str, api_check: bool = False, semantic_check: bool = False) -> dict:
    directory = path_in_workspace(case_dir)
    if not directory.is_dir():
        raise Error('case_dir须为目录')
    names = ['model.xml'] if (directory / 'model.xml').exists() else ['materials.xml', 'geometry.xml', 'settings.xml']
    if (directory / 'tallies.xml').exists():
        names.append('tallies.xml')
    problems = []
    inspected = []
    for name in names:
        path = path_in_workspace(str(Path(case_dir) / name)) if (directory / name).exists() else directory / name
        if not path.is_file():
            problems.append(name + '缺失'); continue
        if path.stat().st_size > 10 * 1024 * 1024:
            problems.append(name + '超过本工具10MiB结构检查上限'); continue
        try:
            data = path.read_bytes()
            if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
                raise ValueError('不接受DTD或实体声明')
            root = ET.fromstring(data)
            expected = name.removesuffix('.xml')
            if root.tag != expected:
                raise ValueError('根元素应为' + expected)
            inspected.append(name)
            for tag in ('particles', 'batches', 'inactive'):
                node = root.find('.//' + tag)
                if node is not None and (int(node.text or '') < (0 if tag == 'inactive' else 1)):
                    problems.append(name + ': ' + tag + '范围无效')
        except (ET.ParseError, ValueError) as exc:
            problems.append(name + ': ' + str(exc)[:300])
    result = {'case_dir': case_dir, 'structure_valid': not problems, 'inspected': inspected,
              'problems': problems, 'validation_level': 'xml_structure_and_basic_settings',
              'provenance': provenance(directory), 'professional_acceptance': False,
              'note': '仅XML结构及少量运行参数检查；未知业务约束、几何重叠和物理正确性未作判定。'}
    if semantic_check:
        result['semantic'] = _semantic_summary(directory, inspected, config())
    if api_check and not problems:
        result['openmc_api'] = api('validate', directory, {})
        result['validation_level'] = 'xml_and_openmc_model_parser'
    return result


def submit_job(case_dir: str, mode: str = 'run', threads: int = 1) -> dict:
    validation = validate_inputs(case_dir)
    if not validation['structure_valid']:
        raise Error('输入结构检查失败：' + '; '.join(validation['problems']))
    runtime = environment()
    if not runtime['executable_available'] or not runtime['version_matches']:
        raise Error('OpenMC可执行程序不可用或版本不符；先调用openmc_environment并按部署文档修正')
    return jobs.submit(case_dir, mode, threads)


def verified_result(job_id: str):
    status = jobs.status(job_id)
    if status['state'] != 'completed':
        raise Error('作业尚未成功完成；先查询openmc_job_status')
    directory = jobs.job_directory(job_id)
    record = jobs.read(directory)
    workflow=record.get('example_workflow')
    if workflow:
        from official_catalog import definition
        if definition(workflow['example_id'])['workflow']=='notebook':
            raise Error('Notebook可含多次求解，不能默认取最后StatePoint画图或导出报告；用openmc_read_example_results的calculation_id选择原始结果，查看对应Notebook报告。')
    if record['mode'] == 'plot':
        raise Error('绘图作业不提供计算statepoint；请查看作业files中的图像')
    candidates = [directory / 'work' / name for name in record.get('output_files', {})
                  if name.startswith('statepoint.') and name.endswith('.h5') and '/' not in name]
    if not candidates:
        raise Error('作业没有statepoint产物；绘图作业请查看files，计算作业请检查日志')
    def batch(path):
        try:
            return int(path.name.split('.')[1])
        except (IndexError, ValueError):
            raise Error('statepoint文件名不能识别')
    point = max(candidates, key=batch)
    if point.is_symlink():
        raise Error('结果文件不能是符号链接')
    if not point.is_file() or jobs.file_hash(point) != record['output_files'][point.name]:
        raise Error('结果文件缺失或已在计算后改变；不能作为本作业原始产物读取')
    return directory, record, point


def read_results(job_id: str, max_values: int = 1000) -> dict:
    if type(max_values) is not int or not 1 <= max_values <= 10000:
        raise Error('max_values须为1..10000')
    directory, record, point = verified_result(job_id)
    status = jobs.status(job_id)
    cfg = jobs.read(directory)['runtime']
    data = api('results', directory / 'work', {'statepoint': point.name, 'max_values': max_values}, cfg)
    data.update(job_id=job_id, provenance=status['provenance'],
                statepoint=relative(point), statepoint_sha256=hashlib.sha256(point.read_bytes()).hexdigest(),
                professional_acceptance=False)
    atomic_json(directory / 'results.json', data)
    return data


def compare_jobs(job_a: str, job_b: str) -> dict:
    """两作业对比第一刀：输入指纹、状态、运行设置与Combined k；不含tally分布对比。"""
    if job_a == job_b:
        raise Error('请选择两个不同的作业编号')
    sides = {}
    for label, job_id in (('a', job_a), ('b', job_b)):
        status = jobs.status(job_id)
        record = jobs.read(jobs.job_directory(job_id))
        progress = status.get('progress') or {}
        sides[label] = {'job_id': job_id, 'state': status['state'],
                        'mode': record.get('mode'), 'case_dir': record.get('case_dir'),
                        'threads': record.get('threads'),
                        'run_mode': progress.get('run_mode'),
                        'batches_total': progress.get('batches_total'),
                        'inactive': progress.get('inactive'),
                        'elapsed_seconds': progress.get('elapsed_seconds'),
                        'keff_combined': progress.get('keff_combined'),
                        'input_files': record.get('input_files', {}),
                        'output_count': len(record.get('output_files', {}))}
    a, b = sides['a'], sides['b']
    names = set(a['input_files']) | set(b['input_files'])
    identical = sorted(n for n in names
                       if a['input_files'].get(n) and a['input_files'].get(n) == b['input_files'].get(n))
    differing = sorted(n for n in names
                       if n in a['input_files'] and n in b['input_files']
                       and a['input_files'][n] != b['input_files'][n])
    keff_a, keff_b = a['keff_combined'], b['keff_combined']
    keff = {'a': keff_a, 'b': keff_b}
    if keff_a and keff_b:
        keff.update(
            difference=round(keff_b['mean'] - keff_a['mean'], 6),
            combined_std_dev=round((keff_a['std_dev'] ** 2 + keff_b['std_dev'] ** 2) ** 0.5, 6),
            note='difference=b-a；combined_std_dev按两作业独立假设合成，仅为工程参考，不构成统计显著性判定。')
    else:
        keff['note'] = '至少一方无Combined k（未完成、固定源、日志无RESULTS节或数值异常如NaN）；不作差值。'
    comparison = {
        'inputs': {'identical': identical, 'differing': differing,
                   'only_in_a': sorted(set(a['input_files']) - set(b['input_files'])),
                   'only_in_b': sorted(set(b['input_files']) - set(a['input_files']))},
        'keff_combined': keff,
        'runtime': {field: {'a': a[field], 'b': b[field], 'same': a[field] == b[field]}
                    for field in ('state', 'mode', 'threads', 'run_mode',
                                  'batches_total', 'inactive')}}
    return {'job_a': job_a, 'job_b': job_b, 'sides': sides, 'comparison': comparison,
            'workspace': str(workspace()), 'location': 'server', 'professional_acceptance': False,
            'note': '工程对比第一刀：输入指纹、状态、运行设置与Combined k；tally分布与网格结果对比未包含，需要时另扩展。'}


def export_report(job_id: str, format: str = 'markdown', tally_id: int | None = None,
                  score: str | None = None, nuclide: str | None = None,
                  filter_selection: list | None = None) -> dict:
    if format not in ('markdown', 'csv', 'pdf', 'doc', 'docx'):
        raise Error('format须为markdown/csv/pdf/docx/doc')
    selection = dict(tally_id=tally_id, score=score, nuclide=nuclide, filter_selection=filter_selection)
    # Presence matters: an explicit [] means select all bins, not job-summary mode.
    if any(value is not None for value in selection.values()):
        from openmc_reports import export_selected_job
        return export_selected_job(job_id, format, selection)
    if format in ('pdf', 'doc', 'docx'):
        from openmc_reports import export_job
        return export_job(job_id, format)
    data = read_results(job_id)
    directory = jobs.job_directory(job_id)
    note = data['provenance'].get('note', '')
    if format == 'markdown':
        output = '# OpenMC接口联调结果\n\n'
        output += f"作业：{job_id}\n\n数据来源：{data['provenance']['data_kind']}\n\n{note}\n\n"
        output += '**专业验收：未进行。** 数值读取成功不代表满足合同物理指标。\n\n'
        output += '```json\n' + json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n```\n'
        name = 'report.md'
    else:
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(['job_id', 'data_kind', 'execution_kind', 'professional_acceptance', 'quantity', 'tally_id', 'scores', 'nuclides', 'flat_index', 'mean', 'std_dev', 'shape', 'values_truncated', 'normalization', 'metadata_file'])
        kind = data['provenance']['data_kind']
        execution_kind = data['provenance']['execution_kind']
        if data['keff']:
            writer.writerow([job_id, kind, execution_kind, False, 'keff', '', '', '', 0, data['keff']['mean'], data['keff']['std_dev'], '[1]', False, 'dimensionless', 'results.json'])
        for tally in data['tallies']:
            for index, (mean, std) in enumerate(zip(tally['mean'], tally['std_dev'])):
                writer.writerow([job_id, kind, execution_kind, False, 'tally_raw', tally['id'], '|'.join(tally['scores']), '|'.join(tally['nuclides']), index, mean, std, json.dumps(tally['shape']), tally['values_truncated'], tally['normalization'], 'results.json'])
        output = stream.getvalue(); name = 'report.csv'
    report = directory / name
    report.write_text(output, encoding='utf-8')
    return {'job_id': job_id, 'format': format, 'selection_mode': 'job_summary', **file_location(report),
            'provenance': data['provenance'], 'professional_acceptance': False,
            'note': '完整shape、filter及原始计分归一化说明见同作业results.json；截断状态随结果保留。'}
