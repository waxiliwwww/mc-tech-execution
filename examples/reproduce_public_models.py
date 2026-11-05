#!/usr/bin/env python3
"""Official model adapters over standard MCP, with an incremental local receipt.

No LLM. Executing this script DOES submit an OpenMC job to the server.
Read docs/reproduce.html first. This is an engineering reproduction, not physics acceptance.
"""
import argparse
import asyncio
import json
import math
from pathlib import Path
import sys
import time
import uuid

from model_workflow import safe_value, save_receipt, verified_preview_hashes
from openmc_demo import ToolCallError, client, error_text

MODELS = {
    'assembly': {'template': 'assembly-17x17', 'dimension': [17, 17, 1],
                 'lower_left': [-10.71, -10.71, -1.0], 'upper_right': [10.71, 10.71, 1.0]},
    'core': {'template': 'core-lattice', 'dimension': [8, 8, 1],
             'lower_left': [-200.0, -200.0, -1.0], 'upper_right': [200.0, 200.0, 1.0]},
}


def require_validation(value):
    semantic = value.get('semantic', {})
    if (value.get('structure_valid') is not True
            or value.get('openmc_api', {}).get('api_parsed') is not True
            or any(semantic.get(key) is not True for key in ('checked', 'ok', 'complete'))):
        raise RuntimeError('校核未完整通过；查看receipt中的validation，不提交计算')


async def reproduce(args, call):
    model = MODELS[args.model]
    record = {'state': 'running', 'stage': 'start', 'case_dir': args.case_dir,
              'model': args.model, 'calls': [], 'job_id': None,
              'professional_acceptance': False, 'checks': {}, 'warnings': []}
    def save():
        save_receipt(args, record)
    async def step(name, parameters):
        record['stage'] = name
        if name != 'openmc_job_status':
            print('调用：' + name, file=sys.stderr)
        entry = {'tool': name, 'arguments': parameters, 'state': 'calling'}
        record['calls'].append(entry)
        save()  # Side-effect calls with a lost response remain explicitly unknown.
        try:
            value = await call(name, parameters)
        except Exception as exc:
            entry['state'] = 'tool_error' if isinstance(exc, ToolCallError) else 'response_unknown'
            entry['error'] = safe_value(error_text(exc))
            save()
            raise
        entry['state'] = 'returned'; entry['result'] = value
        save()
        return value
    try:
        runtime = await step('openmc_environment', {})
        if any(runtime.get(key) is not True for key in ('api_available', 'executable_available', 'version_matches')):
            raise RuntimeError('服务端OpenMC环境未就绪；先由管理员完成环境准备')
        await step('openmc_create_model', {'case_dir': args.case_dir, 'template': model['template'],
                   'particles': args.particles, 'batches': args.batches, 'inactive': args.inactive})
        tally = await step('openmc_define_tally', {'case_dir': args.case_dir, 'scores': ['flux'],
                           'name': 'reproduction flux', 'filters': [{'type': 'mesh',
                               **{key: model[key] for key in ('dimension', 'lower_left', 'upper_right')}}]})
        tally_id = tally['tally_id']
        record['tally_id'] = tally_id; save()
        preview = await step('openmc_preview_input', {'case_dir': args.case_dir})
        expected = verified_preview_hashes(preview)
        validation = await step('openmc_validate_inputs', {'case_dir': args.case_dir,
                                'api_check': True, 'semantic_check': True})
        require_validation(validation)
        submitted = await step('openmc_submit_job', {'case_dir': args.case_dir,
                               'name': 'official-adapter-' + args.model, 'threads': args.threads})
        record['job_id'] = submitted['job_id']; save()
        deadline = time.monotonic() + args.timeout
        while True:
            status = await step('openmc_job_status', {'job_id': record['job_id']})
            if status['state'] in ('completed', 'failed', 'cancelled', 'interrupted'):
                break
            if time.monotonic() >= deadline:
                await step('openmc_cancel_job', {'job_id': record['job_id']})
                raise RuntimeError('等待超时，已请求取消；按原job_id继续查询终态，不自动重投')
            await asyncio.sleep(2)
        if status['state'] != 'completed':
            raise RuntimeError('作业终态=' + status['state'] + '；查看原作业日志，不自动重投')
        if expected != status.get('input_hashes', {}):
            raise RuntimeError('提交快照与所预览输入不一致或缺指纹；停止结果归属判断')
        record['checks']['preview_matches_snapshot'] = True
        results = await step('openmc_read_results', {'job_id': record['job_id'], 'max_values': 10000})
        chosen = next((t for t in results['tallies'] if t['id'] == tally_id), None)
        expected_points = math.prod(model['dimension'])
        if (not chosen or chosen.get('values_truncated') or chosen.get('scores') != ['flux']
                or chosen.get('nuclides') != ['total'] or len(chosen['mean']) != expected_points
                or len(chosen['std_dev']) != expected_points):
            raise RuntimeError('实际tally选择/点数与新增网格不符；不静默换tally，查看receipt')
        record['checks']['tally_points'] = expected_points
        record['warnings'] = results.get('warnings', [])
        if any(v is None for v in chosen['mean'] + chosen['std_dev']):
            record['warnings'].append('存在空统计量，工程产物可继续生成，科学结果需重新评估采样条件。')
        selection = {'job_id': record['job_id'], 'tally_id': tally_id, 'score': 'flux'}
        await step('openmc_plot_mesh', {**selection, 'format': 'png'})
        await step('openmc_plot_results', {**selection, 'nuclide': 'total', 'filter_selection': [], 'format': 'png'})
        report = await step('openmc_export_report', {**selection, 'nuclide': 'total',
                            'filter_selection': [], 'format': 'markdown'})
        expected_selection = {'tally_id': tally_id, 'score': 'flux', 'nuclide': 'total', 'filter_selection': []}
        if (report.get('selection_mode') != 'selected' or report.get('points') != expected_points
                or report.get('selection') != expected_selection):
            raise RuntimeError('报告未沿用所选完整结果；保留已有产物并停止')
        artifacts = await step('openmc_list_artifacts', {'kind': 'job', 'identifier': record['job_id']})
        record['artifact_directory'] = artifacts['directory']
        record['checks']['file_data_crosscheck'] = 'pending_manual_steps_in_docs/reproduce.html'
        record['stage'] = 'completed'; record['state'] = 'completed'; save()
        return record
    except Exception as exc:
        record['state'] = 'failed'; record['error'] = safe_value(error_text(exc)); save()
        raise RuntimeError(safe_value('阶段=' + record['stage'] + '；case_dir=' + args.case_dir
                           + '；job_id=' + str(record['job_id']) + '；记录=' + str(Path(args.receipt).resolve())
                           + '；' + record['error'])) from exc


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mcp-url', required=True, help='已运行的标准MCP地址；不会启动服务或调用模型')
    parser.add_argument('--model', choices=MODELS, default='assembly', help='assembly=官方17×17组件；core=官方简化全堆')
    parser.add_argument('--case-dir', help='服务端工作区内的新目录；省略生成唯一目录，不覆盖旧输入')
    parser.add_argument('--receipt', help='客户端增量JSON路径；省略写到本包artifacts/reproduction，拒绝覆盖已有记录')
    parser.add_argument('--particles', type=int, default=1000, help='每代粒子数；默认1000用于工程练习')
    parser.add_argument('--batches', type=int, default=12, help='总批数；默认12')
    parser.add_argument('--inactive', type=int, default=2, help='非活跃批数；默认2，至少保留2个活跃批')
    parser.add_argument('--threads', type=int, default=1, help='计算线程数，默认1')
    parser.add_argument('--timeout', type=float, default=600, help='求解等待秒数；超时请求取消并保留原编号')
    args = parser.parse_args()
    if (args.particles < 1 or args.inactive < 0 or args.batches - args.inactive < 2
            or not 1 <= args.threads <= 64 or not math.isfinite(args.timeout) or args.timeout <= 0):
        parser.error('采样设置、线程或超时范围无效；参考--help')
    nonce = uuid.uuid4().hex[:10]
    args.case_dir = args.case_dir or 'reproduction/' + args.model + '-' + nonce
    args.receipt = args.receipt or str(Path(__file__).resolve().parents[1] / 'artifacts/reproduction' / (args.model + '-' + nonce + '.json'))
    if Path(args.receipt).exists():
        parser.error('receipt已存在；保留原记录并选择新路径')
    return args


async def main():
    args = arguments()
    async with client(args.mcp_url) as call:
        result = await reproduce(args, call)
    print('工程调用流程完成；数据文件与图报告仍须按手册交叉核对。')
    print('case_dir：' + args.case_dir + '\njob_id：' + result['job_id'])
    print('服务端产物目录：' + result['artifact_directory'])
    print('客户端调用记录：' + str(Path(args.receipt).resolve()))
    for warning in result['warnings']:
        print('提示：' + warning)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception as exc:
        print(error_text(exc), file=sys.stderr)
        sys.exit(1)
