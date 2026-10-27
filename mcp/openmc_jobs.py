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
            process = subprocess.Popen([sys.executable, str(ROOT / 'mcp' / script), identifier],
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                       start_new_session=True,
                                       env={**os.environ, 'MC_WORKSPACE': str(workspace())})
        update(directory, worker_pid=process.pid, worker_identity=identity(process.pid))
        threading.Thread(target=process.wait, daemon=True).start()
    except OSError as exc:
        update(directory, state='failed', error='计算监控进程启动失败', finished_at=time.time())
        raise Error('计算监控进程启动失败') from exc


def submit(case_dir: str, mode: str = 'run', threads: int = 1, _runtime: dict | None = None,
           _internal: bool = False, _job_id: str | None = None) -> dict:
    if not sys.platform.startswith('linux'):
        raise Error('本地持久作业目前支持Linux/WSL；其他系统请在Linux服务器部署MCP')
    if mode not in ('run', 'plot', 'geometry-debug') or type(threads) is not int or not 1 <= threads <= 64:
        raise Error('mode须为run/plot/geometry-debug；threads须为1..64')
    source = path_in_workspace(case_dir, internal=_internal)
    if not source.is_dir() or source == workspace():
        raise Error('case_dir须为工作区内的具体算例子目录')
    cfg = _runtime or config()
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
    shutil.copytree(source, copied, ignore=lambda _directory, names: [n for n in names if is_old_output(n)])
    input_files = {str(p.relative_to(copied)): file_hash(p) for p in copied.rglob('*') if p.is_file()}
    record = {'job_id': job_id, 'state': 'queued', 'submitted_at': time.time(),
              'case_dir': case_dir, 'mode': mode, 'threads': threads,
              'provenance': provenance(source, cfg), 'runtime': cfg,
              'work_dir': relative(copied), 'input_files': input_files}
    atomic_json(directory / 'job.json', record)
    spawn_worker(directory, 'openmc_jobs.py', job_id)
    return {'job_id': job_id, 'state': read(directory)['state'],
            'provenance': record['provenance'], 'next': '使用openmc_job_status查询，提交不等于计算完成。'}


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
    result = {key: value[key] for key in ('job_id', 'state', 'mode', 'case_dir', 'submitted_at',
              'started_at', 'finished_at', 'exit_code', 'error', 'provenance', 'log_truncated') if key in value}
    result['log_tail'] = tail
    result['input_files'] = sorted(value.get('input_files', {}))
    result['files'] = sorted(value.get('output_files', {}))
    return result


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
        with (directory / 'solver.log').open('wb') as output:
            process = subprocess.Popen(invocation, cwd=work, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                       start_new_session=True)
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
                        try:os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:pass
                        group_stopped = True
                if process.poll() is not None:
                    if not group_stopped:
                        # A wrapper may exit while its children still hold stdout.
                        try:os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:pass
                        group_stopped = True
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
            process.wait()
            process.stdout.close()
        cancelled = stopped.is_set() or read(directory).get('cancel_requested')
        state = 'cancelled' if cancelled else ('completed' if process.returncode == 0 and not timed_out else 'failed')
        output_files = {str(p.relative_to(work)): file_hash(p) for p in work.rglob('*')
                        if p.is_file() and not p.is_symlink() and str(p.relative_to(work)) not in value['input_files']}
        update(directory, state=state, exit_code=process.returncode, finished_at=time.time(), output_files=output_files,
               log_truncated=truncated,
               error='超过配置的作业时限' if timed_out else (None if state in ('completed', 'cancelled') else '程序非零退出；查看log_tail或solver.log'))
    except Exception as exc:
        if process and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        update(directory, state='failed', finished_at=time.time(),
               error='运行失败：' + type(exc).__name__ + '；检查程序或输入依赖。')


if __name__ == '__main__':
    run_worker(sys.argv[1])
