#!/usr/bin/env python3
"""A real failure, reviewed diff, terminal confirmation, then explicit validation/recalculation."""
import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import uuid

from openmc_demo import ROOT, client, error_text
from public_case_demo import wait_job


async def confirmed_apply(call, plan, url):
    identifier = plan['plan_id']
    if url:
        preview = await call('read_repair_plan', {'plan_id': identifier})
        print(preview['diff'], file=sys.stderr)
        if not sys.stdin.isatty():raise RuntimeError('非交互输入不能代替人工确认；本演示没有--yes或confirmed参数')
        print('只应用上述差异，之后重新校核/计算。确认修改？[y/N] ', end='', file=sys.stderr, flush=True)
        answer = await asyncio.to_thread(sys.stdin.readline)
        if answer.strip().lower() not in ('y', 'yes'):return {'state': 'declined'}
        current = await call('read_repair_plan', {'plan_id': identifier})
        if current != preview:raise RuntimeError('确认期间计划/文件改变，未执行；请重新预览')
        return await call('apply_repair', {'plan_id': identifier})
    # The direct CLI owns its confirmation. Inherit stdin/stderr so the user sees
    # the actual diff and prompt; capture only the machine-readable JSON stdout.
    result = await asyncio.to_thread(subprocess.run,
        [sys.executable, str(ROOT / 'mcp/cli.py'), 'call', 'apply_repair', json.dumps({'plan_id': identifier})],
        stdout=subprocess.PIPE, text=True, timeout=300)
    if result.returncode:
        current = await call('read_repair_plan', {'plan_id': identifier})
        if current['state'] == 'proposed' and current['file_unchanged']:return {'state': 'declined_or_confirmation_unavailable'}
        raise RuntimeError('应用响应未确认；当前计划状态：' + json.dumps(current, ensure_ascii=False))
    return json.loads(result.stdout)


async def demo(args):
    name = args.case_dir or 'demos/repair-' + uuid.uuid4().hex[:10]
    async with client(args.mcp_url) as call:
        print('1/7 生成故意含运行模式拼写错误的合成工程练习', file=sys.stderr)
        case = await call('openmc_prepare_example', {'case_id': 'run-mode-typo', 'case_dir': name})
        print('2/7 提交真实求解器并保留首次失败', file=sys.stderr)
        first = await call('openmc_submit_job', {'case_dir': name})
        failed = await wait_job(call, first['job_id'], args.timeout)
        if failed['state'] != 'failed':raise RuntimeError('练习没有产生预期真实失败，停止，不伪造诊断')
        print('3/7 按真实日志/输入诊断，附官方依据', file=sys.stderr)
        diagnosis = await call('openmc_diagnose_job', {'job_id': first['job_id']})
        print('4/7 复制新算例并展示修复差异，原失败快照保持', file=sys.stderr)
        prepared = await call('openmc_prepare_repair', {'job_id': first['job_id'], 'target_dir': name + '-repaired'})
        print('5/7 等待终端用户确认', file=sys.stderr)
        applied = await confirmed_apply(call, prepared['plan'], args.mcp_url)
        if applied['state'] != 'applied':
            return {'outcome': 'not_applied', 'case': case, 'failed_job': failed, 'diagnosis': diagnosis,
                    'prepared': prepared, 'application': applied, 'rerun': None, 'professional_acceptance': False}
        print('6/7 显式校核并提交关联重算', file=sys.stderr)
        checked = await call('openmc_recheck_repair', {'plan_id': prepared['plan']['plan_id']})
        if not checked['job']:raise RuntimeError('修复后校核仍未通过：' + json.dumps(checked, ensure_ascii=False))
        finished = await wait_job(call, checked['job']['job_id'], args.timeout)
        if finished['state'] != 'completed':raise RuntimeError('重算仍失败，保留新旧编号：' + json.dumps(finished, ensure_ascii=False))
        print('7/7 读取新计算结果并导出报告', file=sys.stderr)
        result = await call('openmc_read_results', {'job_id': finished['job_id']})
        report = await call('openmc_export_report', {'job_id': finished['job_id'], 'format': 'markdown'})
        return {'outcome': 'recalculation_completed', 'case': case, 'failed_job': failed, 'diagnosis': diagnosis,
                'prepared': prepared, 'application': applied, 'recheck': checked, 'rerun': finished,
                'results': result, 'report': report, 'professional_acceptance': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mcp-url')
    parser.add_argument('--case-dir')
    parser.add_argument('--timeout', type=float, default=120)
    try:print(json.dumps(asyncio.run(demo(parser.parse_args())), ensure_ascii=False, indent=2, allow_nan=False))
    except Exception as error:
        print('修复演示未完成：' + error_text(error), file=sys.stderr); sys.exit(1)
