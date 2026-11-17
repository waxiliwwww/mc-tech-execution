"""Shared scientific plot templates and standalone reproduction script.

Style adapted from SciencePlots 2.2.2 (science/no-latex):
https://github.com/garrettj403/SciencePlots
MIT License — Copyright (c) 2018 John Garrett

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

STYLE_VERSION = 'paper-1'
# Edit this mapping in an exported script, or supply --style-file/--font-size.
# All settings are scoped to one figure; neither TeX nor OpenMC is required.
PAPER_STYLE = {
    'font.family': 'sans-serif', 'font.size': 13, 'axes.labelsize': 15,
    'axes.titlesize': 14, 'legend.fontsize': 11, 'xtick.labelsize': 12,
    'ytick.labelsize': 12, 'axes.linewidth': 1.1, 'lines.linewidth': 2,
    'lines.markersize': 6, 'xtick.direction': 'out', 'ytick.direction': 'out',
    'xtick.top': False, 'ytick.right': False, 'xtick.minor.visible': False,
    'ytick.minor.visible': False, 'xtick.major.size': 5, 'ytick.major.size': 5,
    'xtick.major.width': 1.1, 'ytick.major.width': 1.1, 'axes.grid': False,
    'legend.frameon': True, 'legend.framealpha': 1, 'legend.edgecolor': '#cccccc',
    'text.usetex': False, 'mathtext.fontset': 'dejavusans',
    'figure.facecolor': 'white', 'axes.facecolor': 'white', 'savefig.dpi': 300,
    'savefig.bbox': 'tight', 'savefig.pad_inches': .08, 'svg.fonttype': 'none',
}
COLORS = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b']


def write_text(path, content):
    """Publish one complete companion file, including concurrent identical requests."""
    path = Path(path)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
        pending = Path(stream.name)
        try:
            stream.write(content); stream.flush()
            os.replace(pending, path)
        finally:
            pending.unlink(missing_ok=True)


def _array(values):
    import numpy as np
    return np.asarray(values, dtype=float)  # JSON null remains NaN, never zero.


def _errors(ax, x, mean, std, color, label=None):
    import numpy as np
    if (np.isfinite(std) & (std < 0)).any():
        raise ValueError('Standard deviations must be non-negative; data were not repaired or replaced.')
    valid = np.isfinite(x) & np.isfinite(mean) & np.isfinite(std) & (std >= 0)
    if valid.any():
        ax.errorbar(x[valid], mean[valid], yerr=std[valid], fmt='none',
                    color=color, capsize=3, elinewidth=1.2, label=label)


def _curve(data, figure, options):
    import numpy as np
    ax = figure.subplots()
    x, mean, std = _array(data['x']), _array(data['mean']), _array(data['std_dev'])
    if options.get('style') == 'bar':
        width = .8 * (_array(data['x_upper']) - _array(data['x_lower'])) if 'x_lower' in data else .8
        ax.bar(x, mean, width=width, color=COLORS[0], alpha=.85, label=data.get('score') or 'Estimate')
    else:
        ax.plot(x, mean, 'o-', color=COLORS[0], label=data.get('score') or 'Estimate')
    _errors(ax, x, mean, std, COLORS[0])
    ax.set(xlabel=options.get('x_label') or data['x_label'],
           ylabel=options.get('y_label') or data['y_label'], xscale=data.get('x_scale', 'linear'))
    ax.legend(loc='best')
    return [ax]


def _mesh(data, figure, options):
    import numpy as np
    values, errors = _array(data['values']), _array(data['errors'])
    relative = np.full_like(values, np.nan)
    np.divide(100 * errors, np.abs(values), out=relative, where=values != 0)
    axes = figure.subplots(1, 2)
    zero = np.isfinite(values) & (values == 0)
    for ax, grid, cmap, label, panel in [
        (axes[0], values, 'viridis', options['colorbar_label'], '(a)'),
        (axes[1], relative, 'magma', 'Standard deviation / |mean| (%)', '(b)')]:
        import matplotlib
        colors = matplotlib.colormaps[cmap].with_extremes(bad='#e5e5e5')
        image = ax.pcolormesh(data['x_edges'], data['y_edges'], np.ma.masked_invalid(grid),
                              shading='flat', cmap=colors, rasterized=True)
        figure.colorbar(image, ax=ax, shrink=.86, pad=.03, label=label)
        ax.set(xlabel=options['x_label'], ylabel=options['y_label'], aspect='equal')
        ax.text(-.15, 1.04, panel, transform=ax.transAxes, fontsize=ax.xaxis.label.get_size() + 2)
        missing = int((~np.isfinite(grid)).sum())
        if missing:
            ax.plot([], [], marker='s', linestyle='none', color='#cccccc',
                    label=f'unavailable: {missing} bins')
        if zero.any():
            xs = .5 * (_array(data['x_edges'][:-1]) + _array(data['x_edges'][1:]))
            ys = .5 * (_array(data['y_edges'][:-1]) + _array(data['y_edges'][1:]))
            j, i = np.nonzero(zero)
            ax.scatter(xs[i], ys[j], marker='x', s=22, color='#dd8800', linewidths=1,
                       label=f'zero estimate: {int(zero.sum())} bins')
        if missing or zero.any():
            ax.legend(loc='upper center', bbox_to_anchor=(.5, -.19), fontsize=9)
    return axes


def _convergence(data, figure, options):
    import numpy as np
    from matplotlib.ticker import MaxNLocator
    has_batches = 'generation_k' in data
    axes = np.atleast_1d(figure.subplots(1, 2 if has_batches else 1))
    x, mean, std = _array(data['x']), _array(data['mean']), _array(data['stderr'])
    if has_batches:
        axes[0].plot(x, _array(data['generation_k']), 'o-', color=COLORS[0], label='Generation estimate' if data.get('generations_per_batch',1) > 1 else 'Batch estimate')
    ax = axes[-1]
    ax.plot(x, mean, 'o-', color=COLORS[1], label='Running mean')
    _errors(ax, x, mean, std, COLORS[1], '±1 sample SE')
    if options.get('show_band', True):
        ax.fill_between(x, mean - std, mean + std, color=COLORS[1], alpha=.10)
    for index, axis in enumerate(axes):
        if data.get('combined'):
            k = data['combined']
            axis.axhline(k['mean'], ls='-.', color='#777777', linewidth=1.7, label='Combined k')
        axis.set(xlabel=options['x_label'], ylabel=options['y_label'])
        axis.xaxis.set_major_locator(MaxNLocator(integer=True))
        # Reserve empty space for the legend and the result annotation instead
        # of letting an automatic legend obscure samples or error bars.
        lower, upper = axis.get_ylim()
        span = upper - lower
        axis.set_ylim(lower - .15 * span, upper + .30 * span)
        axis.legend(loc='upper right')
        axis.set_box_aspect(1)
        if has_batches:
            axis.text(-.15, 1.04, '(a)' if index == 0 else '(b)', transform=axis.transAxes,
                      fontsize=axis.xaxis.label.get_size() + 2)
    if data.get('combined'):
        k = data['combined']
        axes[-1].text(.04, .04, f"Combined k = {k['mean']:.5f} ± {k['std_dev']:.5f}",
                      transform=axes[-1].transAxes, fontsize=axes[-1].xaxis.label.get_size() * .8)
    return axes


def render(data, output, *, font_path=None, style_file=None, font_size=None,
           width=None, height=None, dpi=None):
    """Render exactly the saved values; never select or normalize scientific data."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    options = data['plot']
    template = options['template']
    style = {**PAPER_STYLE, 'axes.prop_cycle': matplotlib.cycler(color=COLORS)}
    if font_path and Path(font_path).is_file():
        font_manager.fontManager.addfont(str(font_path))
        style['font.family'] = font_manager.FontProperties(fname=str(font_path)).get_name()
    else:
        installed = {font.name for font in font_manager.fontManager.ttflist}
        style['font.family'] = next((f for f in ['Noto Sans CJK SC', 'Noto Sans CJK JP', 'DejaVu Sans']
                                     if f in installed), 'DejaVu Sans')
    if font_size is not None:
        style.update({name: font_size for name in ['font.size', 'axes.labelsize', 'axes.titlesize',
                                                  'legend.fontsize', 'xtick.labelsize', 'ytick.labelsize']})
    dimensions = (11, 5.3) if template in ('mesh', 'convergence') else (6.8, 5.6)
    with plt.style.context([style] + ([str(style_file)] if style_file else [])):
        figure = plt.figure(figsize=(width or dimensions[0], height or dimensions[1]), layout='constrained')
        try:
            if template in ('curve', 'spectrum', 'scan'):
                _curve(data, figure, options)
            elif template == 'mesh':
                _mesh(data, figure, options)
            elif template == 'convergence':
                _convergence(data, figure, options)
            else:
                raise ValueError('Unknown plot template: ' + str(template))
            title = options.get('title', '')
            if template == 'mesh':
                context = f"mesh {data['mesh_id']} · " + ' × '.join(str(v) for v in data['dimension'])
                if data.get('level') is not None:
                    context += f" · layer k={data['level']}"
                if data.get('energy_label'):
                    context += ' · ' + data['energy_label']
                title = title + '\n' + context
            if title:
                figure.suptitle(title, fontsize=plt.rcParams['axes.titlesize'])
            kind = data.get('provenance', {}).get('data_kind', 'unverified')
            note = 'Engineering preview · ' + kind
            if template in ('curve', 'spectrum', 'scan'):
                missing = sum(value is None for value in data['mean'])
                unavailable = sum(value is None for value in data['std_dev'])
                if missing or unavailable:
                    note += f' · missing values: {missing}; unavailable uncertainties: {unavailable}'
            if template == 'convergence':
                note += ' · sample SE does not establish source convergence'
            figure.supxlabel(note, fontsize=9, color='#666666')
            output = Path(output)
            with tempfile.NamedTemporaryFile(dir=output.parent, suffix=output.suffix, delete=False) as stream:
                pending = Path(stream.name)
            try:
                figure.savefig(pending, **({'dpi': dpi} if dpi else {}))
                os.replace(pending, output)
            finally:
                pending.unlink(missing_ok=True)
        finally:
            plt.close(figure)


def write_script(data_path):
    """Export this same renderer, with no imports from the toolkit or absolute paths."""
    data_path = Path(data_path)
    script = data_path.with_suffix('.py')
    source = Path(__file__).read_text(encoding='utf-8')
    source = source.replace('\nDEFAULT_DATA = None\n', '\nDEFAULT_DATA = ' + repr(data_path.name) + '\n', 1)
    write_text(script, source)
    return script.name


DEFAULT_DATA = None


def main():
    parser = argparse.ArgumentParser(description='Replot saved OpenMC JSON with NumPy/Matplotlib; no solver required.')
    parser.add_argument('--data', type=Path, help='Default: sibling JSON with the original selection and metadata')
    parser.add_argument('--output', type=Path, help='Default: sibling *-replot.png; original figure is preserved')
    parser.add_argument('--title'); parser.add_argument('--xlabel'); parser.add_argument('--ylabel')
    parser.add_argument('--font', type=Path, help='Optional local TTF/OTF/TTC font')
    parser.add_argument('--style-file', type=Path, help='Optional Matplotlib .mplstyle')
    parser.add_argument('--font-size', type=float)
    parser.add_argument('--width', type=float); parser.add_argument('--height', type=float)
    parser.add_argument('--dpi', type=int)
    args = parser.parse_args()
    for name in ('font_size', 'width', 'height', 'dpi'):
        value = getattr(args, name)
        if value is not None and (not math.isfinite(value) or value <= 0):
            parser.error('--' + name.replace('_', '-') + ' must be finite and positive')
    if args.font and not args.font.is_file():
        parser.error('--font must identify an existing local font file')
    if DEFAULT_DATA is None and args.data is None:
        parser.error('Pass --data or use the script exported next to a plot JSON.')
    data_path = args.data or Path(__file__).with_name(DEFAULT_DATA)
    data = json.loads(data_path.read_text(encoding='utf-8'))
    for key, value in [('title', args.title), ('x_label', args.xlabel), ('y_label', args.ylabel)]:
        if value is not None:
            data['plot'][key] = value
    output = args.output or data_path.with_name(data_path.stem + '-replot.png')
    if output.suffix.lower() not in ('.png', '.svg'):
        parser.error('--output must end in .png or .svg')
    render(data, output, font_path=args.font, style_file=args.style_file, font_size=args.font_size,
           width=args.width, height=args.height, dpi=args.dpi)
    print(str(output.resolve()))
    print('Input JSON SHA256: ' + hashlib.sha256(data_path.read_bytes()).hexdigest())


if __name__ == '__main__':
    main()
