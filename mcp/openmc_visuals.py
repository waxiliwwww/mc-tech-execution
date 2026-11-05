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
    axis = {'x': list(range(len(mean))), 'x_kind': 'filter_bin_index', 'x_unit': '', 'x_scale': 'linear',
            'x_label': 'Selected filter-bin index (OpenMC order: ' +
                       (' × '.join(type(f).__name__ for f in tally.filters) or 'no filters') + ')',
            'coordinate_note': 'Flattened selected-bin index; not a physical distance or energy coordinate.'}
    # The same API indices that selected the values must select the coordinates.
    # In particular, explicit bins may be non-contiguous or requested in reverse order.
    if len(tally.filters) == 1 and type(tally.filters[0]).__name__ in ('EnergyFilter', 'EnergyoutFilter'):
        indices = tally.get_filter_indices(filters=types, filter_bins=bins)
        intervals = np.asarray(tally.filters[0].bins)[indices]
        lower, upper = intervals[:, 0], intervals[:, 1]
        outgoing = type(tally.filters[0]).__name__ == 'EnergyoutFilter'
        axis = {'x': ((lower + upper) / 2).tolist(), 'x_lower': lower.tolist(), 'x_upper': upper.tolist(),
                'x_kind': 'energy_bin_midpoint', 'x_unit': 'eV',
                'x_scale': 'log' if (lower > 0).all() else 'linear',
                'x_label': ('Outgoing energy' if outgoing else 'Energy') + ' (eV; arithmetic bin midpoint)',
                'coordinate_note': 'Arithmetic bin midpoints; bounds in x_lower/x_upper. Values remain bin-integrated; not divided by energy-bin width. Request order is preserved.'}
    def finite(values):return [float(v) if np.isfinite(v) else None for v in values]
    return {'tally_id': int(tally.id), 'score': score, 'nuclide': nuclide,
            'filter_selection': selections, 'filters': [str(f)[:2000] for f in tally.filters],
            **axis, 'mean': finite(mean), 'std_dev': finite(std),
            'y_label': score + (' (raw bin tally; not per eV)' if axis['x_unit'] == 'eV' else ' (raw OpenMC tally)'),
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
        if params.get('style', 'line') == 'bar':
            widths = .8 * (np.asarray(series['x_upper']) - np.asarray(series['x_lower'])) if 'x_lower' in series else .8
            axes.bar(x, mean, width=widths)
        else:axes.plot(x, mean, marker='o', linewidth=1.3)
        valid_error = np.isfinite(mean) & np.isfinite(std)
        if valid_error.any():axes.errorbar(x[valid_error], mean[valid_error], yerr=std[valid_error], fmt='none', capsize=3, color='black')
        axes.set_xlabel(series['x_label']); axes.set_ylabel(series['y_label'])
        axes.set_xscale(series.get('x_scale', 'linear'))
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
                         'x_label', 'y_label', 'normalization', 'selection', 'fixed', 'metadata_file',
                         'x_lower', 'x_upper', 'x_unit'])
        for index, row in enumerate(zip(series['x'], series['mean'], series['std_dev'])):
            selection = series.get('selection') or {k: series.get(k) for k in ('tally_id', 'score', 'nuclide', 'filter_selection')}
            writer.writerow([*row, kind, False, series['x_label'], series['y_label'], series['normalization'],
                             json.dumps(selection, ensure_ascii=False), json.dumps(series.get('fixed', {})), data_path.name,
                             series['x_lower'][index] if 'x_lower' in series else '',
                             series['x_upper'][index] if 'x_upper' in series else '', series.get('x_unit', '')])
    return {'image': path.name, 'series_json': data_path.name, 'series_csv': csv_path.name,
            'points': len(series['x']), 'missing': sum(v is None for v in series['mean'])}


def _read_meshes(path: Path):
    from lxml import etree
    tree = etree.parse(str(path))
    meshes = {}
    for node in tree.getroot().iter('mesh'):
        dimension = [int(v) for v in (node.findtext('dimension') or '').split()]
        lower = [float(v) for v in (node.findtext('lower_left') or '').split()]
        upper = [float(v) for v in (node.findtext('upper_right') or '').split()]
        meshes[int(node.get('id'))] = {'dimension': dimension, 'lower_left': lower, 'upper_right': upper}
    return meshes


def plot_mesh(params):
    """规则网格分布的二维热图；3D网格须显式给层号，不静默投影。"""
    import numpy as np
    import openmc
    tallies_path = Path(params['tallies_xml'])
    if not tallies_path.is_file():
        raise ValueError('缺少model.xml或tallies.xml，不能还原网格坐标；请用openmc_define_tally生成带网格过滤的tally后重新计算')
    meshes = _read_meshes(tallies_path)
    with openmc.StatePoint(params['statepoint'], autolink=False) as point:
        available = list(point.tallies.values())
        if not available:
            raise ValueError('计算产物没有tally，不能生成结果图')
        candidates = [t for t in available if any(type(f).__name__ == 'MeshFilter' for f in t.filters)]
        if not candidates:
            raise ValueError('该计算产物没有网格tally；先用openmc_define_tally定义网格过滤并重新提交计算')
        identifier = params.get('tally_id')
        if identifier is not None:
            tally = next((t for t in candidates if int(t.id) == int(identifier)), None)
            if tally is None:
                raise ValueError('tally_id不存在或不是网格tally')
        else:
            tally = candidates[0]
        kinds = [type(f).__name__ for f in tally.filters]
        extra = [k for k in kinds if k not in ('MeshFilter', 'EnergyFilter')]
        if extra:
            raise ValueError('网格分布图当前只支持网格过滤（可叠加能量过滤）；该tally还含：' + '、'.join(extra))
        mesh_filter = next(f for f in tally.filters if type(f).__name__ == 'MeshFilter')
        # MeshFilter.bins 是 (i,j,k) 网格单元坐标（1起），网格编号在 filter.mesh.id。
        mesh_id = int(mesh_filter.mesh.id)
        if mesh_id not in meshes:
            raise ValueError(tallies_path.name + '中找不到网格定义：' + str(mesh_id))
        mesh = meshes[mesh_id]
        dimension = list(mesh['dimension'])
        while len(dimension) < 3:
            dimension.append(1)
        nx, ny, nz = dimension
        cells = nx * ny * nz
        mean = np.asarray(tally.mean, dtype=float)
        std = np.asarray(tally.std_dev, dtype=float)
        if mean.ndim < 1 or mean.shape[0] != cells:
            raise ValueError('网格分箱数与网格维度不一致，不能还原坐标')
        scores = [str(item) for item in tally.scores]
        if len(scores) > 1:
            raise ValueError('该tally有多个score，网格图请用单一score的网格tally（如flux）：' + '、'.join(scores))
        score = scores[0]
        if params.get('score') is not None and params['score'] != score:
            raise ValueError('score与该网格tally的计分不一致：' + score)
        rest_mean = mean.reshape(cells, -1)
        rest_std = std.reshape(cells, -1)
        energy_label = None
        if len(tally.filters) > 1:
            energy_filter = next((f for f in tally.filters if type(f).__name__ == 'EnergyFilter'), None)
            if energy_filter is None:
                raise ValueError('不支持的过滤组合，网格图只支持网格过滤或网格+能量过滤')
            bounds = np.asarray(energy_filter.values, dtype=float)
            bins_count = len(bounds) - 1
            column = params.get('energy_bin')
            if column is None:
                raise ValueError('该tally带能量过滤，须用energy_bin指定第几个能量箱（0起）')
            if column >= bins_count:
                raise ValueError('energy_bin超出能量箱数：' + str(bins_count))
            if rest_mean.shape[1] != bins_count:
                raise ValueError('能量过滤与网格过滤的组合形状无法判定')
            values = rest_mean[:, column]
            errors = rest_std[:, column]
            energy_label = f'{bounds[column]:.6g}-{bounds[column + 1]:.6g} eV'
        else:
            if rest_mean.shape[1] != 1:
                raise ValueError('网格tally含额外维度，不能作为二维分布绘制')
            values = rest_mean[:, 0]
            errors = rest_std[:, 0]
        if nz > 1:
            level = params.get('level')
            if level is None:
                raise ValueError('三维网格须用level指定第几层（0起），不静默投影')
            if type(level) is not int or not 0 <= level < nz:
                raise ValueError('level须在0到nz-1之间')
            start = level * nx * ny
            values = np.asarray(values)[start:start + nx * ny]
            errors = np.asarray(errors)[start:start + nx * ny]
        else:
            level = None
        grid = np.full((ny, nx), np.nan)
        error_grid = np.full((ny, nx), np.nan)
        for j in range(ny):
            for i in range(nx):
                grid[j, i] = values[i + nx * j]
                error_grid[j, i] = errors[i + nx * j]
        x_edges = np.linspace(mesh['lower_left'][0], mesh['upper_right'][0], nx + 1)
        y_edges = np.linspace(mesh['lower_left'][1], mesh['upper_right'][1], ny + 1)
        if not np.isfinite(grid).any():
            raise ValueError('网格值全为非有限值，不能绘图；失败或缺失数据不能补0')
        series = {'values': grid, 'errors': error_grid, 'x_edges': x_edges, 'y_edges': y_edges,
                  'x_label': params.get('x_label') or 'x (cm)',
                  'y_label': params.get('y_label') or 'y (cm)',
                  'title': params.get('title') or f'Mesh tally {tally.id} / {score}',
                  'mesh_id': mesh_id, 'dimension': [nx, ny, nz], 'level': level,
                  'score': score, 'energy_label': energy_label,
                  'colorbar_label': params.get('colorbar_label') or
                                     (str(score) + ' (OpenMC raw score; no power normalization)'),
                  'tally_id': int(tally.id)}
        return draw_mesh(series, Path(params['filename']), params)


def draw_mesh(series, path: Path, params):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    _set_font(params)
    zero_mask = np.isfinite(series['values']) & (series['values'] == 0)
    zero_bins = int(np.count_nonzero(zero_mask))
    zero_note = '本次统计均值为0；单凭该值不能判断物理量严格为0或断言没有径迹贡献。'
    figure, axes = plt.subplots(figsize=(7.2, 5.6), constrained_layout=True)
    try:
        image = axes.pcolormesh(series['x_edges'], series['y_edges'], series['values'], shading='flat',
                                cmap='viridis')
        bar = figure.colorbar(image, ax=axes)
        bar.set_label(series['colorbar_label'])
        if zero_bins:
            xs = .5 * (np.asarray(series['x_edges'][:-1]) + series['x_edges'][1:])
            ys = .5 * (np.asarray(series['y_edges'][:-1]) + series['y_edges'][1:])
            j, i = np.nonzero(zero_mask)
            axes.scatter(xs[i], ys[j], marker='x', s=20, color='#f5bd55', linewidths=.9,
                         label=f'zero estimate: {zero_bins} bins (not a physical-zero conclusion)')
            axes.legend(loc='upper center', bbox_to_anchor=(.5, -.14), fontsize=7, framealpha=.9)
        axes.set_aspect('equal')
        axes.set_xlabel(series['x_label'])
        axes.set_ylabel(series['y_label'])
        axes.set_title(series['title'])
        note = f'mesh {series["mesh_id"]} | dimension {series["dimension"]}'
        if series.get('level') is not None:
            note += f' | layer k={series["level"]}'
        if series.get('energy_label'):
            note += ' | energy bin ' + series['energy_label']
        kind = params['provenance']['data_kind']
        figure.suptitle(note + ' | ' + kind + ' | engineering output; professional acceptance: not performed',
                        fontsize=8, color='#804000', y=1.02)
        figure.savefig(path, dpi=150, bbox_inches='tight')
    finally:
        plt.close(figure)
    values_path = path.with_suffix('.json')
    values_path.write_text(json.dumps({'tally_id': series['tally_id'], 'mesh_id': series['mesh_id'], 'dimension': series['dimension'],
                                       'zero_bins': zero_bins, 'zero_bin_note': zero_note if zero_bins else None,
                                       'level': series.get('level'), 'score': series['score'],
                                       'energy_label': series.get('energy_label'),
                                       'x_edges': [float(v) for v in series['x_edges']],
                                       'y_edges': [float(v) for v in series['y_edges']],
                                       'values': [[None if not np.isfinite(v) else float(v) for v in row]
                                                  for row in series['values']],
                                       'errors': [[None if not np.isfinite(v) else float(v) for v in row]
                                                  for row in series['errors']],
                                       'normalization': 'OpenMC原始计分归一化；未换算功率或实际源强。',
                                       'provenance': params['provenance'], 'professional_acceptance': False},
                                      ensure_ascii=False, indent=2), encoding='utf-8')
    csv_path = path.with_suffix('.csv')
    with csv_path.open('w', newline='', encoding='utf-8') as output:
        writer = csv.writer(output)
        writer.writerow(['i', 'j', 'x_center', 'y_center', 'value', 'std_dev', 'mesh_id', 'level', 'score'])
        nx, ny = series['dimension'][0], series['dimension'][1]
        for j in range(ny):
            for i in range(nx):
                value = series['values'][j, i]
                error = series['errors'][j, i]
                writer.writerow([i, j,
                                 0.5 * (series['x_edges'][i] + series['x_edges'][i + 1]),
                                 0.5 * (series['y_edges'][j] + series['y_edges'][j + 1]),
                                 '' if not np.isfinite(value) else format(float(value), '.9g'),
                                 '' if not np.isfinite(error) else format(float(error), '.9g'),
                                 series['mesh_id'], series.get('level'), series['score']])
    return {'image': path.name, 'values_json': values_path.name, 'values_csv': csv_path.name,
            'tally_id': series['tally_id'], 'zero_bins': zero_bins,
            'warnings': [zero_note] if zero_bins else [],
            'cells': int(series['dimension'][0] * series['dimension'][1]),
            'mesh': {'id': series['mesh_id'], 'dimension': series['dimension'], 'level': series.get('level')}}


def plot_convergence(params):
    """逐批k的累计均值±标准误收敛带；数据来自statepoint的k_generation（单批k）。"""
    import h5py
    import numpy as np
    with h5py.File(params['statepoint'], 'r') as handle:
        if 'k_generation' not in handle:
            raise ValueError('statepoint缺少k_generation，不能生成收敛带')
        generations = np.asarray(handle['k_generation'][()], dtype=float).flatten()
        n_batches = int(handle['n_batches'][()]) if 'n_batches' in handle else len(generations)
        n_inactive = int(handle['n_inactive'][()]) if 'n_inactive' in handle else 0
        combined = np.asarray(handle['k_combined'][()], dtype=float) if 'k_combined' in handle else None
    active = generations[n_inactive:]
    if active.size == 0:
        raise ValueError('没有活跃批次的逐批k可绘制')
    counts = np.arange(1, active.size + 1)
    cumulative = np.cumsum(active) / counts
    stderr = np.array([np.std(active[:i], ddof=1) / np.sqrt(i) if i > 1 else np.nan for i in counts])
    series = {'x': [int(n_inactive + i) for i in counts], 'mean': [float(v) for v in cumulative],
              'stderr': [None if not np.isfinite(v) else float(v) for v in stderr],
              'x_label': params.get('x_label') or 'Batch index (active batches)',
              'y_label': params.get('y_label') or 'k-eff (cumulative mean)',
              'title': params.get('title') or 'k-effective convergence',
              'combined': None if combined is None else {'mean': float(combined[0]), 'std_dev': float(combined[1])},
              'n_inactive': n_inactive, 'n_batches': n_batches}
    return draw_convergence(series, Path(params['filename']), params)


def _set_font(params):
    import matplotlib.pyplot as plt
    font = params.get('_font_path')
    if font and Path(font).is_file():
        from matplotlib.font_manager import FontProperties, fontManager
        fontManager.addfont(font)
        plt.rcParams['font.family'] = FontProperties(fname=font).get_name()


def draw_convergence(series, path: Path, params):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    _set_font(params)
    mean = np.asarray(series['mean'], dtype=float)
    stderr = np.asarray([np.nan if v is None else float(v) for v in series['stderr']], dtype=float)
    x = np.asarray(series['x'], dtype=float)
    figure, axes = plt.subplots(figsize=(8, 4.6), constrained_layout=True)
    try:
        axes.plot(x, mean, marker='o', linewidth=1.3, label='cumulative mean')
        if params.get('show_band', True) and np.isfinite(stderr).any():
            axes.fill_between(x, mean - stderr, mean + stderr, alpha=.25,
                              label='±1 standard error (statepoint k_generation)')
        if series.get('combined'):
            value = series['combined']['mean']
            axes.axhline(value, linestyle='--', linewidth=1.0, color='black', alpha=.7,
                         label=f'combined k = {value:.5f} ± {series["combined"]["std_dev"]:.5f}')
        axes.set_xlabel(series['x_label'])
        axes.set_ylabel(series['y_label'])
        axes.set_title(series['title'])
        axes.grid(alpha=.25)
        axes.legend(fontsize=8)
        kind = params['provenance']['data_kind']
        figure.suptitle('per-batch k from statepoint k_generation; cumulative mean over active batches | '
                        + kind + ' | engineering output; professional acceptance: not performed',
                        fontsize=8, color='#804000')
        figure.savefig(path, dpi=150)
    finally:
        plt.close(figure)
    data_path = path.with_suffix('.json')
    data_path.write_text(json.dumps({**series, 'provenance': params['provenance'],
                                     'professional_acceptance': False}, ensure_ascii=False, indent=2),
                         encoding='utf-8')
    csv_path = path.with_suffix('.csv')
    with csv_path.open('w', newline='', encoding='utf-8') as output:
        writer = csv.writer(output)
        writer.writerow(['batch', 'cumulative_mean_k', 'standard_error', 'combined_k', 'combined_std_dev'])
        for index, batch in enumerate(series['x']):
            writer.writerow([batch, format(mean[index], '.9g'),
                             '' if series['stderr'][index] is None else format(series['stderr'][index], '.9g'),
                             '' if not series.get('combined') else format(series['combined']['mean'], '.9g'),
                             '' if not series.get('combined') else format(series['combined']['std_dev'], '.9g')])
    return {'image': path.name, 'series_json': data_path.name, 'series_csv': csv_path.name,
            'batches': int(len(series['x'])), 'combined': series.get('combined')}


def plot_result(params):
    import openmc
    with openmc.StatePoint(params['statepoint'], autolink=False) as point:
        series = selected_series(point, params)
    series['title'] = f"Tally {series['tally_id']} / {series['score']} / {series['nuclide']}"
    return draw(series, Path(params['filename']), params)


def selection_label(series):
    return f"tally {series['tally_id']} / {series['score']} / {series['nuclide']}"


def metadata_parameters(metadata):
    values = []
    for key, value in (metadata.get('parameters') or {}).items():
        if value is None:continue
        suffix = '（OpenMC默认）' if metadata.get('parameter_sources', {}).get(key) == 'openmc_default' else ''
        values.append(f'{key}={value}{suffix}')
    return '；'.join(values) or '未记录'


def selected_report_rows(series, limit):
    def number(value):return '不可用' if value is None else format(value, '.9g')
    rows = []
    for index, (mean, std) in enumerate(zip(series['mean'][:limit], series['std_dev'][:limit])):
        coordinate = (f"[{number(series['x_lower'][index])}, {number(series['x_upper'][index])}] eV"
                      if 'x_lower' in series else f"bin {index}")
        rows.append([f"tally {series['tally_id']} / {series['score']}",
                     series['nuclide'] + ' / ' + coordinate, number(mean), number(std), 'OpenMC原始归一化'])
    return rows


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
    metadata = data.get('metadata') or {}
    if metadata:
        parameters = metadata_parameters(metadata)
        runtime_info = metadata.get('runtime') or {}
        duration = (f"{runtime_info['duration_seconds']}秒"
                    if runtime_info.get('duration_seconds') is not None else '未记录')
        document.add_paragraph('模板版本：' + str(metadata.get('template') or '未记录') + '（'
                               + str(metadata.get('generator') or '生成方式未记录') + '）')
        document.add_paragraph('参数：' + parameters)
        document.add_paragraph('OpenMC版本：' + str(metadata.get('openmc_version') or '未记录'))
        document.add_paragraph('运行时间：' + str(runtime_info.get('started_at') or '未记录') + ' → '
                               + str(runtime_info.get('finished_at') or '未记录') + '（' + duration + '）')
        document.add_paragraph('随机种子：' + str(metadata.get('seed') or '未记录'))
    for warning in params.get('warnings', data.get('warnings', [])):
        document.add_paragraph('提示：' + warning)
    series = data.get('selected_series')
    if series is not None:
        document.add_paragraph('结果选择：' + selection_label(series))
        document.add_paragraph('过滤条件：' + json.dumps(series['filter_selection'], ensure_ascii=False))
        document.add_paragraph('横轴：' + series['x_label'] + '；' + series['coordinate_note'])
        document.add_paragraph('来源StatePoint SHA256：' + data['statepoint_sha256'])
        document.add_paragraph(f"完整数据：{data['series_csv']}；元数据：{data['series_json']}。共{len(series['mean'])}点，数表以9位有效数字显示。")
        if params.get('image'):
            document.add_heading('所选结果图表', level=1)
            document.add_picture(params['image'], width=Inches(6))
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
        if series is not None:
            rows = selected_report_rows(series, min(20, remaining))
            for values in rows:
                for cell, text in zip(table.add_row().cells, values):cell.text = text
            document.add_paragraph(f"所选数据预览：{len(rows)}/{len(series['mean'])}点；完整均值、标准差与分箱区间见上述CSV/JSON，不因正文预览限制丢弃。")
        for tally_index, tally in enumerate(data.get('tallies', [])):
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
    if series is not None:
        # Energy intervals are longer than repeated names; equal-width columns
        # split exponents and inflate a short preview into several pages.
        table.autofit = False
        widths = [.95, 2.1, 1.0, 1.0, .95]
        for column, width in zip(table.columns, widths):column.width = Inches(width)
        for row_index, row in enumerate(table.rows):
            row._tr.get_or_add_trPr().append(OxmlElement('w:cantSplit'))
            if row_index == 0:row._tr.get_or_add_trPr().append(OxmlElement('w:tblHeader'))
            for cell, width in zip(row.cells, widths):
                cell.width = Inches(width)
                for paragraph in cell.paragraphs:
                    paragraph.paragraph_format.space_after = Pt(0)
                    for run in paragraph.runs:run.font.size = Pt(9)
    if params.get('image') and series is None:
        document.add_heading('结果图表', level=1)
        document.add_picture(params['image'], width=Inches(6))
    document.add_paragraph('保留输入、计算设置、参考数据与专业判据后，才能另行开展专业比较。')
    document.save('report.docx')
    return {'file': 'report.docx'}
