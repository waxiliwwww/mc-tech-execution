#!/usr/bin/env python3
"""Same synthetic-MGXS example via the direct CLI or a standard MCP client. No LLM."""
import argparse
from demo_output import emit_result, output_option
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]


def error_text(error):
    nested = getattr(error, 'exceptions', None)
    return '; '.join(error_text(child) for child in nested) if nested else str(error)


@asynccontextmanager
async def client(url):
    if url:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        token = os.environ.get('MCP_AUTH_TOKEN')
        headers = {'Authorization': 'Bearer ' + token} if token else None
        async with streamablehttp_client(url, headers=headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                async def call(name, arguments):
                    result = await session.call_tool(name, arguments)
                    if result.isError:
                        raise RuntimeError(result.content[0].text)
                    return result.structuredContent or json.loads(result.content[0].text)
                yield call
    else:
        async def call(name, arguments):
            result = await asyncio.to_thread(subprocess.run,
                [sys.executable, str(ROOT / 'mcp/cli.py'), 'call', name, json.dumps(arguments)],
                capture_output=True, text=True, timeout=180)
            if result.returncode:
                raise RuntimeError(result.stderr.strip())
            return json.loads(result.stdout)
        yield call


async def demo(args):
    case_dir = args.case_dir or 'demos/synthetic-' + uuid.uuid4().hex[:10]
    async with client(args.mcp_url) as call:
        print('1/6 检查运行环境', file=sys.stderr)
        runtime = await call('openmc_environment', {})
        if not runtime['api_available'] or not runtime['executable_available'] or not runtime['version_matches']:
            raise RuntimeError('OpenMC运行环境尚未就绪：' + json.dumps(runtime, ensure_ascii=False))
        print('2/6 生成合成单群截面示例（不作专业验收）', file=sys.stderr)
        created = await call('openmc_create_model', {'case_dir': case_dir, 'template': 'synthetic-mgxs'})
        print('3/6 检查XML及公开API模型解析', file=sys.stderr)
        validation = await call('openmc_validate_inputs', {'case_dir': case_dir, 'api_check': True})
        if not validation['structure_valid']:
            raise RuntimeError('输入检查失败：' + json.dumps(validation, ensure_ascii=False))
        print('4/6 提交计算并查询终态', file=sys.stderr)
        submitted = await call('openmc_submit_job', {'case_dir': case_dir})
        job_id = submitted['job_id']
        deadline = time.monotonic() + args.timeout
        while True:
            status = await call('openmc_job_status', {'job_id': job_id})
            if status['state'] in ('completed', 'failed', 'cancelled', 'interrupted'):
                break
            if time.monotonic() > deadline:
                await call('openmc_cancel_job', {'job_id': job_id})
                raise RuntimeError('示例等待超时，已请求取消；请继续查询作业：' + job_id)
            await asyncio.sleep(0.2)
        if status['state'] != 'completed':
            raise RuntimeError('作业未成功：' + status['state'] + '；job_id=' + job_id + '；查询openmc_job_status查看日志和原因')
        print('5/6 读取真实求解器的statepoint', file=sys.stderr)
        results = await call('openmc_read_results', {'job_id': job_id})
        print('6/6 导出Markdown及CSV', file=sys.stderr)
        reports = [await call('openmc_export_report', {'job_id': job_id, 'format': format})
                   for format in ('markdown', 'csv')]
        artifacts = await call('openmc_list_artifacts', {'kind': 'job', 'identifier': job_id})
        return {'artifacts': artifacts, 'case': created, 'runtime': runtime, 'validation': validation, 'job': status,
                'results': results, 'reports': reports, 'professional_acceptance': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mcp-url', help='省略时走直接CLI；提供时用标准MCP客户端')
    parser.add_argument('--case-dir', help='服务端工作区内的新目录；默认随机名，避免覆盖')
    parser.add_argument('--timeout', type=float, default=120)
    output_option(parser)
    args = parser.parse_args()
    try:
        emit_result(asyncio.run(demo(args)), args, '本地计算执行结果')
    except Exception as error:
        print('示例失败：' + error_text(error), file=sys.stderr)
        sys.exit(1)
