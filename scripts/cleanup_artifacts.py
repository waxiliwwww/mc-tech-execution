#!/usr/bin/env python3
"""Preview or remove finished internal records during an offline maintenance window."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
SAFE_STATES = {'completed', 'failed', 'cancelled', 'partial'}
IDENTIFIER = re.compile(r'[a-f0-9]{32}')


class CleanupError(ValueError):
    pass


def read_json(path):
    if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode) or path.stat().st_size > 16 * 1024 * 1024:
        raise CleanupError('拒绝符号链接、特殊文件或过大记录：' + str(path))
    try:
        value = json.loads(path.read_text())
        if not isinstance(value, dict):raise ValueError
        return value
    except (OSError, ValueError) as error:
        raise CleanupError('记录损坏，不能判断清理边界：' + str(path)) from error


def process_identity(pid):
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        if fields[0] == 'Z':return None
        return Path('/proc/sys/kernel/random/boot_id').read_text().strip() + ':' + fields[19]
    except FileNotFoundError:return None
    except (OSError, IndexError) as error:raise CleanupError('无法核对进程状态：' + str(pid)) from error


def inventory(workspace):
    records = {}
    for kind, folder in [('job', '.mc-jobs'), ('scan', '.mc-scans')]:
        base = workspace / folder
        if base.is_symlink():raise CleanupError('内部目录不能是符号链接：' + str(base))
        if not base.exists():continue
        if not base.is_dir():raise CleanupError('内部记录目录无效：' + str(base))
        for target in sorted(base.iterdir()):
            if target.is_symlink():raise CleanupError('内部记录包含符号链接：' + str(target))
            if not target.is_dir():continue
            if not IDENTIFIER.fullmatch(target.name):raise CleanupError('记录目录编号无效：' + str(target))
            record = read_json(target / 'job.json')
            if record.get(kind + '_id') != target.name:raise CleanupError('记录编号与目录不一致：' + str(target))
            records[(kind, target.name)] = {'kind': kind, 'path': target, 'record': record}
    return records


def fingerprint(target):
    """Do not follow links, including links added inside an otherwise valid record."""
    digest = hashlib.sha256()
    size = 0
    for path in [target, *sorted(target.rglob('*'))]:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise CleanupError('拒绝符号链接或特殊文件：' + str(path))
        digest.update(str(path.relative_to(target)).encode())
        digest.update(f':{info.st_ino}:{info.st_size}:{info.st_mtime_ns}'.encode())
        if stat.S_ISREG(info.st_mode):size += info.st_size
    return digest.hexdigest(), size


def safe_record(item, now):
    record = item['record']
    allowed = SAFE_STATES if item['kind'] == 'scan' else SAFE_STATES - {'partial'}
    if record.get('state') not in allowed:
        raise CleanupError('记录未确认结束（interrupted也不能自动清理）：' + str(item['path']))
    finished = record.get('finished_at')
    if type(finished) not in (int, float) or not math.isfinite(finished) or not 0 < finished <= now:
        raise CleanupError('缺少有效完成时间：' + str(item['path']))
    pid, expected = record.get('worker_pid'), record.get('worker_identity')
    if type(pid) is not int or pid <= 0 or not isinstance(expected, str) or not expected:
        raise CleanupError('缺少工作进程身份记录，拒绝推断已经退出：' + str(item['path']))
    current = process_identity(pid)
    if current is not None and current == expected:
        raise CleanupError('工作进程仍在运行：' + str(item['path']))
    if record.get('active_job_id'):
        raise CleanupError('扫描仍有未核对的active_job_id：' + str(item['path']))


def plan(workspace, job_ids, scan_ids, older_than_days=None, allow_repair_references=False):
    now = time.time()
    records = inventory(workspace)
    selected = {('job', value) for value in job_ids} | {('scan', value) for value in scan_ids}
    for kind, identifier in selected:
        if not IDENTIFIER.fullmatch(identifier):raise CleanupError('编号须为32位小写十六进制')
        if (kind, identifier) not in records:raise CleanupError('记录不存在：' + identifier)
    skipped = []
    if older_than_days is not None:
        for key, item in records.items():
            try:safe_record(item, now)
            except CleanupError as error:
                skipped.append(str(error));continue
            if now - item['record']['finished_at'] >= older_than_days * 86400:selected.add(key)
    references = {}
    for (kind, identifier), item in records.items():
        if kind != 'scan':continue
        rows = item['record'].get('cases')
        if not isinstance(rows, list):raise CleanupError('扫描关联记录无效：' + identifier)
        children = []
        for row in rows:
            if not isinstance(row, dict):raise CleanupError('扫描子项损坏：' + identifier)
            child = row.get('job_id')
            if child is None:continue
            if not isinstance(child, str) or not IDENTIFIER.fullmatch(child):raise CleanupError('扫描关联作业编号无效')
            references.setdefault(child, set()).add(identifier)
            children.append(child)
        if ('scan', identifier) in selected:
            selected.update(('job', child) for child in children)
    # An age filter must never silently break an otherwise retained scan.
    for kind, identifier in list(selected):
        if kind == 'job' and any(('scan', parent) not in selected for parent in references.get(identifier, ())):
            if identifier in job_ids or any(('scan', parent) in selected for parent in references.get(identifier, ())):
                raise CleanupError('作业仍被扫描引用，请选择全部相关扫描：' + identifier)
            selected.remove((kind, identifier));skipped.append('保留仍被扫描引用的作业：' + identifier)
    repair_references = []
    plans = workspace / '.mc-plans'
    if plans.is_symlink():raise CleanupError('修复计划目录不能是符号链接')
    if plans.exists():
        for path in sorted(plans.glob('*.json')):
            value = read_json(path)
            context = value.get('context', {})
            if not isinstance(context, dict):raise CleanupError('修复关联记录损坏：' + str(path))
            for key in ('origin_job_id', 'rerun_job_id'):
                identifier = context.get(key)
                if identifier is not None and (not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier)):
                    raise CleanupError('修复关联作业编号无效：' + str(path))
            matched = [key for key in ('origin_job_id', 'rerun_job_id') if ('job', context.get(key)) in selected]
            if matched:repair_references.append({'plan_id':value.get('plan_id', path.stem),'state':value.get('state'),
                                                  'jobs':[context[key] for key in matched]})
    if repair_references and not allow_repair_references:
        raise CleanupError('所选作业被修复计划引用：' + json.dumps(repair_references, ensure_ascii=False) +
                           '；归档并接受无法继续追溯/重验后，可显式加--allow-repair-references。计划和备份仍保留。')
    entries = []
    for key in sorted(selected, key=lambda value: (value[0] == 'scan', value[1])):
        if key not in records:raise CleanupError('扫描引用的作业记录缺失，拒绝部分清理：' + key[1])
        item = records[key];safe_record(item, now)
        stamp, size = fingerprint(item['path'])
        entries.append({'kind':key[0],'identifier':key[1],'path':str(item['path']),
                        'state':item['record']['state'],'bytes':size,'fingerprint':stamp})
    return {'workspace':str(workspace),'entries':entries,'bytes':sum(entry['bytes'] for entry in entries),
            'repair_references':repair_references,'skipped':skipped}


def apply_plan(value, refresh):
    """All preflight checks happen before the first deletion; failures are not a rollback."""
    removed, pending = [], [entry['path'] for entry in value['entries']]
    workspace = Path(value['workspace'])
    if (workspace / '.mc-cleanup.lock').is_symlink():raise CleanupError('清理锁不能是符号链接')
    with (workspace / '.mc-cleanup.lock').open('a') as lock:
        try:fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:raise CleanupError('已有清理进程，稍后再试') from error
        latest = refresh()
        if latest != value:raise CleanupError('预览后记录/关联/产物改变，未删除；重新预览')
        records = inventory(workspace)
        if any(item['record'].get('state') not in (SAFE_STATES if item['kind'] == 'scan' else SAFE_STATES - {'partial'})
               for item in records.values()):
            raise CleanupError('工作区还有运行、排队或未确认结束的记录；先处理计算，不只停止MCP服务')
        with ExitStack() as stack:
            for entry in value['entries']:
                target = Path(entry['path'])
                for name in ('record.lock', 'report.lock'):
                    path = target / name
                    if not path.exists():continue
                    if path.is_symlink():raise CleanupError('记录锁不能是符号链接')
                    handle = stack.enter_context(path.open('a'))
                    try:fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError as error:raise CleanupError('记录仍在使用，未删除：' + str(target)) from error
            if refresh() != value:raise CleanupError('取得记录锁后发现内容变化，未删除；重新预览')
            for entry in value['entries']:
                target = Path(entry['path'])
                try:shutil.rmtree(target)
                except OSError as error:
                    return {'state':'partial_failure','removed':removed,'not_removed_or_partial':pending,
                            'error':str(error),'note':'已删除的内容不会自动回滚；失败目录可能已部分删除。'}
                removed.append(str(target));pending.remove(str(target))
    return {'state':'deleted','removed':removed,'not_removed_or_partial':[]}


def main():
    parser = argparse.ArgumentParser(description='维护窗口清理内部计算/扫描记录；默认仅预览，不删除原算例目录')
    parser.add_argument('--workspace', default=os.environ.get('MC_WORKSPACE', str(ROOT / 'workspace')))
    parser.add_argument('--job-id', action='append', default=[])
    parser.add_argument('--scan-id', action='append', default=[])
    parser.add_argument('--older-than-days', type=float)
    parser.add_argument('--allow-repair-references', action='store_true', help='接受修复关联作业被删后无法继续查询/重验；不删除计划和备份')
    parser.add_argument('--apply', action='store_true', help='确认已停止服务及其他CLI写操作，永久删除列出的内部记录')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    try:
        raw = Path(args.workspace).absolute()
        if raw.is_symlink() or raw == Path('/'):raise CleanupError('工作区不能为符号链接或系统根目录')
        workspace = raw.resolve(strict=True)
        if not workspace.is_dir():raise CleanupError('工作区须为目录')
        if args.older_than_days is not None and (not math.isfinite(args.older_than_days) or args.older_than_days < 0):
            raise CleanupError('older-than-days须为非负有限天数')
        selected = bool(args.job_id or args.scan_id or args.older_than_days is not None)
        if args.apply and not selected:raise CleanupError('--apply必须显式指定编号或older-than-days')
        refresh = lambda: plan(workspace, args.job_id, args.scan_id, args.older_than_days, args.allow_repair_references)
        value = refresh()
        if not selected:
            value['inventory'] = [{'kind':kind,'identifier':identifier,'state':item['record'].get('state')}
                                  for (kind,identifier),item in inventory(workspace).items()]
        result = {'state':'preview','plan':value}
        if args.apply:result.update(apply_plan(value, refresh))
        if args.json:print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(('清理预览（未删除）' if not args.apply else '清理结果：' + result['state']) + '\n工作区：' + str(workspace))
            for entry in value['entries']:print(f"  {entry['kind']} {entry['identifier']}  {entry['bytes']} bytes\n    {entry['path']}")
            for item in value.get('inventory', []):print(f"  {item['kind']} {item['identifier']}  {item['state']}")
            for warning in value['skipped']:print('保留：' + warning)
            if value['repair_references']:print('修复关联将失去作业查询/重验能力：' + json.dumps(value['repair_references'], ensure_ascii=False))
            if result.get('error'):
                print('删除失败：' + result['error'])
                print('已删除：' + json.dumps(result['removed'], ensure_ascii=False))
                print('未删或可能部分删除：' + json.dumps(result['not_removed_or_partial'], ensure_ascii=False))
            print('原算例、配置、知识库与外部核数据库保持；记录目录内输入快照和数据副本随记录删除。')
            if not args.apply:print('核对后，在停止服务及其他CLI写操作的维护窗口，用相同选择加 --apply 执行。')
        return 1 if result['state']=='partial_failure' else 0
    except (CleanupError, OSError) as error:
        message = {'state':'refused','error':str(error)}
        print(json.dumps(message, ensure_ascii=False) if args.json else '未完成清理：' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':raise SystemExit(main())
