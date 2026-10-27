#!/usr/bin/env python3
"""Run a registered official OpenMC example over CLI or MCP; no LLM or private data."""
import argparse
from demo_output import emit_result, output_option
import asyncio
import json
import sys
import time
import uuid

from openmc_demo import client, error_text


async def wait_job(call, identifier, timeout):
    deadline = time.monotonic() + timeout
    while True:
        status = await call('openmc_job_status', {'job_id': identifier})
        if status['state'] in ('completed', 'failed', 'cancelled', 'interrupted'):return status
        if time.monotonic() > deadline:
            await call('openmc_cancel_job', {'job_id': identifier})
            raise RuntimeError('等待超时，已请求取消；继续查询计算编号：' + identifier)
        await asyncio.sleep(.2)


async def demo(args):
    async with client(args.mcp_url) as call:
        print('1/5 查询官方参数资料和例题说明', file=sys.stderr)
        knowledge = await call('search_knowledge', {'query': '粒子数'})
        definition = await call('get_case', {'case_id': 'openmc-pincell-mg'})
        print('2/5 生成官方多群燃料棒例题（公开C5G7数据）', file=sys.stderr)
        name = args.case_dir or 'demos/public-mg-' + uuid.uuid4().hex[:10]
        case = await call('openmc_prepare_example', {'case_id': 'openmc-pincell-mg', 'case_dir': name})
        validation = await call('openmc_validate_inputs', {'case_dir': name, 'api_check': True})
        print('3/5 真实计算并查询终态', file=sys.stderr)
        submitted = await call('openmc_submit_job', {'case_dir': name})
        status = await wait_job(call, submitted['job_id'], args.timeout)
        if status['state'] != 'completed':raise RuntimeError('官方例题计算未成功：' + status['state'] + '；job_id=' + submitted['job_id'] + '；查询openmc_job_status')
        print('4/5 提取结果并绘制能谱计分', file=sys.stderr)
        result = await call('openmc_read_results', {'job_id': submitted['job_id']})
        tally = next(t for t in result['tallies'] if t['name'] == 'Flux spectrum')
        chart = await call('openmc_plot_results', {'job_id': submitted['job_id'], 'tally_id': tally['id'], 'score': 'flux'})
        print('5/5 导出报告和服务端清单', file=sys.stderr)
        report = await call('openmc_export_report', {'job_id': submitted['job_id'], 'format': args.report_format})
        artifacts = await call('openmc_list_artifacts', {'kind': 'job', 'identifier': submitted['job_id']})
        return {'knowledge': knowledge, 'definition': definition, 'case': case, 'validation': validation,
                'job': status, 'results': result, 'chart': chart, 'report': report, 'artifacts': artifacts,
                'professional_acceptance': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mcp-url')
    parser.add_argument('--case-dir')
    parser.add_argument('--timeout', type=float, default=120)
    parser.add_argument('--report-format', choices=['markdown', 'csv', 'docx', 'pdf', 'doc'], default='markdown')
    output_option(parser)
    args = parser.parse_args()
    try:emit_result(asyncio.run(demo(args)), args, '公开例题执行结果')
    except Exception as error:
        print('公开例题失败：' + error_text(error), file=sys.stderr); sys.exit(1)
