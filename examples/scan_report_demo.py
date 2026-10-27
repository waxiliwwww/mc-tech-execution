#!/usr/bin/env python3
"""Run a real synthetic-data parameter scan via CLI or standard MCP, then plot and report."""
import argparse
from demo_output import emit_result, output_option
import asyncio
import json
import sys
import time
import uuid

from openmc_demo import client, error_text


async def demo(args):
    async with client(args.mcp_url) as call:
        name = args.case_dir or 'demos/scan-' + uuid.uuid4().hex[:10]
        print('1/5 创建独立合成截面示例并发现可用参数', file=sys.stderr)
        created = await call('openmc_create_model', {'case_dir': name})
        parameters = await call('openmc_list_parameters', {'case_dir': name})
        density = next(p for p in parameters['parameters'] if p['name'].endswith('.density'))
        print('2/5 提交三个密度倍率组合（macro单位，不代表真实材料密度）', file=sys.stderr)
        submitted = await call('openmc_submit_scan', {'case_dir': name, 'axes': {density['name']: [0.8, 1.0, 1.2]}})
        scan_id = submitted['scan_id']; deadline = time.monotonic() + args.timeout
        while True:
            status = await call('openmc_scan_status', {'scan_id': scan_id})
            if status['state'] in ('completed', 'partial', 'failed', 'cancelled', 'interrupted'):break
            if time.monotonic() > deadline:
                await call('openmc_cancel_scan', {'scan_id': scan_id})
                raise RuntimeError('等待超时，已请求取消，请继续查询：' + scan_id)
            await asyncio.sleep(.3)
        if status['state'] != 'completed':raise RuntimeError('扫描未全部成功：' + status['state'] + '；scan_id=' + scan_id + '；查询openmc_scan_status查看逐项原因')
        print('3/5 绘制flux扫描曲线及单个结果图', file=sys.stderr)
        scan_plot = await call('openmc_plot_scan', {'scan_id': scan_id, 'x_parameter': density['name'], 'metric': 'tally', 'score': 'flux'})
        job_id = status['cases'][0]['job_id']
        result_plot = await call('openmc_plot_results', {'job_id': job_id, 'score': 'flux'})
        print('4/5 导出报告：' + ', '.join(args.formats), file=sys.stderr)
        reports = [await call('openmc_export_scan_report', {'scan_id': scan_id, 'format': f}) for f in args.formats]
        print('5/5 列出服务端产物', file=sys.stderr)
        artifacts = await call('openmc_list_artifacts', {'kind': 'scan', 'identifier': scan_id})
        return {'case': created, 'parameter': density, 'scan': status, 'scan_plot': scan_plot,
                'result_plot': result_plot, 'reports': reports, 'artifacts': artifacts, 'professional_acceptance': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mcp-url')
    parser.add_argument('--case-dir')
    parser.add_argument('--timeout', type=float, default=180)
    parser.add_argument('--formats', nargs='+', choices=['markdown', 'csv', 'docx', 'pdf', 'doc'],
                        default=['markdown', 'csv', 'docx', 'pdf', 'doc'])
    output_option(parser)
    args = parser.parse_args()
    try:emit_result(asyncio.run(demo(args)), args, '参数扫描执行结果')
    except Exception as error:
        print('扫描示例失败：' + error_text(error), file=sys.stderr); sys.exit(1)
