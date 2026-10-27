"""Readable examples are separate from the unchanged JSON CLI/MCP contract."""
import json
from pathlib import PurePosixPath


def output_option(parser):
    parser.add_argument('--json', action='store_true', help='输出完整JSON，供程序处理；进度仍写到标准错误')


def server_path(value, root=None):
    if isinstance(value, dict):
        if value.get('server_path'):
            return value['server_path']
        root = value.get('workspace') or root
        value = value.get('path')
    if not value:
        return '未返回位置'
    path = PurePosixPath(value)
    if path.is_absolute():
        return str(path)
    if root and PurePosixPath(root).is_absolute() and '..' not in path.parts:
        return str(PurePosixPath(root) / path)
    return str(path) + '（服务端相对路径；服务端未返回工作区，不能从客户端目录推算）'


def emit_result(data, args, title):
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False))
        return
    catalog = data.get('artifacts') or {}
    case = data.get('case') or {}
    root = catalog.get('workspace') or case.get('workspace') or data.get('runtime', {}).get('workspace')
    job = data.get('rerun') or data.get('job') or data.get('failed_job') or {}
    scan = data.get('scan') or {}
    state = scan.get('state') or job.get('state') or '未返回'
    if data.get('outcome') == 'not_applied':
        title = '修复未应用：输入保持，未提交重算'
    print('\n' + title)
    kind = scan.get('provenance', {}).get('data_kind') or case.get('data_kind') or job.get('provenance', {}).get('data_kind', '未标明')
    labels = {'synthetic_mgxs':'合成单群截面工程示例', 'public_c5g7_multigroup_example':'官方C5G7多群例题', 'mock_process_fixture':'模拟进程测试夹具'}
    print('数据来源：' + labels.get(kind, kind) + '（' + kind + '）')
    print('工作区（服务端）：' + (root or '未返回；请查询服务端配置'))
    input_path = case.get('case_server_path') or server_path(case.get('case_dir'), root)
    if data.get('prepared'):
        print('原输入（保留）：' + input_path)
        label = '修复副本（尚未应用修改）' if data.get('outcome') == 'not_applied' else '修复副本（本次重算输入）'
        print(label + '：' + server_path(data['prepared'].get('case_dir'), root))
    else:
        print('算例输入：' + input_path)
    if scan:
        print('扫描编号：' + scan['scan_id'] + '；状态：' + state)
        for row in scan.get('cases', []):
            parameters = ', '.join(f'{key}={value}' for key, value in row['parameters'].items())
            keff = row.get('keff') or {}
            uncertainty = keff.get('std_dev')
            print(f"  {parameters} → {row['state']}；作业 {row.get('job_id', '未启动')}；keff={keff.get('mean', '不可用')}，标准差=" +
                  ('不可用' if uncertainty is None else str(uncertainty)))
    elif job:
        print('计算编号：' + job['job_id'] + '；状态：' + state)
        keff = data.get('results', {}).get('keff') or {}
        if keff:
            print('keff=' + str(keff.get('mean', '不可用')) + '；标准差=' +
                  ('不可用' if keff.get('std_dev') is None else str(keff['std_dev'])))
    if data.get('failed_job'):
        print('保留的原失败编号：' + data['failed_job']['job_id'])
    warnings = list(data.get('results', {}).get('warnings', []))
    for row in scan.get('cases', []):
        warnings.extend(row.get('warnings', []))
    for warning in dict.fromkeys(warnings):
        print('统计警告：' + warning)
    if data.get('parameter', {}).get('unit') == 'macro':
        print('参数说明：macro 是宏观截面倍率，不是真实材料质量密度；本例不用于评价参数敏感性。')
    print('产物位置（在服务端打开；未自动下载到客户端）：')
    if catalog.get('directory'):
        print('  记录目录：' + catalog['directory'])
    for key, label in [('scan_plot', '扫描曲线'), ('result_plot', '单次结果图'), ('chart', '结果图')]:
        if data.get(key):
            print('  ' + label + '：' + server_path(data[key], root))
    reports = data.get('reports') or ([data['report']] if data.get('report') else [])
    for report in reports:
        print('  ' + report.get('format', 'report') + '：' + server_path(report, root))
    print('说明：.mc-jobs / .mc-scans 是工作区内的隐藏目录；可用 ls -la 查看。')
    print('结果范围：工程流程验证；统计警告保留，未作专业准确率验收。')
    print('完整结构化结果：在本命令末尾加 --json；底层 ./mc.sh 与 MCP 始终返回结构化结果。')
