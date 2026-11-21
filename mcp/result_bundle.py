"""Compose existing result/plot/report operations without replacing their public tools.

Bundles publish atomically into a new job-local directory. No solver is started;
original StatePoint/input files and previously exported reports are never replaced.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import uuid

import openmc_jobs as jobs
from openmc_runtime import Error, atomic_json, file_location, relative
from openmc_import import publish_directory
from openmc_plots import options, plot_mesh, plot_results
from openmc_reports import export_selected_job


def mesh_report_series(data: dict) -> dict:
    """Adapt the existing mesh output to the existing report's selected-series shape.

    The renderer has already selected and mapped the data. This is row-major
    enumeration of its values[j][i], not another StatePoint reshape/selection.
    """
    nx, ny, _ = data['dimension']
    level = data.get('level') or 0
    mean, std, indices, centers = [], [], [], []
    mesh = data['mesh']
    lo, hi = mesh['lower_left'], mesh['upper_right']
    nz = data['dimension'][2]
    z = lo[2] + (level + .5) * (hi[2] - lo[2]) / nz if len(lo) == 3 else 0.
    for j in range(ny):
        for i in range(nx):
            mean.append(data['values'][j][i]); std.append(data['errors'][j][i])
            indices.append([i, j, level])
            centers.append([(data['x_edges'][i] + data['x_edges'][i+1])/2,
                            (data['y_edges'][j] + data['y_edges'][j+1])/2, z])
    note = '网格选择：level=' + str(data.get('level')) + '；energy_bounds_eV=' + json.dumps(data.get('energy_bounds'))
    return {'tally_id':data['tally_id'], 'score':data['score'], 'nuclide':data['nuclide'],
            'filter_selection':[], 'selection_note':note, 'mean':mean, 'std_dev':std,
            'x':list(range(len(mean))), 'x_label':'Selected mesh bin (i + nx*j; coordinates in mesh JSON)',
            'coordinate_note':'按values[j][i]读取，i变化最快；原生坐标见同名JSON/CSV。',
            'mesh_indices':indices, 'mesh_centers':centers, 'normalization':data['normalization']}


def verify_metadata_inputs(root: Path, record: dict) -> dict:
    """Report/mesh metadata must still belong to the submitted input snapshot.

    Do not hash solver outputs carried by restart workflows as if they were
    immutable inputs. This check covers the actual XML/marker sources consumed
    by the existing plot/report functions, not external nuclear-data libraries.
    """
    checked = {}
    for name in ('model.xml','settings.xml','tallies.xml','materials.xml','geometry.xml','mc-case.json'):
        expected = record.get('input_files',{}).get(name)
        if expected is None:continue
        path = root/'work'/name
        if not path.is_file() or path.is_symlink() or jobs.file_hash(path) != expected:
            raise Error('提交快照的元数据输入已改变：'+name+'；未生成结果包。原结果保留，可用原读取工具分析，勿把已改输入当作原始计算来源。')
        checked[name] = expected
    return checked


def delivery_summary(destination, job_id, selection, normalization, source_hash, filenames, warnings, data_quality=None):
    """Render recorded facts once; do not infer physical causes or host downloads."""
    score = selection['score']
    unit = {'flux': 'particle-cm/source（原始通量计分；未除以体积）',
            'fission': 'reactions/source（每个源粒子的裂变反应计分）'}.get(
                score, '未在本摘要中定义；按原始计分及专业依据解释，不能从名称猜测')
    lines = [f"作业 {job_id}：已完成；本结果包未重新求解。",
             '实际选择：' + json.dumps(selection, ensure_ascii=False, separators=(',', ':')),
             '原始单位：' + unit, '归一化：' + normalization,
             '未换算功率或实际源强；不是每秒物理量，未作专业验收。',
             '来源 StatePoint SHA256：' + source_hash,
             '以下是MCP服务器完整路径，尚未下载到宿主；宿主异机时须通过文件交接取回：']
    if data_quality:
        lines.insert(2, '所选完整数据：' + str(data_quality['points']) + '箱；均值为零'
                     + str(data_quality['zero_mean_bins']) + '箱；均值不可用'
                     + str(data_quality['unavailable_mean_bins']) + '箱。零箱数只属于本次选择，不确定零值原因。')
    lines += [label + '：' + str(destination / name) for label, name in filenames]
    if warnings:lines += ['重要提醒（本次结果；不能省略或改成确定原因）：']
    lines += ['注意：' + warning for warning in warnings]
    return '\n'.join(lines)


def generate_result_bundle(job_id: str, tally_id: int, score: str | None = None,
                           nuclide: str | None = None, filter_selection: list | None = None,
                           view: str = 'auto', level: int | None = None,
                           energy_bin: int | None = None, style: str = 'line',
                           image_format: str = 'png', report_formats: list | None = None,
                           title: str | None = None) -> dict:
    from openmc_tools import verified_result, read_results
    options(style, image_format, title)
    if type(tally_id) is not int or tally_id < 1:raise Error('tally_id须为正整数；先用openmc_read_results查询可用计分')
    if view not in ('auto','series','mesh'):raise Error('view须为auto/series/mesh')
    formats = ['markdown'] if report_formats is None else report_formats
    if (not isinstance(formats,list) or not formats
            or any(not isinstance(f,str) or f not in ('markdown','csv','docx','pdf','doc') for f in formats)
            or len(set(formats)) != len(formats)):
        raise Error('report_formats须为不重复的markdown/csv/docx/pdf/doc列表；PDF/DOC需要Office组件')
    root, record, point = verified_result(job_id)
    source_hash = record['output_files'][point.name]
    checked_inputs = verify_metadata_inputs(root,record)
    selection = {'tally_id':tally_id, 'score':score, 'nuclide':nuclide, 'filter_selection':filter_selection}
    overview = read_results(job_id,max_values=1,detail='full')
    tally = next((t for t in overview['tallies'] if t['id'] == tally_id),None)
    if tally is None:raise Error('tally_id不存在；先用openmc_read_results查询可用计分')
    effective = view
    if view == 'auto':
        # Inspect bounded metadata, not a full selected array just to pick a view.
        # In particular a small 3D slice must not first materialize every layer.
        single_mesh = (len(tally.get('filters',[])) == 1
                       and tally['filters'][0].get('type') == 'MeshFilter'
                       and tally.get('mesh',{}).get('type') == 'RegularMesh')
        effective = 'mesh' if single_mesh and not filter_selection and nuclide in (None,'total') and style == 'line' else 'series'
        if energy_bin is not None:effective = 'mesh'
    if effective == 'series' and (level is not None or energy_bin is not None):
        raise Error('level/energy_bin仅用于mesh视图；任意过滤箱选择请用filter_selection，或调用原单项工具')
    if effective == 'mesh' and filter_selection:
        raise Error('mesh视图绘制完整二维层；任意子集使用view=series及filter_selection，原工具保持可用')
    if effective == 'mesh' and style != 'line':
        raise Error('style仅用于series视图；柱图请用view=series，未静默忽略style')
    parent = root / 'bundles'
    parent.mkdir(exist_ok=True)
    identifier = uuid.uuid4().hex
    destination = parent / ('bundle-' + identifier)
    stage = Path(tempfile.mkdtemp(prefix='.pending-', dir=parent))
    try:
        if effective == 'mesh':
            chart = plot_mesh(job_id,tally_id=tally_id,score=score or tally['scores'][0],level=level,energy_bin=energy_bin,
                              format=image_format,title=title,_output_dir=stage)
            data = json.loads((stage/chart['image']).with_suffix('.json').read_text())
            if nuclide is not None and nuclide != data['nuclide']:
                raise Error('现有mesh工具选择'+data['nuclide']+'；指定其他核素请用view=series及nuclide，未合并或替换核素')
            series = mesh_report_series(data)
            resolved = {'tally_id':data['tally_id'],'score':data['score'],'nuclide':data['nuclide'],
                        'filter_selection':[], 'level':data['level'],'energy_bin':energy_bin,
                        'energy_bounds_eV':data.get('energy_bounds')}
        else:
            chart = plot_results(job_id,**selection,style=style,format=image_format,title=title,_output_dir=stage)
            data = json.loads((stage/chart['image']).with_suffix('.json').read_text())
            series = data
            resolved = {k:data[k] for k in ('tally_id','score','nuclide','filter_selection')}
        report_chart = chart
        if image_format == 'svg' and any(f in ('docx','pdf','doc') for f in formats):
            # python-docx needs a raster image; preserve the requested SVG and
            # render an equivalent PNG through the same existing operation.
            if effective == 'mesh':
                report_chart = plot_mesh(job_id,tally_id=tally_id,score=resolved['score'],level=level,
                                         energy_bin=energy_bin,format='png',title=title,_output_dir=stage)
            else:
                report_chart = plot_results(job_id,**selection,style=style,format='png',title=title,_output_dir=stage)
        reports = []
        for format in formats:
            report = export_selected_job(job_id,format,selection,_output_dir=stage,_chart=report_chart,_series=series)
            reports.append({'format':format,'file':Path(report['path']).name})
        # Existing readback adds scientific warnings and provenance; only a tiny
        # preview crosses this process boundary, never a full array to the Agent.
        warnings = list(overview.get('warnings',[]))
        if not checked_inputs:warnings.append('旧作业未保留可核对的元数据输入指纹；报告头来源未核实。')
        for key in ('zero_bin_note',):
            if data.get(key):warnings.append(data[key])
        if data.get('low_statistics_bins'):
            warnings.append(f"{data['low_statistics_bins']}个非零网格箱相对标准差超过{data['low_statistics_threshold']:.0%}或误差不可用；不是物理合格判据。")
        if any(v is None for v in series['mean']+series['std_dev']):warnings.append('所选数据含不可用统计量，保留为空，未补零。')
        # Keep renderer/export locks private; they are not part of a published bundle.
        (stage/'report.lock').unlink(missing_ok=True)
        _, latest, latest_point = verified_result(job_id)
        verify_metadata_inputs(root,record)
        if latest_point != point or latest['output_files'][latest_point.name] != source_hash:
            raise Error('生成期间原始结果选择改变，未发布结果包')
        warnings = list(dict.fromkeys(warnings))
        filenames = [('图', chart['image']),
                     ('完整JSON', Path(chart.get('values_json') or chart['series_json']).name),
                     ('完整CSV', Path(chart.get('values_csv') or chart['series_csv']).name),
                     ('复现脚本', Path(chart['plot_script']).name)]
        filenames += [('报告(' + r['format'] + ')', r['file']) for r in reports]
        filenames += [('来源清单', 'manifest.json'), ('交付摘要', 'delivery.md')]
        quality = {'points':len(series['mean']),
                   'zero_mean_bins':sum(v == 0 for v in series['mean']),
                   'unavailable_mean_bins':sum(v is None for v in series['mean']),
                   'zero_mean_std_bins':sum(v == 0 and e == 0 for v,e in zip(series['mean'],series['std_dev']))}
        summary = delivery_summary(destination, job_id, resolved, series['normalization'],
                                   source_hash, filenames, warnings, quality)
        (stage / 'delivery.md').write_text(summary + '\n', encoding='utf-8')
        files = {p.name:{'sha256':jobs.file_hash(p),'bytes':p.stat().st_size}
                 for p in sorted(stage.iterdir()) if p.is_file()}
        request = {'job_id':job_id,**selection,'view':view,'level':level,'energy_bin':energy_bin,
                   'style':style,'image_format':image_format,'report_formats':formats,'title':title}
        manifest = {'bundle_id':identifier,'state':'complete','request':request,'view':effective,'selection':resolved,
                    'points':len(series['mean']),'source':{'job_id':job_id,'statepoint':relative(point),'sha256':source_hash,
                    'input_hashes':dict(record.get('input_files',{})),
                    'checked_metadata_input_hashes':checked_inputs,
                    'metadata_inputs_verified':True if checked_inputs else None},'normalization':series['normalization'],
                    'provenance':record['provenance'],'professional_acceptance':False,
                    'warnings':list(dict.fromkeys(warnings)), 'data_quality':quality,
                    'scientific_checks':overview.get('scientific_checks',{}),
                    'statistical_adequacy':overview.get('statistical_adequacy',{}),'files':files,
                    'manifest_note':'files是本包其他文件的SHA256；manifest自身指纹由产物清单返回。'}
        atomic_json(stage/'manifest.json',manifest)
        publish_directory(stage,destination)
        from file_transfer import policy
        limit = policy()['max_file_bytes']
        artifacts = [{**file_location(destination/name),'sha256':jobs.file_hash(destination/name),
                      'bytes':(destination/name).stat().st_size,'download_allowed':(destination/name).stat().st_size<=limit}
                     for name in [*files,'manifest.json']]
        return {'bundle_id':identifier,'job_id':job_id,'state':'complete','view':effective,'selection':resolved,
                'points':len(series['mean']),'normalization':series['normalization'],'source':manifest['source'],
                'chart':relative(destination/chart['image']),
                'data_json':relative(destination/Path(chart.get('values_json') or chart['series_json']).name),
                'data_csv':relative(destination/Path(chart.get('values_csv') or chart['series_csv']).name),
                'plot_script':relative(destination/Path(chart['plot_script']).name),
                'reports':[{**r,'path':relative(destination/r['file'])} for r in reports],
                'manifest':relative(destination/'manifest.json'),'artifacts':artifacts,
                'delivery_summary':summary,'delivery_file':relative(destination/'delivery.md'),'data_quality':quality,
                'warnings':manifest['warnings'],
                'scientific_status':{k:v.get('status','unverified') for k,v in overview.get('scientific_checks',{}).items() if isinstance(v,dict)},
                'statistical_adequacy':overview.get('statistical_adequacy',{}),'professional_acceptance':False,
                'note':'结果包在服务器；未重新求解、未自动下载或覆盖旧报告。用openmc_prepare_download取回所需paths，超限文件留服务器；原单项工具和独立脚本继续可用。'}
    finally:
        if stage.exists():shutil.rmtree(stage)
