"""Human-facing chart operations; selection is delegated to the official Tally API."""
from __future__ import annotations

import json
from pathlib import Path
import uuid

import openmc_jobs as jobs
from openmc_runtime import Error, api, relative, file_location


def options(style, format, title):
    if style not in ('line', 'bar') or format not in ('png', 'svg'):raise Error('style须为line/bar，format须为png/svg')
    if title is not None and (not isinstance(title, str) or len(title) > 200):raise Error('title须为不超过200字的文本')


def plot_results(job_id: str, tally_id: int | None = None, score: str | None = None,
                 nuclide: str | None = None, filter_selection: list | None = None,
                 style: str = 'line', format: str = 'png', title: str | None = None) -> dict:
    from openmc_tools import verified_result
    options(style, format, title)
    target, record, point = verified_result(job_id)
    filename = 'plot-' + uuid.uuid4().hex[:12] + '.' + format
    params = {'statepoint': 'work/' + point.name, 'filename': filename, 'tally_id': tally_id,
              'score': score, 'nuclide': nuclide, 'filter_selection': filter_selection,
              'style': style, 'provenance': record['provenance'], 'title': title,
              'max_points': record['runtime'].get('max_plot_points', 20000),
              'source_sha256': record['output_files'][point.name]}
    value = api('plot-result', target, params, record['runtime'])
    return {'job_id': job_id, **value, **file_location(target / filename),
            'series_json': relative(target / value['series_json']), 'series_csv': relative(target / value['series_csv']),
            'provenance': record['provenance'], 'professional_acceptance': False}


def plot_scan(scan_id: str, x_parameter: str, metric: str = 'keff', fixed: dict | None = None,
              tally_id: int | None = None, score: str | None = None, nuclide: str | None = None,
              filter_selection: list | None = None, bin_index: int = 0, format: str = 'png', title: str | None = None) -> dict:
    import openmc_scan as scans
    from openmc_tools import verified_result
    options('line', format, title)
    record = scans.status(scan_id)
    if record['state'] not in scans.TERMINAL:raise Error('扫描仍在运行；等待终态后绘图')
    if x_parameter not in record['axes']:raise Error('x_parameter不是此扫描的参数维度')
    if metric not in ('keff', 'tally') or type(bin_index) is not int or bin_index < 0:raise Error('metric须为keff/tally；bin_index为非负整数')
    fixed = dict(fixed or {})
    if set(fixed) - (set(record['axes']) - {x_parameter}):raise Error('fixed只能固定其它扫描维度')
    for name, values in record['axes'].items():
        if name == x_parameter:continue
        if name not in fixed and len(values) == 1:fixed[name] = values[0]
        if name not in fixed:raise Error('多维扫描绘曲线时须通过fixed固定其它维度：' + name)
        if fixed[name] not in values:raise Error('fixed取值不在扫描范围内：' + name)
    rows = sorted([row for row in record['cases'] if all(row['parameters'][k] == v for k, v in fixed.items())],
                  key=lambda row: row['parameters'][x_parameter])
    mean, std, sources = [], [], []
    target = scans.directory(scan_id); runtime = jobs.read(target)['runtime']
    selected = None
    for row in rows:
        if row['state'] != 'completed':mean.append(None); std.append(None); continue
        folder, child, point = verified_result(row['job_id'])
        sources.append({'job_id': row['job_id'], 'sha256': child['output_files'][point.name]})
        if metric == 'keff':
            value = row.get('keff') or {}; mean.append(value.get('mean')); std.append(value.get('std_dev'))
        else:
            selected = api('select-series', folder, {'statepoint': 'work/' + point.name, 'tally_id': tally_id,
                           'score': score, 'nuclide': nuclide, 'filter_selection': filter_selection,
                           'max_points': runtime.get('max_plot_points', 20000)}, child['runtime'])
            if bin_index >= len(selected['mean']):raise Error('bin_index超出所选计分数据范围')
            mean.append(selected['mean'][bin_index]); std.append(selected['std_dev'][bin_index])
    if metric == 'tally' and selected is None:raise Error('没有可用的成功计分结果，不能生成扫描曲线')
    specification = next(p for p in jobs.read(target)['parameter_specs'] if p['name'] == x_parameter)
    series = {'x': [row['parameters'][x_parameter] for row in rows], 'mean': mean, 'std_dev': std,
              'x_label': x_parameter + ' [' + specification['unit'] + ']',
              'y_label': 'keff (dimensionless)' if metric == 'keff' else selected['y_label'],
              'title': 'Statistical setting comparison' if specification['category'] == 'statistical_setting' else 'Parameter scan',
              'fixed': fixed, 'metric': metric, 'bin_index': bin_index, 'sources': sources,
              'selection': None if selected is None else {k: selected[k] for k in ('tally_id', 'score', 'nuclide', 'filter_selection')},
              'normalization': 'Raw OpenMC values; failed/missing calculations are gaps.'}
    filename = 'scan-' + uuid.uuid4().hex[:12] + '.' + format
    value = api('plot-series', target, {'series': series, 'filename': filename, 'style': 'line',
                'title': title, 'provenance': record['provenance']}, runtime)
    return {'scan_id': scan_id, **value, **file_location(target / filename),
            'series_json': relative(target / value['series_json']), 'series_csv': relative(target / value['series_csv']),
            'provenance': record['provenance'], 'professional_acceptance': False}
