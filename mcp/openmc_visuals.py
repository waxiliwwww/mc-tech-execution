"""Selected scientific series and headless figures, executed in the OpenMC API runtime."""
from __future__ import annotations

import csv
import json
from pathlib import Path


def selected_series(point, params):
    import numpy as np
    available = point.tallies
    if not available:raise ValueError('计算产物没有tally，不能生成结果图')
    identifier = params.get('tally_id')
    if identifier is None:identifier = next(iter(available))
    if identifier not in available:raise ValueError('tally_id不存在')
    tally = available[identifier]
    score = params.get('score') or tally.scores[0]
    nuclide = params.get('nuclide') or ('total' if 'total' in tally.nuclides else tally.nuclides[0])
    if score not in tally.scores or nuclide not in tally.nuclides:
        raise ValueError('score或nuclide不存在；请先读取结果中的可用项')
    selections = params.get('filter_selection') or []
    types, bins = [], []
    def tuples(value):return tuple(tuples(v) for v in value) if isinstance(value, list) else value
    for selection in selections:
        matches = [f for f in tally.filters if type(f).__name__ == selection['type']]
        if len(matches) != 1 or type(matches[0]) in types:raise ValueError('filter类型不存在、重复或不唯一')
        types.append(type(matches[0])); bins.append(tuples(selection['bins']))
    arguments = dict(scores=[score], nuclides=[nuclide], filters=types, filter_bins=bins)
    mean = tally.get_values(value='mean', **arguments).ravel()
    std = tally.get_values(value='std_dev', **arguments).ravel()
    if len(mean) > params.get('max_points', 20000):
        raise ValueError('所选数据点过多；请通过filter_selection缩小范围，不使用默认截断片段绘图')
    def finite(values):return [float(v) if np.isfinite(v) else None for v in values]
    return {'tally_id': int(tally.id), 'score': score, 'nuclide': nuclide,
            'filter_selection': selections, 'filters': [str(f)[:2000] for f in tally.filters],
            'x': list(range(len(mean))), 'mean': finite(mean), 'std_dev': finite(std),
            'x_label': 'Selected filter-bin index (OpenMC order)',
            'y_label': score + ' (raw OpenMC tally)',
            'normalization': 'Raw OpenMC normalization; no power/source-strength conversion.'}


def draw(series, path: Path, params):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    font = params.get('_font_path')
    if font and Path(font).is_file():
        from matplotlib.font_manager import FontProperties, fontManager
        fontManager.addfont(font)
        plt.rcParams['font.family'] = FontProperties(fname=font).get_name()
    mean = np.array([float(v) if v is not None else np.nan for v in series['mean']])
    std = np.array([float(v) if v is not None else np.nan for v in series['std_dev']])
    if not np.isfinite(mean).any():raise ValueError('没有可绘制的有限结果；失败或缺失数据不能补0')
    x = np.asarray(series['x'], dtype=float)
    figure, axes = plt.subplots(figsize=(8, 4.6), constrained_layout=True)
    try:
        if params.get('style', 'line') == 'bar':axes.bar(x, mean)
        else:axes.plot(x, mean, marker='o', linewidth=1.3)
        valid_error = np.isfinite(mean) & np.isfinite(std)
        if valid_error.any():axes.errorbar(x[valid_error], mean[valid_error], yerr=std[valid_error], fmt='none', capsize=3, color='black')
        axes.set_xlabel(series['x_label']); axes.set_ylabel(series['y_label'])
        axes.set_title(params.get('title') or series.get('title', 'OpenMC result'))
        axes.grid(alpha=.25)
        kind = params['provenance']['data_kind']
        footer = kind + ' | engineering output; professional acceptance: not performed'
        if any(v is None for v in series['mean']):footer += '\nMissing/failed values remain gaps.'
        if any(v is None for v in series['std_dev']):footer += '\nSome uncertainties unavailable; not replaced by zero.'
        figure.suptitle(footer, fontsize=8, color='#804000')
        figure.savefig(path, dpi=150)
    finally:plt.close(figure)
    data_path = path.with_suffix('.json')
    data_path.write_text(json.dumps({**series, 'provenance': params['provenance'], 'source_sha256': params.get('source_sha256'), 'professional_acceptance': False}, ensure_ascii=False, indent=2), encoding='utf-8')
    csv_path = path.with_suffix('.csv')
    with csv_path.open('w', newline='', encoding='utf-8') as output:
        writer = csv.writer(output)
        writer.writerow(['x', 'mean', 'std_dev', 'data_kind', 'professional_acceptance',
                         'x_label', 'y_label', 'normalization', 'selection', 'fixed', 'metadata_file'])
        for row in zip(series['x'], series['mean'], series['std_dev']):
            selection = series.get('selection') or {k: series.get(k) for k in ('tally_id', 'score', 'nuclide', 'filter_selection')}
            writer.writerow([*row, kind, False, series['x_label'], series['y_label'], series['normalization'],
                             json.dumps(selection, ensure_ascii=False), json.dumps(series.get('fixed', {})), data_path.name])
    return {'image': path.name, 'series_json': data_path.name, 'series_csv': csv_path.name,
            'points': len(series['x']), 'missing': sum(v is None for v in series['mean'])}


def plot_result(params):
    import openmc
    with openmc.StatePoint(params['statepoint'], autolink=False) as point:
        series = selected_series(point, params)
    series['title'] = f"Tally {series['tally_id']} / {series['score']} / {series['nuclide']}"
    return draw(series, Path(params['filename']), params)


def render_docx(params):
    from docx import Document
    from docx.shared import Inches, Pt
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    document = Document()
    style = document.styles['Normal']; style.font.name = 'Noto Sans CJK SC'; style.font.size = Pt(10)
    style.element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'), 'Noto Sans CJK SC')
    data = params['data']; is_scan = 'scan_id' in data
    document.add_heading('蒙卡参数扫描报告' if is_scan else '蒙卡计算结果报告', 0)
    identifier = data.get('scan_id', data.get('job_id'))
    document.add_paragraph('编号：' + identifier)
    document.add_paragraph('数据来源：' + data['provenance']['data_kind'])
    document.add_paragraph('执行来源：' + data['provenance'].get('execution_kind', 'openmc_runtime'))
    document.add_paragraph('专业验收：未进行。合成数据和接口执行结果不能替代合同专业指标验证。')
    for warning in params.get('warnings', data.get('warnings', [])):
        document.add_paragraph('提示：' + warning)
    table = document.add_table(rows=1, cols=4 if is_scan else 5)
    table.style = 'Table Grid'
    headings = ['参数组合', '状态', 'keff均值', 'keff标准差'] if is_scan else ['量/计分项', '核素/过滤箱', '均值', '标准差', '说明']
    for cell, text in zip(table.rows[0].cells, headings):cell.text = text
    def number(value):return '不可用' if value is None else format(value, '.9g')
    if is_scan:
        for row in data['cases']:
            keff = row.get('keff') or {}
            cells = table.add_row().cells
            for cell, text in zip(cells, [json.dumps(row['parameters'], ensure_ascii=False), row['state'], number(keff.get('mean')), number(keff.get('std_dev'))]):cell.text = text
            if row.get('error'):document.add_paragraph(f"组合{row['index']}：{row['error']}")
    else:
        if data.get('keff'):
            for cell, text in zip(table.add_row().cells, ['keff', '全局', number(data['keff']['mean']), number(data['keff']['std_dev']), '无量纲']):cell.text = text
        remaining = params.get('max_rows', 1000)
        for tally_index, tally in enumerate(data['tallies']):
            rows = list(zip(tally['mean'], tally['std_dev']))
            count_scores = len(tally['scores']); count_nuclides = len(tally['nuclides'])
            shown = min(remaining, len(rows))
            for index, (mean, std) in enumerate(rows[:shown]):
                score = tally['scores'][index % count_scores]
                nuclide = tally['nuclides'][(index // count_scores) % count_nuclides]
                bin_index = index // (count_scores * count_nuclides)
                values = [f"tally {tally['id']} / {score}", f'{nuclide} / {bin_index}', number(mean), number(std), 'OpenMC原始归一化']
                for cell, text in zip(table.add_row().cells, values):cell.text = text
            remaining -= shown
            if tally['values_truncated'] or len(rows) > shown:
                document.add_paragraph(f"tally {tally['id']}：报告行数有限；完整维度{tally['shape']}及截断状态见results.json，原始数据在statepoint文件。")
            if remaining <= 0:
                omitted = data['tallies'][tally_index+1:]
                if omitted:document.add_paragraph('报告总行数限制；未展示后续tally：' + ', '.join(str(t['id']) for t in omitted) + '。完整数据保留在StatePoint中。')
                break
    if params.get('image'):
        document.add_heading('结果图表', level=1)
        document.add_picture(params['image'], width=Inches(6))
    document.add_paragraph('保留输入、计算设置、参考数据与专业判据后，才能另行开展专业比较。')
    document.save('report.docx')
    return {'file': 'report.docx'}
