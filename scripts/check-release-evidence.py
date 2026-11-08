#!/usr/bin/env python3
"""Fail a release gate when critical real tests were skipped or absent."""
from __future__ import annotations
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CRITICAL = ROOT / 'config/release-critical.txt'


def markers(path=DEFAULT_CRITICAL):
    return [line.strip() for line in Path(path).read_text(encoding='utf-8').splitlines()
            if line.strip() and not line.lstrip().startswith('#')]


# unittest -v may interleave warnings/async diagnostics before a standalone
# terminal status. Prose mentioning an identifier is never an execution record.
RECORD = re.compile(r'^(test\w*|runTest) \(([\w.]+)\)(?: \.\.\. (.*))?$')
STATUS = re.compile(r"(?:^| \.\.\. )(ok|FAIL|ERROR|skipped(?: .*)?|expected failure|unexpected success)$")


def execution_records(text):
    records = []
    pending = None
    for line in text.splitlines():
        match = RECORD.fullmatch(line)
        if match:
            if pending is not None:
                records.append(pending)
            pending = {'name': match[1], 'qualified': match[2], 'status': None}
            tail = match[3] or ''
        else:
            tail = line
        status = STATUS.search(tail)
        if pending is not None and status:
            pending['status'] = status[1]
            records.append(pending)
            pending = None
    if pending is not None:
        records.append(pending)
    return records


def _matches(record, marker):
    return (record['name'] == marker or record['qualified'] == marker
            or record['qualified'].startswith(marker + '.'))


def evaluate(text, required):
    errors = []
    records = execution_records(text)
    if re.search(r'^(FAILED|ERROR)\b', text, re.M):
        errors.append('测试日志包含 FAILED/ERROR')
    if not re.search(r'^OK(?: \(skipped=\d+\))?$', text, re.M):
        errors.append('测试日志没有成功的 OK 汇总')
    if any(r['status'] is None for r in records):
        errors.append('测试执行记录缺少终态，日志可能被截断')
    if any(r['status'] in ('FAIL', 'ERROR', 'unexpected success') for r in records):
        errors.append('测试执行记录存在失败或非预期成功')
    counts = re.findall(r'^Ran (\d+) tests? in .*$', text, re.M)
    if not counts or len(counts) != 1 or int(counts[0]) != len(records):
        errors.append('执行记录数量与 Ran 汇总不一致或汇总缺失')
    for marker in required:
        matched = [r for r in records if _matches(r, marker)]
        if not matched:
            errors.append('关键测试未执行：' + marker)
        elif any((r['status'] or '').startswith('skipped') for r in matched):
            errors.append('关键测试被跳过：' + marker)
        elif any(r['status'] != 'ok' for r in matched):
            errors.append('关键测试未成功：' + marker)
    return errors


def main(argv):
    if len(argv) != 2:
        print('用法：python scripts/check-release-evidence.py <unittest日志>', file=sys.stderr)
        return 2
    log = Path(argv[1])
    if not log.is_file():
        print('发布门禁失败：找不到测试日志 ' + str(log), file=sys.stderr)
        return 2
    errors = evaluate(log.read_text(encoding='utf-8', errors='replace'), markers())
    if errors:
        print('发布门禁失败：', file=sys.stderr)
        for error in errors:
            print('- ' + error, file=sys.stderr)
        print('关键真实用例必须实际执行；skip 不计通过。', file=sys.stderr)
        return 1
    print('发布门禁通过：关键真实用例均已执行且未跳过。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
