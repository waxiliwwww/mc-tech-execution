"""DOCX content model and genuine PDF/Word97 conversion with an isolated Office profile."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import zipfile

import openmc_jobs as jobs
from openmc_runtime import Error, ROOT, api, relative, file_location
from openmc_visuals import selection_label, selected_report_rows


def office_command(runtime):
    command = runtime.get('office_command', ['soffice'])
    if not isinstance(command, list) or not command or any(not isinstance(v, str) or not v for v in command):raise Error('office_command须为管理员配置的命令数组')
    first = Path(command[0])
    if not first.is_absolute() and '/' in command[0]:command = [str(ROOT / first), *command[1:]]
    return command


def convert(source: Path, target: Path, format: str, runtime):
    with tempfile.TemporaryDirectory(prefix='.office-profile-', dir=target.parent) as profile:
        # Preserve scientific chart pixels instead of Office's default JPEG recompression.
        # https://help.libreoffice.org/latest/en-US/text/shared/guide/pdf_params.html
        pdf_options = {'UseLosslessCompression': {'type':'boolean', 'value':'true'},
                       'ReduceImageResolution': {'type':'boolean', 'value':'false'}}
        filter_name = 'pdf:writer_pdf_Export:' + json.dumps(pdf_options) if format == 'pdf' else 'doc:MS Word 97'
        invocation = office_command(runtime) + ['-env:UserInstallation=' + Path(profile).as_uri(), '--headless',
                     '--convert-to', filter_name, '--outdir', str(target.parent), str(source)]
        try:
            process = subprocess.Popen(invocation, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        except OSError as exc:raise Error('未找到Word/PDF转换组件；请运行quickstart准备LibreOffice或配置office_command') from exc
        try:
            stdout, stderr = process.communicate(timeout=runtime.get('report_timeout_seconds', 120))
        except subprocess.TimeoutExpired as exc:
            try:os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:pass
            process.wait()
            raise Error('报告转换超时，未登记成功产物') from exc
        if process.returncode or not target.is_file():raise Error(f'Office转换失败（退出码{process.returncode}），未生成所需格式：' + (stderr + stdout).decode(errors='replace')[-800:])
        signature = target.read_bytes()[:8]
        if format == 'pdf' and not signature.startswith(b'%PDF-'):raise Error('转换产物不是有效PDF')
        if format == 'doc' and signature != bytes.fromhex('D0CF11E0A1B11AE1'):raise Error('转换产物不是Word97二进制DOC')


def render(target, data, runtime, format, image=None):
    if format not in ('pdf', 'docx', 'doc'):raise Error('此导出入口支持pdf/docx/doc')
    with (target / 'report.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with tempfile.TemporaryDirectory(prefix='.report-', dir=target) as temp:
            stage = Path(temp)
            api('render-report', stage, {'data': data, 'image': image, 'max_rows': runtime.get('max_report_rows', 1000)}, runtime)
            document = stage / 'report.docx'
            with zipfile.ZipFile(document) as package:
                if package.testzip() or 'word/document.xml' not in package.namelist():raise Error('生成的DOCX不完整')
            if format != 'docx':convert(document, stage / ('report.' + format), format, runtime)
            output = target / ('report.' + format)
            os.replace(stage / ('report.' + format), output)
    return {'format': format, **file_location(output), 'provenance': data['provenance'],
            'professional_acceptance': False}


def export_job(job_id: str, format: str):
    from openmc_tools import read_results
    from openmc_plots import plot_results
    data = read_results(job_id)
    from openmc_tools import _report_metadata
    target = jobs.job_directory(job_id); runtime = jobs.read(target)['runtime']
    data['metadata'] = _report_metadata(target)
    chart = None
    if data['tallies']:
        try:chart = plot_results(job_id)
        except Error as error:
            if '所选数据点过多' not in str(error) and '没有可绘制的有限结果' not in str(error):raise
            data['warnings'] = [*data['warnings'], '报告数表保留；默认图未生成：' + str(error)]
    image = str(target / chart['image']) if chart else None
    return {'job_id': job_id, 'selection_mode': 'job_summary', **render(target, data, runtime, format, image)}


def export_selected_job(job_id: str, format: str, selection: dict):
    """One native selection supplies chart, complete data and every report format."""
    from openmc_tools import read_results, _report_metadata
    from openmc_plots import plot_results
    target = jobs.job_directory(job_id)
    # Validate/select before replacing any report. Never silently fall back to tally 1.
    chart = plot_results(job_id, **selection)
    image = target / chart['image']
    series = json.loads(image.with_suffix('.json').read_text())
    data = read_results(job_id, max_values=1)
    data.pop('tallies')  # The unselected job preview is not the requested report data.
    # Every format uses the same metadata from the submitted input snapshot.
    # In particular, binary reports must not lose the header at this branch.
    metadata = _report_metadata(target)
    data.update(metadata=metadata, selected_series=series, series_json=image.with_suffix('.json').name,
                series_csv=image.with_suffix('.csv').name)
    if any(v is None for v in series['mean'] + series['std_dev']):
        data['warnings'].append('所选统计量含不可用值；保留为空，不替换为0。')
    runtime = jobs.read(target)['runtime']
    preview = selected_report_rows(series, min(20, runtime.get('max_report_rows', 1000)))
    if format in ('pdf', 'doc', 'docx'):
        result = render(target, data, runtime, format, str(image))
    else:
        if format == 'csv':
            content = image.with_suffix('.csv').read_bytes()
            name = 'report.csv'
        else:
            parameters = '；'.join(f"{key}={value if value is not None else '未记录'}"
                                   for key, value in metadata['parameters'].items()) or '未记录'
            runtime_info = metadata['runtime']
            duration = f"{runtime_info['duration_seconds']}秒" if runtime_info['duration_seconds'] is not None else '未记录'
            rows = ['# OpenMC所选结果报告', '', '作业：' + job_id,
                    '模板版本：' + str(metadata['template'] or '未记录') + '（' + str(metadata['generator'] or '生成方式未记录') + '）',
                    '参数：' + parameters,
                    'OpenMC版本：' + str(metadata['openmc_version'] or '未记录'),
                    '运行时间：' + str(runtime_info['started_at'] or '未记录') + ' → ' + str(runtime_info['finished_at'] or '未记录') + '（' + duration + '）',
                    '随机种子：' + str(metadata['seed'] or '未记录'),
                    '结果选择：' + selection_label(series),
                    '过滤条件：' + json.dumps(series['filter_selection'], ensure_ascii=False), '',
                    '数据来源：' + data['provenance']['data_kind'],
                    '来源StatePoint SHA256：' + data['statepoint_sha256'],
                    '**专业验收：未进行。** 保留OpenMC原始归一化，未换算实际源强或功率。', '',
                    '横轴：' + series['x_label'], series['coordinate_note'], '',
                    f"![所选结果图]({image.name})", '',
                    f"完整{len(series['mean'])}点数据：[CSV]({data['series_csv']})；[JSON与元数据]({data['series_json']})。",
                    '全局keff：' + json.dumps(data['keff'], ensure_ascii=False), '',
                    f"数据预览：{len(preview)}/{len(series['mean'])}点，9位有效数字；完整数据保存在上述文件。", '',
                    '|量/计分项|核素/过滤箱|均值|标准差|说明|', '|---|---|---|---|---|']
            rows += ['|' + '|'.join(row) + '|' for row in preview]
            rows += ['', *('提示：' + warning for warning in data['warnings'])]
            content = ('\n'.join(rows) + '\n').encode('utf-8'); name = 'report.md'
        output = target / name
        with (target / 'report.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with tempfile.NamedTemporaryFile(mode='wb', dir=target, delete=False) as pending:
                pending_path = Path(pending.name)
                try:
                    pending.write(content); pending.flush()
                    os.replace(pending_path, output)
                finally:pending_path.unlink(missing_ok=True)
        result = {'format': format, **file_location(output), 'provenance': data['provenance'],
                  'professional_acceptance': False}
    return {'job_id': job_id, **result, 'selection_mode': 'selected',
            'selection': {k:series[k] for k in ('tally_id', 'score', 'nuclide', 'filter_selection')},
            'points': len(series['mean']), 'preview_rows': len(preview),
            'chart': relative(image), 'series_json': chart['series_json'], 'series_csv': chart['series_csv'],
            'note': '图与正文使用同一选择；正文数表仅预览，完整数据和坐标见配套CSV/JSON。CSV格式导出全部所选数据。'}


def export_scan(scan_id: str, format: str = 'markdown'):
    import openmc_scan as scans
    record = scans.status(scan_id)
    if record['state'] not in scans.TERMINAL:raise Error('扫描仍在运行，请等待终态后导出报告')
    target = scans.directory(scan_id); scans.save_summary(target)
    if format == 'csv':path = target / 'summary.csv'
    elif format == 'markdown':
        path = target / 'report.md'
        lines = ['# 蒙卡参数扫描报告', '', '数据来源：' + record['provenance']['data_kind'],
                 '专业验收：未进行。失败和未执行项保留。', '', '|参数组合|状态|keff|标准差|', '|---|---|---|---|']
        for row in record['cases']:
            keff = row.get('keff') or {}
            lines.append('|'+json.dumps(row['parameters'], ensure_ascii=False)+'|'+row['state']+'|'+str(keff.get('mean'))+'|'+str(keff.get('std_dev'))+'|')
        path.write_text('\n'.join(lines)+'\n', encoding='utf-8')
    else:
        image = next(iter(sorted(target.glob('scan-*.png'), key=lambda p: p.stat().st_mtime, reverse=True)), None)
        return {'scan_id': scan_id, **render(target, record, jobs.read(target)['runtime'], format, str(image) if image else None)}
    return {'scan_id': scan_id, 'format': format, **file_location(path), 'provenance': record['provenance'], 'professional_acceptance': False}
