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


def evaluate(text, required):
    errors = []
    if re.search(r'^(FAILED|ERROR)\b', text, re.M):
        errors.append('测试日志包含 FAILED/ERROR')
    if not re.search(r'^OK(?: \(skipped=.*\))?', text, re.M):
        errors.append('测试日志没有成功的 OK 汇总')
    for marker in required:
        lines = [line for line in text.splitlines() if marker in line]
        if not lines:
            errors.append('关键测试未执行：' + marker)
        elif any('skipped' in line for line in lines):
            errors.append('关键测试被跳过：' + marker)
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
