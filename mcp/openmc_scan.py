"""Serial parameter scans reuse the existing calculation tools and durable local records."""
from __future__ import annotations

import csv
import fcntl
import itertools
import json
import math
from pathlib import Path
import shutil
import signal
import sys
import time
import uuid

import openmc_jobs as jobs
from openmc_runtime import Error, api, atomic_json, config, path_in_workspace, provenance, relative, workspace

TERMINAL = jobs.TERMINAL | {'partial'}


def cancel_active(identifier):
    # Reservation is written before creating/starting a child calculation. A crash
    # before its record exists means nothing has been launched for this ID.
    if identifier and (workspace() / '.mc-jobs' / identifier / 'job.json').is_file():
        return jobs.cancel(identifier)


def directory(scan_id):
    import re
    if not isinstance(scan_id, str) or not re.fullmatch('[a-f0-9]{32}', scan_id):raise Error('无效扫描编号')
    result = path_in_workspace('.mc-scans/' + scan_id, internal=True)
    if not (result / 'job.json').exists():raise Error('扫描记录不存在')
    return result


def list_parameters(case_dir: str) -> dict:
    from openmc_tools import validate_inputs
    checked = validate_inputs(case_dir)
    if not checked['structure_valid']:raise Error('输入结构无效：' + '; '.join(checked['problems']))
    return {'case_dir': case_dir, **api('parameters', path_in_workspace(case_dir), {})}


def copy_inputs(source: Path, target: Path):
    if source == workspace() or target.is_relative_to(source):raise Error('目标须在原算例目录之外')
    for path in source.rglob('*'):
        if path.is_symlink():raise Error('算例中包含符号链接，拒绝复制')
    shutil.copytree(source, target, ignore=lambda _d, names: [n for n in names if jobs.is_old_output(n)])


def prepare_case(case_dir: str, target_dir: str, parameters: dict) -> dict:
    import tempfile
    from openmc_import import publish_directory
    if not isinstance(parameters, dict) or not parameters:
        raise Error('parameters须为非空参数对象；先调用openmc_list_parameters')
    source = path_in_workspace(case_dir)
    target = path_in_workspace(target_dir, exists=False)
    if target.exists():raise Error('目标目录已存在，不能覆盖输入')
    if source == workspace() or target.is_relative_to(source) or source.is_relative_to(target):
        raise Error('目标须在原算例目录之外且不能是其父目录')
    api('check-parameters', source, {'combinations': [parameters]})
    target.parent.mkdir(parents=True, exist_ok=True)
    holder = Path(tempfile.mkdtemp(prefix='.prepare-', dir=target.parent))
    temporary = holder / 'model'
    try:
        copy_inputs(source, temporary)
        result = api('update-parameters', temporary, {'parameters': parameters})
        found = {item['name']: item.get('current')
                 for item in api('parameters', temporary, {}).get('parameters', [])}
        changes = result.get('changes', [])
        readback = []
        for name, expected in parameters.items():
            actual = found.get(name)
            if (isinstance(expected, (int, float)) and not isinstance(expected, bool)
                    and isinstance(actual, (int, float)) and not isinstance(actual, bool)):
                match = math.isclose(float(actual), float(expected), rel_tol=1e-12, abs_tol=1e-15)
            else:
                match = actual == expected
            readback.append({'name': name, 'expected': expected, 'found': actual, 'match': bool(match)})
        verified = all(item['match'] for item in readback)
        if not verified:
            raise Error('修改后读回不一致，未发布目标副本：' + json.dumps(readback, ensure_ascii=False))
        result['parameter_diff'] = {
            'changes': changes, 'readback': readback, 'verified': True,
            'note': 'changes为应用时刻前后值；readback逐项核对请求值与重新读取的落盘值。'
                    '不一致或读取失败时不发布目标副本；通过不表示专业正确性。'}
        source_info = provenance(temporary)
        publish_directory(temporary, target)
        return {'case_dir': target_dir, **result, 'provenance': source_info}
    finally:
        if holder.exists():shutil.rmtree(holder)


def submit_scan(case_dir: str, axes: dict, threads: int = 1, stop_on_error: bool = False,
                name: str | None = None) -> dict:
    task_name = jobs.checked_name(name)
    from openmc_tools import environment, validate_inputs, require_semantic_submission
    if type(threads) is not int or not 1 <= threads <= 64:raise Error('threads须为1..64')
    if type(stop_on_error) is not bool:raise Error('stop_on_error须为布尔值')
    if not isinstance(axes, dict) or not axes:raise Error('axes须为非空对象，键取自openmc_list_parameters')
    for name, values in axes.items():
        if not isinstance(name, str) or not isinstance(values, list) or not values:raise Error('每个扫描维度须有非空值数组')
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):raise Error('扫描值须为有限数值：' + name)
        if len(set(values)) != len(values):raise Error('扫描维度包含重复值：' + name)
    cfg = config()
    count = math.prod(len(values) for values in axes.values())
    maximum = cfg.get('max_scan_cases', 100)
    if count > maximum:raise Error(f'组合数{count}超过部署配置max_scan_cases={maximum}；请缩小范围或调整配置')
    source = path_in_workspace(case_dir)
    checked = validate_inputs(case_dir, semantic_check=True)
    if not checked['structure_valid']:raise Error('输入结构无效，未提交扫描')
    require_semantic_submission(checked.get('semantic') or {})
    combinations = [dict(zip(axes, values)) for values in itertools.product(*axes.values())]
    specs = api('check-parameters', source, {'combinations': combinations}, cfg)['parameters']
    runtime = environment()
    if not runtime['executable_available'] or not runtime['openmc_version_matches']:raise Error('运行时不可用或版本不符，未提交扫描')
    scan_id = uuid.uuid4().hex
    root = path_in_workspace('.mc-scans', exists=False, internal=True); root.mkdir(exist_ok=True)
    target = root / scan_id; target.mkdir()
    copy_inputs(source, target / 'baseline')
    record = {'scan_id': scan_id, 'name': task_name, 'state': 'queued', 'submitted_at': time.time(), 'case_dir': case_dir,
              'axes': axes, 'parameter_specs': specs, 'threads': threads, 'stop_on_error': stop_on_error,
              'runtime': cfg, 'provenance': provenance(source, cfg),
              'cases': [{'index': i, 'parameters': p, 'state': 'planned'} for i, p in enumerate(combinations)]}
    atomic_json(target / 'job.json', record)
    jobs.spawn_worker(target, 'openmc_scan.py', scan_id)
    return {'scan_id': scan_id, 'name': task_name, 'state': 'queued', 'cases': count, 'provenance': record['provenance'],
            'next': '查询openmc_scan_status；这是计算参数批次编号，不是智能体任务。'}



def differences(record: dict) -> dict:
    """Adjacent-axis k differences with an independent 2σ engineering check.

    A scan line is not evidence of a physical trend just because its points
    differ: with large per-point σ, sign-changing deltas are expected. This
    keeps every adjacent pair explicit instead of hiding the comparison in a
    plot or in a single relative-σ grade.
    """
    axes = list(record.get('axes') or {})
    cases = record.get('cases') or []
    pairs = []

    def usable(case):
        keff = case.get('keff') or {}
        mean, sigma = keff.get('mean'), keff.get('std_dev')
        return (case.get('state') == 'completed' and mean is not None and sigma is not None
                and isinstance(mean, (int, float)) and isinstance(sigma, (int, float))
                and not isinstance(mean, bool) and not isinstance(sigma, bool)
                and math.isfinite(mean) and math.isfinite(sigma) and sigma >= 0)

    unavailable = sum(1 for case in cases if not usable(case))
    for axis in axes:
        others = [name for name in axes if name != axis]
        groups = {}
        for case in cases:
            if not usable(case):
                continue
            keff = case.get('keff') or {}
            mean, sigma = keff.get('mean'), keff.get('std_dev')
            key = tuple(case.get('parameters', {}).get(name) for name in others)
            groups.setdefault(key, []).append((case.get('parameters', {}).get(axis), mean, sigma, case.get('index')))
        for values in groups.values():
            values.sort(key=lambda item: item[0])
            for left, right in zip(values, values[1:]):
                difference = right[1] - left[1]
                combined = math.sqrt(left[2] ** 2 + right[2] ** 2)
                within = abs(difference) <= 2 * combined if combined > 0 else difference == 0
                pairs.append({'axis': axis, 'from': left[0], 'to': right[0],
                              'from_index': left[3], 'to_index': right[3],
                              'difference': difference, 'combined_std_dev': combined,
                              'within_2sigma_engineering': bool(within),
                              'note': '相邻轴点、独立σ合成；2σ为工程判据，不是统计显著性检验。'})
    outside = sum(1 for pair in pairs if not pair['within_2sigma_engineering'])
    return {'criterion': 'abs(delta_k) <= 2*sqrt(sigma_left^2 + sigma_right^2)',
            'pairs': pairs, 'outside_2sigma': outside,
            'unavailable_points': unavailable,
            'note': ('扫描轴逐对给出Δk与合成σ；within_2sigma_engineering=false 时，'
                     '不能仅凭该差值声称趋势或物理效应。判据不处理系统误差、源收敛或相关性。')}

def status(scan_id: str) -> dict:
    target = directory(scan_id); record = jobs.read(target)
    if record['state'] not in TERMINAL and record.get('worker_pid') and jobs.identity(record['worker_pid']) != record.get('worker_identity'):
        with (target / 'record.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX); record = jobs.read(target)
            if record['state'] not in TERMINAL:
                record.update(state='interrupted', error='扫描监控进程丢失；未自动重新提交。可查询/取消已记录的计算作业。')
                atomic_json(target / 'job.json', record)
    result = {key: record[key] for key in ('scan_id', 'name', 'state', 'case_dir', 'axes', 'cases', 'active_job_id', 'provenance', 'submitted_at', 'finished_at', 'error') if key in record}
    result['counts'] = {state: sum(item['state'] == state for item in record['cases']) for state in ('planned', 'preparing', 'running', 'completed', 'failed', 'cancelled', 'interrupted', 'not_run')}
    result['differences'] = differences(record)
    return result


def cancel(scan_id: str) -> dict:
    target = directory(scan_id)
    with (target / 'record.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        record = jobs.read(target)
        if record['state'] in TERMINAL and not record.get('active_job_id'):
            return {'scan_id': scan_id, 'state': record['state'], 'changed': False}
        record['cancel_requested'] = True
        atomic_json(target / 'job.json', record)
    active = record.get('active_job_id')
    if active:cancel_active(active)
    if record['state'] in TERMINAL:return {'scan_id': scan_id, 'state': record['state'], 'changed': bool(active), 'next': '扫描监控已结束；继续按已记录job_id确认计算取消。'}
    return {'scan_id': scan_id, 'state': 'cancel_requested', 'changed': True}


def save_summary(target: Path):
    record = jobs.read(target)
    data = {key: record[key] for key in ('scan_id', 'state', 'axes', 'cases', 'provenance')}
    data['name'] = record.get('name')
    atomic_json(target / 'summary.json', data)
    with (target / 'summary.csv').open('w', newline='', encoding='utf-8') as output:
        writer = csv.writer(output)
        writer.writerow(['scan_id', 'index', *record['axes'], 'state', 'job_id', 'keff_mean', 'keff_std_dev', 'data_kind', 'professional_acceptance', 'error'])
        for row in record['cases']:
            keff = row.get('keff') or {}
            writer.writerow([record['scan_id'], row['index'], *[row['parameters'][key] for key in record['axes']], row['state'],
                             row.get('job_id', ''), keff.get('mean'), keff.get('std_dev'), record['provenance']['data_kind'], False, row.get('error', '')])


def run(scan_id):
    from openmc_tools import read_results
    target = directory(scan_id); record = jobs.read(target); stopped = False
    def stop(_s, _f):
        nonlocal stopped
        stopped = True
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    jobs.update(target, state='running', worker_pid=__import__('os').getpid(),
                worker_identity=jobs.identity(__import__('os').getpid()))
    try:
        for index, item in enumerate(record['cases']):
            latest = jobs.read(target)
            if stopped or latest.get('cancel_requested'):break
            try:
                case = target / 'cases' / str(index); case.parent.mkdir(exist_ok=True)
                copy_inputs(target / 'baseline', case)
                api('update-parameters', case, {'parameters': item['parameters']}, record['runtime'])
                # Serialise cancellation with submission and recording the new calculation ID.
                with (target / 'record.lock').open('a') as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX); latest = jobs.read(target)
                    if stopped or latest.get('cancel_requested'):break
                    child_id = uuid.uuid4().hex
                    latest['cases'][index].update(state='preparing', job_id=child_id)
                    latest['active_job_id'] = child_id
                    atomic_json(target / 'job.json', latest)
                    child = jobs.submit(relative(case), threads=record['threads'], _runtime=record['runtime'], _internal=True, _job_id=child_id,
                                        name=(record['name'][:105] + '[' + str(index) + ']') if record.get('name') else None)
                    latest['cases'][index]['state'] = 'running'
                    atomic_json(target / 'job.json', latest)
                while True:
                    child_state = jobs.status(child['job_id'])
                    if child_state['state'] in jobs.TERMINAL:break
                    if stopped or jobs.read(target).get('cancel_requested'):jobs.cancel(child['job_id'])
                    time.sleep(.1)
                latest = jobs.read(target); row = latest['cases'][index]
                row.update(state=child_state['state'], error=child_state.get('error'))
                if child_state['state'] == 'completed':
                    result = read_results(child['job_id'])
                    row.update(keff=result['keff'], warnings=result['warnings'])
                jobs.update(target, cases=latest['cases'], active_job_id=None)
                if child_state['state'] != 'completed' and record['stop_on_error']:break
            except Exception as error:
                active = jobs.read(target).get('active_job_id')
                if active:
                    cancel_error = None
                    try:cancel_active(active)
                    except Exception as stop_error:cancel_error = str(stop_error)
                    child_record = workspace() / '.mc-jobs' / active / 'job.json'
                    outstanding = bool(cancel_error) or (child_record.is_file() and jobs.read(child_record.parent)['state'] not in jobs.TERMINAL)
                    latest = jobs.read(target)
                    latest['cases'][index].update(state='failed', error=str(error)[:1000])
                    for row in latest['cases'][index+1:]:row['state'] = 'not_run'
                    jobs.update(target, state='failed', cases=latest['cases'], finished_at=time.time(),
                                error='扫描监控或结果处理失败；已停止后续提交。' + ('按active_job_id确认当前计算终态。' if outstanding else '') + (cancel_error or ''),
                                active_job_id=active if outstanding else None)
                    save_summary(target)
                    return
                latest = jobs.read(target); latest['cases'][index].update(state='failed', error=str(error)[:1000])
                jobs.update(target, cases=latest['cases'], active_job_id=None)
                if record['stop_on_error']:break
        latest = jobs.read(target)
        cancelled = stopped or latest.get('cancel_requested')
        for row in latest['cases']:
            if row['state'] == 'planned':row['state'] = 'not_run'
        successful = sum(row['state'] == 'completed' for row in latest['cases'])
        state = 'cancelled' if cancelled else ('completed' if successful == len(latest['cases']) else ('partial' if successful else 'failed'))
        jobs.update(target, state=state, cases=latest['cases'], finished_at=time.time(), active_job_id=None)
        save_summary(target)
    except Exception as error:
        active = jobs.read(target).get('active_job_id')
        if active:cancel_active(active)
        jobs.update(target, state='failed', error=str(error)[:1000], finished_at=time.time())


if __name__ == '__main__':run(sys.argv[1])
