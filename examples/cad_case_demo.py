#!/usr/bin/env python3
"""Same STEP -> DAGMC -> OpenMC -> mesh/selected data/report; CLI or standard MCP, no LLM."""
import argparse
import asyncio
import math
import sys
import uuid

from demo_output import emit_result, output_option
from openmc_demo import client, error_text
from public_case_demo import wait_job


async def demo(args):
    case_dir = args.case_dir or 'demos/cad-' + uuid.uuid4().hex[:10]
    async with client(args.mcp_url) as call:
        print('1/7 读取服务端10mm立方体STEP（不上传客户端文件）', file=sys.stderr)
        inspected = await call('openmc_inspect_step', {'step_path': args.step_path})
        box = inspected.get('bounding_box') or {}
        if inspected['solids'] != 1 or any(
                not math.isclose(box.get(axis + 'min', float('nan')), -5.0, abs_tol=1e-6)
                or not math.isclose(box.get(axis + 'max', float('nan')), 5.0, abs_tol=1e-6)
                for axis in ('x', 'y', 'z')):
            raise RuntimeError('此示例固定使用原点中心的10mm单实体立方体；其它CAD请按工具接口自行提供材料、源和计分。')
        print('2/7 转换同一STEP，毫米乘0.1换算为厘米', file=sys.stderr)
        converted = await call('openmc_convert_cad', {
            'step_path': args.step_path, 'target_dir': case_dir,
            'material_tags': ['fuel'], 'scale_factor': 0.1})
        if not converted.get('engine_read', {}).get('ok'):
            raise RuntimeError('转换产物未通过DAGMC引擎读回，保留目录：' + case_dir)
        print('3/7 组装工程测试材料、源和真空边界；不代表业务模型', file=sys.stderr)
        model = await call('openmc_build_dagmc_model', {
            'case_dir': case_dir,
            'materials': [{'name': 'fuel', 'density': 18.95, 'units': 'g/cm3',
                           'nuclides': [{'name': 'U235', 'fraction': 1.0, 'percent_type': 'ao'}]}],
            'particles': args.particles, 'batches': args.batches, 'inactive': args.inactive,
            'source_point': [0.0, 0.0, 0.0], 'seed': 42})
        print('4/7 添加4×4×1网格计分，预览并校核同一model.xml', file=sys.stderr)
        defined = await call('openmc_define_tally', {
            'case_dir': case_dir, 'scores': ['flux'], 'name': 'CAD空间通量',
            'filters': [{'type': 'mesh', 'dimension': [4, 4, 1],
                         'lower_left': [-0.5, -0.5, -0.5], 'upper_right': [0.5, 0.5, 0.5]}]})
        preview = await call('openmc_preview_input', {'case_dir': case_dir})
        validation = await call('openmc_validate_inputs', {'case_dir': case_dir, 'api_check': True})
        if not validation['structure_valid'] or not validation.get('openmc_api', {}).get('api_parsed'):
            raise RuntimeError('输入未通过结构/API校核；保留目录：' + case_dir)
        print('5/7 命名提交真实OpenMC作业并等待终态', file=sys.stderr)
        submitted = await call('openmc_submit_job', {'case_dir': case_dir, 'threads': 1,
                                                   'name': 'STEP转换计算工程示例'})
        status = await wait_job(call, submitted['job_id'], args.timeout)
        if status['state'] != 'completed':
            raise RuntimeError('计算未成功：' + status['state'] + '；job_id=' + submitted['job_id'] +
                               '；用openmc_job_status查看日志，勿直接重复提交')
        print('6/7 从真实StatePoint读取16格均值/标准差，生成空间热图', file=sys.stderr)
        results = await call('openmc_read_results', {'job_id': submitted['job_id']})
        selected = {'job_id': submitted['job_id'], 'tally_id': defined['tally_id'], 'score': 'flux'}
        chart = await call('openmc_plot_mesh', {**selected, 'x_label': 'x (cm)', 'y_label': 'y (cm)'})
        print('7/7 报告沿用同一计分，列出服务端产物', file=sys.stderr)
        report = await call('openmc_export_report', {**selected, 'nuclide': 'total',
                                                   'format': args.report_format})
        artifacts = await call('openmc_list_artifacts', {'kind': 'job', 'identifier': submitted['job_id']})
        return {'input_source': inspected, 'case': converted, 'model': model, 'tally': defined,
                'preview': preview, 'validation': validation, 'job': status,
                'results': results, 'chart': chart, 'report': report, 'artifacts': artifacts,
                'professional_acceptance': False,
                'note': '10mm立方体+纯U235为工程测试组合；CAD文件不自带材料定义。'
                        '热图为原始flux计分，报告配套图使用同一16格数据的展平序号，不冒充空间坐标；'
                        '无专业精度或收敛结论。'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='同一STEP转换→DAGMC模型→真实计算→16格热图与同源报告；不调用模型。',
        epilog='示例固定原点中心10mm立方体及纯U235工程材料；其它CAD通过工具接口自行指定物理输入。')
    parser.add_argument('--mcp-url', help='实际MCP地址；省略时直接CLI调用')
    parser.add_argument('--step-path', default='examples/cad/cube.step',
                        help='服务端工作区内的10mm立方体STEP；默认随包examples/cad/cube.step，不自动上传')
    parser.add_argument('--case-dir', help='服务端工作区新目录；默认demos/cad-随机后缀，不覆盖')
    parser.add_argument('--particles', type=int, default=1000, help='每代粒子数，默认1000，仅工程调试')
    parser.add_argument('--batches', type=int, default=12, help='总批次，默认12')
    parser.add_argument('--inactive', type=int, default=2, help='非活跃批次，默认2，活跃批次至少2')
    parser.add_argument('--timeout', type=float, default=180, help='等待计算的秒数，默认180；到限请求取消')
    parser.add_argument('--report-format', choices=['markdown', 'csv', 'pdf', 'docx', 'doc'], default='markdown',
                        help='报告格式，默认markdown；pdf/doc需Office组件')
    output_option(parser)
    args = parser.parse_args()
    try:
        data = asyncio.run(demo(args))
        if not args.json:
            print('\n输入STEP（服务端）：' + data['input_source']['source']['server_path'])
            print('长度换算：10mm → 1cm；材料：纯U235，18.95g/cm³（工程测试设置）')
            print('图中含义：4×4空间网格的原始flux计分；报告配套图为相同16格的展平序号。')
        emit_result(data, args, '同一STEP转换与计算结果')
    except Exception as error:
        print('CAD示例失败：' + error_text(error), file=sys.stderr)
        sys.exit(1)
