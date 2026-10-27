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
from openmc_runtime import (Error, api, atomic_json, command, config, path_in_workspace,
                            provenance, relative, workspace)


def environment() -> dict:
    cfg = config()
    result = {'runtime': cfg['runtime'], 'expected_version': cfg['expected_version'],
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
    if template not in ('synthetic-mgxs', 'pwr-pin-cell'):
        raise Error('当前支持synthetic-mgxs及pwr-pin-cell模板')
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
    return {'case_dir': case_dir, **value, 'generation_method': 'deterministic_public_api_template',
            'fine_tuned_model_used': False}


def validate_inputs(case_dir: str, api_check: bool = False) -> dict:
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


def export_report(job_id: str, format: str = 'markdown') -> dict:
    if format in ('pdf', 'doc', 'docx'):
        from openmc_reports import export_job
        return export_job(job_id, format)
    if format not in ('markdown', 'csv'):
        raise Error('首版支持markdown/csv；PDF和DOC仍列在后续计划中')
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
    return {'job_id': job_id, 'format': format, 'path': relative(report),
            'provenance': data['provenance'], 'professional_acceptance': False,
            'note': '完整shape、filter及原始计分归一化说明见同作业results.json；截断状态随结果保留。'}
