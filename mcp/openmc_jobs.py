"""Linux/WSL local jobs. Records survive MCP/CLI restarts; only owned workers are signalled."""
from __future__ import annotations

import fcntl
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from owned_processes import OwnedProcessTree, ProcessControlError

from openmc_runtime import (Error, ROOT, atomic_json, command, config, path_in_workspace,
                            provenance, relative, workspace)

TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted'}


def is_old_output(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in
               ('statepoint.*.h5', 'summary.h5', 'tallies.out', 'tracks*.h5',
                'particle*.h5', '*.png', '*.ppm', '*.vti'))


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def identity(pid: int) -> str | None:
    try:
        stat = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        if stat[0] == 'Z':
            return None
        return Path('/proc/sys/kernel/random/boot_id').read_text().strip() + ':' + stat[19]
    except (OSError, IndexError):
        return None


def job_directory(job_id: str) -> Path:
    if not isinstance(job_id, str) or not re.fullmatch('[a-f0-9]{32}', job_id):
        raise Error('无效作业编号')
    directory = path_in_workspace('.mc-jobs/' + job_id, internal=True)
    if not (directory / 'job.json').is_file():
        raise Error('作业记录不存在')
    return directory


def read(directory: Path) -> dict:
    try:
        return json.loads((directory / 'job.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise Error('无法读取作业记录') from exc


def update(directory: Path, **fields) -> dict:
    with (directory / 'record.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        value = read(directory)
        value.update(fields)
        atomic_json(directory / 'job.json', value)
        return value


def spawn_worker(directory: Path, script: str, identifier: str) -> None:
    try:
        with (directory / 'worker.log').open('ab') as log:
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve().parent / script), identifier],
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                       start_new_session=True,
                                       env={**os.environ, 'MC_WORKSPACE': str(workspace())})
        update(directory, worker_pid=process.pid, worker_identity=identity(process.pid))
        threading.Thread(target=process.wait, daemon=True).start()
    except OSError as exc:
        update(directory, state='failed', error='计算监控进程启动失败', finished_at=time.time())
        raise Error('计算监控进程启动失败') from exc


def running_usage() -> dict:
    """统计未结束作业占用的线程数（含queued），用于按核数限并发。"""
    jobs = path_in_workspace('.mc-jobs', exists=False, internal=True)
    used = 0
    count = 0
    if jobs.is_dir():
        for directory in jobs.iterdir():
            marker = directory / 'job.json'
            if not marker.is_file():
                continue
            try:
                record = read(directory)
            except Error:
                continue
            if record.get('state') in ('running', 'queued'):
                used += int(record.get('threads') or 1)
                count += 1
    return {'running_jobs': count, 'used_threads': used}


def check_capacity(cfg: dict, threads: int) -> dict:
    """按CPU核数与可选max_concurrent_jobs限并发；超限直接拒绝并说明如何腾出。"""
    cores = int(cfg.get('cpu_count') or os.cpu_count() or 1)
    maximum = int(cfg.get('max_concurrent_jobs') or 0)
    usage = running_usage()
    if maximum and usage['running_jobs'] >= maximum:
        raise Error('并发作业已达上限' + str(maximum) + '个（当前' + str(usage['running_jobs']) +
                    '个）；等一个作业结束或先取消后再提交')
    if usage['used_threads'] + threads > cores:
        raise Error('按CPU核数限并发：已用' + str(usage['used_threads']) + '线程，本机' + str(cores) +
                    '核，再提交' + str(threads) + '线程会超核；请调小threads、等作业结束或先取消')
    return {**usage, 'cores': cores, 'requested_threads': threads,
            'limit_jobs': maximum or None}


def checked_name(name: str | None) -> str | None:
    if name is None:
        return None
    import unicodedata
    if (not isinstance(name, str) or not name.strip() or len(name) > 120
            or any(unicodedata.category(c).startswith('C') for c in name)):
        raise Error('name须为1..120字符的非空任务名称，不接受控制字符')
    return name


def list_jobs(name: str | None = None, state: str | None = None, limit: int = 50) -> dict:
    """List durable records, preserving duplicate display names and old unnamed jobs."""
    name = checked_name(name)
    if state is not None and state not in TERMINAL | {'queued', 'running'}:
        raise Error('state须为queued/running/completed/failed/cancelled/interrupted')
    if type(limit) is not int or not 1 <= limit <= 200:
        raise Error('limit须为1..200')
    directory = path_in_workspace('.mc-jobs', exists=False, internal=True)
    items, unreadable = [], []
    if directory.exists():
        for child in directory.iterdir():
            if not re.fullmatch('[a-f0-9]{32}', child.name):
                continue
            if child.is_symlink() or (child / 'job.json').is_symlink():
                unreadable.append(child.name)
                continue
            try:
                record = read(child)
                if record.get('job_id') != child.name:
                    raise Error('记录编号不一致')
                if name is not None and record.get('name') != name:
                    continue
                # Refresh recoverable interrupted state exactly as status() does.
                current = status(child.name)
            except (Error, OSError, ValueError, KeyError):
                unreadable.append(child.name)
                continue
            if state is not None and current['state'] != state:
                continue
            items.append({k: current.get(k) for k in
                          ('job_id', 'name', 'state', 'case_dir', 'mode', 'submitted_at',
                           'started_at', 'finished_at', 'exit_code', 'error')})
    items.sort(key=lambda v: (v.get('submitted_at') or 0, v['job_id']), reverse=True)
    return {'jobs': items[:limit], 'total_matches': len(items), 'truncated': len(items) > limit,
            'unreadable_job_ids': unreadable, 'workspace': str(workspace()),
            'note': '名称为精确匹配，可重名；后续状态/取消/结果调用使用job_id。旧记录名称为null。'}


def submit(case_dir: str, mode: str = 'run', threads: int = 1, _runtime: dict | None = None,
           _internal: bool = False, _job_id: str | None = None, _expected_inputs: dict | None = None,
           _example_workflow: dict | None = None, _preserve_outputs: bool = False,
           name: str | None = None, _input_integrity: dict | None = None) -> dict:
    name = checked_name(name)
    if not sys.platform.startswith('linux'):
        raise Error('本地持久作业目前支持Linux/WSL；其他系统请在Linux服务器部署MCP')
    if mode not in ('run', 'plot', 'geometry-debug', 'deplete') or type(threads) is not int or not 1 <= threads <= 64:
        raise Error('mode须为run/plot/geometry-debug/deplete；threads须为1..64')
    source = path_in_workspace(case_dir, internal=_internal)
    if not source.is_dir() or source == workspace():
        raise Error('case_dir须为工作区内的具体算例子目录')
    cfg = _runtime or config()
    if mode == 'deplete':
        markers = source / 'mc-case.json'
        spec = json.loads(markers.read_text(encoding='utf-8')).get('depletion') if markers.is_file() else None
        if not spec:
            raise Error('燃耗提交前先用openmc_configure_depletion配置功率与时间步')
        if not (source / 'materials.xml').is_file() and not (source / 'model.xml').is_file():
            raise Error('燃耗算例缺少materials.xml或model.xml')
    capacity = check_capacity(cfg, threads)
    from openmc_storage import check_submission_space
    disk_admission = check_submission_space(source, workspace(), cfg, _preserve_outputs, is_old_output)
    job_id = _job_id or uuid.uuid4().hex
    if not re.fullmatch('[a-f0-9]{32}', job_id):raise Error('无效预留作业编号')
    jobs = path_in_workspace('.mc-jobs', exists=False, internal=True)
    jobs.mkdir(exist_ok=True)
    directory = jobs / job_id
    directory.mkdir()
    copied = directory / 'work'
    # Each submission gets an immutable input snapshot. Reject symlinks before copying.
    for path in source.rglob('*'):
        if path.is_symlink():
            raise Error('算例包含符号链接，未提交')
    # Ordinary jobs reject stale outputs; typed workflows explicitly carry their
    # predecessor's verified resources (including images/HDF5) into the next step.
    shutil.copytree(source, copied, ignore=None if _preserve_outputs else
                    lambda _directory, names: [n for n in names if is_old_output(n)])
    input_files = {str(p.relative_to(copied)): file_hash(p) for p in copied.rglob('*') if p.is_file()}
    if _expected_inputs is not None and input_files != _expected_inputs:
        raise Error('输入在校核与快照之间发生变化，未启动计算；保留未完成目录供核对')
    record = {'job_id': job_id, 'name': name, 'state': 'queued', 'submitted_at': time.time(),
              'case_dir': case_dir, 'mode': mode, 'threads': threads,
              'provenance': provenance(source, cfg), 'runtime': cfg, 'concurrency': capacity,
              'work_dir': relative(copied), 'input_files': input_files, 'disk_admission': disk_admission}
    if _example_workflow is not None:
        record['example_workflow'] = _example_workflow
    if _input_integrity is not None:
        record['input_integrity'] = _input_integrity
    atomic_json(directory / 'job.json', record)
    spawn_worker(directory, 'openmc_jobs.py', job_id)
    return {'job_id': job_id, 'name': name, 'state': read(directory)['state'],
            'case_dir': case_dir, 'work_dir': relative(copied),
            'provenance': record['provenance'], 'next': '使用openmc_job_status查询，提交不等于计算完成。'}


# Entropy is optional in OpenMC's k table. Horizontal whitespace prevents a
# partial row from accidentally consuming the next line. Keep unknown values null.
PROGRESS_NUMBER = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][+-]?\d+)?'
PROGRESS_VALUE = rf'(?:{PROGRESS_NUMBER}|[+-]?(?:nan|inf(?:inity)?))'
PROGRESS_BATCH_LINE = re.compile(
    rf'^[ \t]*(\d+)/(\d+)[ \t]+({PROGRESS_VALUE})'
    rf'(?:[ \t]+({PROGRESS_VALUE}))?'
    rf'(?:[ \t]+({PROGRESS_VALUE})[ \t]+\+/-[ \t]+({PROGRESS_VALUE}))?[ \t]*$',
    re.M | re.I)
PROGRESS_COMBINED_KEFF = re.compile(
    rf'Combined k-effective\s*=\s*({PROGRESS_VALUE})\s*\+/-\s*({PROGRESS_VALUE})(?=$|[ \t\r\n])', re.I)
PROGRESS_COMBINED_LOOSE = re.compile(r'Combined k-effective\s*=\s*(\S+)\s*\+/-\s*(\S+)')
PROGRESS_RESULT_ESTIMATE = re.compile(
    rf'k-effective\s*\((Collision|Track-length|Absorption)\)\s*=\s*'
    rf'({PROGRESS_VALUE})\s*\+/-\s*({PROGRESS_VALUE})', re.I)
PROGRESS_ESTIMATE_ORDER = (
    ('Absorption', 'solver.log:RESULTS:Absorption k-effective'),
    ('Collision', 'solver.log:RESULTS:Collision k-effective'),
    ('Track-length', 'solver.log:RESULTS:Track-length k-effective'),
)


def _finite_progress(raw):
    import math
    if not raw:return None
    value = float(raw.replace('D', 'E').replace('d', 'e'))
    return value if math.isfinite(value) else None


def _usable_keff(value):
    return (isinstance(value, dict) and value.get('mean') is not None
            and value.get('std_dev') is not None)


def _summary_keff(progress):
    """Choose the most defensible k available in a log, in documented order.

    Combined is preferred only when finite. When its std is -nan, the RESULTS
    estimators are better than the last batch-table Average: Absorption first,
    then Collision, then Track-length. The batch Average is the final fallback.
    """
    combined = progress.get('keff_combined')
    if _usable_keff(combined):
        return combined, 'solver.log:RESULTS:Combined k'
    estimates = progress.get('keff_estimates') or {}
    for name, source in PROGRESS_ESTIMATE_ORDER:
        value = estimates.get(name.lower())
        if _usable_keff(value):
            return value, source
    average = progress.get('keff_average')
    if _usable_keff(average):
        return average, 'solver.log:批次表Average（非Combined，末批波动更大）'
    return None, None


def _progress(directory: Path, value: dict) -> dict:
    """从solver.log尾部解析批次行为结构化进度；只读日志，不影响状态机与统计结论。"""
    work = directory / 'work'
    mode = None
    batches_total = inactive = None
    settings = work / ('model.xml' if (work / 'model.xml').exists() else 'settings.xml')
    if settings.is_file():
        try:
            import xml.etree.ElementTree as ET
            root = ET.parse(settings).getroot()
            mode = root.findtext('.//run_mode')
            raw = root.findtext('.//batches')
            batches_total = int(raw) if raw else None
            raw = root.findtext('.//inactive')
            inactive = int(raw) if raw else None
        except Exception:
            pass   # 进度为尽力而为的展示信息；解析失败不影响状态机
    started = value.get('started_at')
    finished = value.get('finished_at')
    elapsed = round((finished or time.time()) - started, 3) if started else None
    progress = {'run_mode': mode, 'batches_total': batches_total, 'inactive': inactive,
                'latest_batch': None, 'latest_generation': None, 'percent': None,
                'keff_latest': None, 'entropy_latest': None, 'keff_average': None,
                'keff_combined': None, 'keff_estimates': {}, 'keff_source': None,
                'elapsed_seconds': elapsed,
                'note': '按solver.log尾部批次行解析；不替代openmc_read_results的StatePoint统计量。'}
    log_path = directory / 'solver.log'
    if not log_path.is_file():
        progress['note'] = 'solver.log尚不存在；计算可能未产生输出。'
        return progress
    try:
        size = log_path.stat().st_size
        with log_path.open('rb') as log:
            log.seek(max(0, size - 65536))
            text = log.read().decode('utf-8', errors='replace')
    except OSError:
        progress['note'] = 'solver.log暂时不可读；以状态与日志尾部为准。'
        return progress
    matches = PROGRESS_BATCH_LINE.findall(text)
    if matches:
        batch, generation, keff, entropy, average_mean, average_std = matches[-1]
        progress.update(latest_batch=int(batch), latest_generation=int(generation),
                        keff_latest=_finite_progress(keff), entropy_latest=_finite_progress(entropy))
        if average_mean:
            progress['keff_average'] = {'mean': _finite_progress(average_mean), 'std_dev': _finite_progress(average_std)}
        if any(raw and _finite_progress(raw) is None for raw in (keff, entropy, average_mean, average_std)):
            progress['note'] = '批次行含非有限统计量；保留批号，不可用数值为空，请核对采样条件和原日志。'
        if batches_total:
            progress['percent'] = round(100.0 * min(int(batch), batches_total) / batches_total, 2)
    elif mode == 'eigenvalue':
        progress['note'] = '暂未解析到批次行；计算可能仍在初始化或日志已截断。'
    else:
        progress['note'] = '该run_mode无批次k表；进度以状态与日志尾部为准，不编造百分比。'
    combined = PROGRESS_COMBINED_KEFF.search(text)
    if combined and all(_finite_progress(v) is not None for v in combined.groups()):
        progress['keff_combined'] = {'mean': _finite_progress(combined.group(1)),
                                     'std_dev': _finite_progress(combined.group(2))}
        progress['percent'] = 100.0
    else:
        loose = PROGRESS_COMBINED_LOOSE.search(text)
        if loose:
            # NaN/异常数值不能安全进入JSON结果；如实报告行的存在与原文。
            progress['keff_note'] = ('Combined k行含非有限值（原文：'
                                     + loose.group(1) + ' +/- ' + loose.group(2)
                                     + '）；不提供keff_combined。不要直接把非有限值归因于“未收敛”：'
                                     '也可能是某个估计器方差为0或合并除零；先核对RESULTS节与StatePoint，'
                                     '增加批次数未必修复。')
    estimates = {}
    for name, raw_mean, raw_std in PROGRESS_RESULT_ESTIMATE.findall(text):
        mean, std = _finite_progress(raw_mean), _finite_progress(raw_std)
        if mean is not None and std is not None:
            estimates[name.lower()] = {'mean': mean, 'std_dev': std}
    progress['keff_estimates'] = estimates
    if value.get('state') == 'completed' and progress['percent'] is None and mode == 'eigenvalue':
        progress['percent'] = 100.0
    # 标注k的出处：同一作业的k在status/read_results/plot_convergence三处口径不同，
    # 不标来源时用户无法判断该信哪一个。Combined失效时优先RESULTS估计量，
    # 不把批次表最后一列Average当成同等质量的回退。
    _, progress['keff_source'] = _summary_keff(progress)
    return progress


def status(job_id: str) -> dict:
    directory = job_directory(job_id)
    value = read(directory)
    if value['state'] not in TERMINAL and value.get('worker_pid'):
        if identity(value['worker_pid']) != value.get('worker_identity'):
            # Re-read under the record lock: the worker may have just finished.
            with (directory / 'record.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                value = read(directory)
                if value['state'] not in TERMINAL:
                    value.update(state='interrupted', finished_at=time.time(),
                                 error='作业监控进程已退出；运行结果未被确认，不能报告成功。')
                    atomic_json(directory / 'job.json', value)
    log_path = directory / 'solver.log'
    tail = ''
    if log_path.exists():
        with log_path.open('rb') as log:
            log.seek(max(0, log_path.stat().st_size - 4000))
            tail = log.read().decode('utf-8', errors='replace')
    result = {key: value[key] for key in ('job_id', 'name', 'state', 'mode', 'case_dir', 'work_dir', 'submitted_at',
              'started_at', 'finished_at', 'exit_code', 'error', 'provenance', 'log_truncated',
              'process_cleanup', 'cancel_requested', 'input_integrity') if key in value}
    result['log_tail'] = tail
    result['progress'] = _progress(directory, value)
    result['input_files'] = sorted(value.get('input_files', {}))
    result['input_hashes'] = dict(value.get('input_files', {}))
    result['files'] = sorted(value.get('output_files', {}))
    progress = result.get('progress') or {}
    k, summary_source = _summary_keff(progress)
    if summary_source:
        progress['keff_source'] = summary_source
        result['keff_source'] = summary_source
    if value['state'] == 'completed':
        has_k = _usable_keff(k)
        if has_k:
            source = summary_source or '未知来源'
            if source.startswith('solver.log:RESULTS:Combined') or source.startswith('statepoint:k_combined'):
                label = 'Combined k'
            elif '批次表Average' in source:
                label = '批次Average（非Combined）'
            elif source.startswith('solver.log:RESULTS:'):
                label = 'RESULTS估计器（非Combined）'
            else:
                label = '来源未标明（不能当成Combined）'
            result['human_summary'] = (f"已完成；k={k['mean']:.5f}±{k['std_dev']:.5f}"
                                       f"（{label}）")
        else:
            result['human_summary'] = '已完成；该作业无k结果；不要把无值当成k=0'
    elif value['state'] == 'failed':
        result['human_summary'] = f"失败；exit_code={value.get('exit_code')}；查看 log_tail"
    elif value['state'] == 'cancelled':
        result['human_summary'] = '已取消；已完成作业不会重新执行'
    elif progress.get('percent') is not None:
        result['human_summary'] = f"{value['state']}：{progress['percent']:.1f}%"
    else:
        result['human_summary'] = value['state']
    return {'human_summary': result.pop('human_summary'), **result}


def cancel(job_id: str) -> dict:
    directory = job_directory(job_id)
    value = read(directory)
    if value['state'] in TERMINAL:
        return {'job_id': job_id, 'state': value['state'], 'changed': False}
    pid = value.get('worker_pid')
    if not pid:
        # A scan may have died after preparing the record but before worker startup.
        # Keep the durable request even if the worker is just entering Python.
        update(directory, cancel_requested=True, state='cancelled', finished_at=time.time())
        return {'job_id': job_id, 'state': 'cancelled', 'changed': True}
    if not value.get('worker_identity') or identity(pid) != value['worker_identity']:
        return {**status(job_id), 'changed': False}
    # The worker polls this durable request, including before solver startup.
    # Avoid SIGTERM before Python has installed its handler for a newly queued job.
    update(directory, cancel_requested=True)
    return {'job_id': job_id, 'state': 'cancel_requested', 'changed': True,
            'next': '查询到cancelled后确认取消完成；已完成作业不会重新执行。'}


def run_worker(job_id: str) -> None:
    directory = job_directory(job_id)
    value = read(directory)
    cfg = value['runtime']
    work = directory / 'work'
    stopped = threading.Event()
    def request_stop(_signal, _frame):
        stopped.set()
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    update(directory, worker_pid=os.getpid(), worker_identity=identity(os.getpid()),
           state='running', started_at=time.time())
    process = None
    process_tree = None
    cleanup = None
    try:
        if read(directory).get('cancel_requested'):
            update(directory, state='cancelled', finished_at=time.time(), exit_code=None)
            return
        arguments = ['--threads', str(value['threads'])]
        if value['mode'] == 'plot':
            arguments.append('-p')
        elif value['mode'] == 'geometry-debug':
            arguments.append('-g')
        if (work / 'model.xml').exists():
            arguments.append('model.xml')
        invocation = command(cfg, work, arguments=arguments)
        if value.get('example_workflow'):
            workflow = value['example_workflow']
            invocation = cfg['python_command'] + [str(ROOT / 'mcp/official_runner.py'),
                workflow['example_id'], workflow['step'], '--parameters', json.dumps(workflow['parameters']),
                '--threads', str(value['threads'])]
        elif value['mode'] == 'deplete':
            invocation = cfg['python_command'] + [str(ROOT / 'mcp/deplete_runner.py')]
        from nuclear_data import runtime_env
        child_env = runtime_env(cfg)
        # Child runners and notebook kernels read the same immutable runtime
        # as this job; later administrator edits cannot switch its data library.
        snapshot = directory / 'runtime.json'
        atomic_json(snapshot, cfg)
        snapshot.chmod(0o600)
        child_env['MC_OPENMC_CONFIG'] = str(snapshot)
        child_env['OMP_NUM_THREADS'] = str(value['threads'])
        process_tree=OwnedProcessTree()
        with (directory / 'solver.log').open('wb') as output:
            process = subprocess.Popen(invocation, cwd=work, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                       start_new_session=True, env=child_env)
            update(directory,child_pid=process.pid,child_identity=identity(process.pid))
            output_size = 0
            truncated = False
            os.set_blocking(process.stdout.fileno(), False)
            selector = selectors.DefaultSelector()
            selector.register(process.stdout, selectors.EVENT_READ)
            deadline = time.monotonic() + cfg['job_timeout_seconds']
            timed_out = False
            group_stopped = False
            drain_deadline = None
            while selector.get_map() or process.poll() is None:
                if stopped.is_set() or read(directory).get('cancel_requested') or time.monotonic() > deadline:
                    timed_out = time.monotonic() > deadline
                    if not group_stopped:
                        cleanup=process_tree.stop(process,kill_runner=True)
                        group_stopped = True
                        if not cleanup['verified_empty']:break
                if process.poll() is not None:
                    if not group_stopped:
                        # Jupyter kernels create independent sessions. The worker
                        # adopts them and their solvers before sealing artifacts.
                        cleanup=process_tree.stop(process,kill_runner=False)
                        group_stopped = True
                        if not cleanup['verified_empty']:break
                    if drain_deadline is None:drain_deadline = time.monotonic() + 2
                    if time.monotonic() > drain_deadline:break
                for key, _mask in selector.select(timeout=0.1):
                    try:chunk = os.read(key.fileobj.fileno(), 4096)
                    except BlockingIOError:continue
                    if not chunk:
                        selector.unregister(key.fileobj); continue
                    remaining = max(0, cfg['max_log_bytes'] - output_size)
                    output.write(chunk[:remaining]); output.flush()
                    output_size += min(remaining, len(chunk))
                    truncated |= len(chunk) > remaining
            selector.close()
            if process.poll() is not None:process.wait()
            process.stdout.close()
        if cleanup is None:cleanup=process_tree.stop(process,kill_runner=False)
        if not cleanup['verified_empty']:
            update(directory,state='interrupted',exit_code=process.returncode,finished_at=time.time(),
                   process_cleanup=cleanup,error='子进程清理尚未确认；产物未固化，不可视为完成或取消成功')
            return
        cancelled = stopped.is_set() or read(directory).get('cancel_requested')
        state = 'cancelled' if cancelled else ('completed' if process.returncode == 0 and not timed_out else 'failed')
        unexpected_normal_exit=not cancelled and not timed_out and process.returncode==0 and bool(cleanup['unexpected_descendants'])
        if unexpected_normal_exit:state='failed'
        output_files = {str(p.relative_to(work)): file_hash(p) for p in work.rglob('*')
                        if p.is_file() and not p.is_symlink() and
                        (str(p.relative_to(work)) not in value['input_files'] or
                         file_hash(p) != value['input_files'][str(p.relative_to(work))])}
        update(directory, state=state, exit_code=process.returncode, finished_at=time.time(), output_files=output_files,
               log_truncated=truncated,process_cleanup=cleanup,
               error='程序退出后仍有后台子进程，已清理，不能报告完整计算' if unexpected_normal_exit else '超过配置的作业时限' if timed_out else (None if state in ('completed', 'cancelled') else '程序非零退出；查看log_tail或solver.log'))
    except Exception as exc:
        if process and process_tree:
            cleanup=process_tree.stop(process,kill_runner=True)
            if process.poll() is None and cleanup['verified_empty']:process.wait()
        unknown=cleanup is not None and not cleanup['verified_empty']
        update(directory,state='interrupted' if unknown else 'failed',finished_at=time.time(),
               process_cleanup=cleanup,
               error=('进程清理尚未确认，不能报告完成' if unknown else
                      str(exc) if isinstance(exc,ProcessControlError) else
                      '运行失败：'+type(exc).__name__+'；检查程序或输入依赖。'))


if __name__ == '__main__':
    run_worker(sys.argv[1])
