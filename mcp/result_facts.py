"""Bounded, directly quotable facts from submitted inputs and selected results."""
import hashlib
import math
from pathlib import Path
import xml.etree.ElementTree as ET


def score_semantics(score, context=None):
    """Raw, unconverted units only; response/derivative units are not guessed.

    Reference: knowledge/openmc-0.16.0/usersguide/tallies.rst (score tables).
    A valid score named 'total' and nuclide scope 'total' are distinct axes.
    """
    context = context or {}
    known = {'flux': ('通量计分（未除体积）', 'particle-cm/source'),
             'fission': ('裂变反应计分', 'reactions/source'),
             'total': ('总反应计分', 'reactions/source')}
    label, unit = known.get(score, (score, None))
    modified = (context.get('response_weighted') or context.get('derivative')
                or (context.get('multiply_density') is False and score != 'flux'))
    if modified:
        unit = None
    return {'score': score, 'meaning': label, 'raw_unit': unit,
            'unit_status': 'documented' if unit else 'not_defined_here',
            'unit_note': ('单位须结合响应函数、导数或multiply_density设置另行核实；未套用普通计分单位。'
                          if modified else 'OpenMC原始计分；未作体积、源强或功率换算。' if unit else
                          '本摘要未定义该score的单位；保留原值，另查专业依据，不从名称猜测。')}


def interpretation(tallies, run_mode, adequacy):
    scores = []
    for tally in tallies:
        available = tally.get('score_semantics') or [score_semantics(s, tally.get('unit_context')) for s in tally['scores']]
        scores.extend({'tally_id': tally['id'], **item} for item in available)
    applicable = run_mode == 'eigenvalue'
    source = (adequacy or {}).get('source_convergence') or {}
    return {'terminology': 'score是计分类型；nuclide是核素/汇总范围。nuclide=total表示所有核素汇总；'
                          '只有scores中实际出现total时，score=total才表示合法的总反应计分。',
            'scores': scores,
            'source_convergence': {
                'status': 'not_verified' if applicable or not run_mode else 'not_applicable',
                'statement': ('源收敛未验证；本工具仅作相对σ分级和批次设置提示，未执行源收敛诊断。'
                              if applicable or not run_mode else '该运行模式不适用本征值源收敛判定。'),
                'evidence_limit': '批次阈值不是收敛标准；不能据此判断已收敛或未收敛，也不能断言系统偏差存在、已消除或大小。',
                'batch_advisory': source.get('status', 'not_available')},
            'normalization': 'source指每个源粒子；原始计分不是每秒物理量，未换算工程功率或实际源强。'}


def interpretation_summary(facts):
    lines = [facts['terminology']]
    for item in facts['scores']:
        unit = item['raw_unit'] or '未定义（需核实）'
        lines.append(f"tally {item['tally_id']}：score={item['score']}（{item['meaning']}）；原始单位={unit}；{item['unit_note']}")
    source = facts['source_convergence']
    lines += [facts['normalization'], source['statement']]
    if source['status'] == 'not_verified':
        lines.append(source['evidence_limit'])
    return '\n'.join(lines)


def submitted_materials(directory, record):
    result = {'status': 'unavailable', 'materials': [],
              'note': '提交输入声明的密度；独立于核素ao/wo比例，不代表燃耗步后的材料密度。'}
    hashes = record.get('input_files', {})
    name = next((n for n in ('model.xml', 'materials.xml') if n in hashes), None)
    if name is None:
        return dict(result, reason='未保留材料输入指纹，不能核实提交密度。')
    path = Path(directory) / 'work' / name
    limit = record.get('runtime', {}).get('max_input_bytes', 10 * 1024 * 1024)
    try:
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError('材料输入是符号链接')
        with path.open('rb') as source:
            raw = source.read(limit + 1)
        if len(raw) > limit:
            raise ValueError('材料输入超过读取上限')
        digest = hashlib.sha256(raw).hexdigest()
        if digest != hashes[name]:
            raise ValueError('材料输入与提交指纹不符')
        if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
            raise ValueError('不读取含DTD或实体声明的材料输入')
        root = ET.fromstring(raw)
        materials = root if root.tag == 'materials' else root.find('materials')
        if materials is None:
            raise ValueError('提交输入没有materials节点')
        entries = []
        for material in materials.findall('material'):
            density = material.find('density')
            value = None
            if density is not None and density.get('value') is not None:
                value = float(density.get('value'))
                if not math.isfinite(value):
                    raise ValueError('密度不是有限值')
            entries.append({'id': material.get('id'), 'name': material.get('name', ''),
                            'density': {'value': value, 'units': density.get('units') if density is not None else None}})
        return dict(result, status='verified', source_file=name, sha256=digest, materials=entries)
    except (OSError, ValueError, ET.ParseError) as exc:
        return dict(result, reason=str(exc))


def selected_value_rows(series, limit=20):
    rows = []
    for index, (mean, std) in enumerate(zip(series['mean'][:limit], series['std_dev'][:limit])):
        row = {'selected_index': index, 'score': series['score'], 'nuclide': series['nuclide'],
               'mean': mean, 'std_dev': std,
               'raw_unit': score_semantics(series['score'], series.get('unit_context'))['raw_unit']}
        if series.get('mesh_indices') is not None:
            row['mesh_bin'] = [v + 1 for v in series['mesh_indices'][index]]
        rows.append(row)
    return rows


def value_table(rows):
    lines = ['| 选择序号 | 网格箱[i,j,k]（1起始） | score（计分类型） | nuclide（核素/汇总范围） | mean | std_dev | 原始单位 |',
             '| --- | --- | --- | --- | --- | --- | --- |']
    for row in rows:
        row = {**row, 'raw_unit': row.get('raw_unit') or '待核实'}
        def cell(value):
            return str(value).replace('|', '\\|').replace('\n', ' ')
        lines.append('| ' + ' | '.join(cell(row.get(key, '不适用')) for key in
                     ('selected_index', 'mesh_bin', 'score', 'nuclide', 'mean', 'std_dev', 'raw_unit')) + ' |')
    return '\n'.join(lines)
