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


def office_command(runtime):
    command = runtime.get('office_command', ['soffice'])
    if not isinstance(command, list) or not command or any(not isinstance(v, str) or not v for v in command):raise Error('office_command须为管理员配置的命令数组')
    first = Path(command[0])
    if not first.is_absolute() and '/' in command[0]:command = [str(ROOT / first), *command[1:]]
    return command


def convert(source: Path, target: Path, format: str, runtime):
    with tempfile.TemporaryDirectory(prefix='.office-profile-', dir=target.parent) as profile:
        filter_name = 'pdf:writer_pdf_Export' if format == 'pdf' else 'doc:MS Word 97'
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
    target = jobs.job_directory(job_id); runtime = jobs.read(target)['runtime']
    chart = None
    if data['tallies']:
        try:chart = plot_results(job_id)
        except Error as error:
            if '所选数据点过多' not in str(error) and '没有可绘制的有限结果' not in str(error):raise
            data['warnings'] = [*data['warnings'], '报告数表保留；默认图未生成：' + str(error)]
    image = str(target / chart['image']) if chart else None
    return {'job_id': job_id, **render(target, data, runtime, format, image)}


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
